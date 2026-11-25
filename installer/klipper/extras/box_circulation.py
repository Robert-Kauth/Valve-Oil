# Temperature-latched circulation for USB QIDI Boxes.
# Installed on both Klipper trees with the matching native fan controller seam.
from __future__ import annotations

import hashlib
import inspect
import logging
import math
import re
from typing import NamedTuple

FAN_NAME = re.compile(r"box_heater_fan heater_fan_([ab])_box([1-9][0-9]*)$")
VARIABLE = "vo_box_circulation"
DEFAULT_HYSTERESIS = 0.5
SENSOR_TIMEOUT = 5.
PIN_MIN_TIME = .100


class OutletSample(NamedTuple):
    temperature: float
    read_time: float
    print_time: float

    def is_valid(self, min_temp, max_temp):
        # The sensor's own configured range; Klipper shuts down outside it.
        return (math.isfinite(self.temperature) and min_temp <= self.temperature <= max_temp
                and math.isfinite(self.read_time) and self.read_time > 0.
                and math.isfinite(self.print_time)
                and self.print_time - self.read_time <= SENSOR_TIMEOUT)


class OutletTemperature:
    """Normalize native ADC samples once at the sensor boundary.

    Keep the native temperature callback and hardware protections untouched.
    The controller consumes named measurements, not runtime-specific tuples.
    """
    def __init__(self, sensor):
        self.sensor = sensor
        self.min_temp, self.max_temp = sensor.min_temp, sensor.max_temp
        self.adc = sensor.sensor.mcu_adc
        self.mcu = self.adc.get_mcu()
        parameters = tuple(inspect.signature(self.adc.setup_adc_callback).parameters)
        if parameters == ("report_time", "callback"):
            self.read_sample_time = self.qidi_sample_time
        elif parameters == ("callback",):
            self.read_sample_time = self.batched_sample_time
        else:
            raise ValueError("Unsupported Box ADC callback API")

    def qidi_sample_time(self):
        _, read_time = self.adc.get_last_value()
        return read_time

    def batched_sample_time(self):
        read_time, _ = self.adc.get_last_value()
        return read_time

    def read(self, eventtime):
        return OutletSample(self.sensor.get_temp(eventtime)[0],
                            self.read_sample_time(),
                            self.mcu.estimated_print_time(eventtime))


def valid_settings(value):
    return (isinstance(value, dict) and set(value) == {"threshold", "hysteresis"}
            and all(type(value[k]) in (int, float) and math.isfinite(value[k])
                    for k in value)
            and 35. <= value["threshold"] <= 45.
            and .2 <= value["hysteresis"] <= 2.)


class CirculationState:
    """One latch per physical Box, shared by both commanded fan outputs."""
    def __init__(self, threshold, hysteresis, run_on):
        self.threshold, self.hysteresis = threshold, hysteresis
        self.run_on = run_on
        self.thermal = False
        self.until = 0.
        self.reason = "off"

    def update(self, now, air, heating=False, motor=False, fault=False):
        if fault:
            reason = "sensor_fault"
        else:
            # Never raise the activation boundary to implement hysteresis.
            if air > self.threshold:
                self.thermal = True
            elif air <= self.threshold - self.hysteresis:
                self.thermal = False
            reason = ("heating" if heating else "temperature" if self.thermal
                      else "motor" if motor else None)
        if reason:
            self.until = now + self.run_on
        else:
            reason = "run_on" if now < self.until else "off"
        self.reason = reason
        return 0. if reason == "off" else 1.


class BoxCirculation:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.config = config
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")
        self.boxes = {}
        self.printer.register_event_handler("klippy:connect", self.connect)
        self.printer.register_event_handler("klippy:ready", self.ready)
        self.gcode.register_command("VO_BOX_CIRCULATION", self.configure,
                                    desc="Set automatic Box circulation temperatures")

    def connect(self):
        # All configured objects exist at connect, before their ready handlers
        # decide whether native or circulation policy owns scheduling.
        pairs = {}
        for name, obj in self.printer.lookup_objects("box_heater_fan"):
            match = FAN_NAME.fullmatch(name)
            if match:
                side, number = match.groups()
                pairs.setdefault(number, {})[side] = obj
        self.saved = self.printer.lookup_object("save_variables", None)
        for number, pair in pairs.items():
            if set(pair) != {"a", "b"}:
                raise self.config.error("Box %s requires both circulation fans" % number)
            self.boxes[number] = ControlledBox(self, number, pair)
        keys = [b.key for b in self.boxes.values() if b.key]
        if len(set(keys)) != len(keys):
            raise self.config.error("Box circulation requires distinct USB identities")
        for box in self.boxes.values():
            box.attach()

    def ready(self):
        for box in self.boxes.values():
            self.reactor.register_timer(box.tick,
                                        self.reactor.monotonic() + PIN_MIN_TIME)

    def store(self):
        if self.saved is None:
            return None
        return self.saved.get_status(self.reactor.monotonic())["variables"].get(VARIABLE, {})

    def busy(self, now):
        stats = self.printer.lookup_object("print_stats", None)
        if stats is None or stats.get_status(now).get("state") not in (
                "standby", "complete", "cancelled", "error"):
            return True
        extras = self.printer.lookup_object("box_extras", None)
        if extras is None:
            return True
        boxes = extras.get_status(now).get("boxes")
        # As vo_box_dry_guard reads it; an entry that isn't a mapping can't be shown idle.
        if not isinstance(boxes, list) or any(
                not hasattr(b, "get") or b.get("dry_state") == 1 for b in boxes):
            return True
        for box in self.boxes.values():
            status = box.heater.get_status(now)
            if status["target"] or status["power"]:
                return True
        return False

    def configure(self, gcmd):
        number = str(gcmd.get_int("BOX", minval=1))
        box = self.boxes.get(number)
        if box is None or not box.key or self.saved is None:
            raise gcmd.error("Box circulation settings are unavailable")
        values = {"threshold": gcmd.get_float("THRESHOLD"),
                  "hysteresis": gcmd.get_float("HYSTERESIS")}
        if not valid_settings(values):
            raise gcmd.error("Threshold must be 35..45 C; hysteresis must be 0.2..2 C")
        now = self.reactor.monotonic()
        # Re-evaluate physical state, not the last Moonraker/UI snapshot.
        box.tick(now)
        if self.busy(now) or box.state.reason not in ("off", "temperature", "run_on") or box.fault:
            raise gcmd.error("Wait until printing, drying, heating and motor activity have stopped; check for sensor faults")
        stored = self.store()
        if not isinstance(stored, dict):
            raise gcmd.error("Saved Box circulation settings are invalid")
        updated = dict(stored)
        updated[box.key] = values
        command = self.gcode.create_gcode_command("SAVE_VARIABLE", "SAVE_VARIABLE", {
            "VARIABLE": VARIABLE, "VALUE": repr(updated)})
        self.saved.cmd_SAVE_VARIABLE(command)
        if self.store() != updated:
            raise gcmd.error("Box circulation settings were not saved")
        box.state.threshold = values["threshold"]
        box.state.hysteresis = values["hysteresis"]
        box.settings_error = None
        box.tick(self.reactor.monotonic())
        gcmd.respond_info("Box %s circulation settings saved" % number)

    def get_status(self, eventtime):
        return {"boxes": {n: b.status(eventtime) for n, b in self.boxes.items()}}


class ControlledBox:
    def __init__(self, owner, number, pair):
        self.owner, self.number, self.pair = owner, number, pair
        self.printer, self.reactor = owner.printer, owner.reactor
        self.heater = self.printer.lookup_object("heaters").lookup_heater("heater_box" + number)
        try:
            self.sensors = [OutletTemperature(self.printer.lookup_object(
                "temperature_sensor heater_temp_%s_box%s" % (s, number)))
                for s in ("a", "b")]
        except ValueError as exc:
            raise owner.config.error(str(exc)) from exc
        # Persist by MCU identity, not Box index or USB product/firmware version:
        # reconnect/reorder cannot transfer another Box's higher threshold.
        serial = owner.config.getsection("mcu mcu_box" + number).get("serial", "")
        identity = re.fullmatch(
            r"/dev/serial/by-id/usb-Klipper_(?:QIDI_BOX_V[^/]+|02\.03[^/]*)_"
            r"([0-9A-Fa-f]{24})-if00", serial)
        self.key = (hashlib.sha256(bytes.fromhex(identity.group(1))).hexdigest()
                    if identity else None)
        self.settings_error = None
        native_threshold = min(f.heater_temp for f in pair.values())
        if not math.isfinite(native_threshold) or native_threshold <= 0.:
            raise owner.config.error("Invalid Box circulation threshold")
        setting = {"threshold": native_threshold, "hysteresis": DEFAULT_HYSTERESIS}
        stored = owner.store()
        if stored is not None and (not isinstance(stored, dict)
                                   or (self.key in stored and not valid_settings(stored[self.key]))):
            self.settings_error = "invalid_saved_settings"
        elif isinstance(stored, dict) and self.key in stored:
            setting = stored[self.key]
        if not self.key:
            self.settings_error = "unstable_box_identity"
        if owner.saved is None:
            self.settings_error = "persistence_unavailable"
        self.state = CirculationState(setting["threshold"], setting["hysteresis"],
                                      max(f.idle_timeout for f in pair.values()))
        self.outlets: list[float | None] = [None, None]
        self.fault = None
        self.failed_sources = set()
        for f in pair.values():
            if f.heater_names != ["heater_box" + number] and tuple(f.heater_names) != ("heater_box" + number,):
                raise owner.config.error("Unexpected Box circulation heater mapping")
            if not all(hasattr(f, field) for field in (
                    "attach_controller", "set_controller_speed", "fan", "stepper_enable")):
                raise owner.config.error("Unsupported Box circulation implementation")
            if f.fan.max_power != 1.:
                raise owner.config.error("Box circulation requires max_power: 1")

    def attach(self):
        for fan in self.pair.values():
            fan.attach_controller(self)

    def tick(self, eventtime):
        eventtime = self.reactor.monotonic()
        fault = None
        failed_sources = set()
        try:
            air, target = self.heater.get_temp(eventtime)
            power = self.heater.get_status(eventtime)["power"]
            # Heater.get_temp returns exactly 0 when its sensor's reading is stale;
            # any other reading within the heater's configured range is real, below 0 C too.
            if not (math.isfinite(air) and air != 0.
                    and self.heater.min_temp <= air <= self.heater.max_temp
                    and math.isfinite(target) and math.isfinite(power)):
                fault = "invalid_or_stale_temperature"
                failed_sources.add("heater")
                if "heater" not in self.failed_sources:
                    logging.warning("Box circulation input is invalid: box=%s source=heater",
                                    self.number)
        except Exception:
            fault = "temperature_unavailable"
            air, target, power = 0., 0., 0.
            failed_sources.add("heater")
            if "heater" not in self.failed_sources:
                logging.exception("Box circulation input is unavailable: box=%s source=heater",
                                  self.number)

        for index, side in enumerate(("a", "b")):
            try:
                sensor = self.sensors[index]
                sample = sensor.read(eventtime)
                self.outlets[index] = sample.temperature
                if not sample.is_valid(sensor.min_temp, sensor.max_temp):
                    fault = fault or "invalid_or_stale_temperature"
                    source = "outlet_" + side
                    failed_sources.add(source)
                    if source not in self.failed_sources:
                        logging.warning(
                            "Box circulation input is invalid: box=%s source=%s",
                            self.number, source)
            except Exception:
                fault = fault or "temperature_unavailable"
                self.outlets[index] = None
                source = "outlet_" + side
                failed_sources.add(source)
                if source not in self.failed_sources:
                    logging.exception(
                        "Box circulation input is unavailable: box=%s source=%s",
                        self.number, source)

        try:
            motor = any(f.stepper_enable.lookup_enable(n).is_motor_enabled()
                        for f in self.pair.values() for n in f.stepper_names)
        except Exception:
            fault = fault or "motor_unavailable"
            motor = False
            failed_sources.add("motor")
            if "motor" not in self.failed_sources:
                logging.exception("Box circulation input is unavailable: box=%s source=motor",
                                  self.number)
        self.failed_sources = failed_sources
        self.fault = fault
        speed = self.state.update(eventtime, air,
                                  heating=bool(target or power), motor=motor, fault=bool(self.fault))
        if self.fault:
            # A bad signal can only force cooling and turn heating OFF. Native
            # verify_heater and ADC shutdown remain intact; never synthesize
            # healthy temperatures or restore an old heater target.
            self.heater.set_temp(0.)
        for fan in self.pair.values():
            fan.set_controller_speed(self, speed)
        return eventtime + 1.

    def status(self, now):
        return {"threshold": self.state.threshold,
                "hysteresis": self.state.hysteresis,
                "release_temperature": self.state.threshold - self.state.hysteresis,
                "reason": self.state.reason,
                "run_on_remaining": max(0., self.state.until - now) if self.state.reason == "run_on" else 0.,
                "outlet_a": self.outlets[0] if self.outlets[0] is not None and math.isfinite(self.outlets[0]) else None,
                "outlet_b": self.outlets[1] if self.outlets[1] is not None and math.isfinite(self.outlets[1]) else None,
                "sensor_fault": self.fault, "settings_error": self.settings_error,
                "configurable": self.key is not None and self.owner.saved is not None}


def load_config(config):
    return BoxCirculation(config)
