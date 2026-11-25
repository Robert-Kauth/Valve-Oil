from __future__ import annotations

import errno
import fcntl
import os
from enum import Enum
from pathlib import Path

from .errors import LockAcquisitionError


class LockMode(Enum):
    SHARED = fcntl.LOCK_SH
    EXCLUSIVE = fcntl.LOCK_EX


class AdvisoryLock:
    def __init__(self, path: Path, mode: LockMode = LockMode.EXCLUSIVE):
        self._path = path
        self._mode = mode
        self._handle = None

    def __enter__(self) -> "AdvisoryLock":
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._mode is LockMode.SHARED:
            self._handle = os.fdopen(os.open(self._path, os.O_RDONLY | os.O_CREAT, 0o644), "r")
        else:
            self._handle = self._path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self._handle.fileno(), self._mode.value | fcntl.LOCK_NB)
        except OSError as exc:
            self._handle.close()
            self._handle = None
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                raise LockAcquisitionError("Another printer operation is already in progress.") from exc
            raise
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._handle is None:
            return
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None


def acquire(path: Path) -> AdvisoryLock:
    return AdvisoryLock(path)


def acquire_shared(path: Path) -> AdvisoryLock:
    """Serialize a read-only check against mutations without blocking print admission."""
    return AdvisoryLock(path, LockMode.SHARED)
