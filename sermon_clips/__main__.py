"""Sermon Shorts: turn each Sunday sermon into short vertical clips.

    python -m sermon_clips process [--video ID] [--redo]   (new sermons → clips on the media folder)
    python -m sermon_clips page                             (review page for GitHub Pages)
    python -m sermon_clips prune --keep 6                   (keep only the latest sermons)
    python -m sermon_clips drafts                           (Buffer drafts for clips not yet in Buffer)
    python -m sermon_clips notify [--failure TEXT]          (tell Teams which sermons are ready, or what failed)
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, time, timedelta
import json
import os
from pathlib import Path
import shutil
import sys
import time as clock
import traceback
import urllib.request

from . import buffer, picker, posts, render, review, teams, youtube
from .transcript import Transcript

ROOT = Path(__file__).resolve().parent.parent
MEDIA = Path(os.environ.get("MEDIA_DIR", ROOT / "media"))
WORK = Path(os.environ.get("WORK_DIR", ROOT / ".work"))
FAILED = WORK / "failed.json"
BASE_URL = os.environ.get("MEDIA_BASE_URL", "https://raw.githubusercontent.com/franklinis/clp-sermons/media").rstrip("/")
PAGES_URL = os.environ.get("PAGES_URL", "https://franklinis.github.io/clp-sermons/")


def church() -> dict:
    return json.loads((ROOT / "church.json").read_text(encoding="utf-8"))


def sermon_files() -> list[Path]:
    """Every processed sermon's record, newest first."""
    return sorted((MEDIA / "sermons").glob("*/sermon.json"), key=lambda p: p.parent.name, reverse=True)


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- process


def to_do(cfg: dict, video: str | None) -> list[youtube.Sermon]:
    if video:
        return [youtube.video_info(video.rsplit("v=", 1)[-1].rsplit("/", 1)[-1])]
    done = {p.parent.name for p in sermon_files()}
    feed = youtube.playlist(cfg["playlist_id"])
    new = [s for s in feed if s.date >= cfg.get("start_from", "0000") and s.slug not in done]
    return sorted(new, key=lambda s: s.date)[:1]  # one per run: rendering takes a while


def process_one(sermon: youtube.Sermon, cfg: dict, redo: bool) -> Path:
    folder = MEDIA / "sermons" / sermon.slug
    if redo and folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True, exist_ok=True)
    print(f"{sermon.date} {sermon.short_title} ({sermon.url})")

    video, captions = youtube.download(sermon, WORK / sermon.video_id)
    transcript = Transcript.load(captions)
    plan_path = folder / "plan.json"
    limits = cfg["clip_seconds"]
    if plan_path.exists():
        plan = load(plan_path)
    else:
        print("  choosing moments with Claude")
        suggestions = picker.ask_claude(transcript, sermon.title, cfg["clips_per_sermon"], limits["min"], limits["max"])
        plan = {"model": picker.MODEL, "suggestions": suggestions,
                "clips": picker.plan_clips(suggestions, transcript, limits["min"], limits["max"])}
        save(plan_path, plan)
    if not plan["clips"]:
        raise RuntimeError("No usable clips were found in this sermon")

    clips = []
    for clip in plan["clips"]:
        out = folder / f"{clip['id']}.mp4"
        if not out.exists():
            print(f"  rendering {clip['id']}: {clip['title_en']}")
            render.render(clip, video, out, cfg["footer"])
        render.poster(out, folder / f"{clip['id']}.jpg")
        clips.append({**clip, "file": out.name, "poster": f"{clip['id']}.jpg",
                      "seconds": round(sum(e - s for s, e in clip["parts"]), 1)})

    record = folder / "sermon.json"
    previous = load(record) if record.exists() else {}
    save(record, {
        "video_id": sermon.video_id, "title": sermon.title, "short_title": sermon.short_title, "url": sermon.url,
        "date": sermon.date, "folder": f"sermons/{sermon.slug}", "model": plan["model"],
        "processed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "clips": clips, "buffer": previous.get("buffer", {}), "notified": previous.get("notified", False),
    })
    shutil.rmtree(WORK / sermon.video_id, ignore_errors=True)
    return record


def cmd_process(args: argparse.Namespace) -> int:
    cfg = church()
    sermons = to_do(cfg, args.video)
    if not sermons:
        print("No new sermons in the playlist")
        return 0
    failures = []
    for sermon in sermons:
        try:
            process_one(sermon, cfg, args.redo)
        except Exception as err:  # report any failure to Teams, then fail the run
            traceback.print_exc()
            failures.append({"title": sermon.short_title, "error": f"{type(err).__name__}: {err}"[:600]})
    if failures:
        WORK.mkdir(parents=True, exist_ok=True)
        save(FAILED, {"failures": failures})
        return 1
    return 0


# ---------------------------------------------------------------- publish


def cmd_page(args: argparse.Namespace) -> int:
    MEDIA.mkdir(parents=True, exist_ok=True)
    (MEDIA / "index.html").write_text(review.page([load(p) for p in sermon_files()]), encoding="utf-8")
    (MEDIA / ".nojekyll").write_text("")
    return 0


def cmd_prune(args: argparse.Namespace) -> int:
    for path in sermon_files()[args.keep:]:
        shutil.rmtree(path.parent)
        print(f"removed {path.parent.name}")
    return 0


def wait_until_public(url: str, attempts: int = 12) -> None:
    """Buffer fetches the video when the draft is created, so it must be live first."""
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=30):
                return
        except OSError:
            clock.sleep(min(5 * (attempt + 1), 30))
    raise RuntimeError(f"{url} is not reachable")


def _channels() -> list[tuple[str, str]]:
    """Parse BUFFER_VIDEO_CHANNELS: "id:service,id:service" (service = youtube, instagram or facebook)."""
    return [tuple(c.strip().split(":", 1)) for c in os.environ.get("BUFFER_VIDEO_CHANNELS", "").split(",") if c.strip()]


def cmd_drafts(args: argparse.Namespace) -> int:
    channels = _channels()
    api_key = os.environ.get("BUFFER_API_KEY", "")
    if not channels:
        print("BUFFER_VIDEO_CHANNELS is not set, so no drafts were created")
        return 0
    if not api_key:
        print("::warning::BUFFER_API_KEY is not set, so no Buffer drafts were created")
        return 0
    cfg = church()
    post_time = time.fromisoformat(os.environ.get("POST_TIME", "18:00"))
    failed = False
    for path in sermon_files():
        sermon = load(path)
        if all(c.get("due") for c in sermon["clips"]) and all(
            f"{c['id']}:{cid}" in sermon["buffer"] for c in sermon["clips"] for cid, _ in channels
        ):
            continue
        # One clip a day from the day after it was made, skipping Sundays; kept once chosen.
        first = max(date.fromisoformat(sermon["processed_at"][:10]), date.today()) + timedelta(days=1)
        for clip, due in zip(sermon["clips"], posts.schedule(first, len(sermon["clips"]), post_time), strict=True):
            clip.setdefault("due", due.isoformat())
        for clip in sermon["clips"]:
            url = f"{BASE_URL}/{sermon['folder']}/{clip['file']}"
            assets = [{"video": {"url": url, "metadata": {"thumbnailOffset": 1500}}}]
            for channel_id, service in channels:
                key = f"{clip['id']}:{channel_id}"
                if key in sermon["buffer"]:
                    continue
                wait_until_public(url)
                try:
                    post_id = buffer.create_draft(
                        api_key, channel_id, posts.post_text(clip, sermon, cfg, service), assets,
                        buffer.video_metadata(service, posts.youtube_title(clip)), datetime.fromisoformat(clip["due"]))
                except buffer.BufferError as err:
                    print(f"{sermon['date']} {clip['id']} {service}: {err}", file=sys.stderr)
                    failed = True
                    continue
                sermon["buffer"][key] = post_id
                print(f"{sermon['date']} {clip['id']} {service}: draft {post_id}")
        save(path, sermon)
    return 1 if failed else 0


def cmd_notify(args: argparse.Namespace) -> int:
    url = os.environ.get("TEAMS_WEBHOOK_URL", "")
    failures = load(FAILED)["failures"] if FAILED.exists() else []
    if args.failure:  # reported by the Mac that makes the clips
        failures.append({"title": "Sermon Shorts on the Mac", "error": args.failure})
    if failures:
        run = (f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{os.environ.get('GITHUB_REPOSITORY', '')}"
               f"/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}")
        for failure in failures:
            print(f"failed: {failure['title']}: {failure['error']}")
            if url:
                teams.post(url, teams.failed_card(failure["title"], failure["error"], run))
    for path in sermon_files():
        sermon = load(path)
        if sermon.get("notified"):
            continue
        if url:
            poster = f"{BASE_URL}/{sermon['folder']}/{sermon['clips'][0]['poster']}" if sermon["clips"] else None
            teams.post(url, teams.ready_card(sermon, PAGES_URL, poster, bool(sermon.get("buffer"))))
            sermon["notified"] = True
            save(path, sermon)
        print(f"ready: {sermon['short_title']} ({len(sermon['clips'])} clips)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sermon_clips", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("process", help="make clips for the next new sermon")
    p.add_argument("--video", help="a YouTube video id or URL, instead of the next new sermon")
    p.add_argument("--redo", action="store_true", help="start this sermon again (asks Claude again)")
    sub.add_parser("page", help="write the review page")
    p = sub.add_parser("prune", help="keep only the latest sermons")
    p.add_argument("--keep", type=int, default=6)
    sub.add_parser("drafts", help="create Buffer drafts")
    p = sub.add_parser("notify", help="post to Teams")
    p.add_argument("--failure", help="report this error instead of (or as well as) ready sermons")
    args = parser.parse_args(argv)
    commands = {"process": cmd_process, "page": cmd_page, "prune": cmd_prune, "drafts": cmd_drafts,
                "notify": cmd_notify}
    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
