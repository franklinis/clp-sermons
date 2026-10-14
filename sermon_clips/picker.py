"""Ask Claude to choose the sermon's best moments and write their subtitles."""

from __future__ import annotations

import json
import os

import anthropic

from .transcript import Transcript

MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")

SYSTEM = """\
You are helping Christ the Living Petra Church, a Tamil church in Munich, turn its Sunday sermon \
into short vertical videos (YouTube Shorts, Instagram and Facebook Reels). The congregation is \
Tamil-speaking, many from Tamil Nadu, living and working in Germany; sermons mix Tamil, Tanglish \
and English.

You get the sermon's automatic YouTube captions: one caption line per row, prefixed with its start \
time in seconds. They contain recognition errors, and English words often appear spelled out in \
Tamil script (e.g. "ரெவலேஷன் சப்டர் 3" = "Revelation chapter 3").

Choose the moments that will work best as stand-alone Shorts, best first:
- A viewer who never saw the sermon must understand it. It should grab attention in the first \
three seconds and end on a complete thought, ideally a memorable line.
- Prefer stories, vivid everyday illustrations, honest personal testimony and short quotable \
truths over general exhortation. Relatable for Tamils abroad is a plus.
- Leave out worship songs, announcements, prayer for named people, private people's names, \
anything about children, and anything that mocks other faiths, cultures or people, or that would \
mislead when cut from its context.
- A clip is normally one continuous part. Use two or three parts only to skip a digression inside \
a long story; every part must be at least 8 seconds.

For every part, give the start time of the caption line where it begins plus the first three or \
four words exactly as they appear in the captions, and the start time of the caption line holding \
its last words plus those last three or four words, exactly as in the captions. The words are \
used to find the precise cut, so copy them character for character.

Subtitles ("cards") follow the speech, a short phrase at a time, each with the caption time where \
it starts:
- `ta`: what he says, in Tamil script, as spoken (colloquial is fine), with recognition errors \
corrected from context. Words he says in English are written in English (Latin script). If a \
line is spoken entirely in English, write it in `ta` and leave `en` empty.
- `en`: a natural English translation.
- Keep each card short enough to read in its time: about 2 to 6 seconds, at most about 60 \
characters of Tamil and 70 of English.
- If he quotes the Bible, put the reference in the English line, e.g. "(Matthew 26:41)". Never \
invent or complete a quotation he did not say. If a passage is too garbled to be sure what he \
said, choose a different moment rather than guess.

Also write, for each clip: a short hook title in Tamil (`title_ta`, at most 35 characters; mixing \
in English words is fine) and in English (`title_en`); a YouTube title (`youtube_title`, at most \
90 characters, Tamil hook then " | Tamil Sermon"); one or two sentences of post caption in Tamil \
and in English (no hashtags); and `why`: one sentence for the pastor on why this moment works.\
"""

PART = {
    "type": "object",
    "properties": {
        "start": {"type": "number", "description": "Start time (s) of the caption line where the part begins"},
        "first_words": {"type": "string", "description": "First 3-4 words of the part, exactly as in the captions"},
        "end": {"type": "number", "description": "Start time (s) of the caption line holding the part's last words"},
        "last_words": {"type": "string", "description": "Last 3-4 words of the part, exactly as in the captions"},
    },
    "required": ["start", "first_words", "end", "last_words"],
    "additionalProperties": False,
}
CARD = {
    "type": "object",
    "properties": {
        "start": {"type": "number", "description": "Caption time (s) where this phrase starts"},
        "ta": {"type": "string"},
        "en": {"type": "string"},
    },
    "required": ["start", "ta", "en"],
    "additionalProperties": False,
}
SCHEMA = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title_ta": {"type": "string"},
                    "title_en": {"type": "string"},
                    "youtube_title": {"type": "string"},
                    "caption_ta": {"type": "string"},
                    "caption_en": {"type": "string"},
                    "why": {"type": "string"},
                    "parts": {"type": "array", "items": PART},
                    "cards": {"type": "array", "items": CARD},
                },
                "required": ["title_ta", "title_en", "youtube_title", "caption_ta", "caption_en", "why",
                             "parts", "cards"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["clips"],
    "additionalProperties": False,
}


class PickError(Exception):
    """Claude could not return a usable plan."""


def ask_claude(transcript: Transcript, title: str, count: int, min_s: int, max_s: int) -> list[dict]:
    """Claude's raw suggestions (absolute times, as in the captions)."""
    prompt = (
        f"Sermon title: {title}\n\n"
        f"Choose the {count} best moments, each {min_s} to {max_s} seconds long in total.\n\n"
        f"<captions>\n{transcript.for_prompt()}\n</captions>"
    )
    request = {
        "model": MODEL,
        "max_tokens": 64000,
        "system": SYSTEM,
        "messages": [{"role": "user", "content": prompt}],
        "output_config": {"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
    }
    if os.environ.get("ANTHROPIC_FOUNDRY_API_KEY"):
        # Claude on Microsoft Foundry (billed through Azure): ANTHROPIC_FOUNDRY_API_KEY + ANTHROPIC_FOUNDRY_RESOURCE,
        # and CLAUDE_MODEL if the deployment has another name.
        client = anthropic.AnthropicFoundry()
    else:
        client = anthropic.Anthropic()
        # If a safety classifier declines (unlikely for sermons), let the API retry on its recommended model.
        # (The Claude API only; Foundry has no server-side fallbacks.)
        request |= {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
    with client.beta.messages.stream(**request) as stream:
        message = stream.get_final_message()

    usage = message.usage
    print(f"  Claude ({message.model}): {usage.input_tokens} tokens in, {usage.output_tokens} out")
    if message.stop_reason == "refusal":
        details = message.stop_details
        raise PickError(f"Claude declined ({details.category if details else 'no category'})")
    if message.stop_reason == "max_tokens":
        raise PickError("Claude's answer was cut off (max_tokens)")
    text = next((b.text for b in message.content if b.type == "text"), "")
    try:
        return json.loads(text)["clips"]
    except (json.JSONDecodeError, KeyError) as err:
        raise PickError(f"Unexpected answer: {text[:300]}") from err


def plan_clips(suggestions: list[dict], transcript: Transcript, min_s: int, max_s: int) -> list[dict]:
    """Exact cut points, and subtitle cards on the clip's own timeline."""
    clips = []
    for raw in suggestions:
        parts = []
        for p in raw["parts"]:
            start = transcript.cut_start(p["start"], p["first_words"])
            end = transcript.cut_end(p["end"], p["last_words"])
            if end - start >= 3:
                parts.append([round(start, 2), round(end, 2)])
        total = sum(e - s for s, e in parts)
        if not parts or not (min_s * 0.75 <= total <= max_s * 1.2):
            print(f"  skipping '{raw['title_en']}': {total:.0f}s after cutting")
            continue
        clip = {k: raw[k] for k in ("title_ta", "title_en", "youtube_title", "caption_ta", "caption_en", "why")}
        clip["id"] = f"c{len(clips) + 1}"
        clip["parts"] = parts
        clip["cards"] = _cards(raw["cards"], parts)
        clips.append(clip)
    return clips


def _to_timeline(t: float, parts: list[list[float]]) -> float:
    """Where absolute sermon time `t` lands in the clip (parts played back to back)."""
    offset = 0.0
    for s, e in parts:
        if t < e:
            return offset + max(0.0, t - s)
        offset += e - s
    return offset


def _cards(raw: list[dict], parts: list[list[float]]) -> list[list]:
    total = sum(e - s for s, e in parts)
    raw = sorted(raw, key=lambda c: c["start"])
    starts = [min(_to_timeline(c["start"], parts), total) for c in raw]
    cards: list[list] = []
    for i, c in enumerate(raw):
        a = 0.0 if i == 0 else starts[i]
        b = starts[i + 1] if i + 1 < len(raw) else total
        if b - a < 0.4 and cards:
            cards[-1][1] = round(b, 2)  # too short to read: give its time to the previous card
            continue
        cards.append([round(a, 2), round(b, 2), c["ta"].strip(), c["en"].strip()])
    return cards
