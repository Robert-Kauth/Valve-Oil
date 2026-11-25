#!/usr/bin/env python3
"""Run the G-code path checker and the installer's tests, one test module per worker process.

Every Klipper module under installer/klipper/extras/ needs a test of what it must and must not do on the printer;
one that no test module names fails the run.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import logging
import multiprocessing
import os
import re
import subprocess
import sys
import traceback
import unittest
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

TESTS_ROOT = REPO_ROOT / "installer/tests"
REQUIREMENTS = TESTS_ROOT / "requirements.txt"
TEST_VENV = REPO_ROOT / "build/test-venv"
EXTRAS = REPO_ROOT / "installer/klipper/extras"


def test_modules() -> list[str]:
    return sorted(
        ".".join(path.relative_to(REPO_ROOT).with_suffix("").parts)
        for path in TESTS_ROOT.glob("*/test_*.py")
    )


def untested_extras() -> list[str]:
    """Klipper modules (those Klipper loads with load_config) that no test module names."""
    tests = "\n".join(path.read_text(encoding="utf-8") for path in TESTS_ROOT.rglob("test_*.py"))
    return [path.name for path in sorted(EXTRAS.glob("*.py"))
            if re.search(r"^def load_config(?:_prefix)?\(", path.read_text(encoding="utf-8"), re.MULTILINE)
            and not re.search(rf"\b{re.escape(path.stem)}\b", tests)]


class _CurrentStderr(logging.StreamHandler):
    """Logs to whatever sys.stderr is now, so each test's log lines land in that test's captured output."""

    @property
    def stream(self):
        return sys.stderr

    @stream.setter
    def stream(self, value):
        pass


def run_module(module: str) -> tuple[str, int, bool, str]:
    """Run one module's tests. What passing tests print or log is dropped; a failing test's is in its report."""
    root = logging.getLogger()
    if not any(isinstance(handler, _CurrentStderr) for handler in root.handlers):
        handler = _CurrentStderr()
        handler.setFormatter(logging.Formatter(logging.BASIC_FORMAT))
        root.addHandler(handler)
    stream = io.StringIO()
    try:
        suite = unittest.defaultTestLoader.loadTestsFromName(module)
    except Exception:  # a module that cannot be imported fails the run like a failing test
        return module, 0, False, traceback.format_exc()
    # unittest also copies a failing test's captured output to the real stdout and stderr; the report has it already.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        result = unittest.TextTestRunner(stream=stream, verbosity=1, buffer=True).run(suite)
    return module, result.testsRun, result.wasSuccessful(), stream.getvalue()


def ensure_requirements(argv: list[str]) -> None:
    """Run under a Python with installer/tests/requirements.txt: this one, or build/test-venv, made on first use."""
    try:
        import greenlet  # noqa: F401
        import jinja2  # noqa: F401
        return
    except ImportError:
        pass
    python = TEST_VENV / "bin/python"
    if Path(sys.prefix).resolve() == TEST_VENV.resolve():
        raise SystemExit(f"{TEST_VENV} lacks {REQUIREMENTS.relative_to(REPO_ROOT)}; remove it and run again")
    if not python.exists():
        print(f"Setting up {TEST_VENV.relative_to(REPO_ROOT)} for the G-code flow tests", file=sys.stderr)
        subprocess.run([sys.executable, "-m", "venv", str(TEST_VENV)], check=True)
    if subprocess.run([str(python), "-c", "import greenlet, jinja2"], capture_output=True).returncode:
        subprocess.run([str(python), "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                        "-r", str(REQUIREMENTS)], check=True)
    os.execv(str(python), [str(python), str(Path(__file__).resolve()), *argv])


def main(argv: list[str] | None = None) -> int:
    ensure_requirements(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 1),
                        help="worker processes (default: one per CPU, at most 8)")
    args = parser.parse_args(argv)

    path_check = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts/check_gcode_paths.py")],
        cwd=REPO_ROOT,
        check=False,
    )
    if path_check.returncode != 0:
        return path_check.returncode
    untested = untested_extras()
    if untested:
        print(f"No test names these Klipper modules in {EXTRAS.relative_to(REPO_ROOT)}: {', '.join(untested)}; "
              "add one under installer/tests/ for what each must and must not do on the printer", file=sys.stderr)
        return 1

    os.chdir(REPO_ROOT)
    modules = test_modules()
    # Spawned workers exit as any Python process does, so tests' temporary folders, removed at exit, are removed.
    # Forked ones, Linux's default, exit without that.
    with ProcessPoolExecutor(max(1, args.jobs), mp_context=multiprocessing.get_context("spawn")) as pool:
        results = list(pool.map(run_module, modules))

    failed = [result for result in results if not result[2]]
    for module, _, _, output in failed:
        print(f"{module}\n{output}", file=sys.stderr)
    tests = sum(result[1] for result in results)
    if failed:
        print(f"FAILED: {len(failed)} of {len(modules)} test modules ({tests} tests run)", file=sys.stderr)
        return 1
    print(f"OK: {tests} tests in {len(modules)} modules")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
