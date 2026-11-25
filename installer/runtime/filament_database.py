"""Uninstall puts back the filaments Valve Oil moved out of QIDI's filament database.

At each Klipper start, vo_box_rfid (installer/klipper/extras/box_rfid.py) moves out of QIDI's filament-<nozzle>.json
files every filament whose nozzle maximum is above the printer's [extruder] max_temp, as on the Smart 5 (315 C), since
QIDI's Box loads heat the nozzle to that maximum. Each one that isn't Valve Oil's catalog entry is kept in
~/printer_data/.vo_removed_filaments.json with the name of the entry before it. Uninstall writes them back to their
places, unless the name or the filament ID has since been taken, and removes the record.
"""
from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path

from .fs_atomic import atomic_delete, atomic_write_text

RECORD = ".vo_removed_filaments.json"  # box_rfid.REMOVED_RECORD
NOZZLES = ("0.2", "0.4", "0.6", "0.8")  # box_rfid.DATABASE_NOZZLES


def _insert_after(entries: OrderedDict, name: str, entry: dict, after: str | None) -> OrderedDict:
    items = list(entries.items())
    if after is None:
        index = 0
    else:
        index = next((i + 1 for i, (key, _) in enumerate(items) if key == after), len(items))
    items.insert(index, (name, entry))
    return OrderedDict(items)


def restore_removed(printer_data_root: Path) -> list[str]:
    """Put the recorded filaments back. Returns the problems; the record stays while there are any."""
    record_path = printer_data_root / RECORD
    if not record_path.is_file():
        return []
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
        database = Path(record["database_dir"])
        filaments = record["filaments"]
        if not isinstance(filaments, dict):
            raise TypeError("filaments is not an object")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [f"{record_path} could not be read ({exc})"]
    problems = []
    for nozzle in NOZZLES:
        items = [item for item in filaments.get(nozzle) or () if isinstance(item, dict)
                 and isinstance(item.get("name"), str) and isinstance(item.get("entry"), dict)]
        if not items:
            continue
        path = database / f"filament-{nozzle}.json"
        try:
            entries = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=OrderedDict)
            if not isinstance(entries, dict):
                raise TypeError("not a JSON object")
        except (OSError, ValueError, TypeError) as exc:
            problems.append(f"{path} could not be read ({exc})")
            continue
        changed = False
        for item in items:
            used = {entry.get("filament_id") for entry in entries.values() if isinstance(entry, dict)}
            if item["name"] in entries or item["entry"].get("filament_id") in used:
                continue
            entries = _insert_after(entries, item["name"], item["entry"], item.get("after"))
            changed = True
        if not changed:
            continue
        try:
            atomic_write_text(path, json.dumps(entries, ensure_ascii=False, indent=2) + "\n")
        except OSError as exc:
            problems.append(f"{path} could not be written ({exc})")
    if not problems:
        atomic_delete(record_path)
    return problems
