# QIDI Box state and command observations

Evidence qualifiers are defined in `openspec/observations/README.md`. Runtime captures apply to QIDI Max 4 firmware `01.01.06.04`; `01.01.06.05` findings are explicitly config-confirmed, harness-recovered, or static-recovered.

## Saved-variable model

| Variable family | Meaning |
|---|---|
| `enable_box`, `box_count` | Whether prints use the Box, set per print by whatever starts it (see below), and the detected Box count. |
| `value_t0` through `value_t15` | Logical tool-to-slot mapping. Missing mappings defaulted to `slot16` in `BOX_PRINT_START` recovered before `.07.01`; on `.07.01` they resolve to `slot<tool>` (see `BOX_PRINT_START`). |
| `slot0` through `slot15` | Physical-slot `BoxState` values. |
| `last_load_slot` | Last loaded slot according to vendor state. |
| `slot_sync` | Slot currently trusted/bound to the extruder. |
| `extrude_state` | The tool the last `T_MANAGER` or `BOX_PRINT_START` was for; `TRY_RESUME_PRINT` loads its `value_t` (harness, 2026-11-23). `.07.02` no longer saves it after a runout auto-reload (see [`01.01.07.02`](#firmware-01010702-compiled-module-differences)). |
| `filament_slotN`, `color_slotN`, `vendor_slotN` | IDs resolved through `officiall_filas_list.cfg`; slot metadata extends through `slot16`. |
| `runout_0` through `runout_15` | Per-slot runout counters. |
| `load_retry_num`, `retry_step` | Vendor retry state. |
| `retained_slot`, `retained_tool`, `retained_tool_ready`, `retained_filament_id`, `retained_vendor_id` | Repository-managed between-print reuse state. |
| `is_tool_change`, `was_interrupted` | Vendor operation context. |

`slot16` is direct-feed/external-spool metadata and is not a physical slot state. `slot-1` is the observed unsynced/no-active-target sentinel.

### Who writes `enable_box`

- **Static-recovered (Max 4 `qidiclient` SHA-256 `b5285d1a…`, `01.01.07.01`):** the app's only `enable_box` writer (`0x6785d0`, `SAVE_VARIABLE VARIABLE=enable_box VALUE=`) is called only from the Print button of its filament-matching page (`match_filament`, `access/ams/`; handler `0x4d3484`, logging `[AMS] 点击打印按钮，开始打印流程`, "print button tapped, starting the print flow"). When a state check on the app's printer data passes (an int at offset `0x40` above 2 and a flag at `0x98` clear; what they hold is **Unresolved**), it saves `enable_box` 1, then for each of the file's filaments saves `value_t<tool>` to its chosen slot. A filament mapped to slot 16, the external spool, saves `enable_box` 0 instead, or, when the external spool has no filament detected, shows `prepare_to_print_warning` and stops the print (`[AMS] 外置耗材未检测到，弹出警告并中止打印`, "external filament not detected, show a warning and stop the print"). Box detection writes `box.cfg` and `box_count` (see `topology-and-control.md`) and never `enable_box`.
- **Config-confirmed (Max 4 `01.01.07.01` Moonraker `components/klippy_apis.py`):** `/printer/print/start` with a `fileurl` (a cloud print) saves `enable_box` as `int(bool(consumable))` from the request's `consumable` list, and `value_t<index>` as `slot<n>` for each entry that isn't `-1`, before it downloads and starts the file. Without a `fileurl` it saves neither.
- **Config-confirmed:** QIDI's `save_variables.py` defaults `enable_box` to 0, and stock macros read it in print start (`BOX_PRINT_START`), `T0`–`T15`, runout reload, and pause/resume. A print started without any of these writers, such as an upload from Fluidd or OrcaSlicer, runs with the value the last print left.
- **Static-recovered (same app):** the print preview page's polar cooler checkbox (`draw_page_preview.cpp`, `Checkbox polar_cool 新状态`) saves `enable_polar_cooler` (`0x678734`), so the choice persists across prints.

## State values

| State | Value |
|---|---:|
| `ERROR` | `-1` |
| `UNKNOWN` | `-2` |
| `PENDING` | `-3` |
| `EMPTY` | `0` |
| `LOADED` | `1` |
| `IN_EXTRUDER` | `2` |
| `IN_FEEDER` | `3` |

Runtime alignment on the one-Box capture:

```text
last_load_slot = slot2
slot_sync = slot2
extrude_state = 2
slot2 = 2
```

`multi_color_controller.slots.states.slot2=2`, `extruder.loaded=true`, `extruder.filament_detected=true`, and `BoxState.IN_EXTRUDER=2` confirm the loaded-to-extruder interpretation.

Loaded `slot2` reported `runout_button=0`; empty slots reported `1`. Box slot runout/pre-gate polarity is inverted relative to an intuitive `filament present = 1` assumption.

## Moonraker status schema

Visible object names include:

```text
box_extras
multi_color_controller
save_variables
box_stepper slotN
heater_generic heater_boxN
temperature_sensor heater_temp_a_boxN
temperature_sensor heater_temp_b_boxN
mcu mcu_boxN
```

`box_autofeed` returned `{}` when queried but was absent from object lists. Generated `box_rfid card_reader_N` objects were not Moonraker-visible in captures.

### `box_extras`

```text
box_button_state
b_endstop_state
e_endstop_state
box_operate_state
box_drying_state.boxN.{dry_state,end_time}
is_tool_change
```

On `.07.02` the saved `box_drying_state.boxN` also holds `end_timestamp`, and `boxes[N].end_time` counts down (see [`01.01.07.02`](#firmware-01010702-compiled-module-differences)).

### `box_stepper slotN`

```text
runout_button
rfid_state
```

### `multi_color_controller`

| Section | Captured shape |
|---|---|
| `system` | `ready`, `mode` |
| `hardware` | `box_count`, `connected` |
| `slots` | `states.slot0..slot15`, `materials.slot0..slot16`, `last_loaded`; each material resolves `filament.{filament,min_temp,max_temp,box_min_temp,box_max_temp,type}`, `color`, and `vendor`. |
| `extruder` | `loaded`, hotend `target`, `filament_detected` |
| `operation` | `current`, `progress`, `error`, button/operate state, `steps`, `is_waiting_user` |
| `print` | `printing`, `current_tool`, `next_tool` |
| `rfid` | `reading`, `results` |
| `drying` | `boxN.dry_state`, `end_time` |
| `sensors` | `b_endstop`, `e_endstop`, `runout_sensors.slotN`, `pressure_sensor` |
| `config` | Saved Box, mapping, metadata, runout, retry, retained-filament, and interruption variables. |
| `config_summary` | `enable_box`, `auto_reload_detect`, `auto_read_rfid`, `auto_init_detect`, `slot_sync`, `retry_step`, `load_retry_num` |

The `.07.01` `box.b_endstop_state` polarity and its discrepancy with `/printer/query_endstops/status` are documented in [hardware and protocol observations](hardware-and-protocols.md#slot-hardware).

`save_variables` should be consumed through its `variables` dictionary; duplicated top-level fields are a Moonraker representation detail.

`heater_generic heater_boxN.target` is authoritative for heater target. Captures showed `target=45` while both vendor drying-state surfaces reported `dry_state=0`.

Stock Fluidd cannot set a nonzero Box heater target on `01.01.07.01`:

- **Runtime-confirmed:** `configfile.settings` omits `heater_generic heater_boxN` because `box_config.py` builds generated sections with a private access-tracking map. Fluidd's target validation then compares against undefined minimum and maximum values, so only `0` is submitted. Sharing Klipper's tracking map for the generated heater published `min_temp -100`, `max_temp 100`, and `target_max_temp 90` without configuration warnings.
- **Runtime-confirmed:** QIDI's `SET_HEATER_TEMPERATURE` enforces `target_min_temp` and `target_max_temp` only for `heater_generic chamber`. `SET_HEATER_TEMPERATURE HEATER=heater_box2 TARGET=70` was accepted with `target_max_temp 65`; Box heater targets are bounded only by `max_temp`.
- **Config-confirmed:** Fluidd's `getMinMaxTempOfBox` limits Box `N` to the lowest `box_max_temp` among loaded filaments, but selects slots with `u/4===N-1`, so only the first slot of each Box is considered. `officiall_filas_list.cfg` and the `01.01.07.01` filament database give `box_max_temp` `0` for PLA, TPU, PEBA, and some other filaments, which Fluidd reports as `Max 0`.

The observed `mcu_box1` reported firmware `02.03.01.21`, MCU `stm32f401xc`, clock `84000000`, and both-edge stepping enabled.

## `BOX_PRINT_START`

`BOX_PRINT_START`'s setup writes and its decisions on `.07.01` are the `BOX_PRINT_START` stand-in in `installer/tests/gcode_flow/qidi.py`, run by the print start flow tests. Before `.07.01`, a missing `value_t<tool>` defaulted to `slot16`.

## Firmware `01.01.06.05` compiled-module differences

- **Harness-recovered:** `BoxExtras.detect_filament_loaded()` adds `M400` plus escalating `G1 E10 F600` and `G1 E20 F900` retries. Worst-case probing increases from `75 mm` on `.04` to `140 mm` on `.05`; physical sensor response and failure outcomes were not exercised.
- **Harness-recovered:** `BoxExtruderStepper.cmd_EXTRUDER_LOAD()` adds state paths named `diff_mv`, `box_autofeed`, `retry_success`, and `box_autofeed.limit_a_state`, with waste-area recovery extrusions of `30 mm` or `50 mm`. Exact retry thresholds and branch order remain unresolved.
- **Harness-recovered:** `BoxExtruderStepper.cmd_EXTRUDER_UNLOAD()` resolves `printer.lookup_object("virtual_sdcard", None)` into `v_sd` while printing. Downstream effects of the resolved object remain unresolved.
- **Harness-recovered:** `BoxExtruderStepper.switch_next_slot()` scans `value_t0` through `value_t15` rather than using `get_key_by_value(...)` for the next logical tool mapping. Physical auto-reload movement remains unresolved.
- **Harness-recovered:** `TaskQueueManager._decide_flow_id()` selects flow `6` (`EXT_HEAT`, `EXT_LOAD`, `EXT_BITE`, `EXT_WIPE`) for external-spool `LOAD` with slot `16`, filament present, and empty `last_load_slot`; `.04` selected flow `0` for the same controlled input.
- **Static-recovered:** public classes, methods, signatures, defaults, and recovered error-code sets are unchanged. `box_autofeed.so`, `box_detect.so`, and `box_rfid.so` have no confirmed source-level functional change beyond rebuilt binaries.

## Firmware `01.01.07.01` Box stack

- **Unresolved:** which ready callback waited 5 s in the Max 4 start that shut down on an early `INIT_RFID_READ` (issue 265); the race itself is the `MultiColorController` stand-in in `installer/tests/gcode_flow/qidi.py`.
- **Config-confirmed:** stock runout auto-reload checks only `enable_box`.
- **Config-confirmed:** stock `INIT_GLOBALS` runs `INIT_BOX_STATE` (when `box_count` is above 0 and `box_extras` exists) and then `INIT_RFID_READ` inside the same delayed G-code, 5 s after Klipper is ready, so Klipper's G-code queue is held until both finish. **Runtime-confirmed (operator logs, issue 331, Max 4, `01.01.07.01`, one USB Box, `auto_init_detect = 1`, `auto_read_rfid = 1`, spools loaded):** Moonraker saw Klipper ready at 17:38:14; klippy logged `ENABLE delay: stepper=slot0` at 17:38:16 and `1A did not recognize the filament` at 17:38:46. A `SAVE_VARIABLE` sent through Moonraker's `gcode/script` about 5 s after ready got no reply within 10 s, Moonraker still reported it pending at 60 s, and Klipper saved the value afterwards. With both settings at 0 the same install's writes completed. Which of the two commands held the queue, and for how long per slot or per Box, is **Unresolved**.
- **Runtime-confirmed (two USB Boxes, 2026-10-19):** the update regenerated `box.cfg` with `[box_config boxN]`, `[box_extras]`, `[box_autofeed]`, and `[mcu mcu_boxN]` only. `T{n}`, `UNLOAD_T{n}`, and `UNLOAD_FILAMENT` macros are no longer generated.
- **Runtime-confirmed (2026-10-26):** klippy logs `检查到！！错误，恢复默认状态！！` ("error detected, restoring default state") after a G-code command error and repeatedly while Klipper connects at startup; the observed effect was a print sub-status reset.
- **Static-recovered:** `box_extras.so` no longer has `CUT_FILAMENT`, `INIT_RFID_READ`, `RETRY`, `DISABLE_BOX_DRY`, `TRY_RESUME_PRINT`, `AUTO_RELOAD_FILAMENT`, `BOX_PRINT_START`, or `TOOL_CHANGE_START`/`END` handlers. `multi_color_controller.so` implements `BoxAdapterV1` (USB), `BoxAdapterV2` (RS-485), and `ExternalAdapter`, each with cut, unload, load, wipe, eject, print-start, tool-change, try-resume, and auto-reload operations driven by `FlowTask`. Its embedded scripts call stock `CUT_FILAMENT_1`, `MOVE_TO_TRASH`, `WIPE_NOZZLE_DROP`, and `DROP_TRASH`.
- **Runtime-confirmed (two USB Boxes, 2026-10-26):** `gcode/help` lists `LOAD_FILAMENT_CONTROL`, `UNLOAD_FILAMENT_CONTROL`, `EJECT_FILAMENT_CONTROL`, `READ_RFID`, and `UPDATE_FILAMENT_INFORMATION`, and omits `E_BOX`, `E_LOAD`, `E_UNLOAD`, and `RFID_READ`, which stock Fluidd's Box card sends. `box_extras.boxes[N].filament_info` mirrors the resolved per-slot filament fields.
- **Static-recovered:** qidiclient sends `EJECT_FILAMENT_CONTROL SLOT=`, `LOAD_FILAMENT_CONTROL SLOT=`, `UNLOAD_FILAMENT_CONTROL SLOT=`, and `READ_RFID SLOT=` with a numeric slot.
- **Runtime-confirmed:** QIDI's Fluidd `sw.js` precaches `assets/index-DlFvHGAh.js` with revision `null`, so a browser whose service worker already cached the bundle keeps serving it after the file changes on disk until `sw.js` changes.
- **Unresolved:** whether `UPDATE_FILAMENT_INFORMATION` has effects beyond the `SAVE_VARIABLE` slot updates, for example on RS-485 Boxes.
- **Static-recovered:** QIDI Studio `v2.07.02.73` still emits `CUT_FILAMENT T=`, `MOVE_TO_TRASH`, `UNLOAD_T[current_extruder]`, and `T[next_extruder]` for filament change and `UNLOAD_FILAMENT T=` at print end. On `.07.01` the first and third are ignored as unknown commands.
- **Runtime-confirmed (2026-10-19, stock `.07.01` config, QIDI Studio stock filament-change G-code, two USB Boxes):** during a print, `T[n]` → `T_MANAGER` cut once (the extruder stepper was disabled at the start of the change, matching `CUT_FILAMENT_1`), unloaded the previous filament, and attempted the new load; the ignored `CUT_FILAMENT T=` and `UNLOAD_T[n]` lines caused no second cut. A load that never reached the toolhead sensor reported `QDE_004_006` (`Extruder loading failure.`) with `b_endstop_state = 1` and `e_endstop_state = 0` and paused the print; the same code paused print-start loading. The operator attributed both failures to filament quality. Operator `RESUME` retried the load and printing continued.
- **Runtime-confirmed (same print):** each of five further tool changes showed exactly one extruder-stepper disable, consistent with one flow cut per change. Stock end G-code `UNLOAD_FILAMENT T=` cut once (extruder disable one second after `print_end`), unloaded to the Box, and the print reached `ready` about 65 seconds after `print_end`; afterward `loaded_slot` and `last_load_slot` were `slot16`, the toolhead sensor reported no filament, and the extruder target was `0`. Whether the vendor unload wipes the nozzle was not observed.
- **Unresolved:** whether vendor `UNLOAD_FILAMENT` wipes the nozzle or changes the hotend target itself, and RS-485 `BoxAdapterV2` status and behavior on hardware.

## Firmware `01.01.07.02` compiled-module differences

Max 4 `01.01.07.02` (`QD_MAX4_01.01.07.02_20261123_Release.zip`) changes `box2base.so` (`cc1988bd…`), `box485_multicolor.so` (`e5c297a6…`), `box_device.so` (`160926f2…`), `box_extras.so` (`e0559288…`), `box_protocol.so` (`9b5d18ac…`), `box_stepper.so` (`aedb3ea8…`), and `multi_color_controller.so` (`f87e3141…`); every other compiled module and every `klippy` Python file is byte-identical to `.07.01`. Everything below is **static-recovered (2026-11-24)** from the modules' decompressed string tables, symbols, and disassembly, compared with `.07.01`'s; none of it was run in the harness or traced on a printer. The changes the USB Box flows reach are stand-ins in `installer/tests/gcode_flow/qidi.py`: `EXTRUSION_AND_FLUSH`, `T_MANAGER`'s sensor handling, `BOX_PRINT_START`'s `box_count` gate, `FLOW_RETRY`'s chute move and abort, the completion script without its dwell, and `QDE_004_021` in print start, resume, and eject.

- `box_stepper.so`: `do_home_three_steps` and `do_home_double_steps` disassemble identically, the load and unload functions differ only in line numbers, and the string, integer, and float constants are unchanged (550, 380, 240 at 85, 65, 10 mm/s; -350, -1150). The only code change is in `cmd_detect_auto_reload_func`: after a successful runout auto-reload (`SET_PRINT_SUB_STATUS SUB_STATUS=auto_reloading_successful`) it no longer calls `box_extras.save_variable('extrude_state', …)`.
- `box_extras.so`: besides the `QDE_004_021` message above, only drying changes. `enable_box_drying` saves `end_timestamp` (epoch end) in `box_drying_state.boxN`, and `_build_boxes_status` publishes `end_time` as `ceil(end_timestamp - time.time())` when it is present, so the status changes every second while a Box dries. `.07.01` kept `end_time` where the dry started (the Plus 5, `ui/src/state/box-dry.ts`).
- `multi_color_controller.so`:
  - New `SET_BOX_BELONG_WITH_ATTR BOX=<n> ATTR=<0|1|2>` (help `设置BOX左右归属`, set a Box's left/right ownership): `BOX` below 1 or another `ATTR` responds `Error: BOX>=1 and ATTR must be 0, 1 or 2.`; otherwise it goes through `BoxAdapterV2.set_box_belong_with_attr` to `box2base` and `box_device`, which sends the RS-485 `SET_BOX_BELONG_WITH_ATTR` frame and publishes `belong_with` (`left`, `right`, `unknow`).
  - RS-485 `BoxAdapterV2`: `cut` sends `CUT_FILAMENT_1\nMOVE_TO_TRASH\n` (was `CUT_FILAMENT_1`); `load_filament` and `unload_filament` no longer start with `MOVE_TO_TRASH`; `wipe` sends `G1 E100 F600\nG1 E-2 F1800\nM400\nM104 S0\nM106 S255\nM400\nG4 P5000\nWIPE_NOZZLE_DROP\nM106 S0\n` without the `MOVE_TO_TRASH` before and after it. `flow_decision_func` now sets `current_flow_temp` from the target slot's filament.
  - `ExternalAdapter.bite` writes `SAVE_VARIABLE VARIABLE=last_load_slot VALUE='"<slot>"'`, joined, where `.07.01` appended `repr(<slot>)`.
  - Its string table gains `Code:QDE_004_021; Message:Unable to recognize loaded filament.` and `Code:QDE_004_029; Message:Unable to recognize the loaded filament.\n`; no code that loads them was found (only `box_extras`' `QDE_004_021` is sent, above).
  - `get_status` before Klipper is ready now also returns `extruder_limit_state`, `auto_init_detect`, `auto_read_rfid`, `auto_reload_detect`, `enable_box`, `load_retry_num`, and `box_count`; the ready status keys are unchanged. `FLOW_MAP`, the USB adapter's step methods, `ExternalAdapter`'s other steps, and `TaskQueueManager.start_flow` and `stop` have the same strings and calls.
- RS-485 `box2base.so`:
  - `detect_filament_loaded` sends `M83\nFILAMENT_IN_OUT_HOTEND T=0` to `T=4` where `.07.01` sent `M83\nG1 E25 F300`, `E15 F600`, `E10 F300`, `E25 F900`, and `E15 F300`; its unload's `M83\nG1 E-35 F600` became `M83\nFILAMENT_IN_OUT_HOTEND T=5`, which stock `.07.02` `filament.cfg` makes `G1 E-20 F600`.
  - A new auto-reload monitor (`_auto_reload_monitor`, `_execute_auto_reload_box2`, decorator `suspend_auto_reload`) watches the extruder position against `auto_reload_extruder_length` and sends `MOVE_TO_TRASH`, `M109 S`, `PAUSE\nM400`, `RESUME`, and `SET_PRINT_SUB_STATUS SUB_STATUS=auto_reloading_failed`; the old reload's `G1 E100 F300\nG1 E-1 F1800\nDROP_TRASH\nRESUME_PRINT` ends with `RESUME` instead. `try_resume_print_box2` adds `M104 S0` and `进料失败，恢复打印失败` / `退料失败，恢复打印失败` (load or unload failed, resume failed).
  - It still sends `M83\nMOVE_ANOTHER_POINT_CHANGE_FILAMENT\n`, `MOVE_ANOTHER_POINT_CHANGE_FILAMENT\nM400\n`, and `TRY_INTO_EXTRUDER\n` with no `MOVE_TO_TRASH` before them, while stock `.07.02` `filament.cfg` drops the `MOVE_TO_TRASH` those macros, and `WIPE_NOZZLE_DROP`, started with (config-confirmed).
- RS-485 `box_device.so`: a wrapping (`缠料`) handler disables auto-feed, sends `PAUSE`, and up to a counted number of times recovers on its own: heats to `RESUME_PRINT`'s `saved_extruder_temp`, unloads and reloads the slot, checks the filament moves (`\nM83\nG1 E<n> F300\nM400\n` against the buffer's Hall readings), sends `\nCUT_FILAMENT_1\nMOVE_TO_TRASH\n` and `RESUME_PRINT`, or turns the nozzle off (`M104 S0`) and stays paused. A Box error pauses with `QDE_004_069` or `QDE_004_070` and `PAUSE\nM400`, and a Box going offline pauses too. New commands per RS-485 Box: `GET_BOX_LINK_BUFFER_TIMEOUT_NUM`, `SET_BLDC_SPEED_MIN_PWM MIN_PWM=`, and `SET_UNLOAD_PWM BLDC_PWM= DC_PWM=` (0–100).
- RS-485 `box485_multicolor.so`: adds `BOX_DIAG` and `BOX_DIAG_RESET` (registered whenever `[box485]` loads), the `BOX_LINK_BUFFER_TIMEOUT_NUM_ACK` and `BOX_DIAG_REPORT` replies, and RFID-enable broadcasts that follow the print state. With no adapter it logs `Can not find 485 device: <reason>` once per outage (`_reconnect_missing_logged`) where `.07.01` logged `Can not find 485 device` every 2 s.
- **Unresolved:** what `extrude_state` holds after a `.07.02` runout auto-reload, and so which slot a later `TRY_RESUME_PRINT` loads and which tool the Valve Oil UI shows; whether `ExternalAdapter.wipe` still returns `False` (its strings and calls are unchanged, so Valve Oil's `vo_box_flow_recovery` treats it as before); the filament temperature `EXTRUSION_AND_FLUSH` uses for an ID `get_max_temp_by_single_id` doesn't list; and, on RS-485 hardware, whether a `BoxAdapterV2` load can leave the nozzle off the chute before its wipe, whose `WIPE_NOZZLE_DROP` starts with `G1 Y403` wherever the nozzle is.

## Task queue

`.07.01`'s `TaskQueueManager.FLOW_MAP` and its pause, stop, and retry are the stand-ins in `installer/tests/gcode_flow/qidi.py`, run by the Box flow tests.

The `.04` harness recorded flows `0`–`9` only, with `EXT_LOAD` before `EXT_BITE` and a `WAIT_USER` step between the extruder and Box halves of flows `8` and `9`.

Firmware `.04` fake-manager `_decide_flow_id()` results:

- uppercase `LOAD`: flow `1` without filament; flow `2` with filament when `last_load_slot != slot16`;
- uppercase `UNLOAD`: flow `3` with filament when `last_load_slot != slot16`; flow `7` when `last_load_slot=slot16`;
- uppercase `EJECT`: flow `4` in all harnessed states;
- lowercase and adapter-like action strings returned flow `0` in direct harnessing;
- `LOAD` with filament and `last_load_slot=slot16` raised `TypeError`; `last_load_slot=None` raised `AttributeError`.

`start_flow()` sets the flow ID, copies steps, initializes operation index `0`, and clears waiting/active-step state. Default-state `tick()` completed box-side flows `1`, `2`, `3`, and `5` but not extruder/mixed flows `6` through `9`; this is harness behavior, not live operation proof.

### `.07.01` flow pause, stop, and retry

- **Harness-recovered (2026-10-31, `TaskQueueManager` and `MultiColorController` with fake objects):** `multi_color_controller.operation.active_slot` stays the flow's target slot while a flow is paused.
- **Runtime-confirmed (test printer `klippy.log`, 2026-10-31 08:42 UTC, `.07.01`, two USB Boxes):** during a `BOX_LOAD` whose approach missed the toolhead sensor, `Error during homing e_endstop: Communication timeout during homing` ended `LOAD_FILAMENT_CONTROL` with an error, followed by `检查到！！错误，恢复默认状态！！` ("error detected, restoring default state"). The task stayed flow `1` at `BOX_LOAD` with `is_paused` false and the hotend target 240 °C; MCU statistics showed no retransmits and no Box disconnect. `FLOW_STOP` cleared it.
- **Runtime-confirmed (same session):** when the `BOX_LOAD` approach misses the toolhead sensor, the step runs about a minute of toolhead moves with small extruder moves before `QDE_004_006`; with a 140 mm push past the hub, about 640 mm had to be retracted to clear the hub afterwards. `FLOW_RETRY` of that step first pulled the filament back until the hub sensor cleared (`loaded_slot` `slot16`), then fed it to the hub again and ran the load.
- **Config-confirmed (QIDI `klippy/extras/filament_switch_sensor.py`) and runtime-confirmed (test printer, 2026-10-31):** the toolhead sensor's `runout_helper.filament_present` starts as `False`, but `note_filament_present()` stores the button callback's raw state, so after the sensor first changes it holds `0` or `1`. Its status `filament_detected` is `bool()` of it. A check that compares it with `False` by identity stopped matching once the sensor had changed since Klipper started.
- **Static-recovered:** `box_extras.so` names no `stop` method; its only `stop` strings are log messages. No other vendor G-code command reached `TaskQueueManager.stop()` in the harness.
- **Runtime-confirmed (operator `klippy.log`, 2026-10-31, `.07.01`, two USB Boxes, package `26.10.29.1`):** restarting `klipper.service` while `FLOW_STOP` held the G-code lock and the heater was on left the THR board shut down (`Can not update MCU 'THR' config as it is shutdown`) until `FIRMWARE_RESTART`.

### Tool change after a slot runs out

- **Harness-recovered (2026-11-20, `box_extras.so` of all three printers):** `switch_next_slot()` is a `BoxExtruderStepper` method that looks for another slot with the same filament ID and color, remaps the tools on the empty slot to it, or reports `QDE_004_022` and sends `PAUSE`; `T_MANAGER`'s auto-reload branch never reaches it (the stand-in in `installer/tests/gcode_flow/qidi.py`).
- **Runtime-confirmed (operator `klippy.log`, 2026-11-20, package `26.11.06.1`, one USB Box):** after the auto-reload crash and a resume that loaded 1D, the next change back to 1C paused with `QDE_004_005 Please load the filament to 1C first.`

### `.07.01` load and unload with filament at the hub

- **Harness-recovered (2026-10-30, USB `BoxAdapterV1`, fake objects):** `flow_decision_func(action, slot)` reads the toolhead `filament_switch_sensor`, `box1base.b_endstop_state` (hub, `0` = filament), and saved `last_load_slot` (default `slot16`). For slot 2:

  | Action | Toolhead | Hub | `last_load_slot` | Flow |
  |---|---|---|---|---:|
  | `LOAD` | empty | filament | any | `10` |
  | `LOAD` | empty | clear | any | `1` |
  | `LOAD` | filament | any | `slot16` or missing | `8` |
  | `LOAD` | filament | filament | another slot | `2` |
  | `LOAD` | filament | clear | another slot | `-1` |
  | `UNLOAD` | filament | any | `slot16` or missing | `7` |
  | `UNLOAD` | filament | filament | another slot | `3` |
  | `UNLOAD` | filament | clear | another slot | `-1` |
  | `UNLOAD` | empty | any | any | `0` |

- **Harness-recovered:** `BoxAdapterV1.unload_filament()` sends `MOVE_TO_TRASH`, then returns `True` with nothing else when the hub is clear. With filament at the hub and `last_load_slot` `slot16` or missing, it responds `无法识别到已进料耗材` ("can't identify the loaded filament") and returns `False`, so the flow pauses with `Step BOX_UNLOAD failed. Pausing flow.` Otherwise it sends `M109 S<current_flow_temp>` and calls `extruder_unload_single_func()` on `box_stepper <target_unload_slot>`, or on `last_load_slot` when `target_unload_slot` is empty, as it is in flow `10`.
- **Harness-recovered:** `BoxAdapterV1.init_steps_method(task_queue_manager)` sets `box1base` to the `box_extras` object and stores the adapter's step functions in `task_queue_manager.method_map`, which `start_flow()` runs; replacing an adapter attribute afterward does not change a flow. `cmd_FLOW_RETRY` passes `RETRY` (default `0`) as `can_next`. `retry()` sends `M109 S<current_flow_temp>`, then reruns the paused step, or skips it when `can_next` is set.
- **Harness-recovered:** `BoxExtruderStepper.get_status()` publishes `r_endstop_state` as `runout_button`. `BoxExtras.update_b_endstop()` drives the hub LED from `b_endstop_state`. `INIT_BOX_STATE`'s rules for `slot_sync` at start are its stand-in in `installer/tests/gcode_flow/qidi.py` (harness, 2026-11-23).
- **Runtime-confirmed (operator `klippy.log`, 2026-10-30, package `26.10.28.3`, one USB Box):** with the Box heater off and no G-code running, slot 1 (1B) moved at 18:00:59, consistent with the automatic slot load on insertion. 12 s later Klipper logged `Got EOF when reading from device`, then `Lost communication with MCU 'mcu_box1'`. After restarting, three insertion loads of 1B failed with `1B did not recognize the filament` and `QDE_004_001`, and a later request failed with `QDE_004_011`. Over the next three hours, across several restarts and with the nozzle heated to 240–280 °C, `BOX_UNLOAD` refused ten times with `无法识别到已进料耗材`; none passed that step, and the operator reported the hotend staying hot. The saved `last_load_slot` was not captured. A Box reset partway through the automatic slot load, leaving 1B's filament at the hub, fits the vendor predicate above but is not proven.

- **Runtime-confirmed (2026-10-31, `.07.01`, two USB Boxes, 2 s status polling plus `klippy.log`):** during a Box load, `box_extras.loaded_slot` and `last_load_slot` changed to the loading slot when its filament reached the Box hub, early in `BOX_LOAD` and before the extruder moved. This happened in a flow `1` load whose filament never reached the toolhead sensor and that paused with `QDE_004_006`; both values stayed on that slot afterwards. In a flow `2` change from slot 1 to slot 0, both read `slot16` once the hub cleared during `BOX_UNLOAD`, then `slot0` when slot 0 reached the hub. The extruder pulled back 4 mm at the cut and 110 mm during the unload, then moved 164.2 mm forward during slot 0's load and wipe.
- **Runtime-confirmed (same session):** a flow paused at `BOX_LOAD` kept the hotend target at 240 °C. Neither QIDI's touchscreen nor Fluidd offered a retry or stop. `FLOW_STOP` cleared the task (`flow_id None`) but its G-code request had not returned eight minutes later, and every later G-code command waited behind it until `klipper.service` was restarted; the THR MCU then reported shutdown until `FIRMWARE_RESTART`. In a later flow `2` change, `FLOW_RETRY` after `QDE_004_009` (`Extruder unloading failure`) at `BOX_UNLOAD` returned and the flow completed.

### `.07.01` eject

- **Harness-recovered (2026-10-28, fake objects):** `cmd_EJECT_FILAMENT_CONTROL` reads `SLOT`, asks `box_adapter.flow_decision_func("EJECT", slot)` for the flow, and calls `task_queue_manager.start_flow(flow_id, self.normal_complete_script)`. `BoxAdapterV1.flow_decision_func` reads the toolhead `filament_switch_sensor`, saved `last_load_slot`, and `box1base.b_endstop_state`. For an eject of slot 2 it returned:

  | Hub (`b_endstop_state`) | `last_load_slot` | Toolhead sensor | Flow |
  |---|---|---|---:|
  | clear (`1`) | any harnessed value | either | `4` |
  | occupied (`0`) | `slot16` | either | `0` |
  | occupied | `slot2` | filament | `5` |
  | occupied | `slot2` | empty | `11` |
  | occupied | another slot | filament | `4` |
  | occupied | another slot | empty | `11` |

- **Harness-recovered:** `BoxAdapterV2.eject()` returned `True` without sending G-code.
- **Runtime-confirmed (2026-10-28):** `box_adapter` and `task_queue_manager.operation_adapter` were still `None` when a package module's `klippy:ready` handler and a reactor callback it scheduled ran, and `MultiColorController is ready.` was logged after them. Later both held the same `BoxAdapterV1`, and `task_queue_manager` stayed the same object. During flow `4`, `task_queue_manager.current_task.queue` was `['BOX_EJECT']` both when the eject step and when the completion script sent G-code.
- **Unresolved:** live eject flows `5` and `11` were not exercised.

### `.07.01` flow heating and extruder moves

- **Harness-recovered (2026-11-06, fake objects):** `current_flow_temp` lives on `task_queue_manager`. Only `BoxAdapterV1.flow_decision_func` sets it: for `UNLOAD`, the top of the slot filament's `nozzle_temperature` range (240 for an unknown ID); for `EJECT`, the higher of the slot's filament and `last_load_filament_id` (missing counts as `QFA00`); flows `0` and `4` leave it `None`. `clear_flow_parameters()` sets it to `None` at the start of every decision, after the completion script, and in `stop()`.

### `.07.01` filament between the hub and the toolhead

- **Harness-recovered (2026-10-31 and 2026-11-23):** `extruder_load_single_func()`'s happy path is its stand-in in `installer/tests/gcode_flow/qidi.py`. A failed hub stage saves the slot state `-2` and reports `QDE_004_006`; a toolhead sensor that never triggers retries at X30 Y30 and X390 Y30 and a shake with the slot geared before `QDE_004_006`; a load cell that never responds shakes, detects again, and reports `QDE_004_024`. Its shake script's `M400M204 S10000` runs as `M400`, losing the `M204`.
- **Runtime-confirmed (2026-10-31 incident):** the hub record logged 1B reaching the hub at 06:54:51, and a test module that follows the loaded slot logged `1B loaded` in the same second; the extruder then drew filament without it reaching the toolhead sensor, and `QDE_004_006` followed at 06:56:19. Saved state afterwards was `last_load_slot` `slot1`, `slot_sync` `slot-1`, `extrude_state` `2`, `slot1` `-2`, and `retained_slot` `slot1`, with the hub sensor `0` (filament) and the toolhead sensor empty. The operator had lengthened the tube from the hub to the toolhead without raising `FAST_LENGTH` (570).
- **Harness-recovered:** with the toolhead sensor empty and filament at the hub, `UNLOAD_FILAMENT_CONTROL` gets flow `0`: no steps run, then the completion script sends `MOVE_TO_TRASH`, `M104 S0`, and the extruder disable and responds `Flow completed successfully.` `EJECT_FILAMENT_CONTROL` of `last_load_slot` gets flow `11`: `BOX_HEAT` heats the nozzle, and `BOX_UNLOAD` calls `extruder_unload_single_func()` on that slot.
- **Harness-recovered:** `extruder_unload_single_func()` with the toolhead sensor empty and filament at the hub skips the extruder and saves the slot state `3`, then homes the slot back on the hub sensor (`-350` mm at 65 mm/s, then `-1150` mm at 85 mm/s, accel 100). When the hub clears it saves `last_load_slot` `slot16`, retracts 260 mm more at 80 mm/s, saves the slot state `1`, disables the slot motor, and sends `MOVE_TO_TRASH`. Otherwise it moves the toolhead and homes again up to 1500 mm with the extruder synced.
- **Harness-recovered:** the eject step's `slot_unload_single_func()` saves the slot state `3` and homes the slot back up to 3000 mm at 100 mm/s; on failure it reports `QDE_004_003` and saves the slot state `-1`.
- **Harness-recovered:** `BoxExtras.print_sensor_state_to_log()` logs a `last sensor state:` block with its message. The block logged at the 2026-10-31 failure read `b_endstop_state = 1`, while the controller's hub field read `0` afterwards; which value the block prints is **unresolved**.
- **Runtime-confirmed (2026-10-31):** `multi_color_controller` status also publishes `box_warning_info`, an empty list on an idle printer. What fills it is **unresolved**; the controller's strings include the Box heater-timeout and humidity-sensor warnings.
- **Unresolved:** whether flow `11` would have cleared the hub on the printer; it never ran.

### `.07.01` tool change

`T_MANAGER`'s decisions and flow `13`, `TOOL_CHANGE_START`'s `exclude_tool`, `slot_sync`, and `TRY_RESUME_PRINT` are stand-ins in `installer/tests/gcode_flow/qidi.py` with their evidence, run by the G-code flow tests.

- **Runtime-confirmed (operator `klippy.log`, 2026-10-29, two USB Boxes):** in a print whose four tools Fluidd mapped to one slot, each `T_MANAGER T=<n>` returned in 1–5 ms. In a print with distinct slots on 2026-10-28, each blocked for 62–66 s.
- **Harness-recovered (2026-11-03, `box_extras.so` sha256 `0878b5b9…`, real `TaskQueueManager`, fake steppers):** `filament_printing_manager_box1` reports flow `13`'s steps through `start_external_flow`/`set_external_step`, not `start_flow()`.
- **Unresolved:** RS-485 `BoxAdapterV2` tool-change predicates, and whether `multi_color_controller.box.loaded_slot` names the new slot by the time a flow-`13` `T_MANAGER` returns.
- **Unresolved:** the live predicates for cut-before-unload, an unsynced slot, and `slot16`. A fake-object harness emitted an unload from `slot-1` in one unsynced case.
- **Runtime-confirmed (operator `klippy.log`, 2026-11-17, `.07.01`, one USB Box, package `26.11.15.1`):** with print-start Box prep skipped for a T4 start, the slicer's `T4` raised `Unknown config object 'box_stepper slot4'` out of `T_MANAGER` instead of the `QDE_004_027` pause the stand-in models for targets beyond `box_count * 4`; Valve Oil's tool-change guard paused the print. Which of the two QIDI does when is **Unresolved**.

### `.07.01` load-cell timers

- **Harness-recovered (2026-11-03, `box_extras.so` sha256 `0878b5b9…`):** `init_probe_data` takes 19 load-cell readings through `get_probe_mv` with `G4 P100` between them and keeps their median as the resting baseline; it registers no timer. `auto_detect_filament_advance` sets `median` to `999`, registers `_init_probe_data_advance` in `init_probe_data_advance_timer`, which samples every 0.2 s, stores the median after about 20 samples, sets `init_probe_data_advance_timer` to `None`, and returns `NEVER` without unregistering. `detect_filament_loaded` extrudes in steps (`G1 E25 F300`, then `E5`, `E10`, and `E20` moves) while `_read_wei_timer` compares readings with that baseline; a later call stops the timer it holds with `update_timer(NEVER)` and registers a new one without unregistering the old. `box2base.so` contains the same methods.
- **Runtime-confirmed (test printer, 2026-11-03, gdb dump of `reactor._timers`):** after 405 Box loads in one Klipper session, Klipper held 1278 timers, 808 `_read_wei_timer` and 405 `_init_probe_data_advance`, all at `NEVER`, with `read_wei_timer` and `init_probe_data_advance_timer` both `None`. `_check_timers` took about 65% of the main thread at idle, and Klipper's CPU use had risen steadily from about 0.38 to 0.58 of a core over the 33-hour print, with Box unloads peaking near a full core.
- **Config-confirmed:** flow `13` has no heat step, and stock `PAUSE` pauses whenever `idle_timeout` reports `Printing`, as Klipper reports while the toolhead is busy, such as during a Box flow's moves, then sets the hotend target to `0`. A `PAUSE` sent during a `T_MANAGER` run outside a print therefore leaves `pause_resume` paused with no print.
- **Unresolved (operator screen recording, 2026-11-01, `.07.01`, two USB Boxes, package `26.10.29.1`):** right after a Box load finished and turned the hotend off, `T0`, `T5`, and `T4` sent from Fluidd outside a print were followed over about three minutes by repeated `The temperature at the hot end is unstable. Wait for the temperature to stabilize before trying again.`, `action:paused`, and `Extrude below minimum temp`, with the hotend cooling. Fluidd's Box card stayed busy loading the `T4` slot with no failed step shown, and a later `T4` did nothing visible. Which step raised or paused, and which component sends the hot-end message, are unresolved; the hot-end message is not in the `.07.01` Klipper tree or QIDI's touchscreen app on the test printer.

### `.07.01` saved-variable writes

- **Harness-recovered (2026-11-03, `box_extras.so` sha256 `0878b5b9…`, `box2base.so` `d1cd52e4…`, `box485_multicolor.so` `f055bb21…`):** `BoxExtras.save_variable(varname, value)` and `boxbase.save_variable` copy `save_variables.allVariables`, set the name as given, write every variable in `sorted()` order with `repr()` values through `ConfigParser` by truncating `saved_variables.cfg` in place, then call `save_variables.loadVariables()`. They never skip an unchanged value. `ConfigParser` lowercases names, so a mixed-case name whose lowercase form is saved keeps the saved value and moves to where the mixed-case name sorts. A `%` in any value's `repr()` raises `ValueError` before the file is opened; a write failure raises `Unable to save variable <name> as <value>`. `Box485Manager._save_variable` is the same after returning when `sv` is `None`; `cmd_BOX_SAVE` calls `save_variables.set`/`save`, which stock `save_variables` lacks, and writes nothing.
- **Runtime-confirmed (test printer, 2026-11-03, py-spy and a file watcher):** these saves held Klipper's main loop for 1.69 s at Box print start and 110–160 ms per tool change, which rewrote the file in place 8 times.
- **Harness-recovered:** besides `BOX_PRINT_START`'s writes (its stand-in in `installer/tests/gcode_flow/qidi.py`), tool changes save `is_tool_change` at start and end and `extrude_state`, and `box_stepper` load and unload steps save the slot state, `last_load_slot`, `slot_sync`, `load_retry_num`, `diff_mv`, `retry_step`, and `extrude_state` (static-recovered call sites). No vendor code re-reads the file at runtime; readers use `allVariables.get()`.
- **Harness-recovered (2026-11-22, QIDI's `save_variables.py` and `box_extras.so`, identical on Max 4 `01.01.07.01`, Plus 5 and Smart 5 `01.01.02.01`):** Box print start saves `BOX_DRYING_ROLL_ENABLE` 0, and the drying timer reads `box_drying_roll_enable` with default 1. The file stores names in lower case, so:
  - when the key is absent, the first Box print start writes `box_drying_roll_enable = 0`, and drying no longer turns the spools;
  - when the touchscreen has already saved it as 1, print start's 0 never takes effect.
  - `box2base` defaults the same key to 0.
- **Harness-recovered (same):**
  - `SAVE_VARIABLE` with an empty `VALUE=`, an unbalanced quote, or a `%` raises an internal error, and Klipper shuts down.
  - A value that saves `%%` reads back as `%`, and every later save then shuts Klipper down too, across restarts.
- **Config-confirmed (SOC package `postinst`, identical on all three printers):** after an update, keys missing from `saved_variables.cfg` are copied from QIDI's `.bak` through `$(echo $value | xargs)`, which strips their quotes. A file missing `filepath`, `last_file`, `last_load_slot`, or `slot_sync` gets lines such as `last_load_slot = slot16`. **Harness-recovered:** Klipper's loader then can't parse the file and Klipper doesn't start. All five operator files seen have those keys.
- **Static-recovered (`qidiclient`, Max 4 `01.01.07.01`):**
  - it saves `box_count` at every Klipper ready, at app start, and when a Box is plugged in;
  - its factory reset truncates `saved_variables.cfg` and copies `.bak` over it while Klipper runs, and restarts Klipper only after a cool-down.
- **Config-confirmed (QIDI's Moonraker, `moonraker_status_update.py`):** each time qidiclient subscribes, Moonraker saves `beep` again from its own `general.sound` setting. **Runtime-confirmed (operator `klippy.log`, 2026-10-29):** three `beep` saves about 2.4 s after each Klipper ready.

### `.07.01` status cost

- **Harness-recovered (2026-11-03, `multi_color_controller.so` sha256 `b7e3bc21…`, real `BoxExtras` and `MultiColorController` against the test printer's saved variables and filament database):** `multi_color_controller.get_status` builds `box` from `box_extras.get_status`, flattens it to about 440 keys of strings, numbers, and lists, and returns `copy.deepcopy` of that dict, the only deep copy in the call. Its result is new on every call and its output does not depend on `eventtime`. With the USB `BoxAdapterV1`, `box.loaded_slot` and `box.last_load_slot` are both `allVariables.get('last_load_slot', 'slot16')`.
- **Runtime-confirmed (test printer, 2026-11-03, idle, gdb timing):** `multi_color_controller.get_status` took 10.8 ms for 16.5 KB, `box_extras.get_status` 1.8 ms for 8.8 KB. Moonraker subscribed to 294 objects and QIDI's webhooks refreshed them once a second (`SUBSCRIPTION_REFRESH_TIME = 1.`). A macro's first read of an object copies its whole status: `printer.configfile` 48 ms, `printer.gcode` 16 ms, `printer.multi_color_controller` 16 ms, `printer.box_extras` 9 ms.
- **Harness-recovered (`box_stepper.so` sha256 `58ee8c61…`):** each slot's `set_led` timer sends LED changes only; unchanged colours are skipped by QIDI's `led.py`. Steady white or red ticks every 1 s and sends nothing; blinking (`0`, `-3`) sends once a second; breathing (`2`, `3`, `-2`) steps every 0.1 s and sends each step.

### `.07.01` filament IDs in Box flows

- **Harness-recovered (2026-10-29, fake objects with a two-entry `filament-0.4.json`):** `BoxAdapterV1.flow_decision_func` reads the target slot's and the last-loaded slot's saved filament IDs through `get_save_vars_filament_id`, then compares them with `MaterialDatabaseJosn.get_max_temp_id`, which reads `nozzle_temperature` (`[min, max]`, default `[0, 240]`) from each ID's entry. `get_save_vars_filament_id` returns `QFA00` for a missing value, an int, or a bool, and passes anything else through unchanged: any string, including `'None'`, `''`, and `'7'`, and `None`, floats, and lists (Harness-recovered, 2026-11-22, Max 4 `01.01.07.01`). `get_max_temp_id` raises `AttributeError` for a string ID missing from the database and `TypeError` for a non-string; `get_max_temp_by_single_id` falls back to `240` for an unknown string ID.
- **Harness-recovered:** `LOAD_FILAMENT_CONTROL SLOT=16` with `filament_slot16 = 'None'` raised in `get_max_temp_id` for every harnessed toolhead-sensor and hub state. With `QFA00` substituted it selected flow `9` with filament at the toolhead and `last_load_slot` another slot, and flow `6` with the toolhead empty.
- **Harness-recovered:** the controller's `fila_json` database exists from controller construction, and `ExternalAdapter` (`controller.extre`, also created then) and the `BoxAdapterV1` created at ready hold that same object as `material_db`.
- **Runtime-confirmed (operator `klippy.log`, 2026-10-29, package `26.10.23.1`, one USB Box):** three `LOAD_FILAMENT_CONTROL SLOT=16` requests, with `filament_slot16 = 'None'` and `last_load_slot = 'slot16'` saved, each failed with `Internal error on command:"LOAD_FILAMENT_CONTROL"` from `MaterialDatabaseJosn.get_max_temp_id` (`AttributeError: 'NoneType' object has no attribute 'get'`) and shut Klipper down. The automatic restart that followed failed with `Can not update MCU 'mcu' config as it is shutdown`. The requests repeated one to four seconds apart after each shutdown; which client sent them was not recorded.

## Command risk boundary

### Runtime-confirmed query/idle-safe captures

```text
QUERY_MULTI_COLOR
QUERY_SAVE_VARIABLES
GET_MULTI_COLOR_STATUS
MCB_QUERY
SLOT_RFID_READ
INIT_RFID_READ
MULTI_COLOR_INIT_RFID
MULTI_COLOR_READ_RFID
```

The RFID commands were idle-safe only in captures with no valid visible tag result. Successful-tag behavior remains unresolved.

### State-changing or motion-adjacent

```text
MULTI_COLOR_SYNC
MULTI_COLOR_CLEAR_RUNOUT
SET_SAVE_VARIABLE
RESET_MULTI_COLOR_VARS
CLEAR_RUNOUT_NUM
MCB_CONFIG
SET_LIMIT_A
MCB_AUTO_ABORT
```

An already-matching sync or already-clear runout capture produced no delta; that does not prove no-op behavior for other state.

### Heater/dryer

```text
ENABLE_BOX_DRY DISABLE_BOX_DRY DISABLE_BOX_HEATER BOX_TEMP_SET
MULTI_COLOR_DRY MULTI_COLOR_DISABLE_HEATER MULTI_COLOR_SET_TEMP
```

- **Harness-recovered (2026-11-16, `.07.01` `box_extras.so`, fake objects):** drying's timer, `BoxExtras.heating_handler(eventtime, box_num, start_time, end_time)`, reads `get_value_by_key('box_drying_roll_enable', 1)`. When it is truthy, it sends `RUN_STEPPER STEPPER=<slot>` followed by `G4 P5000` through `gcode.run_script_from_command` for each of the Box's four slots (`box_num` 1 gives slots 0–3), then returns `eventtime + 300`. `cmd_RUN_STEPPER` calls the slot's `do_move(-50, 80, 50)`, `dwell(0.5)`, and `disable_stepper()`. `BoxExtruderStepper.do_move` starts its move at `toolhead.get_last_move_time()`, which flushes the toolhead's lookahead, and the `G4 P5000` dwells the toolhead.
- **Runtime-confirmed (operator Max 4 `klippy.log`, `01.01.07.01`, 2026-11-23):** drying started before a print kept turning all four slots of the Box every 300 s through the print, and `BOX_PRINT_START` did not stop it. The first turn of a later drying landed during print start's Z homing move: `RUN_STEPPER`'s `get_last_move_time()` flushed the toolhead in drip mode, raising `DripModeEndSignal` outside the homing move, and Klipper shut down with `Internal error on command:"RUN_STEPPER"`.
- **Runtime-confirmed (Plus 5, 2026-11-16, one USB Box):** a slot motor turns the roller under its spool with no filament fed. Driving an empty slot back 1,350 mm at 80 mm/s turned the spool on its rollers the whole time. `box_extras` status reports each Box's `dry_state` in `boxes[N]`; with a manual heater target it read `0`.

### Toolhead/extruder motion

```text
CLEAR_FLUSH CLEAR_OOZE CUT_FILAMENT MOVE_TO_TRASH
MULTI_COLOR_CLEAR_FLUSH MULTI_COLOR_CLEAR_OOZE MULTI_COLOR_CUT_FILAMENT
EXTRUSION_AND_FLUSH (.07.02, compiled)
```

### Box motion or motion-capable

```text
EXTRUDER_LOAD EXTRUDER_UNLOAD SLOT_UNLOAD E_LOAD E_UNLOAD E_BOX
BOX_PRINT_START MULTI_COLOR_PRINT_START
MULTI_COLOR_LOAD MULTI_COLOR_UNLOAD MULTI_COLOR_SWAP MULTI_COLOR_BOX_UNLOAD
MCB_AUTO_START
```

### Retry/recovery runtime gaps

```text
TRY_MOVE_AGAIN TRY_RESUME_PRINT RESUME_PRINT_1 AUTO_RELOAD_FILAMENT
RELOAD_ALL TIGHTEN_FILAMENT
MULTI_COLOR_RELOAD_ALL MULTI_COLOR_AUTO_RELOAD MULTI_COLOR_RETRY
MULTI_COLOR_TIGHTEN MULTI_COLOR_TRY_RESUME MULTI_COLOR_RESUME_PRINT
```

Moonraker command help is incomplete: callable compiled commands such as `BOX_PRINT_START`, `EXTRUDER_LOAD`, `EXTRUDER_UNLOAD`, `SLOT_UNLOAD`, `SLOT_RFID_READ`, `CLEAR_FLUSH`, and `CLEAR_OOZE` were absent from the filtered help capture.

Additional registered controller surface:

```text
MULTI_COLOR_CONFIG MULTI_COLOR_SYNC MULTI_COLOR_READ_RFID
MULTI_COLOR_INIT_RFID MULTI_COLOR_INIT_MAPPING QUERY_MULTI_COLOR
QUERY_SAVE_VARIABLES MULTI_COLOR_CLEAR_RUNOUT
SET_SAVE_VARIABLE RESET_MULTI_COLOR_VARS
MCB_AUTO_ABORT MCB_AUTO_START MCB_CONFIG MCB_QUERY SET_LIMIT_A
```

## Runtime-confirmed idle effects

- `QUERY_MULTI_COLOR`, `QUERY_SAVE_VARIABLES`, and `GET_MULTI_COLOR_STATUS` preserved saved state and operation state.
- `MULTI_COLOR_SYNC SLOT=slot2` preserved state when already synced to slot 2.
- `MULTI_COLOR_CLEAR_RUNOUT` preserved state when counters were already zero.
- RFID commands returned `ok`, left slot metadata unchanged, ended with `rfid.reading=false` and `rfid.results={}`, and exposed no raw FM17550 payload.
- `MCB_CONFIG SLOT=slot2`, `MCB_QUERY`, `SET_LIMIT_A STATE=0/1/0`, and idle `MCB_AUTO_ABORT` returned `ok`, preserved saved variables, and exposed no `MCB_STATE`, `MCB_DONE`, or `MCB_ERROR` payload.

## Error surface

Owner and message strings are static-recovered. `QDE_004_006` was seen live on 2026-10-19 and 2026-10-31; the console line is `!! Code:<code>; Message:<message>`.

| Code | Owner | Message |
|---|---|---|
| `QDE_004_001` | `box_stepper.so` | `Slot loading failure, please check the trigger, please reload %s.` |
| `QDE_004_002` | `box_stepper.so` | `Extruder has been loaded, cannot load %s.` |
| `QDE_004_003` | `box_stepper.so` | `Slot unloading failure, please unload %s again.` |
| `QDE_004_004` | `box_stepper.so` | `Please unload extruder first.` |
| `QDE_004_005` | `box_stepper.so` | `Please load the filament to %s first.` |
| `QDE_004_006` | `box_stepper.so` | `Extruder loading failure.` |
| `QDE_004_007` | `box_stepper.so` | `Extruder not loaded.` |
| `QDE_004_008` | `box_stepper.so` | `Extruder unloading failure.` |
| `QDE_004_009` | `box_stepper.so` | `Extruder unloading failure.` |
| `QDE_004_010` | `box_extras.so` | `The current feeding status is incorrect. Please exit the filament from the extruder.` |
| `QDE_004_011` | `box_stepper.so` | `Detected that filament have been loaded, please unload filament first` |
| `QDE_004_013` | `box_autofeed.so` | `Detected wrapping filament,please check the filament.` |
| `QDE_004_014` | `box_extras.so` | `Parameter setting error, please reset.` |
| `QDE_004_016` | `box_stepper.so` | `The filament has been exhausted, please load the filament to %s.` |
| `QDE_004_017` | `box_stepper.so` | `Filament flush failed, please clean and then load the filament in %s.` |
| `QDE_004_018` | `box_stepper.so` | `No filament specified, %s cannot be automatically replaced.` |
| `QDE_004_019` | `box_stepper.so` | `Please check if your PTFE Tube is bent` |
| `QDE_004_020` | `box_stepper.so` | `Detected that the filament has been unloaded, please reload.` |
| `QDE_004_021` | `box_extras.so` | `Unable to recognize loaded filament.` (`.07.02` also sends it from print start, resume, and eject; see [`01.01.07.02`](#firmware-01010702-compiled-module-differences)) |
| `QDE_004_022` | `box_stepper.so` | `No replaceable slot found.` |
| `QDE_004_023` | `box_extras.so` | `Auto reload failed.` |
| `QDE_004_024` | `box_stepper.so` | `The filament failed to enter the extruder.` |
| `QDE_004_025` | `box_stepper.so` | `Extruder unloading failure.` |
| `QDE_004_069` | `box_device.so` (`.07.02`) | `Excessive resistance of the power assisted motor.` |
| `QDE_004_070` | `box_device.so` (`.07.02`) | `BOX and BUFFER link errors.` |

`QDE_004_012` and `QDE_004_015` were absent from captured module strings. Exact live predicates for all codes remain unresolved.
