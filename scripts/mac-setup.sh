#!/bin/bash
# One-time setup on the Mac that makes the Sermon Shorts:
# tools (ffmpeg, Deno), a Python environment, and the background job.
#
#   scripts/mac-setup.sh             set up, or update after changes
#   scripts/mac-setup.sh --remove    stop and remove the background job
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="de.christ-livingpetra.sermon-shorts"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/clp-sermons.log"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

if [ "${1:-}" = "--remove" ]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Background job removed."
  exit 0
fi

command -v brew >/dev/null || { echo "Install Homebrew first: https://brew.sh"; exit 1; }
for tool in ffmpeg deno; do
  command -v "$tool" >/dev/null || brew install "$tool"
done
[ -d "/Applications/Google Chrome.app" ] || { echo "Install Google Chrome first (it draws the subtitles)."; exit 1; }

if [ ! -x "$REPO/.venv/bin/python" ]; then
  "$(brew --prefix)/bin/python3" -m venv "$REPO/.venv"
fi
"$REPO/.venv/bin/python" -m pip install -q --upgrade pip
"$REPO/.venv/bin/python" -m pip install -q -r "$REPO/requirements.txt"
chmod +x "$REPO/scripts/mac-run.sh"

# Sunday 15:00 and 19:00; Monday and Tuesday 08:00, 13:00 and 19:00. If the Mac is asleep
# then, macOS runs the job when it wakes up. Sermons already done are skipped.
times=""
for slot in "0 15" "0 19" "1 8" "1 13" "1 19" "2 8" "2 13" "2 19"; do
  set -- $slot
  times+="<dict><key>Weekday</key><integer>$1</integer><key>Hour</key><integer>$2</integer><key>Minute</key><integer>0</integer></dict>"
done
mkdir -p "$(dirname "$PLIST")" "$(dirname "$LOG")"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$REPO/scripts/mac-run.sh</string></array>
  <key>StartCalendarInterval</key><array>$times</array>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
  <key>ProcessType</key><string>Background</string>
</dict>
</plist>
PLIST
plutil -lint -s "$PLIST"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Background job installed: $LABEL (log: $LOG)"

if ! security find-generic-password -s clp-sermons -a anthropic >/dev/null 2>&1; then
  echo
  echo "Last step: store the Claude API key in the Keychain (you'll be asked to paste it):"
  echo "  security add-generic-password -s clp-sermons -a anthropic -w"
fi
