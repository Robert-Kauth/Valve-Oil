"""Stand-ins for the printer's hardware, for running macros with Klipper's own G-code, macro, and motion modules.

Each stand-in keeps the commands, status, and errors of the Klipper object it replaces (klipper/klippy, the code
Valve Oil's Klipper and QIDI's share for these), with time and temperature simulated:

- The reactor's clock moves only when something waits: a dwell or M400 for queued moves, or a heater wait. Timers
  run when the clock passes them, as Klipper's reactor runs them between commands.
- The toolhead queues moves with their duration at the commanded speed and keeps every move, with the commands that
  were running when it was queued. Moves are checked as Klipper's kinematics and extruder check them: "Must home axis
  first", "Move out of range", "Extrude below minimum temp", and "Extrude only move too long".
- Heaters move toward their target at a fixed rate as the clock moves; waits that could never finish fail.
"""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field

NEVER = 9999999999999999.
# How fast the stand-in heaters heat and cool, in degrees per second, and the room they cool to.
HEAT_RATE = {"extruder": 4., "heater_bed": 1., "chamber": .2}
COOL_RATE = {"extruder": 1.5, "heater_bed": .2, "chamber": .05}
AMBIENT = 25.
# The longest a heater wait may take before the harness reports it would never finish, in seconds.
LONGEST_WAIT = 3600.


class LockBusy(Exception):
    """A timer or callback reached the G-code lock while a command held it; it runs again once the lock is free, as
    it would wait on the printer."""


class Mutex:
    """Klipper's reactor mutex, which the G-code dispatcher takes for run_script(). It isn't re-entrant: a command
    that calls run_script() while it holds the lock waits on itself forever on the printer. QIDI's FLOW_STOP did,
    and every later G-code command waited behind it until Klipper was restarted (runtime, 2026-10-31)."""

    def __init__(self, reactor):
        self.reactor = reactor
        self.locked = False

    def __enter__(self):
        if self.locked:
            if self.reactor.in_callback:
                raise LockBusy()
            raise AssertionError("harness: run_script() inside a command that holds the G-code lock; on the printer "
                                 "this waits forever and every later G-code command waits behind it")
        self.locked = True
        return self

    def __exit__(self, *exc):
        self.locked = False
        return False

    def test(self):
        return self.locked


class Completion:
    def __init__(self):
        self.result = None
        self.done = False

    def complete(self, result):
        self.result, self.done = result, True

    def test(self):
        return self.done

    def wait(self, waketime=NEVER, waketime_result=None):
        return self.result if self.done else waketime_result


class Timer:
    def __init__(self, callback, waketime):
        self.callback, self.waketime = callback, waketime


class Reactor:
    """Klipper's reactor with a simulated clock: pause() moves the clock, and timers run as it passes them."""

    NOW, NEVER = 0., NEVER

    def __init__(self):
        self.now = 1000.
        self.timers: list[Timer] = []
        self.callbacks: list = []
        self.listeners: list = []
        self._advancing = False
        self.in_callback = False
        self._busy: list = []

    def monotonic(self):
        return self.now

    def mutex(self, is_locked=False):
        return Mutex(self)

    def completion(self):
        return Completion()

    def register_timer(self, callback, waketime=NEVER):
        timer = Timer(callback, waketime)
        self.timers.append(timer)
        return timer

    def update_timer(self, timer, waketime):
        timer.waketime = waketime

    def unregister_timer(self, timer):
        timer.waketime = NEVER
        if timer in self.timers:
            self.timers.remove(timer)

    def register_callback(self, callback, waketime=NOW):
        self.callbacks.append(callback)

    register_async_callback = register_callback

    def pause(self, waketime):
        self.advance(waketime)
        return self.now

    def advance(self, until):
        """Move the clock to `until`, updating the heaters and running the timers and callbacks that come due. A
        timer that waits moves the clock without running other timers inside it."""
        busy = []
        while True:
            due = [] if self._advancing else [t for t in self.timers if t.waketime <= until and t not in busy]
            step_to = max(self.now, min([t.waketime for t in due], default=until))
            if step_to > self.now:
                for listener in self.listeners:
                    listener(self.now, step_to)
                self.now = step_to
            if not self._advancing:
                self._run_callbacks()
            if not due:
                return
            timer = min(due, key=lambda t: t.waketime)
            self._advancing = self.in_callback = True
            try:
                timer.waketime = timer.callback(self.now)
            except LockBusy:
                busy.append(timer)
            finally:
                self._advancing = self.in_callback = False

    def _run_callbacks(self):
        waiting = []
        while self.callbacks:
            callback = self.callbacks.pop(0)
            self.in_callback = True
            try:
                callback(self.now)
            except LockBusy:
                waiting.append(callback)
            finally:
                self.in_callback = False
        self.callbacks.extend(waiting)


class Buttons:
    """Klipper's buttons: keeps each registered button's handler for the harness to press."""

    def __init__(self, config):
        self.handlers = {}

    def register_debounce_button(self, pin, callback, config):
        self.handlers[config.get_name()] = callback

    def register_buttons(self, pins, callback):
        for pin in pins:
            self.handlers[pin] = callback


class Webhooks:
    def __init__(self, printer):
        self.printer = printer
        self.endpoints = {}

    def register_endpoint(self, path, callback, *args, **kwargs):
        self.endpoints[path] = callback

    def register_mux_endpoint(self, path, key, value, callback):
        self.endpoints[(path, key, value)] = callback

    def get_status(self, eventtime):
        message, state = self.printer.get_state_message()
        return {"state": state, "state_message": message}

    def call_remote_method(self, *args, **kwargs):
        pass


class Heater:
    """One heater: Klipper's Heater interface, with a temperature that follows the target as the clock moves."""

    def __init__(self, printer, name, kind, config):
        self.printer, self.name, self.kind = printer, name, kind
        self.min_temp = config.getfloat("min_temp", 0.)
        self.max_temp = config.getfloat("max_temp", 300.)
        self.min_extrude_temp = config.getfloat("min_extrude_temp", 170.) if kind == "extruder" else 0.
        self.target_temp = 0.
        self.smoothed_temp = AMBIENT
        self.can_extrude = self.min_extrude_temp <= 0.
        self.lock = threading.Lock()
        self.last_pwm_value = 0.
        self._update_can_extrude()

    def set_temp(self, degrees):
        if degrees and (degrees < self.min_temp or degrees > self.max_temp):
            raise self.printer.command_error("Requested temperature (%.1f) out of range (%.1f:%.1f)"
                                             % (degrees, self.min_temp, self.max_temp))
        with self.lock:
            self.target_temp = degrees

    def get_temp(self, eventtime):
        return self.smoothed_temp, self.target_temp

    def check_busy(self, eventtime):
        return self.smoothed_temp < self.target_temp - 2.

    def get_max_power(self):
        return 1.

    def get_name(self):
        return self.name

    def get_status(self, eventtime):
        return {"temperature": round(self.smoothed_temp, 2), "target": self.target_temp,
                "power": self.last_pwm_value}

    def stats(self, eventtime):
        return False, ""

    def update(self, start, end):
        dt = end - start
        goal = self.target_temp if self.target_temp > 0 else AMBIENT
        if self.smoothed_temp < goal:
            self.smoothed_temp = min(goal, self.smoothed_temp + HEAT_RATE[self.kind] * dt)
            self.last_pwm_value = 1. if self.target_temp else 0.
        else:
            self.smoothed_temp = max(goal, self.smoothed_temp - COOL_RATE[self.kind] * dt)
            self.last_pwm_value = .3 if self.target_temp else 0.
        self._update_can_extrude()

    def _update_can_extrude(self):
        with self.lock:
            self.can_extrude = self.min_extrude_temp <= 0. or self.smoothed_temp >= self.min_extrude_temp

    def set_smoothed_temp(self, value):
        self.smoothed_temp = value
        self._update_can_extrude()


class Heaters:
    """Klipper's heaters object: M105, TEMPERATURE_WAIT, SET_HEATER_TEMPERATURE, TURN_OFF_HEATERS."""

    def __init__(self, printer):
        self.printer = printer
        self.heaters: dict[str, Heater] = {}
        self.sensors: dict[str, object] = {}
        self.available_heaters: list[str] = []
        self.available_sensors: list[str] = []
        self.available_monitors: list[str] = []
        self.waits: list[tuple[str, float, float, float]] = []
        gcode = printer.lookup_object("gcode")
        gcode.register_command("TURN_OFF_HEATERS", self.cmd_TURN_OFF_HEATERS)
        gcode.register_command("M105", self.cmd_M105, when_not_ready=True)
        gcode.register_command("ABORT_TEMPERATURE_WAITS", lambda gcmd: None)
        printer.get_reactor().listeners.append(self._update)

    def add_heater(self, config, name, kind):
        heater = Heater(self.printer, name, kind, config)
        self.heaters[name] = heater
        self.available_heaters.append(config.get_name())
        self.add_sensor(config.get_name(), heater)
        gcode = self.printer.lookup_object("gcode")
        short = config.get_name().split()[-1]
        gcode.register_mux_command("SET_HEATER_TEMPERATURE", "HEATER", short, self._set_heater_command(heater))
        return heater

    def add_sensor(self, full_name, sensor):
        self.sensors[full_name] = sensor
        if full_name not in self.available_sensors:
            self.available_sensors.append(full_name)
        gcode = self.printer.lookup_object("gcode")
        gcode.register_mux_command("TEMPERATURE_WAIT", "SENSOR", full_name, self.cmd_TEMPERATURE_WAIT)

    def lookup_heater(self, name):
        if name not in self.heaters:
            raise self.printer.config_error("Unknown heater '%s'" % (name,))
        return self.heaters[name]

    def get_all_heaters(self):
        return list(self.heaters)

    def get_status(self, eventtime):
        return {"available_heaters": self.available_heaters, "available_sensors": self.available_sensors,
                "available_monitors": self.available_monitors}

    def turn_off_all_heaters(self, print_time=0.):
        for heater in self.heaters.values():
            heater.set_temp(0.)

    def cmd_TURN_OFF_HEATERS(self, gcmd):
        self.turn_off_all_heaters()

    def cmd_M105(self, gcmd):
        gcmd.ack("ok")

    def set_temperature(self, heater, temp, wait=False):
        heater.set_temp(temp)
        if wait and temp:
            self._wait(heater.name, lambda: not heater.check_busy(None), temp, NEVER)

    def _set_heater_command(self, heater):
        def cmd(gcmd):
            self.set_temperature(heater, gcmd.get_float("TARGET", 0.))
        return cmd

    def cmd_TEMPERATURE_WAIT(self, gcmd):
        name = gcmd.get("SENSOR")
        low = gcmd.get_float("MINIMUM", float("-inf"))
        high = gcmd.get_float("MAXIMUM", float("inf"), above=low)
        if low == float("-inf") and high == float("inf"):
            raise gcmd.error("Error on 'TEMPERATURE_WAIT': missing MINIMUM or MAXIMUM.")
        sensor = self.sensors[name]
        self._wait(name, lambda: low <= sensor.get_temp(None)[0] <= high, low, high)

    def _wait(self, name, done, low, high):
        reactor = self.printer.get_reactor()
        start = reactor.monotonic()
        while not done():
            if reactor.monotonic() - start > LONGEST_WAIT:
                raise self.printer.command_error(
                    "harness: waiting for %s to reach %s..%s would never finish" % (name, low, high))
            reactor.pause(reactor.monotonic() + 1.)
        self.waits.append((name, low, high, reactor.monotonic() - start))

    def _update(self, start, end):
        for heater in self.heaters.values():
            heater.update(start, end)


class Extruder:
    """Klipper's extruder: M104, M109, the extruder check on every move, and its status."""

    def __init__(self, config, heaters: Heaters):
        self.printer = config.get_printer()
        self.name = config.get_name()
        self.heater = heaters.add_heater(config, "extruder", "extruder")
        self.max_e_dist = config.getfloat("max_extrude_only_distance", 50.)
        self.pressure_advance = config.getfloat("pressure_advance", 0.)
        self.pressure_advance_smooth_time = config.getfloat("pressure_advance_smooth_time", .04)
        self.last_position = 0.
        gcode = self.printer.lookup_object("gcode")
        gcode.register_command("M104", self.cmd_M104)
        gcode.register_command("M109", self.cmd_M109)
        gcode.register_mux_command("ACTIVATE_EXTRUDER", "EXTRUDER", self.name, lambda gcmd: None)
        gcode.register_command("SET_PRESSURE_ADVANCE", self.cmd_SET_PRESSURE_ADVANCE)
        gcode.register_mux_command("SET_EXTRUDER_ROTATION_DISTANCE", "EXTRUDER", self.name, lambda gcmd: None)
        gcode.register_mux_command("SYNC_EXTRUDER_MOTION", "EXTRUDER", self.name, lambda gcmd: None)

    def get_name(self):
        return self.name

    def get_heater(self):
        return self.heater

    def get_axis_gcode_id(self):
        return "E"

    def get_status(self, eventtime):
        status = self.heater.get_status(eventtime)
        status.update({"can_extrude": self.heater.can_extrude, "pressure_advance": self.pressure_advance,
                       "smooth_time": self.pressure_advance_smooth_time, "motion_queue": None})
        return status

    def check_move(self, move, ea_index):
        if not self.heater.can_extrude:
            raise self.printer.command_error(
                "Extrude below minimum temp\nSee the 'min_extrude_temp' config option for details")
        axis_d = move.axes_d[ea_index]
        if not move.axes_d[0] and not move.axes_d[1] and abs(axis_d) > self.max_e_dist:
            raise self.printer.command_error(
                "Extrude only move too long (%.3fmm vs %.3fmm)\n"
                "See the 'max_extrude_only_distance' config option for details" % (axis_d, self.max_e_dist))

    def cmd_M104(self, gcmd, wait=False):
        temp = gcmd.get_float("S", 0.)
        self.printer.lookup_object("heaters").set_temperature(self.heater, temp, wait)

    def cmd_M109(self, gcmd):
        self.cmd_M104(gcmd, wait=True)

    def cmd_SET_PRESSURE_ADVANCE(self, gcmd):
        self.pressure_advance = gcmd.get_float("ADVANCE", self.pressure_advance, minval=0.)
        self.pressure_advance_smooth_time = gcmd.get_float("SMOOTH_TIME", self.pressure_advance_smooth_time)


@dataclass
class Move:
    """A move the toolhead queued: where it went, how fast, when, and the commands running when it was queued."""

    start: tuple
    end: tuple
    speed: float
    print_time: float
    duration: float
    stack: tuple
    axes_d: list = field(default_factory=list)

    @property
    def extrude(self) -> float:
        return self.end[3] - self.start[3]

    @property
    def xy_moved(self) -> bool:
        return bool(self.axes_d[0] or self.axes_d[1])


class _MoveCheck:
    """What Klipper's checks read from a move."""

    def __init__(self, toolhead, start, end):
        self.toolhead = toolhead
        self.start_pos, self.end_pos = tuple(start), tuple(end)
        self.axes_d = [e - s for s, e in zip(start, end)]
        self.move_d = math.sqrt(sum(d * d for d in self.axes_d[:3]))

    def move_error(self, msg="Move out of range"):
        ep = self.end_pos
        return self.toolhead.printer.command_error("%s: %.3f %.3f %.3f [%.3f]" % (msg, ep[0], ep[1], ep[2], ep[3]))

    def limit_speed(self, speed, accel):
        pass


class Kinematics:
    def __init__(self, toolhead, config):
        self.toolhead = toolhead
        self.ranges = []
        for axis in "xyz":
            section = config.getsection("stepper_" + axis)
            self.ranges.append((section.getfloat("position_min", 0.), section.getfloat("position_max")))
        self.endstops = []
        for axis in "xyz":
            section = config.getsection("stepper_" + axis)
            self.endstops.append(section.getfloat("position_endstop", None))

    def get_status(self, eventtime):
        gcode = self.toolhead.printer.lookup_object("gcode")
        return {"homed_axes": "".join(a for a in "xyz" if a in self.toolhead.homed),
                "axis_minimum": gcode.Coord(*[r[0] for r in self.ranges], 0.),
                "axis_maximum": gcode.Coord(*[r[1] for r in self.ranges], 0.)}

    def check_move(self, move):
        for i, axis in enumerate("xyz"):
            if not move.axes_d[i]:
                continue
            if axis not in self.toolhead.homed:
                raise move.move_error("Must home axis first")
            low, high = self.ranges[i]
            if move.end_pos[i] < low or move.end_pos[i] > high:
                raise move.move_error()

    def get_steppers(self):
        return []


class Toolhead:
    """Klipper's toolhead: queued moves with their timing, G4, M400, SET_VELOCITY_LIMIT, M204, and QIDI's extruder
    enable switch."""

    def __init__(self, config, printer):
        self.printer = printer
        self.reactor = printer.get_reactor()
        printer_config = config.getsection("printer")
        self.max_velocity = printer_config.getfloat("max_velocity")
        self.max_accel = self.max_accel_cap = printer_config.getfloat("max_accel")
        self.square_corner_velocity = printer_config.getfloat("square_corner_velocity", 5.)
        self.min_cruise_ratio = printer_config.getfloat("minimum_cruise_ratio", .5)
        self.max_z_velocity = printer_config.getfloat("max_z_velocity", self.max_velocity)
        self.kin = Kinematics(self, config)
        self.commanded_pos = [0., 0., 0., 0.]
        self.homed: set[str] = set()
        self.print_time = self.reactor.monotonic()
        self.moves: list[Move] = []
        self.extruder = None
        self.e_enable = True
        self.stack_source = lambda: ()
        self.accel_log: list[tuple[float, tuple]] = []
        gcode = printer.lookup_object("gcode")
        self.Coord = gcode.Coord
        gcode.register_command("G4", self.cmd_G4)
        gcode.register_command("M400", self.cmd_M400)
        gcode.register_command("SET_VELOCITY_LIMIT", self.cmd_SET_VELOCITY_LIMIT)
        gcode.register_command("M204", self.cmd_M204)
        gcode.register_command("SET_EXTRUDER_ENABLE", lambda gcmd: None)
        gcode.register_command("SET_EXTRUDER_E_ACCUMULATOR", lambda gcmd: None)

    # Position and moves
    def get_position(self):
        return list(self.commanded_pos)

    def set_position(self, newpos, homing_axes=()):
        self.commanded_pos[:] = list(newpos)[:4] + self.commanded_pos[len(newpos):4]
        for axis in homing_axes:
            self.homed.add("xyz"[axis] if isinstance(axis, int) else axis.lower())
        self.printer.send_event("toolhead:set_position")

    def move(self, newpos, speed):
        start = list(self.commanded_pos)
        end = list(newpos)[:4]
        check = _MoveCheck(self, start, end)
        if not any(check.axes_d):
            return
        if any(check.axes_d[:3]):
            self.kin.check_move(check)
        if check.axes_d[3]:
            if self.extruder is None:
                raise check.move_error("Extrude when no extruder present")
            self.extruder.check_move(check, 3)
        distance = check.move_d if check.move_d else abs(check.axes_d[3])
        speed = min(speed, self.max_velocity) if speed > 0 else self.max_velocity
        if check.axes_d[2] and not check.axes_d[0] and not check.axes_d[1]:
            speed = min(speed, self.max_z_velocity)
        duration = distance / speed if speed else 0.
        self.print_time = max(self.print_time, self.reactor.monotonic()) + duration
        self.moves.append(Move(tuple(start), tuple(end), speed, self.print_time, duration, self.stack_source(),
                               check.axes_d))
        self.commanded_pos[:] = end

    def manual_move(self, coord, speed):
        pos = self.get_position()
        for i, value in enumerate(coord):
            if value is not None:
                pos[i] = value
        self.move(pos, speed)
        self.printer.send_event("toolhead:manual_move")

    def dwell(self, delay):
        self.print_time = max(self.print_time, self.reactor.monotonic()) + max(0., delay)

    def wait_moves(self):
        self.reactor.advance(self.print_time)

    def get_last_move_time(self):
        self.print_time = max(self.print_time, self.reactor.monotonic())
        return self.print_time

    def flush_step_generation(self):
        pass

    def register_lookahead_callback(self, callback):
        callback(self.get_last_move_time())

    def note_mcu_movequeue_activity(self, mq_time, set_step_gen_time=False):
        pass

    # Parts of the toolhead others reach for
    def get_kinematics(self):
        return self.kin

    def get_extruder(self):
        return self.extruder

    def set_extruder(self, extruder, extrude_pos):
        self.extruder = extruder
        self.commanded_pos[3] = extrude_pos

    def get_extra_axes(self):
        return [self.extruder]

    def get_max_velocity(self):
        return self.max_velocity, self.max_accel

    def set_max_velocities(self, max_velocity, max_accel, square_corner_velocity, min_cruise_ratio):
        if max_velocity is not None:
            self.max_velocity = max_velocity
        if max_accel is not None:
            self.max_accel = min(max_accel, self.max_accel_cap)
        if square_corner_velocity is not None:
            self.square_corner_velocity = square_corner_velocity
        if min_cruise_ratio is not None:
            self.min_cruise_ratio = min_cruise_ratio
        self.accel_log.append((self.max_accel, self.stack_source()))
        return self.max_velocity, self.max_accel, self.square_corner_velocity, self.min_cruise_ratio

    def get_status(self, eventtime):
        status = dict(self.kin.get_status(eventtime))
        status.update({"print_time": self.print_time, "stalls": 0,
                       "estimated_print_time": self.reactor.monotonic(),
                       "extruder": self.extruder.get_name() if self.extruder else "",
                       "position": self.Coord(*self.commanded_pos), "max_velocity": self.max_velocity,
                       "max_accel": self.max_accel, "minimum_cruise_ratio": self.min_cruise_ratio,
                       "square_corner_velocity": self.square_corner_velocity, "extra_axes": {}})
        return status

    def stats(self, eventtime):
        return False, ""

    def check_busy(self, eventtime):
        return self.print_time, eventtime, self.print_time <= eventtime

    # Commands
    def cmd_G4(self, gcmd):
        self.dwell(gcmd.get_float("P", 0., minval=0.) / 1000.)

    def cmd_M400(self, gcmd):
        self.wait_moves()

    def cmd_SET_VELOCITY_LIMIT(self, gcmd):
        max_velocity = gcmd.get_float("VELOCITY", None, above=0.)
        max_accel = gcmd.get_float("ACCEL", None, above=0.)
        square_corner_velocity = gcmd.get_float("SQUARE_CORNER_VELOCITY", None, minval=0.)
        min_cruise_ratio = gcmd.get_float("MINIMUM_CRUISE_RATIO", None, minval=0., below=1.)
        self.set_max_velocities(max_velocity, max_accel, square_corner_velocity, min_cruise_ratio)

    def cmd_M204(self, gcmd):
        accel = gcmd.get_float("S", None, above=0.)
        if accel is None:
            p = gcmd.get_float("P", None, above=0.)
            t = gcmd.get_float("T", None, above=0.)
            if p is None or t is None:
                gcmd.respond_info('Invalid M204 command "%s"' % (gcmd.get_commandline(),))
                return
            accel = min(p, t)
        self.set_max_velocities(None, accel, None, None)


class Homing:
    """Klipper's G28 under homing_override: homes the named axes to their endstops at once."""

    def __init__(self, printer, second_homing_command=False):
        self.printer = printer
        self.homes: list[str] = []
        self.second_homing = True
        gcode = printer.lookup_object("gcode")
        gcode.register_command("G28", self.cmd_G28)
        gcode.register_command("G99", lambda gcmd: None)
        if second_homing_command:
            # The Smart 5's homing.py adds SET_SECOND_HOMING, which turns its checked second home on or off.
            gcode.register_command("SET_SECOND_HOMING", self.cmd_SET_SECOND_HOMING)

    def cmd_SET_SECOND_HOMING(self, gcmd):
        self.second_homing = bool(gcmd.get_int("ENABLE", 1))

    def cmd_G28(self, gcmd):
        toolhead = self.printer.lookup_object("toolhead")
        axes = [i for i, axis in enumerate("XYZ") if gcmd.get(axis, None) is not None] or [0, 1, 2]
        pos = toolhead.get_position()
        for i in axes:
            endstop = toolhead.kin.endstops[i]
            pos[i] = endstop if endstop is not None else toolhead.kin.ranges[i][0]
        toolhead.set_position(pos, homing_axes=axes)
        self.homes.append("".join("XYZ"[i] for i in axes))


class StepperEnable:
    def __init__(self, printer):
        self.printer = printer
        self.enabled = {"extruder": True}
        gcode = printer.lookup_object("gcode")
        gcode.register_command("SET_STEPPER_ENABLE", self.cmd_SET_STEPPER_ENABLE)
        gcode.register_command("M84", self.cmd_M84)
        gcode.register_command("M18", self.cmd_M84)

    def cmd_M84(self, gcmd):
        # Motors off: the toolhead loses its homed axes.
        toolhead = self.printer.lookup_object("toolhead")
        toolhead.homed.clear()
        self.enabled = {name: False for name in self.enabled}

    def cmd_SET_STEPPER_ENABLE(self, gcmd):
        self.enabled[gcmd.get("STEPPER")] = bool(gcmd.get_int("ENABLE", 1))

    def get_status(self, eventtime):
        return {"steppers": dict(self.enabled)}

    def lookup_enable(self, name):
        raise self.printer.config_error("Unknown stepper '%s'" % (name,))


class ForceMove:
    def __init__(self, printer):
        self.printer = printer
        gcode = printer.lookup_object("gcode")
        gcode.register_command("SET_KINEMATIC_POSITION", self.cmd_SET_KINEMATIC_POSITION)
        gcode.register_command("FORCE_MOVE", lambda gcmd: None)

    def cmd_SET_KINEMATIC_POSITION(self, gcmd):
        toolhead = self.printer.lookup_object("toolhead")
        pos = toolhead.get_position()
        axes = []
        for i, axis in enumerate("XYZ"):
            value = gcmd.get_float(axis, None)
            if value is not None:
                pos[i] = value
                axes.append(i)
        toolhead.set_position(pos, homing_axes=axes or (0, 1, 2))


class Fan:
    """fan_generic, heater_fan, controller_fan: a speed, and SET_FAN_SPEED for fan_generic."""

    def __init__(self, config, settable):
        self.speed = 0.
        if settable:
            config.get_printer().lookup_object("gcode").register_mux_command(
                "SET_FAN_SPEED", "FAN", config.get_name().split()[-1], self.cmd_SET_FAN_SPEED)

    def cmd_SET_FAN_SPEED(self, gcmd):
        self.speed = gcmd.get_float("SPEED", 0.)

    def get_status(self, eventtime):
        return {"speed": self.speed, "rpm": None}


class OutputPin:
    def __init__(self, config):
        self.value = config.getfloat("value", 0.)
        config.get_printer().lookup_object("gcode").register_mux_command(
            "SET_PIN", "PIN", config.get_name().split()[-1], self.cmd_SET_PIN)

    def cmd_SET_PIN(self, gcmd):
        self.value = gcmd.get_float("VALUE", 0.)

    def get_status(self, eventtime):
        return {"value": self.value}


class Led:
    def __init__(self, config):
        self.color = [[0., 0., 0., 0.]]
        config.get_printer().lookup_object("gcode").register_mux_command(
            "SET_LED", "LED", config.get_name().split()[-1], lambda gcmd: None)

    def get_status(self, eventtime):
        return {"color_data": self.color}


class TemperatureSensor:
    def __init__(self, temperature=AMBIENT):
        self.temperature = temperature

    def get_temp(self, eventtime):
        return self.temperature, 0.

    def get_status(self, eventtime):
        return {"temperature": self.temperature}


class StatusOnly:
    """An object macros read only status from."""

    def __init__(self, status=None):
        self.status = dict(status or {})

    def get_status(self, eventtime):
        return dict(self.status)


class IdleTimeout:
    """idle_timeout's state as macros read it: Printing while a print runs, Ready otherwise."""

    def __init__(self, printer):
        self.printer = printer
        self.timeout = 600.
        printer.lookup_object("gcode").register_command("SET_IDLE_TIMEOUT", self.cmd_SET_IDLE_TIMEOUT)

    def cmd_SET_IDLE_TIMEOUT(self, gcmd):
        self.timeout = gcmd.get_float("TIMEOUT", self.timeout, above=0.)

    def get_status(self, eventtime):
        sdcard = self.printer.lookup_object("virtual_sdcard")
        state = "Printing" if sdcard.is_active() else "Ready"
        return {"state": state, "printing_time": 0.}


class BedMesh:
    """bed_mesh. BED_MESH_CALIBRATE travels the toolhead over the probe grid as QIDI's bed_mesh.py and probe.py do:
    a zigzag from mesh_min to mesh_max, lifting to horizontal_move_z before each point (at the mesh speed for the
    first, the probe's lift speed after) and once more at the end. The probe's touches aren't moves here."""

    def __init__(self, printer, config):
        self.printer = printer
        self.profile_name = ""
        self.speed = config.getfloat("speed", 50.)
        self.horizontal_move_z = config.getfloat("horizontal_move_z", 5.)
        self.mesh_min = config.getfloatlist("mesh_min", count=2)
        self.mesh_max = config.getfloatlist("mesh_max", count=2)
        self.probe_count = config.getintlist("probe_count", (3, 3), count=2)
        probe = config.getsection("probe")
        self.probe_offsets = (probe.getfloat("x_offset", 0.), probe.getfloat("y_offset", 0.))
        self.lift_speed = probe.getfloat("lift_speed", probe.getfloat("speed", 5.))
        gcode = printer.lookup_object("gcode")
        gcode.register_command("BED_MESH_CLEAR", self.cmd_BED_MESH_CLEAR)
        gcode.register_command("BED_MESH_PROFILE", self.cmd_BED_MESH_PROFILE)
        gcode.register_command("BED_MESH_CALIBRATE", self.cmd_BED_MESH_CALIBRATE)
        gcode.register_command("BED_MESH_OFFSET", lambda gcmd: None)

    def cmd_BED_MESH_CLEAR(self, gcmd):
        self.profile_name = ""

    def cmd_BED_MESH_PROFILE(self, gcmd):
        self.profile_name = gcmd.get("LOAD", gcmd.get("PROFILE", "default"))

    def points(self):
        (min_x, min_y), (max_x, max_y), (x_count, y_count) = self.mesh_min, self.mesh_max, self.probe_count
        x_dist = math.floor((max_x - min_x) / (x_count - 1) * 100) / 100
        y_dist = math.floor((max_y - min_y) / (y_count - 1) * 100) / 100
        max_x = min_x + x_dist * (x_count - 1)
        return [(min_x + j * x_dist if not i % 2 else max_x - j * x_dist, min_y + i * y_dist)
                for i in range(y_count) for j in range(x_count)]

    def cmd_BED_MESH_CALIBRATE(self, gcmd):
        if gcmd.get_int("ADAPTIVE", 0):
            raise AssertionError("harness: bed_mesh's adaptive mesh has no stand-in")
        toolhead = self.printer.lookup_object("toolhead")
        move_z = gcmd.get_float("HORIZONTAL_MOVE_Z", self.horizontal_move_z)
        for number, (x, y) in enumerate(self.points()):
            toolhead.manual_move([None, None, move_z], self.speed if not number else self.lift_speed)
            toolhead.manual_move([x - self.probe_offsets[0], y - self.probe_offsets[1]], self.speed)
        toolhead.manual_move([None, None, move_z], self.lift_speed)
        self.profile_name = gcmd.get("PROFILE", "default")

    def get_status(self, eventtime):
        return {"profile_name": self.profile_name, "mesh_min": (0., 0.), "mesh_max": (0., 0.),
                "probed_matrix": [[]], "mesh_matrix": [[]], "profiles": {}}

    def get_mesh(self):
        return None

    def set_mesh(self, mesh):
        pass


def register_bed_commands(printer, heater):
    """heater_bed's M140 and M190."""
    heaters = printer.lookup_object("heaters")
    gcode = printer.lookup_object("gcode")
    gcode.register_command("M140", lambda gcmd: heaters.set_temperature(heater, gcmd.get_float("S", 0.)))
    gcode.register_command("M190", lambda gcmd: heaters.set_temperature(heater, gcmd.get_float("S", 0.), True))


class ZTilt:
    def __init__(self, printer):
        self.applied = False
        printer.lookup_object("gcode").register_command("Z_TILT_ADJUST", self.cmd_Z_TILT_ADJUST)

    def cmd_Z_TILT_ADJUST(self, gcmd):
        self.applied = True

    def get_status(self, eventtime):
        return {"applied": self.applied}


class InputShaper:
    """input_shaper: SET_INPUT_SHAPER, with the shapers from the config."""

    def __init__(self, config):
        self.shapers = []
        config.get_printer().lookup_object("gcode").register_command("SET_INPUT_SHAPER", lambda gcmd: None)

    def get_shapers(self):
        return self.shapers

    def get_status(self, eventtime):
        return {}


class ClosedLoop:
    """The Max 4's closed-loop X/Y drivers: their settings commands, which move nothing."""

    # SET_HOMING_STATE and SET_HOMING_MODE are what QIDI's homing.py sends them around a home.
    COMMANDS = ("SET_MOTOR_TEMP_PROTECT", "EXIT_ENFORCE_MODE", "SET_HOMING_MODE", "SET_HOMING_STATE")

    def __init__(self, printer):
        gcode = printer.lookup_object("gcode")
        for command in self.COMMANDS:
            if command not in gcode.ready_gcode_handlers:
                gcode.register_command(command, lambda gcmd: None)


class StepperDriver:
    """A TMC stepper driver: SET_TMC_FIELD, SET_TMC_CURRENT, and DUMP_TMC for its stepper, which move nothing."""

    def __init__(self, config):
        stepper = config.get_name().split()[-1]
        gcode = config.get_printer().lookup_object("gcode")
        for command in ("SET_TMC_FIELD", "SET_TMC_CURRENT", "DUMP_TMC", "INIT_TMC"):
            gcode.register_mux_command(command, "STEPPER", stepper, lambda gcmd: None)

    def get_status(self, eventtime):
        return {}


class BoardControl:
    """The Smart 5's cuc.so for one motor: SET_CUC_CURRENT and its other settings commands, which move nothing
    (qidi-platform.md, Smart 5 Klipper and stock configuration)."""

    COMMANDS = ("SET_CUC_CURRENT", "CUC_READ", "CUC_LOG", "CUC_SET_THRESH", "CUC_TEST_EN", "CUC_TEST_CLOSE")

    def __init__(self, printer):
        gcode = printer.lookup_object("gcode")
        for command in self.COMMANDS:
            if command not in gcode.ready_gcode_handlers:
                gcode.register_command(command, lambda gcmd: None)


class Probe:
    """The load-cell probe (QIDI's probe_air): PROBE and its relatives touch the bed at Z 0 where the toolhead is."""

    def __init__(self, printer):
        self.printer = printer
        self.last_z_result = 0.
        gcode = printer.lookup_object("gcode")
        for command in ("PROBE", "PROBE_ACCURACY", "QUERY_PROBE", "PROBE_CALIBRATE"):
            gcode.register_command(command, self.cmd_PROBE)

    def cmd_PROBE(self, gcmd):
        toolhead = self.printer.lookup_object("toolhead")
        if "z" not in toolhead.homed and "x" not in toolhead.homed:
            raise gcmd.error("Must home before probe")
        self.last_z_result = 0.

    def get_status(self, eventtime):
        return {"last_z_result": self.last_z_result, "last_query": False, "name": "probe"}
