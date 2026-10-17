# Sermon Shorts

After each Sunday sermon is added to the **Sunday Sermons** playlist on YouTube, this repository turns it into **3–5 short vertical clips** (1080×1920) for YouTube Shorts, Instagram Reels and Facebook Reels, and saves them as **drafts in Buffer**. Someone from the church watches them and approves the ones to post; nothing is published automatically.

Each clip has:

- a short hook title in Tamil and English at the top,
- the sermon video in a 4:5 window that follows the preacher as he moves,
- subtitles: what he says in Tamil script (English words in English), with an English translation underneath,
- the voice cleaned of room noise and brought to a normal volume, with soft original background music that dips while he speaks.

## How it works

The clips are made on **a Mac at the church**, because YouTube refuses downloads from cloud servers such as GitHub's ("Sign in to confirm you're not a bot"). GitHub does the rest with the secrets stored in the repository.

On the Mac (`scripts/mac-run.sh`, run by a background job):

1. On Sunday at 15:00 and 19:00, and Monday and Tuesday at 08:00, 13:00 and 19:00, it checks the playlist's RSS feed for a sermon dated on or after `start_from` in `church.json` that hasn't been done yet. If the Mac is asleep then, macOS runs the job when it wakes; YouTube's automatic captions can take a few hours to appear, hence the repeats.
2. It downloads the video and YouTube's automatic Tamil captions with [yt-dlp](https://github.com/yt-dlp/yt-dlp).
3. **Claude** (`claude-opus-5-5` through the Claude API; about $0.50–1 per sermon) reads the timed captions and chooses the best stand-alone moments, writes corrected Tamil subtitles with English translations, the titles and the post text (`sermon_clips/picker.py`). It quotes the first and last words of each moment, and the code finds those words in the word-level caption timings, so every cut falls between words. A long story can be shortened by skipping a digression (a clip of up to three parts).
4. Each clip is rendered (`sermon_clips/render.py`): text by Chrome (correct Tamil shaping), face-following crop with OpenCV, and the sound with ffmpeg.
5. Clips, stills and a review page are pushed to the [`media` branch](../../tree/media), which keeps only the latest 6 sermons. GitHub Pages serves the review page.

On GitHub (`.github/workflows/sermons.yml`, started by the Mac):

6. For each clip and Buffer video channel, a draft is created with the publish time preset: one clip a day from the next day at 18:00, skipping Sundays.
7. A card is posted to the church's Teams channel with a link to the review page, or with the error if something failed on the Mac.

Re-running is safe: Claude's choice is saved as `plan.json` and is reused; finished clips and drafts already in Buffer are skipped.

## Setup

**On the Mac** (once): clone this repository, then

```bash
scripts/mac-setup.sh
```

It installs ffmpeg and Deno with Homebrew (Google Chrome must be installed), creates a Python environment in `.venv` and the background job `de.christ-livingpetra.sermon-shorts`. Then store the Claude API key from [platform.claude.com](https://platform.claude.com) in the Keychain; you'll be asked to paste it, so it never appears on screen or in a file:

```bash
security add-generic-password -s clp-sermons -a anthropic -w
```

The Mac uses its stored GitHub login (the one `git push` uses) to upload the clips and start the GitHub workflow. Its log is `~/Library/Logs/clp-sermons.log`. To stop the background job: `scripts/mac-setup.sh --remove`.

**On GitHub** (Settings → Secrets and variables → Actions):

| Where | Name | Value |
|---|---|---|
| Secret | `BUFFER_API_KEY` | A Buffer API key (publish.buffer.com/settings/api) |
| Secret | `TEAMS_WEBHOOK_URL` | The same Teams Workflows webhook as clp-daily-verse (optional) |
| Variable | `BUFFER_VIDEO_CHANNELS` | `id:service,…` (service = `youtube`, `instagram` or `facebook`) |
| Variable | `POST_TIME` | Optional, default `18:00` (Munich time) |

GitHub Pages: **Settings → Pages → Deploy from a branch → `media` / root**.

Church details, hashtags, the playlist and the number and length of clips are in `church.json`.

## Running it by hand

On the Mac:

```bash
scripts/mac-run.sh                                   # the next new sermon
scripts/mac-run.sh --video <YouTube link>            # a particular one (also older sermons)
scripts/mac-run.sh --video <YouTube link> --redo     # start it again with a fresh choice from Claude
```

## Development

```bash
pip install -r requirements.txt pytest ruff
pytest -q && ruff check .
```

The background music composer (`sermon_clips/music.py`) and Buffer client (`sermon_clips/buffer.py`) come from [clp-daily-verse](https://github.com/franklinis/clp-daily-verse).
