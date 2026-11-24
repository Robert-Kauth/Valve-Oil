"""A pause from the slicer during a print (OrcaSlicer's Add pause, QIDI Studio's pause), as for swapping filament by
hand, and the resume after it, on every printer and from both slicer packs."""
from __future__ import annotations

import unittest

from installer.tests.gcode_flow import checks, slicer
from installer.tests.gcode_flow.printer import FIRMWARE, FlowPrinter
from installer.tests.gcode_flow.qidi import BoxSetup

ONE_BOX = dict(boxes=1, loaded_slot="slot0", current_tool=0, tools={0: "slot0", 1: "slot1"})
CASES = {
    "no Box": BoxSetup(),
    "Box off": BoxSetup(enable_box=0, **ONE_BOX),
    "Box on": BoxSetup(enable_box=1, **ONE_BOX),
}
PRINT_TEMPERATURE = 220.


class SlicerPauseTests(checks.FlowTestCase):
    def paused(self, printer_id, slicer_name, box):
        printer = FlowPrinter(printer_id, box=box)
        job = slicer.print_with_pause(slicer_name, printer_id)
        printer.start_print(job.text, nozzle=PRINT_TEMPERATURE)
        return printer, job

    def test_a_pause_parks_at_the_chute_with_the_nozzle_off(self):
        """A pause leaves the printer for minutes or hours while the operator swaps filament by hand. The nozzle has
        to be off the print, at the chute where anything that oozes or is pushed through drops away, and the heater
        off, whatever the Box setup."""
        for printer_id in FIRMWARE:
            for slicer_name in slicer.PACKS:
                for case, box in CASES.items():
                    with self.subTest(printer=printer_id, slicer=slicer_name, case=case):
                        printer, job = self.paused(printer_id, slicer_name, box)
                        checks.assert_no_errors(self, printer)
                        self.assertTrue(printer.status("pause_resume")["is_paused"])
                        self.assertEqual(printer.status("print_stats")["state"], "paused")
                        self.assertEqual(printer.status("extruder")["target"], 0.)
                        self.assertTrue(checks.at_chute(printer, printer.toolhead.commanded_pos[:2]))
                        checks.assert_purges_only_at_the_chute(
                            self, printer, printer.moves_from_line(job.change_line),
                            len(printer.toolhead.moves))
                        checks.assert_rear_moves_around_the_chute(self, printer)

    def test_a_box_resume_reloads_at_the_chute_and_returns_to_the_print(self):
        """QIDI's resume with the Box enabled reloads the tool's slot from whatever the sensors show, or only re-arms
        it when it's loaded, armed, and at the toolhead (qidi.BoxExtras.try_resume_print_box1). Stock PAUSE turns the nozzle off, and QIDI cuts before it heats, so a resume stopped
        with "Extrude below minimum temp" until the nozzle was heated by hand (operator report, 2026-11-03); Valve Oil
        heats at the chute first. Whatever the reload does, it has to purge only at the chute and put the nozzle back
        where the print stopped, at the print's temperature."""
        branches = (
            ("the tool's slot loaded and armed", True, "slot0", 0),
            ("filament at the toolhead", True, "slot0", 1),
            ("filament at the hub only", False, "slot0", 0),
            ("nothing loaded", False, "slot16", 0),
        )
        for printer_id in FIRMWARE:
            for slicer_name in slicer.PACKS:
                for branch, toolhead_filament, loaded_slot, cuts in branches:
                    with self.subTest(printer=printer_id, slicer=slicer_name, branch=branch):
                        printer, job = self.paused(printer_id, slicer_name, CASES["Box on"])
                        if branch == "the tool's slot loaded and armed":
                            printer.run("SAVE_VARIABLE VARIABLE=slot_sync VALUE='\"slot0\"'")
                        if loaded_slot != "slot0":
                            printer.run("SAVE_VARIABLE VARIABLE=last_load_slot VALUE='\"%s\"'" % (loaded_slot,))
                            printer.vendor.hub_slot = None
                        printer.set_filament_detected(toolhead_filament)
                        paused_at = len(printer.toolhead.moves)
                        printer.trace.clear()
                        printer.run("RESUME")
                        printer.reactor.advance(printer.reactor.monotonic())

                        checks.assert_no_errors(self, printer)
                        self.assertEqual(printer.status("print_stats")["state"], "complete")
                        resumed_at = printer.moves_from_line(job.change_line + job.change_lines)
                        checks.assert_purges_only_at_the_chute(self, printer, paused_at, resumed_at)
                        checks.assert_rear_moves_around_the_chute(self, printer)
                        self.assertEqual(checks.cuts(printer), cuts)
                        self.assertEqual(printer.variables()["last_load_slot"], "slot0")
                        self.assertEqual(printer.toolhead.moves[resumed_at - 1].end[:3], (*job.resume_xy, 2.))
                        self.assertEqual(printer.status("extruder")["target"], PRINT_TEMPERATURE)

    def test_a_resume_without_the_box_enabled_continues_only_with_filament_loaded(self):
        """Without the Box, Valve Oil's RESUME hands over to QIDI's TRY_RESUME_PRINT whenever a QIDI module is loaded,
        which is every printer (#397), and QIDI's then only checks the toolhead sensor. After a hand swap the print
        has to carry on where it stopped, at the print's temperature, purging only at the chute; with no filament
        loaded it has to stay paused rather than print in the air."""
        for printer_id in FIRMWARE:
            for slicer_name in slicer.PACKS:
                for case in ("no Box", "Box off"):
                    for loaded in (True, False):
                        with self.subTest(printer=printer_id, slicer=slicer_name, case=case, loaded=loaded):
                            printer, job = self.paused(printer_id, slicer_name, CASES[case])
                            printer.set_filament_detected(loaded)
                            paused_at = len(printer.toolhead.moves)
                            printer.run("RESUME")
                            printer.reactor.advance(printer.reactor.monotonic())

                            checks.assert_no_errors(self, printer)
                            if not loaded:
                                self.assertEqual(printer.status("print_stats")["state"], "paused")
                                self.assertEqual(len(printer.toolhead.moves), paused_at)
                                continue
                            self.assertEqual(printer.status("print_stats")["state"], "complete")
                            resumed_at = printer.moves_from_line(job.change_line + job.change_lines)
                            checks.assert_purges_only_at_the_chute(self, printer, paused_at, resumed_at)
                            checks.assert_rear_moves_around_the_chute(self, printer)
                            self.assertEqual(printer.toolhead.moves[resumed_at - 1].end[:3], (*job.resume_xy, 2.))
                            self.assertEqual(printer.status("extruder")["target"], PRINT_TEMPERATURE)


class PauseInsideACancelledPartTests(checks.FlowTestCase):
    def test_the_operator_can_move_and_retract_while_paused_inside_a_cancelled_part(self):
        """Klipper drops every move inside a cancelled part, extruder moves included. An operator's print paused
        inside a cancelled part, and retracts from the touchscreen did nothing (Max 4, 26.11.20.1, 2026-11-22).
        Valve Oil lets moves through while the print is paused; once it resumes, the part's own moves are dropped
        again. OrcaSlicer holds a part's EXCLUDE_OBJECT_END back until the next part starts (slicer.py), so a pause
        lands inside the part printed before it. The pause's own park has to go through too: Klipper's print state
        says paused only once the PAUSE line has finished, and the park came before that, so the nozzle stayed over
        the print (review, 2026-11-24)."""
        for printer_id in FIRMWARE:
            for slicer_name in slicer.PACKS:
                with self.subTest(printer=printer_id, slicer=slicer_name):
                    pause = slicer.expand(slicer.pack_file(slicer_name, printer_id, "pause.gcode"),
                                          {"layer_num": 9, "layer_z": 2.}).splitlines()
                    lines = [
                        "EXCLUDE_OBJECT_DEFINE NAME=part_1 CENTER=45,80 POLYGON=[[40,70],[50,70],[50,90],[40,90]]",
                        "EXCLUDE_OBJECT_DEFINE NAME=part_2 CENTER=125,80 POLYGON=[[120,70],[130,70],[130,90],"
                        "[120,90]]",
                        "EXCLUDE_OBJECT NAME=part_1", "EXCLUDE_OBJECT_START NAME=part_1",
                        # Klipper lets a cancelled part's first five extrusions through while it learns the
                        # extruder's position.
                        *("G1 X%d Y75 E0.2 F3000" % (40 + i,) for i in range(5)),
                        "G1 X45 Y80 E0.5 F3000", *pause, "G1 X48 Y85 E0.5 F3000",
                        "EXCLUDE_OBJECT_END NAME=part_1", "EXCLUDE_OBJECT_START NAME=part_2",
                        "G1 X120 Y80 F12000", "G1 E0.8 F2100", "G1 X125 Y80 E0.25 F3000",
                        "EXCLUDE_OBJECT_END NAME=part_2",
                    ]
                    printer = FlowPrinter(printer_id, box=BoxSetup())
                    printer.start_print("\n".join(lines), at=(45., 80., 2.))
                    self.assertEqual(printer.status("print_stats")["state"], "paused")
                    self.assertTrue(checks.at_chute(printer, printer.toolhead.commanded_pos[:2]))
                    self.assertEqual(printer.status("extruder")["target"], 0.)

                    paused_at = len(printer.toolhead.moves)
                    printer.command("G91\nG1 Z5 F600\nG90")
                    printer.command("G1 X100 Y60 F6000")
                    printer.command("M83\nG1 E-5 F300")
                    moved = printer.toolhead.moves[paused_at:]
                    self.assertTrue(any(m.end[2] > m.start[2] for m in moved))
                    self.assertEqual(moved[-1].end[:2], (100., 60.))
                    self.assertEqual(moved[-1].extrude, -5.)

                    printer.command("RESUME")
                    checks.assert_no_errors(self, printer)
                    self.assertEqual(printer.status("print_stats")["state"], "complete")
                    printed = [m.end[:2] for m in printer.toolhead.moves if m.extrude > 0 and m.xy_moved]
                    self.assertNotIn((45., 80.), printed)
                    self.assertNotIn((48., 85.), printed)
                    self.assertEqual(printer.toolhead.moves[-1].end[:2], (125., 80.))


if __name__ == "__main__":
    unittest.main()
