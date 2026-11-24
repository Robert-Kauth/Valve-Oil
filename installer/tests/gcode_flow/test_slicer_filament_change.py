"""A slicer filament change during a print (OrcaSlicer's change of filament at a layer, QIDI Studio's tool change),
on every printer and from both slicer packs, through every branch of QIDI's T_MANAGER (qidi.Vendor.cmd_T_MANAGER):
what the printer does from the change's first line until the print carries on or pauses."""
from __future__ import annotations

import logging
import unittest
from dataclasses import dataclass

from installer.tests.gcode_flow import checks, slicer
from installer.tests.gcode_flow.printer import FIRMWARE, FlowPrinter
from installer.tests.gcode_flow.qidi import BoxSetup

ONE_BOX = dict(boxes=1, current_tool=0)


@dataclass(frozen=True)
class Case:
    name: str
    why: str
    box: BoxSetup
    following: int = 1
    toolhead_filament: bool = True
    cuts: int = 0
    pauses: bool = False
    codes: tuple = ()
    armed: str | None = None


CASES = (
    Case("no Box",
         "QIDI's T_MANAGER does nothing without the Box, so Valve Oil, which hands the cut and the chute move to it, "
         "must still reach the chute before the purge. It didn't, and on the Max 4 the toolhead drove into the back "
         "of the printer and shifted the print (issue #397).",
         BoxSetup()),
    Case("Box off",
         "With Use QIDI Box off, as for an external spool on a Box printer, T_MANAGER does nothing either (#397).",
         BoxSetup(enable_box=0, tools={0: "slot0", 1: "slot1"}, loaded_slot="slot0", **ONE_BOX)),
    Case("another Box slot",
         "QIDI's flow cuts, unloads, and loads; Valve Oil must not cut a second time or purge before the flow is "
         "back at the chute.",
         BoxSetup(enable_box=1, tools={0: "slot0", 1: "slot1"}, loaded_slot="slot0", **ONE_BOX), cuts=1),
    Case("two tools on one slot",
         "T_MANAGER returns at once when both tools map to the same slot; the purge still has to be at the chute.",
         BoxSetup(enable_box=1, tools={0: "slot0", 1: "slot0"}, loaded_slot="slot0", **ONE_BOX)),
    Case("the new tool's slot already loaded",
         "T_MANAGER returns without moving when the new tool's slot is the one loaded, as after a resume reloaded "
         "it; the purge still has to be at the chute.",
         BoxSetup(enable_box=1, tools={0: "slot0", 1: "slot1"}, loaded_slot="slot1", **ONE_BOX), armed="slot1"),
    Case("the new tool's slot at the hub, toolhead empty",
         "T_MANAGER unloads and reloads the slot without cutting; nothing may be pushed out away from the chute.",
         BoxSetup(enable_box=1, tools={0: "slot0", 1: "slot1"}, loaded_slot="slot1", **ONE_BOX),
         toolhead_filament=False),
    Case("a tool with no Box slot",
         "A change to a tool beyond the connected Boxes' slots pauses the print with QDE_004_027 (docs/qidi-box.md); "
         "the pause has to park at the chute, not over the print.",
         BoxSetup(enable_box=1, tools={0: "slot0"}, loaded_slot="slot0", **ONE_BOX), following=5, pauses=True,
         codes=("QDE_004_027",)),
)


class SlicerFilamentChangeTests(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)

    def test_filament_changes_purge_at_the_chute_cut_once_and_carry_on(self):
        filaments = [slicer.Filament(temperature=220)] + [slicer.Filament(temperature=230, range_high=250)] * 5
        for printer_id in FIRMWARE:
            for slicer_name in slicer.PACKS:
                for case in CASES:
                    with self.subTest(printer=printer_id, slicer=slicer_name, case=case.name, why=case.why):
                        printer = FlowPrinter(printer_id, box=case.box, filament_detected=case.toolhead_filament)
                        job = slicer.print_with_change(slicer_name, printer_id, filaments=filaments, current=0,
                                                       following=case.following)
                        printer.start_print(job.text)

                        checks.assert_no_errors(self, printer, case.codes)
                        first = checks.moves_from_line(printer, job.change_line)
                        checks.assert_rear_moves_around_the_chute(self, printer)
                        self.assertEqual(checks.cuts(printer), case.cuts)
                        if case.armed:
                            # T_MANAGER's return re-arms the slot's auto-feed and gears no slot to the extruder.
                            autofeed = printer.vendor.autofeed
                            self.assertEqual(autofeed.bind_stepper, case.armed)
                            self.assertFalse(any(s.extruder_stepper.synced for s in printer.vendor.steppers.values()))
                        if case.pauses:
                            self.assertTrue(printer.status("pause_resume")["is_paused"])
                            self.assertEqual(printer.status("print_stats")["state"], "paused")
                            self.assertTrue(checks.at_chute(printer, printer.toolhead.commanded_pos[:2]))
                            checks.assert_purges_only_at_the_chute(self, printer, first, len(printer.toolhead.moves))
                            continue
                        self.assertEqual(printer.status("print_stats")["state"], "complete")
                        self.assertFalse(printer.status("pause_resume")["is_paused"])
                        checks.assert_purges_only_at_the_chute(
                            self, printer, first, checks.moves_from_line(printer, job.change_line + job.change_lines))
                        state = printer.status("gcode_move")
                        self.assertTrue(state["absolute_coordinates"])
                        self.assertFalse(state["absolute_extrude"])
                        self.assertEqual(printer.toolhead.moves[-1].end[:2],
                                         (job.resume_xy[0] + 5, job.resume_xy[1]))
                        self.assertEqual(printer.status("extruder")["target"], filaments[1].temperature)


FAILURES = (
    ("filament caught at the toolhead",
     "Issue #348: QIDI's unload pulled on filament the toolhead held for a minute and a half. Valve Oil stops 20 mm "
     "past the pull-back and pauses the print saying so; once the filament is free, RESUME finishes the change.",
     dict(caught=True), "Something is holding the filament", "caught"),
    ("a Box motor error in the unload",
     "A Box homing timeout raised out of QIDI's unload and the print was cancelled through on_error_gcode (test "
     "printer klippy.log, 2026-11-03). Valve Oil pauses it instead, and RESUME redoes the change.",
     dict(unload_error="Error during homing b_endstop: Communication timeout during homing"),
     "Communication timeout during homing", "unload_error"),
    ("the loaded slot ran out with auto-reload on",
     "QIDI's auto-reload raises AttributeError before switching slots (harness, 2026-11-20; operator klippy.log the "
     "same day). Valve Oil pauses the print naming the slot that ran out, and RESUME loads the new tool's slot.",
     dict(auto_reload_detect=1, inlets=frozenset({"slot1"})), "1A ran out of filament", None),
)


class FailedFilamentChangeTests(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)

    def test_a_change_that_fails_pauses_the_print_and_resume_finishes_it(self):
        filaments = [slicer.Filament(temperature=220), slicer.Filament(temperature=230, range_high=250)]
        for printer_id in FIRMWARE:
            for name, why, fault, message, cleared in FAILURES:
                with self.subTest(printer=printer_id, case=name, why=why):
                    box = BoxSetup(boxes=1, enable_box=1, tools={0: "slot0", 1: "slot1"}, loaded_slot="slot0",
                                   current_tool=0, **fault)
                    printer = FlowPrinter(printer_id, box=box)
                    job = slicer.print_with_change("orca", printer_id, filaments=filaments, current=0, following=1)
                    printer.start_print(job.text)

                    self.assertFalse(printer.is_shutdown(), printer.shutdown_message)
                    self.assertEqual(printer.status("print_stats")["state"], "paused")
                    self.assertEqual(len(printer.responses.errors), 1)
                    self.assertIn(message, printer.responses.errors[0])
                    self.assertTrue(checks.at_chute(printer, printer.toolhead.commanded_pos[:2]))
                    first = checks.moves_from_line(printer, job.change_line)
                    checks.assert_purges_only_at_the_chute(self, printer, first, len(printer.toolhead.moves))

                    if cleared:
                        setattr(printer.vendor, cleared, cleared == "caught" and False or None)
                    printer.responses.lines.clear()
                    printer.command("RESUME")
                    checks.assert_no_errors(self, printer)
                    self.assertEqual(printer.status("print_stats")["state"], "complete")
                    self.assertEqual(printer.variables()["last_load_slot"], "slot1")
                    checks.assert_purges_only_at_the_chute(
                        self, printer, first, checks.moves_from_line(printer, job.change_line + job.change_lines))
                    checks.assert_rear_moves_around_the_chute(self, printer)
                    self.assertEqual(printer.toolhead.moves[-1].end[:2], (job.resume_xy[0] + 5, job.resume_xy[1]))


class CancelledPartFilamentChangeTests(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)

    def test_a_change_inside_a_cancelled_part_still_cuts_unloads_and_loads(self):
        """Klipper drops every move inside a cancelled part, extruder moves too, and OrcaSlicer writes a filament
        change inside the label of the part printed just before it. After an operator cancelled a part, its change
        ran without the toolhead: no cut, an unload that stopped with filament at the toolhead, and a paused print
        whose retracts did nothing (Max 4, 26.11.20.1, 2026-11-22). Valve Oil lets the change's moves through, first
        raising the nozzle to where the change expects it, while the cancelled part's own moves stay dropped."""
        filaments = [slicer.Filament(temperature=220), slicer.Filament(temperature=230, range_high=250)]
        for printer_id in FIRMWARE:
            with self.subTest(printer=printer_id):
                box = BoxSetup(boxes=1, enable_box=1, tools={0: "slot0", 1: "slot1"}, loaded_slot="slot0",
                               current_tool=0)
                printer = FlowPrinter(printer_id, box=box)
                job = slicer.print_with_change("orca", printer_id, filaments=filaments, current=0, following=1,
                                               cancelled=True)
                printer.start_print(job.text)
                checks.assert_no_errors(self, printer)
                self.assertEqual(printer.status("print_stats")["state"], "complete")
                self.assertEqual(checks.cuts(printer), 1)
                self.assertEqual(printer.variables()["last_load_slot"], "slot1")
                first = checks.moves_from_line(printer, job.change_line)
                resumed = checks.moves_from_line(printer, job.change_line + job.change_lines)
                checks.assert_purges_only_at_the_chute(self, printer, first, resumed)
                # The cancelled part's moves after Klipper's five learning extrusions are dropped.
                part_moves = printer.toolhead.moves[:first]
                self.assertEqual([m for m in part_moves if m.end[:3] == (*job.resume_xy, 2.) and m.extrude > 0], [])
                lift = printer.toolhead.moves[first]
                self.assertEqual(lift.start[:2], lift.end[:2])
                self.assertGreaterEqual(lift.end[2], 5.)


if __name__ == "__main__":
    unittest.main()
