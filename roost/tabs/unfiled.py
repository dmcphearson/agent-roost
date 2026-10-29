"""The Sessions tab's Unfiled view: the Sessions no project lists and the notes in ~/.roost/notes, to file
into a project or let go."""
import curses

from roost.core.formatting import clip, short_when
from roost.app.shell import first_action
from roost.app.widgets import Widget
from roost.core import sessiondata

if Widget:
    from roost.app.widgets import Region

NOTE_MARK = "✎"


class UnfiledTab:
    usel, utop, uquery, urow_y = 0, 0, "", {}

    def unfiled(self, search=True):
        """Unfiled's items as (kind, item): the Sessions no project lists (and not dismissed), and the loose
        notes; running Sessions first, then by their last activity or edit, newest first."""
        gone = sessiondata.read_dismissed()
        q = self.uquery.lower() if search else ""
        items = [("session", s) for s in self.sessions if s["id"] not in gone and not self.project_of(s)]
        items += [("note", n) for n in self.notes or []]
        if q:
            items = [(k, x) for k, x in items if q in (x.get("name") or "").lower()]
        running = lambda k, x: k == "session" and x["state"] != "closed"
        return sorted(items, key=lambda kx: (not running(*kx), -kx[1]["last"]))

    def draw_unfiled(self):
        """The Unfiled view's rows, from the Sessions tab's draw."""
        rows = self.urows = self.unfiled()
        self.usel = max(0, min(self.usel, len(rows) - 1))
        return rows

    def unfiled_actions(self, kind, item):
        return self.session_actions(item) if kind == "session" else self.note_actions(item)

    def keys_unfiled(self, k, rows):
        """Unfiled's keys, from the Sessions tab's: ↑↓, enter for an item's actions, and the mouse on its rows."""
        if isinstance(k, tuple):
            if k[2] in self.urow_y:
                idx = self.urow_y[k[2]]
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
        elif k == "enter" and rows:
            kind, item = rows[self.usel]
            return self.menu(item["name"], self.unfiled_actions(kind, item))
        return None


if Widget:
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
                hl = idx == ui.usel
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
