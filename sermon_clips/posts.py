"""Post text for each platform and when each clip goes out."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TIMEZONE = ZoneInfo("Europe/Berlin")


def post_text(clip: dict, sermon: dict, church: dict, service: str) -> str:
    """Hook in Tamil and English, the full sermon link, the Sunday invitation and hashtags."""
    full = church["full_sermon"]
    invitation = church["invitation"]
    lines = [clip["caption_ta"].strip(), clip["caption_en"].strip(), "",
             f"▶️ {full['ta']} / {full['en']}: {sermon['url']}", "",
             f"⛪ {invitation['ta']}", invitation["en"], church["website"], "",
             " ".join(church["hashtags"].get(service, []))]
    return "\n".join(lines).strip()


def youtube_title(clip: dict) -> str:
    title = " ".join(clip["youtube_title"].split())
    return title if len(title) <= 100 else title[:99].rstrip() + "…"


def schedule(first: date, count: int, at: time, skip_weekdays: tuple[int, ...] = (6,)) -> list[datetime]:
    """One clip a day from `first`, skipping Sundays (the sermon itself goes out then)."""
    times, day = [], first
    while len(times) < count:
        if day.weekday() not in skip_weekdays:
            times.append(datetime.combine(day, at, TIMEZONE))
        day += timedelta(days=1)
    return times
