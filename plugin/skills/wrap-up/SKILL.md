---
name: wrap-up
description: Wrap up this Session so it can be closed without losing what it learned - write its Handoff through roost handoff and rewrite the project's README to the current state. Use when the person runs /wrap-up, or asks to wrap up, hand off, save where things stand, or finish for now.
---

# Wrap up this Session

Wrap-up leaves the next Session everything it needs: a Handoff in the project, and a README that says where the work stands. The Session itself stays open; never end it, run `/exit` or `/clear`, or close it for the person.

1. **Draft the summary** from this Session's conversation, in Markdown, with these headings (the Handoff puts it under `## Summary`, so use `###`):

   - `### What I did`: the work, in a few bullets.
   - `### What worked`
   - `### What failed and why`: dead ends, errors, and wrong turns, with the reason, so nobody repeats them. Write "Nothing" if nothing failed.
   - `### Decisions`: what the person decided, in their terms. Don't record your own guesses as decisions.
   - `### Open questions`: anything unresolved, and who has to answer it.
   - `### Next`: the next concrete step.

   Leave out what roost adds itself (date, model, tokens, cost, files changed, failed commands, commits, PRs). Never paste secrets, tokens, or passwords, even redacted.

2. **Write the Handoff.** Pipe the summary in through a quoted heredoc, so the shell doesn't expand anything in it:

   ```
   roost handoff --session ${CLAUDE_SESSION_ID} <<'ROOST_SUMMARY'
   …the summary…
   ROOST_SUMMARY
   ```

   - It prints `Wrote <path>`. The project folder is the folder above `handoffs/`; in a sub-project (`<project>/sub/<slug>/`) that's the sub-project's folder, and its parent project isn't touched. A path in `~/.roost/notes/` is a Session recap: skip steps 3 and 4, since a recap has no README or status.
   - If it says it redacted secrets, tell the person what kinds, and that they should check the Handoff and rotate anything real.
   - **"no project lists Session …":** this Session has no project or recap. Ask the person which they want:
     - **A Session recap**, for one-off work: suggest it when the work has no finish line. Run the handoff again with `--recap --summary "<one sentence>"` added. The recap is a note in `~/.roost/notes/`, named after the Session and tagged `recap`, so it shows on roost's Notes tab; later wrap-ups add to it, and it can become a project from roost (enter on the recap, then Make it a project).
     - **A new project**, when the work has a finish line: suggest an id and a one-sentence description. Run `roost project new <id> --description "…"`, then `roost project brief <id> --session ${CLAUDE_SESSION_ID}` to list this Session in it, then run the handoff again.
     Don't pick for them.
   - Any other error ("more than one project lists this Session", "already exists"): show it to the person and stop. Don't work around it.

3. **Rewrite the README body** so it describes the project now, not its history. Read `README.md` first.

   - Edit only below the frontmatter's closing `---`. Never change the frontmatter (`description`, `status`, `sessions`, `category`) by hand; roost commands own it.
   - Keep it under about 100 lines, in these sections: `## Current state`, `## Next`, `## Key decisions` (dated, newest kept; drop ones that no longer matter), and `## Files` (every other file in the folder, one line each saying what it is; skip `handoffs/` and `sub/`).
   - Keep any other section the person wrote that still holds.
   - The Handoff holds the story of this Session. The README holds only where things stand.

4. **Status.** Run `roost project status <id>` (a sub-project's id is `<project>/<slug>`; its statuses are active, blocked, and done). If this Session's work changes it (done, blocked, or back to active), propose the new value and ask. Only after a yes, run `roost project status <id> <value>`. Closing a project (`closed`) hides it on the home screen, so say that when you propose it. If nothing changed, leave it and don't ask.

5. **Description.** Run `roost project description <id>`. If it prints `(no description)`, write one sentence saying what the project (or sub-project) is, from this Session's work, and set it with `roost project description <id> "<sentence>"`. If it has one and this Session's work changed what the project is about, propose a new sentence and ask; set it only after a yes. Otherwise leave it.

6. **Finish** with the Handoff's path and this line: "Safe to /clear or exit." Then stop.

For flags, run `roost handoff --help` or `roost project --help`.
