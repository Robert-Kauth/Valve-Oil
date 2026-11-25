# QIDI Box circulation hysteresis for Valve Oil.
#
# QIDI's box_heater_fan runs each USB Box's two circulation fans while the Box
# air (heater_boxN) reads above the fan's heater_temp, 35 C in box.cfg, while
# the Box heats or a slot motor is on, and for 60 s after. It has no
# hysteresis, so a Box held near 35 C, as on top of a printer with a hot
# chamber, switches its fans on and off over and over.
#
# Each second this module sets each fan's heater_temp, which QIDI's fan reads
# each second: the threshold until the air goes above it, then the threshold
# less the hysteresis until the air falls to that. QIDI's fan still decides
# everything else: heating, motors, the run-on and the fan output.
#
# The saved variables vo_box_fan_threshold (35 to 45 C, box.cfg's value when
# unset) and vo_box_fan_hysteresis (0 to 2 C, 0.5 when unset) apply to every
# USB Box at the next check. A value outside its range is ignored and logged.

import logging
import re

THRESHOLD_VARIABLE = "vo_box_fan_threshold"
HYSTERESIS_VARIABLE = "vo_box_fan_hysteresis"
THRESHOLD_RANGE = (35., 45.)
HYSTERESIS_RANGE = (0., 2.)
DEFAULT_HYSTERESIS = .5
BOX_FAN = re.compile(r"box_heater_fan heater_fan_[ab]_box\d+$")
CHECK_TIME = 1.


class BoxCirculation:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.fans = []
        self.ignored = {}
        self.printer.register_event_handler("klippy:ready", self._ready)

    def _ready(self):
        for name, fan in self.printer.lookup_objects("box_heater_fan"):
            if BOX_FAN.match(name) and getattr(fan, "heaters", None) \
                    and isinstance(getattr(fan, "heater_temp", None), float):
                # [fan, box.cfg's threshold, whether the air went above it]
                self.fans.append([fan, fan.heater_temp, False])
        if self.fans:
            reactor = self.printer.get_reactor()
            reactor.register_timer(self._update, reactor.NOW)

    def _saved(self, variables, name, low, high):
        if name not in variables:
            return None
        value = variables[name]
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = None
        if isinstance(value, bool) or number is None or not low <= number <= high:
            if self.ignored.get(name) != repr(value):
                self.ignored[name] = repr(value)
                logging.warning("vo_box_circulation: %s=%r is not between %g"
                                " and %g; ignored", name, value, low, high)
            return None
        self.ignored.pop(name, None)
        return number

    def _update(self, eventtime):
        variables = getattr(self.printer.lookup_object("save_variables", None),
                            "allVariables", None) or {}
        threshold = self._saved(variables, THRESHOLD_VARIABLE, *THRESHOLD_RANGE)
        hysteresis = self._saved(variables, HYSTERESIS_VARIABLE,
                                 *HYSTERESIS_RANGE)
        if hysteresis is None:
            hysteresis = DEFAULT_HYSTERESIS
        for entry in self.fans:
            fan, stock_threshold, warm = entry
            start = stock_threshold if threshold is None else threshold
            air = max(heater.get_temp(eventtime)[0] for heater in fan.heaters)
            if air > start:
                warm = True
            elif air <= start - hysteresis:
                warm = False
            entry[2] = warm
            fan.heater_temp = start - hysteresis if warm else start
        return eventtime + CHECK_TIME


def load_config(config):
    return BoxCirculation(config)
