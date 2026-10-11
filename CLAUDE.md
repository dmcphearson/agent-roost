# Agent Roost

A Python 3 package, `roost/`, started by `bin/roost` (a small launcher). Its commands use only the standard library and must run on the macOS system Python 3.9. Its screens run on Textual, its one dependency, installed in `~/.roost/venv` (`TEXTUAL` in `roost/core/config.py` pins the version; `roost setup textual` installs it). `main()` starts the screens again on that venv's Python. Add no other dependencies.

## Layout

- `roost/core/`: everything that reads or changes Sessions, projects, notes, Handoffs, and roost's install, with no screens.
  A core module a tab shares a subject with is named `<subject>data.py` (`projectdata`, `sessiondata`, `notedata`, `statsdata`), so `core/projectdata.py` is the projects on disk and `tabs/projects.py` is the Projects tab.
- `roost/app/shell.py`: what every tab shares (keys, menus, pickers, the confirm and alert boxes). `roost/tabs/`: one module per tab, each a mixin class. `roost/app/ui.py` composes them into `UI` and lists the tabs (`TABS`). `roost/app/screen.py`: the Textual front end.
- `roost/cli.py`: the commands and `main()`. `roost/demo.py`: `roost --demo`.
- `roost.MODULES` lists every module lowest first. A module imports names only from the ones before it (no import cycles).
- Names the demo or a test replaces at run time (`gather`, `summarize`, `NOTES`, …) are read through their module everywhere else, like `sessiondata.gather()` or `config.NOTES`, so the replacement reaches every caller. A plain `from … import` of such a name would keep the original.

## Gotchas

- Each tab's `draw_*` only works out what to show (its rows, `corner`, `subhead`, and `bar`); its view in `roost/tabs/` (Textual widgets, `Region` rows built with `Line`) draws it and fills the screen-position maps the key and mouse handlers read. A view's per-draw hook is `settle()`, never `arrange` (Textual's own). Key handlers take Textual's key names (`up`, `enter`, `ctrl+q`) or a typed character.

- macOS ships ncurses 6.0 (2015). Its Python binding has no `get_wch`, and its mouse API can't report wheel-down. A rewrite that parsed SGR mouse reports by hand passed pty tests but broke clicking in real Ghostty, so it was rolled back. Any mouse or input change must be tried in a real terminal window before it's kept.
- Every `subprocess` call that runs `claude` must pass `stdin=subprocess.DEVNULL`. Without it, `claude --bg` read the caller's stdin and sent it to a live session as prompts.
- Before `claude --bg --resume <id>`, run `claude rm <id>` if `~/.claude/jobs/<id>` exists. A leftover job entry makes Claude start an empty copy under a new ID instead.
- `claude agents --json` keeps listing retired background sessions with `pid: null`; `live_sessions()` drops them.
- Claude Code's own output is colored; strip ANSI codes before matching it.
- `~/.claude/jobs/pins.json` is shared with Claude Code. Write it only through `set_pin()`, which takes the same `pins.json.lock` directory lock.
- Ghostty's process name is lowercase `ghostty`; match process names case-insensitively.

## Testing

`~/.roost/venv/bin/python -m unittest discover -s tests < /dev/null` uses demo data and temp paths (plain `python3` skips the Textual tests; `< /dev/null` because `roost note` reads stdin). For UI changes, `roost --demo` gives a safe live view. Never test against real sessions by piping input into roost.

## Versioning and release

Calendar versions, `YYYY.MM.XX`, in `VERSION` in `roost/core/config.py` and `version` in `plugin/.claude-plugin/plugin.json` (a test fails if they differ). Note changes in `CHANGELOG.md`. A release is a GitHub release (not just a tag) on main: `install.sh` and `roost update` read the latest one with curl, put its `roost/` and `bin/roost` in `~/.roost/app/<version>`, and copy the launcher to `~/.local/bin/roost`. The Homebrew formula is `Formula/roost.rb` in this repo (the repo is its own tap); after a release, update its `url` and `sha256` in a commit on main, and its Textual resources if `TEXTUAL` changed. roost must keep running on the macOS system Python 3.9.

## Issues

Features, ideas, and bugs are GitHub Issues on dmcphearson/agent-roost. The repo is public: keep issues free of anything private.
