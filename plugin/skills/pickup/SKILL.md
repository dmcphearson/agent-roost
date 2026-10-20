---
name: pickup
description: Load a roost project (or a Session recap, or a note) into this Session so it catches up on where the work stands. Use when the person runs /pickup, or asks to pick up, continue, or load a project. Agent Roost starts new Sessions with /roost:pickup <project> as their first prompt.
argument-hint: "[project]"
---

# Pick up a project

The project to pick up: `$ARGUMENTS`

1. **No project given?** Run `roost project list` and ask the person which one to pick up. Show the list as it prints; don't guess.
2. **Catch up.** Run:

   ```
   roost project brief <project> --session ${CLAUDE_SESSION_ID}
   ```

   It prints the project's README, its newest Handoffs, and the names of its notes (or the Session recap, or the note: roost passes a note as its file's path), and lists this Session in the project's `sessions:` (or the note's). If it fails, show the person the error and stop.
3. **Read only what the brief points to.** The README and the Handoffs are the catch-up. Open a working doc only when the task needs it; don't read `data/`, `out/`, or `_archive/`, and don't read older Handoffs unless the person asks.
4. **Report back in a few lines:** where things stand, what's next (from the README's Next and the newest Handoff), and any open questions the Handoffs left. Then ask what to work on, unless the person already said.

Don't change directory, don't edit the README here, and never resume an old Session (`claude --resume`). For flags, run `roost project --help` or `roost project brief --help`.
