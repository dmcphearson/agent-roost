"""The Sessions tab."""
import curses, os, textwrap, threading, time
from pathlib import Path

from roost.core.statsdata import log_spend
from roost.core.config import VIEWS
from roost.core.sessiondata import (
    CACHE_WARN,
    SECTIONS,
    SORTS,
    STALE_TOKENS,
    active,
    ago,
    cache_left,
    group,
    in_view,
    matches,
    next_cost,
    span,
    stale,
    visible,
)
from roost.core.summaries import summary_settings
from roost.core.advice import ASK_SYSTEM, ask_estimate, ask_input, parse_advice
from roost.core.formatting import clip, ktok, plain_text, redact, short
from roost.app.shell import BAR_KEYS, CLICK, PIN, TAB_X
from roost.app.widgets import Widget
from roost.core import config
from roost.core import sessiondata
from roost.core import projectdata
from roost.core import pricing
from roost.core import summaries
from roost.core import terminals

if Widget:
    from textual.containers import Vertical
    from roost.app.widgets import Region
else:
    Region = None


class SessionsTab:
    """The Sessions tab."""

    def context_note(self, s):
        """CONTEXT and what the next prompt costs, short, for the Sessions panel."""
        ctx = ktok(s["tokens"])
        if s["state"] == "closed" and self.wrapped(s):
            return "— · wrapped up, never reopened"
        if s.get("compacted") and s["state"] != "busy":
            return f"{ctx} · compacted, next prompt is cheap"
        left = cache_left(s)
        if left is None:
            return ctx
        if left > 0:
            return f"{ctx} · next prompt cheap for {span(left)}"
        return f"{ctx} · next prompt costs about {next_cost(s):g}× (cache expired)"

    def cost_line(self, s):
        """What the next message costs, and what to do about it."""
        left, mult = cache_left(s), next_cost(s)
        if left is None:
            return None
        ctx = ktok(s["tokens"])
        if s["state"] == "closed" and self.wrapped(s):
            return "wrapped up: its recap or Handoff has it all, so it's never reopened · enter starts a fresh Session from it", self.DIM
        if s.get("compacted") and s["state"] != "busy":
            return f"compacted to {ctx}: the next prompt starts from that summary, so it costs little", self.DIM
        if left > 0:
            text = f"next prompt is cheap for {span(left)}: Claude reuses the saved {ctx} at about 0.1× the price"
            if left <= CACHE_WARN and s["tokens"] >= STALE_TOKENS:
                return text + " · wrap up or compact now, before it costs more", self.YELLOW | curses.A_BOLD
            return text, self.DIM
        text = f"next prompt costs about {mult:g}×: the saved copy expired, so Claude re-reads all {ctx} ({ktok(s['tokens'] * mult)} tokens' worth)"
        if s["state"] not in ("busy", "opening") and s["kind"] != "interactive" and not s.get("attached"):
            what = "its Handoff" if self.project_of(s) else "a Session recap"
            return text + f" · enter can write {what} from the transcript instead", self.RED if stale(s) else self.DIM
        if stale(s):
            return text + f" · idle {span(time.time() - active(s))}: save what matters to files and close it", self.RED
        return text, self.DIM

    def draw_sessions(self):
        """What the Sessions tab shows: its rows (in the view, matching the search, sorted), its corner
        note, and its key bar. SessionsView draws them."""
        for x in self.sessions:  # the project that lists it, for the PROJECT column and its sort
            p = self.project_of(x)
            x["proj"] = projectdata.label_of(p) if p else ""
        self.wrapping = self.summarizing()  # the Wrapping up section, fixed for this draw
        rows = self.srows = visible(self.sessions, self.view, self.query, self.sort, self.wrapping)
        self.sel = max(0, min(self.sel, len(rows) - 1))
        live = sum(s["state"] != "closed" for s in self.sessions)
        waiting = sum(s["state"] in ("waiting", "blocked") for s in self.sessions)
        self.corner = (f"{live} live · {waiting} need you · {len(self.sessions)} in 90 days", self.DIM)
        self.key_bar(BAR_KEYS, self.msg or (  # a warning goes in the panel's top edge instead
            f"roost {self.update_note} is out: quit and run {'brew upgrade roost' if config.BREW else 'roost update'}" if self.update_note else ""),
            (self.ACCENT if self.msg else self.YELLOW) | curses.A_BOLD)
        return rows

    def session_lines(self, rows, page):
        """The list's lines: ("r", row index) for a Session, and where a view holds more than one
        section, ("h", section) at the start of each and ("s", None) between them. Scrolls self.top
        so the selected row (and its section's header, at the start of a section) is in the page."""
        lines, sec_line = [], None
        sec = lambda s: group(s, self.wrapping)
        multi = len({sec(s) for s in rows}) > 1
        for idx, s in enumerate(rows):
            g = sec(s)
            if multi and (idx == 0 or sec(rows[idx - 1]) != g):
                if idx:
                    lines.append(("s", None))  # a blank line between sections
                lines.append(("h", g))
            lines.append(("r", idx))
            if idx == self.sel:
                sec_line = len(lines) - 1
        if sec_line is not None:
            if sec_line < self.top:
                self.top = sec_line
            elif sec_line >= self.top + page:
                self.top = sec_line - page + 1
            if self.top == sec_line and sec_line > 0 and lines[sec_line - 1][0] == "h":
                self.top -= 1  # keep a section's header in view with its first row
        self.top = max(0, min(self.top, max(len(lines) - page, 0)))
        return lines

    def session_warnings(self, s):
        """What the panel's top edge warns about for s, as (text, attr), and its cleanup note for Id."""
        warns = [(self.warning, self.YELLOW)] if self.warning else []
        cleanup = ""
        left = sessiondata.days_left(s, self.rules)
        if left == float("inf"):
            cleanup = "kept (Claude Desktop sessions aren't cleaned up)"
        elif left is not None and left <= 0:
            warns.append(("past its cleanup date: Claude deletes it on its next sweep · restarting it (enter) resets the clock", self.RED))
        elif left is not None:
            n = int(left)
            when_ = "within a day" if n == 0 else f"in {n} day{'s' if n != 1 else ''}"
            if n <= 20:
                warns.append((f"deleted {when_} · restarting it (enter) resets the clock", self.RED if n <= 10 else self.YELLOW))
            else:
                cleanup = f"deleted {when_}"
        if s.get("partial"):
            warns.append((f"history missing: the full version is {s['partial']} (closed)", self.YELLOW))
        job = self.summaries.get(s["id"])
        if job and job["state"] != "done":
            warns.append((f"summary {job['state']}{' ' + self.progress(job) if job['state'] == 'running' else ''}: {job['text']}",
                          self.RED if job["state"] == "failed" else self.ACCENT))
        cost = self.cost_line(s)
        if cost and cost[1] != self.DIM:  # advice worth acting on (cache about to expire, stale) is a warning;
            warns.append((cost[0].split(" · ", 1)[-1], cost[1]))  # Context already says what it costs
        return warns, cleanup

    MAX_SUMMARIES = 3  # Handoffs and recaps written at once; two in one project take turns on its README

    def ask_summary(self, s):
        """w on a Session: offer to write its Handoff and README update from its transcript, with the
        estimated cost. Never on a working Session or one open in a window. Up to MAX_SUMMARIES run at
        once; an adopt or a question runs alone."""
        other = next((j for k, j in self.summaries.items() if j["state"] == "running" and ":" in k), None)
        if s["id"] in self.summarizing():
            self.msg = "Its summary is already running"
        elif other:
            self.msg = f"Wait for {other['name']}'s run to finish first"
        elif len(self.summarizing()) >= self.MAX_SUMMARIES:
            self.msg = f"{self.MAX_SUMMARIES} summaries are running: wait for one"
        elif s["state"] in ("busy", "opening"):
            self.msg = "It's working: wait until it's idle"
        elif s["kind"] == "interactive" or s.get("attached"):
            self.msg = "It's open in a window: exit it there first (or run /wrap-up in it)"
        else:
            try:
                plan = summaries.summary_plan(s["id"])
            except (ValueError, OSError) as e:
                self.msg = f"Can't summarize it: {e}"
                return
            model, cap = summary_settings()
            self.offer = (model, cap, summaries.summary_estimate(plan, model, pricing.prices()), plan.get("project", True))
            self.confirm = ("summary", s)

    def ask_advice(self, s):
        running = self.busy_job()
        if running:
            self.msg = f"Wait for {running['name']}'s run to finish first"
            return None
        model, cap = summary_settings()
        try:
            text = ask_input(s, self.projects or [], self.recap_of(s), self.project_of(s))
        except (ValueError, OSError) as e:
            self.msg = f"Can't ask about it: {e}"
            return None
        self.offer = (model, cap, ask_estimate(text, model, pricing.prices()), text)
        self.confirm = ("ask", s)
        return None

    def run_ask(self, s):
        model, cap, _, text = self.offer
        ids = [p["id"] for p in self.projects or [] if not p.get("closed")]
        job = self.summaries[f"ask:{s['id']}"] = {"state": "running", "name": s["name"], "step": 1, "steps": 1,
                                                   "started": time.time(), "ask": s["id"]}

        def work():
            try:
                body, cost = summaries.claude_print(model, ASK_SYSTEM, text, cap)
                log_spend("Ask", cost, session=s["id"], model=model)
                job.update(state="done", advice=parse_advice(redact(body)[0], ids), text=f"Advice on {s['name']} (${cost:.2f})")
            except Exception as e:
                config.log(f"ask {s['id'][:8]}: {e}")
                job.update(state="failed", text=f"Couldn't get advice on {s['name']}: {e}")
        threading.Thread(target=work, daemon=True).start()
        self.msg = f"Asking about {s['name']} in the background…"

    def advice_choices(self, s, a):
        """The advice's choices as (label, function), the advised one first. Each label says exactly
        what it does; anything that spends or deletes asks again."""
        proj, v = self.project_of(s), a["verdict"]
        p = next((q for q in self.projects or [] if q["id"] == a["project"]), None)
        what = "Handoff" if proj else "Session recap"
        add = lambda: self.link(s, p)
        both = lambda: (self.link(s, p), self.ask_summary(s), None)[-1]  # a menu's return value would quit roost
        write = lambda: self.ask_summary(s)
        delete = lambda: self.ask_confirm("delete", s)
        leave = lambda: setattr(self, "msg", "Left as it is")
        opts = {
            "add-to-project": [(f"Add it to {a['project']}, then write its Handoff (shows the cost first)", both),
                               (f"Only add it to {a['project']}", add)] if p else [],
            "handoff": [(f"Write its Handoff from the transcript (shows the cost first)", write)] if proj else [],
            "recap": [(f"Write its Session recap from the transcript (shows the cost first)", write)] if not proj else [],
            "delete": [("Delete it: its history moves to the Trash (asks again)", delete)],
            "keep": [("Leave it as it is", leave)],
        }
        out = [(f"{label}  · advised" if i == 0 else label, fn) for i, (label, fn) in enumerate(opts.get(v, []))]
        if v != "keep":
            out.append(("Leave it as it is", leave))
        if v not in ("recap", "handoff") and not (v == "add-to-project" and p):
            out.append((f"Write its {what} from the transcript (shows the cost first)", write))
        if v != "delete":
            out.append(("Delete it: its history moves to the Trash (asks again)", delete))
        return out

    def show_advice(self, s, a):
        """The advice in a menu: what it found, then each choice spelled out, the advised one selected."""
        body = [f"{'Finished.' if a['finished'] else 'Not finished.'} {a['why']}"]
        if a["open"]:
            body += ["", "Open items:"] + [f"• {x}" for x in a["open"]]
        if a["warnings"]:
            body += ["", "Before you delete it:"] + [f"• {x}" for x in a["warnings"]]
        self.choose(f"What to do with {clip(s['name'], 50)}", self.advice_choices(s, a), lambda fn: fn(), body=body)

    def run_summary(self, s):
        """Stop an idle background Session, then summarize it in a thread; the row shows how it went."""
        if s.get("attach"):
            try:
                sessiondata.stop_session(s["attach"])
            except Exception as e:
                config.log(f"stop {s['id'][:8]} for summary: {e}")
                self.msg = f"Couldn't stop it first, so nothing ran: {e}"
                return
        model, cap = self.offer[:2]
        steps = 3 if self.offer[3] else 2  # draft and review, then the README rewrite for a project
        job = self.summaries[s["id"]] = {"state": "running", "text": "writing its Handoff from the transcript", "name": s["name"],
                                         "step": 0, "steps": steps, "started": time.time()}

        def work():
            try:
                path, kinds, cost, updated = summaries.summarize(s["id"], model, cap, progress=lambda n: job.update(step=n))
                readme = {True: " and updated the README", False: "; left the README alone: it was edited while the summary ran"}.get(updated, "")
                job.update(state="done", text=f"Wrote {short(str(path))}{readme} (${cost:.2f})" + (
                    f"; redacted {len(kinds)} secret{'s' if len(kinds) != 1 else ''}: check it and rotate anything real" if kinds else ""))
            except Exception as e:
                config.log(f"summary {s['id'][:8]}: {e}")
                job.update(state="failed", text=f"nothing written: {e}")
        threading.Thread(target=work, daemon=True).start()
        self.msg = f"Summarizing {s['name']} in the background…"
        self.refresh()

    SESSIONS_HELP = [
        ("Views", "Running · Recently closed (3 hours) · Closed · Stale (big, untouched 2 days) · All"),
        ("Sort", "click a column header to sort by it, again to reverse"),
        ("Status", "● working · ◆ needs input · ○ idle · ◔ expires in 8m: next prompt cheap 8 more min · ✕ cache expired: costs ~2× · ○ compacted: small again · ✓ wrapped up: its Handoff or recap has it all"),
    ]

    def project_of(self, s):
        """The project that lists a Session, or None."""
        ids = set(s.get("ids") or [s["id"]])
        return next((p for p in self.projects or [] if ids & set(p["sessions"])), None)

    def resume_refused(self, s):
        """Roost never resumes project work (claude --resume): a closed Session a project lists is
        picked up by a fresh Session instead. Says so and returns True when that's the case."""
        p = s["state"] == "closed" and not s.get("attach") and self.project_of(s)
        if p:
            self.msg = f"That's {projectdata.label_of(p)} work: roost starts a fresh Session for it instead of reopening this one (enter)"
        elif s["state"] == "closed" and self.wrapped(s):
            self.msg = "It's wrapped up: start a fresh Session from its recap instead of reopening it (enter)"
            return True
        return bool(p)

    def link(self, s, p):
        try:
            added, moved = projectdata.assign_session(s["id"], s["name"], p["path"])
        except (ValueError, OSError) as e:
            self.msg = f"Couldn't add it: {e}"
            return None
        name = projectdata.label_of(p)
        self.msg = (f"Moved {s['name']} from {moved[0]} to {name}" if moved else
                    f"Added {s['name']} to {name}" if added else f"Already in {name}")
        self.load_projects()
        return None

    def sessions_typing(self):
        return bool(self.confirm)

    # ---------- row actions ----------
    # Enter or a right-click on a row opens its actions (menu); a double-click runs the first one.
    # These are the only ways to act on a row: no single-letter shortcuts.

    def session_actions(self, s):
        """What can be done with one Session, most likely first."""
        proj = self.project_of(s)
        in_window = s["kind"] == "interactive" or s.get("attached")
        closed = s["state"] == "closed" and not s.get("attach")
        acts = []
        recap = None if proj else self.recap_of(s)
        done = closed and not proj and self.wrapped(s)  # its recap has everything: never reopen it
        if closed and proj:  # roost never resumes project work: a fresh Session picks it up
            if not proj["closed"]:
                acts.append((f"Start a fresh Session for {projectdata.label_of(proj)}", lambda: self.start_for(proj)))
        elif done:
            acts.append(("Start a fresh Session from its recap", lambda: self.start_from_recap(recap, s)))
        else:
            label = "Go to its window" if in_window else "Open in a new window" if self.term else "Open here"
            acts.append((label, lambda: self.open_one(s)))
        if recap:
            acts.append((f"Read its Session recap in {self.editor_name().split()[0]}", lambda: self.edit(recap["path"])))
            acts.append(("Make it a project… (its recap's Handoffs move in)", lambda: self.new_project(recap)))
        elif not proj:
            acts.append(("New project from this Session…", lambda: self.new_project_from(s)))
        others = [p for p in self.projects or [] if not p["closed"] and p is not proj]
        if others:
            acts.append(("Move to another project…" if proj else "Add to a project…", lambda: self.add_to_project(s)))
        if not in_window:
            acts.append((f"Write its {'Handoff' if proj else 'Session recap'} from the transcript…", lambda: self.ask_summary(s)))
            if s["state"] not in ("busy", "opening"):
                acts.append(("Ask what to do with it…", lambda: self.ask_advice(s)))
        if s.get("attach") and s["kind"] != "interactive":
            acts.append(("Unpin" if s.get("pinned") else "Pin: keep it running when idle", lambda: self.pin(s)))
        elif closed and not proj and not done:
            acts.append(("Restart in the background, pinned", lambda: self.pin(s)))
        if not in_window:
            acts.append(("Rename…", lambda: self.rename(s)))
        if s.get("attach"):
            acts.append(("Stop…", lambda: self.ask_confirm("stop", s)))
        elif closed:
            acts.append(("Delete to the Trash…", lambda: self.ask_confirm("delete", s)))
        acts.append(("Copy its id", lambda: self.copy_id(s)))
        if s.get("cwd") and os.path.isdir(s["cwd"]):
            acts.append(("Show its folder in Finder", lambda: self.show_in_finder(s["cwd"])))
        return acts

    def ask_confirm(self, action, s):
        self.confirm = (action, s)

    def open_one(self, s):
        """Open a Session: in a new window (or jump to its window), or here when there's no terminal."""
        if self.resume_refused(s):
            return None
        if not self.term:
            return s
        self.msg = terminals.open_in_window(s, self.term)
        self.refresh()
        return None

    def pin(self, s):
        """Pin or unpin a background Session; restart a closed one in the background, pinned."""
        if not s.get("attach"):
            self.msg = f"Restarting {s['name']} in the background…"
            self.draw()
            try:
                sessiondata.revive_pinned(s)
                self.msg = f"Pinned {s['name']}: running in the background, won't be closed when idle"
            except Exception as e:
                config.log(f"restart {s['id'][:8]}: {e}")
                self.msg = f"Couldn't restart it: {e}"
        else:
            try:
                sessiondata.set_pin(s["attach"], not s.get("pinned"))
                self.msg = f"{'Unpinned' if s.get('pinned') else 'Pinned'} {s['name']}" + \
                    ("" if s.get("pinned") else ": it won't be closed when idle")
            except Exception as e:
                config.log(f"pin {s['id'][:8]}: {e}")
                self.msg = f"Couldn't change pin: {e}"
        self.refresh()
        return None

    def rename(self, s):
        def done(text):
            self.msg = f"Renaming {s['name']}…"
            self.draw()
            try:
                sessiondata.rename_session(s, text)
                self.msg = f"Renamed to {' '.join(text.split())}"
                self.refresh()
            except Exception as e:
                config.log(f"rename {s['id'][:8]}: {e}")
                self.msg = f"Couldn't rename: {e}"
            return None
        self.ask("", s["name"], done, "rename", title=f"Rename {clip(s['name'], 40)}")
        return None

    def add_to_project(self, s):
        cur = self.project_of(s)
        ps = [p for p in self.projects or [] if not p["closed"] and p is not cur]
        self.choose(f"{'Move' if cur else 'Add'} {clip(s['name'], 40)} to a project",
                    [(projectdata.label_of(p), p) for p in ps], lambda p: self.link(s, p), filtering=True)
        return None

    def new_project_from(self, s):
        """Make a project for a Session: its name and folder (from the Session's name), then a one-sentence
        summary (the name to start with), then list the Session in it and offer its Handoff."""
        def made(pid, label, text):
            try:
                folder = projectdata.create_project(pid, text, label)
            except (ValueError, OSError) as e:
                self.msg = f"Couldn't create it: {e}"
                return None
            self.load_projects()
            p = next((p for p in self.projects or [] if p["path"] == str(folder)), None)
            if p is None:
                self.msg = f"Created {short(str(folder))}"
                return None
            self.link(s, p)  # re-reads the projects
            if (self.project_of(s) or {}).get("path") != p["path"]:
                return None  # link() said why
            self.msg = f"Created {label} with {s['name']} in it"
            if s["kind"] == "interactive" or s.get("attached"):
                self.msg += ": run /wrap-up in it to write its first Handoff"
            elif s["state"] not in ("busy", "opening"):
                self.ask_summary(s)  # its first Handoff, after the cost is shown
            return None

        return self.ask_project(s["name"], s["name"], made)

    def keys_sessions(self, k, rows):
        if isinstance(k, tuple):
            _, mx, my, bstate = k
            if my == 2 and bstate & CLICK:
                for x0, x1, i in self.tabs:
                    if x0 <= mx < x1:
                        self.view, self.sel, self.top = i, 0, 0
            elif my == self.list_top and bstate & CLICK:
                for x0, x1, title in self.col_x:
                    if x0 <= mx <= x1:
                        same = self.sort[0] == title
                        self.sort = (title, not self.sort[1] if same else SORTS[title][1])
                        self.sel = self.top = 0
            elif my in self.row_y:
                idx = self.row_y[my]
                how = self.click(k, idx)
                if how:
                    self.sel = idx
                if how == "menu":
                    return self.menu(rows[idx]["name"], self.session_actions(rows[idx]))
                if how == "double":
                    acts = self.session_actions(rows[idx])
                    return acts[0][1]() if acts else None
            self.sel = max(0, min(self.sel, len(rows) - 1))
            return None
        to = self.move(k, self.sel, len(rows))
        if to is not None:
            self.sel = to
        elif k == "right":
            self.view = (self.view + 1) % len(VIEWS); self.sel = self.top = 0
        elif k == "left":
            self.view = (self.view - 1) % len(VIEWS); self.sel = self.top = 0
        elif k == "enter" and rows:
            return self.menu(rows[self.sel]["name"], self.session_actions(rows[self.sel]))
        self.sel = max(0, min(self.sel, len(rows) - 1))
        return None


if Region:
    class ViewRow(Region):
        """Running · Recently closed · All, each with its count, then the search."""
        def lines(self):
            ui = self.ui
            row, x, ui.tabs = self.line(), TAB_X - 1, []  # each view's label starts with a space, as the tab names do
            for i, label in enumerate(VIEWS):
                count = sum(in_view(s, i, ui.summarizing()) and matches(s, ui.query) for s in ui.sessions)
                tab = f" {label} ({count}) "  # a count in ( ), as the Projects sections show theirs
                ui.tabs.append((x, x + len(tab), i))
                row.put(x, tab, curses.A_REVERSE | curses.A_BOLD if i == ui.view else ui.DIM)
                x += len(tab) + 1
            if ui.searching or ui.query:
                row.put(x + 2, f"/ {ui.query}" + ("▏" if ui.searching else ""), ui.ACCENT if ui.searching else ui.DIM)
            return [row]

    class SessionList(Region):
        """The column headers (a click sorts by one), then the Sessions, a section header where each starts."""
        def lines(self):
            ui, w, y0 = self.ui, self.size.width, self.region.y
            rows = getattr(ui, "srows", [])
            page = ui.page = max(self.size.height - 3, 1)  # the headers, a blank row, and the position at the bottom
            name_w = max(16, min(ui.name_w, w - 90))  # NAME is name_width wide (config); PROJECT gets whatever is left
            cols = [("NAME", name_w), ("STATUS", 19), ("LAST ACTIVE", 14), ("CONTEXT", 10), ("PROJECT", 0)]
            head, cx, ui.col_x, ui.list_top = self.line(), 5, [], y0
            for title, cw in cols:
                on = ui.sort[0] == title
                head.put(cx, title + (" ▾" if ui.sort[1] else " ▴") if on else title, curses.A_BOLD | (ui.ACCENT if on else ui.DIM))
                ui.col_x.append((cx, cx + (cw or w) - 1, title))
                cx += cw
            out = [head, self.line()]
            lines = ui.session_lines(rows, page)
            if not rows:
                note = self.line()
                note.put(3, "Nothing here. Try another view, or esc to clear the search.", ui.DIM)
                out.append(note)
            ui.row_y = {}
            for i, (kind_, val) in enumerate(lines[ui.top:ui.top + page]):
                y = y0 + 2 + i
                if kind_ == "s":
                    out.append(self.line())
                    continue
                if kind_ == "h":
                    row = self.line()
                    n = sum(group(s, ui.wrapping) == val for s in rows)
                    label = f"{SECTIONS[val]} ({n}) "
                    row.put(2, label, curses.A_BOLD | (ui.ACCENT if val == 0 else 0))
                    row.rule(2 + len(label), w - len(label) - 4, ui.DIM)
                    out.append(row)
                    continue
                idx, s = val, rows[val]
                ui.row_y[y] = idx
                hl = idx == ui.sel
                look = ui.selected if hl else (lambda a: a)
                row = self.line()
                if hl:
                    row.put(1, " " * (w - 2), look(0))
                    row.put(1, "›", look(curses.A_BOLD | ui.ACCENT))
                cx = 5
                # pinned sessions keep their pin while open; partial copies get a warning mark
                name = (PIN + " " if s.get("pinned") and group(s) == 0 else "") + s["name"] + (" ⚠" if s.get("partial") else "")
                row.put(cx, clip(name, name_w - 2), look(curses.A_BOLD if hl or group(s) == 0 else 0)); cx += name_w
                text, attr = ui.status_cell(s)
                row.put(cx, text, look(attr)); cx += 19
                row.put(cx, ago(s), look(0)); cx += 14
                done = s["state"] == "closed" and ui.wrapped(s)  # never re-read, so its size costs nothing
                row.put(cx, ("—" if done else ktok(s["tokens"])).rjust(5), look(ui.DIM if done else 0)); cx += 10
                row.put(cx, clip(s.get("proj") or "", w - cx - 2), look(0))  # its folder is in the panel
                out.append(row)
            if len(lines) > page:
                while len(out) < self.size.height - 1:
                    out.append(self.line())
                pos = self.line()
                pos.put(w - 12, f"{ui.sel + 1}/{len(rows)}", ui.DIM)
                out.append(pos)
            return out

    class SessionPanel(Region):
        """The selected Session: warnings in the rule over it, its name and status, its facts in two
        columns, then two lines of its last message."""
        def lines(self):
            ui, w = self.ui, self.size.width
            rows = getattr(ui, "srows", [])
            edge = self.line()
            edge.rule(1, w - 2, ui.DIM)
            out = [edge] + [self.line() for _ in range(max(self.size.height - 1, 0))]
            if not rows or len(out) < 8:
                return out
            s = rows[ui.sel]
            kind = "open in a window" if s.get("attached") else {"interactive": "open in a window", "background": "background"}.get(s["kind"], "closed")
            started = f"{span(time.time() - s['started'])} ago · {kind}" if s.get("started") else kind
            warns, cleanup = ui.session_warnings(s)
            x = 3  # a warning sits in the panel's top edge, where it's seen first and covers nothing
            for text, attr in warns:
                label = f" ⚠ {clip(text, w - x - 6)} "
                edge.put(x, label, attr | curses.A_BOLD)
                x += len(label) + 1
            status, sattr = ui.status_cell(s)
            out[1].put(2, clip(s["name"], w - len(status) - 8), curses.A_BOLD)
            out[1].put(w - len(status) - 3, status, sattr)
            p = ui.project_of(s)
            recap = None if p else ui.recap_of(s)
            project = projectdata.label_of(p) if p else ("none · it has a Session recap" if recap else "none")
            files = ", ".join(Path(f).name for f in s["files"][-3:]) + (f" +{len(s['files']) - 3}" if len(s["files"]) > 3 else "")
            facts = [("Project", project, "Context", ui.context_note(s)),
                     ("Folder", short(s["cwd"]), "Started", started),
                     ("Files", files or "none", "Id", s["id"][:8] + (f" · {cleanup}" if cleanup else ""))]
            half = max((w - 4) // 2, 30)
            for j, (l1, v1, l2, v2) in enumerate(facts):
                row = out[2 + j]
                row.put(2, l1, ui.DIM)
                row.put(12, clip(v1, half - 12), 0)
                row.put(2 + half, l2, ui.DIM)
                row.put(12 + half, clip(v2, w - half - 14), 0)
            text = plain_text(recap["summary"]) if recap and recap.get("summary") else s["summary"]
            lines = textwrap.wrap(" ".join(text.replace("**", "").replace("`", "").split()), w - 4)
            for j, line in enumerate(lines[:2]):
                out[6 + j].put(2, line + (" …" if j == 1 and len(lines) > 2 else ""))
            return out

    class SessionsView(Vertical):
        """The Sessions tab."""
        DEFAULT_CSS = """
        SessionsView ViewRow { height: 1; margin: 1 0 1 0; }
        SessionsView SessionList { height: 1fr; }
        SessionsView SessionPanel { height: 10; }
        """

        def compose(self):
            yield ViewRow()
            yield SessionList()
            yield SessionPanel()
else:
    SessionsView = None
