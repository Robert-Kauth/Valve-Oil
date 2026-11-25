# Contracts

Machine-checked invariants. Generated views are outputs, not authority.

## G-code paths

`gcode-paths/*.path.json` hold each path's branch-level invariants: start print (one per printer), end print and cancel on error, filament change (cut, chute, unload), filament runout and resume, homing (Max 4 and Plus 5), Z offset across a print, and fan commands. `scripts/check_gcode_paths.py` checks them against both slicers' packs, `config/box.cfg`, `config/klipper-macros-qd/*.cfg`, and `installer/klipper/macros/*.cfg`, for every printer, and writes the views in `gcode-paths/generated/`.

- A branch names a `source_macro`, or a `source_section` for G-code outside a `gcode_macro` such as `homing_override`.
- The order of commands inside a macro, and commands a macro must never issue, go in a contract. `installer/tests/klipper/test_macro_contract.py` holds only what takes computing: moves against a printer's travel, cuts compared with QIDI's, rules across every installed file.
- To change a path: read `openspec/specs/printer-behavior/spec.md`, the path's contract, and its generated view; update the contract; run `python3 scripts/check_gcode_paths.py --write`, then `python3 scripts/check_gcode_paths.py`; commit the changed generated `.md` and `.mmd` files. If a command change leaves the generated views unchanged, the PR says why it isn't a branch-level invariant.

## Stock config

`stock-config.json` maps `config/` to the QIDI stock snapshot it mirrors; `scripts/check_stock_config.py` checks it. The file's `description` says what may differ.
