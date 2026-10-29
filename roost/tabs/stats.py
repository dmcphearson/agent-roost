"""The Stats tab: spend at list price, by day, kind, and project. The first tab drawn with real
Textual widgets: the shell draws the frame and the key bar, and StatsView fills the room it leaves."""
import curses, threading, time
from datetime import date, timedelta

from roost.core import projectdata
from roost.core.projectdata import plural
from roost.core import statsdata
from roost.core.formatting import clip

try:
    from rich.text import Text
    from textual.containers import Horizontal, Vertical, VerticalScroll
    from roost.app.widgets import SearchRow
    from textual.widgets import Static
except ImportError:  # the commands need none of it
    Static = None

# the look: a color per kind, and the calendar's shades of the accent
KIND_COLORS = dict(zip(statsdata.KINDS, ["#b4b8f0", "#9ece6a", "#e0af68", "#79b8ff", "#a98bf0"]))
ACCENT, MUTED, FAINT, TEXT = "#d98a63", "#9aa0b4", "#4a4f5c", "#d6dae0"
SHADES = ["#4a3f3f", "#7a5548", "#a86a52", ACCENT]
EIGHTHS = " ▏▎▍▌▋▊▉█"
STATS_KEYS = "↑↓ scroll   ←→ range   / find a project   tab switch tab   ? help   ctrl+q quit"
FRESH_FOR = 60  # seconds before coming back to the tab reads the transcripts again (only changed ones)


def money(v):
    return f"${v:,.2f}" if v < 1000 else f"${v:,.0f}"


def bar(value, top, width):
    """A bar of value out of top, width cells long at most, in eighths of a cell."""
    cells = value / top * width if top else 0
    full, part = int(cells), int((cells - int(cells)) * 8)
    return "█" * min(full, width) + (EIGHTHS[part] if part and full < width else "")


class StatsTab:
    """The Stats tab."""

    STATS_HELP = [
        ("Spend", "every reply's tokens at Anthropic's list price, before any discount: a ceiling, not the invoice"),
        ("Kinds", "Live work · Wrap-ups (after /wrap-up) · Recaps and Handoffs, Ask, and Adopt (roost's own calls, "
                  "logged in ~/.roost/spend.jsonl as they run)"),
        ("Range", "←→ Today · This week (from Monday) · Month to date · All time (the transcripts Claude Code keeps)"),
        ("Limit", "set monthly_limit in ~/.config/roost/config.json to see month to date against it"),
    ]

    def stats_typing(self):
        return False

    def stats_names(self):
        """Session id -> its name, and Session id -> the project that lists it, by its label."""
        names = {s["id"]: s["name"] for s in self.sessions}
        project_of = {}
        for p in self.projects or []:
            for sid in p.get("sessions") or []:
                project_of.setdefault(sid, projectdata.label_of(p))
        return names, project_of

    def stats_rows(self, data):
        """The spend rows with a project the log names by its folder shown by its label instead."""
        labels = {p["id"]: projectdata.label_of(p) for p in self.projects or []}
        return {**data, "rows": [(d, k, sid, labels.get(p, p) if p else p, c) for d, k, sid, p, c in data["rows"]]}

    def load_stats(self, force=False):
        """Read spend in the background (only changed transcripts are read again), at most once a minute."""
        job = getattr(self, "sjob", None)
        if job and job["state"] == "running":
            return
        if not force and job and time.time() - job["at"] < FRESH_FOR:
            return
        job = self.sjob = {"state": "running", "at": time.time(), "data": (job or {}).get("data")}

        def run():
            try:
                job["data"], job["state"] = statsdata.gather_spend(), "done"
            except Exception as e:
                job["state"], job["error"] = "failed", str(e)
        threading.Thread(target=run, daemon=True).start()

    def stats_view(self):
        """What StatsView shows now: the range's view, and each range's totals by kind, or None while
        the first read is running."""
        job = getattr(self, "sjob", None)
        if not job or not job.get("data"):
            return None
        names, project_of = self.stats_names()
        today = date.today()
        key = (id(job["data"]), self.srange, self.squery, today, tuple(sorted(project_of.items())))
        if getattr(self, "skey", None) != key:
            data = self.stats_rows(job["data"])
            view = statsdata.spend_view(data, statsdata.RANGES[self.srange], project_of, names, today)
            view["by_range"] = {r: dict(statsdata.spend_view(data, r, project_of, names, today)["kinds"])
                                for r in statsdata.RANGES}
            q = self.squery.lower()
            if q:
                view["projects"] = [p for p in view["projects"] if q in p[0].lower()]
            self.skey, self.sview = key, view
        return self.sview

    def draw_stats(self):
        """What the Stats tab shows around StatsView: where its numbers come from, and the key bar."""
        self.load_stats()
        self.corner = ("list price, before discounts", self.DIM)
        self.subhead = None
        self.key_bar(STATS_KEYS, self.msg or self.warning, self.YELLOW if self.warning and not self.msg else self.DIM)
        return []

    def stats_note(self):
        """While there's nothing to show: the first read running, or why it failed."""
        job = getattr(self, "sjob", None)
        if job and job["state"] == "failed" and not job.get("data"):
            return f"Couldn't read spend: {job.get('error')}", "#f0776c"
        return "Reading spend from the transcripts…", MUTED

    def keys_stats(self, k, drawn):
        if k in ("left", "right"):
            self.srange = (self.srange + (1 if k == "right" else -1)) % len(statsdata.RANGES)
        elif k in ("up", "down", "pageup", "pagedown", "alt+up", "alt+down", "home", "end"):
            step = {"up": -1, "down": 1, "home": -10 ** 6, "end": 10 ** 6}.get(k)
            if step is None:
                step = (1 if k in ("pagedown", "alt+down") else -1) * max(self.page - 2, 1)
            self.sscroll = max(0, self.sscroll + step)  # StatsView keeps it within what it has
        elif k == "enter":
            self.load_stats(force=True)
            self.msg = "Reading spend again"
        return None


if Static:
    class Card(Static):
        """A panel of StatsView: draws from the view the tab worked out."""
        def view(self):
            return self.app.ui.stats_view()

    class Head(Card):
        def render(self):
            v, t = self.view(), Text()
            if not v:
                text, color = self.app.ui.stats_note()
                return Text(text, style=color)
            month = v["today"].strftime("%B")
            t.append(f"Spend · {month} · {money(v['month'])} month to date\n", style=f"bold {TEXT}")
            pace = f"on pace for {money(v['pace'])} by {v['month_end']:%b} {v['month_end'].day}"
            t.append(f"list price, before discounts · {pace}", style=MUTED)
            if v["limit"]:
                share = v["month"] / v["limit"]
                width = 30
                t.append(f"\nMonthly limit {money(v['limit'])}  ", style=MUTED)
                t.append(bar(min(v["month"], v["limit"]), v["limit"], width).ljust(width, "░"),
                         style="#f0776c" if share >= 0.9 else ACCENT)
                t.append(f"  {share:.0%}", style=f"bold {TEXT}")
                if v["pace"] > v["limit"]:
                    t.append(f" · the pace passes it by {money(v['pace'] - v['limit'])}", style="#e3b341")
            return t

    class Calendar(Card):
        """GitHub-style: a column per week, a row per weekday, shaded by that day's spend."""
        def render(self):
            v, t = self.view(), Text()
            if not v:
                return t
            today, days = v["today"], v["calendar"]
            weeks = max(min((self.app.size.width - 70) // 2, 53), 4)  # Facts sits beside it
            if days:  # no further back than the first day with spend, but at least 13 weeks
                first_day = date.fromisoformat(min(days))
                weeks = min(weeks, max((today - first_day).days // 7 + 2, 13))
            first = today - timedelta(days=today.weekday()) - timedelta(weeks=weeks - 1)
            top = max(days.values(), default=0)
            months = [" "] * (weeks * 2 + 2)
            for w in range(weeks):
                d = first + timedelta(days=7 * w)
                if d.day <= 7:
                    for j, ch in enumerate(d.strftime("%b")):
                        if 2 * w + j < len(months):
                            months[2 * w + j] = ch
            t.append("     " + "".join(months) + "\n", style=MUTED)
            for wd in range(7):
                t.append(f"{['Mon', '', 'Wed', '', 'Fri', '', ''][wd]:<5}", style=MUTED)
                for w in range(weeks):
                    d = first + timedelta(days=7 * w + wd)
                    spent = days.get(d.isoformat(), 0)
                    if d > today:
                        t.append("  ")
                    elif spent < 0.005:
                        t.append("· ", style=FAINT)
                    else:
                        t.append("█ ", style=SHADES[min(int(spent / top * 4), 3)])
                t.append("\n")
            t.append("\n     Less ", style=MUTED)
            for c in SHADES:
                t.append("█ ", style=c)
            t.append("More", style=MUTED)
            return t

    class RangeRow(Card):
        """Today · This week · Month to date · All time; ←→ or a click changes it."""
        def render(self):
            ui, t = self.app.ui, Text()
            self.spans = []
            x = 1
            t.append(" ")
            for i, r in enumerate(statsdata.RANGES):
                if i:
                    t.append("  ·  ", style=FAINT)
                    x += 5
                self.spans.append((x, x + len(r), i))
                t.append(r, style=f"bold {ACCENT}" if i == ui.srange else MUTED)
                x += len(r)
            t.append("        ←→ change range", style=FAINT)
            return t

        def on_click(self, event):
            event.stop()
            for x0, x1, i in getattr(self, "spans", []):
                if x0 <= event.x < x1:
                    self.app.ui.srange = i
                    self.app.redraw()

    class RoundLine(Card):
        """Daily spend per kind as rounded step lines (╭╮╰╯), a dot legend under it."""
        def render(self):
            v, t = self.view(), Text()
            if not v:
                return t
            series = [(k, KIND_COLORS[k]) for k in statsdata.KINDS if any(v["chart"][k])]
            days = v["chart_labels"]
            w, h = max(self.size.width - 10, 10), max(self.size.height - 3, 4)
            top = max([max(v["chart"][k]) for k, _ in series] + [0.01]) * 1.05
            grid = [[(" ", "")] * w for _ in range(h)]
            row_of = lambda val: h - 1 - int(round(val / top * (h - 1)))
            for k, color in reversed(series):
                vals = v["chart"][k]
                ys = [row_of(vals[min(int(c / max(w - 1, 1) * len(days)), len(days) - 1)]) for c in range(w)]
                for c, y in enumerate(ys):
                    prev = ys[c - 1] if c else y
                    if y == prev:
                        grid[y][c] = ("─", color)
                    elif y < prev:  # up: ╯ at the old row, │ between, ╭ at the new one
                        grid[prev][c] = ("╯", color)
                        for r in range(y + 1, prev):
                            grid[r][c] = ("│", color)
                        grid[y][c] = ("╭", color)
                    else:  # down: ╮ at the old row, │ between, ╰ at the new one
                        grid[prev][c] = ("╮", color)
                        for r in range(prev + 1, y):
                            grid[r][c] = ("│", color)
                        grid[y][c] = ("╰", color)
            for r in range(h):
                label = f"{'$' + format(top * (1 - r / max(h - 1, 1)), '.0f'):>7} ┼" if r % 2 == 0 else "        │"
                t.append(label, style=MUTED)
                for ch, color in grid[r]:
                    t.append(ch, style=color)
                t.append("\n")
            ticks = [" "] * (w + 9)
            for c in range(0, max(w - 6, 1), max(w // 5, 1)):
                for j, ch in enumerate(days[min(int(c / max(w - 1, 1) * len(days)), len(days) - 1)]):
                    if 9 + c + j < len(ticks):
                        ticks[9 + c + j] = ch
            t.append("".join(ticks) + "\n", style=MUTED)
            for i, (k, color) in enumerate(series or [(statsdata.KINDS[0], KIND_COLORS[statsdata.KINDS[0]])]):
                t.append(("   " if i else "  ") + "● ", style=color)
                t.append(k, style=TEXT)
            return t

    class KindTable(Card):
        """Each kind's spend in every range, the chosen one bright, with bars for it."""
        def render(self):
            v, t = self.view(), Text()
            if not v:
                return t
            ui, by = self.app.ui, v["by_range"]
            chosen = statsdata.RANGES[ui.srange]
            heads = ["Today", "Week", "Month", "All"]
            t.append(f"{'Kind':<21}", style=MUTED)
            for i, hd in enumerate(heads):
                t.append(f"{hd:>10}", style=f"bold {ACCENT}" if i == ui.srange else MUTED)
            t.append("\n")
            top = max(by[chosen].values(), default=0)
            room = self.content_size.width - 63  # bars only when there's room for them
            for k in statsdata.KINDS:
                t.append("● ", style=KIND_COLORS[k])
                t.append(f"{k:<19}", style=TEXT)
                for i, r in enumerate(statsdata.RANGES):
                    t.append(f"{money(by[r].get(k, 0)):>10}", style=f"bold {TEXT}" if i == ui.srange else MUTED)
                t.append(("  " + bar(by[chosen].get(k, 0), top, room) if room >= 3 else "") + "\n", style=KIND_COLORS[k])
            t.append(f"{'Total':<21}", style=f"bold {TEXT}")
            for i, r in enumerate(statsdata.RANGES):
                t.append(f"{money(sum(by[r].values())):>10}", style=f"bold {ACCENT}" if i == ui.srange else MUTED)
            return t

    class ProjectBars(Card):
        def render(self):
            v, t = self.view(), Text()
            if not v:
                return t
            rows = v["projects"][:8]
            if not rows:
                return Text("No spend in this range" + (" for that search" if self.app.ui.squery else ""), style=MUTED)
            top = max(c for _, c in rows)
            name_w = max(min(self.content_size.width - 24, max(len(name) for name, _ in rows)), 10)  # the longest name, whole
            for name, cost in rows:
                t.append(f"{clip(name, name_w):<{name_w + 1}}", style=MUTED if name == statsdata.NO_PROJECT else TEXT)
                t.append(f"{money(cost):>10}  ", style=f"bold {TEXT}")
                t.append(bar(cost, top, max(self.content_size.width - name_w - 14, 3)) + "\n", style=KIND_COLORS["Live work"])
            more = len(v["projects"]) - len(rows)
            if more > 0:
                t.append(f"and {more} more", style=MUTED)
            return t

    class Facts(Card):
        """The range's records, one a line, beside the calendar."""
        def render(self):
            v, t = self.view(), Text()
            if not v:
                return t
            day, sess = v["top_day"], v["top_session"]
            busiest = next((p for p in v["projects"] if p[0] != statsdata.NO_PROJECT), None)
            room = max(self.content_size.width - 22, 10)
            rows = [("Most expensive day", f"{date.fromisoformat(day[0]):%b} {int(day[0][8:])} · {money(day[1])}" if day else "none yet"),
                    ("Priciest Session", f"{money(sess[1])} · {sess[0]}" if sess else "none yet"),
                    ("Busiest project", f"{money(busiest[1])} · {busiest[0]}" if busiest else "none yet"),
                    ("Current streak", plural(v['streak'], 'day')),
                    ("Longest streak", plural(v['longest'], 'day')),
                    ("Read from cache", f"{v['cached']:.0%} of prompt tokens" if v["cached"] is not None else "none yet")]
            t.append(f"{v['range']}\n\n", style=f"bold {TEXT}")
            for label, value in rows:
                t.append(f"{label:<20}", style=MUTED)
                t.append(clip(value, room) + "\n", style=ACCENT)
            if v["unpriced"]:
                t.append("\n" + clip(f"Not priced: {', '.join(v['unpriced'])}", room + 20), style=FAINT)
            return t

    class StatsView(VerticalScroll):
        """The Stats tab's content, placed by RoostApp in the room draw_stats leaves for it."""
        DEFAULT_CSS = """
        StatsView { height: 1fr; background: ansi_default; scrollbar-size-vertical: 1; margin: 0 1 1 1;
                    scrollbar-background: #1b1e24; scrollbar-color: #2d323a; }
        StatsView Card { background: #191c22; border: edge #3b414c; padding: 0 1; height: auto; }
        StatsView Head { background: transparent; border: none; padding: 0 1; height: auto; margin: 0 0 1 0; }
        StatsView RangeRow { background: transparent; border: none; height: 1; padding: 0; margin: 0 0 1 0; }
        StatsView Calendar { height: 13; width: auto; }
        StatsView Facts { width: 1fr; height: 13; }
        StatsView RoundLine { height: 14; }
        StatsView Horizontal { height: auto; background: transparent; }
        StatsView KindTable { width: 1fr; }
        StatsView ProjectBars { width: 1fr; height: 100%; }
        """
        can_focus = False

        def compose(self):
            yield Head()
            yield RangeRow()
            with Horizontal():
                yield Calendar()
                yield Facts()
            yield RoundLine()
            with Horizontal():
                yield KindTable()
                yield ProjectBars()

        def settle(self):
            """Redraw what changed, scrolled to where ↑↓ left it."""
            ui = self.app.ui
            ui.page = max(self.size.height, 1)
            key = (getattr(ui, "skey", None), self.size)
            if key != getattr(self, "shown", None):
                self.shown = key
                for card in self.query(Card):
                    card.refresh(layout=True)
            self.query_one(Head).refresh(layout=True)
            if round(self.scroll_y) != ui.sscroll:
                self.scroll_to(y=ui.sscroll, animate=False)
                ui.sscroll = min(ui.sscroll, round(self.max_scroll_y))

        def watch_scroll_y(self, old, new):  # the wheel or a scrollbar drag: the screens keep track
            super().watch_scroll_y(old, new)
            self.app.ui.sscroll = round(new)
    class StatsPage(Vertical):
        """The Stats tab: the search row while a project search is typed or set, then StatsView."""
        def compose(self):
            yield SearchRow()
            yield StatsView()

        def settle(self):
            ui = self.app.ui
            shown = bool(ui.searching or ui.squery)
            self.query_one(SearchRow).display = shown
            self.query_one(StatsView).styles.margin = (0, 1, 1, 1) if shown else (1, 1, 1, 1)
            self.query_one(StatsView).settle()
else:
    StatsView = StatsPage = None
