"""Per-user UI preferences; selected storage is independent of the installation folder."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def preferences_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return base / "SanThauDataStudio" / "preferences.json"


def selected_data_root() -> Path | None:
    try:
        value = json.loads(preferences_path().read_text(encoding="utf-8"))["data_root"]
        return Path(value) if isinstance(value, str) and Path(value).is_absolute() else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_data_root(path: Path) -> None:
    target = preferences_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump({"data_root": str(path.resolve())}, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
