# Installer tests

`python3 scripts/run_installer_core_tests.py` runs `scripts/check_gcode_paths.py`, checks that a test names each Klipper module in `installer/klipper/extras/`, then runs each test module in its own worker process, so a module's tests can't depend on another module having run. It runs under a Python with `requirements.txt`, setting up `build/test-venv` on first use.

## Layout

- `klipper/`: Valve Oil's Klipper modules and macros. Load a module with `klipper_fakes.load_extra` and reuse its `CommandError` and `Reactor`.
- `gcode_flow/`: slicer G-code run end to end. Each slicer pack's G-code runs through Klipper's own G-code and macro modules on a real install's config, on every printer, and the test checks what the printer did: where it purged, cuts, pauses, the state after. QIDI's compiled modules are stand-ins in `gcode_flow/qidi.py`, built only from recorded evidence (runtime, harness, or config) carried in each stand-in's docstring; a vendor command or branch that isn't recorded fails with "has no stand-in" until it's traced with `scripts/box_trace/`. See `openspec/observations/README.md` for how stand-ins replace observations.
- `firmware/`: board and QIDI firmware; the flows share `firmware/harness.py`.
- `host/`: services, screens, and host files.
- `lifecycle/`: install, update, uninstall, and USB.

## What a test is for

- Durable behavior, safety boundaries, and release contracts, not implementation detail: not private helpers, call order, constants, exact status wording, standard-library behavior, or synthetic examples unless they enforce a security boundary.
- A change to what the printer does during a print (slicer packs, macros, Valve Oil's Klipper modules) gets a `gcode_flow/` scenario. Each test says why it's needed: the incident or risk it guards.
- Each module in `installer/klipper/extras/` has a test of what it must and must not do on the printer.
- Extend existing lifecycle, contract, matrix, or smoke coverage before adding a module; prefer end-to-end scenarios and parameterized firmware and source matrices. A new module needs a distinct boundary: an operator-visible behavior or safety invariant nothing else covers. Remove or merge tests a behavior change supersedes.
- Static invariants belong in `openspec/contracts/` or a standalone checker in `scripts/`; run checkers directly rather than wrapping them in tests.
- No test sleeps for real; tests move a fake clock.
