"""QIDI's virtual SD card (klipper/klippy/extras/virtual_sdcard.py) as the printer runs it: its print loop, pause,
cancel, error handling, and power-loss resume, which Valve Oil hooks only when the methods match QIDI's.

Two things are changed around it, never inside it:
- QIDI writes /home/qidi/printer_data/.cache and .temp by name; those paths lead into the test's printer_data.
- Files are read through Klipper's I/O thread (aio_executor); here they're read where they're asked for.
Each line the print loop sends is recorded with how many moves the toolhead had queued, so a test can find the moves
a line made.
"""
from __future__ import annotations

import contextlib
import glob as _glob
import importlib
import os as _os
import shutil as _shutil
import types

PRINTER_DATA = "/home/qidi/printer_data"


class _Executor:
    def submit(self, func, *args, **kwargs):
        return func(*args, **kwargs)

    def wrap_obj(self, obj):
        return obj


class AioExecutor:
    """aio_executor: runs file reads where they're asked for."""

    def allocate_executor(self, name):
        return _Executor()


class _Redirect:
    def __init__(self):
        self.root = None

    def path(self, path):
        path = str(path)
        if self.root is not None and path.startswith(PRINTER_DATA):
            return str(self.root) + path[len(PRINTER_DATA):]
        return path


_REDIRECT = _Redirect()


def _glob_module():
    module = types.ModuleType("glob")
    module.glob = lambda pattern, *args, **kwargs: _glob.glob(_REDIRECT.path(pattern), *args, **kwargs)
    return module


@contextlib.contextmanager
def _redirected_init(module):
    os_proxy = types.SimpleNamespace(**{name: getattr(_os, name) for name in dir(_os) if not name.startswith("__")})
    path_proxy = types.SimpleNamespace(**{name: getattr(_os.path, name) for name in dir(_os.path)
                                          if not name.startswith("__")})
    path_proxy.exists = lambda path: _os.path.exists(_REDIRECT.path(path))
    os_proxy.path = path_proxy
    os_proxy.makedirs = lambda path, *args, **kwargs: _os.makedirs(_REDIRECT.path(path), *args, **kwargs)
    shutil_proxy = types.SimpleNamespace(rmtree=lambda path, *args, **kwargs: _shutil.rmtree(
        _REDIRECT.path(path), *args, **kwargs))
    saved = module.os, module.shutil
    module.os, module.shutil = os_proxy, shutil_proxy
    try:
        yield
    finally:
        module.os, module.shutil = saved


class _LineRecorder:
    """The print loop's G-code object: notes where each line it sends starts and the moves queued before it."""

    def __init__(self, sdcard, gcode, toolhead):
        self._sdcard, self._gcode, self._toolhead = sdcard, gcode, toolhead
        self.lines: list[tuple[int, int]] = []

    def run_script(self, script):
        if self._sdcard.cmd_from_sd:
            self.lines.append((self._sdcard.file_position, len(self._toolhead.moves)))
        return self._gcode.run_script(script)

    def __getattr__(self, name):
        return getattr(self._gcode, name)


def load(printer, config):
    """QIDI's VirtualSD for `printer`, with its paths in the printer's printer_data."""
    module = importlib.import_module("extras.virtual_sdcard")
    module.glob = _glob_module()
    _REDIRECT.root = printer.root
    printer.objects.setdefault("aio_executor", AioExecutor())
    with _redirected_init(module):
        sdcard = module.VirtualSD(config.getsection("virtual_sdcard"))
    sdcard.cache_dir = str(printer.root / ".cache") + "/"
    sdcard.cache_tmp_dir = str(printer.root / ".temp")
    _os.makedirs(sdcard.cache_dir, exist_ok=True)
    sdcard.recorder = _LineRecorder(sdcard, sdcard.gcode, printer.toolhead)
    sdcard.gcode = sdcard.recorder
    return sdcard


def point_at(printer):
    """Lead QIDI's fixed printer_data paths into this printer's, for the module shared by every printer here."""
    _REDIRECT.root = printer.root
