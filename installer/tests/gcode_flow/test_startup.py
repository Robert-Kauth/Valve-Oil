"""Klipper starting up with Valve Oil installed: QIDI's startup G-code against the order QIDI's modules get ready
in."""
from __future__ import annotations

import unittest

from installer.tests.gcode_flow import checks
from installer.tests.gcode_flow.printer import FIRMWARE, FlowPrinter
from installer.tests.gcode_flow.qidi import BoxSetup


class StartupTests(checks.FlowTestCase):
    def test_a_slow_start_reads_spool_tags_once_qidis_box_code_is_ready(self):
        """QIDI's INIT_GLOBALS runs INIT_RFID_READ 5 s into Klipper's ready callbacks, and QIDI's Box controller
        takes its saved variables and adapter only in its own ready callback, one of the last. When an earlier
        callback waited past those 5 s the command raised and Klipper shut down until a firmware restart, with or
        without a Box (issue 265). Valve Oil holds the read until the controller is ready."""
        for printer_id in FIRMWARE:
            for name, box in (("no Box", BoxSetup()), ("one Box", BoxSetup(boxes=1, enable_box=1))):
                with self.subTest(printer=printer_id, box=name):
                    printer = FlowPrinter(printer_id, box=box, slow_ready=6.)
                    printer.reactor.advance(printer.reactor.monotonic() + 10.)
                    self.assertFalse(printer.is_shutdown(), printer.shutdown_message)
                    self.assertEqual(printer.responses.errors, [])
                    self.assertEqual(printer.lookup_object("multi_color_controller").rfid_reads, 1)


if __name__ == "__main__":
    unittest.main()
