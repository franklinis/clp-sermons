"""Run Sermon Shorts on a church computer (macOS, Windows or Linux) and publish the result.

YouTube refuses downloads from cloud servers such as GitHub's, so the clips are made on a
computer with an ordinary internet connection. `run` makes the clips, pushes them to the
`media` branch and starts the GitHub workflow that creates the Buffer drafts and posts to
Teams (using the secrets stored on GitHub). `setup` stores the Claude API key in the
computer's password store (Keychain / Credential Manager) and schedules `run`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from datetime import datetime
import getpass
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from xml.sax.saxutils import escape

GITHUB_REPO = "franklinis/clp-sermons"
KEYRING_SERVICE, KEYRING_USER = "clp-sermons", "anthropic"
AUTHOR = ["-c", "user.name=franklin", "-c", "user.email=105910928+franklinis@users.noreply.github.com"]
TASK_NAME = "CLP Sermon Shorts"
LAUNCHD_LABEL = "de.christ-livingpetra.sermon-shorts"
# Sunday after the service, and Monday and Tuesday, since YouTube's captions can take hours.
SCHEDULE = [(("Sunday",), "15:00"), (("Sunday",), "19:00"),
            (("Monday", "Tuesday"), "08:00"), (("Monday", "Tuesday"), "13:00"), (("Monday", "Tuesday"), "19:00")]
WEEKDAY_NUMBER = {"Sunday": 0, "Monday": 1, "Tuesday": 2}


def log(message: str) -> None:
    print(f"{datetime.now():%Y-%m-%d %H:%M} {message}", flush=True)


def default_log_file() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / "clp-sermons.log"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "clp-sermons" / "clp-sermons.log"
    return Path.home() / ".local" / "state" / "clp-sermons.log"


def log_to(path: Path) -> None:
    """Send everything (also from ffmpeg and yt-dlp) to a log file; used when the computer runs it on schedule."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115 - kept open for the whole run
    for fd in (1, 2):
        os.dup2(handle.fileno(), fd)
    if sys.platform == "win32":  # child processes take the Win32 handles, not the C runtime's
        import ctypes
        import msvcrt

        for std, fd in ((-11, 1), (-12, 2)):
            ctypes.windll.kernel32.SetStdHandle(std, msvcrt.get_osfhandle(fd))
    sys.stdout = sys.stderr = open(1, "w", encoding="utf-8", buffering=1, closefd=False)  # noqa: SIM115


# ---------------------------------------------------------------- helpers


def git(*args: str, cwd: Path, check: bool = True, quiet: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, check=check, text=True, encoding="utf-8", errors="replace",
                          capture_output=quiet)


def github_token(root: Path) -> str:
    """The GitHub login git itself uses (macOS Keychain, Git Credential Manager on Windows)."""
    out = subprocess.run(["git", "credential", "fill"], cwd=root, input="protocol=https\nhost=github.com\n\n",
                         capture_output=True, text=True, check=True).stdout
    return next((line[9:] for line in out.splitlines() if line.startswith("password=")), "")


def start_workflow(root: Path, failure: str = "") -> None:
    """Start the GitHub workflow: Buffer drafts and Teams, or (with `failure`) report the error to Teams."""
    inputs = {"failure": failure[:900]} if failure else {}
    request = urllib.request.Request(
        f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/sermons.yml/dispatches",
        data=json.dumps({"ref": "main", "inputs": inputs}).encode(), method="POST",
        headers={"Authorization": f"Bearer {github_token(root)}", "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30):
            log("GitHub workflow started")
    except OSError as err:
        log(f"Could not start the GitHub workflow: {err}")


def claude_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        with suppress(Exception):  # no password store available (e.g. a Linux server)
            import keyring

            key = keyring.get_password(KEYRING_SERVICE, KEYRING_USER) or ""
    return key


@contextmanager
def single_run() -> Iterator[bool]:
    """True if no other run is going on (the schedule and a run by hand could overlap)."""
    lock = Path(tempfile.gettempdir()) / "clp-sermons.lock"
    if lock.exists() and time.time() - lock.stat().st_mtime > 4 * 3600:
        lock.unlink(missing_ok=True)  # left behind by a run that crashed
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        yield False
        return
    os.close(fd)
    try:
        yield True
    finally:
        lock.unlink(missing_ok=True)


@contextmanager
def keep_awake() -> Iterator[None]:
    """Stop the computer from sleeping while it renders."""
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)  # CONTINUOUS | SYSTEM_REQUIRED
        try:
            yield
        finally:
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
    elif sys.platform == "darwin" and shutil.which("caffeinate"):
        guard = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
        try:
            yield
        finally:
            guard.terminate()
    else:
        yield


# ---------------------------------------------------------------- run


def run(root: Path, media: Path, failed_file: Path, process: Callable[[], int], publish_page: Callable[[], None],
        prune: Callable[[], None]) -> int:
    with single_run() as alone:
        if not alone:
            log("Another run is in progress")
            return 0
        return _run(root, media, failed_file, process, publish_page, prune)


def _fail(root: Path, message: str) -> int:
    log(f"FAILED: {message}")
    start_workflow(root, message)
    return 1


def _run(root: Path, media: Path, failed_file: Path, process: Callable[[], int], publish_page: Callable[[], None],
         prune: Callable[[], None]) -> int:
    log("Starting")
    key = claude_key()
    if not key:
        return _fail(root, "The Claude API key is missing on this computer: run python -m sermon_clips setup")
    os.environ["ANTHROPIC_API_KEY"] = key

    if git("pull", "-q", "--ff-only", cwd=root, check=False).returncode:
        log("Could not update the code; using the version on this computer")
    if git("fetch", "-q", "origin", "media", cwd=root, check=False).returncode:
        return _fail(root, "Could not reach GitHub")
    if not (media / ".git").exists():
        git("worktree", "prune", cwd=root)
        git("worktree", "add", "-q", "--detach", str(media), "origin/media", cwd=root)
    elif git("checkout", "-q", "-f", "--detach", "origin/media", cwd=media, check=False).returncode:
        git("reset", "-q", "--hard", "origin/media", cwd=media)  # a file in the way: GitHub's copy wins
    started_from = git("rev-parse", "origin/media", cwd=root, quiet=True).stdout.strip()

    failed_file.unlink(missing_ok=True)
    with keep_awake():
        status = process()
    if failed_file.exists():
        failures = json.loads(failed_file.read_text(encoding="utf-8"))["failures"]
        return _fail(root, "; ".join(f"{f['title']}: {f['error']}" for f in failures))
    if status:
        return _fail(root, f"Making clips stopped with an error (exit {status}); see the log on the church computer")

    if not git("status", "--porcelain", cwd=media, quiet=True).stdout.strip():
        log("Nothing new")
        return 0
    git("fetch", "-q", "origin", "media", cwd=root)
    if git("rev-parse", "origin/media", cwd=root, quiet=True).stdout.strip() != started_from:
        # Another computer published meanwhile. Keep these clips; the next run sorts it out.
        log("The media branch changed during this run (another computer?); uploading next time")
        return 0

    prune()
    publish_page()
    # One commit holding only the latest sermons, so the repository never grows. Clips already
    # on GitHub aren't uploaded again; only new ones are.
    git("checkout", "-q", "--orphan", "publish", cwd=media)
    git("add", "-A", cwd=media)
    git(*AUTHOR, "commit", "-qm", "Sermon Shorts", cwd=media)
    pushed = False
    for attempt in range(3):
        if not git("push", "-qf", "origin", "publish:media", cwd=media, check=False).returncode:
            pushed = True
            break
        log(f"Upload failed (attempt {attempt + 1}); retrying")
        time.sleep(30)
    git("checkout", "-q", "--detach", cwd=media)
    git("branch", "-q", "-D", "publish", cwd=media)
    if not pushed:
        return _fail(root, "Could not upload the clips to GitHub")
    log("Clips uploaded")
    start_workflow(root)
    log("Done")
    return 0


# ---------------------------------------------------------------- setup


def store_key() -> None:
    import keyring

    if keyring.get_password(KEYRING_SERVICE, KEYRING_USER):
        print("The Claude API key is already stored on this computer.")
        return
    key = getpass.getpass("Paste the Claude API key from platform.claude.com (it won't be shown): ").strip()
    if not key.startswith("sk-ant-"):
        raise SystemExit("That doesn't look like a Claude API key (they start with sk-ant-).")
    keyring.set_password(KEYRING_SERVICE, KEYRING_USER, key)
    print("Stored in this computer's password store.")


def launchd_plist(root: Path, python: str, log_file: Path) -> bytes:
    times = [{"Weekday": WEEKDAY_NUMBER[day], "Hour": int(at[:2]), "Minute": int(at[3:])}
             for days, at in SCHEDULE for day in days]
    return plistlib.dumps({
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": [python, "-m", "sermon_clips", "run", "--log", str(log_file)],
        "WorkingDirectory": str(root),
        "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"},
        "StartCalendarInterval": times,
        "ProcessType": "Background",
    })


def windows_task_xml(root: Path, python: str, log_file: Path) -> str:
    """A Task Scheduler task. StartWhenAvailable runs a missed time once the computer is on again."""
    triggers = "".join(
        f"<CalendarTrigger><StartBoundary>2026-01-01T{at}:00</StartBoundary><Enabled>true</Enabled>"
        f"<ScheduleByWeek><DaysOfWeek>{''.join(f'<{day} />' for day in days)}</DaysOfWeek>"
        f"<WeeksInterval>1</WeeksInterval></ScheduleByWeek></CalendarTrigger>"
        for days, at in SCHEDULE
    )
    pythonw = str(Path(python).with_name("pythonw.exe")) if Path(python).with_name("pythonw.exe").exists() else python
    return (
        '<?xml version="1.0" encoding="UTF-16"?>'
        '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">'
        f"<RegistrationInfo><Description>Sermon Shorts for Christ the Living Petra Church</Description></RegistrationInfo>"
        f"<Triggers>{triggers}</Triggers>"
        "<Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>"
        "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>"
        "<StartWhenAvailable>true</StartWhenAvailable><RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>"
        "<ExecutionTimeLimit>PT4H</ExecutionTimeLimit><Enabled>true</Enabled></Settings>"
        f"<Actions><Exec><Command>{escape(pythonw)}</Command>"
        f'<Arguments>-m sermon_clips run --log "{escape(str(log_file))}"</Arguments>'
        f"<WorkingDirectory>{escape(str(root))}</WorkingDirectory></Exec></Actions></Task>"
    )


def setup(root: Path, remove: bool) -> int:
    python, log_file = sys.executable, default_log_file()
    if sys.platform == "darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
        target = f"gui/{os.getuid()}"
        subprocess.run(["launchctl", "bootout", f"{target}/{LAUNCHD_LABEL}"], capture_output=True)
        if remove:
            plist.unlink(missing_ok=True)
            print("Scheduled task removed.")
            return 0
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_bytes(launchd_plist(root, python, log_file))
        subprocess.run(["launchctl", "bootstrap", target, str(plist)], check=True)
    elif sys.platform == "win32":
        if remove:
            subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], check=False)
            print("Scheduled task removed.")
            return 0
        xml = Path(tempfile.gettempdir()) / "clp-sermons-task.xml"
        xml.write_text(windows_task_xml(root, python, log_file), encoding="utf-16")
        subprocess.run(["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(xml), "/F"], check=True)
        xml.unlink(missing_ok=True)
    else:
        if remove:
            print("Remove the clp-sermons line from your crontab (crontab -e).")
            return 0
        print("Add this to your crontab (crontab -e):")
        print(f"0 15,19 * * 0 cd {root} && {python} -m sermon_clips run --log {log_file}")
        print(f"0 8,13,19 * * 1,2 cd {root} && {python} -m sermon_clips run --log {log_file}")
    days = "; ".join(f"{'/'.join(d)} {at}" for d, at in SCHEDULE)
    print(f"Scheduled: {days}. Log: {log_file}")
    store_key()
    return 0
