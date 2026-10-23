# Changelog

## Unreleased

- **Sub-projects:** a project's work splits into sub-projects (feature, bugfix, research, chore) in its `sub/` folder, each with its own README and Handoffs. `roost project new <project>/<slug> --type <type>` makes one; picking one up reads the parent project's README first, then the sub-project's own, then the project's other open sub-projects; wrap-up writes into the sub-project; `roost project list` shows them under their project. On the Projects tab, → shows a project's open sub-projects under it and ← hides them; a project's actions add New sub-project… and Start a Session in a sub-project…, and search finds sub-projects (`type:bugfix` by type). Sessions know their sub-project: PROJECT, `project:` search, and Add to or Move to a project

## 2026.10.25

- **IDE terminals:** in IntelliJ (and the other JetBrains IDEs), VS Code, or Cursor, with no `terminal` in config.json, Open and Start copy the command that runs the Session (`cd <folder> && claude …`) instead of replacing roost in that tab, and say how to open a new tab to paste it in

## 2026.10.24

- **Agent Roost:** the product's name everywhere it's named: the README (its title loses the rooster emoji), NOTICE, TRADEMARKS.md, and the top bar, which now reads "Agent Roost". The command stays `roost`
- **Logo:** a low-poly wren, with lockups, wordmarks, and PNGs in `brand/`
- **The wren in the terminal:** `roost --version`, `roost --help`, and `roost setup` show the wren in color above their text in a 24-bit color terminal (not when piped, or with `NO_COLOR` set), and an empty Projects or Sessions tab shows it under its note when the window has room for all of it

## 2026.10.23

- **License:** Apache License 2.0, with NOTICE and TRADEMARKS.md. Releases up to 2026.10.22 stay MIT
- **Typing no longer lags:** the refresh every few seconds reads Sessions and projects in a background thread, so keys never wait on it (it used to hold the screen for up to half a second, and a prompt being typed in didn't pause it). Pin, Rename, and Stop run in the background too, showing Restarting…, Renaming…, or Stopping… until they're done
- **Sessions tab:** columns size the way the Projects tab's do: each fits its longest value and its header with the sort arrow, a wide window's spare room goes to NAME, then PROJECT, then evenly to every column, and a narrow one takes from PROJECT first and NAME last. CONTEXT's sizes line up under its header. The `name_width` setting is retired
- **Stats:** the Project spend card's names are as wide as the longest one, not cut at 34 characters

## 2026.10.22

- **Dates:** lists and cards show a time for today (2:17 PM) and otherwise the date alone, MM/DD/YY or DD/MM/YY by your locale's date format. The Sessions tab keeps its relative times
- **Projects tab:** NAME is as wide as the longest name, and a wide window's spare room goes to NAME, then CATEGORY and TAGS until their values show whole, then evenly to every column. When the window is narrow the other columns give way first, LAST WORKED first and STATUS last. A project's unwrapped count goes on its own line under the running counts, so those rows take two lines and select, click, and scroll as one
- **Notes:** the card's Created date shows the same short way as Edited
- **README:** every row action (with screenshots of the menus), the Stats tab, and the settings in `config.json`
- **Fixes:** closing roost no longer raises an error when a redraw lands while the screen is being taken down
- **Tests:** split from one file into files by area, with shared helpers in `tests/support.py`
- **Log:** the "Claude Code X is newer than roost was tested with" line is logged once per Claude Code version (kept in `~/.roost/compat-logged.json`), not on every launch

## 2026.10.21

- **Notes tab:** notes are grouped into Backlog (tagged `backlog`), Recaps (tagged `recap`, folded until opened), and Notes. A note tagged both is under Backlog. Search matches a note's tags, so `/backlog` finds them
- **Projects tab:** Sessions no longer show under projects, and projects don't open. The SESSIONS column counts running Sessions, then unwrapped ones (closed, no Handoff covers them, not dismissed), like "○1 · 1 unwrapped". A project's Show its Sessions opens the Sessions tab on All, searching `project:<its id>`; esc clears it
- **Dismiss:** a closed Session's actions on the Sessions tab can dismiss it: it's marked dismissed, listed last, and no longer counted as unwrapped. Undismiss brings it back. Only roost's own list (`~/.roost/dismissed.json`) changes
- **Summaries:** a summary estimated over the cap runs with the cap raised for that run, and the confirm box says so, instead of stopping partway with nothing written
- **roost adopt:** a Move into `handoffs/` or `_archive/` is refused (it could replace an archived file or the First Handoff); a plan shown in a run keeps its new names for the rest of the run, even if skipped; a plan's Status is one or two lowercase words
- **Tests:** written by a separate test-writing agent and shown failing before each change; new tests cover the adopt plan checks, Handoff ownership, redaction, and every key the key bar names

## 2026.10.20

- **Session recaps are notes:** a recap is now a note tagged `recap` in `~/.roost/notes/`, shown on the Notes tab. Recaps in the old `~/.roost/sessions/` folder move there the first time roost runs. Starting a Session from a recap opens it in the recap's folder
- **Wrapping up:** a Session whose Handoff or recap is being written shows in its own section at the bottom of the Sessions tab. Up to three summaries run at once
- **Claude Desktop:** a Session open in Claude Desktop shows as "open in Desktop", and Enter switches to Desktop instead of saying its window wasn't found
- **Copying text:** drag over text and it's copied when you let go of the mouse; ctrl+c copies a selection too. A double or triple click acts on a row and no longer selects a whole panel's text
- **Clicks and boxes:** a triple-click acts once, a confirm box can be answered on any tab, and an error in an action shows as a box instead of closing roost
- **Summaries:** if you edit a project's README while its summary runs, roost keeps your edit and leaves the README alone
- **roost update** now updates Claude Code's copy of the plugin too, so Sessions get the new skills. Updating from 2026.10.19 runs the old update step, so run `roost update` once more afterward (roost reminds you at startup)
- **roost adopt** archives a second adopt on the same day beside the first, and refuses a plan that moves two folders to one path
- **Fixes:** a failed update says what's in place when it stops partway; pricing waits an hour before retrying a failed fetch; a recap marker can't point outside its project's `handoffs/`
- **README** brought up to date, with screenshots; `roost --demo` has its own made-up categories and numbers

## 2026.10.19

The first public release.

- **Sessions:** every Claude Code session in one table, with its state, last use, context size, and whether its prompt cache is still warm. Views for Running, Recently closed, Closed, Stale, and All. Open, pin, stop, restart, rename, or delete a Session from its actions
- **Projects:** project folders with their Sessions and Handoffs, by section, category, and tags. Start a Session for a project, or make one from a Session or a note. `roost adopt` brings existing folders into roost's project layout, planning each one and applying it only after you approve
- **Notes:** a Markdown scratchpad in `~/.roost/notes/`, read in a card beside the list and opened in your editor. `roost note` and `/roost:note` save one from the shell or a Session
- **Stats:** what your Claude work costs at list price, by day, kind, and project, with a calendar, records, and an optional monthly limit
- **ctrl+n, from any tab:** a new Project, Session, or Note; P, S, or N picks
- **The plugin:** `/roost:pickup` and `/roost:wrap-up` carry a Session's work into the next through Handoffs
- **Install** with `brew install dmcphearson/tap/roost`, or the curl script. `roost --demo` shows made-up Sessions, projects, and notes for screenshots
