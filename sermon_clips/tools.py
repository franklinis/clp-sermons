"""The programs the clips need, found the same way on macOS, Windows and Linux.

ffmpeg and Deno come from pip packages (imageio-ffmpeg, deno), so the only things
to install by hand are Python, Git and Chrome (or Microsoft Edge on Windows).
"""

from __future__ import annotations

from functools import cache
import os
from pathlib import Path
import shutil


@cache
def ffmpeg() -> str:
    """ffmpeg: the pip-installed build (same filters everywhere), or one on PATH."""
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError):
        found = shutil.which("ffmpeg")
        if not found:
            raise RuntimeError("ffmpeg not found: pip install imageio-ffmpeg") from None
        return found


@cache
def deno() -> str | None:
    """Deno, which yt-dlp needs for YouTube."""
    try:
        import deno as deno_package

        return deno_package.find_deno_bin()
    except (ImportError, FileNotFoundError, RuntimeError):
        return shutil.which("deno")


@cache
def chrome() -> str:
    """Chrome (or Chromium / Edge) for drawing the text: $CHROME first, then the usual installs."""
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        os.environ.get("CHROME"),
        shutil.which("google-chrome"), shutil.which("google-chrome-stable"), shutil.which("chromium"),
        shutil.which("chrome"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        rf"{local}\Google\Chrome\Application\chrome.exe" if local else None,
        # Edge is Chromium and comes with every Windows; its headless mode draws the same.
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    raise RuntimeError("Chrome not found: install Google Chrome, or set the CHROME environment variable")
