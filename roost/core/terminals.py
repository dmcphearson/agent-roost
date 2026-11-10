"""Terminals: which one, opening a Session in a tab or window (AppleScript) or a tmux window, and jumping to it."""
import os, re, shlex, subprocess, time, uuid
from pathlib import Path

from roost.core.sessiondata import launch_dir
from roost.core.projectdata import find_project
from roost.core.formatting import short
from roost.core.mdfiles import readme_lines
from roost.core import config
from roost.core import projectdata
from roost.core import plugin
from roost.core import system


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
    opened, where, hint = open_new(term, cwd, shlex.join(argv), os.path.basename(cwd.rstrip("/")) or cwd)
    if not opened:
        return failed(term, where)
    return f"Started a Session in {short(cwd)}{hint}"


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
    opened, where, hint = open_new(term, cwd, shlex.join(argv), name)
    if not opened:
        return failed(term, where)
    projectdata.assign_session(sid, name, folder)
    return f"Started a Session for {name} in {short(cwd)}{hint}"


# ---------- terminals ----------
# Each terminal gets three AppleScripts: "new" opens a window in a folder and
# runs a command; "tab" does the same in a new tab of its front window; "focus" brings forward a window whose title ends with one of
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
        "tab": """
on run argv
  tell application "Ghostty"
    set cfg to new surface configuration
    set initial working directory of cfg to item 1 of argv
    set command of cfg to item 3 of argv
    if (count of windows) is 0 then
      new window with configuration cfg
    else
      new tab in front window with configuration cfg
    end if
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
        # Terminal can't open a tab by script: cmd+T through System Events, which needs Accessibility
        # permission for the terminal roost runs in. Without it this fails and roost opens a window.
        "tab": """
on run argv
  tell application "Terminal"
    if (count of windows) is 0 then
      do script "cd " & quoted form of (item 1 of argv) & " && " & (item 2 of argv)
    else
      activate
      tell application "System Events" to tell process "Terminal" to keystroke "t" using command down
      delay 0.3
      do script "cd " & quoted form of (item 1 of argv) & " && " & (item 2 of argv) in selected tab of front window
    end if
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
        "tab": """
on run argv
  tell application "iTerm"
    if (count of windows) is 0 then
      set w to (create window with default profile)
    else
      set w to current window
      tell w to create tab with default profile
    end if
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


def open_in():
    """Where roost opens a Session: "tab" (the default) or "window", from config.json's open_in."""
    return "window" if config.read_config().get("open_in") == "window" else "tab"


def open_new(term, cwd, cmd, title):
    """Open cmd in cwd in a new tab or window of term, as open_in says. Returns (opened, where, hint):
    where is "tab" or "window"; Terminal falls back to a window when it can't open a tab, with a hint."""
    if term == "tmux":  # always a tmux window, whatever open_in says
        return tmux("new-window", "-c", cwd, "-n", title, window_command(cmd, title)[1]) is not None, "tmux window", ""
    t, where = TERMINALS[term], open_in()
    if osa(t["tab" if where == "tab" else "new"], cwd, *window_command(cmd, title)) is not None:
        return True, where, ""
    if where == "tab" and term == "terminal" and osa(t["new"], cwd, *window_command(cmd, title)) is not None:
        return True, "window", (" (a tab needs Accessibility permission for your terminal: System Settings → "
                                "Privacy & Security → Accessibility)")
    return False, where, ""


def failed(term, where):
    if term == "tmux":
        return "Couldn't open a tmux window (is tmux running? roost setup chooses another terminal)"
    return f"Couldn't open a {TERMINALS[term]['label']} {where} (check Automation permission, or run roost setup)"


# ---------- tmux ----------
# tmux is driven by its own command, on macOS and Linux: a Session opens in a new tmux window named
# after it, and jumping to one finds the window whose name or pane title ends with its name.

TMUX_WINDOWS = "#{session_name}:#{window_index}\t#{window_name}\t#{pane_title}"


def tmux(*args):
    """Run a tmux command; its output, or None when it fails or tmux isn't there."""
    try:
        r = subprocess.run(["tmux", *args], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def tmux_windows():
    """(target, window name, pane title) for every tmux window, or [] when tmux isn't running."""
    rows = [line.split("\t") for line in (tmux("list-windows", "-a", "-F", TMUX_WINDOWS) or "").splitlines()]
    return [(r[0], r[1], r[2] if len(r) > 2 else "") for r in rows if len(r) >= 2]


def tmux_focus(s):
    """Switch to the tmux window s is open in. Returns a status message."""
    names = [n for n in (s["name"], s.get("ai_title")) if n]
    for target, name, title in tmux_windows():
        if any(name.endswith(n) or title.endswith(n) for n in names):
            tmux("switch-client", "-t", target)
            tmux("select-window", "-t", target)
            return f"Switched to {s['name']}, already open"
    return f"{s['name']} is already open, but its tmux window wasn't found"


def detect_terminal():
    if os.environ.get("TMUX"):  # inside tmux, whatever terminal tmux runs in
        return "tmux"
    if not system.mac():  # the AppleScript terminals are macOS only
        return None
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
    if cfg["terminal"] == "tmux":
        return "tmux"
    return cfg["terminal"] if cfg["terminal"] in TERMINALS and system.mac() else None


def app_running(app):
    # process names vary in case (Ghostty runs as "ghostty"), so match case-insensitively
    name = "iTerm2" if app == "iTerm" else app
    return subprocess.run(["pgrep", "-iq", f"^{name}$"], stdin=subprocess.DEVNULL).returncode == 0


def window_titles(term):
    """Titles of every open terminal tab. Claude doesn't report which background
    sessions are attached to a window, so titles are one way to tell."""
    if term == "tmux":
        return [x for _, name, title in tmux_windows() for x in (name, title) if x]
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
    """Open the session in a new tab or window of `term` (open_in) without leaving roost. Returns a status message."""
    if s.get("desktop"):
        return go_to_desktop(s)
    if term == "tmux" and (s["kind"] == "interactive" or s.get("attached")):
        return tmux_focus(s)
    t = TERMINALS.get(term)
    if s["kind"] == "interactive" or s.get("attached"):
        names = [n for n in {s["name"], s.get("ai_title")} if n]
        if osa(t["focus"], *names) == "ok":
            return f"Switched to {s['name']}, already open"
        return f"{s['name']} is already open, but its window wasn't found"
    cmd = f"claude attach {s['attach']}" if s.get("attach") else f"claude --resume {s['id']}"
    opened, where, hint = open_new(term, launch_dir(s), cmd, s["name"])
    if not opened:
        return failed(term, where)
    s["opening"] = time.time()
    return f"Opened {s['name']} in a new {where}{hint}"


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
