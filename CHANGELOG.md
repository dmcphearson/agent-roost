# Changelog

## 2026.10.19

The first public release.

- **Sessions:** every Claude Code session in one table, with its state, last use, context size, and whether its prompt cache is still warm. Views for Running, Recently closed, Closed, Stale, and All. Open, pin, stop, restart, rename, or delete a Session from its actions
- **Projects:** project folders with their Sessions and Handoffs, by section, category, and tags. Start a Session for a project, or make one from a Session or a note. `roost adopt` brings existing folders into roost's project layout, planning each one and applying it only after you approve
- **Notes:** a Markdown scratchpad in `~/.roost/notes/`, read in a card beside the list and opened in your editor. `roost note` and `/roost:note` save one from the shell or a Session
- **Stats:** what your Claude work costs at list price, by day, kind, and project, with a calendar, records, and an optional monthly limit
- **ctrl+n, from any tab:** a new Project, Session, or Note; P, S, or N picks
- **The plugin:** `/roost:pickup` and `/roost:wrap-up` carry a Session's work into the next through Handoffs
- **Install** with Homebrew (this repo is its own tap) or the curl script. `roost --demo` shows made-up Sessions, projects, and notes for screenshots
