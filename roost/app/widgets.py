"""The widgets every tab shares: the tab bar, the key bar, the boxes and the actions menu over any
tab, and Line, a row of text laid out by column. Each tab's own widgets are in its module."""
import curses, re, textwrap

from roost.core.formatting import clip
from roost.core import wren
from roost.app.shell import DIVIDER, SAVE

try:
    from rich.cells import cell_len
    from rich.style import Style
    from rich.text import Text
    from textual.widget import Widget
except ImportError:  # the CLI commands need none of it
    Widget = None

# The look, after the Textual prototype: a dark slate frame, a near-black card, soft green for "you are
# here", and greys for everything secondary.
BG, CARD, RAISED, LINE = "#1b1e24", "#191c22", "#22262d", "#2d323a"
TEXT, MUTED, FAINT = "#d6dae0", "#8b929c", "#5c636d"
GREEN, GREEN_BG, YELLOW, RED, BLUE = "#6bd968", "#1d2b20", "#e3b341", "#f0776c", "#79b8ff"
# The colors the screens ask for by pair number: the five basic ones, soft grey for secondary text, a
# fainter grey, and the status colors (Okabe-Ito, distinguishable with the common kinds of color
# blindness). A selected row's band is each of those on a raised grey (100 + the pair).
PAIRS = {0: TEXT, 1: GREEN, 2: YELLOW, 3: BLUE, 4: RED, 5: GREEN, 6: MUTED,
         20: "#2fbf8f", 21: "#a98bf0", 22: YELLOW, 23: BLUE, 24: "#e8833a", 25: "#4c9df0", 26: "#a8a08c"}
MIN_SIZE = (100, 28)  # columns and rows the screens need; a smaller window says how big to make it
BAR_KEY = re.compile(r"^(page up/down|← or esc|\S+)( .*)?$")  # a key bar item: its key, then what it does


class Line:
    """One row of a widget, written column by column like a terminal row: put(x, text, attr) places
    text at column x in the look of attr (a curses attr: see TextualUI.style, or a Rich Style for exact
    colors). text() is the row as Rich Text. A wide character takes two columns; one cut in half is a space."""
    def __init__(self, ui, width, fill=None, card=False):
        self.ui, self.card, self.width = ui, card, max(width, 0)
        self.cells = [(" ", fill)] * self.width

    def put(self, x, text, attr=0):
        for ch in text:
            n = cell_len(ch)
            if n == 0:
                continue
            if x < 0 or x + n > self.width:
                break
            self.cells[x] = (ch, attr)
            if n == 2:
                self.cells[x + 1] = ("", attr)
            x += n
        return x

    def rule(self, x, n, attr):
        """A line across n columns, in the faint line color when attr is the dim one."""
        for i in range(x, min(x + max(n, 0), self.width)):
            self.cells[i] = ("─", ("rule", attr))

    def text(self):
        out, run, look = Text(), "", None
        for x, (ch, attr) in enumerate(self.cells):
            if ch == "":
                if x and cell_len(self.cells[x - 1][0]) == 2:
                    continue
                ch = " "
            elif cell_len(ch) == 2 and (x + 1 >= self.width or self.cells[x + 1][0] != ""):
                ch = " "
            if attr != look and run:
                out.append(run, self.style(look))
                run = ""
            run, look = run + ch, attr
        if run:
            out.append(run, self.style(look))
        return out

    def style(self, attr):
        if isinstance(attr, Style):
            return attr
        if isinstance(attr, tuple):
            return self.ui.style(attr[1], self.card, line=True)
        return self.ui.style(attr or 0, self.card)


def wren_below(region, out):
    """The wren, centred under a list with nothing to show, when the whole bird fits below what's drawn
    (out, the region's lines so far): never a clipped one. Adds its lines to out."""
    art = wren.wren_rows()
    if not art or len(out) + 1 + len(art) > region.size.height:
        return
    x = max((region.size.width - len(art[0])) // 2, 0)
    out.append(region.line())
    for cells in art:
        row = region.line()
        for i, (ch, fg, bg) in enumerate(cells):
            if fg:
                row.put(x + i, ch, Style(color=fg, bgcolor=bg))
        out.append(row)


def wrap_body(body, width):
    """A box's text, wrapped: a line starting with • keeps its hanging indent."""
    return [part for x in body for part in (textwrap.wrap(x, width, subsequent_indent="  " if x.startswith("•") else "") or [""])]


if Widget:
    def add_edge_border():
        """Textual's border style "edge": a thin line in the cells just outside a box, drawn with the
        eighth blocks that sit against the box (▁ ▔ ▕ ▏) on the page's color. A box-drawing line sits
        mid-cell on the box's own fill, so half a cell of fill shows past it; this one meets the fill."""
        from textual import _border
        from textual.css import constants
        _border.BORDER_CHARS["edge"] = ((" ", "▁", " "), ("▕", " ", "▏"), (" ", "▔", " "))
        _border.BORDER_LOCATIONS["edge"] = ((1, 1, 1), (1, 0, 1), (1, 1, 1))  # 1: on the page's color
        _border.BORDER_LABEL_LOCATIONS["edge"] = (1, 1)  # a title in the top edge is on the page's color too
        constants.VALID_BORDER.add("edge")

    add_edge_border()

    class Region(Widget):
        """A part of a tab drawn as Lines. Clicks, right-clicks, and the wheel go to the screens' key
        handlers as mouse events in screen columns and rows, as every tab's handler expects."""
        can_focus = False

        @property
        def ui(self):
            return self.app.ui

        def lines(self):
            return []

        def render(self):
            lines = self.lines()
            return Text("\n").join(l.text() if isinstance(l, Line) else l for l in lines) if lines else Text("")

        def line(self, fill=None):
            return Line(self.ui, self.size.width, fill, card=getattr(self, "CARD", False))

        def on_click(self, event):
            event.stop()
            if event.button == 1 and event.chain <= 2:  # a third click in a row would act a second time
                state = curses.BUTTON1_DOUBLE_CLICKED if event.chain == 2 else curses.BUTTON1_CLICKED
                self.app.press(("mouse", event.screen_x, event.screen_y, state))

        def on_mouse_down(self, event):
            if event.button == 3:
                event.stop()
                self.app.press(("mouse", event.screen_x, event.screen_y, curses.BUTTON3_PRESSED))

        def wheel(self, event, step):
            event.stop()
            ui = self.ui
            if not (ui.asking or ui.helping or ui.confirm or ui.alerting):
                self.app.press("down" if step > 0 else "up")

        def on_mouse_scroll_down(self, event):
            self.wheel(event, 1)

        def on_mouse_scroll_up(self, event):
            self.wheel(event, -1)

    class SearchRow(Region):
        """Under the tabs: the tab's search as it's typed (or kept), and what the tab lists from, at the right."""
        DEFAULT_CSS = "SearchRow { height: 1; margin: 1 0 1 0; }"

        def lines(self):
            ui, w = self.ui, self.size.width
            row, x = self.line(), 12
            query = getattr(ui, ui.query_attr(), "")
            if ui.searching or query:
                prompt = f"/ {query}" + ("▏" if ui.searching else "")
                x = row.put(x, prompt, ui.ACCENT if ui.searching else ui.DIM)
            text, attr = getattr(ui, "subhead", None) or ("", 0)
            if text:
                row.put(max(w - len(text) - 2, x + 4), text, attr)
            return [row]

    class TopBar(Region):
        """The top row: Agent Roost, the tabs (the current one in green), and the tab's own note at the right."""
        NAME = "Agent Roost"

        def lines(self):
            ui, w = self.ui, self.size.width
            row = self.line()
            row.put(1, self.NAME, curses.A_BOLD | ui.ACCENT)
            x, ui.tab_x = len(self.NAME) + 3, []
            for i, tab in enumerate(ui.TABS):
                label = f" {tab.name} "
                ui.tab_x.append((x, x + len(label), i))
                row.put(x, label, curses.A_REVERSE | curses.A_BOLD if i == ui.tab else ui.DIM)
                x += len(label) + 1
            text, attr = getattr(ui, "corner", None) or ("", 0)
            if text:
                row.put(w - len(text) - 2, text, attr)
            return [row]

    class KeyBar(Region):
        """The bottom rows: each key in green, what it does in grey, and the message or warning at the
        right. A prompt or a confirmation takes its place."""
        def lines(self):
            ui, w = self.ui, self.size.width
            lines, note_attr = getattr(ui, "bar", None) or ([], 0)
            out = []
            for keys, note in lines:
                row, x = self.line(), 2
                for item in (k for k in keys.split("   ") if k):
                    m = BAR_KEY.match(item)
                    key, what = (m.group(1), m.group(2) or "") if m and "·" not in item else ("", item)
                    row.put(x, key, ui.GREEN)
                    row.put(x + len(key), what, ui.DIM)
                    x += len(item) + 3
                if note:
                    row.put(max(w - len(note) - 3, x + 1), clip(note, w - 4), note_attr)
                out.append(row)
            return out

    class Overlay(Region):
        """A box over every tab, in the middle of the screen, with an edge in its tone's color.
        place() puts it where box() says, or hides it."""
        DEFAULT_CSS = "Overlay { layer: top; position: absolute; background: #22262d; display: none; }"

        def box(self):
            """(top, left, height, width, tone attr) for the box, or None when it's not up."""
            return None

        def place(self):
            got = self.box()
            if not got:
                self.display = False
                return
            y0, x0, bh, bw, tone = got
            self.styles.offset, self.styles.width, self.styles.height = (x0, y0), bw, bh
            self.styles.border = ("edge", self.ui.style(tone).color.name if self.ui.style(tone).color else GREEN)
            self.display = True
            self.refresh(layout=True)

        def row(self):
            return Line(self.ui, self.size.width, self.ui.selected(0))

    class TextBox(Overlay):
        """A confirmation, a failure, or the keys: a title, lines of text, and its keys, as text() gives them."""
        def text(self):
            return None  # (title, body lines, keys, tone attr)

        def box(self):
            got = self.text()
            if not got:
                return None
            title, body, keys, tone = got
            h, w = self.app.size.height, self.app.size.width
            bw = min(w - 4, max(48, len(title) + 6, len(keys) + 6, *(len(x) + 6 for x in body)), 84)
            self.body = wrap_body(body, bw - 6)[:max(0, h - 8)]
            bh = len(self.body) + 5 + (1 if self.body else 0)
            return max((h - bh) // 2, 0), max((w - bw) // 2, 0), bh, bw, tone

        def lines(self):
            got = self.text()
            if not got:
                return []
            ui, (title, _, keys, tone) = self.ui, got
            inner = self.size.width
            out = [self.row() for _ in range(self.size.height)]
            out[0].put(2, clip(title, inner - 4), ui.selected(curses.A_BOLD | tone))
            for i, line in enumerate(self.body):
                out[2 + i].put(2, line, ui.selected(0))
            out[-1 if len(out) < 2 else len(out) - 1].put(2, clip(keys, inner - 4), ui.selected(ui.DIM))
            return out

    class ConfirmBox(TextBox):
        def text(self):
            return self.ui.confirm_text() if self.ui.confirm else None

    class AlertBox(TextBox):
        def text(self):
            return self.ui.alert_text() if self.ui.alerting else None

    class HelpBox(Overlay):
        """? : the keys every tab shares, then the current tab's."""
        def box(self):
            ui = self.ui
            if not ui.helping:
                return None
            h, w = self.app.size.height, self.app.size.width
            rows = ui.help_rows()
            bw = min(w - 4, max(len(a) + len(b) for a, b in rows) + 16)
            bh = len(rows) + 4
            return max((h - bh) // 2, 0), max((w - bw) // 2, 0), bh, bw, ui.ACCENT

        def lines(self):
            ui = self.ui
            if not ui.helping:
                return []
            rows, inner = ui.help_rows(), self.size.width
            out = [self.row() for _ in range(self.size.height)]
            out[0].put(2, f"Keys, then {ui.TABS[ui.tab].name}", ui.selected(curses.A_BOLD | ui.ACCENT))
            for i, (a, b) in enumerate(rows):
                if 1 + i < len(out):
                    out[1 + i].put(2, a.ljust(9), ui.selected(curses.A_BOLD))
                    out[1 + i].put(12, clip(b, inner - 14), ui.selected(0))
            out[-1].put(2, "any key closes", ui.selected(ui.DIM))
            return out

    class Menu(Overlay):
        """A list to pick from (a row's actions, a picker): its title, its text, the options, and its keys.
        It records where it is in the screen's columns and rows, for a click on an option."""
        def box(self):
            ui, c = self.ui, self.ui.choosing
            if not c:
                return None
            h, w = self.app.size.height, self.app.size.width
            opts = ui.choice_options()
            bw = min(w - 4, max([len(c["title"]) + 6, 44, 76 if c.get("body") else 0]
                                + [len(label) + 10 for label, _ in c["options"]]))
            self.body = wrap_body(c.get("body") or [], bw - 6)[:max(0, h - 12)]
            off = 2 + (len(self.body) + 2 if self.body else 0)  # the options' first row, below the title and the text
            bh = min(max(len(opts), 1) + 3 + off, h - 2)
            y0, x0 = max((h - bh) // 2, 0), max((w - bw) // 2, 0)
            c["box"], c["off"], c["fit"] = (y0, x0, bw, bh), off, bh - 3 - off
            c["top"] = max(0, min(c["sel"] - c["fit"] + 1, len(opts) - c["fit"]))
            return y0, x0, bh, bw, ui.ACCENT

        def lines(self):
            ui, c = self.ui, self.ui.choosing
            if not c or "box" not in c:
                return []
            opts, inner = ui.choice_options(), self.size.width
            out = [self.row() for _ in range(self.size.height)]
            out[0].put(2, clip(c["title"], inner - 4), ui.selected(curses.A_BOLD | ui.ACCENT))
            for i, line in enumerate(self.body):
                out[2 + i].put(2, line, ui.selected(0))
            top, first = c["top"], c["off"] - 1  # the border takes the box's first row
            for i, (label, value) in enumerate(opts[top:top + c["fit"]]):
                on = top + i == c["sel"]
                if (label, value) == DIVIDER:
                    if first + i < len(out):
                        out[first + i].put(2, "─" * (inner - 4), ui.selected(ui.DIM))
                    continue
                if c.get("multi") is not None and not isinstance(value, tuple) and value is not SAVE:
                    label = ("☑ " if value in c["multi"] else "☐ ") + label
                if first + i < len(out):
                    out[first + i].put(2, clip(f"{'›' if on else ' '} {label}", inner - 4),
                                       ui.selected(curses.A_BOLD if on else 0) | (curses.A_UNDERLINE if on else 0))
            if not opts and first < len(out):
                out[first].put(4, "nothing matches", ui.selected(ui.DIM))
            foot = "↑↓ choose · enter tick · esc cancel" if c.get("multi") is not None else "↑↓ choose · enter pick · esc cancel"
            if c["typed"] is not None:
                foot = f"{'type to find or add' if c.get('add') else 'filter'}: {c['typed']}▏  " + foot
            if c.get("keys"):  # a letter picks its row
                foot = " ".join(dict.fromkeys(k.upper() for k in c["keys"])) + " picks · ↑↓ enter · esc cancel"
            out[-1].put(2, clip(foot, inner - 4), ui.selected(ui.DIM))
            return out

    class TooSmall(Region):
        """What shows instead of the screens in a window under MIN_SIZE."""
        DEFAULT_CSS = "TooSmall { layer: top; width: 100%; height: 100%; display: none; background: ansi_default; }"

        def lines(self):
            ui, (h, w) = self.ui, (self.size.height, self.size.width)
            text = ["roost needs a bigger window", f"at least {MIN_SIZE[0]} × {MIN_SIZE[1]}; this one is {w} × {h}",
                    "", "ctrl+q quits"]
            out = [self.line() for _ in range(h)]
            for i, t in enumerate(text):
                if 0 <= h // 2 - 2 + i < h:
                    out[h // 2 - 2 + i].put(max((w - len(t)) // 2, 0), t, curses.A_BOLD if i == 0 else ui.DIM)
            return out
