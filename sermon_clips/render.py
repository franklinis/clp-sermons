"""Render one clip as a 1080×1920 Short.

Layout: a hook title at the top, the sermon video in a 4:5 window that follows
the preacher, Tamil and English subtitles over the lower part of the video,
and the church name below. Text is drawn by headless Chrome (correct Tamil
shaping), the video is composited frame by frame with OpenCV, and ffmpeg
cleans the voice and lays a soft original music bed underneath.
"""

from __future__ import annotations

from datetime import date
import html
import json
import os
from pathlib import Path
import shutil
import subprocess

import cv2
import numpy as np

from . import music

ROOT = Path(__file__).resolve().parent.parent
LOGO = ROOT / "assets" / "logo-white.png"

W, H, FPS = 1080, 1920, 30
VID_W, VID_H, VID_Y = 1080, 1350, 300   # the 4:5 video window
DEADZONE, FOLLOW = 90, 0.06             # virtual camera: hold still, then ease toward the face

FONTS = ('<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+Tamil:wght@600;800'
         '&family=Roboto:wght@500;700;900&display=block" rel="stylesheet">')


def find_chrome() -> str:
    for candidate in (os.environ.get("CHROME"), shutil.which("google-chrome"), shutil.which("google-chrome-stable"),
                      shutil.which("chromium"), "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"):
        if candidate and Path(candidate).exists():
            return candidate
    raise RuntimeError("Chrome not found; set the CHROME environment variable")


def esc(text: str) -> str:
    """HTML-escape, keeping mixed words like "ICU-ல" on one line."""
    return html.escape(text).replace("-", "‑")


def frame_html(title_ta: str, title_en: str, footer: str) -> str:
    return f"""<!doctype html><meta charset="utf-8">{FONTS}<style>
html,body{{margin:0;width:{W}px;height:{H}px;overflow:hidden}}
body{{background:linear-gradient(180deg,#0a1433 0%,#16245c 45%,#2a1450 100%);color:#fff;font-family:Roboto,sans-serif}}
.title{{position:absolute;top:0;height:{VID_Y}px;left:40px;right:40px;display:flex;flex-direction:column;
  justify-content:center;text-align:center}}
.ta{{font-family:'Noto Sans Tamil',Roboto;font-weight:800;font-size:68px;line-height:1.22}}
.en{{margin-top:8px;font-size:40px;font-weight:700;color:#ffd76a}}
.foot{{position:absolute;top:{VID_Y + VID_H + 26}px;left:0;right:0;display:flex;gap:16px;align-items:center;
  justify-content:center;font-size:28px;font-weight:500;letter-spacing:.05em;opacity:.92}}
.foot img{{height:58px}}
</style><body>
<div class="title"><div class="ta">{esc(title_ta)}</div><div class="en">{esc(title_en)}</div></div>
<div class="foot"><img src="{LOGO.as_uri()}">{esc(footer)}</div>"""


def card_html(ta: str, en: str) -> str:
    en_div = f'<div class="en">{esc(en)}</div>' if en else ""
    return f"""<!doctype html><meta charset="utf-8">{FONTS}<style>
html,body{{margin:0;width:{W}px;height:{H}px;overflow:hidden;background:transparent}}
.box{{position:absolute;left:24px;right:24px;bottom:{H - (VID_Y + VID_H - 50)}px;display:flex;justify-content:center}}
.in{{background:rgba(6,10,28,.80);border-radius:24px;padding:20px 28px 22px;text-align:center;max-width:1032px}}
.ta{{font-family:'Noto Sans Tamil',Roboto;font-weight:800;font-size:50px;line-height:1.32;color:#fff;
  text-shadow:0 2px 8px rgba(0,0,0,.45)}}
.en{{margin-top:10px;font-family:Roboto;font-weight:700;font-size:36px;line-height:1.28;color:#ffd76a}}
</style><body><div class="box"><div class="in"><div class="ta">{esc(ta)}</div>{en_div}</div></div>"""


def chrome_png(page_html: str, out: Path, transparent: bool) -> None:
    page = out.with_suffix(".html")
    page.write_text(page_html, encoding="utf-8")
    args = [find_chrome(), "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-sandbox",
            f"--user-data-dir={out.parent / 'chrome-profile'}", "--allow-file-access-from-files",
            "--force-device-scale-factor=1", f"--window-size={W},{H}", "--virtual-time-budget=8000",
            f"--screenshot={out}"]
    if transparent:
        args.append("--default-background-color=00000000")
    subprocess.run([*args, page.as_uri()], check=True, capture_output=True, timeout=120)


def load_rgba(path: Path):
    img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    ys, xs = np.where(img[:, :, 3] > 0)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    part = img[y0:y1, x0:x1].astype(np.float32)
    return (y0, y1, x0, x1), part[:, :, :3], part[:, :, 3:] / 255.0


def video_size(src: Path) -> tuple[int, int]:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                          "-of", "json", str(src)], check=True, capture_output=True, text=True).stdout
    stream = json.loads(out)["streams"][0]
    return stream["width"], stream["height"]


def frames(src: Path, start: float, dur: float, fps: float, w: int, h: int):
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-ss", f"{start}", "-t", f"{dur}", "-i", str(src),
                             "-vf", f"fps={fps},scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
                            stdout=subprocess.PIPE)
    size = w * h * 3
    while True:
        buf = proc.stdout.read(size)
        if len(buf) < size:
            break
        yield np.frombuffer(buf, np.uint8).reshape(h, w, 3)
    proc.wait()


def track(src: Path, start: float, dur: float, src_w: int, src_h: int, crop_w: int) -> np.ndarray:
    """Crop x (source pixels) for every output frame, following the largest face."""
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    det_fps, scale = 5, max(1, src_w // 640)
    xs = []
    for img in frames(src, start, dur, det_fps, src_w // scale, src_h // scale):
        faces = cascade.detectMultiScale(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), 1.1, 5, minSize=(30, 30))
        if len(faces):
            fx, _, fw, _ = max(faces, key=lambda f: f[2] * f[3])
            xs.append((fx + fw / 2) * scale)
        else:
            xs.append(np.nan)
    xs = np.array(xs, float)
    if not np.isfinite(xs).any():
        xs[:] = src_w / 2
    # Drop one-off jumps (a face in the backdrop), then fill gaps from neighbours.
    med = np.array([np.nanmedian(xs[max(0, i - 3):i + 4]) for i in range(len(xs))])
    xs[np.abs(xs - med) > 250] = np.nan
    idx = np.arange(len(xs))
    ok = np.isfinite(xs)
    xs = np.interp(idx, idx[ok], xs[ok])
    n = int(round(dur * FPS))
    target = np.interp(np.arange(n) / FPS * det_fps, idx, xs)
    cam = np.empty(n)
    c = target[:det_fps * 2].mean() if n else src_w / 2
    for i, t in enumerate(target):
        d = t - c
        if abs(d) > DEADZONE:
            c += FOLLOW * (d - np.sign(d) * DEADZONE)
        cam[i] = c
    return np.clip(cam - crop_w / 2, 0, src_w - crop_w).astype(int)


def audio_graph(parts: list, dur: float, music_input: int | None) -> str:
    """Join the parts, clean the voice, then lay a quiet music bed under it that dips while he speaks."""
    g = [f"[{i + 1}:a]aresample=48000,aformat=channel_layouts=stereo,"
         f"afade=t=in:d=0.06,afade=t=out:st={e - s - 0.08:.2f}:d=0.08[p{i}]" for i, (s, e) in enumerate(parts)]
    g.append("".join(f"[p{i}]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=0:a=1[sp]")
    # Church recordings are quiet (-30 to -34 LUFS): remove hiss and rumble *before* raising the level.
    g.append("[sp]highpass=f=85,lowpass=f=9000,afftdn=nr=14:nf=-42:tn=1,"
             "acompressor=threshold=0.08:ratio=3:attack=10:release=250,loudnorm=I=-18:TP=-3:LRA=9[vo]")
    tail = f"loudnorm=I=-16:TP=-1.5:LRA=9,aresample=48000,afade=t=in:d=0.15,afade=t=out:st={dur - 0.4:.2f}:d=0.4[aout]"
    if music_input is None:
        g.append(f"[vo]{tail}")
        return ";".join(g)
    g += [
        f"[{music_input}:a]atrim=0:{dur:.2f},aresample=48000,aformat=channel_layouts=stereo,haas,"
        "aecho=0.8:0.55:80|190|370:0.32|0.22|0.12,highpass=f=70,lowpass=f=7000,"
        f"afade=t=in:st=0:d=2,afade=t=out:st={max(0.0, dur - 3):.2f}:d=3,loudnorm=I=-31:TP=-9:LRA=7,aresample=48000[bed]",
        "[vo]asplit[v1][v2]",
        "[bed][v1]sidechaincompress=threshold=0.03:ratio=4:attack=80:release=1500[ducked]",
        f"[ducked][v2]amix=inputs=2:normalize=0,atrim=0:{dur:.2f},{tail}",
    ]
    return ";".join(g)


def music_seed(key: str) -> date:
    """A stable date per clip, so each clip gets its own key and progression."""
    return date.fromordinal(739000 + sum(map(ord, key)) * 7 % 3000)


def render(clip: dict, src: Path, out: Path, footer: str, with_music: bool = True) -> Path:
    parts = clip["parts"]
    dur = sum(e - s for s, e in parts)
    work = out.parent / f".work-{out.stem}"
    work.mkdir(parents=True, exist_ok=True)
    src_w, src_h = video_size(src)
    crop_w = min(src_w, round(src_h * 4 / 5))

    for _ in range(3):
        chrome_png(frame_html(clip["title_ta"], clip["title_en"], footer), work / "frame.png", transparent=False)
        base = cv2.imread(str(work / "frame.png"))[:H, :W]
        # Headless Chrome occasionally leaves a white box in the bottom corner; the footer there is always dark.
        if base[H - 200:, W - 120:].min(axis=2).max() < 200:
            break
    cards = []
    for i, (a, b, ta, en) in enumerate(clip["cards"]):
        p = work / f"card{i:02d}.png"
        chrome_png(card_html(ta, en), p, transparent=True)
        cards.append((a, b, load_rgba(p)))

    xs = np.concatenate([track(src, s, e - s, src_w, src_h, crop_w) for s, e in parts])
    inputs: list[str] = []
    for s, e in parts:
        inputs += ["-ss", f"{s}", "-t", f"{e - s}", "-i", str(src)]
    if with_music:
        inputs += ["-i", str(music.compose(music_seed(out.stem + clip["title_en"]), dur + 1, work / "music.wav"))]
    enc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(FPS),
         "-i", "-", *inputs,
         "-filter_complex", audio_graph(parts, dur, len(parts) + 1 if with_music else None),
         "-map", "0:v", "-map", "[aout]",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-profile:v", "high", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", str(out)],
        stdin=subprocess.PIPE)
    i = 0
    for s, e in parts:
        for img in frames(src, s, e - s, FPS, src_w, src_h):
            x = xs[min(i, len(xs) - 1)]
            frame = base.copy()
            frame[VID_Y:VID_Y + VID_H] = cv2.resize(img[:, x:x + crop_w], (VID_W, VID_H),
                                                    interpolation=cv2.INTER_LANCZOS4)
            t = i / FPS
            for a, b, ((y0, y1, x0, x1), rgb, alpha) in cards:
                if a <= t < b:
                    region = frame[y0:y1, x0:x1].astype(np.float32)
                    frame[y0:y1, x0:x1] = (rgb * alpha + region * (1 - alpha)).astype(np.uint8)
            enc.stdin.write(frame.tobytes())
            i += 1
    enc.stdin.close()
    if enc.wait() != 0:
        raise RuntimeError(f"ffmpeg failed for {out.name}")
    shutil.rmtree(work, ignore_errors=True)
    return out


def poster(video: Path, out: Path, at: float = 1.5) -> Path:
    """A still for the review page."""
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{at}", "-i", str(video), "-frames:v", "1",
                    "-vf", "scale=540:-1", "-q:v", "4", str(out)], check=True)
    return out
