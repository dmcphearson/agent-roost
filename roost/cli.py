"""roost — where your Claude sessions come home to roost.

  roost              open roost on the tab you used last (Projects the first time)
  roost <text>       open it with a search already applied
  roost <command>    run a command; roost <command> --help gives its flags
  roost --list       plain-text listing (no UI), for scripts and pipes
  roost --demo       made-up sessions for screenshots; nothing real is read or changed
  roost --version    print the version

Commands:
{commands}

Keys, the same on every tab: ↑↓ move · ←→ switch views · enter (or right-click) opens the
selected row's actions · / search · tab switch tabs · ? help · ctrl+q quit. Double-click a row
to run its first action (open a Session, start one on a project). Click a tab, view, or
column header to switch or sort.
Pinned background sessions (marked with a pin) are never closed for being idle. Pinning a closed session
restarts it in the background (no model call).
In Ghostty, Open starts the session in a new window (or jumps to it if it's already
open) and roost stays open. Elsewhere it opens in the same terminal.
"""
import argparse, os, sys
from collections import namedtuple
from pathlib import Path

from roost.core.sessiondata import ago, group, matches
from roost.core.projectdata import project_command, project_flags
from roost.core.notedata import note_command, note_flags
from roost.core.taskdata import task_command, task_flags
from roost.core.handoffs import handoff_command, handoff_flags, ledger_command, ledger_flags
from roost.core.adopt import adopt_command, adopt_flags
from roost.core.formatting import ktok
from roost.app.ui import UI
from roost.app.screen import App, run_screens, textual_python
from roost.demo import use_demo
from roost.core import config
from roost.core import wren
from roost.core import projectdata
from roost.core import sessiondata
from roost.core import install
from roost.core import terminals


def plain(sessions, query):
    rows = [s for s in sessions if matches(s, query)]
    rows.sort(key=lambda s: (group(s), -s["last"]))
    for s in rows:
        print(f"{s['id'][:8]}  {UI.label(s['state']):<8} {ago(s):<16} {ktok(s['tokens']):>5}  {s['name']}")


# ---------- commands ----------
# Each command: its one-line help, a function that adds its flags to a parser, and a function
# that runs it with the parsed flags and returns the exit code.

Command = namedtuple("Command", "help flags run")
COMMANDS = {
    "setup": Command("check prerequisites, choose the terminal and Projects folder, and install the plugin",
                     lambda p: p.add_argument("step", nargs="?", choices=["plugin", "textual"],
                                              help="plugin: only install and register roost's Claude Code plugin; "
                                                   "textual: only install Textual, which draws the screens, in ~/.roost/venv"),
                     lambda args: install.setup(args.step)),
    "update": Command("install the latest release (Homebrew: brew upgrade roost)",
                      lambda p: None, lambda args: install.self_update()),
    "project": Command("projects: new <id> [--description \"…\"] · brief <id> · list · status <id> · description <id>",
                       project_flags, lambda args: project_command(args)),
    "task": Command("tasks: new <project> \"title\" · list <project> · show <id> · claim <id> · note <id> · status <id>",
                    task_flags, task_command),
    "handoff": Command("write this Session's Handoff in its project, from a summary on stdin (/wrap-up runs it)",
                       handoff_flags, handoff_command),
    "note": Command("save a note to your scratchpad (the Notes tab): note \"title\" [text], or the text on stdin",
                    note_flags, note_command),
    "ledger": Command("print a project's timeline: every Handoff's facts, oldest first",
                      ledger_flags, ledger_command),
    "adopt": Command("bring existing project folders into roost's project layout: plan, review each plan, then apply",
                     adopt_flags, adopt_command),
}


def usage():
    width = max(map(len, COMMANDS))
    lines = "\n".join(f"  {name.ljust(width)}  {c.help}" for name, c in COMMANDS.items())
    return __doc__.strip().replace("{commands}", lines)


def run_command(name, argv):
    """Parse a command's flags (--help prints them and exits) and run it. Returns its exit code."""
    c = COMMANDS[name]
    parser = argparse.ArgumentParser(prog=f"roost {name}", description=c.help)
    c.flags(parser)
    return c.run(parser.parse_args(argv))


def main(argv):
    if argv and argv[0] in ("-h", "--help"):
        wren.print_wren()
        print(usage()); return
    if argv and argv[0] == "--version":
        wren.print_wren()
        print(f"roost {config.VERSION}"); return
    if "--demo" not in argv:
        try:
            projectdata.move_recaps()  # once: recaps from before they were notes
        except Exception as e:
            config.log(f"move recaps: {e}")
    if argv and argv[0] in COMMANDS:
        sys.exit(run_command(argv[0], argv[1:]))
    as_list = "--list" in argv
    if not as_list and sys.stdout.isatty() and App is None:
        py = textual_python()
        if py and Path(sys.prefix) != config.VENV:  # the screens run on the Python in ~/.roost/venv
            os.execv(str(py), [str(py), os.path.realpath(sys.argv[0])] + argv)
        print("roost's screens need Textual: run roost setup textual, which installs it in ~/.roost/venv.\n"
              "roost --list prints a plain listing without it.", file=sys.stderr)
        sys.exit(1)
    if "--demo" in argv:
        use_demo()
    query = " ".join(a for a in argv if a not in ("--list", "--demo"))
    print("roost: reading sessions…", end="\r", file=sys.stderr, flush=True)
    sessions = sessiondata.gather()
    print(" " * 30, end="\r", file=sys.stderr, flush=True)
    if as_list or not sys.stdout.isatty():
        return plain(sessions, query)
    choice = run_screens(sessions, query)
    if choice:
        terminals.open_session(choice)
