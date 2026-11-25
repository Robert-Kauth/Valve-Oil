"""Resuming a print after a power loss (QIDI's SDCARD_POWER_OFF_CONTINUES_PINT_FILE, which the touchscreen sends with
the state Moonraker saved) through QIDI's own virtual_sdcard code, with parts cancelled before the power went off."""
from __future__ import annotations

import base64
import itertools
import json
import unittest

from installer.tests.gcode_flow import checks, slicer
from installer.tests.gcode_flow.printer import FIRMWARE, FlowPrinter
from installer.tests.gcode_flow.qidi import BoxSetup

WALL_LINES = 400


def two_parts():
    """A layer of two parts, the first long enough that a restart 4 KiB back still lands inside it."""
    lines = ["EXCLUDE_OBJECT_DEFINE NAME=part_1 CENTER=45,80 POLYGON=[[40,70],[50,70],[50,90],[40,90]]",
             "EXCLUDE_OBJECT_DEFINE NAME=part_2 CENTER=125,80 POLYGON=[[120,70],[130,70],[130,90],[120,90]]",
             "G90", "M83", "EXCLUDE_OBJECT_START NAME=part_1"]
    lines += ["G1 X%.1f Y%.1f E0.05 F3000 ; outer wall" % (40 + i % 10, 70 + i % 20) for i in range(WALL_LINES)]
    lines += ["EXCLUDE_OBJECT_END NAME=part_1", "EXCLUDE_OBJECT_START NAME=part_2"]
    lines += ["G1 X%.1f Y%.1f E0.05 F3000 ; outer wall" % (120 + i % 10, 70 + i % 20) for i in range(20)]
    lines += ["EXCLUDE_OBJECT_END NAME=part_2"]
    return lines


def saved_state(file_position, cancelled):
    """What QIDI's Moonraker saves for a power-loss resume (its components/database.py and virtual_sdcard.py, Max 4
    01.01.06.05 and 01.01.07.01, Plus 5 and Smart 5 01.01.02.01): positions, temperatures, the file position, every
    defined part and the cancelled ones, but not the part being printed. With more than 64 parts it saves neither
    list."""
    params = {"gcode_position": [45, 80, 2.0, 0], "base_position": [0, 0, 0, 0], "homing_position": [0, 0, 0, 0],
              "all_objects": [{"name": "PART_1"}, {"name": "PART_2"}], "exclude_objects": cancelled,
              "extruder_target": 220, "heater_bed_target": 60, "file_position": file_position,
              "absolute_coord": True, "absolute_extrude": False, "speed_factor": 1 / 60., "extrude_factor": 1.}
    return base64.b64encode(json.dumps({"params": params}).encode()).decode()


class PowerLossResumeTests(checks.FlowTestCase):
    def test_a_cancelled_part_stays_skipped_after_a_power_loss_resume(self):
        """Issue #366: QIDI's resume cancels the saved cancelled parts again but restarts the file 4096 bytes before
        where the power went off, without the label of the part it restarts inside, so Klipper counted the print as
        outside every part and a cancelled part printed again, in the air, until its end. Valve Oil starts that
        part again once QIDI has put the toolhead back."""
        lines = two_parts()
        lost_at = sum(len(line) + 1 for line in lines[:5 + WALL_LINES - 20])
        for printer_id in FIRMWARE:
            with self.subTest(printer=printer_id):
                printer = FlowPrinter(printer_id, box=BoxSetup())
                (printer.root / ".cache/job.gcode").write_text("\n".join(lines), encoding="utf-8")
                printer.job_lines = lines
                printer.toolhead.moves.clear()
                printer.command("SDCARD_POWER_OFF_CONTINUES_PINT_FILE STATE=%s" % (saved_state(lost_at, ["PART_1"]),))

                # The restart lands partway through a line, whose rest Klipper doesn't know as a command.
                text = "\n".join(lines)
                restart = text[lost_at - 4096:].split("\n")[0]
                printer.unknown_commands = [c for c in printer.unknown_commands if c != restart]
                checks.assert_no_errors(self, printer)
                self.assertEqual(printer.status("print_stats")["state"], "complete")
                printed = [m for m in printer.toolhead.moves if m.extrude > 0 and m.xy_moved]
                self.assertEqual([m.end[:2] for m in printed if m.end[0] < 90], [])
                self.assertEqual(sum(1 for m in printed if m.end[0] > 90), 20)

    def test_a_power_loss_inside_a_filament_change_in_a_cancelled_part_finishes_the_change(self):
        """Power lost during a filament change that runs inside a cancelled part (OrcaSlicer writes the change inside
        the label of the part printed before it): QIDI's resume restarts 4 KiB back, inside that part. The change has
        to run again at the chute, with its moves let through the cancelled part, while the part's own moves stay
        dropped and the next part prints (Max 4, 26.11.20.1, 2026-11-22: after a power cut and resume the cancelled
        part printed again). A change longer than that, as custom change G-code can be, restarts after its move to
        the chute, so the resume has to take the toolhead there before the flush (review, 2026-11-24: about 160 mm
        purged where QIDI put the toolhead back)."""
        filaments = [slicer.Filament(temperature=220), slicer.Filament(temperature=230, range_high=250)]
        for printer_id, slicer_name, long_change in itertools.product(FIRMWARE, slicer.PACKS, (False, True)):
            with self.subTest(printer=printer_id, slicer=slicer_name, long_change=long_change):
                change = slicer.expand(slicer.pack_file(slicer_name, printer_id, "change_filament.gcode"),
                                       slicer.change_filament_context(filaments, 0, 1, slicer.Layer())).splitlines()
                if long_change:
                    flush = change.index("; FLUSH_START")
                    change[flush:flush] = ["; custom change G-code %03d " % (i,) + "." * 40 for i in range(80)]
                lines = two_parts()
                end_1 = lines.index("EXCLUDE_OBJECT_END NAME=part_1")
                lines[end_1:end_1] = change
                # Power goes off at the change's first flush, after QIDI's tool change.
                lost_at = sum(len(line) + 1 for line in lines[:lines.index("; FLUSH_START")])
                printer = FlowPrinter(printer_id, box=BoxSetup())
                (printer.root / ".cache/job.gcode").write_text("\n".join(lines), encoding="utf-8")
                printer.job_lines = lines
                printer.toolhead.moves.clear()
                printer.command("SDCARD_POWER_OFF_CONTINUES_PINT_FILE STATE=%s"
                                % (saved_state(lost_at, ["PART_1"]),))

                restart = "\n".join(lines)[lost_at - 4096:].split("\n")[0].strip()
                printer.unknown_commands = [c for c in printer.unknown_commands if c != restart]
                checks.assert_no_errors(self, printer)
                self.assertEqual(printer.status("print_stats")["state"], "complete")
                part_2 = next(i for i, m in enumerate(printer.toolhead.moves) if m.end[0] > 90 and m.extrude > 0
                              and not checks.at_chute(printer, m.end[:2]))
                checks.assert_purges_only_at_the_chute(self, printer, 0, part_2)
                checks.assert_rear_moves_around_the_chute(self, printer)
                purged = sum(out for move, out in list(checks.pushed_out(printer.toolhead.moves))[:part_2])
                self.assertGreater(purged, 0.)
                printed = [m for m in printer.toolhead.moves[part_2:] if m.extrude > 0 and m.xy_moved]
                self.assertEqual(len(printed), 20)
                self.assertTrue(all(120 <= m.end[0] <= 130 and 70 <= m.end[1] <= 90 for m in printed))


if __name__ == "__main__":
    unittest.main()
