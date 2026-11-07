"""Read and write the desktop app's .env file (KEY=VALUE lines), without a new dependency.

Docker loads .env through Compose; the desktop app has no Compose, so it reads its own .env
(in the user data root, see app_paths) at startup and the Settings screen saves the Gemini
key here. CLAUDE.md rule 7: values are never logged, printed or returned in error messages.

Format: blank lines and lines starting with # are ignored; an optional `export ` prefix is
accepted; a value may be wrapped in double quotes (with \\" and \\\\ escapes) or single
quotes (taken literally). Writes are atomic, and the new file is readable by its owner only
(tempfile.mkstemp creates it with mode 0600 on macOS and Linux).
"""

import os
import re
from collections.abc import MutableMapping
from pathlib import Path

from jobmatch.storage.files import atomic_write_text

_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PLAIN = re.compile(r"^[A-Za-z0-9_.:/+=@-]*$")  # written without quotes


class EnvFileError(ValueError):
    pass


def _parse_value(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        out, i, body = [], 0, raw[1:-1]
        while i < len(body):
            if body[i] == "\\" and i + 1 < len(body) and body[i + 1] in '"\\':
                out.append(body[i + 1])
                i += 2
            else:
                out.append(body[i])
                i += 1
        return "".join(out)
    if len(raw) >= 2 and raw[0] == raw[-1] == "'":
        return raw[1:-1]
    return raw.split(" #", 1)[0].strip()  # unquoted: an inline " # comment" is dropped


def _split(line: str) -> tuple[str, str] | None:
    text = line.strip()
    if not text or text.startswith("#"):
        return None
    if text.startswith("export "):
        text = text[len("export ") :].lstrip()
    key, sep, value = text.partition("=")
    key = key.strip()
    if not sep or not _KEY.match(key):
        return None  # not a KEY=VALUE line: ignored, like Compose does
    return key, value


def read_env_file(path: Path) -> dict[str, str]:
    """Every KEY=VALUE in the file (later lines win). Missing file = empty."""
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        parsed = _split(line)
        if parsed:
            values[parsed[0]] = _parse_value(parsed[1])
    return values


def load_into_environ(
    path: Path, environ: MutableMapping[str, str] | None = None, *, override: bool = False
) -> list[str]:
    """Put the file's values into the environment; returns the names set (never values).
    Existing variables win unless override=True."""
    environ = os.environ if environ is None else environ
    names: list[str] = []
    for key, value in read_env_file(path).items():
        if override or key not in environ:
            environ[key] = value
            names.append(key)
    return names


def _format_value(value: str) -> str:
    if _PLAIN.match(value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def set_value(path: Path, key: str, value: str) -> None:
    """Add or replace KEY in the file, keeping every other line (comments included)."""
    if not _KEY.match(key):
        raise EnvFileError(f"not a valid variable name: {key!r}")
    if "\n" in value or "\r" in value:
        raise EnvFileError(f"{key}: the value must be on one line")
    line = f"{key}={_format_value(value)}"
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    out: list[str] = []
    replaced = False
    for existing in lines:
        parsed = _split(existing)
        if parsed and parsed[0] == key:
            if not replaced:
                out.append(line)
                replaced = True
            continue  # drop duplicates of the key
        out.append(existing)
    if not replaced:
        out.append(line)
    atomic_write_text(path, "\n".join(out) + "\n")


def remove_value(path: Path, key: str) -> bool:
    """Delete KEY from the file. Returns whether it was there."""
    if not path.exists():
        return False
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    kept = [ln for ln in lines if (p := _split(ln)) is None or p[0] != key]
    if len(kept) == len(lines):
        return False
    atomic_write_text(path, "\n".join(kept) + ("\n" if kept else ""))
    return True
