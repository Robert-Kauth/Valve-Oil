# Valve Oil's Klipper macros and modules

What the installer adds to the printer's Klipper config and Klipper. QIDI's own config is `config/` (stock, checked by `scripts/check_stock_config.py`); Valve Oil's Klipper tree is `klipper/`.

## Layout

- `macros/`: Valve Oil's macros for every printer, installed as `vo-macros/*.cfg`, which `config/printer.cfg` includes.
- `printers/<printer>/`: what differs by printer: homing (`homing.cfg`), cutter, wiper, and chute moves (`nozzle_motion.cfg`), and positions and calibration defaults (`printer_profile.cfg`, `_vo_printer`). Every printer has the same macro names; the installer layers the detected printer's folder onto `macros/`.
- `extras/`: Valve Oil's Klipper modules, installed as `klippy/extras/vo_<name>.py` (see `installer/package.yaml`).
- `qidi/`: QIDI's Klipper sources Valve Oil patches, with the stock files they replace in `qidi/stock/`.

## Where things are

- Include graph: `config/printer.cfg` → `MCU_ID.cfg`, `timelapse.cfg`, `klipper-macros-qd/*.cfg`, `vo-macros/*.cfg`, `box.cfg`.
- QIDI's macros and the Valve Oil files that wrap or replace them: `start_end.cfg`, `kinematics.cfg` (homing; Valve Oil's sequence is `printers/<printer>/homing.cfg`), `filament.cfg`, and `bed_mesh.cfg` (adaptive mesh). `config/KAMP/*.cfg` is not the active mesh path.
- Cooling: `macros/cooling.cfg`. Pause, resume, and cancel: `config/klipper-macros-qd/pause_resume_cancel.cfg`.
- Globals: QIDI's timing and behavior globals are `config/klipper-macros-qd/globals.cfg`, and something outside the config may read them even when nothing here does. Valve Oil's are `macros/globals.cfg`; positions and calibration defaults per printer are `printers/<printer>/printer_profile.cfg`.
- What QIDI's Box code does on a live printer (calls, settings, G-code, motor positions): `scripts/box_trace/`.

## Timing and terms

- `G4 P...` is fixed dead time; `M400` waits only for queued motion. A conservative speedup trims `G4` before it changes speeds or acceleration.
- "Purge" is extrusion at the rear waste chute and wiper; "prime line" is the slicer-start extrusion at the front of the bed.

## Checks

- `python3 scripts/format_klipper_configs.py` formats the `.cfg` files.
- `python3 scripts/check_gcode_paths.py` checks the macros against `openspec/contracts/gcode-paths/` (see `openspec/contracts/README.md`).
- `python3 scripts/run_installer_core_tests.py` runs the tests, including `installer/tests/gcode_flow/` (see `installer/tests/README.md`).
