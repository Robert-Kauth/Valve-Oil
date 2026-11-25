"""A printer for G-code flow tests: Valve Oil installed into a printer's newest stock printer_data, its config read
with Klipper's own config reader, and every section loaded as the printer loads it.

Klipper's own modules come from klipper/klippy, Valve Oil's opt-in Klipper (upstream v0.14 with QIDI's changes). Most
printers run QIDI's own Klipper instead, whose copies of these modules differ in code but parse commands, run macros,
and transform moves the same way for what these flows do: the G-code dispatcher, gcode_move, gcode_macro, homing_override, pause_resume, exclude_object,
save_variables, print_stats, QIDI's print_stats_manager, delayed_gcode, respond, gcode_arcs, display_status, and the
filament switch sensor. Valve Oil's modules are the installed ones. Hardware is hardware.py's; QIDI's compiled
modules are qidi.py's. Every config section is one of these, or is listed in IGNORED_SECTIONS with what it does on a
printer that these flows don't reach.
"""
from __future__ import annotations

import importlib
import importlib.util
import io
import logging
import shutil
import sys
import types
import weakref
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from installer.tests.gcode_flow import hardware, qidi, sdcard
from installer.tests.helpers import (
    REPO_ROOT,
    build_env,
    homing_smart5_fixture_bytes,
    homing_sync_reset_fixture_bytes,
    moonraker_urlopen,
    stock_printer_data,
    temp_path,
)

KLIPPY = REPO_ROOT / "klipper/klippy"
if str(KLIPPY) not in sys.path:
    sys.path.insert(0, str(KLIPPY))
# QIDI's save_variables imports requests for its AI camera upload, which these flows never reach.
sys.modules.setdefault("requests", types.ModuleType("requests"))

import configfile  # noqa: E402  (Klipper's)
import gcode as klipper_gcode  # noqa: E402  (Klipper's)

FIRMWARE = {"max4": "01.01.07.02", "plus5": "01.01.02.02", "smart5": "smart5-01.01.02.01"}

# Klipper modules loaded from klipper/klippy/extras for their sections, or when another module loads them.
KLIPPER_MODULES = {
    "gcode_macro", "save_variables", "pause_resume", "exclude_object", "respond", "gcode_arcs",
    "homing_override", "print_stats_manager", "delayed_gcode", "display_status", "filament_switch_sensor",
    "tuning_tower", "print_stats",
}

# Sections whose objects these flows never reach, and what they are on the printer.
IGNORED_SECTIONS = {
    "mcu": "the boards' serial links",
    "stepper_x": "read by the stand-in toolhead's kinematics",
    "stepper_y": "read by the stand-in toolhead's kinematics",
    "stepper_z": "read by the stand-in toolhead's kinematics",
    "stepper_z1": "the second Z motor",
    "adc_temperature": "a thermistor table",
    "verify_heater": "heater fault checks",
    "duplicate_pin_override": "pin sharing",
    "lis2dw": "the accelerometer",
    "resonance_tester": "input shaper calibration",
    "screws_tilt_adjust": "bed screw calibration",
    "bed_screws": "bed screw calibration",
    "d_bus_service_manager": "QIDI's D-Bus link to the touchscreen",
    "cl_interface": "the Max 4's closed-loop driver link, looked up only when present",
    "smart_output_pin": "QIDI's light and camera pins",
}

# Valve Oil's modules not loaded, and why. Each runs on the printer; these flows don't depend on what it does.
VO_NOT_LOADED = {
    "vo_klipper_health": "reads the host's threads and Klipper's internals; logs only",
    "vo_klipper_fixes": "patches Klipper's reactor, MCU, and heater internals at startup",
    "vo_box_link_monitor": "watches the Box boards' serial links; logs only",
    "vo_spoolman": "talks to Moonraker's Spoolman link",
    "vo_background_save_config": "writes printer.cfg on a thread; VO_SAVE_CONFIG_QD is a stand-in here",
    "vo_update_lights": "follows the updater's status file",
    "vo_klipper": "switches between QIDI's and Valve Oil's Klipper",
    "vo_box485_monitor": "watches RS-485 Boxes; logs only",
    "vo_box_mcu_temperature": "reports Box board temperatures",
    "vo_probe_report": "reports probe results",
    "vo_probe_test": "the probe signal test",
    "vo_resonance_sweep": "input shaper calibration",
    "vo_bed_soak": "bed soak calibration runs",
    "vo_homing_guard": "watches the closed-loop drivers while homing",
    "vo_step_loss_monitor": "watches the closed-loop drivers; off unless turned on",
    "vo_box_slot_jog": "moves Box slot motors itself through Klipper's C step queue; jogs and RFID spool turns",
    "vo_box_spool_roll": "turns spools on their rollers through Klipper's C step queue; off by default",
}


@lru_cache(maxsize=None)
def qidi_module_builds(firmware: str) -> dict:
    """{path under klippy/: sha256} of QIDI's compiled modules on a firmware, as installer/package.yaml pins them for
    Valve Oil's Klipper; qidi.py's stand-ins pick their behaviour by these builds."""
    from installer.runtime.manifest import load_manifest

    manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
    for key, files in manifest.system_optimizations.klipper_tree.qidi_modules:
        if key == firmware:
            return {path: pin.sha256 for path, pin in files}
    raise KeyError(f"installer/package.yaml pins no QIDI modules for {firmware}")


@lru_cache(maxsize=None)
def installed_printer_data(printer_id: str) -> Path:
    """printer_data after a real Valve Oil install into the printer's newest stock printer_data."""
    from installer.runtime.cli import resolve_runtime_paths
    from installer.runtime.manifest import load_manifest
    from installer.runtime.reporter import PlainReporter
    from installer.runtime.runner import run_install

    manifest = load_manifest(REPO_ROOT / "installer/package.yaml")
    firmware = FIRMWARE[printer_id]
    homing = homing_smart5_fixture_bytes() if printer_id == "smart5" else homing_sync_reset_fixture_bytes()
    root = stock_printer_data(manifest, firmware, homing=homing)
    paths = resolve_runtime_paths(bundle_root=REPO_ROOT, environ=build_env(
        root, moonraker_url="http://moonraker.invalid/printer/objects/query?print_stats"))
    run_install(paths, manifest, PlainReporter(io.StringIO()),
                urlopen=moonraker_urlopen(saved_variables_path=paths.config_root / "saved_variables.cfg"))
    # The boards' serial numbers, which printer.cfg includes; this repository keeps none.
    mcu_ids = root / "config/MCU_ID.cfg"
    if not mcu_ids.exists():
        mcu_ids.write_text("", encoding="utf-8")
    return root


class _TracingHandlers(dict):
    """The dispatcher's command table: each command it hands out records itself on the printer's trace."""

    def __init__(self, printer, *args):
        super().__init__(*args)
        self.printer = printer

    def get(self, cmd, default=None):
        handler = super().get(cmd, default)
        # Only the dispatcher looks a command up with a default (its cmd_default); register_command doesn't.
        if handler is None or default is None:
            return handler
        printer = self.printer

        def traced(gcmd):
            printer.stack.append(gcmd.get_commandline())
            printer.trace.append(Command(cmd, gcmd.get_commandline()))
            try:
                return handler(gcmd)
            finally:
                printer.stack.pop()
        return traced


@dataclass
class Command:
    name: str
    line: str


@dataclass
class Responses:
    lines: list = field(default_factory=list)

    @property
    def errors(self):
        return [line for line in self.lines if line.startswith("!!")]


def _close_print_file(sdcard):
    if getattr(sdcard, "current_file", None) is not None:
        sdcard.current_file.close()


# The installed extras folder of the printer built last, in Klipper's extras package path.
_INSTALLED_EXTRAS = None


class FlowPrinter:
    """Klipper's Printer for the harness: objects, events, and config, with the hardware and QIDI stand-ins."""

    config_error = configfile.error
    command_error = klipper_gcode.CommandError

    def __init__(self, printer_id: str, *, box: qidi.BoxSetup, variables: dict | None = None,
                 filament_detected: bool = True, slow_ready: float = 0.):
        self.printer_id = printer_id
        # The stock firmware installed into, and its QIDI module builds, which pick the stand-ins' behaviour.
        self.firmware = FIRMWARE[printer_id]
        self.qidi_builds = qidi_module_builds(self.firmware)
        hardware.GAPS.clear()
        self.root = temp_path(f"gcode-flow-{printer_id}-") / "printer_data"
        shutil.copytree(installed_printer_data(printer_id), self.root, symlinks=True)
        self.config_root = self.root / "config"
        box.write_config(self.config_root)
        qidi.write_saved_variables(self.config_root / "saved_variables.cfg", box, variables or {})
        self.box_setup = box
        # Seconds a klippy:ready callback just before QIDI's Box controller's takes, as on a slow start.
        self.slow_ready = slow_ready
        self.reactor = hardware.Reactor()
        self.objects: dict[str, object] = {}
        self.event_handlers: dict[str, list] = {}
        self.state_message = "Printer is not ready"
        self.in_shutdown_state = False
        self.shutdown_message = None
        self.trace: list[Command] = []
        self.stack: list[str] = []
        self.responses = Responses()
        self.unknown_commands: list[str] = []
        self.toolhead = None
        self.start_args = {"config_file": str(self.config_root / "printer.cfg")}
        self._build(filament_detected)
        # QIDI's print loop keeps a paused print's file open, as on the printer; close it with the printer.
        weakref.finalize(self, _close_print_file, self.lookup_object("virtual_sdcard"))

    # Klipper's Printer interface
    def get_start_args(self):
        return self.start_args

    def get_reactor(self):
        return self.reactor

    def get_state_message(self):
        if self.in_shutdown_state:
            return self.state_message, "shutdown"
        return self.state_message, "ready" if self.state_message == "Printer is ready" else "startup"

    def is_shutdown(self):
        return self.in_shutdown_state

    def add_object(self, name, obj):
        if name in self.objects:
            raise self.config_error("Printer object '%s' already created" % (name,))
        self.objects[name] = obj

    def lookup_object(self, name, default=configfile.sentinel):
        if name in self.objects:
            return self.objects[name]
        if default is configfile.sentinel:
            raise self.config_error("Unknown config object '%s'" % (name,))
        return default

    def lookup_objects(self, module=None):
        if module is None:
            return list(self.objects.items())
        prefix = module + " "
        objs = [(n, o) for n, o in self.objects.items() if n.startswith(prefix)]
        if module in self.objects:
            return [(module, self.objects[module])] + objs
        return objs

    def load_object(self, config, section, default=configfile.sentinel):
        if section in self.objects:
            return self.objects[section]
        if section.split()[0] in KLIPPER_MODULES:
            return self._load(config, section)
        if default is not configfile.sentinel:
            return default
        raise self.config_error("harness: load_object(%r) has no stand-in" % (section,))

    def register_event_handler(self, event, callback):
        self.event_handlers.setdefault(event, []).append(callback)

    def send_event(self, event, *params):
        return [cb(*params) for cb in self.event_handlers.get(event, [])]

    def invoke_shutdown(self, msg, details=None):
        if self.in_shutdown_state:
            return
        self.in_shutdown_state = True
        self.state_message = self.shutdown_message = msg
        for cb in self.event_handlers.get("klippy:shutdown", []):
            try:
                cb()
            except Exception:
                logging.exception("harness: shutdown handler failed")

    def invoke_async_shutdown(self, msg, details=None):
        self.invoke_shutdown(msg, details)

    def request_exit(self, result):
        pass

    def set_rollover_info(self, name, info, log=True):
        pass

    # Building the printer
    def _build(self, filament_detected):
        gcode = klipper_gcode.GCodeDispatch(self)
        gcode.base_gcode_handlers = gcode.gcode_handlers = _TracingHandlers(self, gcode.base_gcode_handlers)
        gcode.ready_gcode_handlers = _TracingHandlers(self, gcode.ready_gcode_handlers)
        gcode.register_output_handler(self.responses.lines.append)
        default = gcode.cmd_default

        def unknown(gcmd):
            if gcmd.get_command():
                self.unknown_commands.append(gcmd.get_commandline())
            return default(gcmd)
        gcode.cmd_default = unknown
        self.add_object("gcode", gcode)
        self.add_object("webhooks", hardware.Webhooks(self))
        pconfig = configfile.PrinterConfig(self)
        self.add_object("configfile", pconfig)
        config = pconfig.read_main_config()
        self._redirect_paths(config)
        self.config = config
        self.toolhead = hardware.Toolhead(config, self)
        self.toolhead.stack_source = lambda: tuple(self.stack)
        self.add_object("toolhead", self.toolhead)
        self._load(config, "gcode_move")
        # exclude_object looks tuning_tower up without a default when the first part is cancelled.
        self._load(config, "tuning_tower")
        self.add_object("heaters", hardware.Heaters(self))
        self.add_object("stepper_enable", hardware.StepperEnable(self))
        self.add_object("homing", hardware.Homing(self, second_homing_command=self.printer_id == "smart5"))
        self.add_object("idle_timeout", hardware.IdleTimeout(self))
        self.add_object("virtual_sdcard", sdcard.load(self, config))
        self.add_object("motion_report", hardware.StatusOnly({"live_position": None, "live_velocity": 0.}))
        self.vendor = qidi.Vendor(self, config)
        for section_config in config.get_prefix_sections(""):
            self._load_section(config, section_config.get_name())
        self._build_settings(pconfig, config)
        sensor = self.lookup_object("filament_switch_sensor filament_switch_sensor", None)
        self.send_event("klippy:mcu_identify")
        for cb in self.event_handlers.get("klippy:connect", []):
            cb()
        self.state_message = "Printer is ready"
        for cb in self.event_handlers.get("klippy:ready", []):
            cb()
        if sensor is not None:
            self.set_filament_detected(filament_detected)
        # delayed_gcode and the macros' startup work (INIT_GLOBALS and the like) run in Klipper's first seconds.
        self.reactor.advance(self.reactor.monotonic() + 10.)

    def _build_settings(self, pconfig, config):
        """configfile's settings: Klipper lists each option its modules parsed, typed as they parsed it. The
        stand-ins parse only what they use, so every other option is listed as a number when it reads as one."""
        settings = {}
        for section in config.fileconfig.sections():
            entries = settings.setdefault(section.lower(), {})
            for option, value in config.fileconfig.items(section):
                entries[option] = _setting(value)
        for (section, option), value in pconfig.validate.access_tracking.items():
            settings.setdefault(section, {})[option] = value
        pconfig.validate.status_settings = settings

    def _redirect_paths(self, config):
        fileconfig = config.fileconfig
        for section in fileconfig.sections():
            for option, value in fileconfig.items(section):
                for home in ("/home/qidi/printer_data", "~/printer_data"):
                    if home in value:
                        fileconfig.set(section, option, value.replace(home, str(self.root)))

    def _load_section(self, config, section):
        module = section.split()[0]
        if section in self.objects:
            return
        if module in KLIPPER_MODULES:
            self._load(config, section)
        elif module.startswith("vo_"):
            if module not in VO_NOT_LOADED:
                self._load_valve_oil(config, section)
            elif module == "vo_background_save_config":
                # Its save runs SAVE_CONFIG's write on a thread; the mesh and offsets it saves aren't read back here.
                self.gcode.register_command("VO_SAVE_CONFIG_QD", lambda gcmd: None)
        elif self.vendor.load(config, section):
            pass
        elif module == "printer":
            pass
        elif module == "extruder":
            self.toolhead.set_extruder(hardware.Extruder(config.getsection(section), self.lookup_object("heaters")),
                                       0.)
            self.add_object(section, self.toolhead.extruder)
        elif module == "heater_bed":
            heater = self.lookup_object("heaters").add_heater(config.getsection(section), "heater_bed",
                                                               "heater_bed")
            self.add_object(section, heater)
            hardware.register_bed_commands(self, heater)
        elif module == "heater_generic":
            name = section.split()[-1]
            self.add_object(section, self.lookup_object("heaters").add_heater(config.getsection(section), name,
                                                                              "chamber"))
        elif module == "temperature_sensor":
            sensor = hardware.TemperatureSensor()
            self.add_object(section, sensor)
            self.lookup_object("heaters").add_sensor(section, sensor)
        elif module in ("fan_generic", "heater_fan", "controller_fan"):
            self.add_object(section, hardware.Fan(config.getsection(section), module == "fan_generic"))
        elif module == "output_pin":
            self.add_object(section, hardware.OutputPin(config.getsection(section)))
        elif module == "neopixel":
            self.add_object(section, hardware.Led(config.getsection(section)))
        elif module == "bed_mesh":
            self.add_object(section, hardware.BedMesh(self, config.getsection(section)))
        elif module == "input_shaper":
            self.add_object(section, hardware.InputShaper(config.getsection(section)))
        elif module in ("tmc2209", "tmc2240"):
            self.add_object(section, hardware.StepperDriver(config.getsection(section)))
        elif module == "cuc":
            if "cuc" not in self.objects:
                self.objects["cuc"] = hardware.BoardControl(self)
            self.add_object(section, self.objects["cuc"])
        elif module == "closed_loop":
            self.add_object(section, hardware.ClosedLoop(self))
        elif module == "probe_air":
            self.add_object(section, hardware.Probe(self, config.getsection(section)))
        elif module == "z_tilt":
            self.add_object(section, hardware.ZTilt(self, config.getsection(section)))
        elif module == "force_move":
            self.add_object(section, hardware.ForceMove(self))
        elif module in IGNORED_SECTIONS:
            pass
        else:
            raise hardware.HarnessGap(f"no stand-in or reason to ignore [{section}]; add one to printer.py")

    def _load(self, config, section):
        if section in self.objects:
            return self.objects[section]
        module_parts = section.split()
        module = self._klipper_module(module_parts[0])
        init = getattr(module, "load_config_prefix" if len(module_parts) > 1 else "load_config")
        if module_parts[0] == "filament_switch_sensor" and "buttons" not in self.objects:
            self.objects["buttons"] = hardware.Buttons(config)
        self.objects[section] = init(config.getsection(section))
        return self.objects[section]

    def _klipper_module(self, name):
        """A Klipper module, from klipper/printers/<printer>/ where the printer's QIDI Klipper differs from the
        Max 4's (klipper/printers/README.md)."""
        override = REPO_ROOT / "klipper/printers" / self.printer_id / "klippy/extras" / f"{name}.py"
        if not override.exists():
            return importlib.import_module("extras." + name)
        spec = importlib.util.spec_from_file_location("extras." + name, override)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _load_valve_oil(self, config, section):
        # This printer's installed modules, imported from Klipper's extras package as Klipper imports them; one
        # imported for another printer is imported again when this printer's copy differs.
        import extras
        global _INSTALLED_EXTRAS
        installed = str(self.root / "klipper/klippy/extras")
        if _INSTALLED_EXTRAS in extras.__path__:
            extras.__path__.remove(_INSTALLED_EXTRAS)
        extras.__path__.insert(0, installed)
        _INSTALLED_EXTRAS = installed
        name = "extras." + section.split()[0]
        module = importlib.import_module(name)
        mine = Path(installed, Path(module.__file__).name)
        if Path(module.__file__) != mine and mine.read_bytes() != Path(module.__file__).read_bytes():
            del sys.modules[name]
            module = importlib.import_module(name)
        init = getattr(module, "load_config_prefix" if len(section.split()) > 1 else "load_config")
        self.objects[section] = init(config.getsection(section))

    # What a test does with the printer
    @property
    def gcode(self):
        return self.lookup_object("gcode")

    def run(self, script: str):
        """Run G-code as the console does."""
        self.gcode.run_script(script)

    def start_print(self, text: str, *, at=(150., 150., 2.), nozzle=220., bed=60., name="job.gcode"):
        """Print `text` from the virtual SD card, with the printer as print start leaves it: X, Y, and Z homed, the
        nozzle and bed at temperature, absolute moves and relative extrusion, and the toolhead over the print."""
        self.toolhead.set_position([*at, 0.], homing_axes=(0, 1, 2))
        heaters = self.lookup_object("heaters")
        for name_, temperature in (("extruder", nozzle), ("heater_bed", bed)):
            heater = heaters.lookup_heater(name_)
            heater.set_temp(temperature)
            heater.set_smoothed_temp(temperature)
        self.run("G90\nM83\nG92 E0")
        self.print_file(text, name=name)

    def print_file(self, text: str, *, name="job.gcode"):
        """Start `text` as QIDI's touchscreen and Moonraker do, from printer_data/.cache, and run it until it ends,
        pauses, or fails."""
        sdcard.point_at(self)
        (self.root / ".cache" / name).write_text(text, encoding="utf-8")
        self.job_lines = text.split("\n")
        self.trace.clear()
        self.toolhead.moves.clear()
        self.lookup_object("virtual_sdcard").recorder.lines.clear()
        self.command("SDCARD_PRINT_FILE FILENAME=%s" % (name,))

    def moves_from_line(self, line: int) -> int:
        """How many moves the toolhead had queued when the print loop sent file line `line`."""
        offset = sum(len(text.encode()) + 1 for text in self.job_lines[:line])
        return dict(self.lookup_object("virtual_sdcard").recorder.lines)[offset]

    def idle(self, at=(150., 150., 20.), homed=True):
        """The printer between prints: X, Y, and Z homed (or not) with the toolhead at `at`, heaters off."""
        self.toolhead.set_position([*at, 0.], homing_axes=(0, 1, 2) if homed else ())
        if not homed:
            self.toolhead.homed.clear()
        self.trace.clear()
        self.toolhead.moves.clear()

    def command(self, script: str):
        """Run G-code as the console does, then let the clock catch up with what it queued and let anything it
        started, such as the print loop, run."""
        sdcard.point_at(self)
        self.run(script)
        self.reactor.advance(self.reactor.monotonic())

    def set_filament_detected(self, detected: bool):
        sensor = self.lookup_object("filament_switch_sensor filament_switch_sensor")
        handler = self.lookup_object("buttons").handlers["filament_switch_sensor filament_switch_sensor"]
        handler(self.reactor.monotonic(), detected)
        self.reactor.advance(self.reactor.monotonic())
        return sensor

    def status(self, name):
        return self.lookup_object(name).get_status(self.reactor.monotonic())

    def variables(self):
        return dict(self.lookup_object("save_variables").allVariables)


def _setting(value: str):
    """An option as Klipper's getfloat or getfloatlist would read it, or the text."""
    try:
        return float(value)
    except ValueError:
        pass
    parts = [part.strip() for part in value.split(",")]
    if len(parts) > 1:
        try:
            return tuple(float(part) for part in parts)
        except ValueError:
            pass
    return value
