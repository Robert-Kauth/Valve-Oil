"""Stand-ins for QIDI's compiled modules (multi_color_controller.so, box_extras.so, box_stepper.so, box2base.so,
box485, ai_func_manager).

Each behaviour carries its evidence: what was run or captured, on which firmware, and when. Where that evidence
still lives in openspec/observations/, the stand-in cites it there; a stand-in that is the only record says so in
full. Harness runs mean QIDI's compiled module run against fake Klipper objects; runtime means captured on a printer.
A command or step QIDI has that no flow here has needed yet, or whose behaviour isn't recorded, fails the flow with
"has no stand-in", so a test never passes by skipping vendor code it reached.

Builds: Max 4 01.01.07.01 box_extras.so sha256 0878b5b9..., multi_color_controller.so b7e3bc21... (also the Plus 5
01.01.02.01's), box2base.so d1cd52e4...; the Smart 5's multi_color_controller.so caee552f....
"""
from __future__ import annotations

import configparser
from dataclasses import dataclass, field
from pathlib import Path

from installer.tests.gcode_flow.hardware import HarnessGap
from installer.tests.klipper_fakes import BoxAutoFeed

EMPTY_SLOT = "slot16"
# Box flows QIDI's TaskQueueManager.FLOW_MAP holds on 01.01.07.01 (harness, 2026-10-28, read from the module).
FLOW_MAP = {
    0: [],
    1: ["BOX_HEAT", "BOX_LOAD", "BOX_WIPE"],
    2: ["BOX_HEAT", "BOX_CUT", "BOX_UNLOAD", "BOX_LOAD", "BOX_WIPE"],
    3: ["BOX_HEAT", "BOX_CUT", "BOX_UNLOAD"],
    4: ["BOX_EJECT"],
    5: ["BOX_HEAT", "BOX_CUT", "BOX_UNLOAD", "BOX_EJECT"],
    6: ["EXT_HEAT", "EXT_BITE", "EXT_LOAD", "EXT_WIPE"],
    7: ["EXT_HEAT", "EXT_CUT", "EXT_UNLOAD"],
    8: ["EXT_HEAT", "EXT_CUT", "EXT_UNLOAD", "BOX_HEAT", "BOX_LOAD", "BOX_WIPE"],
    9: ["BOX_HEAT", "BOX_CUT", "BOX_UNLOAD", "EXT_HEAT", "EXT_BITE", "EXT_LOAD", "EXT_WIPE"],
    10: ["BOX_HEAT", "BOX_UNLOAD", "BOX_LOAD", "BOX_WIPE"],
    11: ["BOX_HEAT", "BOX_UNLOAD", "BOX_EJECT"],
    12: ["BOX_HEAT", "BOX_UNLOAD", "EXT_HEAT", "EXT_BITE", "EXT_LOAD", "EXT_WIPE"],
    13: ["BOX_CUT", "BOX_UNLOAD", "BOX_LOAD"],
}
# On 01.01.07.01 QIDI's compiled Box modules register, by gcode.commands on a two-Box Max 4 (runtime, 2026-10-19):
# T_MANAGER, EXTRUDER_LOAD, EXTRUDER_UNLOAD, SLOT_UNLOAD, UNLOAD_FILAMENT, UNLOAD_FILAMENT_CONTROL,
# LOAD_FILAMENT_CONTROL, EJECT_FILAMENT_CONTROL, TRY_RESUME_PRINT, INIT_RFID_READ, CLEAR_FLUSH, CLEAR_OOZE,
# BOX_PRINT_START, TOOL_CHANGE_START, TOOL_CHANGE_END, AUTO_RELOAD_FILAMENT, DISABLE_BOX_HEATER, FLOW_RETRY,
# FLOW_STOP, INTERNAL_BOX_UNLOAD, and QUERY_CURRENT_BOX_CHANNEL_FILAMENT, and not CUT_FILAMENT or UNLOAD_T<n>,
# which the slicer packs still send and Klipper drops. Those whose behaviour isn't recorded:
UNMODELLED_COMMANDS = (
    "EXTRUDER_LOAD", "EXTRUDER_UNLOAD", "SLOT_UNLOAD", "UNLOAD_FILAMENT", "CLEAR_FLUSH",
    "CLEAR_OOZE", "AUTO_RELOAD_FILAMENT", "INTERNAL_BOX_UNLOAD", "QUERY_CURRENT_BOX_CHANNEL_FILAMENT", "READ_RFID",
    "UPDATE_FILAMENT_INFORMATION",
)
# What QIDI's print_sensor_state_to_log says for each code; it sends `!! <message>` (harness, 2026-10-31).
CODES = {
    "QDE_004_005": "",
    "QDE_004_006": "Extruder loading failure.",
    "QDE_004_008": "Extruder unloading failure.",
    "QDE_004_017": "",
    "QDE_004_027": "",
}
UNKNOWN_FILAMENT_TOP = 240.


@dataclass
class BoxSetup:
    """The Box side of a printer: how many USB Boxes box.cfg lists, Use QIDI Box, which slot each tool maps to, which
    slot QIDI has as loaded, the tool the last change went to, and where filament physically is."""

    boxes: int = 0
    enable_box: int = 0
    tools: dict = field(default_factory=dict)
    loaded_slot: str = EMPTY_SLOT
    current_tool: int = -1
    # The slot whose filament is at the Box hub; by default the loaded slot's.
    hub: str | None = "loaded"
    # Slots with filament at their inlet; by default every slot a tool maps to and the loaded slot.
    inlets: frozenset | None = None
    # Filament held at the toolhead, so pulling back never clears the toolhead sensor (issue #348).
    caught: bool = False
    # An error a Box motor raises during an unload, such as a homing timeout.
    unload_error: str | None = None
    auto_reload_detect: int = 0
    # Boxes (from 0) QIDI's timed drying is running on.
    drying: frozenset = frozenset()

    def write_config(self, config_root: Path):
        # The touchscreen app writes "# No Box Config" with no Box, and otherwise a [box_config box<i>] per Box,
        # then [box_extras] and [box_autofeed], then [mcu mcu_box<n>] per Box (topology-and-control.md, Layout).
        if not self.boxes:
            text = "# No Box Config\n"
        else:
            text = "".join(f"[box_config box{i}]\n\n" for i in range(self.boxes))
            text += "[box_extras]\n\n[box_autofeed]\n\n"
            text += "".join(f"[mcu mcu_box{i + 1}]\n\n" for i in range(self.boxes))
        (config_root / "box.cfg").write_text(text, encoding="utf-8")

    def hub_slot(self):
        if self.hub == "loaded":
            return None if self.loaded_slot == EMPTY_SLOT else self.loaded_slot
        return self.hub

    def inlet_slots(self):
        if self.inlets is not None:
            return set(self.inlets)
        slots = set(self.tools.values())
        if self.loaded_slot != EMPTY_SLOT:
            slots.add(self.loaded_slot)
        return slots


def write_saved_variables(path: Path, box: BoxSetup, extra: dict):
    parser = configparser.ConfigParser()
    parser.read(path)
    if not parser.has_section("Variables"):
        parser.add_section("Variables")
    values = {"box_count": box.boxes, "enable_box": box.enable_box, "last_load_slot": box.loaded_slot,
              "extrude_state": box.current_tool, "slot_sync": "slot-1",
              "auto_reload_detect": box.auto_reload_detect}
    for tool in range(16):
        values[f"value_t{tool}"] = box.tools.get(tool, "")
    values.update(extra)
    for name, value in values.items():
        parser.set("Variables", name, repr(value))
    with open(path, "w", encoding="utf-8") as handle:
        parser.write(handle)


def _slot_number(slot):
    return int(slot[4:]) if isinstance(slot, str) and slot.startswith("slot") and slot[4:].isdigit() else None


class Task:
    """multi_color_controller's FlowTask: the flow, its steps left, the step running or failed, and whether it's
    paused on that step."""

    def __init__(self, flow_id, steps):
        self.flow_id = flow_id
        self.queue = list(steps)
        self.index = 0
        self.current_step = ""
        self.is_paused = False
        self.is_cancelled = False

    @property
    def current_queue(self):
        return self.queue[self.index:]

    def status(self):
        return {"flow_id": self.flow_id, "current_queue": list(self.current_queue),
                "current_step": self.current_step, "is_paused": self.is_paused}


class TaskQueueManager:
    """multi_color_controller's TaskQueueManager (harness, 2026-10-28 to 2026-11-06).

    - start_flow runs the flow's steps in order through method_map, the adapter's bound step methods, which take no
      arguments; a truthy result goes on to the next step.
    - A step that returns False responds `Step <step> failed. Pausing flow.` and leaves the task paused on that
      step; the command that started the flow returns then. Starting another flow while one is paused replaces it.
    - A step that raises propagates out of start_flow and retry, leaving the task on that step with is_paused false.
    - After the last step it runs the completion callback, then clears current_flow_temp and the task.
    - retry sends M109 S<current_flow_temp> with run_script_from_command (nothing without a temperature), then reruns
      the paused step, or skips it when can_next is set, and carries on.
    - stop clears the task, sends M104 S0 through its own gcode attribute with run_script(), which takes the G-code
      lock, when current_flow_temp is set, whatever the print state, and responds `Flow stop requested.`; with no
      flow it does nothing and says nothing. It runs no completion script.
    """

    FLOW_MAP = FLOW_MAP

    def __init__(self, gcode):
        self.gcode = gcode
        self.current_task = None
        self.current_flow_temp = None
        self.method_map = {}
        self.on_complete = None

    def clear_flow_parameters(self):
        self.current_flow_temp = None

    def start_flow(self, flow_id, on_complete=None):
        self.current_task = Task(flow_id, FLOW_MAP[flow_id])
        self.on_complete = on_complete
        return self._run()

    def retry(self, can_next=0):
        task = self.current_task
        if task is None or not task.is_paused:
            self.gcode.respond_info("No failed step to retry.")
            return False
        if self.current_flow_temp is not None:
            self.gcode.run_script_from_command("M109 S%s" % (self.current_flow_temp,))
        task.is_paused = False
        if can_next:
            task.index += 1
        return self._run()

    def _run(self):
        task = self.current_task
        while task.index < len(task.queue):
            step = task.queue[task.index]
            task.current_step = step
            if not self.method_map[step]():
                task.is_paused = True
                self.gcode.respond_info("Step %s failed. Pausing flow." % (step,))
                return False
            task.index += 1
        if self.on_complete is not None:
            self.on_complete(task)
        self.clear_flow_parameters()
        self.current_task = None
        return True

    def stop(self):
        if self.current_task is None:
            return
        self.current_task = None
        if self.current_flow_temp is not None:
            self.gcode.run_script("M104 S0")
        self.clear_flow_parameters()
        self.gcode.respond_info("Flow stop requested.")


class BoxExtras:
    """box_extras.so's BoxExtras: its status, the hub sensor, the print-time tool change and resume, and the parts
    Valve Oil's modules call."""

    def __init__(self, vendor):
        self.vendor = vendor

    @property
    def b_endstop_state(self):
        # The hub sensor: 0 with filament, 1 clear (harness, 2026-10-30).
        return 1 if self.vendor.hub_slot is None else 0

    def get_status(self, eventtime=None):
        variables = self.vendor.variables()
        # Each Box's dry_state is 1 while QIDI's timed drying runs on it (runtime, 2026-11-16 and 2026-11-23).
        boxes = [{"dry_state": 1 if number in self.vendor.setup.drying else 0}
                 for number in range(self.vendor.setup.boxes)]
        return {"last_load_slot": variables.get("last_load_slot", EMPTY_SLOT),
                "loaded_slot": variables.get("last_load_slot", EMPTY_SLOT),
                "is_tool_change": self.vendor.is_tool_change, "boxes": boxes, "buffer": {}, "auto_refill": 0}

    def _pause_print_retry_flow(self, manager, step):
        # Marks the task paused on that step and sends nothing (harness, 2026-11-03).
        if manager.current_task is not None:
            manager.current_task.is_paused = True

    def filament_printing_manager_box1(self, tool):
        """T_MANAGER's tool change on a USB Box, 01.01.07.01.

        Evidence: BoxExtras.filament_printing_manager_box1 and BoxAdapterV1 run against fake Klipper objects
        (harness, 2026-10-29; flow steps with the real TaskQueueManager 2026-11-03). On the test Max 4 (2026-10-19,
        two USB Boxes, stock QIDI Studio G-code and then Valve Oil 26.10.19.1's) each change between slots logged one
        extruder disable and the operator saw one cut, made by flow 13 (BOX_CUT, BOX_UNLOAD, BOX_LOAD) through
        CUT_FILAMENT_1, with Valve Oil's own cut skipped.

        Decisions, in order:
        - exclude_tool set, by TOOL_CHANGE_START for a change between tools on one slot: return at once.
        - the previous tool (saved extrude_state, -1 for none) mapped to the same slot: slot_sync(True) on the target
          slot, save extrude_state, and return, without reading last_load_slot or the toolhead sensor.
        - otherwise save extrude_state, then:
          - a target beyond box_count * 4, slot16, or last_load_slot missing or slot16: QDE_004_027 and PAUSE;
          - auto-reload: the target differs from last_load_slot, auto_reload_detect is 1, the loaded slot's inlet
            is empty, and the toolhead sensor detects filament, whatever the hub reads (harness, 2026-11-20, the same
            box_extras.so on all three printers): flush_all_filament() (QDE_004_017 and PAUSE if it fails), then
            loaded_stepper.loaded_stepper.switch_next_slot() raises AttributeError ('BoxExtruderStepper' object has
            no attribute 'loaded_stepper') before any flow step. On a Max 4 (operator klippy.log, 2026-11-20) the
            flush ran about 20 purge cycles at the waste chute over 11 minutes before that error. extrude_state is
            the tool saved above (harness, 2026-11-23): the "3" once recorded came from a T3 run;
          - last_load_slot is the target and the toolhead sensor detects filament: slot_sync(True) and return;
          - last_load_slot is the target with the toolhead empty: flow 13 without its cut;
          - any other target: flow 13: CUT_FILAMENT_1, MOVE_TO_TRASH, unload last_load_slot, load the target.
        The returns send no G-code, change no heater target, and write only extrude_state and slot_sync. Flow 13's steps run here, reported as flow 13 on the
        task manager: a step that returns False marks the task paused on it and the manager sends PAUSE; a step that
        raises leaves flow 13 on that step, not paused, and propagates out of T_MANAGER.
        """
        vendor = self.vendor
        variables = vendor.variables()
        if vendor.exclude_tool:
            return
        previous = variables.get("extrude_state", -1)
        target = variables.get(f"value_t{tool}") or f"slot{tool}"
        previous_slot = variables.get(f"value_t{previous}") or f"slot{previous}"
        if previous != -1 and previous_slot == target:
            vendor.stepper(target).slot_sync(True)
            vendor.save("extrude_state", tool)
            return
        vendor.save("extrude_state", tool)
        loaded = variables.get("last_load_slot", EMPTY_SLOT)
        number = _slot_number(target)
        if number is None or number >= variables.get("box_count", 0) * 4 or target == EMPTY_SLOT \
                or loaded in ("", EMPTY_SLOT):
            vendor.report("QDE_004_027")
            vendor.script("PAUSE")
            return
        detected = vendor.toolhead_detects()
        if target != loaded and variables.get("auto_reload_detect", 0) == 1 \
                and loaded not in vendor.inlets and detected:
            if not self.flush_all_filament():
                vendor.report("QDE_004_017")
                vendor.script("PAUSE")
                return
            raise AttributeError("'BoxExtruderStepper' object has no attribute 'loaded_stepper'")
        if loaded == target and detected:
            vendor.stepper(target).slot_sync(True)
            return
        manager = vendor.controller.task_queue_manager
        steps = ["BOX_UNLOAD", "BOX_LOAD"] if loaded == target else ["BOX_CUT", "BOX_UNLOAD", "BOX_LOAD"]
        manager.current_task = Task(13, steps)
        adapter = vendor.controller.box_adapter
        adapter.target_slot = target
        for step in steps:
            manager.current_task.current_step = step
            if not adapter.tool_change_step(step):
                self._pause_print_retry_flow(manager, step)
                vendor.script("PAUSE")
                return
            manager.current_task.index += 1
        manager.current_task = None

    def flush_all_filament(self):
        """BoxExtras.flush_all_filament, from T_MANAGER's auto-reload (harness, 2026-11-23): with no file printing it
        sends nothing and returns False; otherwise, while the toolhead sensor detects filament, up to 50 cycles of
        MOVE_TO_TRASH, M83, G1 E100 F300, G1 E-2 F1800, the part fan at full for 5 s, and DROP_TRASH; when the
        sensor clears, G1 E25 F300 and True; after 50 cycles, QDE_004_017 and False. Here the slot's tail passes the
        toolhead sensor in the first cycle."""
        vendor = self.vendor
        if vendor.printer.lookup_object("virtual_sdcard").current_file is None:
            return False
        for cycle in range(50):
            if not vendor.toolhead_detects():
                vendor.script("G1 E25 F300")
                return True
            vendor.script("MOVE_TO_TRASH\nM83\nG1 E100 F300\nG1 E-2 F1800\nM106 S255\nM400\nG4 P5000\nM106 S0\n"
                          "DROP_TRASH\nM400")
            vendor.printer.set_filament_detected(False)
            vendor.hub_slot = None
        return False

    def try_resume_print_box1(self):
        """QIDI's RESUME step with the Box enabled (harness, 2026-11-03 and 2026-11-23, box_extras.so 0878b5b9...;
        BoxAdapterV1.try_resume_print only calls it), with or without a paused flow, whose task it never reads:
        MOVE_TO_TRASH, then it reads extrude_state, the tool T_MANAGER saved (-2 or missing: `M118 Printer resume
        failed` and nothing else), and loads value_t<tool>, or slot<tool> without a mapping, from the sensors:
        - the target loaded, slot_sync naming it, and filament at the toolhead: slot_sync(True) and RESUME_PRINT,
          with no heating, unload, or load;
        - the hub clear: MOVE_TO_TRASH, M109 to RESUME_PRINT's saved_extruder_temp (250 without one), load;
        - filament at the hub and last_load_slot missing or slot16: "can not recognize the loaded filament" and no
          resume;
        - filament at the hub and the toolhead: CUT_FILAMENT_1 and MOVE_TO_TRASH, then as below;
        - filament at the hub: M109, unload last_load_slot, load the target.
        A load or unload that fails sends M104 S0 and `M118 Printer resume failed` and doesn't resume. Otherwise it
        clears the task manager's external flow and sends RESUME_PRINT. The cut runs before the M109, on the nozzle
        stock PAUSE turned off; an operator report (2026-11-03, console screenshot, Box setup and version not
        reported) of "Extrude below minimum temp" on every resume until the nozzle was heated by hand is unresolved,
        and that order accounts for it."""
        vendor = self.vendor
        variables = vendor.variables()
        vendor.script("MOVE_TO_TRASH")
        tool = variables.get("extrude_state", -2)
        if tool == -2:
            vendor.script("M118 Printer resume failed")
            return
        target = variables.get(f"value_t{tool}") or f"slot{tool}"
        loaded = variables.get("last_load_slot", EMPTY_SLOT) or EMPTY_SLOT
        temperature = vendor.printer.lookup_object("gcode_macro RESUME_PRINT").variables.get(
            "saved_extruder_temp", 250)
        detected = vendor.toolhead_detects()
        if loaded == target and variables.get("slot_sync") == target and detected:
            vendor.stepper(target).slot_sync(True)
        else:
            if vendor.hub_slot is None:
                vendor.script("MOVE_TO_TRASH\nM109 S%s" % (temperature,))
            elif loaded == EMPTY_SLOT:
                vendor.gcode.respond_info("can not recognize the loaded filament")
                return
            else:
                if detected:
                    vendor.script("CUT_FILAMENT_1\nMOVE_TO_TRASH")
                vendor.script("M109 S%s" % (temperature,))
                if not vendor.stepper(loaded).extruder_unload_single_func():
                    vendor.script("M104 S0\nM118 Printer resume failed")
                    return
            if not vendor.stepper(target).extruder_load_single_func():
                vendor.script("M104 S0\nM118 Printer resume failed")
                return
        vendor.controller.task_queue_manager.current_task = None
        vendor.script("RESUME_PRINT")


class BoxStepper:
    """box_stepper.so's BoxExtruderStepper for one slot: the unload, load, and slot_sync Valve Oil watches."""

    def __init__(self, vendor, slot, mcu):
        self.vendor = vendor
        self.slot = self.stepper_name = slot
        self.gcode = vendor.gcode
        self.extruder_stepper = _ExtruderStepper(vendor.printer, _SlotMotor(vendor.printer, slot, mcu))
        self.stepper = self.extruder_stepper.stepper
        self.enabled = False

    def sync_unbind_extruder(self):
        if self.extruder_stepper.synced:
            self.extruder_stepper.sync_to_extruder("")

    def disable_stepper(self):
        self.enabled = False

    def slot_sync(self, value, sync_to_extruder=False):
        """box_stepper.so's slot_sync (harness, 2026-11-23, box_stepper.so sha256 58ee8c61..., box_autofeed.so
        03a38b45..., Max 4 01.01.07.01). With True: unbind every slot from the extruder, then box_autofeed.auto_start
        (40, 40, 18, 1, slot), 40 mm/s, 40 mm/s^2, 18 mm feeds (hub_load_v, hub_load_a, and hub_load_length, absent
        from stock saved variables), with no abort first, and save slot_sync as the slot. No slot motor is geared to
        the extruder: the Box feeds the slot whenever the buffer asks. With sync_to_extruder too, it aborts the
        auto-feed first and gears the slot to the extruder. With False: unbind every slot and abort the auto-feed."""
        vendor = self.vendor
        autofeed = vendor.printer.lookup_object("box_autofeed")
        if not value or sync_to_extruder:
            autofeed.auto_abort()
        for stepper in vendor.steppers.values():
            stepper.sync_unbind_extruder()
        if not value:
            return
        if sync_to_extruder:
            self.extruder_stepper.sync_to_extruder("extruder")
        else:
            autofeed.auto_start(40, 40, 18, 1, self)
        vendor.save("slot_sync", self.slot)

    def extruder_unload_single_func(self):
        """Unload from the toolhead to the Box (box_stepper.so, harness 2026-10-31 and 2026-11-20, all three
        printers' builds). The slot is geared to the extruder for the pull-back, and every extruder move is G-code
        through its own gcode attribute, never forward: G1 E-30 F300, then G1 E-120 F600 (E-100 F800 on the Plus 5's
        first attempt), then while the toolhead sensor still detects filament up to seven G1 E-10 F300. The two
        lengths come from box_extras.get_value_by_key (exl_1, exl_2), which Valve Oil's vo_box_feed_motion answers
        only on the QIDI builds it checks, not here. An exception from the slot's gcode during the 10 mm pulls leaves
        the function at once. With filament still at the toolhead QIDI tries twice more with toolhead moves recorded
        only for the Max 4, then reports QDE_004_008 (Extruder unloading failure); those attempts have no stand-in.
        These moves pass Klipper's extruder check, which refuses them below min_extrude_temp. With the toolhead empty
        and filament at the hub it skips the extruder. Once the hub clears it saves last_load_slot slot16, retracts
        the slot 260 mm more, disables the slot motor, and sends MOVE_TO_TRASH."""
        vendor = self.vendor
        if vendor.toolhead_detects():
            self.enabled = True
            self.extruder_stepper.sync_to_extruder("extruder")
            self.gcode.run_script_from_command("M83\nG1 E-30 F300\nM400")
            if not vendor.caught:
                vendor.printer.set_filament_detected(False)
            second = "G1 E-100 F800" if vendor.printer.printer_id == "plus5" else "G1 E-120 F600"
            self.gcode.run_script_from_command(second + "\nM400")
            for _pull in range(7):
                if not vendor.toolhead_detects():
                    break
                self.gcode.run_script_from_command("G1 E-10 F300\nM400")
            if vendor.toolhead_detects():
                raise HarnessGap("QIDI's second and third unload attempts, with filament still at the toolhead "
                                 "after seven 10 mm pulls, have no stand-in")
            self.sync_unbind_extruder()
        if vendor.unload_error:
            raise vendor.printer.command_error(vendor.unload_error)
        vendor.hub_slot = None
        vendor.save("last_load_slot", EMPTY_SLOT)
        self.enabled = False
        self.gcode.run_script_from_command("MOVE_TO_TRASH")
        return True

    def extruder_load_single_func(self):
        """Load to the toolhead (box_stepper.so, harness 2026-10-31 and 2026-11-23): after waiting for moves, an empty
        slot inlet ends it with QDE_004_005 and False. Otherwise it saves the slot state 3, homes the slot to the hub
        (3000 mm at 85 mm/s), saves last_load_slot as the slot, homes to the toolhead sensor in three stages (550,
        380, and 240 mm at 85, 65, and 10 mm/s), saves the slot state 2, aborts the auto-feed, gears the slot to the
        extruder and saves slot_sync, then detect_filament_loaded turns the part fan off and extrudes M83, G1 E25
        F300, E5 F300, E10 F600, E5 F300, E20 F900, and up to three more E5/E20 pairs (140 mm at most) where the
        toolhead is, stopping once the load cell responds. Then it saves last_load_filament_id, unbinds the slot,
        arms its auto-feed (auto_start(40, 40, 18, 1, slot)), and saves slot_sync as the slot. A hub that never
        triggers, or another slot's filament at it, saves the slot state -2, disables the slot motor, and reports
        QDE_004_006; a toolhead sensor that never triggers runs retries at X30 Y30 and X390 Y30 first. Here the
        filament reaches the hub and the toolhead, and the load cell responds after the first 40 mm."""
        vendor = self.vendor
        self.gcode.run_script_from_command("M400")
        if self.slot not in vendor.inlets:
            vendor.report("QDE_004_005")
            return False
        vendor.save(self.slot, 3)
        if vendor.hub_slot not in (None, self.slot):
            vendor.save(self.slot, -2)
            vendor.report("QDE_004_006")
            return False
        vendor.hub_slot = self.slot
        vendor.save("last_load_slot", self.slot)
        vendor.printer.set_filament_detected(True)
        vendor.save(self.slot, 2)
        vendor.printer.lookup_object("box_autofeed").auto_abort()
        self.extruder_stepper.sync_to_extruder("extruder")
        vendor.save("slot_sync", self.slot)
        self.gcode.run_script_from_command("M106 S0\nM83\nG1 E25 F300\nG1 E5 F300\nG1 E10 F600\nM400")
        self.slot_sync(False)
        vendor.printer.lookup_object("box_autofeed").auto_start(40, 40, 18, 1, self)
        vendor.save("slot_sync", self.slot)
        return True

    def slot_unload_single_func(self):
        """The eject step's pull-back of the slot until its inlet clears (harness 2026-10-31); Box motion only."""
        self.vendor.inlets.discard(self.slot)
        return True


class _SlotMotor:
    """Klipper's MCU_stepper of a slot motor: resetting its step clock, as a homing end does, goes to its Box."""

    def __init__(self, printer, slot, mcu):
        self.printer, self.name, self.mcu = printer, slot, mcu

    def get_mcu(self):
        return self.mcu

    def get_mcu_position(self):
        return 0

    def get_step_dist(self):
        return 1.

    def note_homing_end(self):
        self.printer.lookup_object("box_autofeed").reset_step_clock(self.name)


class _ExtruderStepper:
    """QIDI's slot extruder_stepper: gears the slot motor to the extruder, or with '' to nothing."""

    def __init__(self, printer, stepper):
        self.printer, self.stepper = printer, stepper
        self.synced = None

    def sync_to_extruder(self, name):
        self.synced = name or None


class _BoxMcu:
    def __init__(self, name):
        self.name = name

    def get_name(self):
        return self.name


class BoxAdapterV1:
    """multi_color_controller.so's adapter for USB Boxes: the flow decisions and step methods (harness,
    2026-10-28 to 2026-11-06)."""

    def __init__(self, vendor, box1base):
        self.vendor = vendor
        self.box1base = box1base
        self.target_slot = None
        self.targer_eject_slot = None

    def init_steps_method(self, manager):
        manager.method_map.update({"BOX_HEAT": self.extruder_heating, "BOX_CUT": self.cut,
                                   "BOX_UNLOAD": self.unload_filament, "BOX_LOAD": self.load_filament,
                                   "BOX_EJECT": self.eject})
        manager.method_map["BOX_WIPE"] = self.wipe
        external = ExternalAdapter(self.vendor)
        manager.method_map.update({"EXT_HEAT": external.heat, "EXT_CUT": external.cut, "EXT_UNLOAD": external.unload})
        for step in ("EXT_BITE", "EXT_LOAD", "EXT_WIPE"):
            manager.method_map[step] = _no_stand_in(step)

    def flow_decision_func(self, action, slot):
        """Which flow a Load, Unload, or Eject of `slot` runs, from the toolhead sensor, the hub sensor, and
        last_load_slot (harness, 2026-10-30 and 2026-10-28). It also sets current_flow_temp: for UNLOAD the top of
        the slot filament's nozzle range (240 for an ID QIDI doesn't list), for EJECT the higher of the slot's
        filament and the last loaded filament; flows 0 and 4 leave it None."""
        vendor = self.vendor
        manager = vendor.controller.task_queue_manager
        manager.clear_flow_parameters()
        toolhead = vendor.toolhead_detects()
        hub = vendor.hub_slot is not None
        loaded = vendor.variables().get("last_load_slot", EMPTY_SLOT) or EMPTY_SLOT
        if action == "UNLOAD":
            if not toolhead:
                flow = 0
            elif loaded == EMPTY_SLOT:
                flow = 7
            else:
                flow = 3 if hub else -1
            self.target_slot = None
        elif action == "EJECT":
            if not hub:
                flow = 4
            elif loaded == EMPTY_SLOT:
                flow = 0
            elif loaded == slot:
                flow = 5 if toolhead else 11
            else:
                flow = 4 if toolhead else 11
            self.targer_eject_slot = slot
        else:
            if not toolhead:
                flow = 10 if hub else 1
            elif loaded == EMPTY_SLOT:
                flow = 8
            else:
                flow = 2 if hub else -1
            self.target_slot = slot
        if flow not in (0, 4, -1):
            manager.current_flow_temp = UNKNOWN_FILAMENT_TOP
        return flow

    def extruder_heating(self):
        # BOX_HEAT sends MOVE_TO_TRASH and M109 S<current_flow_temp>; with None it sends `M109 SNone`, which sets
        # the target to 0 without waiting.
        temperature = self.vendor.controller.task_queue_manager.current_flow_temp
        self.vendor.script("MOVE_TO_TRASH\nM109 S%s" % (temperature,))
        return True

    def cut(self):
        # BOX_CUT sends only CUT_FILAMENT_1 and waits for moves.
        self.vendor.script("CUT_FILAMENT_1\nM400")
        return True

    def unload_filament(self):
        """BOX_UNLOAD: MOVE_TO_TRASH; with the hub clear, True with nothing else; with filament at the hub and
        last_load_slot slot16 or missing, `无法识别到已进料耗材` and False; otherwise M109 to current_flow_temp (or
        RESUME_PRINT's saved temperature without one) and the unload of the target slot or last_load_slot."""
        vendor = self.vendor
        vendor.script("MOVE_TO_TRASH")
        if vendor.hub_slot is None:
            return True
        loaded = vendor.variables().get("last_load_slot", EMPTY_SLOT) or EMPTY_SLOT
        if loaded == EMPTY_SLOT:
            vendor.gcode.respond_info("无法识别到已进料耗材")
            return False
        temperature = vendor.controller.task_queue_manager.current_flow_temp
        if temperature is None:
            temperature = vendor.printer.lookup_object("gcode_macro RESUME_PRINT").variables["saved_extruder_temp"]
        vendor.script("M109 S%s" % (temperature,))
        return vendor.stepper(loaded).extruder_unload_single_func()

    def load_filament(self):
        """BOX_LOAD (harness, 2026-11-23): MOVE_TO_TRASH, then with the hub clear, M109 to current_flow_temp and the
        target's load. With filament at the hub, which no flow here reaches and so has no stand-in: last_load_slot
        slot16 or missing, `检测到有耗材通过HUB...` and False; beyond box_count * 4, `无法识别到已进料耗材` and False;
        any other slot: M109, CUT_FILAMENT_1 only with filament at the toolhead, unload last_load_slot
        (`退料失败，请手动退料` and False if it fails), then the load."""
        vendor = self.vendor
        vendor.script("MOVE_TO_TRASH")
        if vendor.hub_slot is not None:
            raise HarnessGap("QIDI's BOX_LOAD with filament at the hub has no stand-in")
        vendor.script("M109 S%s" % (vendor.controller.task_queue_manager.current_flow_temp,))
        return vendor.stepper(self.target_slot).extruder_load_single_func()

    def wipe(self):
        # BOX_WIPE (harness, 2026-11-23), whatever the hub reads: one script, then a wait for moves.
        self.vendor.script("MOVE_TO_TRASH\nG1 E100 F600\nG1 E-0.8 F1800\nM400\nM104 S0\nDROP_TRASH\nMOVE_TO_TRASH\nM400")
        return True

    def eject(self):
        # BOX_EJECT sends MOVE_TO_TRASH through its own gcode attribute, then pulls the slot back to its inlet.
        self.vendor.script("MOVE_TO_TRASH")
        return self.vendor.stepper(self.targer_eject_slot).slot_unload_single_func()

    def tool_change_step(self, step):
        # Flow 13's steps inside T_MANAGER: BOX_CUT then MOVE_TO_TRASH, the unload of last_load_slot, the load.
        if step == "BOX_CUT":
            self.cut()
            self.vendor.script("MOVE_TO_TRASH")
            return True
        if step == "BOX_UNLOAD":
            loaded = self.vendor.variables().get("last_load_slot", EMPTY_SLOT)
            return self.vendor.stepper(loaded).extruder_unload_single_func()
        return self.vendor.stepper(self.target_slot).extruder_load_single_func()


class ExternalAdapter:
    """multi_color_controller.so's external-spool steps (harness, 2026-11-23, the Max 4 and Plus 5 build b7e3bc21...;
    the Smart 5's caee552f... also sends G1 Y180 F10000 after WIPE_NOZZLE_DROP in EXT_WIPE and EXT_UNLOAD). It has no
    flow decision or resume of its own.

    The external-spool load's own steps (flow 6) have no stand-in, since no flow here runs them; recorded on that
    harness run:
    - EXT_BITE saves last_load_slot slot16 (SAVE_VARIABLE) unless it already is, and returns True with filament at
      the toolhead; without, it responds `No filament detected. Please insert filament and try again.`, returns
      False, and has a reactor callback send FLOW_RETRY, so the step repeats until filament comes.
    - EXT_LOAD sends G92 E0, G1 E80 F400, M400 and returns True.
    - EXT_WIPE sends MOVE_TO_TRASH, M109 S<flow temperature>, G92 E0, G1 E120 F300, M400, M106 S255, G92 E0,
      G1 E-4 F1000, M104 S140, TEMPERATURE_WAIT SENSOR=extruder MAXIMUM=<flow temperature - 30>, WIPE_NOZZLE_DROP,
      M400, M106 S0, M104 S0, M118 cleaning complete. It always returns False, so flow 6 ends paused at it after a
      complete wipe (runtime, Smart 5, 2026-11-16 and 2026-11-17: every load logged `Step EXT_WIPE failed. Pausing
      flow.`)."""

    def __init__(self, vendor):
        self.vendor = vendor

    def heat(self):
        temperature = self.vendor.controller.task_queue_manager.current_flow_temp
        self.vendor.script("DISABLE_ALL_SENSOR\nG28 O\nMOVE_TO_TRASH\nM109 S%s\nM109 S%s\nM118 Heat up complete"
                           % (temperature, temperature))
        return True

    def cut(self):
        self.vendor.script("CUT_FILAMENT_1\nMOVE_TO_TRASH\nG92 E0\nG1 E-60 F300")
        self.vendor.printer.set_filament_detected(False)
        return True

    def unload(self):
        after_wipe = "\nG1 Y180 F10000" if self.vendor.printer.printer_id == "smart5" else ""
        self.vendor.script("M104 S0\nWIPE_NOZZLE_DROP%s\nM106 S0\nM400\n_KM_BEEP_IF_AVAILABLE BEEPS=8\n"
                           "M118 Unload finish" % (after_wipe,))
        return True


class BoxAdapterV2:
    """multi_color_controller.so's RS-485 adapter, which it uses with no USB Box (runtime, project Plus 5, no Box,
    2026-11-10; harness, the 01.01.02.01 build with no Box objects). Its decisions and steps aren't recorded."""

    def __init__(self, vendor):
        self.vendor = vendor
        self.box1base = None

    def init_steps_method(self, manager):
        for step in set(step for steps in FLOW_MAP.values() for step in steps):
            manager.method_map[step] = _no_stand_in(step)

    def flow_decision_func(self, action, slot):
        raise HarnessGap("QIDI's RS-485 adapter's flow decisions have no stand-in")


class MultiColorController:
    """multi_color_controller.so's MultiColorController: its status, task queue, adapters, and commands.

    It registers INIT_RFID_READ in its constructor and sets save_vars and box_adapter, both None until then, in its
    klippy:ready handler, one of the last; INIT_RFID_READ before that raises AttributeError ('NoneType' object has
    no attribute 'allVariables'), or with only save_vars set, 'NoneType' object has no attribute 'box_count'; either
    is an internal error that shuts Klipper down (harness, 2026-11-11). Stock
    INIT_GLOBALS runs it 5 s into the ready callbacks, so a callback before the controller's that waits 5 s let it
    run first; a Max 4 start shut down that way (issue 265).

    Its status (runtime, two USB Boxes, 2026-10-26 and 2026-10-31) has box_count, enable_box, auto_reload_detect,
    load_retry_num, task (flow_id, current_queue, current_step, is_paused; flow_id None, [] and '' when idle), box
    (identity box_v1 for USB Boxes, box_count, loaded_slot, last_load_slot, is_tool_change, the hub sensor
    b_endstop_state, buffer and per-Box fields), extra (the external spool), operation.active_slot, and
    box_warning_info. With no USB Box it uses its RS-485 adapter and reports box_v2 (runtime, project Plus 5,
    2026-11-10). box_extras' own status has buffer, last_load_slot, loaded_slot, is_tool_change, and per-Box boxes,
    but no hub sensor; box485's has box_count and boxes; box2base publishes none.
    """

    def __init__(self, vendor):
        self.vendor = vendor
        self.task_queue_manager = TaskQueueManager(vendor.gcode)
        self.box_adapter = None
        self.save_vars = None
        self._initialized = False
        self.rfid_reads = 0

    def ready(self):
        self.save_vars = self.vendor.printer.lookup_object("save_variables")
        if self.vendor.setup.boxes:
            self.box_adapter = BoxAdapterV1(self.vendor, self.vendor.box_extras)
        else:
            self.box_adapter = BoxAdapterV2(self.vendor)
        self.box_adapter.init_steps_method(self.task_queue_manager)
        self._initialized = True

    def get_status(self, eventtime=None):
        variables = self.vendor.variables()
        identity = "box_v1" if self.vendor.setup.boxes else "box_v2"
        task = self.task_queue_manager.current_task
        idle = {"flow_id": None, "current_queue": [], "current_step": "", "is_paused": False}
        return {"box_count": variables.get("box_count", 0), "enable_box": variables.get("enable_box", 0),
                "auto_reload_detect": variables.get("auto_reload_detect", 0),
                "load_retry_num": variables.get("load_retry_num", 0),
                "task": task.status() if task is not None else idle,
                "box": {"identity": identity, "box_count": variables.get("box_count", 0),
                        "loaded_slot": variables.get("last_load_slot", EMPTY_SLOT),
                        "last_load_slot": variables.get("last_load_slot", EMPTY_SLOT),
                        "is_tool_change": self.vendor.is_tool_change,
                        "b_endstop_state": 1 if self.vendor.hub_slot is None else 0},
                # QIDI keeps operation.active_slot on a paused flow's target slot (harness, 2026-10-31); nothing
                # Valve Oil reads it, so it isn't modelled.
                "extra": {}, "operation": {"active_slot": ""}, "box_warning_info": []}

    def normal_complete_script(self, task):
        """The completion script of a Load, Unload, or Eject (harness, 2026-10-28): MOVE_TO_TRASH, M104 S0, and the
        extruder disabled, with G4 P5000 and WIPE_NOZZLE_DROP when current_flow_temp is set, including 0, then a
        wait for moves; `Flow completed successfully.`"""
        script = "MOVE_TO_TRASH\nM104 S0\nSET_STEPPER_ENABLE STEPPER=extruder enable=0"
        if self.task_queue_manager.current_flow_temp is not None:
            script += "\nG4 P5000\nWIPE_NOZZLE_DROP"
        self.vendor.script(script + "\nM400")
        self.vendor.gcode.respond_info("Flow completed successfully.")

    def cmd_INIT_RFID_READ(self, gcmd):
        if self.save_vars is None:
            raise AttributeError("'NoneType' object has no attribute 'allVariables'")
        self.rfid_reads += 1
        # The tag reads themselves, with a Box and auto_read_rfid on, take seconds per slot (issue 331); they move
        # nothing and change no state these flows read.

    def _flow_command(self, action, gcmd):
        slot = "slot%d" % (gcmd.get_int("SLOT"),)
        flow = self.box_adapter.flow_decision_func(action, slot)
        if flow == -1:
            raise HarnessGap(f"what QIDI does with flow -1 for {action} isn't recorded")
        self.task_queue_manager.start_flow(flow, self.normal_complete_script)

    def cmd_UNLOAD_FILAMENT_CONTROL(self, gcmd):
        self._flow_command("UNLOAD", gcmd)

    def cmd_EJECT_FILAMENT_CONTROL(self, gcmd):
        self._flow_command("EJECT", gcmd)

    def cmd_LOAD_FILAMENT_CONTROL(self, gcmd):
        self._flow_command("LOAD", gcmd)

    def cmd_FLOW_RETRY(self, gcmd):
        self.task_queue_manager.retry(gcmd.get_int("RETRY", 0))

    def cmd_FLOW_STOP(self, gcmd):
        # The multi_color_controller/flow_stop endpoint makes the same call; Valve Oil's code only sends FLOW_STOP.
        self.task_queue_manager.stop()

    def cmd_T_MANAGER(self, gcmd):
        # T_MANAGER returns without a flow when saved enable_box is not 1 (harness, 2026-10-29). On printers without
        # a Box, and in issue #397's klippy.log (Max 4, no Box, 2026-11-23), no Box activity was logged either.
        if self.vendor.variables().get("enable_box", 0) != 1:
            return
        if not isinstance(self.box_adapter, BoxAdapterV1):
            raise HarnessGap("QIDI's RS-485 tool change has no stand-in")
        self.vendor.box_extras.filament_printing_manager_box1(gcmd.get_int("T"))

    def cmd_BOX_PRINT_START(self, gcmd):
        """BOX_PRINT_START EXTRUDER=<tool> HOTENDTEMP=<temperature>, from print start (harness, 2026-11-17 and
        2026-11-23, multi_color_controller.so b7e3bc21... and box_extras.so 0878b5b9...).

        Every call saves load_retry_num 0 and retry_step None, sends CLEAR_TOOLCHANGE_STATE, saves runout_0 to
        runout_15 0, extrude_state as the tool (an earlier record of -1 was wrong), and BOX_DRYING_ROLL_ENABLE 0. With a USB Box and enable_box not 1
        it returns there. Otherwise BoxExtras.box_print_start resolves a missing value_t<tool> to slot<tool> and
        looks up `box_stepper slot<N>` with no default: a slot on no configured Box, slot16 included, raises
        "Unknown config object", an internal error that shuts Klipper down. With another Box slot loaded, QIDI cuts
        it first and looks the target up only then (box_extras.py line 1510 against 1532); here the lookup comes
        first, which differs only in a cut before that shutdown, and Valve Oil turns the Box off for such a tool. Three OrcaSlicer prints starting on T4
        with one Box shut down that way (operator klippy.log, 2026-11-17). Then MOVE_TO_TRASH and M109 to the
        temperature, and from the hub, last_load_slot, and the toolhead sensor:
        - the hub clear: load the target;
        - last_load_slot the target with filament at the toolhead: slot_sync(True) on it;
        - last_load_slot the target with the toolhead empty: load only;
        - another slot, filament at the toolhead: MOVE_TO_TRASH, CUT_FILAMENT_1, MOVE_TO_TRASH, unload, load;
        - another slot, toolhead empty: unload, load;
        - last_load_slot slot16: `can not recognize the loaded filament` and PAUSE.
        A failed unload or load sends PAUSE."""
        vendor = self.vendor
        tool = gcmd.get_int("EXTRUDER", 0)
        temperature = gcmd.get_float("HOTENDTEMP")
        vendor.save("load_retry_num", 0)
        vendor.script("SAVE_VARIABLE VARIABLE=retry_step VALUE=None")
        vendor.script("CLEAR_TOOLCHANGE_STATE")
        for slot in range(16):
            vendor.save("runout_%d" % (slot,), 0)
        vendor.save("extrude_state", tool)
        vendor.save("BOX_DRYING_ROLL_ENABLE", 0)
        variables = vendor.variables()
        if variables.get("enable_box", 0) != 1:
            return
        if not isinstance(self.box_adapter, BoxAdapterV1):
            raise HarnessGap("QIDI's RS-485 print start has no stand-in")
        target = variables.get(f"value_t{tool}") or f"slot{tool}"
        stepper = vendor.stepper(target)
        vendor.script("MOVE_TO_TRASH\nM109 S%s" % (max(0., temperature),))
        loaded = variables.get("last_load_slot", EMPTY_SLOT) or EMPTY_SLOT
        detected = vendor.toolhead_detects()
        if vendor.hub_slot is not None:
            if loaded == EMPTY_SLOT:
                vendor.gcode.respond_info("can not recognize the loaded filament")
                vendor.script("PAUSE")
                return
            if loaded == target:
                if detected:
                    stepper.slot_sync(True)
                    return
            else:
                if detected:
                    vendor.script("MOVE_TO_TRASH\nCUT_FILAMENT_1\nMOVE_TO_TRASH")
                if not vendor.stepper(loaded).extruder_unload_single_func():
                    vendor.script("PAUSE")
                    return
        if not stepper.extruder_load_single_func():
            vendor.script("PAUSE")

    def cmd_CLEAR_TOOLCHANGE_STATE(self, gcmd):
        # Saves is_tool_change; exclude_tool stays as it is (harness, 2026-11-23).
        self.vendor.is_tool_change = False

    def cmd_TRY_RESUME_PRINT(self, gcmd):
        """With enable_box 0, or an adapter whose box_count isn't above 0, it only reads the toolhead sensor:
        RESUME_PRINT with filament there, M118 Please load filament without (the print stays paused). It saves,
        moves, and heats nothing. Otherwise the adapter resumes. The RS-485 adapter, used with no USB Box, takes its
        box_count from box2base, 0 with no RS-485 device, so no-Box printers always take the sensor branch (harness,
        Max 4, Plus 5 and Smart 5 multi_color_controller.so, 2026-11-23)."""
        variables = self.vendor.variables()
        box_count = variables.get("box_count", 0) if isinstance(self.box_adapter, BoxAdapterV1) else 0
        if variables.get("enable_box", 0) == 0 or not box_count > 0:
            self.vendor.script("RESUME_PRINT" if self.vendor.toolhead_detects() else "M118 Please load filament")
            return
        if not isinstance(self.box_adapter, BoxAdapterV1):
            raise HarnessGap("QIDI's RS-485 Box resume (try_resume_print_box2) has no stand-in")
        self.vendor.box_extras.try_resume_print_box1()


class Vendor:
    """QIDI's compiled modules on one printer, with where the filament physically is."""

    def __init__(self, printer, config):
        self.printer = printer
        self.gcode = printer.lookup_object("gcode")
        self.setup = printer.box_setup
        self.is_tool_change = False
        self.exclude_tool = False
        self.tool_change = None
        self.codes: list[str] = []
        self.box_extras = None
        self.controller = None
        self.steppers: dict[str, BoxStepper] = {}
        self.autofeed = None
        self.hub_slot = self.setup.hub_slot()
        self.inlets = self.setup.inlet_slots()
        self.caught = self.setup.caught
        self.unload_error = self.setup.unload_error

    def variables(self):
        save_variables = self.printer.lookup_object("save_variables", None)
        return dict(getattr(save_variables, "allVariables", {}) or {})

    def toolhead_detects(self):
        sensor = self.printer.lookup_object("filament_switch_sensor filament_switch_sensor")
        return sensor.get_status(None)["filament_detected"]

    def stepper(self, slot):
        if slot not in self.steppers:
            # box_extras looks the slot's stepper up with no default: a slot on no configured Box raises
            # "Unknown config object" (harness, 2026-11-17), an internal error that shuts Klipper down.
            self.printer.lookup_object("box_stepper %s" % (slot,))
        return self.steppers[slot]

    def save(self, name, value):
        text = '\'"%s"\'' % (value,) if isinstance(value, str) else repr(value)
        self.gcode.run_script_from_command("SAVE_VARIABLE VARIABLE=%s VALUE=%s" % (name, text))

    def script(self, script):
        self.gcode.run_script_from_command(script)

    def report(self, code):
        self.codes.append(code)
        message = CODES.get(code, "")
        self.gcode.respond_raw("!! Code:%s; Message:%s" % (code, message) if message else "!! Code:%s" % (code,))

    def load(self, config, section) -> bool:
        module = section.split()[0]
        printer = self.printer
        gcode = self.gcode
        if module == "multi_color_controller":
            self.controller = controller = MultiColorController(self)
            printer.add_object(section, controller)
            for command in UNMODELLED_COMMANDS:
                gcode.register_command(command, _unmodelled(command))
            for command in ("T_MANAGER", "TRY_RESUME_PRINT", "UNLOAD_FILAMENT_CONTROL", "EJECT_FILAMENT_CONTROL",
                            "LOAD_FILAMENT_CONTROL", "FLOW_RETRY", "FLOW_STOP", "INIT_RFID_READ", "BOX_PRINT_START",
                            "CLEAR_TOOLCHANGE_STATE"):
                gcode.register_command(command, getattr(controller, "cmd_" + command))
            gcode.register_command("TOOL_CHANGE_START", self.cmd_TOOL_CHANGE_START)
            gcode.register_command("TOOL_CHANGE_END", self.cmd_TOOL_CHANGE_END)
            gcode.register_command("DISABLE_BOX_HEATER", lambda gcmd: None)
            if printer.slow_ready:
                reactor = printer.get_reactor()
                printer.register_event_handler(
                    "klippy:ready", lambda: reactor.pause(reactor.monotonic() + printer.slow_ready))
            printer.register_event_handler("klippy:ready", controller.ready)
        elif module == "box_extras":
            self.box_extras = BoxExtras(self)
            printer.add_object(section, self.box_extras)
            gcode.register_command("INIT_BOX_STATE", self.cmd_INIT_BOX_STATE)
        elif module == "box_config":
            index = int(section.split()[-1][3:])
            mcu = _BoxMcu("mcu_box%d" % (index + 1,))
            for number in range(index * 4, index * 4 + 4):
                slot = "slot%d" % (number,)
                self.steppers[slot] = BoxStepper(self, slot, mcu)
                printer.add_object("box_stepper %s" % (slot,), self.steppers[slot])
            printer.add_object(section, _Status({}))
        elif module == "box2base":
            printer.add_object(section, object())
        elif module == "box485":
            printer.add_object(section, _Status({"box_count": 0, "boxes": {}}))
        elif module == "box_autofeed":
            # QIDI's box_autofeed with each Box's auto-feed as Box firmware 02.03.01.21 runs it (klipper_fakes).
            self.autofeed = BoxAutoFeed(printer.get_reactor(), self.steppers)
            printer.add_object(section, self.autofeed)
            gcode.register_command("SET_LIMIT_A", self.autofeed.cmd_SET_LIMIT_A)
        elif module == "ai_func_manager":
            printer.add_object(section, _AiFuncManager())
        else:
            return False
        return True

    def cmd_INIT_BOX_STATE(self, gcmd):
        """INIT_BOX_STATE, from stock INIT_GLOBALS 5 s after Klipper is ready with a Box (harness, 2026-11-23,
        box_extras.so 0878b5b9...). It reads saved slot_sync: 'slot-1' or missing, it returns; None, a number, or a
        list raises AttributeError, which shuts Klipper down 5 s after each start (harness, 2026-11-22). With the
        hub clear, or the hub occupied and the toolhead empty, it saves 'slot-1'. With both occupied: last_load_slot
        a configured slot runs slot_sync(True) on the slot slot_sync names, even when it differs from last_load_slot;
        last_load_slot slot16 saves nothing; beyond box_count * 4 saves 'slot-1'. It never changes last_load_slot."""
        variables = self.variables()
        synced = variables.get("slot_sync", "slot-1")
        if synced == "slot-1":
            return
        if not isinstance(synced, str):
            raise AttributeError("'%s' object has no attribute 'replace'" % (type(synced).__name__,))
        if self.hub_slot is None or not self.toolhead_detects():
            self.save("slot_sync", "slot-1")
            return
        loaded = variables.get("last_load_slot", EMPTY_SLOT) or EMPTY_SLOT
        if loaded == EMPTY_SLOT:
            return
        number = _slot_number(loaded)
        if number is None or number >= variables.get("box_count", 0) * 4:
            self.save("slot_sync", "slot-1")
            return
        self.stepper(synced).slot_sync(True)

    def cmd_TOOL_CHANGE_START(self, gcmd):
        """TOOL_CHANGE_START F=<from> T=<to>, which both slicer packs send before a filament change (harness,
        2026-11-23, box_extras.so 0878b5b9... and multi_color_controller.so b7e3bc21..., the Max 4 01.01.07.01's).

        With enable_box 0 the controller does nothing. Otherwise box_extras' ToolChange.tool_change_start: when
        value_t<F> and value_t<T> (missing ones read as slot<n>) name the same slot and its transform isn't already
        installed, it sends MOVE_TO_TRASH, sets exclude_tool, installs itself as the G-code move transform, whose
        move() forwards nothing, so every G1 of the change, extrusion included, is dropped, and turns heater waits
        off, which only SET_HEATER_TEMPERATURE's WAIT reads (QIDI's heaters.py). T_MANAGER and box_stepper's
        EXTRUDER_LOAD and EXTRUDER_UNLOAD return at once while exclude_tool is set; CLEAR_TOOLCHANGE_STATE doesn't
        clear it. Tool changes save is_tool_change at start and end (harness, 2026-11-03). The touchscreen and
        Fluidd write value_t, so their mappings decide when two tools share a slot."""
        if self.variables().get("enable_box", 0) != 1:
            return
        self.is_tool_change = True
        variables = self.variables()
        start = gcmd.get_int("F", -1)
        end = gcmd.get_int("T", -1)
        same = (variables.get(f"value_t{start}") or f"slot{start}") == (variables.get(f"value_t{end}") or f"slot{end}")
        if same and self.tool_change is None:
            self.script("MOVE_TO_TRASH")
            self.exclude_tool = True
            self.tool_change = _ToolChange(self.printer)

    def cmd_TOOL_CHANGE_END(self, gcmd):
        """ToolChange.tool_change_end: when exclude_tool is set, puts the previous move transform back, resets
        gcode_move's position to the toolhead's, clears exclude_tool, and turns heater waits back on."""
        if self.variables().get("enable_box", 0) != 1:
            return
        self.is_tool_change = False
        if self.exclude_tool:
            self.tool_change.remove()
            self.tool_change = None
            self.exclude_tool = False


class _ToolChange:
    """box_extras' ToolChange as the G-code move transform: drops every move."""

    def __init__(self, printer):
        self.gcode_move = printer.lookup_object("gcode_move")
        self.next_transform = self.gcode_move.set_move_transform(self, force=True)

    def move(self, newpos, speed):
        pass

    def get_position(self):
        return self.next_transform.get_position()

    def remove(self):
        self.gcode_move.set_move_transform(self.next_transform, force=True)
        self.gcode_move.reset_last_position()


class _Status:
    def __init__(self, status):
        self.status = status

    def get_status(self, eventtime=None):
        return dict(self.status)


class _AiFuncManager:
    def m4050(self):
        pass


def _no_stand_in(step):
    def run():
        raise HarnessGap(f"QIDI's {step} step has no stand-in")
    return run


def _unmodelled(command):
    def cmd(gcmd):
        raise HarnessGap(f"QIDI's {command} has no stand-in yet ({gcmd.get_commandline()})")
    return cmd
