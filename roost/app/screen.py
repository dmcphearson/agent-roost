"""The Textual front end: the screens as widgets, and the keys and the mouse fed back to them."""
import curses, time, traceback

from roost.app.shell import QUIT
from roost.app.ui import UI
from roost.app.widgets import (BG, BLUE, CARD, FAINT, GREEN, GREEN_BG, LINE, MIN_SIZE, MUTED, PAIRS, RAISED, RED,
                               TEXT, YELLOW, Widget)
from roost.core import config


# ---------- the Textual front end ----------
# roost's screens run in Textual (installed in ~/.roost/venv by roost setup). Each tab works out what
# it shows (UI.draw), and its widgets draw that: the shared ones in roost.app.widgets, each tab's own
# in its module. Keys and the mouse come from Textual, and the editor gets the terminal through
# Textual's suspend.

if Widget:
    from rich.style import Style
    from textual.app import App
    from textual.screen import Screen
    from textual.containers import Horizontal, Vertical
    from textual.widgets import Input, Label
    from roost.app.widgets import AlertBox, ConfirmBox, HelpBox, KeyBar, Menu, Region, TooSmall, TopBar
else:  # the CLI commands need none of it; main() says how to get it
    App = None


class Size:
    """The window's size, asked the way the screens ask a curses window."""
    def __init__(self, app):
        self.app = app

    def getmaxyx(self):
        size = self.app.size
        return max(size.height, 1), max(size.width, 1)


class TextualUI(UI):
    """The screens on Textual: colors by pair number, as the screens ask for them, and the terminal
    handed to an editor."""
    def __init__(self, app, sessions, query):
        self.app, self.scr = app, Size(app)
        self.setup(sessions, query)
        self.GREEN, self.YELLOW, self.CYAN, self.RED, self.ACCENT = (self.pair(i) for i in range(1, 6))
        self.DIM = self.pair(6)
        self.status = {k: self.pair(i) for i, k in enumerate(
            ["working", "idle", "input", "expiring", "expired", "opening", "compacted"], 20)}
        self.styles = {}
        self.too_small = False

    @staticmethod
    def pair(n):
        return n << 8

    @staticmethod
    def pair_of(attr):
        return (attr & curses.A_COLOR) >> 8

    def style(self, attr, card=False, line=False):
        """The Rich style for a curses attr: on the card's background inside a card, and a rule (a line
        drawn in the dim color) in the faint line color. The selected row is a raised band, and what
        curses shows in reverse (the current tab) is green."""
        key = (attr, card, line)
        if key not in self.styles:
            n = self.pair_of(attr)
            base = n - 100 if n >= 100 else n
            fg = LINE if line and base == 6 else FAINT if base == 7 else PAIRS.get(base, TEXT)
            if attr & curses.A_REVERSE:
                fg = GREEN
            bg = RAISED if n >= 100 else CARD if card else "default"  # the terminal's background: roost sets it to BG
            self.styles[key] = Style(color=fg, bgcolor=bg, bold=bool(attr & (curses.A_BOLD | curses.A_REVERSE)) or None,
                                     underline=bool(attr & curses.A_UNDERLINE) or None,
                                     italic=bool(attr & getattr(curses, "A_ITALIC", 0)) or None)
        return self.styles[key]

    def in_terminal(self, run):
        self.app.window_look(False)  # the editor gets the terminal's own colors
        try:
            suspended = self.app.suspend()
            suspended.__enter__()
        except Exception as e:  # Textual can't give the terminal away here (a test, say)
            self.app.window_look(True)
            raise OSError(f"this terminal can't hand the screen to an editor ({e})")
        try:
            run()
        finally:
            suspended.__exit__(None, None, None)
            self.app.window_look(True)


if Widget:
    class Keys(Widget):
        """Takes the keys (it has the focus whenever a prompt doesn't) and hands them to the screens."""
        can_focus = True
        DEFAULT_CSS = "Keys { width: 0; height: 0; }"

        def on_key(self, event):
            event.stop()
            event.prevent_default()
            if event.key == "ctrl+c" and self.screen.get_selected_text():  # copies a selection; quits without one
                self.screen.copy_selection()
                return
            self.app.press(event.character if event.is_printable and event.character else event.key)

    class Prompt(Vertical):
        """The prompt: a box in the middle of the screen when it has a title (ctrl+n's), else on the bottom row."""

        def __init__(self):
            super().__init__()
            self.asking = None

        def compose(self):
            with Horizontal():
                yield Label(id="ask")
                yield Input(select_on_focus=False)
                yield Label(id="hint")

        def show(self, a):
            """Put up the prompt a (see UI.ask), once: after that the input keeps what's typed."""
            if a is self.asking:
                return
            self.asking, h, w = a, self.app.size.height, self.app.size.width
            box = bool(a.get("title"))
            self.set_classes("box" if box else "line")
            self.border_title = a.get("title") or ""
            self.query_one("#ask", Label).update(a["prompt"])
            self.query_one("#hint", Label).update(f"enter {a['verb']} · esc cancel")
            # the hint goes under the input in a box: a second row of the same Horizontal can't, so it's moved
            hint = self.query_one("#hint", Label)
            if box and hint.parent is not self:
                hint.remove()
                self.mount(Label(f"enter {a['verb']} · esc cancel", id="hint"))
            elif not box and hint.parent is self:
                hint.remove()
                self.query_one(Horizontal).mount(Label(f"enter {a['verb']} · esc cancel", id="hint"))
            self.styles.offset = (max((w - 64) // 2, 0), max(h // 2 - 3, 0)) if box else (0, h - 1)
            field = self.query_one(Input)
            field.value = a["text"]
            field.cursor_position = len(a["text"])
            self.display = True
            field.focus()

        def hide(self):
            if self.asking is not None:
                self.asking, self.display = None, False
                self.app.query_one(Keys).focus()

        def on_input_changed(self, event):
            if self.app.ui.asking is self.asking and self.asking is not None:
                self.asking["text"] = event.value

        def on_input_submitted(self, event):
            event.stop()
            self.app.press("enter")

        def on_key(self, event):
            if event.key in ("escape", "tab", "shift+tab"):  # tab stays in the prompt, as in the curses screen
                event.stop()
                event.prevent_default()
                if event.key == "escape":
                    self.app.press("escape")

    class RoostScreen(Screen):
        """The one screen. A double or triple click acts on a row, so it doesn't also select the whole
        widget's text, as Textual does by default. Text dragged over is copied when the mouse is let go:
        the terminal keeps cmd+c for its own selection, so it never reaches roost."""
        def _select_all_in_widget(self, widget):
            pass

        def on_text_selected(self, event):
            if self.get_selected_text():  # a plain click selects nothing
                self.copy_selection()

        def copy_selection(self):
            self.app.ui.copy_text(self.get_selected_text(), "Copied the selected text")
            self.app.redraw()

    class RoostApp(App):
        """roost on Textual. Returns a Session to open in this terminal when there's no other, or None."""
        # the app's CSS, so it wins over the Textual widgets' own
        CSS = """
        Screen { background: ansi_default; layers: base over top; }
        TopBar { height: 1; }
        KeyBar { height: 1; }
        Prompt { layer: top; position: absolute; display: none; background: #22262d; color: #d6dae0; height: auto; }
        Prompt.box { width: 64; border: edge #6bd968; border-title-color: #6bd968;
                     border-title-style: bold; padding: 0 1; }
        Prompt.box Horizontal { height: 1; margin: 1 0; }
        Prompt.line { width: 100%; height: 1; padding: 0 2; }
        Prompt.line Horizontal { height: 1; }
        Prompt Label#ask { color: #6bd968; text-style: bold; width: auto; }
        Prompt Input { border: none; height: 1; padding: 0; background: #22262d; color: #d6dae0; width: 1fr; }
        Prompt Input:focus { border: none; }
        Prompt Label#hint { color: #8b929c; width: auto; }
        Prompt.line Label#hint { padding: 0 0 0 3; }
        """
        ENABLE_COMMAND_PALETTE = False

        def __init__(self, sessions, query):
            super().__init__(ansi_color=True)  # the frame is the terminal's own background, translucency and all
            self.ui = TextualUI(self, sessions, query)
            self.drawn, self.last_key, self.last_refresh = None, time.time(), time.time()

        def get_default_screen(self):
            return RoostScreen(id="_default")

        def compose(self):
            yield Keys()
            yield TopBar()
            for tab in self.ui.TABS:
                yield tab.widget()
            yield KeyBar()
            yield ConfirmBox()
            yield Prompt()
            yield Menu()
            yield HelpBox()
            yield AlertBox()
            yield TooSmall()

        def on_mount(self):
            self.window_look(True)
            self.query_one(Keys).focus()
            self.set_interval(1, self.tick)
            self.redraw()

        def on_resize(self, event):
            self.redraw()

        def redraw(self):
            """Work out what the screens show, then have every widget that shows it draw again."""
            ui = self.ui
            h, w = ui.scr.getmaxyx()
            ui.too_small = w < MIN_SIZE[0] or h < MIN_SIZE[1]
            small = self.query_one(TooSmall)
            small.display = ui.too_small
            if ui.too_small:
                small.refresh()
                return
            self.drawn = ui.draw()
            for i, tab in enumerate(ui.TABS):
                view = self.query_one(tab.widget)
                view.display = i == ui.tab
                if i == ui.tab:
                    if hasattr(view, "settle"):
                        view.settle()
                    for part in view.query(Region):
                        part.refresh()
            self.query_one(TopBar).refresh()
            bar = self.query_one(KeyBar)
            bar.styles.height = max(len(ui.bar[0]) if ui.bar else 1, 1)
            bar.refresh()
            for box in (ConfirmBox, Menu, HelpBox, AlertBox):
                self.query_one(box).place()
            if ui.asking:
                self.query_one(Prompt).show(ui.asking)
            else:
                self.query_one(Prompt).hide()

        def tick(self):
            """Each second: redraw (a summary's clock counts up); when no key came for a few seconds,
            re-read status, last used, context, and summaries first, as the curses screen did."""
            now = time.time()
            if now - self.last_key >= 3 and now - self.last_refresh >= 2.5:
                self.last_refresh = now
                try:
                    self.ui.refresh()
                except Exception as e:
                    config.log(f"refresh: {e}")
            self.redraw()

        def press(self, k):
            self.last_key = time.time()
            if self.ui.too_small and k != "ctrl+q":
                return  # the window is too small to show what a key would do
            try:
                result = self.ui.dispatch(k, self.drawn)
            except Exception as e:  # a bug in one action is a box, not the end of roost (and its background runs)
                config.log(f"key {k!r}: {traceback.format_exc()}")
                self.ui.msg, result = f"Couldn't do that: {e} · roost's log has the details", None
            if result is not None:
                self.exit(None if result is QUIT else result)
                return
            self.redraw()

        def action_quit(self):  # ctrl+q, which Textual binds first: roost decides (a summary may be running)
            self.press("ctrl+q")

        def action_help_quit(self):  # ctrl+c quits, as it did in the curses screen
            self.exit(None)

        def on_unmount(self):
            self.window_look(False)

        def window_look(self, on):
            """While roost runs, the terminal's own background (its padding, and a title bar that follows
            it) is roost's frame color, and the window is titled roost. Off puts the terminal's back."""
            driver = getattr(self, "_driver", None)
            if driver:
                driver.write(f"\x1b]11;{BG}\x07\x1b]2;roost\x07" if on else "\x1b]111\x07")


def textual_python():
    """The Python in ~/.roost/venv, if roost setup has made it."""
    py = config.VENV / "bin" / "python"
    return py if py.exists() else None


def run_screens(sessions, query):
    """Run roost's screens on Textual. Returns a Session to open in this terminal, or None."""
    return RoostApp(sessions, query).run()
