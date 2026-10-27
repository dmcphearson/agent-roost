"""The Notes tab."""
import curses, os, re, subprocess
from pathlib import Path

from roost.core.config import HOME
from roost.core.projectdata import plural
from roost.core.notedata import at_end, create_note, editor
from roost.core.formatting import clip, short, short_day, short_when
from roost.app.shell import BAR_KEYS, CLICK, NOTE_READ_KEYS, NOTE_SECTIONS, first_action, grouped
from roost.app.widgets import Widget
from roost.core import config
from roost.core import projectdata
from roost.core import notedata

if Widget:
    from markdown_it import MarkdownIt
    from textual.containers import Vertical, VerticalScroll
    from textual.widgets import Markdown
    from roost.app.widgets import Region, SearchRow
else:
    Region = None


class NotesTab:
    """The Notes tab."""

    # ---------- the Notes tab ----------

    @staticmethod
    def note_section(n):
        """Backlog for a note tagged backlog (even if it's also a recap), Recaps for a recap, else Notes."""
        tags = {t.lower() for t in n.get("tags") or []}
        return "Backlog" if "backlog" in tags else "Recaps" if projectdata.RECAP_TAG in tags else "Notes"

    def note_rows(self):
        """What the Notes tab lists: a header for each section with anything in it (Backlog, Recaps, then
        Notes), then unless it's folded its notes, newest edit first; then + New note. Recaps start folded.
        A search (/) keeps the notes whose name, summary, project, or tags hold it, unfolded."""
        q = self.nquery.lower()
        hit = lambda n: not q or any(q in (n.get(f) or "").lower() for f in ("name", "id", "summary", "project")) \
            or any(q in t.lower() for t in n.get("tags") or [])
        notes, rows = [n for n in self.notes or [] if hit(n)], []
        for sec in NOTE_SECTIONS:
            items = [n for n in notes if self.note_section(n) == sec]
            if items:
                rows.append({"id": sec, "head": sec, "count": len(items)})
                rows += [] if sec in self.nfolded and not q else items
        return rows + [{"id": "+ New note", "new": True}]

    @staticmethod
    def first_note(rows):
        """Where the Notes tab's selection starts: the first note, under its section's header."""
        return next((i for i, r in enumerate(rows) if r.get("path")), 0)

    def fold_notes(self, sec, open_=None):
        """Open or close a Notes section; open_ None flips it."""
        shut = sec in self.nfolded if open_ is None else open_
        (self.nfolded.discard if shut else self.nfolded.add)(sec)

    def select_note(self, path):
        paths = [n.get("path") for n in self.note_rows()]
        if path in paths:
            self.nsel = paths.index(path)

    def editor_name(self):
        ed = editor()
        return os.path.basename(ed[0][0]) if ed else "micro (not installed yet: brew install micro)"

    def draw_notes(self):
        """What the Notes tab shows: its rows, the corner note, the search row, and the key bar. NotesView
        draws the list down the left (on the frame) and the selected note in the card: where it is
        (Notes / its title), its Markdown, then its facts in two columns under a rule. In a window under
        120 columns, the card goes under the list. → moves into the card to scroll it; ← or esc moves back."""
        rows = self.nrows = self.note_rows()
        if not getattr(self, "nstarted", False):  # the tab opens on its first note, not a header
            self.nstarted, self.nsel = True, self.first_note(rows)
        self.nsel = max(0, min(self.nsel, len(rows) - 1))
        if getattr(self, "nlast", None) != self.nsel:
            self.nlast, self.nscroll = self.nsel, 0  # a newly selected note opens at its top
        r = rows[self.nsel]
        reading = self.nreading and not r.get("new") and not r.get("head")
        self.corner = (plural(len(self.notes or []), "note"), self.DIM)
        self.subhead = (f"folder {short(str(config.NOTES))}", self.DIM)
        self.key_bar(NOTE_READ_KEYS if reading else BAR_KEYS.replace("←→ views", "→ read"), self.msg or self.warning,
                     self.ACCENT | curses.A_BOLD)
        return rows

    def note_facts(self, r):
        """The card's footer: two columns of labeled facts."""
        if r.get("new"):
            return [("Folder", short(str(config.NOTES)))], [("Opens in", self.editor_name())]
        if r.get("head"):
            return [("Section", f"{r['head']}: {NOTE_SECTIONS[r['head']]}")], [("", "enter or a click opens or closes it")]
        left = [("Tags", ", ".join(r["tags"]) or "none"), ("File", short(r["path"]))]
        right = [("Created", short_day(r["created"])), ("Edited", short_when(r["last"]))]
        if r["sessions"]:
            right.append(("Sessions", str(len(r["sessions"]))))
        return left + [(k.capitalize(), v) for k, v in r["extra"].items()], right

    NOTES_HELP = [
        ("Where", "your scratchpad: Markdown files in ~/.roost/notes; moving one into a project takes it off this tab"),
        ("Sections", "Backlog (tagged backlog), Recaps (tagged recap, folded until opened), then Notes · enter, a click, or →← opens and closes one"),
        ("Search", "/ matches a note's title, summary, project, or tags, so /backlog finds the backlog"),
        ("Reading", "→ or a click on the text moves into it to scroll · ← or esc moves back to the list"),
        ("Editor", "the editor setting, else $VISUAL, else $EDITOR, else micro; editor_wait says whether roost waits"),
    ]

    def notes_typing(self):
        return bool(self.confirm)

    def new_note(self):
        """Ask for a title on the bottom row, make the note, and open it in the editor."""
        def made(title):
            try:
                path = create_note(title)
            except (ValueError, OSError) as e:
                self.msg = f"Couldn't make it: {e}"
                return None
            self.load_projects()
            self.nquery = ""
            self.select_note(str(path))
            self.edit(str(path))
            if self.msg.startswith(("Saved", "No changes", "Opened")) and self.TABS[self.tab].name != "Notes":
                self.msg = f"{self.msg} (it's on the Notes tab)"
            return None

        self.ask("", "", made, "create", title="New note: its title")
        return None

    def edit(self, path):
        """Open a file in the editor. A terminal editor (micro) takes over this screen until it quits."""
        ed = editor()
        if not ed:
            self.msg = "No editor found: install micro (brew install micro), or set editor in ~/.config/roost/config.json"
            return None
        argv, wait = ed
        try:
            if not wait:
                subprocess.Popen(at_end(argv, path), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
                self.msg = f"Opened {short(path)} in {os.path.basename(argv[0])}"
                return None
            before = os.stat(path).st_mtime

            def run():
                with open("/dev/tty") as tty:  # the editor needs the terminal
                    subprocess.run(at_end(argv, path), stdin=tty)
            self.in_terminal(run)
            self.msg = f"Saved {short(path)}" if os.stat(path).st_mtime != before else f"No changes to {short(path)}"
        except OSError as e:
            config.log(f"edit {path}: {e}")
            self.msg = f"Couldn't open the editor: {e}"
        self.load_projects()
        self.select_note(path)
        return None

    def start_from_recap(self, recap, s):
        """A fresh Session that picks up a Session recap, where the old one worked. It lists itself in
        the recap, so its own wrap-up adds to the same file."""
        cwd = next((d for d in (recap.get("folder"), s.get("home"), s.get("cwd")) if d and os.path.isdir(d)), HOME)
        return self.start(recap["id"], cwd, s["name"])

    def start_from_note(self, n):
        """A fresh Session whose first prompt picks up the note: in its project's folder if it's in one,
        where its Session worked if it's a Session recap, else home."""
        recap = n["extra"].get("folder") if projectdata.RECAP_TAG in n["tags"] else None
        cwd = str(Path(n["path"]).parent.parent) if n["project"] else recap if recap and os.path.isdir(recap) else HOME
        return self.start(short(n["path"]), cwd, n["name"])

    def move_note(self, n):
        ps = [p for p in self.projects or [] if not p["closed"] and p["id"] != n["project"]]
        self.choose(f"Move {clip(n['name'], 40)} to a project", [(projectdata.label_of(p), p) for p in ps],
                    lambda p: self.filed(n, p["path"]), filtering=True)
        return None

    def filed(self, n, folder):
        try:
            path = notedata.file_note(n["path"], folder)
        except (ValueError, OSError) as e:
            self.msg = f"Couldn't move it: {e}"
            return None
        self.msg = f"Moved {clip(n['name'], 40)} to {Path(folder).name}/notes"
        self.load_projects()
        self.select_note(str(path))
        return None

    def note_to_project(self, n):
        """A new project from a note: its name, its folder, then its summary (the note's title to start
        with); the note moves in. A Session recap's Handoffs become the project's, as from the Sessions tab."""
        if projectdata.RECAP_TAG in n["tags"]:
            return self.new_project({"id": Path(n["path"]).stem, "path": n["path"], "summary": n["extra"].get("summary") or n["name"]})

        def made(pid, label, text):
            try:
                folder = projectdata.create_project(pid, text, label)
            except (ValueError, OSError) as e:
                self.msg = f"Couldn't create it: {e}"
                return None
            self.filed(n, folder)
            if not self.msg.startswith("Couldn't"):
                self.msg = f"Created {label}, with {clip(n['name'], 40)} in its notes/"
            return None

        return self.ask_project(n["name"], n["name"], made)

    def note_actions(self, n):
        """What can be done with one note, most likely first."""
        work = [(f"Edit in {self.editor_name().split()[0]}", lambda: self.edit(n["path"])),
                ("Start a Session from it" + ("" if self.plugin_ok else " (no plugin: it won't get /roost:pickup)"),
                 lambda: self.start_from_note(n))]
        organize = []
        if any(not p["closed"] and p["id"] != n["project"] for p in self.projects or []):
            organize.append(("Move to another project" if n["project"] else "Move to a project", lambda: self.move_note(n)))
        if not n["project"]:
            organize.append(("Make it a project", lambda: self.note_to_project(n)))
        organize.append(("Edit its tags", lambda: self.edit_tags(n)))
        organize.append(("Show it in Finder", lambda: self.show_in_finder(n["path"], reveal=True)))
        return grouped(work, organize, [("Delete to the Trash", lambda: self.ask_confirm("note", n))])

    def keys_notes(self, k, rows):
        r = rows[self.nsel] if rows else None
        if isinstance(k, tuple):
            if k[2] in self.nrow_y and k[1] < self.nlist_w:
                idx = self.nrow_y[k[2]]
                how = self.click(k, idx)
                if how:
                    self.nsel, self.nreading = idx, False
                if how and rows[idx].get("new"):  # + New note works like a button
                    return self.new_note()
                if rows[idx].get("head"):
                    if how in ("click", "double"):  # a header opens or closes with one click
                        self.fold_notes(rows[idx]["head"])
                    return None
                if how == "menu":
                    return self.menu(rows[idx]["name"], self.note_actions(rows[idx]))
                if how == "double":
                    return first_action(self.note_actions(rows[idx]))()
            elif k[3] & CLICK and self.npane[0] <= k[2] < self.npane[0] + self.npane[2] \
                    and self.npane[1] <= k[1] and r and not r.get("new") and not r.get("head"):
                self.nreading = True  # a click on the text moves into it
            return None
        if self.nreading:
            page = max(getattr(self, "nroom", self.page) - 2, 1)
            steps = {"up": -1, "down": 1, "pageup": -page, "alt+up": -page, "pagedown": page, "alt+down": page,
                     "home": -10 ** 6, "end": 10 ** 6}
            if k in steps:
                self.nscroll = max(0, self.nscroll + steps[k])  # draw keeps it within the text
            elif k == "left":
                self.nreading = False
            elif k == "enter":
                return self.menu(r["name"], self.note_actions(r))
            return None
        to = self.move(k, self.nsel, len(rows))
        if to is not None:
            self.nsel = to
        elif r and r.get("head"):
            if k in ("enter", "left", "right"):
                self.fold_notes(r["head"], None if k == "enter" else k == "right")
        elif k == "right" and r and not r.get("new"):
            self.nreading = True
        elif k == "enter":
            return self.new_note() if r.get("new") else self.menu(r["name"], self.note_actions(r))
        return None


# Laying out a whole long note in Textual's Markdown widget on every move through the list took about a
# second a move for a 200-line note, holding up every key. In the list the card shows the note's start,
# and only once the selection stops moving.
NOTE_PREVIEW_LINES = 80  # lines of a note the card shows until → reads it
NOTE_SETTLE = 0.15  # seconds the selection stays on a note before the card shows it

if Region:
    def note_markdown(r, limit=None):
        """What the reading card shows for a row: the note's Markdown, a line break where the file has one.
        With limit, its first limit lines and how many more there are."""
        if r.get("head"):
            return f"*{r['head']}: {NOTE_SECTIONS[r['head']]}. Enter, a click, or → and ← open and close it.*"
        if r.get("new"):
            return "*Give a new note a title, and roost opens it in your editor. Ctrl+n does the same from any tab, " \
                   "and in a Session, /roost:note saves an idea here.*"
        out, fence = [], False
        for line in r["lines"]:
            if line.lstrip().startswith(("```", "~~~")):
                fence = not fence
            elif not fence:  # a task list's boxes, which CommonMark leaves as text
                line = re.sub(r"^(\s*(?:[-*+]|\d+[.)])\s+)\[([ xX])\](?=\s)",
                              lambda m: m.group(1) + ("☐" if m.group(2) == " " else "☑"), line)
            out.append(line)
        if limit is not None and len(out) > limit:
            more = len(out) - limit
            fence = sum(l.lstrip().startswith(("```", "~~~")) for l in out[:limit]) % 2  # cut inside a code block
            out = out[:limit] + (["```"] if fence else []) + ["", f"*… {plural(more, 'more line')}: → reads the whole note*"]
        return "\n".join(out) or "*Empty. Enter, then Edit, to write it.*"

    def line_breaks(md):
        """A new line in the file is a new line in the card, as in most notes apps (CommonMark would join them)."""
        def rule(state):
            for token in state.tokens:
                for child in token.children or []:
                    if child.type == "softbreak":
                        child.type = "hardbreak"
        md.core.ruler.push("line_breaks", rule)
        return md

    class NoteList(Region):
        """The sections and their notes, newest edit first, then + New note."""
        def lines(self):
            ui, w, y0 = self.ui, self.size.width, self.region.y
            rows = getattr(ui, "nrows", [])
            if not rows:
                return []
            reading = ui.nreading and not rows[ui.nsel].get("new") and not rows[ui.nsel].get("head")
            list_w = w - 1 if self.has_class("side") else w - 2
            page = ui.page = max(self.size.height - (1 if self.has_class("side") else 0), 1)
            if ui.nsel < ui.ntop:
                ui.ntop = ui.nsel
            elif ui.nsel >= ui.ntop + page:
                ui.ntop = ui.nsel - page + 1
            ui.ntop = max(0, min(ui.ntop, max(len(rows) - page, 0)))
            ui.nrow_y, ui.nlist_w, out = {}, self.region.x + list_w + 1, []
            for i, n in enumerate(rows[ui.ntop:ui.ntop + page]):
                idx, row = ui.ntop + i, self.line()
                out.append(row)
                ui.nrow_y[y0 + i] = idx
                hl = idx == ui.nsel
                look = ui.selected if hl else (lambda a: a)
                if hl:
                    row.put(1, " " * list_w, look(0))
                if n.get("new"):
                    row.put(3, "+ New note", look(ui.ACCENT | (curses.A_BOLD if hl else 0)))
                    row.put(list_w - len("ctrl+n"), "ctrl+n", look(ui.pair(7)))  # as the key bar names it
                    continue
                if n.get("head"):  # as the Projects tab draws its section headers
                    label = f"{'▸' if n['head'] in ui.nfolded else '▾'} {n['head']} ({n['count']}) "
                    row.put(3, label, look(curses.A_BOLD | (ui.ACCENT if n["head"] == "Backlog" else 0)))
                    if not hl:
                        row.rule(3 + len(label), list_w - 3 - len(label), ui.DIM)
                    continue
                when = short_when(n["last"])
                row.put(3, clip(n["name"], list_w - len(when) - 5),
                        look(ui.ACCENT | curses.A_BOLD if hl and not reading else curses.A_BOLD if hl else 0))
                row.put(list_w - len(when), when, look(ui.DIM))
            if len(rows) == 1 and ui.nquery:
                row = self.line()
                row.put(3, "No note matches. esc clears it.", ui.DIM)
                out.insert(1, row)
            if len(rows) > page:
                while len(out) < page:
                    out.append(self.line())
                pos = self.line()
                pos.put(list_w - 6, f"{ui.nsel + 1}/{len(rows)}", ui.pair(7))
                out.append(pos)
            return out

    class CardHead(Region):
        """The card's first line: Notes / its title, and reading while ↑↓ scroll it."""
        CARD = True

        def lines(self):
            ui, w = self.ui, self.size.width
            rows = getattr(ui, "nrows", [])
            if not rows:
                return []
            r, row = rows[ui.nsel], self.line()
            reading = ui.nreading and not r.get("new") and not r.get("head")
            crumb = "Notes  /  "
            row.put(0, crumb, ui.DIM)
            row.put(len(crumb), clip("New note" if r.get("new") else r.get("head") or r["name"], w - len(crumb) - 9),
                    ui.ACCENT | curses.A_BOLD if reading else 0)
            if reading:
                row.put(w - 7, "reading", ui.ACCENT)
            return [row]

    class NoteFacts(Region):
        """The card's footer: a rule, then the note's facts in two columns."""
        CARD = True

        def lines(self):
            ui, w = self.ui, self.size.width
            rows = getattr(ui, "nrows", [])
            if not rows:
                return []
            left, right = ui.note_facts(rows[ui.nsel])
            edge = self.line()
            edge.rule(0, w, ui.DIM)
            out = [edge] + [self.line() for _ in range(max(len(left), len(right)))]
            half = w // 2
            for col, items in enumerate((left, right)):
                for j, (label, value) in enumerate(items):
                    out[1 + j].put(col * half, label, ui.pair(7))
                    out[1 + j].put(col * half + 10, clip(value, half - 12), ui.DIM if label != "Tags" else 0)
            return out

    class Reader(VerticalScroll):
        """The reading card's text: the note's Markdown."""
        can_focus = False

        def __init__(self):
            super().__init__(Markdown(parser_factory=lambda: line_breaks(MarkdownIt("gfm-like"))))
            self.shown = self.wanted = self.timer = None

        def show(self, r, reading=False):
            """Show the note r (its start, unless reading) once the selection has stayed on it for
            NOTE_SETTLE, laying out its Markdown again only when it changed."""
            key = (r.get("path"), r.get("last"), r.get("new"), r.get("head"), reading)
            if key == self.wanted:
                return
            self.wanted = key
            if self.timer:
                self.timer.stop()

            def render():
                self.timer = None
                if key != self.shown:
                    self.shown = key
                    self.query_one(Markdown).update(note_markdown(r, None if reading else NOTE_PREVIEW_LINES))
            self.timer = self.set_timer(NOTE_SETTLE, render)

        def watch_scroll_y(self, old, new):  # the wheel, or a scrollbar drag: the screens keep track
            super().watch_scroll_y(old, new)
            self.app.ui.nscroll = round(new)

        def on_click(self, event):
            event.stop()
            if event.chain > 2:  # a third click in a row would act a second time
                return
            state = curses.BUTTON1_DOUBLE_CLICKED if event.chain == 2 else curses.BUTTON1_CLICKED
            self.app.press(("mouse", event.screen_x, event.screen_y, state))

    class NoteCard(Vertical):
        """The selected note: where it is, its Markdown, and its facts."""
        def compose(self):
            yield CardHead()
            yield Reader()
            yield NoteFacts()

        def on_click(self, event):
            event.stop()
            self.app.press(("mouse", event.screen_x, event.screen_y, curses.BUTTON1_CLICKED))

    class NotesView(Vertical):
        """The Notes tab: the list beside the card in a window 120 columns wide, else above it."""
        DEFAULT_CSS = """
        NotesView #notes-body { height: 1fr; margin: 0 0 1 0; }
        NotesView #notes-body.side { layout: horizontal; }
        NotesView NoteList { height: 1fr; }
        NotesView NoteList.side { height: 100%; }
        NotesView NoteCard { background: #191c22; border: edge #3b414c; padding: 1 2 1 0;  /* a line around the fill, as the Stats cards have */ height: 1fr; margin: 0 2 0 2;  /* a gap between the list and the card */ }
        NotesView #notes-body.stack NoteCard { margin: 1 2 0 1; }
        NotesView CardHead { height: 1; margin: 0 0 1 2; }
        NotesView Reader { height: 1fr; padding: 0 0 0 2; background: #191c22; color: #d6dae0; scrollbar-size-vertical: 1;
                           scrollbar-background: #191c22; scrollbar-color: #2d323a; scrollbar-color-hover: #8b929c; }
        NotesView NoteFacts { height: auto; margin: 1 0 0 2; }
        Reader Markdown { background: #191c22; padding: 0 2 0 0; margin: 0; }
        Reader MarkdownH1, Reader MarkdownH2 { content-align: left top; color: #d6dae0; background: #191c22;
                 text-style: bold; margin: 0 0 1 0; }
        Reader MarkdownH3, Reader MarkdownH4, Reader MarkdownH5, Reader MarkdownH6 { color: #d6dae0;
                 background: #191c22; text-style: bold; margin: 0; }
        Reader MarkdownBlock > .code_inline { color: #6bd968; background: #1d2b20; }
        Reader MarkdownBullet { color: #6bd968; }
        Reader MarkdownParagraph { margin: 0 0 1 0; }
        Reader MarkdownBulletList MarkdownParagraph, Reader MarkdownOrderedList MarkdownParagraph,
        Reader MarkdownBlockQuote MarkdownParagraph { margin: 0; }
        Reader MarkdownBulletList, Reader MarkdownOrderedList { margin: 0 0 1 0; }
        Reader Vertical > MarkdownBulletList, Reader Vertical > MarkdownOrderedList { margin: 0; }
        Reader MarkdownBlockQuote { background: #191c22; border-left: outer #5c636d; color: #8b929c; }
        Reader MarkdownFence { background: #1b1e24; margin: 0 0 1 0; }
        Reader MarkdownFence > Label { padding: 0 1; }
        Reader MarkdownTable { background: #191c22; }
        Reader MarkdownHorizontalRule { border-bottom: solid #2d323a; background: #191c22; }
        """

        def compose(self):
            yield SearchRow()
            with Vertical(id="notes-body"):
                yield NoteList()
                yield NoteCard()

        def settle(self):
            """Beside or above, by the window's width; then the note, scrolled to where ↑↓ left it."""
            ui, w = self.app.ui, self.size.width  # the right pane's width
            side = w >= 86
            body, lst = self.query_one("#notes-body"), self.query_one(NoteList)
            body.set_class(side, "side")
            body.set_class(not side, "stack")
            lst.set_class(side, "side")
            rows = getattr(ui, "nrows", [])
            lst.styles.width = max(30, min(w // 3, 44)) + 2 if side else "100%"
            lst.styles.height = "100%" if side else max(min(len(rows), (self.app.size.height - 8) // 3), 1) + (1 if len(rows) > 1 else 0)
            if not rows:
                return
            reader = self.query_one(Reader)
            reader.show(rows[ui.nsel], bool(ui.nreading))
            card = self.query_one(NoteCard)
            ui.npane = (card.region.y, card.region.x, card.region.height, card.region.width)
            ui.nroom = max(reader.size.height, 1)
            if round(reader.scroll_y) != ui.nscroll:
                reader.scroll_to(y=ui.nscroll, animate=False)
                ui.nscroll = min(ui.nscroll, round(reader.max_scroll_y)) if reader.max_scroll_y else ui.nscroll
else:
    NotesView = None
