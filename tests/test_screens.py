from support import *  # noqa: F401,F403 (the shared namespace, helpers, and fixtures)
from support import _SCRATCH, own_tests  # noqa: F401

load_tests = own_tests(__name__)


@unittest.skipUnless(roost.App, "Textual isn't installed here: run the tests with ~/.roost/venv/bin/python")
class TextualScreens(unittest.TestCase):
    """The Textual front end, driven by Textual's pilot on made-up Sessions; no terminal or editor."""
    def setUp(self):
        import unittest.mock
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        stubs = dict(read_projects=lambda sessions: [], read_recaps=lambda: [], plugin_problem=lambda: "",
                     check_compat=lambda: "", plugin_registered=lambda s=None: True, taken_plugin_change=lambda: "",
                     retention=lambda: (30, None), update_available=lambda: "", chosen_terminal=lambda: "ghostty",
                     mark_open=lambda ss, term: ss, regather=lambda old: old, read_config=lambda: {},
                     save_config=lambda **kw: None, NOTES=Path(tmp.name) / "notes",
                     # the Projects tab reads spend too: never from the person's transcripts
                     gather_spend=lambda table=None, now=None: {"rows": [], "hours": {}, "tokens": {},
                                                                "unpriced": [], "priced": True})
        patcher = unittest.mock.patch.multiple(roost, **stubs)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.sessions = [session("Printer fix", mins=5, tokens=41000), session("Laptop refresh", state="waiting")]

    def run_app(self, steps, size=(120, 30)):
        """Run the app at size (120×30 unless asked), do each step with the pilot, and return the app."""
        import asyncio
        app = roost.RoostApp(self.sessions, "")

        async def go():
            async with app.run_test(size=size) as pilot:
                await pilot.pause()
                for step in steps:
                    await step(pilot)
                    await pilot.pause()
                app.lines = screen_text(app)
        asyncio.run(go())
        return app

    @staticmethod
    def keys(*keys):
        return lambda pilot: pilot.press(*keys)

    def screen(self, app):
        return app.lines

    def test_a_wrap_up_shows_under_its_own_heading_at_the_bottom(self):
        done = dict(self.sessions[0], state="closed", kind=None, attach=None)  # stopped for its summary
        self.sessions = [done, self.sessions[1]]

        async def start(pilot):
            pilot.app.ui.summaries = {done["id"]: {"state": "running", "text": "writing", "name": done["name"],
                                                   "step": 1, "steps": 3, "started": time.time()}}
            await open_view("Sessions")(pilot)
        lines = self.screen(self.run_app([start]))  # the whole width: the Sessions tab has no sidebar
        heads = [l.strip().split(" (")[0] for l in lines if "(1)" in l]
        self.assertEqual(heads[-1], "Wrapping up")
        self.assertLess(next(i for i, l in enumerate(lines) if "Laptop refresh" in l),
                        next(i for i, l in enumerate(lines) if "Printer fix" in l))

    def test_the_sessions_tab_names_each_sessions_project(self):
        import unittest.mock
        pid = "2026-10-printer-fleet"
        proj = {"id": pid, "path": "/nowhere/" + pid, "sessions": [self.sessions[0]["id"]], "summary": "", "status": "active",
                "category": "", "tags": [], "last": None, "closed": False, "readme": True}
        with unittest.mock.patch.object(roost, "read_projects", lambda sessions: [proj]):
            app = self.run_app([open_view("Sessions")])
        lines = self.screen(app)
        self.assertEqual(app.ui.TABS[app.ui.tab].name, "Sessions")
        self.assertIn("PROJECT", next(l for l in lines if "NAME" in l))
        self.assertIn(pid, next(l for l in lines[:-10] if "Printer fix" in l))
        self.assertNotIn(pid, next(l for l in lines[:-10] if "Laptop refresh" in l))
        panel = "\n".join(lines[-10:])
        self.assertRegex(panel, r"Project +(2026-10-printer-fleet|none)")
        self.assertIn("Context", panel)

    def test_it_draws_the_same_frame_and_tab_switches_tabs(self):
        app = self.run_app([self.keys("tab")])
        lines = self.screen(app)
        self.assertRegex(lines[0], r"^\s*Agent Roost\s+Projects\s+Sessions\s+Notes\s+Stats(\s|$)")
        self.assertEqual(app.ui.TABS[app.ui.tab].name, "Sessions")
        self.assertIn("Printer fix", "\n".join(lines))
        self.assertIn("ctrl+q quit", lines[-1])

    def test_a_confirmation_is_a_box_with_its_keys(self):
        p = {"id": "2026-10-a-project-with-a-long-enough-name-to-crowd-the-row", "path": "/nowhere/x"}

        async def ask(pilot):
            ui = pilot.app.ui
            ui.offer, ui.confirm = ("claude-sonnet-5-5", 5.0, 0.08, None), ("adopt", p)
            pilot.app.redraw()
        app = self.run_app([ask])
        shown = "\n".join(self.screen(app))  # a box in the middle of the screen, keys and all
        for part in ("Plan how to adopt 2026-10-a-project", "about $0.08 with claude-sonnet-5-5", "y to plan · any other key cancels"):
            self.assertIn(part, shown)

    def test_a_failure_is_a_box_until_a_key(self):
        async def fail(pilot):
            pilot.app.ui.msg = "Couldn't move it: [Errno 13] Permission denied: '/x'"
            pilot.app.redraw()

        async def done(pilot):
            pilot.app.ui.msg = "Moved it to the Trash"
            pilot.app.redraw()
        app = self.run_app([fail])
        shown = "\n".join(self.screen(app))
        for part in ("Couldn't move it", "[Errno 13] Permission denied: '/x'", "any key closes"):
            self.assertIn(part, shown)
        self.assertFalse(self.screen(app)[-1].rstrip().endswith("Permission denied: '/x'"))  # not the corner note
        app = self.run_app([fail, self.keys("down")])
        self.assertIsNone(app.ui.alerting)
        self.assertNotIn("any key closes", "\n".join(self.screen(app)))
        app = self.run_app([done])  # a success stays a note in the corner
        self.assertIsNone(app.ui.alerting)
        self.assertIn("Moved it to the Trash", self.screen(app)[-1])

    def test_a_key_handler_that_raises_is_a_box_not_a_crash(self):
        import unittest.mock
        def boom(ui, k, rows):
            raise RuntimeError("boom")
        # the Sessions view's keys, found by name: Unfiled sits between Projects and Sessions since step 3b-1
        tabs = [t._replace(keys=boom) if t.name == "Sessions" else t for t in roost.UI.TABS]
        with unittest.mock.patch.object(roost.UI, "TABS", tabs), unittest.mock.patch.object(roost, "log"):
            app = self.run_app([open_view("Sessions"), self.keys("j")])
        self.assertTrue(app.ui.alerting)
        self.assertIn("boom", "\n".join(self.screen(app)))

    def test_ctrl_q_quits_and_plain_q_is_a_letter(self):
        app = self.run_app([self.keys("q")])
        self.assertIsNone(app.return_value)
        app = self.run_app([self.keys("ctrl+q")])
        self.assertFalse(app.is_running)

    def test_the_window_takes_roosts_color_and_title_and_gets_them_back(self):
        import unittest.mock
        sent = []
        with unittest.mock.patch("textual.drivers.headless_driver.HeadlessDriver.write", lambda self, d: sent.append(d)):
            self.run_app([self.keys("ctrl+q")])
        looks = [d for d in sent if d.startswith("\x1b]1")]
        self.assertEqual(looks, ["\x1b]11;#1b1e24\x07\x1b]2;roost\x07", "\x1b]111\x07"])

    def test_the_wheel_moves_the_sessions_list(self):
        wheel = type("Wheel", (), {"x": 10, "y": 6, "stop": lambda self: None})()

        async def scroll(pilot):
            pilot.app.query_one(roost.SessionList).on_mouse_scroll_down(wheel)
        app = self.run_app([open_view("Sessions"), scroll])
        self.assertEqual((app.ui.TABS[app.ui.tab].name, app.ui.sel), ("Sessions", 1))

    def test_a_triple_click_acts_once(self):
        import unittest.mock
        ran = []
        with unittest.mock.patch.object(roost.UI, "session_actions", lambda ui, s: [("Open", lambda: ran.append(s["name"]))]):
            self.run_app([open_view("Sessions"), lambda pilot: pilot.click(offset=(10, 6), times=3)])
        self.assertEqual(len(ran), 1)

    def test_a_double_or_triple_click_selects_no_text(self):
        """Clicks act on rows; text is selected only by dragging over it, for cmd+c. The pilot's own
        clicks don't name the widget under the mouse, which Textual's select-all needs, so these do."""
        import unittest.mock
        from textual import events
        seen = []

        async def clicks(pilot):
            x = 10  # a Session's row
            w, _ = pilot.app.get_widget_at(x, 6)
            for chain in (2, 3):
                pilot.app.screen.selections = {}
                w.post_message(events.Click(w, x, 6, 0, 0, 1, False, False, False, screen_x=x, screen_y=6, chain=chain))
                await pilot.pause()
                seen.append(dict(pilot.app.screen.selections))
        with unittest.mock.patch.object(roost.UI, "session_actions", lambda ui, s: [("Open", lambda: None)]):
            self.run_app([open_view("Sessions"), clicks])
        self.assertEqual(seen, [{}, {}])

    def test_letting_go_after_a_drag_copies_the_text_and_ctrl_c_copies_it_too(self):
        """Ghostty keeps cmd+c for its own selection, so roost copies what's dragged over as soon as the
        mouse is let go; ctrl+c copies a selection too, and quits only when nothing is selected."""
        import unittest.mock
        from textual import events
        copied, running = [], []

        async def drag(pilot):
            with unittest.mock.patch.object(type(pilot.app.screen), "get_selected_text", lambda screen: "Printer fix"):
                pilot.app.screen.post_message(events.TextSelected())
                await pilot.pause()
                await pilot.press("ctrl+c")
                running.append(pilot.app.is_running)
            pilot.app.screen.post_message(events.TextSelected())  # a plain click: nothing selected, nothing copied
            await pilot.pause()
        with unittest.mock.patch.object(roost.UI, "clipboard", lambda ui, text: copied.append(text)):
            app = self.run_app([drag])
        self.assertEqual((copied, running), (["Printer fix", "Printer fix"], [True]))
        self.assertEqual(app.ui.msg, "Copied the selected text")

    def test_ctrl_c_with_nothing_selected_copies_nothing(self):
        import unittest.mock
        copied = []
        with unittest.mock.patch.object(roost.UI, "clipboard", lambda ui, text: copied.append(text)):
            app = self.run_app([self.keys("ctrl+c")])
        self.assertEqual((copied, app.ui.msg), ([], ""))

    def test_a_right_click_opens_a_rows_actions(self):
        app = self.run_app([open_view("Sessions"), lambda pilot: pilot.click(offset=(10, 6), button=3)])
        self.assertTrue(app.ui.choosing)
        self.assertIn(app.ui.choosing["title"], [s["name"] for s in self.sessions])

    def test_ctrl_n_asks_for_a_title_then_makes_the_note(self):
        import unittest.mock
        with tempfile.TemporaryDirectory() as tmp:  # a stand-in editor, so the test needs none installed
            stub = Path(tmp) / "stub-editor"
            stub.write_text("#!/bin/sh\nexit 0\n")
            stub.chmod(0o755)
            with unittest.mock.patch.multiple(roost, read_config=lambda: {"editor": str(stub)}):
                app = self.run_app([self.keys("ctrl+n", "n"), self.keys(*"Q4 ideas"), self.keys("enter")])
        self.assertTrue((roost.NOTES / "q4-ideas.md").exists())
        self.assertIn("Couldn't open the editor", app.ui.alerting)  # a test has no terminal to give it; a failure is a box

    def test_the_edge_border_meets_the_fill(self):
        """The eighth blocks sit against the box, on the page's color, so no fill shows past the line."""
        from textual import _border
        from textual.css import constants
        self.assertIn("edge", constants.VALID_BORDER)
        self.assertEqual(_border.BORDER_CHARS["edge"], ((" ", "▁", " "), ("▕", " ", "▏"), (" ", "▔", " ")))
        self.assertEqual(_border.BORDER_LOCATIONS["edge"][1], (1, 0, 1))

    def test_a_line_keeps_wide_characters(self):
        app = self.run_app([])
        line = roost.Line(app.ui, 10)
        line.put(0, "📌 ab")
        self.assertEqual(line.text().plain, "📌 ab     ")
        line.put(1, "x")  # over a wide character's second half: it's blanked, not shifted
        self.assertEqual(line.text().plain, " x ab     ")

    def test_a_new_line_in_the_file_is_a_new_line_in_the_card(self):
        md = roost.line_breaks(roost.MarkdownIt("gfm-like"))
        kinds = [c.type for t in md.parse("first line\nsecond line") for c in t.children or []]
        self.assertEqual(kinds, ["text", "hardbreak", "text"])
        text = roost.note_markdown({"lines": ["- [ ] open", "- [x] done", "```", "- [ ] code", "```"]})
        self.assertEqual(text.splitlines()[:2], ["- ☐ open", "- ☑ done"])
        self.assertIn("- [ ] code", text)  # code is left as it is

    def test_a_warning_sits_in_the_panels_top_edge(self):
        import unittest.mock
        with unittest.mock.patch.object(roost, "days_left", lambda s, rules: 6):
            app = self.run_app([open_view("Sessions")])
        lines = self.screen(app)
        edge = next(i for i, l in enumerate(lines) if "⚠ deleted in 6 days" in l)
        self.assertTrue(lines[edge].lstrip().startswith("─"))  # the rule over the panel
        self.assertNotIn("deleted", lines[edge + 1])  # the facts line has its own row

    def test_a_prompt_on_the_bottom_row_is_a_textual_input(self):
        async def rename(pilot):
            pilot.app.ui.rename(self.sessions[0])
            pilot.app.redraw()
        app = self.run_app([open_view("Sessions"), rename, self.keys("backspace", "backspace", "!")])
        self.assertEqual(app.ui.asking["text"], "Printer f!")
        app = self.run_app([open_view("Sessions"), rename, self.keys("escape")])
        self.assertEqual((app.ui.asking, app.ui.msg), (None, "Cancelled"))

    def test_a_small_window_says_how_big_to_make_it_and_ignores_keys(self):
        import asyncio
        app = roost.RoostApp(self.sessions, "")

        async def go():
            async with app.run_test(size=(90, 24)) as pilot:
                await pilot.pause()
                await pilot.press("tab")  # with room, tab would open the Sessions tab
                await pilot.pause()
                app.lines = screen_text(app)
        asyncio.run(go())
        self.assertIn("at least 100 × 28; this one is 90 × 24", "\n".join(self.screen(app)))
        self.assertEqual(app.ui.TABS[app.ui.tab].name, "Projects")

    def test_on_notes_ctrl_n_and_a_rows_actions_show_over_the_card(self):
        roost.create_note("Printer idea", body="first line")
        seen = {}

        async def look(pilot):
            seen["prompt"] = pilot.app.query_one(roost.Prompt).region
        app = self.run_app([open_view("Notes"), self.keys("ctrl+n", "N"), look])
        p = seen["prompt"]
        self.assertTrue(p.height and p.bottom <= 30, p)  # the title box is on screen, not under the reader
        app = self.run_app([open_view("Notes"), self.keys("enter")])
        y0, x0, bw, bh = app.ui.choosing["box"]
        title = self.screen(app)[y0 + 1][x0 + 3:x0 + 3 + len("Printer idea")]
        self.assertEqual(title, "Printer idea")  # nothing of the card is drawn over the menu

    def grouped_menu(self, ran):
        """A step that puts up a menu of a, b | c, with each action recording its name in ran."""
        async def step(pilot):
            act = lambda name: (name, lambda: ran.append(name))
            pilot.app.ui.menu("Things", [act("a"), act("b"), roost.DIVIDER, act("c")])
            pilot.app.redraw()
        return step

    @staticmethod
    def option_y(ui, i):
        """The screen row of the menu's option i, where the Menu drew it."""
        y0, x0, bw, bh = ui.choosing["box"]
        return y0 + ui.choosing["off"] + i - ui.choosing.get("top", 0)

    def test_a_menus_divider_is_drawn_as_a_rule_not_an_option(self):
        app = self.run_app([self.grouped_menu([])])
        ui = app.ui
        opts = ui.choice_options()
        i = next(i for i, o in enumerate(opts) if tuple(o) == tuple(roost.DIVIDER))
        y0, x0, bw, bh = ui.choosing["box"]
        inside = lambda y: self.screen(app)[y][x0 + 1:x0 + bw - 1]
        self.assertIn("› a", inside(self.option_y(ui, 0)))  # where options are drawn
        rule = inside(self.option_y(ui, i))
        self.assertIn("───", rule, rule)
        self.assertNotIn("›", rule)
        self.assertEqual(rule.strip().strip("─"), "", rule)  # a rule and nothing else

    def test_a_click_on_a_menus_divider_does_nothing_and_leaves_it_open(self):
        ran, seen = [], {}

        async def click_divider(pilot):
            ui = pilot.app.ui
            i = next(i for i, o in enumerate(ui.choice_options()) if tuple(o) == tuple(roost.DIVIDER))
            await pilot.click(offset=(ui.choosing["box"][1] + 4, self.option_y(ui, i)))
            await pilot.pause()
            seen["after divider"] = (list(ran), bool(ui.choosing))

        async def click_c(pilot):  # the row under the divider: the clicks land where the rows are
            ui = pilot.app.ui
            i = [o[0] for o in ui.choice_options()].index("c")
            await pilot.click(offset=(ui.choosing["box"][1] + 4, self.option_y(ui, i)))
        app = self.run_app([self.grouped_menu(ran), click_divider, click_c])
        self.assertEqual(seen["after divider"], ([], True))
        self.assertEqual((ran, app.ui.choosing), (["c"], None))

    def test_the_frame_is_the_terminals_own_background(self):
        app = self.run_app([])
        self.assertEqual(app.ui.style(0).bgcolor.name, "default")  # so it's as translucent as the window's edges
        self.assertEqual(app.ui.style(0, card=True).bgcolor.name, roost.CARD)

    def test_notes_list_down_the_left_with_the_note_in_the_card(self):
        roost.create_note("Printer idea", body="first line\nsecond line")
        app = self.run_app([open_view("Notes")])
        lines = self.screen(app)
        self.assertTrue(any("Printer idea" in l and l.index("Printer idea") < 10 for l in lines))
        self.assertTrue(any("Notes  /  Printer idea" in l for l in lines))
        self.assertTrue(any("Tags" in l and "Created" in l for l in lines))  # the facts, in two columns
        self.assertEqual(app.ui.nrows[app.ui.nsel]["name"], "Printer idea")

    def test_the_stats_tab_shows_spend_and_arrows_change_the_range(self):
        import unittest.mock
        from datetime import date
        today = date.today().isoformat()
        data = {"rows": [(today, "Live work", self.sessions[0]["id"], None, 12.5), (today, "Ask", None, None, 0.25)],
                "tokens": {today: [90, 100]}, "unpriced": [], "priced": True}
        seen = {}

        async def look(pilot):
            for _ in range(20):  # the first read runs in the background
                if pilot.app.ui.stats_view():
                    break
                await pilot.pause(0.05)
            pilot.app.redraw()
            await pilot.pause()
            view = pilot.app.query_one(roost.StatsView)
            seen["shown"], seen["region"] = view.display, view.region
            seen["head"] = str(view.query_one(roost.Head).render())
            seen["table"] = str(view.query_one(roost.KindTable).render())
        with unittest.mock.patch.multiple(roost, gather_spend=lambda table=None, now=None: data, monthly_limit=lambda: None):
            app = self.run_app([open_view("Stats"), look, self.keys("right")])
        self.assertEqual(app.ui.TABS[app.ui.tab].name, "Stats")
        self.assertTrue(seen["shown"])
        self.assertTrue(seen["region"].height and seen["region"].bottom <= 28, seen["region"])  # clear of the key bar
        self.assertIn("$12.75 month to date", seen["head"])
        self.assertIn("Total", seen["table"])
        self.assertEqual(app.ui.srange, 3)  # → from Month to date is All time
        self.assertIn("←→ range", self.screen(app)[-1])

        async def hidden(pilot):
            seen["hidden"] = not pilot.app.query_one(roost.StatsPage).display
        self.run_app([hidden])
        self.assertTrue(seen["hidden"])  # only on its own tab

    # Wrapped-up Sessions: a closed Session a project's Handoff covers to its last message.

    def with_wrapped(self, tab, linked, steps=()):
        """roost on tab with project printer-fleet (short enough not to be clipped) listing linked, where
        "Old printer audit" (closed, its last message covered by a Handoff) is wrapped up. linked takes
        "wrapped" and indexes into self.sessions."""
        import unittest.mock
        self.pid = "printer-fleet"
        old = dict(session("Old printer audit", state="closed", kind=None, mins=120), attach=None)
        self.sessions = self.sessions + [old]
        ids = [old["id"] if i == "wrapped" else self.sessions[i]["id"] for i in linked]
        proj = {"id": self.pid, "path": "/nowhere/" + self.pid, "sessions": ids, "summary": "", "status": "active",
                "category": "", "tags": [], "last": time.time(), "closed": False, "readme": True,
                "covered": {old["id"]: old["last"]}}
        with unittest.mock.patch.multiple(roost, read_projects=lambda sessions: [proj], read_config=lambda: {"tab": tab}):
            app = self.run_app(list(steps))
        self.assertTrue(app.ui.wrapped_up(proj, old))  # the fixture really is wrapped up
        side = [l[:roost.SIDEBAR_W] for l in self.screen(app)]  # the detail pane can name the project on another row
        return app, next((l for l in side if self.pid in l), None)

    def test_a_project_with_only_wrapped_up_sessions_has_no_open_marker(self):
        app, line = self.with_wrapped("Projects", ["wrapped"])
        self.assertEqual(app.ui.TABS[app.ui.tab].name, "Projects")
        self.assertNotIn("▸", line)
        self.assertNotIn("▾", line)

    def test_a_project_with_a_running_session_has_no_open_marker_and_the_same_list_row(self):
        """Spec 1: a project with a running Session has no ▸ either, and a wrapped-up Session linked beside
        the running one doesn't change its row in the Projects list."""
        row = lambda lines: next(l[:roost.SIDEBAR_W] for l in lines if self.pid in l[:roost.SIDEBAR_W])
        app, _ = self.with_wrapped("Projects", [1], [self.on_project("printer-fleet")])
        without = self.screen(app)
        self.sessions = self.sessions[:2]
        app, _ = self.with_wrapped("Projects", [1, "wrapped"], [self.on_project("printer-fleet")])
        with_it = self.screen(app)
        self.assertNotIn("▸", row(without))
        self.assertNotIn("▾", row(without))
        self.assertEqual(row(with_it), row(without))

    # Spec 2, 3, 4: the SESSIONS cell's unwrapped count, Show its Sessions, and Dismiss, on a real screen.

    @staticmethod
    def closed(name, mins, **kw):
        return dict(session(name, state="closed", kind=None, mins=mins), attach=None, **kw)

    def fleet(self):
        """Project printer-fleet ("Printer fleet") listing Printer fix (idle), Toner order (closed, no
        Handoff), and Old printer audit (wrapped up). Laptop refresh (waiting) and two decoys, whose
        name, or summary and folder, hold the project's id or label, aren't in it."""
        toner, old = self.closed("Toner order", 30), self.closed("Old printer audit", 120)
        decoys = [self.closed("printer-fleet notes", 40),
                  dict(self.closed("Spare", 50, summary="About the Printer fleet"), cwd="/tmp/printer-fleet")]
        self.sessions = self.sessions + [toner, old] + decoys
        self.pid = "printer-fleet"
        linked = [self.sessions[0], toner, old]
        return self.proj(self.pid, linked, covered=[old], label="Printer fleet"), linked, decoys

    @staticmethod
    def proj(pid, linked, covered=(), label=None):
        p = {"id": pid, "path": "/nowhere/" + pid, "sessions": [s["id"] for s in linked], "summary": "", "status": "active",
             "category": "", "tags": [], "last": time.time(), "closed": False, "readme": True,
             "covered": {s["id"]: s["last"] for s in covered}}
        return dict(p, label=label) if label else p

    def run_with(self, tab, projects, steps, size=(120, 30)):
        """roost on tab with these projects and a temp config.DISMISSED, shared by every roost this test runs."""
        import unittest.mock
        if not hasattr(self, "persisted"):
            tmp = tempfile.TemporaryDirectory()
            self.addCleanup(tmp.cleanup)
            patcher, self.persisted = dismissed_file(Path(tmp.name) / "dismissed.json")
            self.addCleanup(patcher.stop)
        with unittest.mock.patch.multiple(roost, read_projects=lambda sessions: projects, read_config=lambda: {"tab": tab}):
            return self.run_app(list(steps), size=size)

    @staticmethod
    def look(seen, label, get):
        async def step(pilot):
            pilot.app.redraw()
            await pilot.pause()
            seen[label] = get(pilot.app)
        return step

    @staticmethod
    def select(set_it):
        async def step(pilot):
            pilot.app.ui.draw()
            set_it(pilot.app.ui)
            pilot.app.redraw()
        return step

    def on_session(self, name):
        def at(ui):
            names = [s["name"] for s in getattr(ui, "srows", [])]
            assert name in names, f"{name} isn't in the Sessions view's rows {names}, on the {ui.TABS[ui.tab].name} tab"
            ui.sel = names.index(name)
        return self.select(at)

    def on_project(self, pid):
        return self.select(lambda ui: setattr(ui, "psel", next(i for i, r in enumerate(ui.project_rows())
                                                               if r.kind == "project" and r.item["id"] == pid)))

    @staticmethod
    def pick(label):
        """enter on the selected row, then its action labeled label."""
        async def step(pilot):
            await pilot.press("enter")
            await pilot.pause()
            ui = pilot.app.ui
            opts = [l for l, _ in ui.choice_options()] if ui.choosing else []  # as shown: sel indexes these
            assert label in opts, f"{label!r} not in its actions {opts}"
            ui.choosing["sel"] = opts.index(label)
            await pilot.press("enter")
        return step

    @staticmethod
    def line_of(lines, text):
        """The first list line holding text (the panel at the bottom comes after the list)."""
        return next((l for l in lines[:-10] if text in l), None)

    CLOSED_VIEW = ("right",)  # from Running, the view roost opens on with a running Session, to Closed

    def test_show_its_sessions_lists_exactly_the_projects_sessions_until_esc(self):
        """Spec 3 (with 4): Show its Sessions puts the Sessions tab on Running (one of its Sessions is
        running) with project:<id>, listing the project's running Session; → lists its closed ones
        (closed, wrapped up, dismissed) and neither decoy; esc lists every closed Session again. A plain
        search for the id matches name, summary, and folder only."""
        p, linked, decoys = self.fleet()
        seen = {}
        ids = lambda app: sorted(s["id"] for s in app.ui.srows)
        running = lambda ss: sorted(s["id"] for s in ss if s["state"] != "closed")
        closed = lambda ss: sorted(s["id"] for s in ss if s["state"] == "closed")
        self.run_with("Sessions", [p], [
            open_view("Sessions"), self.keys(*self.CLOSED_VIEW), self.on_session("Toner order"), self.pick("Dismiss"),
            open_view("Projects"), self.on_project(self.pid), self.pick("Show its Sessions"),
            self.look(seen, "shown", lambda app: (app.ui.TABS[app.ui.tab].name, roost.VIEWS[app.ui.view], app.ui.query, ids(app))),
            self.keys("right"), self.look(seen, "closed", lambda app: (roost.VIEWS[app.ui.view], ids(app))),
            self.keys("escape"), self.look(seen, "cleared", lambda app: (app.ui.query, ids(app))),
            self.keys("slash", *self.pid, "enter"), self.look(seen, "plain", ids)])
        self.assertEqual(seen["shown"], ("Sessions", "Running", f"project:{self.pid}", running(linked)))
        self.assertEqual(seen["closed"], ("Closed", closed(linked)))
        self.assertEqual(seen["cleared"], ("", closed(self.sessions)))
        self.assertEqual(seen["plain"], sorted(s["id"] for s in decoys))  # the decoys are closed

    def test_dismiss_marks_sorts_and_uncounts_a_session_and_undismiss_takes_it_back(self):
        """Spec 4: a dismissed Session's STATUS says dismissed, and it lists after every other Session in
        its view (Closed, whatever the sort). Undismiss undoes both, and its id leaves config.DISMISSED."""
        p, _, _ = self.fleet()
        seen = {}
        names = lambda app: [s["name"] for s in app.ui.srows]
        status = lambda app: self.line_of(screen_text(app), "Toner order")
        self.run_with("Sessions", [p], [
            open_view("Sessions"), self.keys(*self.CLOSED_VIEW), self.look(seen, "before", names),
            self.on_session("Toner order"), self.pick("Dismiss"),
            self.look(seen, "closed view", names), self.look(seen, "status", status),
            self.select(lambda ui: setattr(ui, "sort", ("NAME", True))), self.look(seen, "by name", names),  # Z to A: Toner order would lead Closed
            self.select(lambda ui: setattr(ui, "sort", ("LAST ACTIVE", True))),
            self.on_session("Toner order"), self.pick("Undismiss"),
            self.look(seen, "after", names), self.look(seen, "status after", status)],
            size=(120, 40))  # the status line takes a row: at 30 rows the Dismissed section's row is cut off
        self.assertLess(seen["before"].index("Toner order"), seen["before"].index("Old printer audit"))  # the fixture
        for view in ("closed view", "by name"):
            self.assertEqual(seen[view][-1], "Toner order", (view, seen[view]))
        self.assertIn("dismissed", seen["status"])
        self.assertEqual(seen["after"], seen["before"])
        self.assertNotIn("dismissed", seen["status after"])
        self.assertNotIn(self.sessions[2]["id"], self.persisted())

    def test_a_dismissed_session_is_still_dismissed_in_a_fresh_roost(self):
        """Spec 4: dismissed persists: a second roost, reading the same config.DISMISSED, draws it dismissed."""
        p, _, _ = self.fleet()
        pristine = [dict(s) for s in self.sessions]  # the second roost gets Sessions the first never touched
        self.run_with("Sessions", [p], [open_view("Sessions"), self.keys(*self.CLOSED_VIEW), self.on_session("Toner order"), self.pick("Dismiss")])
        self.assertIn(self.sessions[2]["id"], self.persisted())
        self.sessions = pristine
        app = self.run_with("Sessions", [p], [open_view("Sessions"), self.keys(*self.CLOSED_VIEW)])
        self.assertIn("dismissed", self.line_of(self.screen(app), "Toner order"))
        self.assertEqual(app.ui.srows[-1]["name"], "Toner order")

    def test_the_sessions_tab_still_lists_a_wrapped_up_session(self):
        app, _ = self.with_wrapped("Sessions", ["wrapped"], [open_view("Sessions"), self.keys(*self.CLOSED_VIEW)])  # it's closed
        self.assertEqual(app.ui.TABS[app.ui.tab].name, "Sessions")
        line = next((l for l in self.screen(app)[:-10] if "Old printer audit" in l), None)
        self.assertIsNotNone(line, "\n".join(self.screen(app)))
        self.assertIn("✓ wrapped up", line)

    # The Sessions views: Running and Closed, a simple history, and Unfiled.

    def views_fixture(self):
        """Two running Sessions (Printer fix, Laptop refresh) and three closed: Printer toner order, Old
        printer audit, and Spare audit (big and untouched for days)."""
        self.sessions = self.sessions + [self.closed("Printer toner order", 30), self.closed("Old printer audit", 60 * 24 * 10),
                                         dict(self.closed("Spare audit", 60 * 24 * 9), tokens=900_000)]

    @staticmethod
    def view_row(app):
        """The Sessions view row's text, the whole width: the Sessions tab has no sidebar."""
        return screen_text(app)[app.ui.view_row_y]

    def test_the_view_row_shows_running_closed_and_unfiled_with_their_counts_then_the_search(self):
        self.views_fixture()
        seen = {}
        self.run_app([open_view("Sessions"), self.look(seen, "row", self.view_row),
                      self.keys("slash", *"printer", "enter"), self.look(seen, "searched", self.view_row)])
        # no project lists any of the five Sessions, and there are no notes: all five are unfiled
        self.assertRegex(seen["row"], r"^\s*Running \(2\)\s+Closed \(3\)\s+Unfiled \(5\)\s*$")
        self.assertRegex(seen["searched"], r"^\s*Running \(1\)\s+Closed \(2\)\s+Unfiled \(\d+\)\s+/ printer\s*$")

    def test_left_and_right_switch_between_running_closed_and_unfiled_wrapping(self):
        self.views_fixture()
        seen = {}
        view = lambda app: roost.VIEWS[app.ui.view]
        keys = ["right", "right", "right", "left", "left", "left"]
        steps = [open_view("Sessions"), self.look(seen, "start", view)]
        for i, k in enumerate(keys):
            steps += [self.keys(k), self.look(seen, i, view)]
        self.run_app(steps)
        self.assertEqual([seen["start"]] + [seen[i] for i in range(len(keys))],
                         ["Running", "Closed", "Unfiled", "Running", "Unfiled", "Closed", "Running"])

    def test_a_click_on_a_view_label_picks_that_view(self):
        self.views_fixture()
        seen = {}
        view = lambda app: roost.VIEWS[app.ui.view]

        def click(label):
            async def step(pilot):
                pilot.app.redraw()
                await pilot.pause()
                y = pilot.app.ui.view_row_y
                x = screen_text(pilot.app)[y].index(f" {label} (") + 3
                await pilot.click(offset=(x, y))
            return step
        self.run_app([open_view("Sessions"), click("Closed"), self.look(seen, "closed", view),
                      click("Unfiled"), self.look(seen, "unfiled", view), click("Running"), self.look(seen, "running", view)])
        self.assertEqual((seen["closed"], seen["unfiled"], seen["running"]), ("Closed", "Unfiled", "Running"))

    # The key bar: every key it names does what it says, on each tab that draws it.

    BAR_TABS = ("Projects", "Sessions", "Notes")

    def run_on_tab(self, name, steps):
        """A fresh roost on tab name, with a project linked to the first Session and two notes."""
        import unittest.mock
        self.pid = "2026-10-printer-fleet"
        proj = {"id": self.pid, "path": "/nowhere/" + self.pid, "sessions": [self.sessions[0]["id"]], "summary": "",
                "status": "active", "category": "", "tags": [], "last": time.time(), "closed": False, "readme": True}
        roost.create_note("Printer idea", body="first line")
        roost.create_note("Laptop idea", body="first line")
        with unittest.mock.patch.multiple(roost, read_projects=lambda sessions: [proj], read_config=lambda: {"tab": name}):
            return self.run_app([open_view(name)] + list(steps))  # roost starts on it, as remembered

    def test_every_key_the_bar_names_does_what_it_says(self):
        sel = {"Projects": "psel", "Sessions": "sel", "Notes": "nsel"}
        for name in self.BAR_TABS:
            seen = {}

            def look(label, get):
                async def step(pilot):
                    seen[label] = get(pilot.app.ui)
                return step

            async def pick_project(pilot):  # enter acts on the selected row: the project
                ui = pilot.app.ui
                ui.psel = next(i for i, r in enumerate(ui.project_rows()) if r.kind == "project" and r.item["id"] == self.pid)
                pilot.app.redraw()

            def actions(ui):
                if name == "Projects":
                    return [l for l, _ in ui.row_actions(ui.prows[ui.psel])]
                if name == "Sessions":
                    return [l for l, _ in ui.session_actions(ui.srows[ui.sel])] if ui.sel < len(ui.srows) else []
                return [l for l, _ in ui.note_actions(ui.nrows[ui.nsel])] if ui.nsel < len(ui.nrows) else []
            at = lambda ui: getattr(ui, sel[name])
            async def catch_exit(pilot):  # what ctrl+q leads to: the app exits (run_test would end it anyway)
                pilot.app.exit = lambda *a, **k: seen.setdefault("exits", []).append(a)
            # Projects: → moves the keys into the selected project's details (its Session is an item there), ← back
            arrows = {"Projects": lambda ui: ui.focus == "pane", "Sessions": lambda ui: ui.view,
                      "Notes": lambda ui: ui.nreading}[name]
            names = [t.name for t in roost.UI.TABS]
            steps = [look("tab", lambda ui: ui.TABS[ui.tab].name), look("rows", lambda ui: len(ui.draw())),
                     look("at", at), self.keys("down"), look("down", at), self.keys("up"), look("up", at)]
            if name == "Projects":
                steps.append(pick_project)
            steps += [look("arrows", arrows), self.keys("right"), look("right", arrows), self.keys("left"), look("left", arrows)]
            if name == "Projects":
                steps.append(pick_project)
            steps += [look("actions", actions), self.keys("enter"),
                      look("menu", lambda ui: ui.choosing and [l for l, _ in ui.choosing["options"]]), self.keys("escape"),
                      self.keys("slash"), look("search", lambda ui: ui.searching), self.keys("escape"),
                      self.keys("ctrl+n"), look("new", lambda ui: ui.choosing and ui.choosing["title"]),
                      look("new options", lambda ui: ui.choosing and " ".join(l for l, _ in ui.choosing["options"])),
                      self.keys("n"), look("note prompt", lambda ui: ui.asking and ui.asking["title"]), self.keys("escape"),
                      self.keys("tab"), look("next tab", lambda ui: ui.TABS[ui.tab].name), self.keys("shift+tab"),
                      look("back", lambda ui: ui.TABS[ui.tab].name),
                      self.keys("question_mark"), look("help", lambda ui: ui.helping), self.keys("escape"),
                      look("help closed", lambda ui: ui.helping), catch_exit, self.keys("ctrl+q")]
            self.run_on_tab(name, steps)
            with self.subTest(tab=name):
                self.assertEqual(seen["tab"], name)
                self.assertGreater(seen["rows"], seen["at"] + 1)  # room to move down one
                self.assertEqual((seen["down"], seen["up"]), (seen["at"] + 1, seen["at"]), "↑↓ move")
                if name == "Sessions":
                    self.assertEqual((seen["right"], seen["left"]), ((seen["arrows"] + 1) % len(roost.VIEWS), seen["arrows"]), "←→ views")
                else:  # Projects: → into the details, ← back to the list · Notes: → reads the note, ← goes back to the list
                    self.assertEqual((seen["arrows"], seen["right"], seen["left"]), (False, True, False), "←→")
                self.assertTrue(seen["actions"])
                self.assertEqual(seen["menu"], seen["actions"], "enter actions")
                self.assertTrue(seen["search"], "/ search")
                self.assertEqual(seen["new"], "New", "ctrl+n new")
                self.assertIn("Note", seen["new options"])
                self.assertEqual(seen["note prompt"], "New note: its title", "ctrl+n then n")
                self.assertEqual((seen["next tab"], seen["back"]), (names[(names.index(name) + 1) % len(names)], name), "tab next tab")
                self.assertEqual((seen["help"], seen["help closed"]), (True, False), "? help")
                self.assertEqual(seen.get("exits"), [(None,)], "ctrl+q quit")

    def test_the_bar_on_every_tab_names_the_bars_keys(self):
        entries = re.split(r" {2,}", roost.BAR_KEYS.strip())
        for name in self.BAR_TABS:
            app = self.run_on_tab(name, [])
            rows = len(app.ui.bar[0]) if app.ui.bar else 1
            drawn = "   ".join(self.screen(app)[-rows:])
            with self.subTest(tab=name):
                self.assertEqual(app.ui.TABS[app.ui.tab].name, name)
                for entry in entries:
                    if entry.startswith("←→"):  # what ←→ does is the tab's own: views, → into details, or → read
                        self.assertIn("→ ", drawn)
                    else:
                        self.assertIn(entry, drawn)
                self.assertIn("tab next tab", drawn)

    def test_the_status_line_above_the_key_bar_says_how_many_sessions_are_running_on_every_tab(self):
        """One line just above the key bar, on every tab: empty on the left, "N running" (Sessions whose
        state isn't closed) on the right, or "nothing running"."""
        import unittest.mock
        data = {"rows": [], "tokens": {}, "unpriced": [], "priced": True}
        roost.create_note("Printer idea", body="first line")
        tabs = [t.name for t in roost.UI.TABS]
        self.assertEqual(tabs, list(TABS))
        everyone = self.sessions + [self.closed("Toner order", 30)]  # idle, waiting, closed: 2 running
        for sessions, want in ((everyone, "2 running"), ([self.closed("Toner order", 30)], "nothing running")):
            for i, name in enumerate(tabs):
                self.sessions = sessions
                with self.subTest(tab=name, want=want), \
                        unittest.mock.patch.multiple(roost, gather_spend=lambda table=None, now=None: data,
                                                     monthly_limit=lambda: None):
                    app = self.run_app([open_view(name)] if i else [])
                    self.assertEqual(app.ui.TABS[app.ui.tab].name, name)
                    self.assertEqual(app.ui.status_line(), ("", want))
                    rows = len(app.ui.bar[0]) if app.ui.bar else 1
                    lines = self.screen(app)
                    self.assertIn("quit", "   ".join(lines[-rows:]))  # the key bar is the bottom rows
                    status = lines[-rows - 1]
                    self.assertTrue(status.endswith(want), status)
                    self.assertRegex(status.strip(), rf"^(● )?{want}$", "the left side is empty for now")

    # Slow work off the UI thread: the idle refresh and the slow actions don't hold up the keys.

    QUICK = 0.3  # what "returns at once" allows; each slow call below takes 1s unless the test lets it finish

    class Slow:
        """A stand-in for a slow call: it waits (up to 1s) until the test lets it finish, then returns
        what result gives for its arguments, or raises error. It counts its calls and says when one has
        started and when one has finished."""
        def __init__(self, result=lambda *a, **k: None, error=None):
            import threading
            self.result, self.error, self.calls = result, error, 0
            self.go, self.started, self.finished = threading.Event(), threading.Event(), threading.Event()

        def __call__(self, *a, **k):
            self.calls += 1
            self.started.set()
            self.go.wait(1.0)
            self.finished.set()
            if self.error:
                raise self.error
            return self.result(*a, **k)

    @staticmethod
    async def until(pilot, done, limit=3.0):
        """Tick and let the app run until done() or limit seconds; returns done()."""
        end = time.monotonic() + limit
        while time.monotonic() < end and not done():
            pilot.app.tick()
            await pilot.pause(0.02)
        return done()

    @staticmethod
    def idle(app):
        """As if no key came for a while and the last refresh was long ago, so the next tick refreshes."""
        app.last_key = app.last_refresh = time.time() - 60

    def refresh_reads(self, slow, steps):
        import unittest.mock
        with unittest.mock.patch.multiple(roost, regather=slow):
            return self.run_app(steps)

    def test_an_idle_refresh_doesnt_hold_up_the_tick_and_shows_what_it_read(self):
        added = session("Badge printer setup", mins=1)
        slow = self.Slow(result=lambda old: [dict(s) for s in old] + [added])
        seen = {}

        async def step(pilot):
            app = pilot.app
            self.idle(app)
            t = time.monotonic()
            app.tick()
            seen["took"] = time.monotonic() - t
            seen["before"] = [s["name"] for s in app.ui.sessions]
            seen["started"] = slow.started.wait(1.0)
            slow.go.set()
            seen["shown"] = await self.until(pilot, lambda: added["id"] in [s["id"] for s in app.ui.sessions])
        self.refresh_reads(slow, [step])
        self.assertLess(seen["took"], self.QUICK, "tick waited for the refresh's read")
        self.assertNotIn("Badge printer setup", seen["before"])
        self.assertTrue(seen["started"], "the idle tick didn't start a refresh")
        self.assertTrue(seen["shown"], "a later tick didn't show the Sessions the refresh read")

    def test_only_one_refresh_read_runs_at_a_time(self):
        slow = self.Slow(result=lambda old: old)
        seen = {}

        async def step(pilot):
            app = pilot.app
            self.idle(app)
            app.tick()
            seen["started"] = slow.started.wait(1.0)
            for _ in range(3):  # idle and due again each time: only the read in flight holds them back
                self.idle(app)
                app.tick()
            seen["in flight"] = not slow.finished.is_set()
            seen["calls"] = slow.calls
            slow.go.set()
        self.refresh_reads(slow, [step])
        self.assertTrue(seen["started"])
        self.assertTrue(seen["in flight"], "the first read had already finished, so this shows nothing")
        self.assertEqual(seen["calls"], 1)

    def test_a_refresh_read_from_before_a_key_isnt_applied(self):
        added = session("Badge printer setup", mins=1)
        slow = self.Slow(result=lambda old: [dict(s) for s in old] + [added])
        seen = {}

        async def step(pilot):
            app = pilot.app
            self.idle(app)
            app.tick()
            seen["started"] = slow.started.wait(1.0)
            app.press("down")  # a key while the read is out: what it read is already old
            slow.go.set()
            seen["finished"] = slow.finished.wait(2.0)
            await self.until(pilot, lambda: False, limit=0.3)  # ticks that would pick the read up
            seen["names"] = [s["name"] for s in app.ui.sessions]
        self.refresh_reads(slow, [step])
        self.assertTrue(seen["started"] and seen["finished"])
        self.assertNotIn("Badge printer setup", seen["names"])

    def slow_action(self, patch, slow, opens, act, during, after):
        """On the Sessions tab (Running, then whatever opens moves it to): opens(pilot) gets the action ready, act(app) does it with
        patch (a sessiondata name) replaced by slow. Returns how long act took, what the screen said
        then, whether during was what it said, and what it said once a later tick said after."""
        import unittest.mock
        seen = {}
        shown = lambda ui: ui.alerting or ui.msg

        async def step(pilot):
            app = pilot.app
            t = time.monotonic()
            act(app)
            seen["took"] = time.monotonic() - t
            seen["during"] = shown(app.ui)
            slow.go.set()
            seen["ended"] = await self.until(pilot, lambda: shown(app.ui).startswith(after))
            seen["after"] = shown(app.ui)
        with unittest.mock.patch.multiple(roost, **{patch: slow}):
            self.run_app([open_view("Sessions")] + opens + [step])
        self.assertLess(seen["took"], self.QUICK, f"the action waited for {patch}")
        self.assertEqual(seen["during"], during)
        self.assertTrue(seen["ended"], f"a later tick said {seen['after']!r}, not {after!r}…")
        return seen

    def picked(self, name, label):
        """Select the Session name and open its actions with label chosen, ready for enter."""
        async def step(pilot):
            app = pilot.app
            app.press("enter")
            ui = app.ui
            opts = [l for l, _ in ui.choice_options()] if ui.choosing else []  # as shown: sel indexes these
            assert label in opts, f"{label!r} not in its actions {opts}"
            ui.choosing["sel"] = opts.index(label)
            app.redraw()
        return [self.on_session(name), step]

    def pin_closed(self, slow):
        self.sessions = self.sessions + [self.closed("Toner order", 30)]
        return self.slow_action("revive_pinned", slow, [self.keys(*self.CLOSED_VIEW)] + self.picked("Toner order", "Restart in the background, pinned"),
                                lambda app: app.press("enter"), "Restarting Toner order in the background…",
                                "Pinned Toner order: " if not slow.error else "Couldn't restart it: ")

    def test_pin_on_a_closed_session_restarts_it_without_holding_up_the_keys(self):
        seen = self.pin_closed(self.Slow())
        self.assertEqual(seen["after"], "Pinned Toner order: running in the background, won't be closed when idle")

    def test_pin_on_a_closed_session_that_fails_says_why(self):
        seen = self.pin_closed(self.Slow(error=RuntimeError("no worker started")))
        self.assertEqual(seen["after"], "Couldn't restart it: no worker started")

    def rename_one(self, slow):
        def act(app):
            app.ui.asking["text"] = "Printer fix 2"
            app.press("enter")
        return self.slow_action("rename_session", slow, self.picked("Printer fix", "Rename") + [self.keys("enter")], act,
                                "Renaming Printer fix…", "Renamed to " if not slow.error else "Couldn't rename: ")

    def test_rename_doesnt_hold_up_the_keys(self):
        seen = self.rename_one(self.Slow())
        self.assertEqual(seen["after"], "Renamed to Printer fix 2")

    def test_a_rename_that_fails_says_why(self):
        seen = self.rename_one(self.Slow(error=RuntimeError("claude stop timed out")))
        self.assertEqual(seen["after"], "Couldn't rename: claude stop timed out")

    def stop_one(self, slow):
        return self.slow_action("stop_session", slow, self.picked("Printer fix", "Stop") + [self.keys("enter")],
                                lambda app: app.press("y"), "Stopping Printer fix…",
                                "Stopped Printer fix" if not slow.error else "Couldn't stop it: ")

    def test_stop_after_its_confirmation_doesnt_hold_up_the_keys(self):
        seen = self.stop_one(self.Slow())
        self.assertEqual(seen["after"], "Stopped Printer fix. It's under Closed")

    def test_a_stop_that_fails_says_why(self):
        seen = self.stop_one(self.Slow(error=RuntimeError("no such job")))
        self.assertEqual(seen["after"], "Couldn't stop it: no such job")

    def assert_wren_below_the_note(self, lines, anchor="Nothing here."):
        """The empty note (or the line anchor names), then every row of the wren's cells on the lines
        below it, in order."""
        note = next((i for i, l in enumerate(lines) if anchor in l), None)
        self.assertIsNotNone(note, f"no {anchor} line:\n" + "\n".join(lines))
        below = lines[note + 1:]
        self.assertTrue(any(b in l for l in below for b in "▀▄"), "no half blocks below the note:\n" + "\n".join(lines))
        art = ["".join(cell[0] for cell in row).strip() for row in roost.wren_rows()]
        art = art[next(i for i, a in enumerate(art) if a):len(art) - next(i for i, a in enumerate(reversed(art)) if a)]
        start = next((i for i in range(len(below)) if art[0] in below[i]), None)
        self.assertIsNotNone(start, "the wren's top row isn't below the note:\n" + "\n".join(lines))
        for k, row in enumerate(art):
            self.assertIn(row, below[start + k], f"wren row {k} isn't drawn under the one above it:\n" + "\n".join(lines))

    def test_an_empty_projects_tab_draws_the_wren_below_its_note(self):
        # no projects (read_projects returns none) and a search that matches nothing: no rows at all,
        # not even + New project, so the Nothing here. note shows
        app = self.run_app([self.keys("/", *"zzqx", "enter")])
        self.assertEqual(app.ui.TABS[app.ui.tab].name, "Projects")
        self.assert_wren_below_the_note(self.screen(app))

    def test_an_empty_sessions_tab_draws_the_wren_below_its_note(self):
        self.sessions = []
        # 120x40: at 120x30 the detail panel and views row leave too little room for the whole wren,
        # and a wren that doesn't fit isn't drawn
        app = self.run_app([open_view("Sessions")], size=(120, 40))
        self.assertEqual(app.ui.TABS[app.ui.tab].name, "Sessions")
        self.assert_wren_below_the_note(self.screen(app))

    def test_a_projects_tab_with_no_projects_draws_the_wren_below_new_project(self):
        app = self.run_app([])  # read_projects returns no projects; no search, so + New project is the only row
        lines = self.screen(app)
        self.assertEqual(app.ui.TABS[app.ui.tab].name, "Projects")
        self.assertFalse(any("Nothing here." in l for l in lines))
        self.assert_wren_below_the_note(lines, anchor="+ New project")

    def test_the_wren_is_drawn_whole_or_not_at_all(self):
        """A wren taller than the room under the note isn't drawn, not even its top rows. A made-up
        30-cell-tall art stands in for the wren, taller than a minimum-size window: a tall window shows
        all of it (so the stand-in reaches the screen), a window of the minimum size has no room and shows
        no half block."""
        import unittest.mock
        wren = importlib.import_module("roost.core.wren")
        tall = ["......" + "5a3a1e" * 4 + "......"] * 60  # 60 pixel rows: 30 cell rows of ▀, more than MIN_SIZE's rows
        cases = [("Projects", [], []), ("Projects", [], [self.keys("/", *"zzqx", "enter")]),
                 ("Sessions", [], [open_view("Sessions")])]
        for tab, sessions, steps in cases:
            with self.subTest(tab=tab, search=bool(steps) and tab == "Projects"), \
                    unittest.mock.patch.object(wren, "WREN", tall):
                self.sessions = sessions
                anchor = "Nothing here." if steps else "+ New project"
                big = self.run_app(steps, size=(120, 60))
                self.assertEqual(big.ui.TABS[big.ui.tab].name, tab)
                self.assert_wren_below_the_note(self.screen(big), anchor=anchor)
                small = self.run_app(steps, size=roost.MIN_SIZE)
                lines = self.screen(small)
                self.assertTrue(any(anchor in l for l in lines), "\n".join(lines))
                self.assertFalse(any(b in l for l in lines for b in "▀▄"),
                                 "a clipped wren in a short window:\n" + "\n".join(lines))

    def test_a_projects_tab_with_rows_has_no_wren(self):
        import unittest.mock
        pid = "2026-10-printer-fleet"
        proj = {"id": pid, "path": "/nowhere/" + pid, "sessions": [], "summary": "", "status": "active",
                "category": "", "tags": [], "last": time.time(), "closed": False, "readme": True}
        with unittest.mock.patch.object(roost, "read_projects", lambda sessions: [proj]):
            app = self.run_app([])
        lines = self.screen(app)
        self.assertEqual(app.ui.TABS[app.ui.tab].name, "Projects")
        self.assertTrue(any(pid in l for l in lines), "\n".join(lines))
        self.assertFalse(any("Nothing here." in l for l in lines))
        self.assertFalse(any(b in l for l in lines for b in "▀▄"), "\n".join(lines))

    def test_a_sessions_tab_with_rows_has_no_wren(self):
        app = self.run_app([open_view("Sessions")])
        lines = self.screen(app)
        self.assertEqual(app.ui.TABS[app.ui.tab].name, "Sessions")
        self.assertTrue(any("Printer fix" in l for l in lines), "\n".join(lines))
        self.assertFalse(any("Nothing here." in l for l in lines))
        self.assertFalse(any(b in l for l in lines for b in "▀▄"), "\n".join(lines))


@unittest.skipUnless(roost.App, "Textual isn't installed here: run the tests with ~/.roost/venv/bin/python")
class TopTabs(unittest.TestCase):
    """The top bar's tabs: Agent Roost, then Projects, Sessions, Notes, and Stats, the current one green and
    bold. Tab and shift+tab move to the next and previous tab, wrapping, wherever the keys are; a click on a
    label opens its tab; roost starts on the tab it was on. Only the Projects tab has a list at the left
    (SIDEBAR_W wide): Sessions, Notes, and Stats take the whole window."""
    setUp = TextualScreens.setUp
    run_app = TextualScreens.run_app
    keys = staticmethod(TextualScreens.keys)
    select = staticmethod(TextualScreens.select)
    PID = "printer-fleet"
    GREEN = importlib.import_module("roost.app.widgets").GREEN.lower()
    YELLOW = importlib.import_module("roost.app.widgets").YELLOW.lower()

    def start(self, steps, size=(120, 30), tab=None):
        """roost with project printer-fleet (worked on now, listing Printer fix) and two notes, started on tab
        as remembered (none: no setting). What it saves as the start tab goes in self.saved."""
        import unittest.mock
        proj = TextualScreens.proj(self.PID, [self.sessions[0]])
        roost.create_note("Printer idea", body="first line")
        roost.create_note("Laptop idea", body="first line")
        self.saved = []
        with unittest.mock.patch.multiple(roost, read_projects=lambda sessions: [proj],
                                          read_config=lambda: {"tab": tab} if tab else {},
                                          save_config=lambda **kw: self.saved.append(kw)):
            return self.run_app(list(steps), size=size)

    @staticmethod
    def shown(app):
        return app.ui.TABS[app.ui.tab].name

    def look(self, seen, label, get=None):
        """A step that keeps the tab shown, or what get(app) says, under label."""
        async def step(pilot):
            pilot.app.redraw()
            await pilot.pause()
            seen[label] = get(pilot.app) if get else self.shown(pilot.app)
        return step

    @staticmethod
    def label_x(bar, name):
        """The column of the tab label name in the top bar: its first match after Agent Roost."""
        after = bar.index("Agent Roost") + len("Agent Roost")
        m = re.compile(rf"\b{name}\b").search(bar, after)
        return m.start() if m else None

    # 1. The top bar

    def test_the_top_bar_reads_agent_roost_then_the_tabs_with_the_current_one_green_and_bold(self):
        for name in TABS:
            seen = {}

            async def bar(pilot):
                lines = screen_text(pilot.app)
                seen["bar"] = lines[0]
                seen["styles"] = {n: style_at(pilot.app, 0, self.label_x(lines[0], n)) for n in TABS
                                  if self.label_x(lines[0], n) is not None}
                seen["corner"] = pilot.app.ui.corner
            app = self.start([open_view(name), bar])
            with self.subTest(tab=name):
                self.assertEqual(self.shown(app), name)
                self.assertRegex(seen["bar"], r"^\s*Agent Roost\s+Projects\s+Sessions\s+Notes\s+Stats(\s|$)")
                self.assertNotIn("\U0001f413", seen["bar"])
                self.assertEqual(set(seen["styles"]), set(TABS), seen["bar"])
                for n, st in seen["styles"].items():
                    on = color_of(st) == self.GREEN and bool(st.bold)
                    self.assertEqual(on, n == name, f"{n} on the {name} tab: {st}")
                if name == "Projects":  # the projects folder stays at the top bar's right
                    self.assertTrue(seen["corner"] and seen["corner"][0], seen["corner"])
                    self.assertTrue(seen["bar"].rstrip().endswith(seen["corner"][0]), seen["bar"])

    # 2. Moving between the tabs

    def test_tab_and_shift_tab_move_to_the_next_and_previous_tab_wrapping(self):
        seen, keys = {}, ["tab"] * 4 + ["shift+tab"] * 4
        steps = [self.look(seen, "start")]
        for i, k in enumerate(keys):
            steps += [self.keys(k), self.look(seen, i)]
        app = self.start(steps)
        self.assertEqual([seen["start"]] + [seen[i] for i in range(len(keys))],
                         ["Projects", "Sessions", "Notes", "Stats", "Projects", "Stats", "Notes", "Sessions", "Projects"])
        self.assertIsNone(app.ui.choosing)

    def test_tab_is_the_next_tab_with_the_keys_in_the_projects_details_or_reading_a_note(self):
        seen = {}
        on_project = TextualScreens.on_project(self, self.PID)  # it lists Printer fix: an item in its details
        self.start([on_project, self.keys("right"), self.look(seen, "details", lambda app: (self.shown(app), app.ui.focus)),
                    self.keys("tab"), self.look(seen, "tab"), self.keys("shift+tab"), self.look(seen, "back")])
        self.assertEqual(seen["details"], ("Projects", "pane"), "the fixture: → moved the keys into the details")
        self.assertEqual((seen["tab"], seen["back"]), ("Sessions", "Projects"))
        seen = {}
        self.start([open_view("Notes"), self.keys("right"), self.look(seen, "reading", lambda app: app.ui.nreading),
                    self.keys("tab"), self.look(seen, "tab")])
        self.assertTrue(seen["reading"], "the fixture: → reads the note")
        self.assertEqual(seen["tab"], "Stats")

    def test_a_click_on_a_tab_label_opens_its_tab(self):
        """The labels sit after the name, wherever its length puts them, and a click where a label is drawn
        opens that tab: the hit areas follow the labels."""
        seen, order = [], list(reversed(TABS))  # every tab, from the last

        async def click_each(pilot):
            for name in order:
                bar = screen_text(pilot.app)[0]  # no wide characters, so a string index is a column
                first = self.label_x(bar, name)
                assert first is not None, f"no {name} label in the top bar: {bar!r}"
                for x in (first, first + len(name) - 1):  # the label's first and last letters
                    await pilot.click(offset=(x, 0))
                    await pilot.pause()
                    seen.append((name, self.shown(pilot.app)))
                    await pilot.press("tab")  # move off it, so the next click has to switch
                    await pilot.pause()
        self.start([click_each])
        self.assertEqual(seen, [(name, name) for name in order for _ in range(2)])

    # 3. The remembered tab

    def test_roost_starts_on_the_tab_it_was_on_when_it_last_quit(self):
        for name in TABS:
            with self.subTest(tab=name):
                self.assertEqual(self.shown(self.start([], tab=name)), name)

    def test_the_tab_shown_is_remembered(self):
        seen = {}
        last = lambda app: next((kw["tab"] for kw in reversed(self.saved) if "tab" in kw), None)
        self.start([self.keys("tab"), self.look(seen, "sessions", last), self.keys("tab", "tab"), self.look(seen, "stats", last),
                    self.keys("tab"), self.look(seen, "projects", last)])
        self.assertEqual((seen["sessions"], seen["stats"], seen["projects"]), ("Sessions", "Stats", "Projects"))

    def test_roost_starts_on_projects_with_the_first_project_selected_and_the_keys_in_the_list(self):
        for tab in ("Projects", None):
            seen = {}
            app = self.start([self.look(seen, "row", lambda app: app.ui.project_rows()[app.ui.psel])], tab=tab)
            row = seen["row"]
            with self.subTest(tab=tab):
                self.assertEqual(self.shown(app), "Projects")
                self.assertEqual((row.kind, row.item["id"]), ("project", self.PID))
                self.assertEqual(app.ui.focus, "side")

    # 4. No sidebar on Sessions, Notes, and Stats

    def test_two_blank_rows_separate_the_top_bar_from_the_content_on_every_tab(self):
        """Rows 1 and 2 are blank on every tab and the content starts on row 3: on Projects the list's first
        row and the details' name line, on Sessions the views row, on Notes and Stats their first row."""
        seen, W = {}, roost.SIDEBAR_W

        def grab(name):
            async def step(pilot):
                pilot.app.redraw()
                await pilot.pause()
                seen[name] = (self.shown(pilot.app), screen_text(pilot.app), bold_text(pilot.app))
            return step
        steps = [grab("Projects")]
        for name in TABS[1:]:
            steps += [open_view(name), grab(name)]
        self.start(steps, size=(120, 40))
        blank = lambda l: not l.strip(" │┃▏▕")
        for name in TABS:
            shown, lines, bold = seen[name]
            with self.subTest(tab=name):
                self.assertEqual(shown, name)
                first = next((y for y in range(1, len(lines)) if not blank(lines[y])), None)
                self.assertEqual(first, 3, "two blank rows under the top bar, then the content:\n" + "\n".join(lines[:6]))
                if name == "Projects":
                    side = next((y for y in range(1, len(lines)) if not blank(lines[y][:W])), None)
                    self.assertEqual(side, 3, "the list's first row:\n" + "\n".join(lines[:6]))
                    nm = min(((y, t) for y, x, t in bold if x >= W and y > 0), default=None)
                    self.assertEqual(nm and nm[0], 3, ("the details' name line", nm))
                if name == "Sessions":
                    self.assertRegex(lines[3], r"^\s*Running \(\d+\)\s+Closed \(\d+\)\s+Unfiled \(\d+\)", "the views row")

    def test_sessions_notes_and_stats_take_the_whole_window_with_no_project_list(self):
        widget = {"Sessions": [roost.SessionList], "Notes": [roost.NoteList, roost.NoteCard], "Stats": [roost.StatsView]}
        for name, kinds in widget.items():
            seen = {}
            self.start([open_view(name), self.look(seen, "x", lambda app: (screen_text(app), [app.query_one(k).region for k in kinds]))],
                       size=(120, 40))
            lines, boxes = seen["x"]
            with self.subTest(tab=name):
                self.assertFalse([l for l in lines if "+ New project" in l], "\n".join(lines))
                self.assertLess(boxes[0].x, 4, f"{name} starts at the window's left, not right of a list: {boxes[0]}")
                self.assertGreaterEqual(boxes[-1].right, 116, f"{name} runs to the window's right: {boxes[-1]}")

    def test_no_entries_are_drawn_under_the_projects(self):
        seen = {}
        self.start([self.look(seen, "lines", screen_text)], size=(120, 40))
        side = [l[:roost.SIDEBAR_W].strip(" ▌›│┃▏▕") for l in seen["lines"][1:-2]]
        new = next((i for i, l in enumerate(side) if "+ New project" in l), None)
        self.assertIsNotNone(new, "\n".join(side))
        for entry in ("Unfiled", "Sessions", "Notes", "Stats"):
            self.assertFalse([l for l in side[new + 1:] if re.fullmatch(rf"{entry}( +\d+)?", l)], (entry, side))

    def test_a_click_on_a_session_row_works_at_its_own_columns(self):
        seen = {}
        self.start([open_view("Sessions"), self.look(seen, "lines", screen_text)])
        y = next((i for i, l in enumerate(seen["lines"][:-10]) if "Laptop refresh" in l), None)
        self.assertIsNotNone(y, "\n".join(seen["lines"]))
        for x in (6, 20):  # in the columns a list at the left took, and further right
            seen = {}
            app = self.start([open_view("Sessions"), lambda pilot, x=x: pilot.click(offset=(x, y)), self.keys("enter"),
                              self.look(seen, "menu", lambda app: app.ui.choosing and app.ui.choosing["title"])])
            with self.subTest(x=x):
                self.assertEqual(seen["menu"], "Laptop refresh")
                self.assertEqual(self.shown(app), "Sessions")

    def test_at_120_by_40_the_sessions_list_has_its_whole_header_across_the_window(self):
        seen = {}
        self.start([open_view("Sessions"), self.look(seen, "list", lambda app: app.query_one(roost.SessionList).region),
                    self.look(seen, "lines", screen_text)], size=(120, 40))
        box, lines = seen["list"], seen["lines"]
        self.assertLess(box.x, 4, box)
        self.assertGreater(box.width, 120 - roost.SIDEBAR_W, box)
        head = next(l for l in lines if "NAME" in l)
        for title in ("NAME", "STATUS", "LAST ACTIVE", "CONTEXT", "PROJECT"):
            self.assertIn(title, head, f"{title} is cut off: {head!r}")

    def test_the_notes_card_is_beside_its_list_at_120_columns(self):
        seen = {}
        self.start([open_view("Notes"), self.look(seen, "boxes", lambda app: (app.query_one(roost.NoteList).region,
                                                                             app.query_one(roost.NoteCard).region))],
                   size=(120, 40))
        lst, card = seen["boxes"]
        self.assertLess(lst.x, 4, lst)
        self.assertGreaterEqual(card.x, lst.right, (lst, card))
        self.assertLess(card.y, lst.bottom, (lst, card))
        self.assertLessEqual(card.right, 120, card)

    # 5. The status bar's running count

    def test_a_click_on_the_running_count_opens_the_sessions_tab_on_running(self):
        seen = {}

        async def click_count(pilot):
            pilot.app.ui.view = CLOSED  # somewhere else, so the click has to put it on Running
            pilot.app.redraw()
            await pilot.pause()
            lines = screen_text(pilot.app)
            y = max(i for i, l in enumerate(lines) if l.rstrip().endswith("2 running"))
            seen["status row"] = y
            await pilot.click(offset=(lines[y].rindex("2 running") + 3, y))
        app = self.start([click_count])
        self.assertGreater(seen["status row"], 20, "the count is on the status bar, at the bottom")
        self.assertEqual((self.shown(app), app.ui.view), ("Sessions", RUNNING))

    def test_the_running_count_has_a_green_dot_before_it(self):
        seen = {}

        async def look(pilot):
            lines = screen_text(pilot.app)
            y = max(i for i, l in enumerate(lines) if l.rstrip().endswith("2 running"))
            seen["row"], seen["dot"] = lines[y], None
            m = re.search(r"(●)\s+2 running$", lines[y].rstrip())
            if m:
                seen["dot"] = style_at(pilot.app, y, m.start(1))
        self.start([look])
        self.assertRegex(seen["row"].rstrip(), r"●\s+2 running$")
        self.assertEqual(color_of(seen["dot"]), self.GREEN)

    # 6. The keys go to the tab shown

    def test_the_keys_go_to_each_tabs_view(self):
        seen = {}
        title = lambda app: app.ui.choosing and app.ui.choosing["title"]
        self.start([open_view("Sessions"), self.look(seen, "at", lambda app: (app.ui.sel, app.ui.view)),
                    self.keys("down"), self.look(seen, "down", lambda app: app.ui.sel),
                    self.keys("enter"), self.look(seen, "menu", lambda app: (title(app), app.ui.srows[app.ui.sel]["name"])),
                    self.keys("escape"), self.keys("right"), self.look(seen, "right", lambda app: app.ui.view)])
        sel, view = seen["at"]
        self.assertEqual(seen["down"], sel + 1, "↓ moves in the Sessions list")
        self.assertEqual(seen["right"], (view + 1) % len(roost.VIEWS), "→ switches Sessions' views")
        self.assertEqual(seen["menu"][0], seen["menu"][1], "enter opens the selected Session's menu")
        seen = {}
        self.start([open_view("Notes"), self.look(seen, "at", lambda app: app.ui.nsel),
                    self.keys("down"), self.look(seen, "down", lambda app: app.ui.nsel),
                    self.keys("right"), self.look(seen, "reading", lambda app: app.ui.nreading), self.keys("left"),
                    self.keys("enter"), self.look(seen, "menu", lambda app: (title(app), app.ui.nrows[app.ui.nsel]["name"]))])
        self.assertEqual(seen["down"], seen["at"] + 1, "↓ moves in the Notes list")
        self.assertTrue(seen["reading"], "→ reads the note")
        self.assertEqual(seen["menu"][0], seen["menu"][1], "enter opens the selected note's menu")
        seen = {}
        self.start([open_view("Stats"), self.look(seen, "at", lambda app: app.ui.srange), self.keys("right"),
                    self.look(seen, "right", lambda app: app.ui.srange)])
        self.assertEqual(seen["right"], (seen["at"] + 1) % len(roost.statsdata.RANGES), "→ switches Stats' range")

    def test_esc_leaves_a_note_or_clears_a_search_and_stays_on_the_tab(self):
        seen = {}
        reading = lambda app: (self.shown(app), app.ui.nreading)
        self.start([open_view("Notes"), self.keys("right"), self.look(seen, "reading", reading),
                    self.keys("escape"), self.look(seen, "esc", reading), self.keys("escape"), self.look(seen, "esc esc", reading)])
        self.assertEqual((seen["reading"], seen["esc"], seen["esc esc"]), (("Notes", True), ("Notes", False), ("Notes", False)))
        seen = {}
        search = lambda app: (self.shown(app), app.ui.query)
        self.start([open_view("Sessions"), self.keys("slash", "x", "enter"), self.look(seen, "search", search),
                    self.keys("escape"), self.look(seen, "esc", search), self.keys("escape"), self.look(seen, "esc esc", search)])
        self.assertEqual((seen["search"], seen["esc"], seen["esc esc"]), (("Sessions", "x"), ("Sessions", ""), ("Sessions", "")))

    def test_help_shows_the_current_tabs_keys(self):
        tabs = {t.name: t for t in roost.UI.TABS}
        for name in TABS:
            seen = {}
            app = self.start([open_view(name), self.keys("question_mark"), self.look(seen, "help", lambda app: app.ui.help_rows())])
            with self.subTest(tab=name):
                self.assertTrue(app.ui.helping)
                self.assertEqual(seen["help"][-len(tabs[name].help):], tabs[name].help)
                self.assertTrue(any(f"Keys, then {name}" in l for l in self.screen(app)), "\n".join(self.screen(app)))

    screen = TextualScreens.screen


if __name__ == "__main__":
    unittest.main()
