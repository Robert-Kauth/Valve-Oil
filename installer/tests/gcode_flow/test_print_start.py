"""A print's start from the slicer packs' start G-code on an idle, cold printer, through Valve Oil's print start
macros, on every printer: homing, heating, leveling, meshing, priming, and the Box's part in it."""
from __future__ import annotations

import logging
import unittest

from installer.tests.gcode_flow import checks, slicer
from installer.tests.gcode_flow.printer import FIRMWARE, FlowPrinter
from installer.tests.gcode_flow.qidi import BoxSetup

FIRST_LAYER_MIN_Y = 140.


class PrintStartTests(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)

    def started(self, printer_id, slicer_name, box, tool=0):
        printer = FlowPrinter(printer_id, box=box)
        printer.set_filament_detected(True)
        job = slicer.print_from_start(slicer_name, printer_id, filaments=[slicer.Filament()] * 8, tool=tool)
        printer.start_cold(job.text)
        return printer, job

    def assert_primes_only_on_the_prime_line(self, printer, job):
        """Before the first part's first move, filament leaves the nozzle only on the prime line, drawn on the first
        layer in front of the print, or at the chute."""
        debt, last = 0., checks.moves_from_line(printer, job.change_lines)
        for move in printer.toolhead.moves[:last]:
            if move.extrude < 0:
                debt -= move.extrude
                continue
            out = max(0., move.extrude - debt)
            debt = max(0., debt - move.extrude)
            if out <= .01 or checks.at_chute(printer, move.end[:2]):
                continue
            x, y, z = move.end[:3]
            self.assertTrue(z <= .3 and y <= FIRST_LAYER_MIN_Y - 5,
                            f"{out:.1f} mm left the nozzle at X{x:g} Y{y:g} Z{z:g} ({' > '.join(move.stack)})")

    def assert_z_homes_after_leveling(self, printer):
        """Z_TILT_ADJUST moves the Z motors against each other, so Z=0 is no longer where Z homed, and Klipper's docs
        home Z again before the mesh. Valve Oil's re-home never ran from March to November 2026: Klipper ran
        _VO_G29_HOME_Z_OR_FULL as _VO_G29, and QIDI's Klipper ignores unknown commands without a word."""
        names = [command.name for command in printer.trace]
        self.assertEqual("Z_TILT_ADJUST" in names, printer.lookup_object("z_tilt", None) is not None)
        if "Z_TILT_ADJUST" not in names:
            return
        tilt = max(i for i, name in enumerate(names) if name == "Z_TILT_ADJUST")
        mesh = next(i for i in range(tilt, len(names)) if names[i] in ("BED_MESH_CALIBRATE", "BED_MESH_PROFILE"))
        self.assertTrue(any(command.name == "G28" and "Z" in command.line.upper().split()[1:]
                            for command in printer.trace[tilt:mesh]))

    def test_a_print_starts_primes_in_front_and_prints(self):
        """Print start with no Box, and with the Box off, homes, heats the bed and nozzle, levels and meshes, draws
        the prime line, and hands over to the print with the nozzle at the first layer's temperature."""
        boxes = (("no Box", BoxSetup()),
                 ("Box off", BoxSetup(boxes=1, enable_box=0, tools={0: "slot0"}, loaded_slot="slot0")))
        for printer_id in FIRMWARE:
            for slicer_name in slicer.PACKS:
                for name, box in boxes:
                    with self.subTest(printer=printer_id, slicer=slicer_name, box=name):
                        printer, job = self.started(printer_id, slicer_name, box)
                        checks.assert_no_errors(self, printer)
                        self.assertEqual(printer.status("print_stats")["state"], "complete")
                        self.assertEqual(printer.status("toolhead")["homed_axes"], "xyz")
                        self.assertEqual(printer.status("extruder")["target"], slicer.Filament().temperature)
                        self.assertEqual(printer.status("heater_bed")["target"], 60.)
                        self.assert_primes_only_on_the_prime_line(printer, job)
                        self.assert_z_homes_after_leveling(printer)
                        checks.assert_rear_moves_around_the_chute(self, printer)

    def test_a_print_on_a_tool_with_no_box_slot_prints_from_the_external_spool(self):
        """QIDI's BOX_PRINT_START looks up the start tool's slot with no default, so a print starting on T4 with one
        Box shut Klipper down with "Unknown config object 'box_stepper slot4'" (three OrcaSlicer prints, operator
        klippy.log, 2026-11-17). Valve Oil turns Use QIDI Box off for that print, as the touchscreen does for the
        external spool, and says so."""
        for printer_id in FIRMWARE:
            with self.subTest(printer=printer_id):
                box = BoxSetup(boxes=1, enable_box=1, tools={0: "slot0"}, loaded_slot="slot0", current_tool=0)
                printer, job = self.started(printer_id, "orca", box, tool=4)
                checks.assert_no_errors(self, printer)
                self.assertEqual(printer.status("print_stats")["state"], "complete")
                self.assertEqual(printer.variables()["enable_box"], 0)
                self.assertTrue(any("T4 has no QIDI Box slot" in line for line in printer.responses.lines))

    def test_a_box_print_start_loads_the_tools_slot_at_the_chute(self):
        """QIDI's BOX_PRINT_START gets the start tool's slot to the toolhead: nothing to do but re-arm it when it's
        already there, a load when nothing is loaded, and a cut and unload first when another slot is. Everything it
        and Valve Oil's print start push out has to go into the chute or onto the prime line."""
        cases = (
            ("its slot loaded", BoxSetup(boxes=1, enable_box=1, tools={0: "slot0"}, loaded_slot="slot0",
                                         current_tool=0), True, 0),
            ("nothing loaded", BoxSetup(boxes=1, enable_box=1, tools={0: "slot0"}, loaded_slot="slot16", hub=None),
             False, 0),
            ("another slot loaded", BoxSetup(boxes=1, enable_box=1, tools={0: "slot0", 1: "slot1"},
                                             loaded_slot="slot1", current_tool=1), True, 1),
        )
        for printer_id in FIRMWARE:
            for name, box, toolhead, cuts in cases:
                with self.subTest(printer=printer_id, case=name):
                    printer = FlowPrinter(printer_id, box=box)
                    printer.set_filament_detected(toolhead)
                    job = slicer.print_from_start("orca", printer_id, filaments=[slicer.Filament()] * 2, tool=0)
                    printer.start_cold(job.text)
                    checks.assert_no_errors(self, printer)
                    self.assertEqual(printer.status("print_stats")["state"], "complete")
                    self.assertEqual(printer.variables()["last_load_slot"], "slot0")
                    self.assertEqual(checks.cuts(printer), cuts)
                    self.assert_primes_only_on_the_prime_line(printer, job)
                    self.assert_z_homes_after_leveling(printer)
                    checks.assert_rear_moves_around_the_chute(self, printer)

    def test_no_print_starts_while_a_box_dries(self):
        """QIDI's timed drying turns every slot of the Box every 5 minutes and holds the toolhead 5 seconds for each;
        a turn during print start's Z homing shut Klipper down with an internal error on RUN_STEPPER (operator
        klippy.log, 2026-11-23). Valve Oil refuses to start a print while a Box dries, before anything moves or
        heats."""
        for printer_id in FIRMWARE:
            with self.subTest(printer=printer_id):
                box = BoxSetup(boxes=1, enable_box=1, tools={0: "slot0"}, loaded_slot="slot0", current_tool=0,
                               drying=frozenset({0}))
                printer = FlowPrinter(printer_id, box=box)
                job = slicer.print_from_start("orca", printer_id, filaments=[slicer.Filament()], tool=0)
                (printer.root / "gcodes").mkdir(exist_ok=True)
                (printer.root / "gcodes/job.gcode").write_text(job.text, encoding="utf-8")
                printer.trace.clear()
                printer.toolhead.moves.clear()
                with self.assertRaisesRegex(printer.command_error, "Box 1 is drying"):
                    printer.command("SDCARD_PRINT_FILE FILENAME=job.gcode")
                self.assertEqual(printer.status("print_stats")["state"], "standby")
                self.assertEqual(printer.toolhead.moves, [])
                self.assertEqual(printer.status("extruder")["target"], 0.)
                self.assertEqual(printer.status("heater_bed")["target"], 0.)


if __name__ == "__main__":
    unittest.main()
