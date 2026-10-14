"""Tell the church's Teams channel when a sermon's Shorts are ready to review."""

from __future__ import annotations

import json
import urllib.request


def _text(text: str, **style: object) -> dict:
    return {"type": "TextBlock", "text": text, "wrap": True, **style}


def ready_card(sermon: dict, review_url: str, poster_url: str | None, in_buffer: bool) -> dict:
    clips = "\n".join(f"- {c['title_ta']} ({c['seconds']:.0f}s)" for c in sermon["clips"])
    where = "They're in Buffer as drafts: approve the ones you want." if in_buffer else "Watch them on the review page."
    body = [_text(f"🎬 {len(sermon['clips'])} Shorts from “{sermon['short_title']}”", weight="Bolder", size="Medium"),
            _text(f"Sermon of {sermon['date']}. {where}", spacing="Small")]
    if poster_url:
        body.append({"type": "Image", "url": poster_url, "size": "Medium", "altText": sermon["clips"][0]["title_en"]})
    body.append(_text(clips, spacing="Small"))
    actions = [{"type": "Action.OpenUrl", "title": "Watch the clips", "url": review_url}]
    if in_buffer:
        actions.append({"type": "Action.OpenUrl", "title": "Open Buffer", "url": "https://publish.buffer.com"})
    content = {"type": "AdaptiveCard", "version": "1.4", "body": body, "actions": actions,
               "$schema": "http://adaptivecards.io/schemas/adaptive-card.json"}
    return {"type": "message",
            "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "content": content}]}


def failed_card(title: str, error: str, run_url: str) -> dict:
    content = {"type": "AdaptiveCard", "version": "1.4",
               "body": [_text(f"⚠️ Sermon Shorts failed: {title}", weight="Bolder"), _text(error, spacing="Small")],
               "actions": [{"type": "Action.OpenUrl", "title": "Open the run", "url": run_url}],
               "$schema": "http://adaptivecards.io/schemas/adaptive-card.json"}
    return {"type": "message",
            "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "content": content}]}


def post(url: str, message: dict) -> None:
    request = urllib.request.Request(
        url, data=json.dumps(message).encode(), headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=30):
        pass
