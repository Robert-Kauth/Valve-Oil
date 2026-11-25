from __future__ import annotations

import types
import unittest

from installer.tests.helpers import REPO_ROOT, load_source
from installer.tests.klipper_fakes import Reactor, load_extra


box_circulation = load_extra("box_circulation")


class Fan:
    """Klipper's fan.Fan, recording the speeds QIDI's box_heater_fan sets."""

    def __init__(self, config):
        self.speed = 0.

    def get_mcu(self):
        return types.SimpleNamespace(estimated_print_time=lambda eventtime: eventtime)

    def set_speed(self, print_time, speed):
        self.speed = speed


# QIDI's box_heater_fan, as in both Klippers Valve Oil runs.
box_heater_fan = load_source(
    "extras.box_heater_fan", REPO_ROOT / "klipper/klippy/extras/box_heater_fan.py",
    stand_ins={"extras": types.SimpleNamespace(__path__=[], fan=types.SimpleNamespace(Fan=Fan))})


class Heater:
    def __init__(self):
        self.temp, self.target = 25., 0.

    def get_temp(self, eventtime):
        return self.temp, self.target


class Steppers:
    def get_steppers(self):
        return []


class Printer:
    def __init__(self, reactor, heater, variables):
        self.reactor = reactor
        self.handlers = {}
        self.objects = {
            "heaters": types.SimpleNamespace(lookup_heater=lambda name: heater),
            "stepper_enable": Steppers(),
            "save_variables": types.SimpleNamespace(allVariables=variables),
        }

    def get_reactor(self):
        return self.reactor

    def register_event_handler(self, event, callback):
        self.handlers.setdefault(event, []).append(callback)

    def load_object(self, config, name):
        return self.objects[name]

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)

    def lookup_objects(self, module):
        return [(name, obj) for name, obj in self.objects.items() if name.startswith(module + " ")]

    def send_event(self, event):
        for callback in self.handlers.get(event, []):
            callback()


class Config:
    """box.cfg's options for one of a Box's fans, as QIDI's box_config builds them."""

    def __init__(self, printer, options=None):
        self.printer = printer
        self.options = {"heater": "heater_box1", "heater_temp": 35., "idle_timeout": 60, "stepper": ""}
        self.options.update(options or {})

    def get_printer(self):
        return self.printer

    def getfloat(self, name, default=None, **kwargs):
        return float(self.options.get(name, default))

    def getint(self, name, default=None, **kwargs):
        return int(self.options.get(name, default))

    def getlist(self, name, default=None):
        value = self.options.get(name, default)
        return [part.strip() for part in value.split(",") if part.strip()] if isinstance(value, str) else value


class BoxCirculationTests(unittest.TestCase):
    def setUp(self):
        self.reactor = Reactor()
        self.heater = Heater()
        self.variables = {}
        self.printer = Printer(self.reactor, self.heater, self.variables)
        self.fans = [box_heater_fan.load_config_prefix(Config(self.printer)) for _ in "ab"]
        self.printer.objects["box_heater_fan heater_fan_a_box1"] = self.fans[0]
        self.printer.objects["box_heater_fan heater_fan_b_box1"] = self.fans[1]
        box_circulation.load_config(Config(self.printer))
        self.printer.send_event("klippy:connect")
        self.printer.send_event("klippy:ready")

    def run_seconds(self, seconds, temp=None):
        """Moves Klippy's clock on a second at a time, with the Box air at `temp`; returns the fans' speeds."""
        if temp is not None:
            self.heater.temp = temp
        speeds = []
        for _ in range(seconds):
            for timer in self.reactor.timers:
                if timer.waketime <= self.reactor.now:
                    timer.waketime = timer.callback(self.reactor.now)
            speeds.append([fan.fan.speed for fan in self.fans])
            self.reactor.now += 1.
        return speeds

    def test_fans_keep_running_until_the_air_falls_by_the_hysteresis(self):
        self.assertEqual(self.run_seconds(3, 35.1)[-1], [1., 1.])
        # Below QIDI's 35 C, but not 0.5 C below: the fans don't start their run-on.
        self.assertEqual(self.run_seconds(120, 34.9)[-1], [1., 1.])
        self.assertEqual(self.run_seconds(120, 34.5)[-1], [0., 0.])
        # Back up to the threshold, not above it: they stay off.
        self.assertEqual(self.run_seconds(120, 35.)[-1], [0., 0.])

    def test_the_run_on_follows_the_release(self):
        self.run_seconds(3, 36.)
        speeds = self.run_seconds(70, 34.4)
        self.assertEqual(speeds[55], [1., 1.])
        self.assertEqual(speeds[-1], [0., 0.])

    def test_saved_settings_apply_without_a_restart(self):
        self.variables.update(vo_box_fan_threshold=40, vo_box_fan_hysteresis=1)
        self.assertEqual(self.run_seconds(120, 38.)[-1], [0., 0.])
        self.run_seconds(3, 40.5)
        self.assertEqual(self.run_seconds(120, 39.1)[-1], [1., 1.])
        self.assertEqual(self.run_seconds(120, 39.)[-1], [0., 0.])

    def test_hysteresis_zero_is_qidis_behaviour(self):
        self.variables["vo_box_fan_hysteresis"] = 0
        self.run_seconds(3, 35.1)
        speeds = self.run_seconds(70, 34.99)
        self.assertEqual(speeds[55], [1., 1.])
        self.assertEqual(speeds[-1], [0., 0.])

    def test_values_out_of_range_are_ignored(self):
        for threshold, hysteresis in ((34, 3), (46, -1), (True, "x"), (float("nan"), None)):
            self.variables.update(vo_box_fan_threshold=threshold, vo_box_fan_hysteresis=hysteresis)
            with self.assertLogs(level="WARNING"):
                self.run_seconds(1, 20.)
            self.assertEqual([fan.heater_temp for fan in self.fans], [35., 35.])

    def test_heating_still_runs_the_fans(self):
        self.heater.target = 50.
        self.assertEqual(self.run_seconds(2, 25.)[-1], [1., 1.])


if __name__ == "__main__":
    unittest.main()
