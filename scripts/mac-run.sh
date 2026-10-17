#!/bin/bash
# Make Sermon Shorts on this Mac and publish them.
#
#   scripts/mac-run.sh                      the next new sermon in the playlist (what the background job runs)
#   scripts/mac-run.sh --video <link>       a particular sermon
#   scripts/mac-run.sh --video <link> --redo  start that sermon again (new moments from Claude)
#
# YouTube refuses downloads from cloud servers, so the clips are made here, at home. The
# finished clips are pushed to the `media` branch; GitHub then creates the Buffer drafts and
# posts to Teams (the "Sermon Shorts" workflow), using the secrets stored in the repository.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
export MEDIA_DIR="$REPO/media" WORK_DIR="$REPO/.work"
GITHUB_REPO="franklinis/clp-sermons"
AUTHOR=(-c user.name=franklin -c user.email=105910928+franklinis@users.noreply.github.com)

log() { echo "$(date '+%Y-%m-%d %H:%M') $*"; }

github_token() {
  printf "protocol=https\nhost=github.com\n\n" | git credential fill 2>/dev/null | sed -n 's/^password=//p'
}

# Start the GitHub workflow: drafts and Teams, or (with an argument) report a failure to Teams.
publish_on_github() {
  local inputs="{}"
  if [ -n "${1:-}" ]; then
    inputs=$(python3 -c 'import json,sys; print(json.dumps({"failure": sys.argv[1][:900]}))' "$1")
  fi
  curl -sf -o /dev/null -X POST -H "Authorization: Bearer $(github_token)" -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/$GITHUB_REPO/actions/workflows/sermons.yml/dispatches" \
    -d "{\"ref\":\"main\",\"inputs\":$inputs}" && log "GitHub workflow started" || log "Could not start the GitHub workflow"
}

fail() {
  log "FAILED: $1"
  publish_on_github "$1"
  exit 1
}

# One run at a time (the background job and a run by hand could overlap).
LOCK="/tmp/clp-sermons.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  log "Another run is in progress"; exit 0
fi
trap 'rmdir "$LOCK"' EXIT

log "Starting"
[ -x "$REPO/.venv/bin/python" ] || fail "Not set up on this Mac: run scripts/mac-setup.sh"

ANTHROPIC_API_KEY="$(security find-generic-password -s clp-sermons -a anthropic -w 2>/dev/null)"
[ -n "$ANTHROPIC_API_KEY" ] || fail "The Claude API key is missing from this Mac's Keychain (see the README)"
export ANTHROPIC_API_KEY

# Latest code, and the clips already published (with their Buffer draft ids).
git -C "$REPO" pull -q --ff-only || log "Could not update the code; using the version on this Mac"
git -C "$REPO" fetch -q origin media || fail "Could not reach GitHub"
if [ ! -e "$MEDIA_DIR/.git" ]; then
  git -C "$REPO" worktree add -q --detach "$MEDIA_DIR" origin/media || fail "Could not check out the media branch"
else
  git -C "$MEDIA_DIR" checkout -q --detach origin/media 2>/dev/null || git -C "$MEDIA_DIR" reset -q --hard origin/media
fi

rm -f "$WORK_DIR/failed.json"
cd "$REPO" || exit 1
# Keep the Mac awake while it works.
caffeinate -i "$REPO/.venv/bin/python" -m sermon_clips process "$@"
status=$?

if [ -f "$WORK_DIR/failed.json" ]; then
  fail "$(python3 -c 'import json,sys; print("; ".join(f["title"] + ": " + f["error"] for f in json.load(open(sys.argv[1]))["failures"]))' "$WORK_DIR/failed.json")"
fi
[ "$status" -eq 0 ] || fail "Making clips stopped with an error (exit $status); see ~/Library/Logs/clp-sermons.log"

if [ -z "$(git -C "$MEDIA_DIR" status --porcelain)" ]; then
  log "Nothing new"; exit 0
fi

"$REPO/.venv/bin/python" -m sermon_clips prune --keep 6
"$REPO/.venv/bin/python" -m sermon_clips page
# One commit holding only the latest sermons, so the repository never grows. Clips already on
# GitHub aren't uploaded again; only new ones are.
cd "$MEDIA_DIR" || exit 1
git checkout -q --orphan publish
git add -A
git "${AUTHOR[@]}" commit -qm "Sermon Shorts" || fail "Could not save the clips"
pushed=0
for attempt in 1 2 3; do
  if git push -qf origin publish:media; then pushed=1; break; fi
  log "Upload failed (attempt $attempt); retrying"; sleep 30
done
git checkout -q --detach && git branch -q -D publish
[ "$pushed" -eq 1 ] || fail "Could not upload the clips to GitHub"
log "Clips uploaded"
publish_on_github
log "Done"
