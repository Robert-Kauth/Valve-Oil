from __future__ import annotations

import errno
import fcntl
from pathlib import Path

from .errors import LockAcquisitionError


class AdvisoryLock:
    def __init__(self, path: Path, *, shared: bool = False):
        self._path = path
        self._shared = shared
        self._handle = None

    def __enter__(self) -> "AdvisoryLock":
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self._path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self._handle.fileno(), (fcntl.LOCK_SH if self._shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except OSError as exc:
            self._handle.close()
            self._handle = None
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                raise LockAcquisitionError("Another installer run is already in progress.") from exc
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
    """For a run that only reads: it keeps installer runs out but lets Klipper's vo_print_guard admit prints."""
    return AdvisoryLock(path, shared=True)
