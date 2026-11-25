# Valve Oil print guard: no print or Box drying starts while Valve Oil's
# installer runs.
#
# An installer run (install, update, uninstall, the hourly update check on a
# printer with automatic updates) holds ~/printer_data/.vo_installer.lock
# exclusively, checks that the printer is idle, and then changes files and
# restarts Klipper. A print that starts after that check would be cut off.
# So the commands that start a print or Box drying take the same lock shared,
# without waiting, and keep it while QIDI's code selects or extracts the file
# and schedules the print. If an installer run holds it, they stop with an
# error; if they hold it, the installer can't start, and once they return it
# sees the print (virtual_sdcard.is_active).
#
# A QIDI firmware update restarts the services and the host and doesn't hold
# the lock in between, so its pending record also stops a start.
#
# Commands run inside a print that has already started (a resume, M24 on a
# paused print) aren't held up: the installer doesn't run while a print does.

import errno
import fcntl
import logging
import os

COMMANDS = ("SDCARD_PRINT_FILE", "SDCARD_POWER_OFF_CONTINUES_PINT_FILE", "M24",
            "BOX_START_DRYING", "ENABLE_BOX_DRY")
INSTALLER_LOCK = ".vo_installer.lock"
FIRMWARE_UPDATE_RECORD = ".vo_firmware_update_pending.json"


class PrintGuard:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object("gcode")
        config_file = self.printer.get_start_args()["config_file"]
        data = os.path.dirname(os.path.dirname(os.path.abspath(config_file)))
        self.lock_path = os.path.join(data, INSTALLER_LOCK)
        self.record_path = os.path.join(data, FIRMWARE_UPDATE_RECORD)
        self.printer.register_event_handler("klippy:ready", self._ready)

    def _ready(self):
        for name in COMMANDS:
            original = self.gcode.register_command(name, None)
            if original is not None:
                self.gcode.register_command(name, self._guarded(name, original),
                                            desc=self.gcode.gcode_help.get(name))

    def _print_active(self):
        eventtime = self.printer.get_reactor().monotonic()
        print_stats = self.printer.lookup_object("print_stats", None)
        sd = self.printer.lookup_object("virtual_sdcard", None)
        return ((print_stats is not None and print_stats.get_status(eventtime).get("state")
                 in ("printing", "paused", "extracting"))
                or (sd is not None and sd.is_active()))

    def _guarded(self, name, original):
        def handler(gcmd):
            if self._print_active():
                return original(gcmd)
            try:
                handle = os.open(self.lock_path, os.O_RDONLY | os.O_CREAT | os.O_CLOEXEC, 0o644)
            except OSError:
                logging.exception("vo_print_guard: can't open %s", self.lock_path)
                raise gcmd.error("Valve Oil can't tell whether its installer is running. Check klippy.log.")
            try:
                try:
                    fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
                except OSError as exc:
                    if exc.errno not in (errno.EAGAIN, errno.EACCES):
                        raise
                    logging.info("vo_print_guard: %s refused: installer running", name)
                    raise gcmd.error("Valve Oil's installer is running. Start again when it has finished.")
                if os.path.lexists(self.record_path):
                    logging.info("vo_print_guard: %s refused: firmware update pending", name)
                    raise gcmd.error("A QIDI firmware update hasn't finished. Finish it with Update now "
                                     "or ./install.sh first.")
                return original(gcmd)
            finally:
                os.close(handle)
        return handler


def load_config(config):
    return PrintGuard(config)
