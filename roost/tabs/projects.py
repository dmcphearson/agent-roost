"""The Projects tab."""
import curses, os, textwrap, threading, time
from datetime import date, datetime
from pathlib import Path

from roost.core.statsdata import log_spend
from roost.core.projectdata import label_of, plural, rename_project, session_name
from roost.core.notedata import read_note, read_notes
from roost.core.summaries import summary_settings
from roost.core.adopt import (
    ADOPT_PROMPT,
    ADOPT_SYSTEM,
    adopt_estimate,
    adopt_input,
    adopt_plan_text,
    apply_plan,
    backfill,
    parse_plan,
    plan_problems,
)
from roost.core.formatting import clip, plain_text, short, short_when, slug
from roost.app.shell import (BAR_KEYS, ENTRIES, LAST_MESSAGE_LINES, RECENT_PROJECT_DAYS, Row, SECTION_RULES, SIDEBAR_W,
                             first_action, grouped)
from roost.core.config import VIEWS
from roost.app.widgets import Widget
from roost.core import config
from roost.core import sessiondata
from roost.core import projectdata
from roost.core import pricing
from roost.core import handoffs as handoffdata
from roost.core import summaries
from roost.core import terminals
from roost.core import adopt as adopting


if Widget:
    from textual.containers import Horizontal, Vertical
    from roost.app.widgets import Region, SearchRow, wren_below
else:
    Region = None


class ProjectsTab:
    """The Projects tab."""

    # roost adopt, from a project's actions: plan (one model call, in the background), read and edit
    # the plan in the editor, apply it, then write Handoffs for the Sessions it lists. Each step asks.
    def adopt_plan_path(self, p):
        return config.ADOPT_PLANS / f"{Path(p['path']).name}.md"

    def busy_job(self):
        return next((j for j in self.summaries.values() if j["state"] == "running"), None)

    def ask_adopt(self, p):
        running = self.busy_job()
        if running:
            self.msg = f"Wait for {running['name']}'s run to finish first"
            return None
        if self.adopt_plan_path(p).exists():  # planned before (here or by roost adopt): reuse it
            self.confirm = ("adopt-review", p)
            return None
        model, cap = summary_settings()
        try:
            est = adopt_estimate(adopt_input(Path(p["path"])), model, pricing.prices())
        except OSError as e:
            self.msg = f"Can't read its folder: {e}"
            return None
        self.offer = (model, cap, est, None)
        self.confirm = ("adopt", p)
        return None

    def run_adopt_plan(self, p):
        model, cap = self.offer[:2]
        folder, saved = Path(p["path"]), self.adopt_plan_path(p)
        job = self.summaries[f"adopt:{p['path']}"] = {"state": "running", "name": label_of(p), "step": 1, "steps": 1,
                                                       "started": time.time(), "adopt": "plan", "path": p["path"]}

        def work():
            try:
                body, cost = summaries.claude_print(model, ADOPT_SYSTEM, ADOPT_PROMPT + adopt_input(folder), cap)
                log_spend("Adopt", cost, project=p["id"], model=model)
                config.ADOPT_PLANS.mkdir(parents=True, exist_ok=True)
                saved.write_text(adopt_plan_text(folder, body))
                job.update(state="done", text=f"Planned {label_of(p)} (${cost:.2f})")
            except Exception as e:
                config.log(f"adopt plan {p['id']}: {e}")
                job.update(state="failed", text=f"Couldn't plan {label_of(p)}, nothing changed: {e}")
        threading.Thread(target=work, daemon=True).start()
        self.msg = f"Planning {label_of(p)} in the background…"

    def adopt_job(self, p):
        return self.summaries.get(f"adopt:{p['path']}")

    def review_adopt(self, p):
        """Open the plan in the editor, then ask to apply it."""
        self.edit(str(self.adopt_plan_path(p)))
        self.confirm = ("adopt-apply", p)

    def apply_adopt(self, p):
        saved = self.adopt_plan_path(p)
        try:
            plan = parse_plan(saved.read_text(errors="replace"))
            new = apply_plan(Path(p["path"]), plan)
        except (ValueError, OSError) as e:
            config.log(f"adopt apply {p['id']}: {e}")
            self.msg = (f"Couldn't apply it: {e}. Steps before the failure are done, and anything they replaced "
                        "is in its _archive/")
            self.load_projects()
            return
        saved.unlink()
        self.load_projects()
        self.msg = f"Adopted {new.name}: replaced files are in _archive/" + (
            f" · its INDEX.md names the old folder: rebuild it" if new.name != Path(p["path"]).name
            and (new.parent / "INDEX.md").exists() else "")
        np = next((q for q in self.projects or [] if q["path"] == str(new)), None)
        if np:
            self.ask_adopt_handoffs(np)

    def ask_adopt_handoffs(self, p):
        """After adopting: offer a Handoff for each Session it lists, at one price. Sessions open in a
        window or working are left for later."""
        model, cap = summary_settings()
        table, sids, total, left = pricing.prices(), [], 0.0, []
        for sid in p["sessions"]:
            s = next((x for x in self.sessions if sid in (x.get("ids") or [x["id"]])), None)
            if s and (s["kind"] == "interactive" or s.get("attached") or s["state"] in ("busy", "opening")):
                left.append(f"{s['name']} (it's open in a window or working)")
                continue
            try:
                est = summaries.summary_estimate(dict(summaries.summary_plan(sid), project=False), model, table)
            except (ValueError, OSError) as e:
                left.append(f"{session_name(sid)} ({e})")
                continue
            sids.append(sid)
            total = None if est is None or total is None else total + est
        if left:
            self.msg += f" · no Handoff yet for {'; '.join(left)}"
        if sids:
            self.offer = (model, cap, total, sids)
            self.confirm = ("adopt-handoffs", p)

    def run_adopt_handoffs(self, p):
        model, cap, _, sids = self.offer
        for sid in sids:  # a background Session is stopped first, as a single Handoff does
            s = next((x for x in self.sessions if sid in (x.get("ids") or [x["id"]])), None)
            if s and s.get("attach"):
                try:
                    sessiondata.stop_session(s["attach"])
                except Exception as e:
                    config.log(f"stop {sid[:8]} for adopt: {e}")
                    sids = [x for x in sids if x != sid]
        if not sids:
            self.msg = "Couldn't stop its Sessions, so nothing ran"
            return
        job = self.summaries[f"adopt:{p['path']}"] = {"state": "running", "name": label_of(p), "step": 1, "steps": len(sids),
                                                       "started": time.time(), "adopt": "handoffs", "path": p["path"]}
        said = []

        def say(line):
            said.append(line)
            job.update(step=min(len(said) + 1, len(sids)))

        def work():
            try:
                spent = backfill(Path(p["path"]), model, cap, say=say, sids=sids)
                skipped = [l for l in said if ": skipped, " in l]
                job.update(state="done", text=f"Wrote {plural(len(said) - len(skipped), 'Handoff')} for {label_of(p)} (${spent:.2f})"
                           + (f"; {len(skipped)} skipped: {skipped[0].strip()}" if skipped else ""))
            except Exception as e:
                config.log(f"adopt handoffs {p['id']}: {e}")
                job.update(state="failed", text=f"Handoffs for {label_of(p)} stopped: {e}")
        threading.Thread(target=work, daemon=True).start()
        self.msg = f"Writing {plural(len(sids), 'Handoff')} for {label_of(p)} in the background…"
        self.refresh()

    def adopt_problems(self, p):
        try:
            return plan_problems(Path(p["path"]), parse_plan(self.adopt_plan_path(p).read_text(errors="replace")))
        except OSError as e:
            return [str(e)]

    def adopt_prompt(self, action, p):
        """An adopt step's question: (title, body lines, keys, color)."""
        name = clip(label_of(p), 60)
        if action in ("adopt", "adopt-handoffs"):
            model, cap, est, sids = self.offer
            cost = f"about ${est:.2f}" if est is not None else "cost unknown (no price list)"
            if action == "adopt":
                return (f"Plan how to adopt {name}?",
                        [f"One model call: {cost} with {model}, capped at ${cap:g}.",
                         "It runs in the background. You read the plan before anything changes."],
                        "y to plan · any other key cancels", self.YELLOW)
            return (f"Write a Handoff for each of its {plural(len(sids), 'Session')}?",
                    [f"From their transcripts: {cost} with {model}.", "Any still running in the background is stopped first."],
                    "y to write · any other key: not now", self.YELLOW)
        if action == "adopt-review":
            return (f"The plan to adopt {name} is ready",
                    [f"Read it, and edit it if you like, in {self.editor_name().split()[0]}. Roost asks before applying it."],
                    "enter to read it · any other key: later", self.GREEN)
        problems = self.adopt_problems(p)
        if problems:
            return ("The plan can't be applied yet", [f"• {x}" for x in problems],
                    "e to edit it · any other key: later", self.RED)
        plan = parse_plan(self.adopt_plan_path(p).read_text(errors="replace"))
        steps = [f"renames the folder {plan['rename']}" if plan["rename"] != Path(p["path"]).name else "",
                 f"moves {plural(len(plan['moves']), 'file')}" if plan["moves"] else "",
                 f"archives {plural(len(plan['archive']), 'file')}" if plan["archive"] else "",
                 f"splits out {plural(len(plan['split']), 'project')}" if plan["split"] else "",
                 "writes a first Handoff" if plan["first"] else "", "rewrites the README"]
        return (f"Apply the plan to {name}?", [f"• It {x}" for x in steps if x] + ["", "Replaced files go to _archive/."],
                "y to apply · e to edit · any other key: later", self.YELLOW)

    def rename_project_ui(self, p):
        """Rename a project or sub-project in two prompts: its label (the README's heading, its name on
        screen), then its folder's name, which follows a new label when it was made from the old one."""
        live = [s["name"] for s in self.linked(p) if s["state"] != "closed"]
        if live:
            self.msg = f"{', '.join(live)} {'is' if len(live) == 1 else 'are'} running in it: close {'it' if len(live) == 1 else 'them'} first"
            return None
        old_label, folder = p.get("title") or label_of(p), Path(p["path"]).name

        def got_label(text):
            label = " ".join(text.split())
            if not label:
                self.msg = "Cancelled: it needs a label"
                return None
            start = slug(label) if label != old_label and folder == slug(old_label) else folder
            self.ask("", start, lambda name: done(label, name.strip()), "rename", title=f"{clip(label, 30)}: its folder name")
            return None

        def done(label, name):
            if label == old_label and name == folder:
                self.msg = "Nothing changed"
                return None
            new = Path(p["path"])
            try:
                if name != folder:
                    new = rename_project(new, name)
                if label != old_label:
                    projectdata.set_label(new, label)
            except (ValueError, OSError) as e:
                self.msg = f"Couldn't rename it: {e}"
                self.load_projects()
                return None
            if p["id"] in self.subs_open:  # a project's sub-projects stay shown under its new name
                self.subs_open.discard(p["id"])
                self.subs_open.add(new.name)
            self.load_projects()
            row = next((r for r in self.project_rows() if r.kind in ("project", "sub") and r.item["path"] == str(new)), None)
            if row:
                self.select_key(self.row_key(row))
            self.msg = (f"Renamed {p['id']} to {projectdata.project_id(new)}" if name != folder else f"Renamed {p['id']}") + (
                f", labeled {label}" if label != old_label else "") + (
                " · its INDEX.md still names the old folder: rebuild it" if name != folder and (new.parent / "INDEX.md").exists() else "")
            return None
        self.ask("", old_label, got_label, "next", title=f"Rename {clip(p['id'], 40)}: its label")
        return None

    def edit_description(self, p):
        """Edit a project's or sub-project's one-sentence description; empty clears it."""
        def done(text):
            try:
                projectdata.set_description(p["path"], text)
            except (ValueError, OSError) as e:
                self.msg = f"Couldn't save it: {e}"
                return None
            self.load_projects()
            self.msg = f"Saved {p['id']}'s description" if text.strip() else f"Cleared {p['id']}'s description"
            return None
        self.ask("", p["summary"], done, "save", title=f"{clip(p.get('title') or label_of(p), 30)}: its description (one sentence)")
        return None

    def delete_project_ui(self, p):
        """Delete on a project or sub-project: a confirm box, then its id typed exactly, then its folder
        (with a project's sub-projects) goes to the Trash. Its Sessions keep running."""
        self.ask_confirm("project-delete", p)

    def ask_delete_typed(self, p):
        def done(text):
            if text.strip() != p["id"]:
                self.msg = f"Cancelled: that didn't match {p['id']}"
                return None
            try:
                adopting.trash(p["path"])
            except OSError as e:
                config.log(f"delete project {p['path']}: {e}")
                self.msg = f"Couldn't move it: {e}"
                return None
            self.subs_open.discard(p["id"])
            self.load_projects()
            self.psel = min(self.psel, max(len(self.project_rows()) - 1, 0))
            self.msg = f"Moved {p['id']} to the Trash"
            return None
        self.ask("", "", done, "delete", title=f"Type {clip(p['id'], 40)} to move it to the Trash")

    def load_projects(self):
        self.projects, self.recaps, self.notes = self.read_projects_notes(self.sessions)

    @staticmethod
    def read_projects_notes(sessions):
        """The projects, Session recaps, and notes on disk. Only reads, so a refresh can run it off the UI thread."""
        return projectdata.read_projects(sessions), projectdata.read_recaps(), read_notes()

    def wrapped(self, s):
        """A closed Session whose newest Handoff, in its project or its Session recap, covers its last
        message: what it did is saved, so it's never reopened, only picked up by a fresh Session."""
        p = self.project_of(s)
        home = p or self.recap_of(s)
        return bool(home) and self.wrapped_up(home, s)

    def wrapped_up(self, p, s):
        """A closed Session whose newest Handoff in p (a project, or a recap) covers its last message;
        its project's Handoffs have what it did, so it gets no row under the project."""
        if s.get("state") != "closed" or s.get("attach"):
            return False
        cov = p.get("covered") or {}
        to = max((cov.get(i, 0) for i in s.get("ids") or [s["id"]]), default=0)
        return bool(to) and to >= (s.get("active") or s["last"]) - 60  # a minute's slack for the last records

    def linked(self, p):
        """The Sessions a project lists, newest first, in any state."""
        return sorted((s for s in self.sessions if set(s.get("ids") or [s["id"]]) & set(p["sessions"])),
                      key=lambda s: -s["last"])

    def unwrapped(self, p):
        """A project's closed Sessions that no Handoff covers and the person hasn't dismissed:
        work its files may not have yet. The SESSIONS column counts them after the running ones."""
        gone = sessiondata.read_dismissed()
        return [s for s in self.linked(p) if s["state"] == "closed" and not s.get("attach")
                and not self.wrapped_up(p, s) and s["id"] not in gone]

    def sessions_field(self, p):
        """The detail pane's Sessions field: the running counts (◆ need input, ● working, ○ idle), then
        how many closed Sessions no Handoff covers, or "none running"."""
        parts = [f"{self.MARKS[key]}{n}" for key, n in self.live_counts(p)]
        n = len(self.unwrapped(p))
        return " ".join(parts + ([f"{n} unwrapped"] if n else [])) or "none running"

    def running(self, p):
        return any(s["state"] != "closed" for s in self.linked(p))

    def ready(self, p):
        """Ready to pick up: open, nothing running, and a Handoff that covers its newest Session, if it has one."""
        if p["closed"] or not p.get("handed") or self.running(p):
            return False
        linked = self.linked(p)
        return not linked or self.wrapped_up(p, linked[0])

    def markers(self, r):
        """A sidebar row's marks: ● a Session is running in it, ◆ it's ready to pick up. A project with
        its sub-projects folded carries their marks too, so nothing hides under it."""
        if r.kind not in ("project", "sub"):
            return ""
        items = [r.item]
        if r.kind == "project" and r.item["id"] not in self.subs_open:
            items += [s for s in r.item.get("subs") or [] if not s["closed"]]
        marks = (("●", any(map(self.running, items))), ("◆", any(map(self.ready, items))))
        return " ".join(m for m, on in marks if on)

    DETAIL_ROWS = 5  # lines in each of the detail pane's lists
    RECENT_SESSION_DAYS = 7

    def detail_sections(self, r):
        """The detail pane's sections under the fields, as (heading, lines): a project's open sub-projects,
        then its Sessions, Handoffs, notes, and this month's spend."""
        if r.kind not in ("project", "sub"):
            return []
        p, out = r.item, []
        subs = [s for s in p.get("subs") or [] if not s["closed"]] if r.kind == "project" else []
        if subs:
            lines = []
            for s in subs:
                state = ("running" if self.running(s) else
                         f"ready · Handoff {short_when(s['handed'])}" if self.ready(s) else "")
                lines.append(f"{self.markers(Row(r.sec, 'sub', s, 0)):3} {s.get('title') or s['slug']}  "
                             f"{s.get('type') or ''}  {s.get('status') or ''}  {state}".rstrip())
            out.append(("Sub-projects", lines))
        recent = self.recent_sessions(p)
        out.append(("Sessions", [f"{s['name']}  {short_when(s['last'])}" for s in recent]
                    or [f"none in the last {self.RECENT_SESSION_DAYS} days"]))
        out.append(("Handoffs", [f"{short_when(t)}  {title}" for t, title, _ in (p.get("handoffs") or [])[:self.DETAIL_ROWS]]
                    or ["No Handoffs yet"]))
        out.append(("Notes", [title for _, title, _ in (p.get("notes") or [])[:self.DETAIL_ROWS]] or ["No notes"]))
        out.append(("Stats", [self.month_line(p)]))
        return out

    def recent_sessions(self, p):
        """The detail pane's Sessions: running ones, then ones closed in the last RECENT_SESSION_DAYS days."""
        linked, since = self.linked(p), time.time() - self.RECENT_SESSION_DAYS * 86400
        return ([s for s in linked if s["state"] != "closed"]
                + [s for s in linked if s["state"] == "closed" and s["last"] >= since])[:self.DETAIL_ROWS]

    def detail_items(self, r):
        """What can be selected in the detail pane, in the order it's drawn: (kind, item) for each open
        sub-project, Session, Handoff, and note its sections list."""
        if r.kind not in ("project", "sub"):
            return []
        p = r.item
        subs = [s for s in p.get("subs") or [] if not s["closed"]] if r.kind == "project" else []
        return ([("sub", s) for s in subs] + [("session", s) for s in self.recent_sessions(p)]
                + [("handoff", h) for h in (p.get("handoffs") or [])[:self.DETAIL_ROWS]]
                + [("note", n) for n in (p.get("notes") or [])[:self.DETAIL_ROWS]])

    def detail_actions(self, r, kind, x):
        """An item of the detail pane: its name, and its actions."""
        if kind == "sub":
            return label_of(x), self.sub_actions(x)
        if kind == "session":
            return x["name"], self.session_actions(x)
        if kind == "note":
            note = read_note(Path(x[2]), r.item["id"])
            return note["name"], self.note_actions(note)
        return x[1], self.handoff_actions(x[2])

    def handoff_actions(self, path):
        return [("Open it", lambda: self.edit(path)), ("Show it in Finder", lambda: self.show_in_finder(path, reveal=True))]

    def keys_detail(self, k, r):
        """The detail pane's keys, with the keys in it: ↑↓ through its items, enter for one's actions."""
        items = self.detail_items(r) if r else []
        if isinstance(k, tuple):
            _, mx, my, bstate = k
            if my not in getattr(self, "drow_y", {}) or not items:
                return None
            idx = self.drow_y[my]
            self.focus = "pane"
            how = self.click(k, idx)
            if how:
                self.dsel = idx
            if how == "menu":
                return self.menu(*self.detail_actions(r, *items[idx]))
            if how == "double":
                fn = first_action(self.detail_actions(r, *items[idx])[1])
                return fn() if fn else None
            return None
        to = self.move(k, self.dsel, len(items))
        if to is not None:
            self.dsel = to
        elif k == "enter" and items:
            return self.menu(*self.detail_actions(r, *items[min(self.dsel, len(items) - 1)]))
        return None

    def month_line(self, p):
        """This month's spend on p (its Sessions', or logged under its name) and how many of its Sessions ran."""
        job = getattr(self, "sjob", None)
        if not job or not job.get("data"):
            return "loading…"
        start = date.today().replace(day=1)
        key = (id(job["data"]), p["id"], start, tuple(p["sessions"]))
        first = start.isoformat()  # spend rows' days are YYYY-MM-DD
        cache = self.__dict__.setdefault("_month_spend", {})
        if key not in cache:
            names, sids = {p["id"], label_of(p)}, set(p["sessions"])
            cache.clear()
            cache[key] = sum(c for d, _, sid, proj, c in self.stats_rows(job["data"])["rows"]
                             if d and d >= first and (proj in names or sid in sids))
        n = sum(datetime.fromtimestamp(s["last"]).date() >= start for s in self.linked(p))
        return f"This month  ${cache[key]:.2f} · {plural(n, 'Session')}"

    def show_sessions(self, p):
        """The Sessions tab, on All, searching for project:<id>: exactly the Sessions p lists. Esc clears it."""
        self.view, self.query, self.sel, self.top, self.searching = VIEWS.index("All"), f"project:{p['id']}", 0, 0, False
        self.switch_tab([t.name for t in self.TABS].index("Sessions"))
        return None

    def project_rows(self):
        """What the Projects tab lists, top to bottom: a header for each section that has anything in
        it, then (unless the section is folded) its projects. Closed comes last, folded until opened,
        then + New project. Sessions are on the Sessions tab (a project's Show its Sessions filters it),
        and Session recaps on the Notes tab.
        A project's open sub-projects follow it once → opens it.
        A search (/) keeps the rows whose name, summary, tags, or folder holds it, unfolded; a sub-project
        that matches (or whose type does, type:bugfix) shows under its project. """
        ps, cutoff = self.projects or [], time.time() - RECENT_PROJECT_DAYS * 86400
        q = self.pquery.lower()

        def hit(kind, x):
            if not q:
                return True
            if q.startswith("type:"):
                return kind == "sub" and x.get("type", "").lower() == q[5:].strip()
            fields = [x.get("name"), x.get("id"), x.get("label"), x.get("summary"), x.get("category"), " ".join(x.get("tags") or []), x.get("cwd")]
            return any(q in (f or "").lower() for f in fields)

        def subs(p):
            """The sub-project rows that follow p: the open ones when it's opened, or the matches in a search."""
            open_ = [s for s in p.get("subs") or [] if not s["closed"]]
            return [s for s in open_ if hit("sub", s)] if q else open_ if p["id"] in self.subs_open else []

        ps = [p for p in ps if hit("project", p) or (q and subs(p))]
        sections = [  # one list: Closed comes last, folded until opened
            ("Recent", [("project", p) for p in ps if not p["closed"] and (p["last"] or 0) >= cutoff]),
            ("Inactive", [("project", p) for p in ps if not p["closed"] and (p["last"] or 0) < cutoff]),
            ("Closed", [("project", p) for p in ps if p["closed"]]),
        ]
        rows = []
        for sec, items in sections:
            if not items:
                continue
            rows.append(Row(sec, "head", {"id": sec, "count": len(items)}, 0))
            if sec in self.folded and not q:
                continue
            for kind, item in items:
                rows.append(Row(sec, kind, item, 0))
                rows += [Row(sec, "sub", s, 0) for s in subs(item)]
        if not q:
            rows.append(Row("New", "new", {"id": "+ New project"}, 0))
        return rows

    def row_status(self, r, dim):
        """STATUS for a Projects row: a Session's live status, a project's status field, or recap."""
        if r.kind == "session":
            text, attr = self.status_cell(r.item)
            return text, self.DIM if r.item["state"] == "closed" else attr
        if r.kind == "project":
            job = self.adopt_job(r.item)
            if job and job["state"] == "running":
                took = self.progress(job).split(" · ")[1]
                text = f"planning {took}" if job["adopt"] == "plan" else f"Handoffs {self.progress(job)}"
                return self.mark("opening", text)
            return r.item.get("status") or "—", dim
        if r.kind == "sub":
            return r.item.get("status") or "—", dim
        return "recap", self.DIM

    def live_counts(self, p):
        """(mark, count) for a project's running Sessions: needs input, working, then idle."""
        live = [s for s in self.linked(p) if s["state"] != "closed"]
        groups = [("input", ("waiting", "blocked")), ("working", ("busy", "opening")), ("idle", ("idle", "watching", "live"))]
        return [(key, n) for key, states in groups if (n := sum(s["state"] in states for s in live))]

    @staticmethod
    def row_key(r):
        return (r.kind, r.item.get("path") or r.item["id"], r.sec)

    def select_key(self, key):
        keys = [self.row_key(r) for r in self.project_rows()]
        if key in keys:
            self.psel = keys.index(key)

    def draw_projects(self):
        """What the Projects tab shows: its rows, the selected one's panel, the corner note, the search
        row, and the key bar. ProjectsView draws them."""
        h, w = self.scr.getmaxyx()
        self.load_stats()  # the detail pane's Stats; read in the background, at most once a minute
        rows = self.prows = self.project_rows()
        self.psel = max(0, min(self.psel, len(rows) - 1))
        if getattr(self, "dfor", None) != (self.psel, self.entry):  # another row: its items start at the top
            self.dfor, self.dsel = (self.psel, self.entry), 0
        ps = self.projects or []
        closed = sum(p["closed"] for p in ps)
        self.corner = (f"{len(ps) - closed} projects · {closed} closed", self.DIM)
        root = short(str(config.projects_dir()))
        self.subhead = (f"folder {root}" + (" · missing: run roost setup" if self.projects is None else ""),
                        self.YELLOW | curses.A_BOLD if self.projects is None else self.DIM)
        self.pname, self.pfields = self.project_panel(rows, h, w - SIDEBAR_W)
        self.key_bar(BAR_KEYS.replace("←→ views", "←→ open/close"), self.msg or self.warning, self.ACCENT | curses.A_BOLD)
        return rows

    def project_panel(self, rows, h, w):
        """The detail pane, w wide: the selected row's name and its labeled fields, with the whole
        description when there's room."""
        if not rows:
            return "", []
        r = rows[self.psel]
        item = r.item
        if r.kind == "new":
            return "+ New project", [("", "enter or a click: give it a name, its folder, and a one-sentence description if you like")]
        if r.kind == "head":
            return r.sec, [("Section", f"{r.sec}: {SECTION_RULES[r.sec]}"), ("", "enter or a click opens or closes it")]
        name = item["name"] if r.kind == "session" else label_of(item) if r.kind == "project" else item["id"]
        if r.kind == "project":
            n = len(item["sessions"])
            last = short_when(item["last"]) if item["last"] else "never (no Sessions or Handoffs yet)"
            fields = [("Status", f"{item['status'] or 'none set'}   Last worked  {last}   Sessions  {n}"
                       + ("" if item["readme"] else "   (no README.md)")),
                      ("Sessions", self.sessions_field(item)),
                      *([("Repo", short(item["repo"]))] if item.get("repo") else []),
                      ("Category", item.get("category") or "none"),
                      ("Tags", ", ".join(item.get("tags") or []) or "none"),
                      ("Folder", short(item["path"]) + f"   Id  {item['id']}")]
            if not item.get("adopted", True) and not item["closed"]:
                fields.append(("Shape", "not adopted yet (no handoffs/): Adopt it, in its actions (enter), plans its README and files, then its Handoffs"))
            n_subs = sum(not s["closed"] for s in item.get("subs") or [])
            if n_subs:
                fields.append(("Sub-projects", f"{n_subs} open · " + ("← hides them" if item["id"] in self.subs_open else "→ shows them")))
            for folder, days in item.get("archives") or []:
                fields.append(("Archive", f"_archive/{folder} is {days} days old, from roost adopt: its actions (enter) can move it to the Trash"))
        elif r.kind == "sub":
            n = len(item["sessions"])
            last = short_when(item["last"]) if item["last"] else "never (no Sessions or Handoffs yet)"
            fields = [("Type", item["type"] or "none"),
                      ("Status", f"{item['status'] or 'none set'}   Last worked  {last}   Sessions  {n}"),
                      ("Sessions", self.sessions_field(item)),
                      *([("Repo", short(item["repo"]))] if item.get("repo") else []),
                      ("Folder", short(item["path"]))]
        elif r.kind == "session":
            text, _ = self.status_cell(item)
            fields = [("Status", f"{text}   Last active  {short_when(item['last'])}   Session  {item['id'][:8]}"
                       + "   in this project"),
                      ("Folder", short(item["cwd"]))]
            job = self.summaries.get(item["id"])
            if job and job["state"] != "done":
                fields.append(("Handoff", f"{job['state']}{' ' + self.progress(job) if job['state'] == 'running' else ''}: {job['text']}"))
        else:
            fields = [("Status", f"Session recap   Last worked  {short_when(item['last'])}   Sessions  {len(item.get('sessions') or [])}"),
                      ("File", short(item["path"])),
                      ("", "enter: start a Session from it, or make it a project (its Handoffs move in)")]
        described = r.kind in ("project", "sub")  # a project's summary is its description
        summary = textwrap.wrap(plain_text(item["summary"]), max(w - 18, 10)) or ["no description yet" if described else "(none)"]
        # a Session's "summary" is only its last message, so it's labeled that and kept short
        last = r.kind == "session"
        room = LAST_MESSAGE_LINES if last else max(2, h - len(fields) - 10)  # what the pane has under the fields
        if len(summary) > room:
            summary = summary[:room - 1] + [clip(summary[room - 1], w - 20) + " …"]
        title = "Last message" if last else "Description" if described else "Summary"
        return name, fields + [(title, summary[0])] + [("", line) for line in summary[1:]]

    def project_lines(self, rows, page):
        """The sidebar's lines: a row's index, or None for the blank line before every header but the
        first. Scrolls self.ptop so the selected row is in the page."""
        lines, sel_line, sel_end = [], None, None
        for idx, r in enumerate(rows):
            if r.kind in ("head", "new") and idx:
                lines.append(None)
            if idx == self.psel:
                sel_line = len(lines)
            lines.append(idx)
            if idx == self.psel:
                sel_end = len(lines) - 1
        if sel_line is not None:
            if sel_line < self.ptop:
                self.ptop = sel_line
            elif sel_end >= self.ptop + page:
                self.ptop = min(sel_end - page + 1, sel_line)
        self.ptop = max(0, min(self.ptop, max(len(lines) - page, 0)))
        return lines

    PROJECTS_HELP = [
        ("Sections", f"Recent (active in {RECENT_PROJECT_DAYS} days), Inactive, then Closed, folded until opened"),
        ("Open", "→ opens a section, or a project's sub-projects (▸), ← closes it · enter or a click on a header opens a section too"),
        ("Sub-projects", "pieces of a project's work (feature, bugfix, research, chore), each with its own README and Handoffs; New sub-project is in a project's actions"),
        ("Columns", "SESSIONS counts the project's running Sessions (◆ need input · ● working · ○ idle), and under them its unwrapped ones: closed, no Handoff covers them, not dismissed · LAST WORKED is a time today, else a date"),
        ("Sessions", "listed on the Sessions tab only: a project's Show its Sessions opens it on just that project's, searching project:<its id>"),
        ("Lists", "your projects (roost setup picks the folder); Sessions no project lists are on the Sessions tab, recaps on Notes"),
        ("Kinds", "each project has one category (what kind of work) and any tags (what it's about), both from its actions"),
    ]

    def projects_typing(self):
        return bool(self.confirm)

    @property
    def subs_open(self):
        """The ids of the projects whose sub-projects show under them: none until → opens one."""
        return self.__dict__.setdefault("_subs_open", set())

    def toggle(self, r, open_=None):
        """Open or close a section header; open_ None flips it."""
        if r.kind == "head":
            shut = r.sec in self.folded if open_ is None else bool(open_)
            (self.folded.discard if shut else self.folded.add)(r.sec)

    def open_or_close(self, rows, open_):
        """→ opens the selected section or project's sub-projects, ← closes it; ← on a sub-project closes
        its project's and selects the project. A project's Sessions are on the Sessions tab."""
        r = rows[self.psel]
        if r.kind == "project" and any(not s["closed"] for s in r.item.get("subs") or []):
            (self.subs_open.add if open_ else self.subs_open.discard)(r.item["id"])
        elif r.kind == "sub" and not open_:
            self.subs_open.discard(r.item["parent"])
            self.select_key(("project", str(Path(r.item["path"]).parent.parent), r.sec))
        else:
            self.toggle(r, open_)

    def start_folders(self, p):
        """Where a Session for p can start: the project folder, then the folders its Sessions worked in
        (where each was started, else where it last was), newest first, each once."""
        out = [p["path"]]
        for s in self.linked(p):
            d = next((d for d in (s.get("home"), s.get("cwd")) if d and os.path.isdir(d)), None)
            if d and d not in out:
                out.append(d)
        return out

    def start(self, pid, cwd, name=None):
        """Start a fresh Session for pid in cwd. Returns what to run in this terminal, if there's no other."""
        try:
            started = terminals.start_session(pid, cwd, self.term, name)
        except (ValueError, OSError) as e:
            self.msg = f"Couldn't start it: {e}"
            return None
        if isinstance(started, dict):
            return started
        self.msg = started
        self.load_projects()
        return None

    def start_for(self, p):
        """Start a fresh Session for project p, asking which folder when its Sessions used several."""
        if p["closed"]:
            self.msg = f"{label_of(p)} is closed"
            return None
        folders = self.start_folders(p)
        if len(folders) == 1:
            return self.start(p["id"], folders[0])
        self.choose(f"Start a Session for {clip(label_of(p), 40)} in", [(short(f), f) for f in folders],
                    lambda f: self.start(p["id"], f))
        return None

    def project_actions(self, p):
        """What can be done with a project, in groups: work on it, its sub-projects, organize it, then
        what removes things."""
        real = Path(p["path"]).is_dir()  # a real folder (the demo's aren't)
        work, subs, organize, remove = [], [], [], []
        if not p["closed"]:
            work.append(("Start a Session" + ("" if self.plugin_ok else " (no plugin: it won't get /roost:pickup)"),
                         lambda: self.start_for(p)))
        if any(not s["closed"] for s in p.get("subs") or []):
            work.append(("Start a Session in a sub-project", lambda: self.start_in_sub(p)))
        if self.linked(p):
            work.append(("Show its Sessions", lambda: self.show_sessions(p)))
        if real:
            subs.append(("New sub-project", lambda: self.new_subproject(p)))
        if p["readme"]:
            organize.append(("Set its category", lambda: self.edit_category(p)))
            organize.append(("Edit its tags", lambda: self.edit_tags(p)))
            organize.append(("Edit its description", lambda: self.edit_description(p)))
        if p.get("readme") and real:
            organize.append(("Rename", lambda: self.rename_project_ui(p)))
        organize.append(("Show its folder in Finder", lambda: self.show_in_finder(p["path"])))
        if p["readme"] and not p["closed"] and not p.get("adopted", True):  # a demo project counts as adopted
            remove.append(("Adopt it: plan its README and files, then write its Handoffs", lambda: self.ask_adopt(p)))
        for name, _ in p.get("archives") or []:
            path = str(Path(p["path"]) / "_archive" / name)
            remove.append((f"Move _archive/{name} to the Trash",
                           lambda path=path, name=name: self.ask_confirm("archive", {"name": f"{p['id']}/_archive/{name}", "path": path})))
        if real:
            remove.append(("Delete", lambda: self.delete_project_ui(p)))
        return grouped(work, subs, organize, remove)

    def sub_actions(self, s):
        """What can be done with a sub-project: a project's actions that fit one, in the same groups."""
        real = Path(s["path"]).is_dir()  # a real folder (the demo's aren't)
        work, organize = [], [("Edit its tags", lambda: self.edit_tags(s))]
        if not s["closed"]:
            work.append(("Start a Session" + ("" if self.plugin_ok else " (no plugin: it won't get /roost:pickup)"),
                         lambda: self.start_for(s)))
        if self.linked(s):
            work.append(("Show its Sessions", lambda: self.show_sessions(s)))
        if s["readme"]:
            organize.append(("Edit its description", lambda: self.edit_description(s)))
        if real:
            organize.append(("Rename", lambda: self.rename_project_ui(s)))
        organize.append(("Show its folder in Finder", lambda: self.show_in_finder(s["path"])))
        return grouped(work, organize, [("Delete", lambda: self.delete_project_ui(s))] if real else [])

    def start_in_sub(self, p):
        """Pick one of p's open sub-projects and start a Session in it."""
        subs = [s for s in p.get("subs") or [] if not s["closed"]]
        w = max(len(s["slug"]) for s in subs) + 2
        self.choose(f"Start a Session in a sub-project of {clip(label_of(p), 30)}",
                    [(f"{s['slug'].ljust(w)}{s['type']}: {clip(plain_text(s['summary']), 50)}", s) for s in subs],
                    self.start_for)
        return None

    def new_subproject(self, p):
        """Make a sub-project of p: its type, then its slug (its folder's name in sub/), then an optional one-sentence
        description. It shows under p, opened, and is selected."""
        name = clip(label_of(p), 30)

        def made(kind, slug_, text):
            try:
                folder = projectdata.create_subproject(f"{p['id']}/{slug_}", kind, text)
            except (ValueError, OSError) as e:
                self.msg = f"Couldn't create it: {e}"
                return None
            self.msg = f"Created {short(str(folder))}"
            self.load_projects()
            self.subs_open.add(p["id"])
            for sec in [None] + sorted(self.folded):  # its project's section may be folded
                if sec:
                    self.folded.discard(sec)
                at = next((i for i, r in enumerate(self.project_rows()) if r.kind == "sub" and r.item["path"] == str(folder)), None)
                if at is not None:
                    self.psel = at
                    break
            return None

        def got_slug(kind, text):
            slug_ = text.strip()
            if not projectdata.SLUG.fullmatch(slug_):
                self.msg = "Couldn't create it: a sub-project's slug is lowercase letters, digits, and hyphens, like badge-printers"
                return None
            self.ask("", "", lambda summary: made(kind, slug_, summary), "create", title=f"{slug_}: a one-sentence description (optional: enter skips)")
            return None

        def got_type(kind):
            self.ask("", "", lambda text: got_slug(kind, text), "next",
                     title=f"New {kind} in {name}: its slug (lowercase, like badge-printers)")
            return None

        self.choose(f"New sub-project of {name}: its type", [(t, t) for t in projectdata.SUB_TYPES], got_type)
        return None

    def recap_of(self, s):
        """The Session recap s wrote, if any: /wrap-up writes one when no project lists the Session."""
        ids = set(s.get("ids") or [s["id"]])
        return next((n for n in getattr(self, "recaps", None) or [] if ids & set(n.get("sessions") or [])), None)

    def row_actions(self, r):
        if r.kind == "project":
            return self.project_actions(r.item)
        if r.kind == "sub":
            return self.sub_actions(r.item)
        return self.session_actions(r.item)

    def tags_in_use(self):
        """Every tag on a project or a note, for the tag picker."""
        return sorted({t for x in (self.projects or []) + (self.notes or []) for t in x.get("tags") or []})

    def edit_tags(self, x):
        """Tick a project's or a note's tags from the ones in use, or type a new one."""
        def got(tags):
            try:
                projectdata.set_tags(x["path"], tags)
            except (ValueError, OSError) as e:
                self.msg = f"Couldn't set them: {e}"
                return None
            self.msg = f"Tags: {', '.join(tags)}" if tags else "Tags cleared"
            self.load_projects()
            return None

        library = sorted(set(self.tags_in_use()) | set(x["tags"]))
        self.choose(f"Tags for {clip(x.get('name') or label_of(x), 40)}", [(t, t) for t in library], got,
                    filtering=True, add="tag", multi=x["tags"])
        return None

    def edit_category(self, p):
        """Pick a project's one category from CATEGORIES.md and the ones in use, or type a new one,
        which joins the list."""
        listed = projectdata.read_categories()

        def got(category):
            try:
                if category and category not in [n for n, _ in listed]:
                    projectdata.add_category(category)
                projectdata.set_category(p["path"], category)
            except (ValueError, OSError) as e:
                self.msg = f"Couldn't set it: {e}"
                return None
            self.msg = f"Category: {category}" if category else "Category cleared"
            self.load_projects()
            return None

        cur = p.get("category")
        in_use = {q["category"] for q in self.projects or [] if q.get("category")}
        names = [n for n, _ in listed] + sorted(in_use - {n for n, _ in listed})
        about = dict(listed)
        w = max([len(n) for n in names] or [0]) + 2
        options = [((n.ljust(w) + (about.get(n) or ("(no projects yet)" if n not in in_use else ""))).rstrip()
                    + ("  · current" if n == cur else ""), n) for n in names]
        self.choose(f"Category for {clip(label_of(p), 40)}", options + ([("No category", "")] if cur else []), got,
                    filtering=True, add="category")
        return None

    def new_project(self, note=None):
        """Make a project: its name, its folder, then an optional one-sentence description. From a Session recap,
        the recap's Handoffs move in and its name and summary are the starting text."""
        def made(pid, label, text):
            try:
                folder = (handoffdata.promote_recap(note["path"], pid, text, label) if note
                          else projectdata.create_project(pid, text, label))
            except (ValueError, OSError) as e:
                self.msg = f"Couldn't create it: {e}"
                return None
            self.msg = f"Created {short(str(folder))}"
            self.load_projects()
            self.folded.discard("Inactive")  # a new project has no work yet, so it lands in Inactive
            self.select_key(("project", str(folder), "Inactive"))
            return None

        label = note["id"].replace("-", " ").capitalize() if note else ""  # a recap printer-fix starts as Printer fix
        return self.ask_project(label, note["summary"] if note else "", made,
                                f"Project from the recap {clip(note['id'], 24)}" if note else "New project")

    def keys_projects(self, k, rows):
        r = rows[self.psel] if rows and self.psel < len(rows) else None
        if (isinstance(k, tuple) and k[1] >= SIDEBAR_W) or self.focus == "pane":  # the detail pane's
            return self.keys_detail(k, r)
        if isinstance(k, tuple):
            _, mx, my, bstate = k
            if my in self.prow_y:
                idx = self.prow_y[my]
                how = self.click(k, idx)
                if how:
                    self.psel = idx
                row = rows[idx]
                if row.kind == "new":
                    if how:  # + New project works like a button
                        return self.new_project()
                elif row.kind == "head":
                    if how:  # a header opens or closes with one click
                        self.toggle(row)
                elif how == "menu":
                    return self.menu(row.item.get("name") or label_of(row.item), self.row_actions(row))
                elif how == "double":
                    fn = first_action(self.row_actions(row))
                    return fn() if fn else None
            return None
        to = self.move(k, self.psel, len(rows))
        if to is not None:
            self.psel = to
        elif k in ("right", "left") and r:
            self.open_or_close(rows, k == "right")
        elif k == "enter":
            if not r or r.kind == "new":
                return self.new_project()
            if r.kind in ("head", "more"):
                self.toggle(r)
            else:
                return self.menu(r.item.get("name") or label_of(r.item), self.row_actions(r))
        self.psel = max(0, min(self.psel, len(self.project_rows()) - 1))
        return None


if Region:
    class ProjectList(Region):
        """The sidebar, on every view: the sections and their projects, each with its marks at the right,
        then the entries (Sessions, Notes, Stats) whose views show in the right pane."""
        def lines(self):
            ui, w, y0 = self.ui, self.size.width, self.region.y
            rows = getattr(ui, "prows", [])
            page = max(self.size.height - 1 - (len(ENTRIES) + 1), 1)  # the position, a blank line, and the entries
            if ui.tab == 0:
                ui.page = page
            lines = ui.project_lines(rows, page)
            out = []
            if not rows:
                note = self.line()
                note.put(2, "Nothing here." + (" Press enter to create a project." if not ui.pquery
                                               else " Esc clears the search."), ui.DIM)
                out.append(note)
            ui.prow_y = {}
            for i, idx in enumerate(lines[ui.ptop:ui.ptop + page]):
                row = self.line()
                out.append(row)
                if idx is None:
                    continue
                r = rows[idx]
                ui.prow_y[y0 + i] = idx
                hl = idx == ui.psel and not ui.entry
                look = ui.selected if hl else (lambda a: a)
                if hl:
                    row.put(0, " " * (w - 1), look(0))
                    row.put(0, "›", look(curses.A_BOLD | (ui.ACCENT if ui.focus == "side" else ui.DIM)))
                if r.kind == "head":
                    label = f"{'▸' if r.sec in ui.folded else '▾'} {r.sec} ({r.item['count']}) "
                    row.put(2, clip(label, w - 3), look(curses.A_BOLD | (ui.ACCENT if r.sec == "Recent" else 0)))
                    if not hl and w - 3 - len(label) > 0:
                        row.rule(2 + len(label), w - 3 - len(label), ui.DIM)
                    continue
                if r.kind == "new":
                    row.put(2, "+ New project", look(ui.ACCENT | (curses.A_BOLD if hl else 0)))
                    continue
                dim = ui.DIM if r.sec == "Closed" else 0
                if r.kind == "project":
                    has = any(not s["closed"] for s in r.item.get("subs") or [])
                    name, x = ("▾ " if r.item["id"] in ui.subs_open else "▸ " if has else "  ") + label_of(r.item), 2
                    bold = curses.A_BOLD if r.sec == "Recent" else 0
                else:
                    name, x, bold = r.item.get("title") or r.item["slug"], 6, 0
                marks = ui.markers(r)
                row.put(x, clip(name, w - x - len(marks) - 2), look(bold | dim))
                if marks:
                    row.put(w - 1 - len(marks), marks, look(ui.ACCENT))
            if len(lines) > page:
                while len(out) < page:
                    out.append(self.line())
                pos = self.line()
                pos.put(w - 10, f"{ui.psel + 1}/{len(rows)}", ui.DIM)
                out.append(pos)
            out.append(self.line())
            ui.entry_y = {}
            counts = {"Unfiled": len(ui.unfiled(0, search=False)), "Sessions": sum(s["state"] != "closed" for s in ui.sessions),
                      "Notes": len(ui.notes or [])}
            for name in ENTRIES:
                row = self.line()
                ui.entry_y[y0 + len(out)] = name
                on = name == ui.entry
                look = ui.selected if on else (lambda a: a)
                if on:
                    row.put(0, " " * (w - 1), look(0))
                    row.put(0, "›", look(curses.A_BOLD | (ui.ACCENT if ui.focus == "side" else ui.DIM)))
                row.put(2, name, look(curses.A_BOLD if on else 0))
                if counts.get(name):
                    n = str(counts[name])
                    row.put(w - 2 - len(n), n, look(ui.DIM))
                out.append(row)
            if all(r.kind == "new" for r in rows):  # no projects (or none matching): just + New project, or nothing
                wren_below(self, out)
            return out

    class ProjectPanel(Region):
        """The detail pane: the selected row's name, its labeled fields, its sections, and its key hints."""
        def lines(self):
            ui, w = self.ui, self.size.width
            out = []
            if not getattr(ui, "prows", None):
                return out
            name = self.line()
            name.put(2, clip(ui.pname, w - 4), curses.A_BOLD)
            out.append(name)
            for label, value in ui.pfields:
                row = self.line()
                row.put(2, label, ui.DIM)
                row.put(16, clip(value, w - 18), 0 if label in ("Summary", "Description", "Last message") and value != "no description yet" or not label else ui.DIM)
                out.append(row)
            r = ui.prows[ui.psel]
            room = self.size.height - 1  # the last row is the key hints
            picks = ui.detail_items(r)
            kinds = {"Sub-projects": "sub", "Sessions": "session", "Handoffs": "handoff", "Notes": "note"}
            ui.drow_y, at, y0 = {}, 0, self.region.y
            for head, items in ui.detail_sections(r):
                if len(out) + 2 > room:
                    break
                out.append(self.line())
                row = self.line()
                row.put(2, head, curses.A_BOLD)
                out.append(row)
                n = sum(kind == kinds.get(head) for kind, _ in picks)  # its lines that are items, not "none yet"
                for i, item in enumerate(items[:max(room - len(out), 0)]):
                    row = self.line()
                    idx = at + i if i < n else None
                    hl = idx is not None and idx == ui.dsel and ui.focus == "pane"
                    look = ui.selected if hl else (lambda a: a)
                    if idx is not None:
                        ui.drow_y[y0 + len(out)] = idx
                    if hl:
                        row.put(1, " " * (w - 2), look(0))
                        row.put(2, "›", look(curses.A_BOLD | ui.ACCENT))
                    row.put(4, clip(item, w - 6), look(0))
                    out.append(row)
                at += n
            if r.kind in ("project", "sub"):
                while len(out) < room:
                    out.append(self.line())
                hint = "enter actions" + ("   tab its items" if picks and ui.focus == "side" else "")
                if r.kind == "project" and any(not s["closed"] for s in r.item.get("subs") or []):
                    hint += "   ← hide sub-projects" if r.item["id"] in ui.subs_open else "   → sub-projects"
                row = self.line()
                row.put(2, hint, ui.DIM)
                out.append(row)
            return out

    class ProjectsView(Vertical):
        """A project's view in the right pane: the search row, then the selected row's details."""
        DEFAULT_CSS = "ProjectsView ProjectPanel { height: 1fr; padding: 0 0 0 1; }"

        def compose(self):
            yield SearchRow()
            yield ProjectPanel()

else:
    ProjectsView = None
