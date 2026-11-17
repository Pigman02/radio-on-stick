from __future__ import annotations

import os
import platform
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any


def resolve_ffmpeg(path: str = "ffmpeg") -> str:
    """Return an executable path, or an empty string when ffmpeg is absent."""
    value = str(path or "ffmpeg").strip() or "ffmpeg"
    candidate = Path(value).expanduser()
    if candidate.is_file():
        return str(candidate)
    if os.path.isabs(value):
        return ""
    found = shutil.which(value)
    if found:
        return found
    # Package managers sometimes install outside the service PATH, notably
    # Homebrew on macOS and WinGet's package directory.
    if platform.system() == "Darwin":
        for item in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
            if Path(item).is_file():
                return item
    elif platform.system() == "Windows":
        for item in (
            r"C:\ffmpeg\bin\ffmpeg.exe",
            str(Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Packages"),
        ):
            if item.lower().endswith(".exe") and Path(item).is_file():
                return item
    else:
        for item in ("/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/snap/bin/ffmpeg"):
            if Path(item).is_file():
                return item
    return ""


@lru_cache(maxsize=8)
def _ffmpeg_version_cached(executable: str, timeout: float) -> str:
    try:
        result = subprocess.run(
            [executable, "-version"],
            capture_output=True,
            check=False,
            timeout=timeout,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode != 0:
        return ""
    first = (result.stdout or "").splitlines()
    return first[0].strip() if first else ""


def ffmpeg_version(path: str = "ffmpeg", timeout: float = 4.0) -> str:
    executable = resolve_ffmpeg(path)
    if not executable:
        return ""
    return _ffmpeg_version_cached(executable, float(timeout))


def ffmpeg_status(path: str = "ffmpeg") -> dict[str, Any]:
    executable = resolve_ffmpeg(path)
    version = ffmpeg_version(executable) if executable else ""
    return {
        "available": bool(executable and version),
        "path": executable,
        "version": version,
    }
