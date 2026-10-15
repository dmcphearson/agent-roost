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
                     save_config=lambda **kw: None, NOTES=Path(tmp.name) / "notes")
        patcher = unittest.mock.patch.multiple(roost, **stubs)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.sessions = [session("Printer fix", mins=5, tokens=41000), session("Laptop refresh", state="waiting")]

    def run_app(self, steps):
        """Run the app at 120×30, do each step with the pilot, and return the app."""
        import asyncio
        app = roost.RoostApp(self.sessions, "")

        async def go():
            async with app.run_test(size=(120, 30)) as pilot:
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
            await pilot.press("tab")
        lines = self.screen(self.run_app([start]))
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
            app = self.run_app([self.keys("tab")])
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
        self.assertTrue(lines[0].startswith(" 🐓 roost   Projects   Sessions   Notes"))
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
        tabs = [roost.UI.TABS[0]._replace(keys=boom)] + roost.UI.TABS[1:]  # the Projects tab, the first one shown
        with unittest.mock.patch.object(roost.UI, "TABS", tabs), unittest.mock.patch.object(roost, "log"):
            app = self.run_app([self.keys("j")])
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

    def test_a_click_on_a_tab_name_switches_and_the_wheel_moves(self):
        wheel = type("Wheel", (), {"x": 10, "y": 6, "stop": lambda self: None})()

        async def scroll(pilot):
            pilot.app.query_one(roost.SessionList).on_mouse_scroll_down(wheel)
        app = self.run_app([lambda pilot: pilot.click(offset=(24, 0)), scroll])
        self.assertEqual((app.ui.TABS[app.ui.tab].name, app.ui.sel), ("Sessions", 1))

    def test_a_triple_click_acts_once(self):
        import unittest.mock
        ran = []
        with unittest.mock.patch.object(roost.UI, "session_actions", lambda ui, s: [("Open", lambda: ran.append(s["name"]))]):
            self.run_app([self.keys("tab"), lambda pilot: pilot.click(offset=(10, 6), times=3)])
        self.assertEqual(len(ran), 1)

    def test_a_double_or_triple_click_selects_no_text(self):
        """Clicks act on rows; text is selected only by dragging over it, for cmd+c. The pilot's own
        clicks don't name the widget under the mouse, which Textual's select-all needs, so these do."""
        import unittest.mock
        from textual import events
        seen = []

        async def clicks(pilot):
            w, _ = pilot.app.get_widget_at(10, 6)
            for chain in (2, 3):
                pilot.app.screen.selections = {}
                w.post_message(events.Click(w, 10, 6, 0, 0, 1, False, False, False, screen_x=10, screen_y=6, chain=chain))
                await pilot.pause()
                seen.append(dict(pilot.app.screen.selections))
        with unittest.mock.patch.object(roost.UI, "session_actions", lambda ui, s: [("Open", lambda: None)]):
            self.run_app([self.keys("tab"), clicks])
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
        app = self.run_app([self.keys("tab"), lambda pilot: pilot.click(offset=(10, 6), button=3)])
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
            app = self.run_app([self.keys("tab")])
        lines = self.screen(app)
        edge = next(i for i, l in enumerate(lines) if "⚠ deleted in 6 days" in l)
        self.assertTrue(lines[edge].lstrip().startswith("─"))  # the rule over the panel
        self.assertNotIn("deleted", lines[edge + 1])  # the facts line has its own row

    def test_a_prompt_on_the_bottom_row_is_a_textual_input(self):
        async def rename(pilot):
            pilot.app.ui.rename(self.sessions[0])
            pilot.app.redraw()
        app = self.run_app([self.keys("tab"), rename, self.keys("backspace", "backspace", "!")])
        self.assertEqual(app.ui.asking["text"], "Printer f!")
        app = self.run_app([self.keys("tab"), rename, self.keys("escape")])
        self.assertEqual((app.ui.asking, app.ui.msg), (None, "Cancelled"))

    def test_a_small_window_says_how_big_to_make_it_and_ignores_keys(self):
        import asyncio
        app = roost.RoostApp(self.sessions, "")

        async def go():
            async with app.run_test(size=(90, 24)) as pilot:
                await pilot.pause()
                await pilot.press("tab")
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
        app = self.run_app([self.keys("tab", "tab", "ctrl+n", "N"), look])
        p = seen["prompt"]
        self.assertTrue(p.height and p.bottom <= 30, p)  # the title box is on screen, not under the reader
        app = self.run_app([self.keys("tab", "tab", "enter")])
        y0, x0, bw, bh = app.ui.choosing["box"]
        title = self.screen(app)[y0 + 1][x0 + 3:x0 + 3 + len("Printer idea")]
        self.assertEqual(title, "Printer idea")  # nothing of the card is drawn over the menu

    def test_the_frame_is_the_terminals_own_background(self):
        app = self.run_app([])
        self.assertEqual(app.ui.style(0).bgcolor.name, "default")  # so it's as translucent as the window's edges
        self.assertEqual(app.ui.style(0, card=True).bgcolor.name, roost.CARD)

    def test_notes_list_down_the_left_with_the_note_in_the_card(self):
        roost.create_note("Printer idea", body="first line\nsecond line")
        app = self.run_app([self.keys("tab", "tab")])
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
            app = self.run_app([self.keys("tab", "tab", "tab"), look, self.keys("right")])
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
        return app, next((l for l in self.screen(app) if self.pid in l), None)

    def test_a_project_with_only_wrapped_up_sessions_has_no_open_marker(self):
        app, line = self.with_wrapped("Projects", ["wrapped"])
        self.assertEqual(app.ui.TABS[app.ui.tab].name, "Projects")
        self.assertNotIn("▸", line)
        self.assertNotIn("▾", line)

    def test_a_project_with_a_running_session_has_no_marker_and_the_same_sessions_cell(self):
        """Spec 1 and 2: a project with a running Session has no ▸ either, and a wrapped-up Session linked
        beside the running one doesn't change its line (it isn't counted as unwrapped)."""
        _, without = self.with_wrapped("Projects", [1])
        self.sessions = self.sessions[:2]
        _, with_it = self.with_wrapped("Projects", [1, "wrapped"])
        self.assertNotIn("▸", without)
        self.assertNotIn("▾", without)
        self.assertIn("◆1", without)
        self.assertEqual(with_it, without)

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

    def run_with(self, tab, projects, steps):
        """roost on tab with these projects and a temp config.DISMISSED, shared by every roost this test runs."""
        import unittest.mock
        if not hasattr(self, "persisted"):
            tmp = tempfile.TemporaryDirectory()
            self.addCleanup(tmp.cleanup)
            patcher, self.persisted = dismissed_file(Path(tmp.name) / "dismissed.json")
            self.addCleanup(patcher.stop)
        with unittest.mock.patch.multiple(roost, read_projects=lambda sessions: projects, read_config=lambda: {"tab": tab}):
            return self.run_app(list(steps))

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
        return self.select(lambda ui: setattr(ui, "sel", [s["name"] for s in ui.srows].index(name)))

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
            opts = [l for l, _ in ui.choosing["options"]] if ui.choosing else []
            assert label in opts, f"{label!r} not in its actions {opts}"
            ui.choosing["sel"] = opts.index(label)
            await pilot.press("enter")
        return step

    @staticmethod
    def line_of(lines, text):
        """The first list line holding text (the panel at the bottom comes after the list)."""
        return next((l for l in lines[:-10] if text in l), None)

    ALL_VIEW = ("right",) * 4  # from Running, the view roost opens on with a running Session, to All

    def test_the_sessions_cell_counts_unwrapped_sessions_after_the_running_ones(self):
        """Spec 2: the running marks, then "N unwrapped" for linked closed Sessions no Handoff covers, on a
        second line under the marks; with only unwrapped ones, "N unwrapped"; with only wrapped-up ones,
        nothing. Spec 1: no ▸."""
        p, _, _ = self.fleet()
        desks = [self.closed("Desk order", 35), self.closed("Desk survey", 45)]
        vpn = self.closed("VPN audit", 150)
        self.sessions = self.sessions + desks + [vpn]
        projects = [p, self.proj("bravo-desks", desks), self.proj("charlie-vpn", [vpn], covered=[vpn])]
        lines = self.screen(self.run_with("Projects", projects, []))
        both, unwrapped, wrapped = (self.line_of(lines, pid) for pid in ("Printer fleet", "bravo-desks", "charlie-vpn"))
        i = lines.index(both)
        self.assertIn("○1", both)
        self.assertNotIn("unwrapped", both)
        self.assertEqual(lines[i + 1].strip(), "1 unwrapped")
        self.assertEqual(lines[i + 1].index("1 unwrapped"), both.index("○1"))
        self.assertIn("2 unwrapped", unwrapped)
        self.assertFalse(set("○◆●·") & set(unwrapped.split("bravo-desks", 1)[1]), unwrapped)
        self.assertNotIn("unwrapped", wrapped)
        self.assertFalse(set("○◆●·") & set(wrapped.split("charlie-vpn", 1)[1]), wrapped)
        for line in (both, unwrapped, wrapped):
            self.assertFalse(set("▸▾") & set(line), line)

    def test_show_its_sessions_lists_exactly_the_projects_sessions_until_esc(self):
        """Spec 3 (with 4): Show its Sessions puts the Sessions tab on All with project:<id>, listing
        the project's Sessions (running, closed, wrapped up, dismissed) and neither decoy; esc lists
        every Session again. A plain search for the id matches name, summary, and folder only."""
        p, linked, decoys = self.fleet()
        seen = {}
        ids = lambda app: sorted(s["id"] for s in app.ui.srows)
        self.run_with("Sessions", [p], [
            self.keys(*self.ALL_VIEW), self.on_session("Toner order"), self.pick("Dismiss"),
            self.keys("shift+tab"), self.on_project(self.pid), self.pick("Show its Sessions"),
            self.look(seen, "shown", lambda app: (app.ui.TABS[app.ui.tab].name, app.ui.view, app.ui.query, ids(app))),
            self.keys("escape"), self.look(seen, "cleared", lambda app: (app.ui.query, ids(app))),
            self.keys("slash", *self.pid, "enter"), self.look(seen, "plain", ids)])
        self.assertEqual(seen["shown"], ("Sessions", ALL, f"project:{self.pid}", sorted(s["id"] for s in linked)))
        self.assertEqual(seen["cleared"], ("", sorted(s["id"] for s in self.sessions)))
        self.assertEqual(seen["plain"], sorted(s["id"] for s in decoys))

    def test_dismiss_marks_sorts_and_uncounts_a_session_and_undismiss_takes_it_back(self):
        """Spec 4: a dismissed Session's STATUS says dismissed, it lists after every other Session in
        its view (All or Closed, whatever the sort), and its project stops counting it as unwrapped.
        Undismiss undoes all three, and its id leaves config.DISMISSED."""
        p, _, _ = self.fleet()
        seen = {}
        names = lambda app: [s["name"] for s in app.ui.srows]
        status = lambda app: self.line_of(screen_text(app), "Toner order")

        def project(app):  # the project's line and the line under it, where "N unwrapped" goes
            lines = screen_text(app)
            i = lines.index(self.line_of(lines, "Printer fleet"))
            return lines[i] + "\n" + lines[i + 1]
        self.run_with("Sessions", [p], [
            self.keys(*self.ALL_VIEW), self.look(seen, "before", names),
            self.on_session("Toner order"), self.pick("Dismiss"),
            self.look(seen, "all", names), self.look(seen, "status", status),
            self.keys("left", "left"), self.look(seen, "closed view", names),
            self.select(lambda ui: setattr(ui, "sort", ("NAME", True))), self.look(seen, "by name", names),  # Z to A: Toner order would lead Closed
            self.select(lambda ui: setattr(ui, "sort", ("LAST ACTIVE", True))), self.keys("right", "right"),
            self.keys("shift+tab"), self.look(seen, "project", project), self.keys("tab"),
            self.on_session("Toner order"), self.pick("Undismiss"),
            self.look(seen, "after", names), self.look(seen, "status after", status),
            self.keys("shift+tab"), self.look(seen, "project after", project)])
        self.assertLess(seen["before"].index("Toner order"), seen["before"].index("Old printer audit"))  # the fixture
        for view in ("all", "closed view", "by name"):
            self.assertEqual(seen[view][-1], "Toner order", (view, seen[view]))
        self.assertIn("dismissed", seen["status"])
        self.assertIn("1 unwrapped", seen["project after"])
        self.assertNotIn("unwrapped", seen["project"])
        self.assertEqual(seen["after"], seen["before"])
        self.assertNotIn("dismissed", seen["status after"])
        self.assertNotIn(self.sessions[2]["id"], self.persisted())

    def test_a_dismissed_session_is_still_dismissed_in_a_fresh_roost(self):
        """Spec 4: dismissed persists: a second roost, reading the same config.DISMISSED, draws it dismissed."""
        p, _, _ = self.fleet()
        pristine = [dict(s) for s in self.sessions]  # the second roost gets Sessions the first never touched
        self.run_with("Sessions", [p], [self.keys(*self.ALL_VIEW), self.on_session("Toner order"), self.pick("Dismiss")])
        self.assertIn(self.sessions[2]["id"], self.persisted())
        self.sessions = pristine
        app = self.run_with("Sessions", [p], [self.keys(*self.ALL_VIEW)])
        self.assertIn("dismissed", self.line_of(self.screen(app), "Toner order"))
        self.assertEqual(app.ui.srows[-1]["name"], "Toner order")

    def test_the_sessions_tab_still_lists_a_wrapped_up_session(self):
        app, _ = self.with_wrapped("Sessions", ["wrapped"], [self.keys("right", "right", "right", "right")])  # the All view
        self.assertEqual(app.ui.TABS[app.ui.tab].name, "Sessions")
        line = next(l for l in self.screen(app)[:-10] if "Old printer audit" in l)
        self.assertIn("✓ wrapped up", line)

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
            return self.run_app(steps)

    def test_every_key_the_bar_names_does_what_it_says(self):
        sel = {"Projects": "psel", "Sessions": "sel", "Notes": "nsel"}
        for name in self.BAR_TABS:
            seen = {}

            def look(label, get):
                async def step(pilot):
                    seen[label] = get(pilot.app.ui)
                return step

            async def pick_header(pilot):  # ←→ act on the selected row: the Recent header, folded to start
                ui = pilot.app.ui
                ui.folded.add("Recent")
                ui.psel = next(i for i, r in enumerate(ui.project_rows()) if r.kind == "head" and r.sec == "Recent")
                pilot.app.redraw()

            async def pick_project(pilot):  # enter acts on the selected row: the project, under Recent opened again
                ui = pilot.app.ui
                ui.folded.discard("Recent")
                ui.psel = next(i for i, r in enumerate(ui.project_rows()) if r.kind == "project" and r.item["id"] == self.pid)
                pilot.app.redraw()

            def actions(ui):
                if name == "Projects":
                    return [l for l, _ in ui.row_actions(ui.prows[ui.psel])]
                if name == "Sessions":
                    return [l for l, _ in ui.session_actions(ui.srows[ui.sel])]
                return [l for l, _ in ui.note_actions(ui.nrows[ui.nsel])]
            at = lambda ui: getattr(ui, sel[name])
            async def catch_exit(pilot):  # what ctrl+q leads to: the app exits (run_test would end it anyway)
                pilot.app.exit = lambda *a, **k: seen.setdefault("exits", []).append(a)
            arrows = {"Projects": lambda ui: "Recent" not in ui.folded, "Sessions": lambda ui: ui.view,
                      "Notes": lambda ui: ui.nreading}[name]
            steps = [look("tab", lambda ui: ui.TABS[ui.tab].name), look("rows", lambda ui: len(ui.draw())),
                     look("at", at), self.keys("down"), look("down", at), self.keys("up"), look("up", at)]
            if name == "Projects":
                steps.append(pick_header)
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
            app = self.run_on_tab(name, steps)
            names = [t.name for t in app.ui.TABS]
            with self.subTest(tab=name):
                self.assertEqual(seen["tab"], name)
                self.assertGreater(seen["rows"], seen["at"] + 1)  # room to move down one
                self.assertEqual((seen["down"], seen["up"]), (seen["at"] + 1, seen["at"]), "↑↓ move")
                if name == "Sessions":
                    self.assertEqual((seen["right"], seen["left"]), ((seen["arrows"] + 1) % len(roost.VIEWS), seen["arrows"]), "←→ views")
                else:  # Projects: → unfolds the Recent header, ← folds it · Notes: → reads the note, ← goes back to the list
                    self.assertEqual((seen["arrows"], seen["right"], seen["left"]), (False, True, False), "←→")
                self.assertTrue(seen["actions"])
                self.assertEqual(seen["menu"], seen["actions"], "enter actions")
                self.assertTrue(seen["search"], "/ search")
                self.assertEqual(seen["new"], "New", "ctrl+n new")
                self.assertIn("Note", seen["new options"])
                self.assertEqual(seen["note prompt"], "New note: its title", "ctrl+n then n")
                self.assertEqual((seen["next tab"], seen["back"]), (names[(names.index(name) + 1) % len(names)], name), "tab switch tab")
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
                    if entry.startswith("←→"):  # what ←→ does is the tab's own: views, open/close, or → read
                        self.assertIn("→ ", drawn)
                    else:
                        self.assertIn(entry, drawn)


if __name__ == "__main__":
    unittest.main()
