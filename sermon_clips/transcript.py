"""YouTube's automatic captions as timed words and lines."""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
import json
from pathlib import Path


@dataclass
class Transcript:
    words: list[tuple[float, str]]  # (start second, word), in order
    lines: list[tuple[float, str]]  # (start second, caption line)

    @classmethod
    def load(cls, json3: Path) -> Transcript:
        data = json.loads(json3.read_text(encoding="utf-8"))
        words, lines = [], []
        for event in data.get("events", []):
            segs = event.get("segs")
            if not segs:
                continue
            start = event["tStartMs"] / 1000
            text = " ".join("".join(s.get("utf8", "") for s in segs).split())
            if text:
                lines.append((start, text))
            for seg in segs:
                word = seg.get("utf8", "").strip()
                if word:
                    words.append((start + seg.get("tOffsetMs", 0) / 1000, word))
        words.sort(key=lambda w: w[0])
        return cls(words, lines)

    @property
    def duration(self) -> float:
        return self.words[-1][0] + 1 if self.words else 0.0

    def for_prompt(self) -> str:
        """One caption line per row, prefixed with its start time in seconds."""
        return "\n".join(f"[{start:.1f}] {text}" for start, text in self.lines)

    def _find(self, near: float, phrase: str, last: bool) -> int | None:
        """Index of the first (or last) word of `phrase` spoken close to `near` seconds."""
        tokens = [_norm(t) for t in phrase.split() if _norm(t)]
        if not tokens:
            return None
        n = len(tokens)
        best = None
        for i in range(len(self.words) - n + 1):
            t = self.words[i][0]
            if t < near - 10 or t > near + 25:
                continue
            closer = best is None or abs(t - near) < abs(self.words[best][0] - near)
            if closer and [_norm(w) for _, w in self.words[i:i + n]] == tokens:
                best = i
        if best is None and n > 1:  # captions sometimes merge or split words: retry with fewer
            return self._find(near, " ".join(phrase.split()[1:] if last else phrase.split()[:-1]), last)
        return best + n - 1 if (best is not None and last) else best

    def cut_start(self, near: float, first_words: str) -> float:
        """Just before the clip's first word."""
        i = self._find(near, first_words, last=False)
        if i is None:
            times = [w[0] for w in self.words]
            i = min(bisect_left(times, near - 0.25), len(times) - 1)
        return max(0.0, self.words[i][0] - 0.12)

    def cut_end(self, near: float, last_words: str) -> float:
        """Just before the word after the clip's last word, so the last word is not cut off."""
        i = self._find(near, last_words, last=True)
        if i is None:
            times = [w[0] for w in self.words]
            i = max(0, bisect_left(times, near) - 1)
        if i + 1 >= len(self.words):
            return self.words[i][0] + 1.0
        nxt = self.words[i + 1][0]
        return min(nxt - 0.08, self.words[i][0] + 1.6)  # not a long pause before the next sentence


def _norm(word: str) -> str:
    return "".join(ch for ch in word.lower() if ch.isalnum() or "\u0b80" <= ch <= "\u0bff")
