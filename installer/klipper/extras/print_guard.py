# Print admission shared by updates, SD starts/resumes and QIDI Box drying.
import errno
import fcntl
import logging
import os
import stat
from enum import Enum, auto


class Admission(Enum):
    SELECT = auto()
    START = auto()
    RESUME = auto()
    START_OR_RESUME = auto()
    DRY = auto()


COMMANDS = {
    "M23": Admission.SELECT,
    "M24": Admission.START_OR_RESUME,
    "PRINT_START": Admission.START,
    "SDCARD_PRINT_FILE": Admission.START,
    "SDCARD_POWER_OFF_CONTINUES_PINT_FILE": Admission.START,
    "RESUME": Admission.RESUME,
    "RESUME_PRINT": Admission.RESUME,
    "TRY_RESUME_PRINT": Admission.RESUME,
    "BOX_START_DRYING": Admission.DRY,
    "ENABLE_BOX_DRY": Admission.DRY,
}
INSTALLER_LOCK = ".vo_installer.lock"
FIRMWARE_UPDATE_MARKER = ".vo_firmware_update_pending.json"


class PrintGuard:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object("gcode")
        config_file = self.printer.get_start_args()["config_file"]
        self.data_dir = os.path.dirname(os.path.dirname(os.path.abspath(config_file)))
        self.commands = dict(COMMANDS)
        # QIDI and user macros may rename native commands. Guard those names
        # too: M24.6245197 and _KM_RESUME_BASE otherwise bypass the public macro.
        renames = {section.get_name().split(None, 1)[1].upper(): renamed.upper()
                   for section in config.get_prefix_sections("gcode_macro ")
                   if (renamed := section.get("rename_existing", None)) is not None}
        pending = list(self.commands)
        while pending:
            name = pending.pop()
            renamed = renames.get(name)
            if renamed is not None and renamed not in self.commands:
                self.commands[renamed] = self.commands[name]
                pending.append(renamed)
        self.printer.register_event_handler("klippy:ready", self._ready)

    def _ready(self):
        for name, admission in self.commands.items():
            original = self.gcode.register_command(name, None)
            if original is not None:
                self.gcode.register_command(
                    name, self._guarded(name, original, admission),
                    desc=self.gcode.gcode_help.get(name))

    def _guarded(self, name, original, admission):
        def handler(gcmd):
            handle = None
            try:
                handle = os.open(os.path.join(self.data_dir, INSTALLER_LOCK),
                                 os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
                                 | os.O_CLOEXEC | os.O_NONBLOCK, 0o600)
                if not stat.S_ISREG(os.fstat(handle).st_mode):
                    raise OSError("Installer lock is not a regular file")
                fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except OSError as exc:
                if handle is not None:
                    os.close(handle)
                if handle is not None and exc.errno in (errno.EAGAIN, errno.EACCES):
                    logging.info("vo_print_guard: %s refused: installer busy", name)
                    raise gcmd.error("Valve Oil is updating. Wait for it to finish before starting a print or drying.")
                logging.exception("vo_print_guard: %s could not acquire %s", name, INSTALLER_LOCK)
                raise gcmd.error("Cannot verify Valve Oil update safety. Check the Klipper log before printing.")
            try:
                # Firmware updates span service restarts and reboots; a broken
                # marker also blocks admission until the updater repairs it.
                if os.path.lexists(os.path.join(self.data_dir, FIRMWARE_UPDATE_MARKER)):
                    raise gcmd.error("A firmware update has not finished. Finish or recover it before printing or drying.")
                self._check_drying(gcmd, admission)
                # Keep the lock through extraction and scheduling. After the
                # handler returns the updater sees virtual_sdcard.is_active.
                return original(gcmd)
            finally:
                os.close(handle)
        return handler

    def _print_state(self):
        print_stats = self.printer.lookup_object("print_stats", None)
        if print_stats is None:
            return None
        return print_stats.get_status(self.printer.get_reactor().monotonic()).get("state")

    def _check_drying(self, gcmd, admission):
        if admission is Admission.DRY:
            sd = self.printer.lookup_object("virtual_sdcard", None)
            if self._print_state() in ("extracting", "printing", "paused") or (sd is not None and sd.is_active()):
                raise gcmd.error("Box drying can't start during a print. Set the Box heater target instead.")
            return
        if admission not in (Admission.START, Admission.START_OR_RESUME):
            return
        if admission is Admission.START_OR_RESUME and self._print_state() == "paused":
            return
        try:
            boxes = self.drying_boxes()
        except Exception:
            logging.exception("vo_print_guard: Box status failed")
            raise gcmd.error("Cannot read Box drying state. Check the Klipper log before printing.")
        if boxes:
            raise gcmd.error(
                "%s drying. Stop drying before starting a print."
                % (("Box %d is" % boxes[0]) if len(boxes) == 1
                   else "Boxes %s and %d are" % (
                       ", ".join(str(box) for box in boxes[:-1]), boxes[-1])))

    def drying_boxes(self):
        """Numbers of the Boxes QIDI's drying is running on, from 1."""
        box_extras = self.printer.lookup_object("box_extras", None)
        if box_extras is None:
            return []
        eventtime = self.printer.get_reactor().monotonic()
        boxes = box_extras.get_status(eventtime).get("boxes")
        if not isinstance(boxes, list) or any(not hasattr(box, "get") or box.get("dry_state") not in (0, 1) for box in boxes):
            raise ValueError("Invalid Box drying status")
        return [number for number, box in enumerate(boxes, 1)
                if box.get("dry_state") == 1]


def load_config(config):
    return PrintGuard(config)
