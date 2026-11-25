#!/usr/bin/env python3
"""Check that config/ is QIDI's stock config, as openspec/contracts/stock-config.json maps it.

config/ mirrors the stock Max 4 config the contract names. Comments, whitespace, and blank lines may differ. Message
strings may differ only where the contract lists the translation, and only inside quotes or a `description:`. Lines
may be added only where the contract lists them. Files QIDI writes on the printer are pinned byte for byte or left
to scripts/check_identifiers.py. Valve Oil's own behavior goes in installer/, not here.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG = REPO_ROOT / "config"
CONTRACT = REPO_ROOT / "openspec/contracts/stock-config.json"

QUOTED = re.compile(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'')
DESCRIPTION = re.compile(r"^(description\s*[:=]).*$")
WHERE = ("Put Valve Oil's changes in installer/ (AGENTS.md maps where). config/ changes with QIDI's stock: move the "
         "contract's `stock` to the new snapshot.")


def settings(text: str) -> list[tuple[bool, str]]:
    """A config file's lines without comments or blank lines, each as (indented, text)."""
    lines = []
    for raw in text.splitlines():
        if raw.lstrip().startswith(("#", ";")):
            continue
        line = re.split(r"\s[#;]", raw, maxsplit=1)[0].rstrip()
        if line.strip():
            lines.append((line[:1].isspace(), line.strip()))
    return lines


def skeleton(line: str) -> str:
    """A line with its strings and description emptied: what a translation must leave alone."""
    return DESCRIPTION.sub(r"\1", QUOTED.sub('""', line))


def tracked_config_files() -> set[str]:
    listed = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "config"],
                            cwd=REPO_ROOT, check=True, capture_output=True, text=True).stdout.split()
    return {str(Path(path).relative_to("config")) for path in listed if (REPO_ROOT / path).is_file()}


def compare(name: str, stock: str, repo: str, added: set[str], translated: dict[str, str]) -> list[str]:
    failures = []
    stock_lines, repo_lines = settings(stock), settings(repo)
    # Indentation counts only as indented or not: inside a gcode template its depth doesn't matter.
    matcher = difflib.SequenceMatcher(a=stock_lines, b=repo_lines, autojunk=False)
    for tag, a0, a1, b0, b1 in matcher.get_opcodes():
        if tag == "equal":
            continue
        old = [text for _, text in stock_lines[a0:a1]]
        new = [text for _, text in repo_lines[b0:b1]]
        if tag == "insert" and all(line in added for line in new):
            continue
        if (tag == "replace" and len(old) == len(new)
                and all(translated.get(a) == b and indent_a == indent_b
                        for (indent_a, a), (indent_b, b) in zip(stock_lines[a0:a1], repo_lines[b0:b1]))):
            continue
        shown = "\n".join([*(f"    - {'  ' * indented}{line}" for indented, line in stock_lines[a0:a1]),
                           *(f"    + {'  ' * indented}{line}" for indented, line in repo_lines[b0:b1])])
        failures.append(f"config/{name}: differs from stock at line {b0 + 1} of its settings:\n{shown}")
    return failures


def main() -> int:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    stock_root = REPO_ROOT / contract["stock"]
    failures: list[str] = []

    stock_files = {str(path.relative_to(stock_root)) for path in stock_root.rglob("*") if path.is_file()}
    printer_files = set(contract["unchanged"]) | set(contract["printer_written"])
    present = tracked_config_files()
    for name in sorted(present - stock_files - printer_files):
        failures.append(f"config/{name}: not in QIDI's stock config ({contract['stock']})")
    for name in sorted((stock_files | printer_files) - present):
        failures.append(f"config/{name}: missing; config/ holds every stock file")

    for name, sha256 in sorted(contract["unchanged"].items()):
        path = CONFIG / name
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() != sha256:
            failures.append(f"config/{name}: changed; it is read-only on the printer")

    for name, pairs in contract["translated_strings"].items():
        for stock_line, repo_line in pairs:
            if skeleton(stock_line) != skeleton(repo_line):
                failures.append(f"{CONTRACT.relative_to(REPO_ROOT)}: translation for config/{name} changes more "
                                f"than a string or description:\n    - {stock_line}\n    + {repo_line}")

    for name in sorted(stock_files & present):
        failures.extend(compare(
            name,
            (stock_root / name).read_text(encoding="utf-8", errors="replace"),
            (CONFIG / name).read_text(encoding="utf-8", errors="replace"),
            set(contract["added_lines"].get(name, [])),
            dict(map(tuple, contract["translated_strings"].get(name, []))),
        ))

    if failures:
        print("config/ differs from QIDI's stock config:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        print(f"  {WHERE} A translated message string is listed in {CONTRACT.relative_to(REPO_ROOT)}.",
              file=sys.stderr)
        return 1
    print(f"config/ matches {contract['stock']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
