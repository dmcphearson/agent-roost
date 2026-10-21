"""Terminals: which one, opening a Session in a window, and AppleScript."""
import os, re, shlex, subprocess, time, uuid
from pathlib import Path

from roost.core.sessiondata import launch_dir
from roost.core.projectdata import find_project
from roost.core.formatting import short
from roost.core.mdfiles import readme_lines
from roost.core import config
from roost.core import projectdata
from roost.core import plugin


def mark_open(sessions, term):
    """Flag background sessions that are open in a window right now."""
    titles, clients = window_titles(term), attach_clients()
    for s in sessions:
        s["attached"] = s["kind"] == "background" and (
            s.get("attach") in clients or any(x.endswith(s["name"]) for x in titles))
    return sessions


def start_plain(cwd, term):
    """Start a Session with no project in cwd: claude, with roost's plugin when it has to be named.
    With a terminal, opens a new window and returns a message; without one, returns what to run here."""
    try:
        argv = ["claude", *plugin.plugin_args()]
    except ValueError:  # no plugin: the Session just goes without roost's skills
        argv = ["claude"]
    if not term:
        return {"start": argv, "cwd": cwd}
    t = TERMINALS[term]
    if osa(t["new"], cwd, *window_command(shlex.join(argv), os.path.basename(cwd.rstrip("/")) or cwd)) is None:
        return f"Couldn't open a {t['label']} window (check Automation permission, or run roost setup)"
    return f"Started a Session in {short(cwd)}"


def start_session(pid, cwd, term, name=None):
    """Start a fresh Session in cwd whose first prompt is /pickup <pid>, and list it in the project's
    sessions:. Claude Code takes the id roost picks, so roost knows it before the Session starts.
    pid is a project, a Session recap, or a note's path; name is the Session's name (default pid).
    With a terminal, opens a new window and returns a message; without one, returns what to run here."""
    sid, name = str(uuid.uuid4()), name or pid
    argv = ["claude", "--session-id", sid, "-n", name, *plugin.plugin_args(), f"/roost:pickup {pid}"]
    folder = find_project(pid)  # a project's folder, or a Session recap's file
    readme_lines(folder)  # refuse before starting anything if the README (or recap) can't take the Session
    if not term:
        projectdata.assign_session(sid, name, folder)
        return {"start": argv, "cwd": cwd}
    t = TERMINALS[term]
    if osa(t["new"], cwd, *window_command(shlex.join(argv), name)) is None:
        return f"Couldn't open a {t['label']} window (check Automation permission, or run roost setup)"
    projectdata.assign_session(sid, name, folder)
    return f"Started a Session for {name} in {short(cwd)}"


# ---------- terminals ----------
# Each terminal gets two AppleScripts: "new" opens a window in a folder and
# runs a command; "focus" brings forward a window whose title ends with one of
# the given names (Claude Code puts the session name in the title).

TERMINALS = {
    "ghostty": {
        "label": "Ghostty",
        "app": "Ghostty",
        "term_program": "ghostty",
        "titles": 'tell application "Ghostty" to get name of every terminal of every tab of every window',
        "new": """
on run argv
  tell application "Ghostty"
    set cfg to new surface configuration
    set initial working directory of cfg to item 1 of argv
    set command of cfg to item 3 of argv
    new window with configuration cfg
    activate
  end tell
end run
""",
        "focus": """
on run argv
  tell application "Ghostty"
    repeat with w in windows
      repeat with t in tabs of w
        repeat with term in terminals of t
          repeat with n in argv
            if (name of term) ends with (n as text) then
              focus term
              activate
              return "ok"
            end if
          end repeat
        end repeat
      end repeat
    end repeat
  end tell
  return "missing"
end run
""",
    },
    "terminal": {
        "label": "Terminal (macOS)",
        "app": "Terminal",
        "term_program": "apple_terminal",
        "titles": 'tell application "Terminal" to get custom title of every tab of every window',
        "new": """
on run argv
  tell application "Terminal"
    do script "cd " & quoted form of (item 1 of argv) & " && " & (item 2 of argv)
    activate
  end tell
end run
""",
        "focus": """
on run argv
  tell application "Terminal"
    repeat with w in windows
      repeat with t in tabs of w
        repeat with n in argv
          if (custom title of t) contains (n as text) or (name of w) contains (n as text) then
            set selected of t to true
            set index of w to 1
            activate
            return "ok"
          end if
        end repeat
      end repeat
    end repeat
  end tell
  return "missing"
end run
""",
    },
    "iterm2": {
        "label": "iTerm2",
        "app": "iTerm",
        "term_program": "iterm.app",
        "titles": 'tell application "iTerm" to get name of every session of every tab of every window',
        "new": """
on run argv
  tell application "iTerm"
    set w to (create window with default profile)
    tell current session of w to write text "cd " & quoted form of (item 1 of argv) & " && " & (item 2 of argv)
    activate
  end tell
end run
""",
        "focus": """
on run argv
  tell application "iTerm"
    repeat with w in windows
      repeat with t in tabs of w
        repeat with s in sessions of t
          repeat with n in argv
            if (name of s) contains (n as text) then
              select w
              tell t to select
              tell s to select
              activate
              return "ok"
            end if
          end repeat
        end repeat
      end repeat
    end repeat
  end tell
  return "missing"
end run
""",
    },
}


def detect_terminal():
    tp = os.environ.get("TERM_PROGRAM", "").lower()
    return next((k for k, v in TERMINALS.items() if v["term_program"] == tp), None)


# An IDE's terminal can't be scripted to open a tab, so there roost copies the line that runs a
# Session, and says how to open a tab to paste it in.
IDE_NOTES = {
    "jetbrains": "Copied the command: press ⌘T and paste it to open it in a new tab",
    "vscode": "Copied the command: open a new terminal (⌃⇧`) and paste it",
}


def ide_terminal(env=None):
    """The IDE whose terminal roost runs in: "jetbrains" (IntelliJ and the rest), "vscode" (VS Code
    and Cursor), or None."""
    env = os.environ if env is None else env
    if env.get("TERMINAL_EMULATOR", "").startswith("JetBrains"):
        return "jetbrains"
    return "vscode" if env.get("TERM_PROGRAM") == "vscode" else None


def run_line(s):
    """The shell line that runs s the way open_session would, to paste in another tab."""
    if s.get("start"):
        return f"cd {shlex.quote(s['cwd'])} && {shlex.join(s['start'])}"
    if s.get("attach"):
        return shlex.join(["claude", "attach", s["attach"]])
    return f"cd {shlex.quote(launch_dir(s))} && {shlex.join(['claude', '--resume', s['id']])}"


def chosen_terminal():
    """The terminal roost opens sessions in: config file first, then auto-detect.
    None means open in the current terminal."""
    cfg = config.read_config()
    if "terminal" not in cfg:
        return detect_terminal()
    return cfg["terminal"] if cfg["terminal"] in TERMINALS else None


def app_running(app):
    # process names vary in case (Ghostty runs as "ghostty"), so match case-insensitively
    name = "iTerm2" if app == "iTerm" else app
    return subprocess.run(["pgrep", "-iq", f"^{name}$"], stdin=subprocess.DEVNULL).returncode == 0


def window_titles(term):
    """Titles of every open terminal tab. Claude doesn't report which background
    sessions are attached to a window, so titles are one way to tell."""
    if not term or not app_running(TERMINALS[term]["app"]):
        return []
    out = osa(TERMINALS[term]["titles"]) or ""
    return [x.strip() for x in out.split(",") if x.strip()]


def attach_clients():
    """Short ids of background sessions some window is running `claude attach` on."""
    out = subprocess.run(["ps", "-axo", "command="], stdin=subprocess.DEVNULL, capture_output=True, text=True).stdout
    return set(re.findall(r"claude attach ([0-9a-f]{8})", out))


def osa(script, *args):
    """Run an AppleScript; its output, or None when it fails or hangs (macOS waiting on an Automation prompt)."""
    try:
        r = subprocess.run(["osascript", "-", *args], input=script, capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        config.log("osascript took over 10s and was stopped")
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def window_command(cmd, title):
    """Shell line that titles the window after the session, runs cmd, then leaves
    a normal shell. Returns (typed form, direct-exec form); Ghostty uses the
    direct form so its shell integration can't rename the window to the command."""
    line = f"printf '\\033]0;%s\\007' {shlex.quote(title)}; {cmd}"
    shell = os.environ.get("SHELL", "/bin/zsh")
    direct = f"{shell} -lic {shlex.quote(line + f'; exec {shell} -l')}"
    return line, direct


def open_in_window(s, term):
    """Open the session in a new window of `term` without leaving roost. Returns a status message."""
    if s.get("desktop"):
        return go_to_desktop(s)
    t = TERMINALS[term]
    if s["kind"] == "interactive" or s.get("attached"):
        names = [n for n in {s["name"], s.get("ai_title")} if n]
        if osa(t["focus"], *names) == "ok":
            return f"Switched to {s['name']}, already open"
        return f"{s['name']} is already open, but its window wasn't found"
    cmd = f"claude attach {s['attach']}" if s.get("attach") else f"claude --resume {s['id']}"
    cwd = launch_dir(s)
    if osa(t["new"], cwd, *window_command(cmd, s["name"])) is None:
        return f"Couldn't open a {t['label']} window (check Automation permission, or run roost setup)"
    s["opening"] = time.time()
    return f"Opened {s['name']} in a new window"


def go_to_desktop(s):
    """Bring Claude Desktop forward, where s is open. Returns a status message."""
    if osa('tell application "Claude" to activate') is None:
        return f"{s['name']} is open in Claude Desktop, but Claude Desktop couldn't be brought forward"
    return f"Switched to Claude Desktop, where {s['name']} is open"


def open_session(s):
    if s.get("start"):  # a fresh Session from a project (start_session)
        os.chdir(s["cwd"])
        os.execvp("claude", s["start"])
    if s.get("attach"):
        os.execvp("claude", ["claude", "attach", s["attach"]])
    os.chdir(launch_dir(s))
    os.execvp("claude", ["claude", "--resume", s["id"]])


def app_installed(app):
    return any(Path(d, f"{app}.app").exists() for d in
               ("/Applications", "/System/Applications/Utilities", str(Path.home() / "Applications")))
