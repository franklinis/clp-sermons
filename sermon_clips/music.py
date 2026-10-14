"""Original background music, composed in code (shared with clp-daily-verse).

Because the church owns it outright, it can never be claimed or muted by
YouTube's or Meta's copyright matching, which can happen with "royalty-free"
library tracks. The seed picks the key, chord progression and sparse
piano-like notes, so re-rendering a clip sounds the same.
"""

from __future__ import annotations

from array import array
from datetime import date
import math
from pathlib import Path
import random
import wave

RATE = 22050


# Scale degrees (semitones above the key) for the chords of a major key.
CHORDS = {"I": (0, 4, 7), "ii": (2, 5, 9), "IV": (5, 9, 12), "V": (7, 11, 14), "vi": (9, 12, 16)}
PROGRESSIONS = [
    ("I", "vi", "IV", "V"),
    ("I", "IV", "vi", "V"),
    ("vi", "IV", "I", "V"),
    ("I", "V", "vi", "IV"),
    ("IV", "I", "ii", "V"),
]
KEYS = {"C": 48, "D": 50, "E♭": 51, "F": 53, "G": 55, "A♭": 56, "A": 57}  # MIDI note of the key's root
PENTATONIC = (0, 2, 4, 7, 9)  # always consonant over these chords


def _freq(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def _add_pad(buf: array, start: float, length: float, notes: list[int], gain: float) -> None:
    """A soft chord: slightly detuned sines that swell in and fade out."""
    attack = release = min(1.6, length / 2)
    first, last = int(start * RATE), min(len(buf), int((start + length) * RATE))
    oscillators = []
    for n in notes:
        f = _freq(n)
        weight = 0.55 if f < 140 else 1.0  # keep the bass soft on phone speakers
        oscillators += [(f * 0.99965, weight), (f * 1.00035, weight), (f * 2, 0.15 * weight)]
    scale = gain / sum(w for _, w in oscillators)
    steps = [(2 * math.pi * f / RATE, w * scale) for f, w in oscillators]
    for i in range(first, last):
        t = (i - first) / RATE
        env = min(1.0, t / attack, (length - t) / release)
        env = math.sin(env * math.pi / 2) ** 2
        buf[i] += env * sum(w * math.sin(step * i) for step, w in steps)


def _add_note(buf: array, start: float, midi: int, gain: float) -> None:
    """A piano-like note: quick attack, gentle exponential decay, a few harmonics."""
    f = _freq(midi)
    first = int(start * RATE)
    span = 5.0
    last = min(len(buf), first + int(span * RATE))
    w1, w2, w3 = (2 * math.pi * f * k / RATE for k in (1, 2, 3))
    for i in range(first, last):
        t = (i - first) / RATE
        env = min(1.0, t / 0.008, (span - t) / 0.3) * math.exp(-t / 0.9)
        k = i - first
        buf[i] += gain * env * (math.sin(w1 * k) + 0.35 * math.exp(-t / 0.4) * math.sin(w2 * k)
                                + 0.12 * math.exp(-t / 0.25) * math.sin(w3 * k))


def compose(day: date, seconds: float, out: Path) -> Path:
    """Write a mono WAV of `seconds` length for `day`; the video step adds reverb and stereo."""
    rng = random.Random(day.toordinal() * 7919)
    key = KEYS[rng.choice(sorted(KEYS))]
    progression = rng.choice(PROGRESSIONS)
    chord_len = rng.uniform(4.0, 5.5)
    buf = array("d", [0.0]) * int(seconds * RATE + RATE)

    t, index = 0.0, 0
    while t < seconds:
        degrees = CHORDS[progression[index % len(progression)]]
        root = key + degrees[0] - 12
        voicing = [root, root + 7] + [key + d for d in degrees]
        # Overlap chords so each one swells in while the last fades away.
        _add_pad(buf, max(0.0, t - 0.8), chord_len + 1.6, voicing, 0.5)
        t += chord_len
        index += 1

    beat = rng.uniform(0.55, 0.75)
    t = rng.uniform(0.8, 1.6)
    while t < seconds - 2.5:
        if rng.random() < 0.45:
            octave = rng.choice((12, 24))
            _add_note(buf, t, key + octave + rng.choice(PENTATONIC), rng.uniform(0.13, 0.2))
        t += beat * rng.choice((1, 1, 2))

    peak = max(abs(x) for x in buf) or 1.0
    samples = array("h", (int(32767 * 0.85 * x / peak) for x in buf[: int(seconds * RATE)]))
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(samples.tobytes())
    return out
