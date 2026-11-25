from __future__ import annotations

import unittest
from pathlib import Path

from installer.runtime import diagnostics
from installer.tests.helpers import FIXTURES_ROOT, temp_path

LOGS = FIXTURES_ROOT / "diagnostics"
# Lines box_flow_recovery writes for an unload from the toolhead (26.11.22.2 and later): one whose pull-backs Klipper
# dropped inside a cancelled part, and one that cleared the toolhead sensor.
STAMP = "2026-11-22 12:22:34 - "
DROPPED_UNLOAD = [
    STAMP + "vo_box_flow_recovery: Box unload of 1A stopped: Klipper didn't move the extruder for the 105 mm of "
            "pull-back QIDI's unload asked for because the print is inside part_2, a cancelled part, where Klipper "
            "drops every move, so the toolhead filament sensor still detects filament and the Box hasn't pulled it. "
            "Check that the extruder turns.",
    STAMP + "vo_box_flow_recovery: 1A unload from the toolhead: toolhead sensor filament -> filament; hub sensor "
            "filament -> filament; extruder pulled back 105 mm, 20 of them after its pull-back, and moved 0 mm; slot "
            "motor moved 0 mm during the pulls; Box buffer 0 feeds before it since the last unload from the toolhead, "
            "0 during it, switch released; raised CommandError: Box unload of 1A stopped",
]
CLEARED_UNLOAD = [
    STAMP + "vo_box_flow_recovery: 1C unload from the toolhead: toolhead sensor filament -> clear, cleared after 85 mm; "
            "hub sensor filament -> clear; extruder pulled back 85 mm, 0 of them after its pull-back, and moved 85 mm; "
            "slot motor moved 85 mm during the pulls; Box buffer 3 feeds before it since the last unload from the "
            "toolhead, 0 during it, switch released; returned True",
]

# Lines vo_klipper_health writes when the THR stops taking Klipper's commands while probing, as in issue #412: when the
# stall passes 3 s, when Klipper shuts down during it, and when the THR takes commands again.
THR_STALL = [
    "2026-11-22 12:30:00 - vo_klipper_health: THR has taken no commands from Klipper for 3 s, though it still sends "
    "its own reports. Commands to it, heater changes included, wait until it takes them again.",
    "2026-11-22 12:30:26 - vo_klipper_health: THR had taken no commands from Klipper for 29 s, from 12:29:57, when "
    "Klipper shut down; Klipper resent 820 bytes to it meanwhile. Its commands, heater changes included, were "
    "waiting, which can cause the shutdown's error.",
    "2026-11-22 12:30:39 - vo_klipper_health: THR took no commands from Klipper for about 42 s, from 12:29:57; "
    "Klipper resent 1331 bytes to it meanwhile. Commands sent to it in that time, heater changes included, reached it "
    "late.",
]
HEALTH_ONLY = [
    "2026-11-22 12:30:00 - vo_klipper_health: timers=53 removed=0 main_thread_load=0.17 peak=0.26 max_delay=0.017",
    "2026-11-22 12:30:06 - vo_klipper_health: 33 endstop syncs from 12:29:01 passed; THR spare 26.7 ms from mcu, "
    "delay 6.4 ms, no link errors; mcu spare 28.6 ms from THR, delay 6.2 ms, no link errors",
]


def _log(lines: list[str], version: str = "26.11.22.2") -> Path:
    path = temp_path("vo-diagnostics-") / "klippy.log"
    path.write_text("\n".join([
        "2026-11-22 12:00:00 - Start printer at Sun Nov 22 12:00:00 2026 (1795347216.0 17.3)",
        f'variable_package_version = "{version}"',
        *lines,
    ]) + "\n")
    return path


def _found(path: Path, **options) -> dict[str, dict]:
    return {finding["id"]: finding for finding in diagnostics.check([path], **options)["findings"]}


class SignatureTest(unittest.TestCase):
    """Each signature against a log that shows it and one that doesn't; a signature ships only with both."""

    def test_signatures_in_logs_that_show_them(self) -> None:
        for name, path, expected in (
            ("auto-feed re-armed during a feed, an off step clock, and the unloads it stopped",
             LOGS / "step-clock-offset.log",
             {"autofeed_rearmed_during_feed", "box_step_clock_offset", "unload_toolhead_sensor_stuck", "qidi_error"}),
            ("pull-backs dropped inside a cancelled part", _log(DROPPED_UNLOAD),
             {"cancelled_part_dropped_moves"}),
            ("toolhead load-cell readings nothing waited for, and the load they failed", LOGS / "load-cell-reply-lost.log",
             {"load_cell_reply_lost", "qidi_error"}),
            ("the THR stopped taking commands while probing", _log(THR_STALL), {"board_command_stall"}),
        ):
            with self.subTest(name):
                self.assertEqual(set(_found(path)), expected)

    def test_logs_that_dont_show_them(self) -> None:
        for name, path in (
            ("feeds that keep their clock, and an idle re-arm", LOGS / "box-feeds-normal.log"),
            ("an unload whose pull-backs moved", _log(CLEARED_UNLOAD)),
            ("Klipper's health lines without a stall", _log(HEALTH_ONLY)),
        ):
            with self.subTest(name):
                self.assertEqual(_found(path), {})

    def test_step_clock_offset_names_how_far_off(self) -> None:
        finding = _found(LOGS / "step-clock-offset.log")["box_step_clock_offset"]
        self.assertEqual(finding["details"], ["Box 1 slot stepper oid 1: 21.8 s ahead of Klipper's"])
        self.assertTrue(finding["docs"].startswith(diagnostics.DOCS_URL + "#"))

    def test_command_stall_counts_each_stall_once_with_its_length(self) -> None:
        finding = _found(_log(THR_STALL + THR_STALL[:1]))["board_command_stall"]
        self.assertEqual(finding["count"], 2)
        self.assertEqual(finding["severity"], "fault")
        self.assertEqual(finding["details"], ["THR: 29 s when Klipper shut down", "THR: 42 s"])
        self.assertEqual(finding["first"], "2026-11-22 12:30:00")
        self.assertEqual(len(finding["evidence"]), 4)

    def test_severity_follows_the_release_that_ran_and_the_one_installed(self) -> None:
        offset = LOGS / "step-clock-offset.log"
        self.assertEqual(_found(offset)["box_step_clock_offset"]["severity"], "fault")
        self.assertEqual(_found(offset, installed="26.11.22.2")["box_step_clock_offset"]["severity"], "fixed")
        # A session whose unloads reset the slot clock repaired it, whatever release its config named.
        lines = offset.read_text().splitlines()
        repaired = _log([*lines, "2026-11-22 13:00:28 - vo_box_flow_recovery: 1A step clock reset before the unload's "
                                 "pull-back"], version="26.11.22.1")
        found = _found(repaired)
        self.assertEqual((found["box_step_clock_offset"]["severity"], found["box_step_clock_offset"]["repaired"]),
                         ("repaired", 1))
        # QIDI's error codes are for reference only.
        self.assertEqual(found["qidi_error"]["severity"], "info")


class BudgetTest(unittest.TestCase):
    """A check reads the newest part of the logs within its budget, and says how far back it reached."""

    def setUp(self) -> None:
        offset = (LOGS / "step-clock-offset.log").read_text()
        filler = "".join(f"2026-11-22 13:{minute:02d}:00 - Stats {minute}: gcodein=0 mcu: mcu_awake=0.04\n"
                         for minute in range(60)) * 40
        self.path = temp_path("vo-diagnostics-") / "klippy.log"
        self.path.write_text(offset + filler + "2026-11-22 14:00:00 - Start printer at Sun Nov 22 14:00:00 2026\n"
                             + filler.replace(" 13:", " 14:"))
        self.size = self.path.stat().st_size

    def test_the_whole_log_within_the_budget(self) -> None:
        result = diagnostics.check([self.path])
        self.assertTrue(result["checked"]["complete"])
        self.assertIn("box_step_clock_offset", {f["id"] for f in result["findings"]})

    def test_a_log_larger_than_the_budget_is_read_from_its_newest_part(self) -> None:
        result = diagnostics.check([self.path], byte_budget=self.size // 3)
        self.assertFalse(result["checked"]["complete"])
        self.assertGreater(result["checked"]["from"], "2026-11-22 13:00:00")
        self.assertEqual(result["findings"], [])
        self.assertIn("not the whole period", diagnostics.report(result))

    def test_since_klipper_last_started(self) -> None:
        result = diagnostics.check([self.path], since="start")
        self.assertTrue(result["checked"]["complete"])
        self.assertEqual(result["checked"]["from"], "2026-11-22 14:00:00")
        self.assertLess(result["checked"]["files"][0]["bytes_read"], self.size // 2)

    def test_a_check_that_runs_out_of_time_says_so(self) -> None:
        ticks = iter(range(0, 10**6, 100))
        result = diagnostics.check([self.path], time_budget=1, clock=lambda: next(ticks))
        self.assertEqual((result["checked"]["stopped"], result["checked"]["complete"]), ("time", False))


if __name__ == "__main__":
    unittest.main()
