"""Find new sermons in the playlist and download them.

New uploads are found through the playlist's public RSS feed. The video and
YouTube's automatic Tamil captions are then downloaded with yt-dlp.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
import subprocess
import sys
import urllib.request
import xml.etree.ElementTree as ET

FEED = "https://www.youtube.com/feeds/videos.xml?playlist_id={}"
NS = {"atom": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
# Titles end with the sermon date, e.g. "யாரை நீ நம்புகிறாய்? - Sept 27 2026 | Tamil Church Munich".
TITLE_DATE = re.compile(r"(Jan|Feb|Mar|Apr|May|June?|July?|Aug|Sept?|Oct|Nov|Dec)[a-z]*\.? (\d{1,2}),? (\d{4})")


@dataclass
class Sermon:
    video_id: str
    title: str
    published: datetime

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"

    @property
    def date(self) -> str:
        """The sermon's date from its title, else the upload date (YYYY-MM-DD)."""
        match = TITLE_DATE.search(self.title)
        if match:
            month = match.group(1)[:3]
            try:
                return datetime.strptime(f"{month} {match.group(2)} {match.group(3)}", "%b %d %Y").strftime("%Y-%m-%d")
            except ValueError:
                pass
        return self.published.strftime("%Y-%m-%d")

    @property
    def slug(self) -> str:
        return f"{self.date}-{self.video_id}"

    @property
    def short_title(self) -> str:
        """The title without the date and channel suffix."""
        return re.split(r"\s+-\s+(?=[A-Z][a-z]+\.? \d)|\s+\|\s+", self.title)[0].strip()


def playlist(playlist_id: str) -> list[Sermon]:
    """The playlist's latest videos (up to 15), newest first."""
    request = urllib.request.Request(FEED.format(playlist_id), headers={"User-Agent": "clp-sermons"})
    with urllib.request.urlopen(request, timeout=30) as response:
        root = ET.fromstring(response.read())
    sermons = [
        Sermon(
            video_id=entry.findtext("yt:videoId", namespaces=NS),
            title=entry.findtext("atom:title", namespaces=NS),
            published=datetime.fromisoformat(entry.findtext("atom:published", namespaces=NS)),
        )
        for entry in root.findall("atom:entry", NS)
    ]
    return sorted(sermons, key=lambda s: s.published, reverse=True)


def video_info(video_id: str) -> Sermon:
    """Look up one video by id (for runs started by hand)."""
    out = subprocess.run(
        [sys.executable, "-m", "yt_dlp", "--skip-download", "--print", "%(title)s\n%(timestamp)s",
         f"https://www.youtube.com/watch?v={video_id}"],
        check=True, capture_output=True, text=True,
    ).stdout.strip().splitlines()
    return Sermon(video_id, out[0], datetime.fromtimestamp(int(out[1])))


def download(sermon: Sermon, workdir: Path) -> tuple[Path, Path]:
    """The video (1080p mp4) and its automatic Tamil captions (json3)."""
    workdir.mkdir(parents=True, exist_ok=True)
    video, captions = workdir / "sermon.mp4", workdir / "sermon.ta-orig.json3"
    if not (video.exists() and captions.exists()):
        subprocess.run(
            [sys.executable, "-m", "yt_dlp", "--no-progress", "--no-playlist",
             "-f", "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080]", "--merge-output-format", "mp4",
             "--write-auto-subs", "--sub-langs", "ta-orig", "--sub-format", "json3",
             "-o", str(workdir / "sermon.%(ext)s"), sermon.url],
            check=True,
        )
    if not captions.exists():
        raise RuntimeError(f"YouTube has no automatic Tamil captions for {sermon.url} yet; try again later")
    return video, captions
