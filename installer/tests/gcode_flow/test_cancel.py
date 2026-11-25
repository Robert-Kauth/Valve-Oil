"""Stopping a print from Fluidd or the touchscreen (CANCEL_PRINT), on every printer, while it prints and while it's
paused."""
from __future__ import annotations

import unittest

from installer.tests.gcode_flow import checks, slicer
from installer.tests.gcode_flow.printer import FIRMWARE, FlowPrinter
from installer.tests.gcode_flow.qidi import BoxSetup

PRINT_TEMPERATURE = 250.
FAN_OFF_BELOW = 170.
# QIDI's cancel parks: the Max 4 up to Z200 and back to Y380, the Plus 5 up to Z200, the Smart 5 up to Z100.
PARK = {"max4": ["G1 Z200 F480", "G1 Y380 F5000"], "plus5": ["G1 Z200 F480"], "smart5": ["G1 Z100 F480"]}
PRINT = "\n".join(["G1 X100 Y100 E1 F3000", "G1 X120 Y100 E1 F3000", "_TEST_STOP", "G1 X140 Y100 E1 F3000"])


class CancelTests(checks.FlowTestCase):
    def printer(self, printer_id):
        printer = FlowPrinter(printer_id, box=BoxSetup())
        if printer_id != "max4":
            # QIDI's homing sets it; the Plus 5 and Smart 5 cancels lift Z only when it's set.
            printer.run("SET_GCODE_VARIABLE MACRO=_HOME_STATE VARIABLE=is_homed VALUE=1")
        return printer

    def stop_while_printing(self, printer, nozzle=None):
        """Stops the print partway, as Moonraker does: CANCEL_PRINT between two of the file's lines, with the nozzle
        at `nozzle` if given."""
        stopped = {}

        def stop(gcmd):
            if nozzle is not None:
                printer.lookup_object("heaters").lookup_heater("extruder").set_smoothed_temp(nozzle)
            stopped["moves"] = len(printer.toolhead.moves)
            stopped["trace"] = len(printer.trace)
            printer.gcode.run_script_from_command("CANCEL_PRINT")
        printer.gcode.register_command("_TEST_STOP", stop)
        printer.start_print(PRINT, nozzle=PRINT_TEMPERATURE)
        return stopped

    def assert_cools_with_the_fan_until(self, printer):
        """The part fan stays at full until the nozzle is below 170°C, then turns off within the next check."""
        fan = printer.lookup_object("fan_generic cooling_fan")
        extruder = printer.lookup_object("heaters").lookup_heater("extruder")
        self.assertGreaterEqual(extruder.smoothed_temp, FAN_OFF_BELOW)
        while extruder.smoothed_temp >= FAN_OFF_BELOW:
            self.assertEqual(fan.speed, 1.)
            printer.reactor.advance(printer.reactor.monotonic() + .5)
        printer.reactor.advance(printer.reactor.monotonic() + 1.)
        self.assertEqual(fan.speed, 0.)

    def test_stop_turns_the_nozzle_off_and_the_part_fan_on_before_it_parks(self):
        """A stopped print left the nozzle heating at the print's temperature over the print through QIDI's slow lift
        to Z200 (8 mm/s), and turned it off only in PRINT_END, after parking (operator report, 2026-11-24). The nozzle
        has to go off, with the part fan at full to cool it, before the first park move, and the fan has to stay on
        until the nozzle is below 170°C, through PRINT_END's M107."""
        for printer_id in FIRMWARE:
            with self.subTest(printer=printer_id):
                printer = self.printer(printer_id)
                stopped = self.stop_while_printing(printer)
                checks.assert_no_errors(self, printer)
                self.assertEqual(printer.status("print_stats")["state"], "cancelled")
                parked = printer.toolhead.moves[stopped["moves"]:]
                self.assertEqual([move.stack[-1] for move in parked], PARK[printer_id])
                lines = [command.line for command in printer.trace[stopped["trace"]:]]
                first_park = lines.index(PARK[printer_id][0])
                self.assertLess(lines.index("M104 S0"), first_park)
                self.assertLess(lines.index("M106 S255"), first_park)
                self.assertEqual(printer.status("extruder")["target"], 0.)
                checks.assert_rear_moves_around_the_chute(self, printer)
                self.assert_cools_with_the_fan_until(printer)

    def test_stop_while_paused_cools_the_nozzle_with_the_part_fan(self):
        """A paused print is stopped as often as a running one. The pause has already turned the nozzle off, but it
        is still hot; the part fan cools it the same way."""
        for printer_id in FIRMWARE:
            for slicer_name in slicer.PACKS:
                with self.subTest(printer=printer_id, slicer=slicer_name):
                    printer = self.printer(printer_id)
                    printer.start_print(slicer.print_with_pause(slicer_name, printer_id).text, nozzle=PRINT_TEMPERATURE)
                    self.assertTrue(printer.status("pause_resume")["is_paused"])
                    printer.command("CANCEL_PRINT")
                    checks.assert_no_errors(self, printer)
                    self.assertEqual(printer.status("print_stats")["state"], "cancelled")
                    self.assertFalse(printer.status("pause_resume")["is_paused"])
                    checks.assert_rear_moves_around_the_chute(self, printer)
                    self.assert_cools_with_the_fan_until(printer)

    def test_a_cold_nozzle_and_a_new_print_keep_their_fan(self):
        """The cooldown is for a hot nozzle only, and must never turn the fan off during the next print or after the
        operator set it."""
        for printer_id in FIRMWARE:
            with self.subTest(printer=printer_id, case="a cold nozzle"):
                printer = self.printer(printer_id)
                self.stop_while_printing(printer, nozzle=FAN_OFF_BELOW - 20.)
                checks.assert_no_errors(self, printer)
                self.assertNotIn("M106 S255", [command.line for command in printer.trace])
                self.assertEqual(printer.lookup_object("fan_generic cooling_fan").speed, 0.)

            with self.subTest(printer=printer_id, case="a new print during the cooldown"):
                printer = self.printer(printer_id)
                self.stop_while_printing(printer)
                fan = printer.lookup_object("fan_generic cooling_fan")
                printer.start_print("G1 X100 Y100 E1 F3000\nM106 S128\nM109 S220\nG1 X120 Y100 E1 F3000\nPAUSE",
                                    nozzle=PRINT_TEMPERATURE)
                printer.reactor.advance(printer.reactor.monotonic() + 300.)
                self.assertAlmostEqual(fan.speed, 128 / 255.)

            for command, speed in (("M107", 0.), ("M106 S100", 100 / 255.)):
                with self.subTest(printer=printer_id, case=f"the operator sends {command}"):
                    printer = self.printer(printer_id)
                    self.stop_while_printing(printer)
                    fan = printer.lookup_object("fan_generic cooling_fan")
                    printer.command(command)
                    printer.reactor.advance(printer.reactor.monotonic() + 300.)
                    self.assertAlmostEqual(fan.speed, speed)


if __name__ == "__main__":
    unittest.main()
