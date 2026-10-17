# Sermon Shorts

After each Sunday sermon is added to the **Sunday Sermons** playlist on YouTube, this repository turns it into **3–5 short vertical clips** (1080×1920) for YouTube Shorts, Instagram Reels and Facebook Reels, and saves them as **drafts in Buffer**. Someone from the church watches them and approves the ones to post; nothing is published automatically.

Each clip has:

- a short hook title in Tamil and English at the top,
- the sermon video in a 4:5 window that follows the preacher as he moves,
- subtitles: what he says in Tamil script (English words in English), with an English translation underneath,
- the voice cleaned of room noise and brought to a normal volume, with soft original background music that dips while he speaks.

## How it works

1. `.github/workflows/sermons.yml` runs on Sunday evening, Monday and Tuesday (several times, since YouTube's automatic captions can take a few hours). It checks the playlist's RSS feed for a sermon dated on or after `start_from` in `church.json` that hasn't been done yet.
2. It downloads the video and YouTube's automatic Tamil captions with [yt-dlp](https://github.com/yt-dlp/yt-dlp).
3. **Claude** (`claude-opus-5-5`, through Azure/Microsoft Foundry or the Claude API; about $0.50–1 per sermon) reads the timed captions and chooses the best stand-alone moments, writes corrected Tamil subtitles with English translations, the titles and the post text (`sermon_clips/picker.py`). It quotes the first and last words of each moment, and the code finds those words in the word-level caption timings, so every cut falls between words. A long story can be shortened by skipping a digression (a clip of up to three parts).
4. Each clip is rendered (`sermon_clips/render.py`): text by headless Chrome (correct Tamil shaping), face-following crop with OpenCV, and the sound with ffmpeg.
5. Clips, stills and a review page are pushed to the [`media` branch](../../tree/media), which keeps only the latest 6 sermons. GitHub Pages serves the review page.
6. For each clip and Buffer video channel, a draft is created with the publish time preset: one clip a day from the next day at 18:00, skipping Sundays.
7. A card is posted to the church's Teams channel with a link to the review page, or with the error if something failed.

Re-running is safe: Claude's choice is saved as `plan.json` and is reused; finished clips and drafts already in Buffer are skipped.

## Setup

| Where | Name | Value |
|---|---|---|
| Secret | `ANTHROPIC_FOUNDRY_API_KEY` | Claude through Azure: the key of the Microsoft Foundry resource where Claude is deployed |
| Variable | `ANTHROPIC_FOUNDRY_RESOURCE` | That resource's name (the `<name>` in `https://<name>.services.ai.azure.com`) |
| Variable | `CLAUDE_MODEL` | Optional: the deployment name, if it isn't `claude-opus-5-5` |
| Secret | `ANTHROPIC_API_KEY` | *Or*, instead of Azure, a key from [platform.claude.com](https://platform.claude.com) |
| Secret | `YOUTUBE_COOKIES` | YouTube sign-in cookies of a separate Google account (see below) |
| Secret | `BUFFER_API_KEY` | The same Buffer key as clp-daily-verse |
| Secret | `TEAMS_WEBHOOK_URL` | The same Teams Workflows webhook as clp-daily-verse (optional) |
| Variable | `BUFFER_VIDEO_CHANNELS` | `id:service,…` (service = `youtube`, `instagram` or `facebook`) |
| Variable | `POST_TIME` | Optional, default `18:00` (Munich time) |

GitHub Pages: **Settings → Pages → Deploy from a branch → `media` / root**.

Church details, hashtags, the playlist and the number and length of clips are in `church.json`.

## Running it by hand

**Actions → Sermon Shorts → Run workflow**:

- leave *video* empty to do the next new sermon, or paste a YouTube link to do a particular one (also older sermons);
- tick *redo* to start a sermon again with a fresh choice of moments from Claude.

### YouTube cookies

YouTube refuses downloads from cloud servers such as GitHub's ("Sign in to confirm you're not a bot") unless the request is signed in. Use a **separate Google account** made only for this, never the church's own account, because YouTube may restrict accounts used this way.

1. Install the browser extension **Get cookies.txt LOCALLY** (Chrome or Firefox).
2. Open a **private/incognito window** (allow the extension there), sign in to YouTube with the separate account, then open `https://www.youtube.com/robots.txt` in the same tab.
3. Export the cookies for youtube.com with the extension (Netscape format) and **close the private window** straight away without signing out, so YouTube doesn't replace these cookies.
4. Paste the whole file into the repository secret `YOUTUBE_COOKIES`.

Cookies expire after some weeks or months. When they do, the Teams card says so; repeat these steps and re-run the workflow.

If you'd rather not use cookies, run the same steps on a computer at home, where YouTube allows downloads, then push the `media` folder:

```bash
pip install -r requirements.txt   # plus ffmpeg and Deno (brew install ffmpeg deno)
export ANTHROPIC_API_KEY=...       # never commit it
python -m sermon_clips process --video <YouTube link>
python -m sermon_clips page
```

## Development

```bash
pip install -r requirements.txt pytest ruff
pytest -q && ruff check .
```

The background music composer (`sermon_clips/music.py`) and Buffer client (`sermon_clips/buffer.py`) come from [clp-daily-verse](https://github.com/franklinis/clp-daily-verse).
