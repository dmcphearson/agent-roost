"""The Unfiled entry: the Sessions no project lists and the notes in ~/.roost/notes, to file into a project
or let go."""
import curses, time

from roost.core.formatting import clip, short_when
from roost.app.shell import BAR_KEYS, CLICK, first_action
from roost.app.widgets import Widget
from roost.core import sessiondata

if Widget:
    from textual.containers import Vertical
    from roost.app.widgets import Region

UNFILED_VIEWS = ("Recent", "All")
RECENT_DAYS = 7
NOTE_MARK = "✎"


class UnfiledTab:
    uview, usel, utop, uquery, urow_y = 0, 0, 0, "", {}

    def unfiled(self, view=None, search=True):
        """The view's items as (kind, item): running Sessions first, then Sessions and notes by their last
        activity or edit, newest first. Recent keeps the running ones and the last RECENT_DAYS days'."""
        view = self.uview if view is None else view
        gone = sessiondata.read_dismissed()
        since = time.time() - RECENT_DAYS * 86400
        q = self.uquery.lower() if search else ""
        items = [("session", s) for s in self.sessions if s["id"] not in gone and not self.project_of(s)]
        items += [("note", n) for n in self.notes or []]
        if view == 0:
            items = [(k, x) for k, x in items if (k == "session" and x["state"] != "closed") or x["last"] >= since]
        if q:
            items = [(k, x) for k, x in items if q in (x.get("name") or "").lower()]
        running = lambda k, x: k == "session" and x["state"] != "closed"
        return sorted(items, key=lambda kx: (not running(*kx), -kx[1]["last"]))

    def draw_unfiled(self):
        rows = self.urows = self.unfiled()
        self.usel = max(0, min(self.usel, len(rows) - 1))
        self.corner = (f"{len(self.unfiled(1))} unfiled", self.DIM)
        self.key_bar(BAR_KEYS, self.msg or self.warning, self.ACCENT | curses.A_BOLD)
        return rows

    def unfiled_actions(self, kind, item):
        return self.session_actions(item) if kind == "session" else self.note_actions(item)

    def keys_unfiled(self, k, rows):
        if isinstance(k, tuple):
            _, mx, my, bstate = k
            if my == getattr(self, "uview_y", None) and bstate & CLICK:
                for x0, x1, i in getattr(self, "uview_x", []):
                    if x0 <= mx < x1:
                        self.uview, self.usel, self.utop = i, 0, 0
            elif my in self.urow_y:
                idx = self.urow_y[my]
                how = self.click(k, idx)
                if how:
                    self.usel = idx
                kind, item = rows[idx]
                if how == "menu":
                    return self.menu(item["name"], self.unfiled_actions(kind, item))
                if how == "double":
                    fn = first_action(self.unfiled_actions(kind, item))
                    return fn() if fn else None
            return None
        to = self.move(k, self.usel, len(rows))
        if to is not None:
            self.usel = to
        elif k in ("right", "left"):
            self.uview, self.usel, self.utop = (self.uview + (1 if k == "right" else -1)) % len(UNFILED_VIEWS), 0, 0
        elif k == "enter" and rows:
            kind, item = rows[self.usel]
            return self.menu(item["name"], self.unfiled_actions(kind, item))
        return None

    def unfiled_typing(self):
        return bool(self.confirm)

    UNFILED_HELP = [
        ("Unfiled", "Sessions no project lists, and your notes: add one to a project, make it a project, or let it go"),
        ("Views", f"←→ Recent (running, and the last {RECENT_DAYS} days) · All"),
    ]


if Widget:
    class UnfiledViews(Region):
        """Recent and All, each with its count, then the search."""
        DEFAULT_CSS = "UnfiledViews { height: 1; margin: 1 0 1 0; }"

        def lines(self):
            ui, row, x = self.ui, self.line(), 2
            ui.uview_y, ui.uview_x = self.region.y, []
            for i, label in enumerate(UNFILED_VIEWS):
                tab = f" {label} ({len(ui.unfiled(i))}) "
                ui.uview_x.append((self.region.x + x, self.region.x + x + len(tab), i))
                row.put(x, tab, curses.A_REVERSE | curses.A_BOLD if i == ui.uview else ui.DIM)
                x += len(tab) + 1
            if ui.searching or ui.uquery:
                row.put(x + 2, f"/ {ui.uquery}" + ("▏" if ui.searching else ""), ui.ACCENT if ui.searching else ui.DIM)
            return [row]

    class UnfiledList(Region):
        """The items: a Session's status or a note's mark, its name, and when it was last active or edited."""
        def lines(self):
            ui, w, y0 = self.ui, self.size.width, self.region.y
            rows = getattr(ui, "urows", [])
            if not rows:
                note = self.line()
                note.put(3, "Nothing unfiled." + (" Esc clears the search." if ui.uquery else ""), ui.DIM)
                return [note]
            page = max(self.size.height, 1)
            if ui.usel < ui.utop:
                ui.utop = ui.usel
            elif ui.usel >= ui.utop + page:
                ui.utop = ui.usel - page + 1
            ui.urow_y, out = {}, []
            for i, (kind, item) in enumerate(rows[ui.utop:ui.utop + page]):
                idx, row = ui.utop + i, self.line()
                ui.urow_y[y0 + i] = idx
                hl = idx == ui.usel and ui.focus == "pane"
                look = ui.selected if hl else (lambda a: a)
                if hl:
                    row.put(1, " " * (w - 2), look(0))
                    row.put(1, "›", look(curses.A_BOLD | ui.ACCENT))
                if kind == "session":
                    mark, attr = ui.status_cell(item)
                else:
                    mark, attr = NOTE_MARK, ui.DIM
                when = short_when(item["last"])
                row.put(3, clip(mark, 18), look(attr))
                row.put(23, clip(item["name"], w - 23 - len(when) - 4), look(curses.A_BOLD if hl else 0))
                row.put(w - len(when) - 2, when, look(ui.DIM))
                out.append(row)
            return out

    class UnfiledView(Vertical):
        """The Unfiled entry's view in the right pane."""
        DEFAULT_CSS = "UnfiledView UnfiledList { height: 1fr; }"

        def compose(self):
            yield UnfiledViews()
            yield UnfiledList()
else:
    UnfiledView = None
