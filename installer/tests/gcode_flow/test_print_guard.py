"""Real Klipper command admission versus the installer's OS lock, without hardware."""
import json
import unittest
from unittest import mock

from installer.runtime import safety
from installer.runtime.errors import ActivePrintError, LockAcquisitionError, PrinterStateError
from installer.runtime.locking import acquire, acquire_shared
from installer.tests.gcode_flow.printer import FIRMWARE, FlowPrinter
from installer.tests.gcode_flow.qidi import BoxSetup
from installer.tests.helpers import MOONRAKER_QUERY_URL, moonraker_urlopen, load_source, REPO_ROOT
from installer.tests.klipper_fakes import load_extra


class PrintGuardTests(unittest.TestCase):
    def printer(self, printer_id="max4"):
        return FlowPrinter(printer_id, box=BoxSetup())

    def test_installer_run_refuses_every_print_and_drying_start_on_every_printer(self):
        """Slicer and API starts, the touchscreen's M24, QIDI's power-loss resume and Box drying."""
        module = load_extra("print_guard")
        for printer_id in FIRMWARE:
            printer = self.printer(printer_id)
            for command in module.COMMANDS:
                if command not in printer.gcode.ready_gcode_handlers:
                    continue  # not every printer's firmware has QIDI's recovery or ENABLE_BOX_DRY
                with self.subTest(printer=printer_id, command=command):
                    with acquire(printer.root / module.INSTALLER_LOCK), \
                            self.assertRaisesRegex(printer.command_error, "installer is running"):
                        printer.run(command)
                    self.assertFalse(printer.status("virtual_sdcard")["is_active"])
                    self.assertEqual(printer.status("extruder")["target"], 0.)
            # Once the installer run ends, prints start again.
            printer.print_file("M118 MSG=print_admission_pass\n")
            self.assertEqual(printer.status("print_stats")["state"], "complete")

    def test_paused_print_resumes_while_the_installer_lock_is_held(self):
        from installer.tests.gcode_flow import slicer

        for printer_id in FIRMWARE:
            for command in ("RESUME", "M24"):
                with self.subTest(printer=printer_id, command=command):
                    printer = self.printer(printer_id)
                    printer.start_print(slicer.print_with_pause("orca", printer_id).text, nozzle=220.)
                    self.assertEqual(printer.status("print_stats")["state"], "paused")
                    with acquire(printer.root / ".vo_installer.lock"):
                        printer.run(command)
                        printer.reactor.advance(printer.reactor.monotonic())
                    self.assertEqual(printer.status("print_stats")["state"], "complete")

    def test_an_update_check_that_only_reads_lets_prints_start(self):
        printer = self.printer()
        with acquire_shared(printer.root / ".vo_installer.lock"):
            printer.print_file("M118 MSG=shared_lock_pass\n")
        self.assertEqual(printer.status("print_stats")["state"], "complete")

    def test_slicer_print_start_does_not_readmit_an_active_sd_print(self):
        from installer.tests.gcode_flow import sdcard, slicer

        for printer_id in FIRMWARE:
            printer = self.printer(printer_id)
            sdcard.point_at(printer)
            path = printer.root / ".cache" / "already-admitted.gcode"
            printer.set_filament_detected(True)
            path.write_text(slicer.print_from_start("orca", printer_id, filaments=[slicer.Filament()] * 8, tool=0).text)
            printer.run("SDCARD_PRINT_FILE FILENAME=already-admitted.gcode")
            self.assertTrue(printer.status("virtual_sdcard")["is_active"])
            with acquire(printer.root / ".vo_installer.lock"):
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
        with self.assertRaisesRegex(printer.command_error, "firmware update hasn.t finished"):
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
                                         ("standby", None, ("printer_state_unknown",)),
                                         ("standby", True, ("printing",)),
                                         ("standby", False, ())):
            status = {"print_stats": {"state": state}, "virtual_sdcard": {"is_active": active},
                      "webhooks": {"state": "ready"}, "heaters": {"available_heaters": []}}
            with mock.patch.object(server, "_moonraker_objects", return_value=status):
                self.assertEqual(server.live_printer(), expected)


if __name__ == "__main__":
    unittest.main()
