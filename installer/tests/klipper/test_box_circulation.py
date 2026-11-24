"""Circulation integration with the real native Box fan loader and timers.

Only the MCU, thermal inputs and persistence I/O are doubles; no hardware
fault is injected into a heated printer.
"""
import ast
import struct
import types
import unittest

from installer.tests.helpers import REPO_ROOT, load_source
from installer.tests.klipper_fakes import CommandError, load_extra
from installer.tests.klipper.test_box_monitors import Printer, Config as BaseConfig, Status

circulation = load_extra("box_circulation")
qidi_mcu = load_source(
    "qidi_circulation_mcu",
    REPO_ROOT / "installer/tests/fixtures/source-patches/qidi-mcu-adc.py")
native_mcu = load_source(
    "circulation_mcu", REPO_ROOT / "klipper/klippy/mcu.py",
    stand_ins={name: types.SimpleNamespace() for name in
               ("serialhdl", "msgproto", "pins", "chelper", "clocksync")})


class Fan:
    def __init__(self, config):
        self.max_power = 1.
        self.speed = 0.
        self.commands = []

    def get_mcu(self):
        return self

    def estimated_print_time(self, eventtime):
        return eventtime

    def set_speed(self, when, speed):
        self.commands.append((when, speed))
        self.speed = speed

    def get_status(self, eventtime):
        return {"speed": self.speed, "rpm": None}


native = load_source("extras.box_heater_fan",
                     REPO_ROOT / "klipper/klippy/extras/box_heater_fan.py",
                     stand_ins={"extras": types.SimpleNamespace(fan=types.SimpleNamespace(Fan=Fan))})


class Config(BaseConfig):
    error = CommandError

    def getsection(self, name):
        return Config(self.printer, self.sections, name)

    def getlist(self, key, default=None):
        return self.get(key, default)

    def getfloat(self, key, default=None, **kwargs):
        return float(self.get(key, default))

    def getint(self, key, default=None, **kwargs):
        return int(self.get(key, default))


class Command:
    error = CommandError

    def __init__(self, **params):
        self.params = params

    def get_int(self, key, **kwargs):
        return int(self.params[key])

    def get_float(self, key, **kwargs):
        return float(self.params[key])

    def respond_info(self, message):
        pass


class Heater:
    def __init__(self):
        self.air, self.target, self.power = 30., 0., 0.

    def get_temp(self, eventtime):
        return self.air, self.target

    def get_status(self, eventtime):
        return {"temperature": self.air, "target": self.target, "power": self.power}

    def set_temp(self, value):
        self.target = value


class MCUClock:
    def __init__(self):
        self.offset = 0.

    def estimated_print_time(self, eventtime):
        return eventtime + self.offset

    def clock32_to_clock64(self, clock):
        return clock

    def clock_to_print_time(self, clock):
        return clock / 1_000_000.

    def register_config_callback(self, callback):
        pass

class Sensor:
    def __init__(self, adc_type):
        self.sensor = self
        # Use the real producer/accessor on both supported Klipper trees.
        self.mcu_adc = adc_type(MCUClock(), {"pin": "test"})
        self.mcu_adc._inv_max_adc = 1. / 4095.
        self.mcu_adc._report_clock = 300_000
        self.value = 30.
        self.callback = self.temperature_callback

    def temperature_callback(self, eventtime, value):
        params = {"next_clock": round(eventtime * 1_000_000.) + 300_000}
        if isinstance(self.mcu_adc, native_mcu.MCU_adc):
            params["values"] = struct.pack("<H", 2048)
        else:
            params["value"] = 2048
        self.mcu_adc._handle_analog_in_state(params)
        self.value = value

    def setup_callback(self, callback):
        self.callback = callback

    def get_temp(self, eventtime):
        return self.value, 0.

class Saved:
    def __init__(self, values=None):
        self.values = values or {}
        self.fail = False
        self.writes = 0

    def get_status(self, eventtime):
        return {"variables": self.values}

    def cmd_SAVE_VARIABLE(self, cmd):
        if self.fail:
            raise CommandError("storage unavailable")
        self.values[cmd.params["VARIABLE"]] = ast.literal_eval(cmd.params["VALUE"])
        self.writes += 1


def fake_serial(number=1, version="1.2.1"):
    """Generate synthetic MCU identities; never copy a physical device ID."""
    return f"/dev/serial/by-id/usb-Klipper_QIDI_BOX_V2_{version}_{number:024x}-if00"


class Harness:
    def __init__(self, identities=None, saved=None, enabled=True, idle_timeout=60,
                 adc_type=qidi_mcu.MCU_adc):
        identities = identities or {"1": fake_serial()}
        self.printer = p = Printer()
        p.config_error = CommandError
        self.saved = saved or Saved()
        p.objects.update({"save_variables": self.saved, "print_stats": Status(state="standby"),
                          "box_extras": Status(boxes=[{"dry_state": 0} for _ in identities])})
        p.gcode.create_gcode_command = lambda name, line, params: Command(**params)
        self.motor = False
        self.heaters, self.sensors, self.fans = {}, {}, {}
        self.native_callbacks, self.sensor_callbacks = {}, {}
        sections = {"vo_box_circulation": {}}
        for n, serial in identities.items():
            sections["mcu mcu_box" + n] = {"serial": serial}
        p.objects["heaters"] = types.SimpleNamespace(lookup_heater=lambda name: self.heaters[name])
        p.objects["stepper_enable"] = types.SimpleNamespace(
            get_steppers=lambda: ["slot-1"],
            lookup_enable=lambda name: types.SimpleNamespace(is_motor_enabled=lambda: self.motor))
        cfg = Config(p, sections, "vo_box_circulation")
        self.manager = circulation.load_config(cfg) if enabled else None
        for n in identities:
            self.heaters["heater_box" + n] = Heater()
            for side in ("a", "b"):
                name = "box_heater_fan heater_fan_%s_box%s" % (side, n)
                sections[name] = {"heater": ["heater_box" + n], "heater_temp": 35.,
                                  "stepper": ["slot-1"], "idle_timeout": idle_timeout}
                obj = native.load_config_prefix(Config(p, sections, name))
                self.fans[n + side] = p.objects[name] = obj
                self.native_callbacks[n + side] = obj.callback
                sensor_name = "temperature_sensor heater_temp_%s_box%s" % (side, n)
                self.sensors[n + side] = p.objects[sensor_name] = Sensor(adc_type)
                self.sensor_callbacks[n + side] = self.sensors[n + side].callback
        p.send_event("klippy:connect")
        self.refresh()
        p.send_event("klippy:ready")
        self.advance(1)

    def refresh(self, missing=()):
        for name, sensor in self.sensors.items():
            if name not in missing:
                print_time = sensor.mcu_adc.get_mcu().estimated_print_time(
                    self.printer.reactor.now
                )
                sensor.callback(print_time, sensor.value)

    def advance(self, seconds, missing=()):
        for _ in range(seconds):
            self.refresh(missing)
            self.printer.advance(1)

    def reason(self, n="1"):
        return self.manager.get_status(self.printer.reactor.now)["boxes"][n]["reason"]

    def configure(self, n=1, threshold=36., hysteresis=.5):
        self.manager.configure(Command(BOX=n, THRESHOLD=threshold, HYSTERESIS=hysteresis))


class BoxCirculationTests(unittest.TestCase):
    def test_unknown_adc_contract_is_rejected_before_controllers_attach(self):
        class UnknownADC(qidi_mcu.MCU_adc):
            def setup_adc_callback(self, callback, options):
                pass

        with self.assertRaisesRegex(CommandError, "Unsupported Box ADC callback API"):
            Harness(adc_type=UnknownADC)

    def test_actual_adc_contracts_recover_fault_and_finish_run_on(self):
        # The two Klipper trees expose opposite sample tuple orders.
        # Feed real transport handlers so a double cannot hide that mismatch.
        for adc_type in (qidi_mcu.MCU_adc, native_mcu.MCU_adc):
            with self.subTest(adc_type=adc_type):
                h = Harness(adc_type=adc_type)
                assert h.manager is not None
                h.advance(10)
                self.assertIsNone(h.manager.boxes["1"].fault)
                self.assertEqual(h.reason(), "off")
                h.heaters["heater_box1"].target = 38.
                h.advance(8, missing=("1a",))
                self.assertEqual(h.reason(), "sensor_fault")
                self.assertEqual(h.heaters["heater_box1"].target, 0.)
                self.assertEqual([h.fans[k].fan.speed for k in ("1a", "1b")], [1., 1.])
                h.advance(2)
                self.assertIsNone(h.manager.boxes["1"].fault)
                self.assertEqual(h.reason(), "run_on")
                h.advance(61)
                self.assertEqual(h.reason(), "off")
                self.assertEqual([h.fans[k].fan.speed for k in ("1a", "1b")], [0., 0.])
                self.assertEqual(h.heaters["heater_box1"].target, 0.)

    def test_native_loader_timers_have_one_owner_and_uninstall_restores_stock(self):
        h = Harness()
        assert h.manager is not None
        self.assertEqual(len(h.printer.reactor.timers), 1)
        for name, fan in h.fans.items():
            self.assertEqual(fan.callback, h.native_callbacks[name])
            self.assertEqual(h.sensors[name].callback, h.sensor_callbacks[name])
            self.assertIs(fan.controller, h.manager.boxes["1"])
        self.assertEqual(h.manager.get_status(0)["boxes"]["1"]["release_temperature"], 34.5)
        h.heaters["heater_box1"].air = 35.1
        h.advance(2)
        self.assertEqual([h.fans[k].fan.speed for k in ("1a", "1b")], [1., 1.])
        h.heaters["heater_box1"].air = 34.9
        h.advance(70)
        self.assertEqual(h.reason(), "temperature")
        h.heaters["heater_box1"].air = 34.4
        h.advance(61)
        self.assertEqual(h.reason(), "off")
        self.assertEqual([h.fans[k].fan.speed for k in ("1a", "1b")], [0., 0.])
        stock = Harness(enabled=False)
        self.assertEqual(len(stock.printer.reactor.timers), 2)
        self.assertTrue(all(fan.controller is None for fan in stock.fans.values()))
        stock.heaters["heater_box1"].air = 35.1
        stock.advance(2)
        self.assertEqual(stock.fans["1a"].fan.speed, 1.)
        stock.heaters["heater_box1"].air = 34.8
        stock.advance(61)
        self.assertEqual(stock.fans["1a"].fan.speed, 0.)

    def test_controller_preserves_the_configured_native_run_on(self):
        h = Harness(idle_timeout=3)
        assert h.manager is not None
        self.assertEqual(h.manager.boxes["1"].state.run_on, 3)
        h.heaters["heater_box1"].target = 38.
        h.advance(1)
        h.heaters["heater_box1"].target = 0.
        h.advance(2)
        self.assertEqual(h.reason(), "run_on")
        h.advance(2)
        self.assertEqual(h.reason(), "off")

    def test_only_attached_controller_can_drive_native_output(self):
        h = Harness()
        fan = h.fans["1a"]
        with self.assertRaises(CommandError):
            fan.set_controller_speed(object(), 1.)
        assert h.manager is not None
        for speed in (float("nan"), -0.1, 1.1):
            with self.subTest(speed=speed), self.assertRaises(CommandError):
                fan.set_controller_speed(h.manager.boxes["1"], speed)
        self.assertEqual(fan.fan.speed, 0.)
        h.heaters["heater_box1"].target = 38.
        h.advance(1)
        self.assertEqual([h.fans[k].fan.speed for k in ("1a", "1b")], [1., 1.])

    def test_input_failures_log_the_exact_boundary_and_fail_safe(self):
        for source in ("heater", "outlet_a", "motor"):
            with self.subTest(source=source):
                h = Harness()
                if source == "heater":
                    def heater_failure(eventtime):
                        raise RuntimeError("heater read failed")
                    h.heaters["heater_box1"].get_temp = heater_failure
                elif source == "outlet_a":
                    def outlet_failure(eventtime):
                        raise RuntimeError("outlet read failed")
                    h.sensors["1a"].get_temp = outlet_failure
                else:
                    def motor_failure(name):
                        raise RuntimeError("motor read failed")
                    h.fans["1a"].stepper_enable.lookup_enable = motor_failure
                assert h.manager is not None
                with self.assertLogs(level="ERROR") as captured:
                    h.manager.boxes["1"].tick(h.printer.reactor.now)
                self.assertTrue(any("source=" + source in line for line in captured.output))
                self.assertEqual(h.reason(), "sensor_fault")
                self.assertEqual(h.heaters["heater_box1"].target, 0.)
                self.assertEqual([h.fans[k].fan.speed for k in ("1a", "1b")], [1., 1.])

    def test_sensor_freshness_uses_current_time_not_timer_dispatch_time(self):
        h = Harness()
        assert h.manager is not None
        box = h.manager.boxes["1"]
        h.refresh()
        box.tick(h.printer.reactor.now - .001)
        self.assertIsNone(box.fault)
        self.assertEqual(h.reason(), "off")
        # A serial callback can arrive after the timer's clock snapshot.
        heater = h.heaters["heater_box1"]
        heater.target = 38.
        def concurrent_sample(eventtime):
            h.printer.reactor.now += .001
            h.refresh()
            return heater.air, heater.target
        heater.get_temp = concurrent_sample
        box.tick(h.printer.reactor.now)
        self.assertIsNone(box.fault)
        self.assertEqual(heater.target, 38.)
        heater.get_temp = lambda eventtime: (heater.air, heater.target)
        # An old timer timestamp must not hide a genuinely expired sample.
        old = h.printer.reactor.now
        h.printer.reactor.now += circulation.SENSOR_TIMEOUT + 1.
        box.tick(old)
        self.assertEqual(h.reason(), "sensor_fault")
        self.assertEqual(heater.target, 0.)

    def test_sensor_freshness_uses_each_box_mcu_print_clock(self):
        h = Harness({"1": fake_serial(1), "2": fake_serial(2)})
        assert h.manager is not None
        host_now = 10_000.
        h.printer.reactor.now = host_now
        for name in ("1a", "1b"):
            sensor = h.sensors[name]
            sensor.mcu_adc.get_mcu().offset = -9_000.
            sensor.mcu_adc._last_state = (0.5, 999.75)
        for name in ("2a", "2b"):
            sensor = h.sensors[name]
            sensor.mcu_adc.get_mcu().offset = 9_000.
            sensor.mcu_adc._last_state = (0.5, 18_900.)
        for number in ("1", "2"):
            h.heaters["heater_box" + number].target = 38.
            h.manager.boxes[number].tick(host_now)
        self.assertIsNone(h.manager.boxes["1"].fault)
        self.assertEqual(h.heaters["heater_box1"].target, 38.)
        self.assertEqual(h.manager.boxes["2"].fault,
                         "invalid_or_stale_temperature")
        self.assertEqual(h.heaters["heater_box2"].target, 0.)

    def test_sensor_sample_time_rejects_absent_and_nonfinite_but_accepts_newer(self):
        for read_time, expected_fault in (
                (0., "invalid_or_stale_temperature"),
                (float("nan"), "invalid_or_stale_temperature"),
                (float("inf"), "invalid_or_stale_temperature"),
                (10_000.001, None)):
            with self.subTest(read_time=read_time):
                h = Harness()
                assert h.manager is not None
                h.printer.reactor.now = 1_000.
                h.heaters["heater_box1"].target = 38.
                for sensor in h.sensors.values():
                    sensor.mcu_adc.get_mcu().offset = 9_000.
                    sensor.mcu_adc._last_state = (0.5, read_time)
                box = h.manager.boxes["1"]
                box.tick(h.printer.reactor.now)
                self.assertEqual(box.fault, expected_fault)
                self.assertEqual(h.heaters["heater_box1"].target,
                                 0. if expected_fault else 38.)

    def test_no_chatter_inside_deadband_and_reheat_resets_run_on(self):
        h = Harness()
        heater = h.heaters["heater_box1"]
        heater.target = 38.
        h.advance(1)
        self.assertEqual(h.reason(), "heating")
        heater.target = 0.
        heater.air = 35.01
        h.advance(1)
        for air in [34.95, 35.01, 34.9, 35., 34.85]:
            heater.air = air
            h.advance(65)
            self.assertEqual(h.fans["1a"].fan.speed, 1.)
        heater.air = 34.49
        h.advance(30)
        self.assertEqual(h.reason(), "run_on")
        heater.target = 38.
        h.advance(1)
        self.assertEqual(h.reason(), "heating")
        heater.target = 0.
        h.advance(61)
        self.assertEqual(h.reason(), "off")

    def test_valid_outlet_temperatures_do_not_control_circulation(self):
        for side in ("a", "b"):
            with self.subTest(side=side):
                h = Harness()
                heater = h.heaters["heater_box1"]
                heater.target = 38.
                h.advance(1)
                self.assertEqual(h.reason(), "heating")
                heater.target = 0.
                h.sensors["1" + side].value = 82.
                h.advance(30)
                self.assertEqual(h.reason(), "run_on")
                h.advance(31)
                self.assertEqual(h.reason(), "off")
                for temperature in (39., 40., 42., 45., 49., 82.):
                    h.sensors["1" + side].value = temperature
                    h.advance(1)
                    self.assertEqual(h.reason(), "off")
                    self.assertEqual([h.fans[k].fan.speed for k in ("1a", "1b")], [0., 0.])
                heater.air = 35.1
                h.advance(1)
                self.assertEqual(h.reason(), "temperature")
                heater.air = 34.4
                h.advance(61)
                self.assertEqual(h.reason(), "off")

    def test_missing_stale_nan_and_outlier_shutdown_heat_not_circulation(self):
        for side in ("a", "b"):
            for value in (None, float("nan"), float("inf"), -10., 135.):
                with self.subTest(side=side, value=value):
                    h = Harness()
                    h.heaters["heater_box1"].target = 38.
                    if value is None:
                        h.advance(8, missing=("1" + side,))
                    else:
                        h.sensors["1" + side].value = value
                        h.advance(2)
                    self.assertEqual(h.reason(), "sensor_fault")
                    self.assertEqual(h.heaters["heater_box1"].target, 0.)
                    self.assertEqual([h.fans[k].fan.speed for k in ("1a", "1b")], [1., 1.])
                    h.sensors["1" + side].value = 30.
                    h.advance(62)
                    self.assertEqual(h.heaters["heater_box1"].target, 0.)
                    self.assertEqual(h.reason(), "off")

    def test_bad_average_and_residual_power_are_not_heater_off(self):
        h = Harness()
        heater = h.heaters["heater_box1"]
        heater.power = .1
        h.advance(1)
        self.assertEqual(h.reason(), "heating")
        # Heater.get_temp returns zero for stale average-temperature data.
        for invalid in (0., float("nan")):
            heater.target = 38.
            heater.air = invalid
            h.advance(1)
            self.assertEqual(h.reason(), "sensor_fault")
            self.assertEqual(heater.target, 0.)

    def test_motor_circulation_keeps_native_timeout(self):
        h = Harness()
        h.motor = True
        h.advance(1)
        self.assertEqual(h.reason(), "motor")
        h.motor = False
        h.advance(30)
        self.assertEqual(h.reason(), "run_on")
        h.advance(31)
        self.assertEqual(h.reason(), "off")

    def test_saved_settings_follow_physical_identity_after_topology_change(self):
        h = Harness()
        h.configure(threshold=40., hysteresis=1.)
        self.assertEqual(h.saved.writes, 1)
        identities = {
            "1": fake_serial(2),
            "2": fake_serial(version="1.3.0"),
        }
        changed = Harness(identities, h.saved)
        status = changed.manager.get_status(changed.printer.reactor.now)["boxes"]
        self.assertEqual(status["1"]["threshold"], 35.)
        self.assertEqual(status["2"]["threshold"], 40.)
        self.assertEqual(status["2"]["hysteresis"], 1.)
        changed.heaters["heater_box1"].target = 38.
        changed.advance(1)
        with self.assertRaises(CommandError):
            changed.configure(n=2)
        self.assertEqual(changed.saved.writes, 1)

    def test_invalid_or_transient_identity_cannot_persist_settings(self):
        for serial in ("/dev/ttyACM0", "/dev/serial/by-id/not-a-qidi-box",
                       "/dev/serial/by-id/usb-Klipper_QIDI_BOX_V2_1.2.1_invalid-if00"):
            with self.subTest(serial=serial):
                h = Harness({"1": serial})
                assert h.manager is not None
                self.assertFalse(h.manager.get_status(h.printer.reactor.now)["boxes"]["1"]["configurable"])
                with self.assertRaises(CommandError):
                    h.configure()
                self.assertEqual(h.saved.writes, 0)

    def test_settings_recheck_busy_print_dry_heat_motor_and_fault(self):
        for condition in ("printing", "paused", "extracting", "dry", "heat", "motor", "fault"):
            with self.subTest(condition=condition):
                h = Harness()
                if condition in ("printing", "paused", "extracting"):
                    h.printer.objects["print_stats"].status["state"] = condition
                elif condition == "dry":
                    h.printer.objects["box_extras"].status["boxes"][0]["dry_state"] = 1
                elif condition == "heat":
                    h.heaters["heater_box1"].target = 38.
                elif condition == "motor":
                    h.motor = True
                else:
                    h.sensors["1a"].value = float("nan")
                    h.refresh()
                with self.assertRaises(CommandError):
                    h.configure()
                self.assertEqual(h.saved.writes, 0)

    def test_editing_warm_idle_threshold_keeps_run_on_with_warm_outlets(self):
        h = Harness()
        h.heaters["heater_box1"].air = 36.
        h.advance(1)
        h.configure(threshold=40.)
        self.assertEqual(h.reason(), "run_on")
        h.advance(59)
        self.assertEqual(h.fans["1a"].fan.speed, 1.)
        h.advance(2)
        self.assertEqual(h.reason(), "off")
        h.configure(threshold=35.)
        self.assertEqual(h.reason(), "temperature")
        h.sensors["1b"].value = 60.
        h.refresh()
        h.configure(threshold=40.)
        self.assertEqual(h.reason(), "run_on")
        self.assertEqual(h.saved.writes, 3)

    def test_settings_reject_invalid_values_and_do_not_adopt_failed_write(self):
        for threshold, hysteresis in [(34., .5), (46., .5), (35., 0), (35., 3), (float("nan"), .5), (35., float("inf"))]:
            h = Harness()
            with self.assertRaises(CommandError):
                h.configure(threshold=threshold, hysteresis=hysteresis)
            self.assertEqual(h.saved.writes, 0)
        h = Harness()
        h.saved.fail = True
        with self.assertRaises(CommandError):
            h.configure()
        self.assertEqual(h.manager.get_status(0)["boxes"]["1"]["threshold"], 35.)

    def test_duplicate_or_unstable_id_cannot_share_a_threshold(self):
        with self.assertRaises(CommandError):
            Harness({
                "1": fake_serial(),
                "2": fake_serial(version="1.3.0"),
            })
        h = Harness({"1": "/dev/ttyUSB0"})
        with self.assertRaises(CommandError):
            h.configure()
        self.assertFalse(h.manager.get_status(0)["boxes"]["1"]["configurable"])


if __name__ == "__main__":
    unittest.main()
