"""Atomic JSON persistence shared by the scanner, WebUI and Telegram bot.

The container can be stopped, restarted or power-cycled at any moment.  Writing
an in-memory dict straight to the final path leaves a window where a reader (or
the next boot) sees a truncated file.  All state files therefore go through a
temporary file + fsync + rename, and critical readers can fall back to the
rotating ``*.bak-*`` copies that the WebUI keeps.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def atomic_write_json(path, value, *, indent: int = 2, mode: int = 0o644) -> None:
    """Publish *value* as JSON at *path* without exposing a partial file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, indent=indent)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except OSError:
            pass


def load_json(path, default=None, *, recover: bool = False):
    """Read JSON, returning *default* when the file is missing or unreadable.

    With ``recover=True`` the newest ``<name>.bak-*`` sibling is tried before
    giving up.  That keeps a corrupted ``config.json`` from taking the WebUI
    and the scanner down when a valid backup is already on disk.
    """
    path = Path(path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, ValueError, UnicodeError):
        if not recover:
            return default

    for backup in sorted(path.parent.glob(f"{path.name}.bak-*"), reverse=True):
        try:
            return json.loads(backup.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            continue
    return default
