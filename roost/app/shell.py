"""The screens' shell: tabs, keys, menus, pickers, prompts, and the confirm and alert boxes."""
import curses, os, re, subprocess, textwrap, threading, time
from collections import namedtuple
from pathlib import Path

from roost.core.sessiondata import CACHE_WARN, cache_left, span, visible
from roost.core.projectdata import clean_tags, label_of, plural
from roost.core.plugin import plugin_version
from roost.core.formatting import clip, short, slug
from roost.core.summaries import run_cap
from roost.core.config import VIEWS
from roost.core import config
from roost.core import sessiondata
from roost.core import install
from roost.core import plugin
from roost.core import adopt as adopting
from roost.core import terminals


# ---------- UI ----------

Tab = namedtuple("Tab", "name draw keys help typing widget", defaults=(None,))  # widget: Textual fills the room the tab leaves
Row = namedtuple("Row", "sec kind item depth")  # one line of the Projects tab
RECENT_PROJECT_DAYS = 14
LAST_MESSAGE_LINES = 4  # a Session's last message in the Projects panel
SECTION_RULES = {"Recent": f"active in the last {RECENT_PROJECT_DAYS} days",
                 "Inactive": f"{RECENT_PROJECT_DAYS} days since last activity",
                 "Closed": "status closed, or closed out"}
NOTE_SECTIONS = {"Backlog": "tagged backlog: ideas and maybe-projects",  # the Notes tab's, top to bottom
                 "Recaps": "tagged recap: what a Session did, for a fresh one to pick up",
                 "Notes": "everything else"}
QUIT = object()  # what a key handler returns to close roost
SAVE = object()  # the picker's Save row, when it ticks several
DIVIDER = ("─", object())  # a line between an actions list's groups: shown, never picked


def grouped(*groups):
    """An actions list from its groups (work on it, organize it, delete it), a DIVIDER between them."""
    return [a for g in groups if g for a in [DIVIDER] + list(g)][1:]


def first_action(acts):
    """The function of the first real action (what a double-click runs), or None."""
    return next((fn for label, fn in acts if (label, fn) != DIVIDER), None)


def printable(k):
    """Is k a character typed into a search or a prompt, not a named key like "up"?"""
    return isinstance(k, str) and len(k) == 1 and k.isprintable()
CLICK = curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED | curses.BUTTON1_DOUBLE_CLICKED
RIGHT_CLICK = curses.BUTTON3_CLICKED | curses.BUTTON3_PRESSED
BAR_KEYS = "↑↓ move   ←→ views   enter actions   / search   ctrl+n new   tab next tab   ? help   ctrl+q quit"
LIST_KEYS = BAR_KEYS.replace("←→ views", "→ into details")  # the Projects tab, with the keys in its list
DETAIL_KEYS = BAR_KEYS.replace("←→ views", "← back   page up/down scroll")  # and with them in a project's details
SIDEBAR_W = 34  # the Projects tab's list; its details take the rest
PIN = "\uf435"  # a pinned Session's mark: Nerd Font's pin (nf-oct-pin), which Ghostty ships
TAB_X = 12  # where the first tab's name starts: the views and searches under the tabs line up with it
SEARCH_KEYS = "type to search · enter keep it · esc clear"
NOTE_READ_KEYS = "↑↓ scroll   page up/down a screen   ← or esc back to the list   enter actions"


def start_tab(names):
    """Index of the view roost opens on: the last one used, if it still exists."""
    last = config.read_config().get("tab")
    return names.index(last) if last in names else 0


FAILED = re.compile(r"^(Couldn't|Can't|No editor found|Its folder no longer exists|Summary of .+ failed|Handoffs for .+ stopped)")
REFUSED = re.compile(r"^(Wait for |It's working|It's open in a window|\d summaries are running|Its summary is already running|That's .+ work:|"
                     r"It's wrapped up:|.+ is closed$|.+ (is|are) running in it)|· no Handoff yet for ")


class Shell:
    """The screens' shell: what every tab shares. Each tab's module adds its own draw, keys, and actions (see roost.app.ui)."""
    reading, fresh, told = False, None, None  # a refresh's reads in a thread (see read_later)
    keys_seen = 0  # keys so far: a read that a key came during may be out of date
    focus, dsel = "side", 0  # on the Projects tab, where keys go: its list ("side") or the details ("pane")

    def setup(self, sessions, query):
        """Everything the screens keep, read once at start: no terminal is touched."""
        self.sessions, self.query = sessions, query
        self.view = 0 if any(s["state"] != "closed" for s in sessions) else 1
        self.sel, self.top, self.searching, self.msg = 0, 0, False, ""
        self.confirm = None  # session waiting on a stop confirmation
        self.tabs, self.row_y = [], {}  # screen positions, filled in by draw()
        self.last_click = (0, -1)
        self.sort = ("LAST ACTIVE", True)
        self.col_x, self.list_top = [], 4  # clickable column headers, filled in by draw()
        self.page = 10  # rows that fit on screen, set by draw()
        self.asking = None  # the one-line prompt up on the bottom row (see ask), on any tab
        self.helping = False
        self.alerting = None  # a failure, or why an action didn't run, shown as a box until a key
        names = [tab.name for tab in self.TABS]
        self.tab = names.index("Sessions") if query else start_tab(names)  # a search is for Sessions
        self.focus = "side"
        self.psel, self.ptop, self.prow_y, self.tab_x = 0, 0, {}, []
        self.pquery = ""  # the Projects tab's search
        self.folded = {"Inactive", "Closed"}  # closed sections
        self.nsel, self.ntop, self.nrow_y, self.nquery = 0, 0, {}, ""  # Notes: selection, scroll, rows, search
        self.nfolded = {"Recaps"}  # the Notes tab's closed sections
        self.nreading, self.nscroll = False, 0  # Notes: reading the selected note's text, and how far down
        self.srange, self.sscroll, self.squery = 2, 0, ""  # Stats: the range (Month to date), scroll, project search
        self.corner, self.bar = None, None  # the tab's note at the top right, and its key bar (see key_bar)
        self.choosing = None  # the list up for picking from (see choose), on any tab
        self.summaries = {}  # Session id -> {state: running, done, or failed; text; name} for summary runs
        self.offer = None  # (model, cap, estimate) while a summary run waits on its confirmation
        self.quit_anyway = False  # q was pressed once while a summary was running
        self.load_projects()
        self.psel = next((i for i, r in enumerate(self.project_rows()) if r.kind == "project"), 0)
        self.warning = plugin.plugin_problem() or sessiondata.check_compat()
        self.plugin_ok = plugin.plugin_registered() or plugin_version() is not None  # can a Session get /roost:pickup
        self.msg = plugin.taken_plugin_change()
        due = sum(len(p.get("archives") or []) for p in self.projects or [])
        if due and not self.msg:
            self.msg = f"{due} _archive folder{'s are' if due != 1 else ' is'} past its keep time: the project's actions (enter) clear it"
        self.rules = sessiondata.retention()
        self.update_note = ""
        # check for a newer release without holding up the first screen
        threading.Thread(target=lambda: setattr(self, "update_note", install.update_available()), daemon=True).start()
        self.term = terminals.chosen_terminal()
        terminals.mark_open(self.sessions, self.term)

    LABELS = {"busy": "working", "waiting": "needs input", "blocked": "needs input"}

    @classmethod
    def label(cls, state):
        return cls.LABELS.get(state, state)

    def strip(self):
        """The current tab's summary strip, as (mark, attr, number, word) parts, or [] when it has none."""
        fn = getattr(self, f"{self.TABS[self.tab].name.lower()}_strip", None)
        return fn() if fn else []

    def picked(self, attr):
        """attr as the selected row's name shows it in a list: bold green, with no band behind it."""
        return (attr & ~curses.A_COLOR & ~curses.A_DIM) | curses.A_BOLD | self.ACCENT

    def selected(self, attr):
        """attr as it should look in a raised box (a menu, a card, the help)."""
        return (attr & ~curses.A_COLOR & ~curses.A_DIM) | self.pair(100 + self.pair_of(attr))

    @staticmethod
    def pair(n):
        """The attr of color pair n."""
        return curses.color_pair(n)

    @staticmethod
    def pair_of(attr):
        return curses.pair_number(attr)

    def read_fresh(self, sessions):
        """What a refresh reads: the Sessions (claude agents, ps, the transcripts), which are open in a
        window, and the projects, recaps, and notes. Only reads, so it can run off the UI thread."""
        fresh = terminals.mark_open(sessiondata.regather(sessions), self.term)
        return (fresh, *self.read_projects_notes(fresh))

    def read_later(self, act=None, doing=""):
        """refresh() with its reads in a thread: they take a few hundred ms, mostly claude agents, which on
        the UI thread stalls every key. act, a slow action (a stop, a restart), runs first in the same
        thread and returns the message for after it; doing is the message meanwhile. draw() takes what
        was read, unless a key came while it was read: that key may have changed it (a delete, a stop).
        Without act, one runs at a time."""
        if act is None:
            if self.reading:
                return
            self.reading = True
        elif doing:
            self.msg = doing

        def work():
            try:
                if act:
                    self.told = act()
                keys = self.keys_seen
                self.fresh = (keys, self.read_fresh(list(self.sessions)))
            except Exception as e:
                config.log(f"refresh: {e}")
            finally:
                if act is None:
                    self.reading = False
        threading.Thread(target=work, daemon=True).start()

    def take_fresh(self):
        """Show what read_later read and said, if no key came since it was read."""
        if self.told:
            self.msg, self.told = self.told, None
        if self.fresh:
            (keys, fresh), self.fresh = self.fresh, None
            if keys == self.keys_seen:
                self.refresh(fresh)

    def refresh(self, fresh=None):
        """Re-read sessions and projects (or take fresh, what read_fresh read), keeping the same row
        selected even if rows move."""
        rows = visible(self.sessions, self.view, self.query, self.sort, self.summarizing())
        sel_id = rows[self.sel]["id"] if rows and self.sel < len(rows) else None
        self.sessions, *rest = fresh or self.read_fresh(self.sessions)
        rows = visible(self.sessions, self.view, self.query, self.sort, self.summarizing())
        ids = [s["id"] for s in rows]
        if sel_id in ids:
            self.sel = ids.index(sel_id)
        prows, nrows = self.project_rows(), self.note_rows()
        picked = self.row_key(prows[self.psel]) if self.psel < len(prows) else None
        note = nrows[self.nsel].get("path") if self.nsel < len(nrows) else None
        self.projects, self.recaps, self.notes = rest
        self.select_key(picked)
        self.select_note(note)

    # symbol, color key: the symbol and the word carry the meaning, the color reinforces it
    MARKS = {"working": "●", "input": "◆", "idle": "○", "expiring": "◔", "expired": "✕",
             "opening": "◌", "compacted": "○", "closed": "·", "wrapped": "✓"}

    def status_cell(self, s):
        """STATUS draws the eye only when something needs you: a session waiting on you, a cache about
        to expire (the next prompt is about to cost 2x), or one that has expired."""
        state, left, job = s["state"], cache_left(s), self.summaries.get(s["id"])
        if job and job["state"] == "running":
            return self.mark("opening", f"summarizing {self.progress(job)}")
        ask = self.summaries.get(f"ask:{s['id']}")
        if ask and ask["state"] == "running":
            return self.mark("opening", f"asking {self.progress(ask).split(' · ')[1]}")
        if job and job["state"] == "failed":
            return self.mark("expired", "summary failed", curses.A_BOLD)
        if state in ("idle", "waiting", "blocked") and left is not None:
            if s.get("compacted"):
                return self.mark("compacted", "compacted")
            if left <= 0:
                return self.mark("expired", "cache expired")
            if left <= CACHE_WARN:
                return self.mark("expiring", f"expires in {span(left)}", curses.A_BOLD)
        if state == "closed" and s["id"] in sessiondata.read_dismissed():
            return f"{self.MARKS['closed']} dismissed", self.DIM
        if state == "closed" and self.wrapped(s):
            return f"{self.MARKS['wrapped']} wrapped up", self.DIM
        if s.get("desktop") and state not in ("busy", "waiting", "blocked"):
            return f"{self.MARKS['idle']} open in Desktop", self.DIM
        key = {"busy": "working", "waiting": "input", "blocked": "input"}.get(state, state)
        if key in self.status:
            return self.mark(key, self.label(state))
        return f"{self.MARKS.get(key, '·')} {self.label(state)}", self.DIM

    @staticmethod
    def progress(job):
        """'2/3 · 1m40s': which model call a summary run is on, and how long it has run."""
        secs = int(time.time() - job.get("started", time.time()))
        took = f"{secs // 60}m{secs % 60:02d}s" if secs >= 60 else f"{secs}s"
        return f"{max(job.get('step', 0), 1)}/{job.get('steps', 3)} · {took}"

    def mark(self, key, text, extra=0):
        return f"{self.MARKS[key]} {text}", self.status[key] | extra

    def draw(self):
        """Work out what the screens show now: a failure or refusal becomes a box, then the current tab
        works out its rows, its corner note, and its key bar (its widgets draw them). Returns what the
        tab worked out, which its key handler gets back."""
        self.report_summaries()
        self.take_fresh()
        if self.msg and (FAILED.search(self.msg) or REFUSED.search(self.msg)):
            self.alerting, self.msg = self.msg, ""
        self.corner, self.bar = None, None
        drawn = self.draw_projects()  # the sidebar, on every view
        if self.tab:
            self.subhead = None
            drawn = self.TABS[self.tab].draw(self)
        return drawn

    def status_line(self):
        """The status bar's two sides: the left kept for Sessions waiting on you, the right how many run."""
        n = sum(s["state"] != "closed" for s in self.sessions)
        return "", f"{n} running" if n else "nothing running"

    def key_bar(self, keys, note="", note_attr=0):
        """The tab's key bar: its keys (or the search's while typing one) and a note at the right. A
        prompt or a confirmation takes its place."""
        self.bar = None if self.asking or self.confirm else \
            (self.bar_lines(SEARCH_KEYS if self.searching else keys, note), note_attr)

    def alert_text(self):
        """A failure, or why an action didn't run, as a box until any key: its first clause is the title."""
        text = self.alerting
        failed = bool(FAILED.search(text))
        head, _, rest = text.partition(": ") if failed else ("", "", text)
        title = head or ("That didn't work" if failed else "Not done")
        body = [x.strip() for x in rest.split(" · ")] if rest else []
        return title, body, "any key closes", self.RED if failed else self.YELLOW

    def summarizing(self):
        """Ids of the Sessions whose Handoff or recap is being written now."""
        return {k for k, j in getattr(self, "summaries", {}).items() if j["state"] == "running" and ":" not in k}

    def report_summaries(self):
        """Once per finished run: say how it went, and re-read projects when it wrote a Handoff. A
        finished adopt plan asks to be read, once nothing else is on screen."""
        for job in self.summaries.values():
            if job.get("ask") and job["state"] != "running" and not job.get("told"):
                if job["state"] == "done":
                    if self.confirm or self.choosing or self.asking:
                        continue
                    s = next((x for x in self.sessions if x["id"] == job["ask"]), None)
                    if s:
                        self.show_advice(s, job["advice"])
                job["told"] = True
                self.msg = job["text"]
                continue
            if job.get("adopt") and job["state"] != "running" and not job.get("told"):
                if job["state"] == "done" and job["adopt"] == "plan":
                    if self.confirm or self.choosing or self.asking:
                        continue
                    p = next((q for q in self.projects or [] if q["path"] == job["path"]), None)
                    if p:
                        self.confirm = ("adopt-review", p)
                job["told"] = True
                self.msg = job["text"]
                self.load_projects()
                continue
            if job["state"] != "running" and not job.get("told"):
                job["told"] = True
                self.msg = job["text"] if job["state"] == "done" else f"Summary of {job['name']} failed, {job['text']}"
                if job["state"] == "done":
                    self.load_projects()

    def keys_confirm(self, k):
        (action, s), self.confirm = self.confirm, None
        if action == "summary" and k in ("y", "Y"):
            self.run_summary(s)
        elif action == "adopt" and k in ("y", "Y"):
            self.run_adopt_plan(s)
        elif action == "ask" and k in ("y", "Y"):
            self.run_ask(s)
        elif action == "adopt-review" and k in ("y", "Y", "enter"):
            self.review_adopt(s)
        elif action == "adopt-apply" and k in ("e", "E"):
            self.review_adopt(s)
        elif action == "adopt-apply" and k in ("y", "Y") and not self.adopt_problems(s):
            self.apply_adopt(s)
        elif action == "adopt-handoffs" and k in ("y", "Y"):
            self.run_adopt_handoffs(s)
        elif action.startswith("adopt") and action != "adopt":
            self.msg = "Later, then: its plan is kept, and Adopt it in its actions picks up from there" if action != "adopt-handoffs" \
                else "No Handoffs written: Write its Handoff, on each Session, does one at a time"
        elif action == "archive" and k in ("y", "Y"):
            try:
                adopting.trash(s["path"])
                self.msg = f"Moved {s['name']} to the Trash"
            except OSError as e:
                config.log(f"clear archive {s['path']}: {e}")
                self.msg = f"Couldn't move it: {e}"
            self.load_projects()
        elif action == "project-delete" and k in ("y", "Y"):
            self.ask_delete_typed(s)
        elif action == "note" and k in ("y", "Y"):
            try:
                adopting.trash(s["path"])
                self.msg = f"Moved {s['name']} to the Trash"
            except OSError as e:
                config.log(f"delete note {s['path']}: {e}")
                self.msg = f"Couldn't move it: {e}"
            self.load_projects()
        elif action == "stop" and k in ("y", "Y"):
            def stop():
                try:
                    sessiondata.stop_session(s["attach"])
                    return f"Stopped {s['name']}. It's under Closed"
                except Exception as e:
                    config.log(f"stop {s['id'][:8]}: {e}")
                    return f"Couldn't stop it: {e}"
            self.read_later(stop, f"Stopping {s['name']}…")
        elif action == "delete" and k in ("y", "Y"):
            try:
                sessiondata.delete_session(s)
                self.sessions = [x for x in self.sessions if x is not s]
                self.msg = f"Deleted {s['name']} (in the Trash if you need it back)"
            except Exception as e:
                config.log(f"delete {s['id'][:8]}: {e}")
                self.msg = f"Couldn't delete it: {e}"
        else:
            self.msg = "Cancelled"
        return None

    def confirm_text(self):
        """The open confirmation: (title, body lines, keys, color)."""
        action, c = self.confirm
        if action.startswith("adopt"):
            return self.adopt_prompt(action, c)
        if action == "ask":
            model, cap, est, _ = self.offer
            cost = f"about ${est:.2f}" if est is not None else "cost unknown (no price list)"
            return (f"Ask what to do with {clip(c['name'], 50)}?",
                    [f"One model call reads its transcript: {cost} with {model}, capped at ${cap:g}.",
                     "It says whether the work finished and what to do: delete it, write a recap or Handoff, or add it to a project."],
                    "y to ask · any other key cancels", self.YELLOW)
        if action == "archive":
            return (f"Move {c['name']} to the Trash?", ["It holds the files roost adopt replaced."],
                    "y to move it · any other key cancels", self.RED)
        if action == "project-delete":
            subs = len([d for d in (Path(c["path"]) / "sub").glob("*/") if d.is_dir()]) if not c.get("parent") else 0
            live = [x for x in self.linked(c) if x["state"] != "closed"]
            return (f"Move {clip(c['id'], 50)} to the Trash?",
                    ([f"Its {plural(subs, 'sub-project')} {'goes' if subs == 1 else 'go'} with it."] if subs else [])
                    + ([f"{plural(len(live), 'Session')} running in it keep running, with no project."] if live else [])
                    + ["Next, type its name to confirm."],
                    "y to go on · any other key cancels", self.RED)
        if action == "note":
            return (f"Move the note {clip(c['name'], 50)} to the Trash?", [], "y to move it · any other key cancels", self.RED)
        if action == "summary":
            model, cap, est, project = self.offer
            cost = f"about ${est:.2f}" if est is not None else "cost unknown (no price list)"
            what = "Handoff and README update" if project else "Session recap (no project lists it)"
            return (f"Write {clip(c['name'], 40)}'s {what}?",
                    [f"From its transcript: {cost} with {model}, " + (
                        f"over the ${cap:g} cap, so this run is capped at ${run_cap(est, cap):g}." if run_cap(est, cap) != cap
                        else f"capped at ${cap:g}.")] + (["It's stopped first."] if c.get("attach") else []),
                    "y to run · any other key cancels", self.YELLOW)
        if action == "stop":
            return (f"Stop {c['name']}?", (["It's working right now."] if c["state"] == "busy" else []) + ["Its history is kept."],
                    "y to stop · any other key cancels", self.YELLOW)
        return (f"Delete {c['name']}?", ["Its history moves to the Trash."], "y to delete · any other key cancels", self.RED)

    def bar_lines(self, keys, note=""):
        """The bottom rows of a tab: its keys (separated by three spaces), wrapped between keys to fit,
        then the note (a message or warning) at the right of the last row, or on a row of its own."""
        w = self.scr.getmaxyx()[1]
        lines = []
        for item in (k for k in keys.split("   ") if k):
            if lines and len(lines[-1][0]) + 3 + len(item) <= w - 4:
                lines[-1][0] += "   " + item
            else:
                lines.append([item, ""])
        lines = lines or [["", ""]]
        if note:
            if len(lines[-1][0]) + len(note) + 6 <= w:
                lines[-1][1] = note
            else:
                lines.append(["", note])
        return lines

    # The keys are the same on every tab; everything else is in a row's actions.
    GLOBAL_HELP = [
        ("Move", "↑↓ · page up/down or option+↑↓ a screen · home/end top and bottom · click a row"),
        ("Views", "←→ switch views on Sessions (or click one) · → into a project's details, ← back · → reads a note"),
        ("Actions", "enter or right-click opens the selected row's actions · double-click runs the first one"),
        ("Search", "/ then type · enter keeps it · esc clears it"),
        ("New", "ctrl+n from anywhere: a new Project (P), Session (S), or Note (N)"),
        ("Tabs", "tab the next tab · shift+tab the one before · or click a tab's name"),
        ("Close", "esc closes a list or prompt · ? these keys · ctrl+q quit"),
    ]

    def help_rows(self):
        """What ? shows: the keys every tab shares, then what the current tab lists."""
        return self.GLOBAL_HELP + self.TABS[self.tab].help

    CONTROL_KEYS = {"\n": "enter", "\r": "enter", "\t": "tab", "\x1b": "escape", "\x7f": "backspace",
                    "\b": "backspace", "\x0e": "ctrl+n", "\x11": "ctrl+q", "\x15": "ctrl+u"}

    def dispatch(self, k, drawn):
        """Handle one key (see handle). In an IDE's terminal, a session to open here is copied
        instead, to paste in a new tab, and roost keeps running."""
        result = self.handle(k, drawn)
        ide = None if result is None or result is QUIT or self.term else terminals.ide_terminal()
        if not ide:
            return result
        self.copy_text(terminals.run_line(result), terminals.IDE_NOTES[ide])
        return None

    def handle(self, k, drawn):
        """Handle one key: an open menu or prompt first, then the keys every tab shares, then the
        current tab's. A mouse event arrives as ("mouse", x, y, buttons). Returns None to keep
        going, QUIT, or a session to open here."""
        self.msg = ""
        self.keys_seen += 1
        if getattr(self, "alerting", None):
            self.alerting = None
            return None
        if self.helping:
            self.helping = False
            return None
        if self.choosing:
            return self.keys_choice(k)
        if self.asking:
            return self.keys_ask(k)
        if self.searching:
            return self.keys_search(k)
        if self.confirm:  # on any tab: one can come up from a background run, wherever the person is
            self.keys_confirm(k)
            self.load_projects()
            return None
        tab = self.TABS[self.tab]
        if not tab.typing(self):  # a confirmation or the category picker takes every key
            if k in ("tab", "shift+tab"):
                self.switch_tab(self.tab + (1 if k == "tab" else -1))
                return None
            if k == "?":
                self.helping = True
                return None
            if k == "ctrl+q":  # ctrl+q; a plain q is just a letter
                running = [j for j in self.summaries.values() if j["state"] == "running"]
                if running and not self.quit_anyway:
                    self.quit_anyway = True
                    what = f"A summary of {running[0]['name']} is" if len(running) == 1 else f"{len(running)} summaries are"
                    self.msg = f"{what} running and would be lost: ctrl+q again to quit anyway"
                    return None
                return QUIT
            self.quit_anyway = False
            if k == "/":
                self.searching = True
                return None
            if k == "ctrl+n":  # ctrl+n: a new project, Session, or note, from any tab
                return self.new_menu()
            if k == "escape" and getattr(self, "nreading", False):  # esc leaves a note's text
                self.nreading = False
                return None
            if k == "escape":  # esc clears the search, then leaves a project's details; it never quits
                if getattr(self, self.query_attr()):
                    setattr(self, self.query_attr(), "")
                elif self.focus == "pane":
                    self.focus = "side"
                return None
            if isinstance(k, tuple) and k[2] == 0:  # the top bar: a click on a tab's name shows it
                if k[3] & CLICK:
                    for x0, x1, i in self.tab_x:
                        if x0 <= k[1] < x1:
                            self.switch_tab(i)
                return None
        return tab.keys(self, k, drawn)

    def query_attr(self):
        """Each tab keeps its own search."""
        name = self.TABS[self.tab].name
        if name == "Sessions" and VIEWS[self.view] == "Unfiled":
            return "uquery"
        return {"Sessions": "query", "Notes": "nquery", "Stats": "squery"}.get(name, "pquery")

    def keys_search(self, k):
        attr = self.query_attr()
        text = getattr(self, attr)
        if k == "enter":
            self.searching = False
        elif k == "escape":
            self.searching = False
            setattr(self, attr, "")
        elif k == "backspace":
            setattr(self, attr, text[:-1])
        elif printable(k):
            setattr(self, attr, text + k)
            self.sel = self.top = self.psel = self.ptop = self.usel = self.utop = 0
            if attr == "nquery":
                self.nsel, self.ntop = self.first_note(self.note_rows()), 0  # the first match
        return None

    def ask(self, prompt, text, done, verb="save", title=None):
        """Put up a one-line prompt on the bottom row of any tab, starting with text, or with a title, in a
        box in the middle of the screen. Enter calls done(text), whose return value is what the key
        handler returns; esc cancels."""
        self.asking = {"prompt": prompt, "text": text, "done": done, "verb": verb, "title": title}

    def all_projects(self):
        """Every project, each followed by its sub-projects: what a Session can belong to."""
        return [x for p in self.projects or [] for x in [p] + (p.get("subs") or [])]

    def ask_project(self, label, summary, made, what="New project"):
        """A new project, in three prompts: its name as people read it (the README's heading), its
        folder (made from the name, editable), then an optional one-sentence description. made(pid, label, summary)
        creates it and returns what the key handler returns."""
        def got_label(text):
            label = " ".join(text.split())
            if not label:
                self.msg = "Cancelled: a project needs a name"
                return None
            self.ask("", slug(label), lambda pid: got_id(label, pid.strip()), "next",
                     title=f"{clip(label, 30)}: its folder's name")
            return None

        def got_id(label, pid):
            self.ask("", summary, lambda text: made(pid, label, text), "create",
                     title=f"{clip(label, 30)}: a one-sentence description (optional: enter skips)")
            return None

        self.ask("", label, got_label, "next", title=f"{what}: its name")
        return None

    def keys_ask(self, k):
        a = self.asking
        if k == "enter":
            self.asking = None
            return a["done"](a["text"])
        if k == "escape":
            self.asking, self.msg = None, "Cancelled"
        elif k == "backspace":
            a["text"] = a["text"][:-1]
        elif k == "ctrl+u":  # ctrl+u clears
            a["text"] = ""
        elif printable(k):
            a["text"] += k
        return None

    def move(self, k, cur, n):
        """The row ↑↓, page up/down (or option+↑↓), and home/end move to, or None for another key."""
        page = max(self.page - 2, 1)
        steps = {"up": -1, "down": 1, "pageup": -page, "alt+up": -page, "pagedown": page, "alt+down": page}
        if k in steps:
            return max(0, min(cur + steps[k], n - 1))
        if k == "home":
            return 0
        if k == "end":
            return max(n - 1, 0)
        return None

    def click(self, k, idx):
        """For a mouse event on row idx: "menu" for a right-click, "double" for a double-click,
        "click" for a single one, else None."""
        bstate = k[3]
        if bstate & RIGHT_CLICK:
            self.last_click = (0, -1)
            return "menu"
        if not bstate & CLICK:
            return None
        now = time.time()
        double = (bstate & curses.BUTTON1_DOUBLE_CLICKED) or (self.last_click[1] == idx and now - self.last_click[0] < 0.4)
        self.last_click = (0, -1) if double else (now, idx)
        return "double" if double else "click"

    def menu(self, title, actions):
        """The actions for one row, as a list to pick from: (label, function) pairs. Picking one runs it."""
        if not actions:
            self.msg = "Nothing to do here"
            return None
        self.choose(title, actions, lambda fn: fn())
        return None

    NEW = [("Project", "p"), ("Session", "s"), ("Note", "n")]

    def new_menu(self):
        """ctrl+n: make a project, start a Session, or write a note. P, S, or N (shifted or not) picks one."""
        make = {"p": self.new_project, "s": self.new_session, "n": self.new_note}
        self.choose("New", [(f"{label:<10}{key.upper()}", key) for label, key in self.NEW], lambda key: make[key](),
                    keys={c: key for _, key in self.NEW for c in (key, key.upper())})
        return None

    def new_session(self):
        """Ask for a folder (the Projects folder to start), then start a Session with no project there."""
        def got(text):
            folder = os.path.expanduser(text.strip())
            if not os.path.isdir(folder):
                self.msg = f"No folder at {text.strip() or '(nothing)'}"
                return None
            started = terminals.start_plain(os.path.realpath(folder), self.term)
            if isinstance(started, dict):
                return started
            self.msg = started
            return None

        self.ask("", short(str(config.projects_dir())) + "/", got, "start", title="New Session: in which folder")
        return None

    def choose(self, title, options, done, filtering=False, add=None, multi=None, body=None, keys=None):
        """Put up a list to pick from over any tab. options are (label, value); enter calls done(value),
        whose return value is what the key handler returns. With filtering, typing narrows the list;
        with add (a word, like "tag"), what's typed can also be added as a new value. With multi (the
        values ticked to start with), enter ticks or unticks, and the Save row calls done(ticked). body
        is lines of text shown between the title and the options. keys maps a key to the value it picks."""
        self.choosing = {"title": title, "options": list(options), "sel": 0, "typed": "" if filtering else None,
                         "done": done, "add": add, "multi": None if multi is None else list(multi), "body": body or [],
                         "keys": keys or {}}
        self.choosing["sel"] = self.next_option(self.choice_options(), -1, 1)

    @staticmethod
    def next_option(opts, at, step):
        """The option after at (step 1) or before it (-1), wrapping and stepping over dividers."""
        for i in range(1, len(opts) + 1):
            j = (at + step * i) % len(opts)
            if tuple(opts[j]) != DIVIDER:
                return j
        return 0

    def choice_options(self):
        c = self.choosing
        q = (c["typed"] or "").lower()
        out = []
        for o in c["options"]:  # a divider only between two groups, and none while filtering
            if tuple(o) != DIVIDER:
                if q in o[0].lower():
                    out.append(o)
            elif not q and out and tuple(out[-1]) != DIVIDER:
                out.append(o)
        if out and tuple(out[-1]) == DIVIDER:
            out.pop()
        if c.get("add") and q:
            try:
                new = clean_tags(q)
            except ValueError:
                new = []
            if len(new) == 1 and new[0] not in [v for _, v in c["options"]]:
                out.append((f"+ New {c['add']}: {new[0]}", ("new", new[0])))
        if c.get("multi") is not None:
            out.append(("Save", SAVE))
        return out

    def pick(self, value):
        """Enter (or a click) on a picker row: tick it, add it, save, or pick it."""
        c = self.choosing
        if value is DIVIDER[1]:
            return None
        if isinstance(value, tuple) and value[0] == "new":
            value = value[1]
            if c["multi"] is not None:
                c["options"].append((value, value))
                c["multi"].append(value)
                c["typed"], c["sel"] = "", len(c["options"])  # on Save, so enter again saves
                return None
        if c["multi"] is not None and value is not SAVE:
            (c["multi"].remove if value in c["multi"] else c["multi"].append)(value)
            return None
        self.choosing = None
        return c["done"](c["multi"] if value is SAVE else value)

    def keys_choice(self, k):
        c, opts = self.choosing, self.choice_options()
        if isinstance(k, tuple):  # click an option to pick it; click outside the list to cancel
            y0, x0, bw, bh = c.get("box", (0, 0, 0, 0))
            off = c.get("off", 2)
            i = k[2] - y0 - off + c.get("top", 0)
            if not (x0 <= k[1] < x0 + bw and y0 <= k[2] < y0 + bh):
                if k[3] & (CLICK | RIGHT_CLICK):
                    self.choosing, self.msg = None, "Cancelled"
            elif k[3] & CLICK and 0 <= k[2] - y0 - off < c.get("fit", bh - 5) and i < len(opts):
                return self.pick(opts[i][1])
            return None
        if not isinstance(k, tuple) and k in c["keys"]:
            return self.pick(c["keys"][k])
        if k == "up":
            c["sel"] = self.next_option(opts, c["sel"], -1) if opts else 0
        elif k == "down":
            c["sel"] = self.next_option(opts, c["sel"], 1) if opts else 0
        elif k == "escape":
            self.choosing, self.msg = None, "Cancelled"
        elif k == "enter":
            if opts:
                return self.pick(opts[min(c["sel"], len(opts) - 1)][1])
        elif c["typed"] is not None and k == "backspace":
            c["typed"], c["sel"] = c["typed"][:-1], 0
        elif c["typed"] is not None and printable(k):
            c["typed"], c["sel"] = c["typed"] + k, 0
        return None

    def switch_tab(self, i):
        """Show tab i (wrapping), with the keys in its list, and remember it for the next start."""
        self.tab, self.focus = i % len(self.TABS), "side"
        try:
            config.save_config(tab=self.TABS[self.tab].name)
        except OSError:
            pass

    def open_running(self):
        """The status bar's running count: every running Session."""
        self.view, self.sel, self.top = VIEWS.index("Running"), 0, 0
        self.switch_tab([tab.name for tab in self.TABS].index("Sessions"))

    def copy_id(self, s):
        self.copy_text(s["id"], f"Copied {s['id'][:8]}… to the clipboard")
        return None

    def copy_text(self, text, done):
        """Put text on the clipboard and say done, or why it couldn't."""
        try:
            self.clipboard(text)
            self.msg = done
        except Exception as e:
            config.log(f"copy: {e}")
            self.msg = f"Couldn't copy: {e}"

    @staticmethod
    def clipboard(text):
        subprocess.run(["pbcopy"], input=text, text=True, timeout=5, check=True)

    def show_in_finder(self, path, reveal=False):
        if path and os.path.exists(path):
            subprocess.run(["open", "-R", path] if reveal else ["open", path], stdin=subprocess.DEVNULL, timeout=10)
            self.msg = f"Opened {short(path)}"
        else:
            self.msg = "Its folder no longer exists"
        return None
