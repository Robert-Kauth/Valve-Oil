"""Real Klipper command admission versus the installer's OS lock, without hardware."""
import json
import unittest
from unittest import mock

from installer.runtime import safety
from installer.runtime.errors import ActivePrintError, LockAcquisitionError, PrinterStateError
from installer.runtime.locking import acquire
from installer.tests.gcode_flow.printer import FIRMWARE, FlowPrinter
from installer.tests.gcode_flow.qidi import BoxSetup
from installer.tests.helpers import MOONRAKER_QUERY_URL, moonraker_urlopen, load_source, REPO_ROOT
from installer.tests.klipper_fakes import load_extra


class PrintGuardTests(unittest.TestCase):
    def printer(self, printer_id="max4"):
        return FlowPrinter(printer_id, box=BoxSetup())

    def test_update_refuses_every_print_entry_on_every_printer(self):
        """API/slicer starts, SD selection/resume and QIDI recovery cannot bypass an update."""
        module = load_extra("print_guard")
        for printer_id in FIRMWARE:
            printer = self.printer(printer_id)
            lock = printer.root / module.INSTALLER_LOCK
            commands = (*module.COMMANDS, "_KM_RESUME_BASE", "M24.6245197")
            self.assertIn("_KM_RESUME_BASE", printer.gcode.ready_gcode_handlers)
            self.assertIn("M24.6245197", printer.gcode.ready_gcode_handlers)
            for command in commands:
                if command not in printer.gcode.ready_gcode_handlers:
                    continue  # QIDI recovery is not present on every model.
                with self.subTest(printer=printer_id, command=command):
                    with acquire(lock), self.assertRaisesRegex(printer.command_error, "Valve Oil is updating"):
                        printer.run(command)
                    self.assertFalse(printer.status("virtual_sdcard")["is_active"])
                    self.assertEqual(printer.status("extruder")["target"], 0.)
            # Releasing the installer lock restores printing; no sticky latch.
            printer.print_file("M118 MSG=print_admission_pass\n")
            self.assertEqual(printer.status("print_stats")["state"], "complete")

    def test_paused_print_resume_and_native_aliases_wait_without_moving(self):
        from installer.tests.gcode_flow import slicer

        for printer_id in FIRMWARE:
            for pack in slicer.PACKS:
                with self.subTest(printer=printer_id, slicer=pack):
                    printer = self.printer(printer_id)
                    printer.start_print(slicer.print_with_pause(pack, printer_id).text, nozzle=220.)
                    self.assertEqual(printer.status("print_stats")["state"], "paused")
                    assert printer.toolhead is not None
                    moves = len(printer.toolhead.moves)
                    with acquire(printer.root / ".vo_installer.lock"):
                        for command in ("RESUME", "_KM_RESUME_BASE", "RESUME_PRINT", "M24.6245197"):
                            with self.assertRaisesRegex(printer.command_error, "Valve Oil is updating"):
                                printer.run(command)
                        self.assertEqual(len(printer.toolhead.moves), moves)
                        self.assertTrue(printer.status("pause_resume")["is_paused"])
                    printer.run("RESUME")
                    printer.reactor.advance(printer.reactor.monotonic())
                    self.assertEqual(printer.status("print_stats")["state"], "complete")

    def test_print_admission_holds_lock_until_sd_work_is_visible(self):
        """An update loses during start; after start it sees active SD before print_stats updates."""
        printer = self.printer()
        sd = printer.lookup_object("virtual_sdcard")
        (printer.root / ".cache" / "guard.gcode").write_text("M118 MSG=pass\n")
        original = sd._load_file
        admitted = []

        def load(*args, **kwargs):
            with self.assertRaises(LockAcquisitionError):
                with acquire(printer.root / ".vo_installer.lock"):
                    self.fail("Updater entered during print admission")
            admitted.append(True)
            return original(*args, **kwargs)

        from installer.tests.gcode_flow import sdcard
        sdcard.point_at(printer)
        with mock.patch.object(sd, "_load_file", side_effect=load):
            printer.run("SDCARD_PRINT_FILE FILENAME=guard.gcode")
        self.assertEqual(admitted, [True])
        self.assertEqual(printer.status("print_stats")["state"], "standby")
        self.assertTrue(printer.status("virtual_sdcard")["is_active"])
        payload = {"result": {"status": {name: printer.status(name) for name in ("print_stats", "virtual_sdcard")}}}
        with acquire(printer.root / ".vo_installer.lock"):
            with self.assertRaises(ActivePrintError):
                safety.ensure_printer_idle(MOONRAKER_QUERY_URL, urlopen=moonraker_urlopen(raw_payload=payload))
        printer.reactor.advance(printer.reactor.monotonic())
        self.assertEqual(printer.status("print_stats")["state"], "complete")

    def test_pending_firmware_survives_process_unlock_and_failed_start_releases_lock(self):
        printer = self.printer()
        marker = printer.root / ".vo_firmware_update_pending.json"
        marker.write_text(json.dumps({"id": "test-update"}))
        with self.assertRaisesRegex(printer.command_error, "firmware update has not finished"):
            printer.run("SDCARD_PRINT_FILE FILENAME=missing.gcode")
        with acquire(printer.root / ".vo_installer.lock"):
            pass
        marker.unlink()
        with self.assertRaises(printer.command_error):
            printer.run("SDCARD_PRINT_FILE FILENAME=missing.gcode")
        with acquire(printer.root / ".vo_installer.lock"):
            pass
        printer.print_file("M118 MSG=recovered\n")
        self.assertEqual(printer.status("print_stats")["state"], "complete")

    def test_unsafe_lock_path_fails_closed(self):
        printer = self.printer()
        lock = printer.root / ".vo_installer.lock"
        lock.symlink_to(printer.root / "missing-lock-target")
        with self.assertLogs(level="ERROR"), self.assertRaisesRegex(printer.command_error, "Cannot verify"):
            printer.run("M24")
        self.assertFalse(printer.status("virtual_sdcard")["is_active"])

    def test_extracting_and_unknown_sd_state_are_not_idle(self):
        for state in ("printing", "paused", "extracting"):
            with self.subTest(state=state), self.assertRaises(ActivePrintError):
                safety.ensure_printer_idle(MOONRAKER_QUERY_URL, urlopen=moonraker_urlopen(state))
        for sd in ({}, {"is_active": None}, {"is_active": "false"}):
            payload = {"result": {"status": {"print_stats": {"state": "standby"}, "virtual_sdcard": sd}}}
            with self.subTest(sd=sd), self.assertRaises(PrinterStateError):
                safety.ensure_printer_idle(MOONRAKER_QUERY_URL, urlopen=moonraker_urlopen(raw_payload=payload))

    def test_status_service_reports_extracting_and_scheduled_start_as_busy(self):
        server = load_source("vo_status_print_guard", REPO_ROOT / "installer/system/vo_status.py")
        for state, active, expected in (("extracting", False, ("printing",)),
                                         ("starting", False, ("printer_state_unknown",)),
                                         ("standby", True, ("printing",)),
                                         ("standby", False, ())):
            status = {"print_stats": {"state": state}, "virtual_sdcard": {"is_active": active},
                      "webhooks": {"state": "ready"}, "heaters": {"available_heaters": []}}
            with mock.patch.object(server, "_moonraker_objects", return_value=status):
                self.assertEqual(server.live_printer(), expected)


if __name__ == "__main__":
    unittest.main()
