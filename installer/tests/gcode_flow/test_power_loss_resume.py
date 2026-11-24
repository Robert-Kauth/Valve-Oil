"""Resuming a print after a power loss (QIDI's SDCARD_POWER_OFF_CONTINUES_PINT_FILE, which the touchscreen sends with
the state Moonraker saved) through QIDI's own virtual_sdcard code, with parts cancelled before the power went off."""
from __future__ import annotations

import base64
import json
import logging
import unittest

from installer.tests.gcode_flow import checks
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


class PowerLossResumeTests(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)

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


if __name__ == "__main__":
    unittest.main()
