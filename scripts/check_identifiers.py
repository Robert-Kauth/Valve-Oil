#!/usr/bin/env python3
"""Check the repository for hardware identifiers and personal addresses that must not be published.

- A Klipper board's USB serial (`usb-Klipper_<chip>_<hex id>-if00`) or CAN bus UUID copied from a real printer.
- config/MCU_ID.cfg and config/box.cfg, which QIDI writes on each printer: every by-id serial is REDACTED.
- Kevin O'Connor's address outside the upstream Klipper GPL copyright headers that carry it.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

USB_SERIAL = re.compile(r"usb-Klipper_[\w.-]*?_([0-9A-Fa-f]{12,})-if\d+")
CANBUS_UUID = re.compile(r"canbus_uuid\s*[:=]\s*[0-9A-Fa-f]{12}\b")
KLIPPER_EMAIL = "kevin" + "@koconnor.net"  # split so this file doesn't hold the address
KLIPPER_COPYRIGHT = re.compile(r"^\s*(?:#|//)\s*Copyright \(C\) [\d, -]+\s+Kevin O'Connor <%s>\s*$"
                               % re.escape(KLIPPER_EMAIL))
REDACTED_FILES = ("config/MCU_ID.cfg", "config/box.cfg")
BY_ID_SERIAL = re.compile(r"^\s*serial\s*[:=]\s*(/dev/serial/by-id/\S+)")


def repository_files() -> list[str]:
    listed = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                            cwd=REPO_ROOT, check=True, capture_output=True, text=True).stdout
    return sorted(path for path in listed.split("\0") if path and (REPO_ROOT / path).is_file())


def main() -> int:
    failures: list[str] = []
    for path in repository_files():
        data = (REPO_ROOT / path).read_bytes()
        if b"\0" in data[:8192]:
            continue
        for number, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), start=1):
            if USB_SERIAL.search(line) or CANBUS_UUID.search(line):
                failures.append(f"{path}:{number}: a board's serial or CAN UUID; replace the id with REDACTED")
            if KLIPPER_EMAIL in line and not KLIPPER_COPYRIGHT.match(line):
                failures.append(f"{path}:{number}: {KLIPPER_EMAIL} belongs only in Klipper's GPL copyright header")
            if path in REDACTED_FILES:
                match = BY_ID_SERIAL.match(line)
                if match and "REDACTED" not in match.group(1):
                    failures.append(f"{path}:{number}: replace the board's id in {match.group(1)} with REDACTED")
    if failures:
        print("Identifiers that must not be published:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1
    print("no hardware identifiers or personal addresses found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
