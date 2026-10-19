"""Atomic file writes: temp file in the same folder, fsync, then os.replace."""

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import IO, Any


def fsync_dir(directory: Path) -> None:
    """Persist a rename on POSIX. Windows has no directory fsync, so skip it there."""
    if os.name == "nt":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def atomic_open(path: Path, *, binary: bool = False, encoding: str = "utf-8") -> Iterator[IO[Any]]:
    """Yield a file handle whose content replaces `path` only if the block finishes cleanly."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        if binary:
            handle = os.fdopen(fd, "wb")
        else:
            handle = os.fdopen(fd, "w", encoding=encoding, newline="")
        with handle:
            yield handle
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(tmp_name)
        raise
    fsync_dir(path.parent)


def atomic_write_text(path: Path, text: str) -> None:
    with atomic_open(path) as f:
        f.write(text)
