"""Box unloads and ejects from the console, Fluidd, or the touchscreen between prints, and QIDI's flow pause, stop,
and retry, through QIDI's flows (qidi.TaskQueueManager, qidi.BoxAdapterV1) with Valve Oil's flow recovery around
them."""
from __future__ import annotations

import unittest

from installer.tests.gcode_flow import checks, slicer
from installer.tests.gcode_flow.printer import FIRMWARE, FlowPrinter
from installer.tests.gcode_flow.qidi import EMPTY_SLOT, BoxSetup

LOADED = dict(boxes=1, enable_box=1, tools={0: "slot0"}, loaded_slot="slot0", current_tool=0)
CHUTE_WIPES = ("DROP_TRASH", "WIPE_NOZZLE_DROP")


def part_fan_at_wipes(printer, before):
    """The part fan's speed when each chute wipe in the trace starts its own commands, and its speed at the end,
    starting from BEFORE. The trace has a command before what it runs."""
    speed, at_wipes, wipe = before, [], None
    for command in printer.trace:
        words = command.line.split()
        if command.name == "SET_FAN_SPEED" and "FAN=cooling_fan" in words:
            speed = float(next(w for w in words if w.startswith("SPEED=")).split("=")[1])
            continue
        if wipe is not None:
            at_wipes.append((wipe, speed))
            wipe = None
        if command.name in CHUTE_WIPES:
            wipe = command.name
    if wipe is not None:
        at_wipes.append((wipe, speed))
    return at_wipes, speed


def part_fan(printer):
    return printer.status("fan_generic cooling_fan")["speed"]


def cool_nozzle(printer, below=170.):
    """Let the clock run until the nozzle is below BELOW and the part fan cooldown's next check has run."""
    give_up = printer.reactor.monotonic() + 900.
    while printer.status("extruder")["temperature"] >= below:
        assert printer.reactor.monotonic() < give_up, "the nozzle never cooled below %s" % below
        printer.reactor.advance(printer.reactor.monotonic() + 5.)
    printer.reactor.advance(printer.reactor.monotonic() + 2.)


class BoxFlowTests(checks.FlowTestCase):
    def printer(self, printer_id, box, **idle):
        printer = FlowPrinter(printer_id, box=box)
        printer.idle(**idle)
        printer.toolhead.extruder.heater.targets.clear()
        return printer

    def assert_parked_off(self, printer):
        """A flow that ends leaves the toolhead at the chute, the heater off, and the extruder motor off."""
        self.assertTrue(checks.at_chute(printer, printer.toolhead.commanded_pos[:2]))
        self.assertEqual(printer.status("extruder")["target"], 0.)
        self.assertFalse(printer.status("stepper_enable")["steppers"]["extruder"])
        self.assertIsNone(printer.status("multi_color_controller")["task"]["flow_id"])

    def test_a_heated_unload_heats_cuts_pulls_back_and_parks_with_the_heater_off(self):
        """With COLD=0, QIDI's unload (flow 3: BOX_HEAT, BOX_CUT, BOX_UNLOAD) heats to the top of the filament's
        range, cuts, pulls the filament back into the Box at the chute, and parks with the heater off, wiping first
        because it heated. Nothing may be pushed out of the nozzle anywhere. The wipe runs with the part fan at full
        so the hot nozzle's ooze sets on the wiper (Plus 5, 2026-11-25: with the fan off it oozed), and the fan stays
        at full after the flow until the nozzle is below 170 °C, then goes back to its earlier speed."""
        for printer_id in FIRMWARE:
            with self.subTest(printer=printer_id):
                printer = self.printer(printer_id, BoxSetup(**LOADED))
                printer.command("SET_FAN_SPEED FAN=cooling_fan SPEED=0.4")
                printer.trace.clear()
                printer.command("UNLOAD_FILAMENT_CONTROL SLOT=0 COLD=0")
                checks.assert_no_errors(self, printer)
                at_wipes, _ = part_fan_at_wipes(printer, 0.4)
                self.assertEqual(at_wipes, [("WIPE_NOZZLE_DROP", 1.)])
                self.assertEqual(part_fan(printer), 1.)
                cool_nozzle(printer)
                self.assertAlmostEqual(part_fan(printer), 0.4, places=2)
                self.assert_parked_off(printer)
                checks.assert_purges_only_at_the_chute(self, printer, 0, len(printer.toolhead.moves))
                checks.assert_rear_moves_around_the_chute(self, printer)
                self.assertEqual(checks.cuts(printer), 1)
                self.assertIn(240., printer.toolhead.extruder.heater.targets)
                self.assertIn("WIPE_NOZZLE_DROP", [c.name for c in printer.trace])
                self.assertEqual(printer.variables()["last_load_slot"], EMPTY_SLOT)
                self.assertIsNone(printer.vendor.hub_slot)
                self.assertFalse(printer.status("filament_switch_sensor filament_switch_sensor")["filament_detected"])
                self.assertIn("// Flow completed successfully.", printer.responses.lines)

    def test_a_cold_unload_cuts_and_pulls_back_without_heating(self):
        """Between prints a Box slot's unload runs QIDI's unload with the nozzle cold, whether it asks with COLD=1
        (Valve Oil UI, Fluidd) or doesn't say, as QIDI's touchscreen never does (Plus 5, 2026-11-25: its unloads
        heated to 280 °C). The cutter cuts cold filament above the hotend, QIDI's unload and eject steps never read
        the nozzle temperature, and its pull-backs pass Klipper's cold-extrusion check only while Valve Oil allows
        retracts. With no flow temperature QIDI's completion skips its wipe. Afterwards the printer has to be as
        before: a cold retract refused again, and M109, which the flow skipped, heating again."""
        for printer_id in FIRMWARE:
            for cold in (" COLD=1", ""):
                with self.subTest(printer=printer_id, command="UNLOAD_FILAMENT_CONTROL SLOT=0" + cold):
                    self.assert_cold_unload(printer_id, "UNLOAD_FILAMENT_CONTROL SLOT=0" + cold)

    def assert_cold_unload(self, printer_id, command):
        printer = self.printer(printer_id, BoxSetup(**LOADED))
        printer.command(command)
        checks.assert_no_errors(self, printer)
        self.assert_parked_off(printer)
        self.assertEqual([t for t in printer.toolhead.extruder.heater.targets if t > 0], [])
        self.assertEqual(checks.cuts(printer), 1)
        self.assertNotIn("WIPE_NOZZLE_DROP", [c.name for c in printer.trace])
        self.assertEqual(printer.variables()["last_load_slot"], EMPTY_SLOT)

        moves = len(printer.toolhead.moves)
        with self.assertRaisesRegex(printer.command_error, "Extrude below minimum temp"):
            printer.command("M83\nG1 E-4 F1800")
        self.assertEqual(len(printer.toolhead.moves), moves)
        printer.command("M109 S240")
        self.assertEqual(printer.status("extruder")["target"], 240.)
        self.assertGreaterEqual(printer.status("extruder")["temperature"], 239.)

    def test_a_retried_cold_unload_stays_cold(self):
        """A cold unload that stops, on caught filament or on a Box motor error raised out of QIDI's unload, pauses
        QIDI's flow. QIDI's FLOW_RETRY heats to the flow's temperature before rerunning the step, which would heat a
        nozzle the operator chose to keep cold, so the retry has to stay cold. Cold retracts mustn't stay allowed
        through the pause."""
        faults = (("filament caught at the toolhead", dict(caught=True), "caught"),
                  ("a Box motor error in the unload",
                   dict(unload_error="Error during homing b_endstop: Communication timeout during homing"),
                   "unload_error"))
        for printer_id in FIRMWARE:
            for name, fault, cleared in faults:
                with self.subTest(printer=printer_id, case=name):
                    printer = self.printer(printer_id, BoxSetup(**fault, **LOADED))
                    with self.assertRaises(printer.command_error):
                        printer.command("UNLOAD_FILAMENT_CONTROL SLOT=0 COLD=1")
                    task = printer.status("multi_color_controller")["task"]
                    self.assertEqual((task["current_step"], task["is_paused"]), ("BOX_UNLOAD", True))
                    with self.assertRaisesRegex(printer.command_error, "Extrude below minimum temp"):
                        printer.command("M83\nG1 E-4 F1800")

                    setattr(printer.vendor, cleared, None)
                    printer.responses.lines.clear()
                    printer.command("FLOW_RETRY")
                    checks.assert_no_errors(self, printer)
                    self.assert_parked_off(printer)
                    self.assertEqual([t for t in printer.toolhead.extruder.heater.targets if t > 0], [])
                    self.assertEqual(printer.variables()["last_load_slot"], EMPTY_SLOT)

    def test_a_cold_unload_of_the_external_spool_heats_and_says_so(self):
        """COLD=1 covers only flows made of Box heat, cut, unload, and eject steps. QIDI's external-spool unload
        (flow 7) has steps of its own that it can't skip the heat of, so it has to heat as usual and tell the operator
        why, not run half cold."""
        for printer_id in FIRMWARE:
            with self.subTest(printer=printer_id):
                box = BoxSetup(boxes=1, enable_box=0, tools={0: "slot0"}, loaded_slot=EMPTY_SLOT, hub=None)
                printer = self.printer(printer_id, box)
                printer.command("UNLOAD_FILAMENT_CONTROL SLOT=16 COLD=1")
                checks.assert_no_errors(self, printer)
                self.assert_parked_off(printer)
                self.assertIn(240., printer.toolhead.extruder.heater.targets)
                self.assertEqual(checks.cuts(printer), 1)
                self.assertTrue(any("heats the nozzle" in line for line in printer.responses.lines))

    def test_a_cold_unload_is_refused_while_a_print_is_paused(self):
        """COLD=1 is for a printer between prints: it skips the flow's heating and lets cold pull-backs through. A
        paused print, where the operator is at the printer and an Unload is a button away, holds its filament and
        temperature for RESUME, so a cold unload is refused before anything moves, heats, or cuts. (A running
        print's refusal is test_cold_retracts.py's: here a console command can't arrive while QIDI's print loop
        runs.)"""
        for printer_id in FIRMWARE:
            with self.subTest(printer=printer_id):
                printer = FlowPrinter(printer_id, box=BoxSetup(**LOADED))
                printer.start_print(slicer.print_with_pause("orca", printer_id).text)
                self.assertEqual(printer.status("print_stats")["state"], "paused")
                moves, targets = len(printer.toolhead.moves), list(printer.toolhead.extruder.heater.targets)
                printer.trace.clear()
                with self.assertRaises(printer.command_error):
                    printer.command("UNLOAD_FILAMENT_CONTROL SLOT=0 COLD=1")
                self.assertEqual(len(printer.toolhead.moves), moves)
                self.assertEqual(printer.toolhead.extruder.heater.targets, targets)
                self.assertEqual([c.name for c in printer.trace], ["UNLOAD_FILAMENT_CONTROL"])
                self.assertIsNone(printer.status("multi_color_controller")["task"]["flow_id"])
                self.assertEqual(printer.variables()["last_load_slot"], "slot0")
                self.assertEqual(printer.status("print_stats")["state"], "paused")

    def test_a_flow_after_relative_positioning_still_parks(self):
        """QIDI's MOVE_TO_TRASH sets no G90, so relative positioning left over from a script that stopped before its
        G90 sent every Box flow's moves off the bed with "Move out of range" until Klipper restarted. Valve Oil
        switches to absolute positioning for flows outside a print."""
        for printer_id in FIRMWARE:
            with self.subTest(printer=printer_id):
                printer = self.printer(printer_id, BoxSetup(**LOADED))
                printer.command("G91")
                printer.command("UNLOAD_FILAMENT_CONTROL SLOT=0")
                checks.assert_no_errors(self, printer)
                self.assert_parked_off(printer)

    def test_filament_caught_at_the_toolhead_stops_the_unload_and_flow_stop_turns_the_heater_off(self):
        """Issue #348: QIDI's unload keeps pulling filament the toolhead holds, about 450 mm over a minute and a half,
        before QDE_004_008. Valve Oil stops it 20 mm past the pull-back, lets the slot go from the extruder, and
        pauses the flow saying what's wrong. FLOW_STOP from the console then has to clear the flow and turn the
        heater off: QIDI's stop takes the G-code lock the console command already holds, which hung Klipper
        (2026-10-31)."""
        for printer_id in FIRMWARE:
            with self.subTest(printer=printer_id):
                printer = self.printer(printer_id, BoxSetup(caught=True, **LOADED))
                with self.assertRaisesRegex(printer.command_error, "Something is holding the filament"):
                    printer.command("UNLOAD_FILAMENT_CONTROL SLOT=0 COLD=0")
                pulled = -sum(m.extrude for m in printer.toolhead.moves if m.extrude < 0 and "CUT_FILAMENT_1"
                              not in " ".join(m.stack))
                self.assertLessEqual(pulled, 150. + 20.)
                # Stopped before the Box pulled: QIDI's unload starts the Box's pull only once the toolhead sensor
                # clears, so the filament is still at the hub.
                self.assertEqual(printer.vendor.hub_slot, "slot0")
                task = printer.status("multi_color_controller")["task"]
                self.assertEqual((task["current_step"], task["is_paused"]), ("BOX_UNLOAD", True))
                stepper = printer.vendor.steppers["slot0"]
                self.assertFalse(stepper.extruder_stepper.synced or stepper.enabled)
                self.assertEqual(printer.status("extruder")["target"], 240.)

                printer.command("FLOW_STOP")
                self.assertIsNone(printer.status("multi_color_controller")["task"]["flow_id"])
                self.assertEqual(printer.status("extruder")["target"], 0.)
                self.assertIn("// Box flow stopped. Nozzle heater off.", printer.responses.lines)
                printer.command("M400")
                self.assertFalse(printer.is_shutdown())

    def test_flow_retry_finishes_an_unload_once_the_filament_is_free(self):
        """After the operator frees caught filament, FLOW_RETRY of a heated unload heats to the flow's temperature
        again and reruns the paused unload, which then finishes as an unload does."""
        for printer_id in FIRMWARE:
            with self.subTest(printer=printer_id):
                printer = self.printer(printer_id, BoxSetup(caught=True, **LOADED))
                with self.assertRaises(printer.command_error):
                    printer.command("UNLOAD_FILAMENT_CONTROL SLOT=0 COLD=0")
                printer.vendor.caught = False
                printer.responses.lines.clear()
                printer.command("FLOW_RETRY")
                checks.assert_no_errors(self, printer)
                self.assert_parked_off(printer)
                self.assertEqual(printer.variables()["last_load_slot"], EMPTY_SLOT)

    def test_an_eject_of_an_unloaded_slot_homes_and_heats_nothing(self):
        """Ejecting a slot whose filament stops at the Box (flow 4) only pulls the slot back. With X and Y unhomed it
        homed them through MOVE_TO_TRASH, retracted the slot, parked, and turned the heater off without heating or
        cutting (runtime, 2026-10-28, two USB Boxes)."""
        for printer_id in FIRMWARE:
            with self.subTest(printer=printer_id):
                printer = self.printer(printer_id, BoxSetup(boxes=1, enable_box=1, tools={0: "slot0", 1: "slot1"},
                                                            loaded_slot=EMPTY_SLOT, hub=None), homed=False)
                printer.set_filament_detected(False)
                printer.command("EJECT_FILAMENT_CONTROL SLOT=1")
                checks.assert_no_errors(self, printer)
                self.assertEqual(printer.status("toolhead")["homed_axes"][:2], "xy")
                self.assertEqual([t for t in printer.toolhead.extruder.heater.targets if t > 0], [])
                self.assertEqual(checks.cuts(printer), 0)
                self.assertNotIn("slot1", printer.vendor.inlets)
                self.assertTrue(checks.at_chute(printer, printer.toolhead.commanded_pos[:2]))
                self.assertEqual(printer.status("extruder")["target"], 0.)

    def test_an_eject_of_the_loaded_slot_unloads_it_first(self):
        """Ejecting the loaded slot with filament at the toolhead (flow 5) heats, cuts, unloads, and then pulls the
        slot back out of the Box, parking with the heater off."""
        for printer_id in FIRMWARE:
            with self.subTest(printer=printer_id):
                printer = self.printer(printer_id, BoxSetup(**LOADED))
                printer.command("EJECT_FILAMENT_CONTROL SLOT=0")
                checks.assert_no_errors(self, printer)
                self.assert_parked_off(printer)
                checks.assert_purges_only_at_the_chute(self, printer, 0, len(printer.toolhead.moves))
                self.assertEqual(checks.cuts(printer), 1)
                self.assertNotIn("slot0", printer.vendor.inlets)
                self.assertEqual(printer.variables()["last_load_slot"], EMPTY_SLOT)

    def test_a_load_purges_and_wipes_at_the_chute_and_parks_with_the_heater_off(self):
        """QIDI's Box load heats at the chute, pulls the slot's filament to the toolhead, extrudes until the load
        cell feels it, then its wipe (BOX_WIPE) pushes 100 mm through at the chute and drops the waste. Loading over
        another slot's filament (flow 2) cuts and unloads that first. Every gram has to go into the chute. From the
        wipe on the part fan runs at full, so the hot nozzle's ooze sets on the wiper (Plus 5, 2026-11-25: with the
        fan off it oozed), until the flow ends and the nozzle is below 170 °C, then goes back to its earlier
        speed."""
        cases = (
            ("into an empty toolhead", BoxSetup(boxes=1, enable_box=1, tools={0: "slot0", 1: "slot1"},
                                                loaded_slot=EMPTY_SLOT, hub=None), False, 0),
            ("over another slot", BoxSetup(**dict(LOADED, tools={0: "slot0", 1: "slot1"})), True, 1),
        )
        for printer_id in FIRMWARE:
            for name, box, toolhead, cuts in cases:
                with self.subTest(printer=printer_id, case=name):
                    printer = self.printer(printer_id, box)
                    printer.set_filament_detected(toolhead)
                    printer.trace.clear()
                    printer.command("LOAD_FILAMENT_CONTROL SLOT=1")
                    checks.assert_no_errors(self, printer)
                    at_wipes, _ = part_fan_at_wipes(printer, 0.)
                    self.assertTrue(at_wipes)
                    self.assertEqual({speed for _, speed in at_wipes}, {1.})
                    self.assertEqual(part_fan(printer), 1.)
                    cool_nozzle(printer)
                    self.assertEqual(part_fan(printer), 0.)
                    self.assert_parked_off(printer)
                    checks.assert_purges_only_at_the_chute(self, printer, 0, len(printer.toolhead.moves))
                    checks.assert_rear_moves_around_the_chute(self, printer)
                    self.assertEqual(checks.cuts(printer), cuts)
                    self.assertEqual(printer.variables()["last_load_slot"], "slot1")
                    self.assertEqual(printer.variables()["slot_sync"], "slot1")

    def test_the_part_fan_cooldown_after_a_load_holds_nothing_up(self):
        """The fan keeps cooling the nozzle after a load on a timer, so the operator can unload, heat, or start a
        print at once (user, 2026-11-25). An unload during it runs straight away and leaves the fan cooling; heating
        the nozzle puts the fan back; a speed set by anything else stays."""
        box = BoxSetup(boxes=1, enable_box=1, tools={0: "slot0", 1: "slot1"}, loaded_slot=EMPTY_SLOT, hub=None)
        for printer_id in FIRMWARE:
            with self.subTest(printer=printer_id, then="unload"):
                printer = self.printer(printer_id, box)
                printer.set_filament_detected(False)
                printer.command("LOAD_FILAMENT_CONTROL SLOT=1")
                self.assertEqual(part_fan(printer), 1.)
                started, targets = printer.reactor.monotonic(), len(printer.toolhead.extruder.heater.targets)
                printer.command("UNLOAD_FILAMENT_CONTROL SLOT=1")
                checks.assert_no_errors(self, printer)
                self.assert_parked_off(printer)
                self.assertEqual([t for t in printer.toolhead.extruder.heater.targets[targets:] if t > 0], [])
                self.assertLess(printer.reactor.monotonic() - started, 60.)
                self.assertGreaterEqual(printer.status("extruder")["temperature"], 170.)
                self.assertEqual(part_fan(printer), 1.)
                cool_nozzle(printer)
                self.assertEqual(part_fan(printer), 0.)
            for then, script, speed in (("heat", "M104 S200", 0.), ("fan set", "M106 S128", 128 / 255.)):
                with self.subTest(printer=printer_id, then=then):
                    printer = self.printer(printer_id, box)
                    printer.set_filament_detected(False)
                    printer.command("LOAD_FILAMENT_CONTROL SLOT=1")
                    printer.command(script)
                    printer.reactor.advance(printer.reactor.monotonic() + 2.)
                    self.assertAlmostEqual(part_fan(printer), speed, places=2)
                    printer.command("M104 S0")
                    cool_nozzle(printer, below=50.)
                    self.assertAlmostEqual(part_fan(printer), speed, places=2)

    def test_a_load_from_an_empty_slot_stops_and_flow_stop_turns_the_heater_off(self):
        """A load of a slot with nothing at its inlet stops with QDE_004_005 after the nozzle has heated, and nothing
        on the touchscreen or in Fluidd offered a way on (2026-10-31). FLOW_STOP has to end it with the heater off."""
        for printer_id in FIRMWARE:
            with self.subTest(printer=printer_id):
                box = BoxSetup(boxes=1, enable_box=1, tools={0: "slot0", 1: "slot1"}, loaded_slot=EMPTY_SLOT,
                               hub=None, inlets=frozenset({"slot0"}))
                printer = self.printer(printer_id, box)
                printer.set_filament_detected(False)
                printer.command("LOAD_FILAMENT_CONTROL SLOT=1")
                task = printer.status("multi_color_controller")["task"]
                self.assertEqual((task["current_step"], task["is_paused"]), ("BOX_LOAD", True))
                self.assertEqual(printer.status("extruder")["target"], 240.)
                printer.command("FLOW_STOP")
                self.assertEqual(printer.status("extruder")["target"], 0.)
                self.assertIsNone(printer.status("multi_color_controller")["task"]["flow_id"])
                self.assertEqual(printer.vendor.codes, ["QDE_004_005"])
                self.assertFalse(printer.is_shutdown())

    def test_an_external_spool_unload_on_a_box_printer_cuts_and_parks(self):
        """With the external spool loaded (last_load_slot slot16) an unload runs QIDI's external flow 7: heat at the
        chute, cut and pull 60 mm back, wipe, and park with the heater off."""
        for printer_id in FIRMWARE:
            with self.subTest(printer=printer_id):
                box = BoxSetup(boxes=1, enable_box=0, tools={0: "slot0"}, loaded_slot=EMPTY_SLOT, hub=None)
                printer = self.printer(printer_id, box)
                printer.command("UNLOAD_FILAMENT_CONTROL SLOT=16")
                checks.assert_no_errors(self, printer)
                self.assert_parked_off(printer)
                checks.assert_purges_only_at_the_chute(self, printer, 0, len(printer.toolhead.moves))
                self.assertEqual(checks.cuts(printer), 1)
                self.assertFalse(printer.status("filament_switch_sensor filament_switch_sensor")["filament_detected"])


if __name__ == "__main__":
    unittest.main()
