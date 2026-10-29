from support import *  # noqa: F401,F403 (the shared namespace, helpers, and fixtures)
from support import own_tests  # noqa: F401
from test_screens import TextualScreens  # for their helpers; their tests run in test_screens.py

load_tests = own_tests(__name__)


@unittest.skipUnless(roost.App, "Textual isn't installed here: run the tests with ~/.roost/venv/bin/python")
class Unfiled(unittest.TestCase):
    """The Sessions tab's Unfiled view (Running, Closed, Unfiled, switched with ← →): every Session no project
    (or sub-project) lists and the person hasn't dismissed, and every note in ~/.roost/notes. Running Sessions
    first, then the rest by last activity or edit, newest first. A row's actions are the Unfiled entry's were."""
    run_app = TextualScreens.run_app
    keys, select = staticmethod(TextualScreens.keys), staticmethod(TextualScreens.select)
    LISTED = ["Laptop refresh", "Badge scan", "Printer idea", "Toner order", "Cable plan", "Old idea", "Old audit"]
    NEVER = ["Printer fix", "Fleet audit", "Badge work", "Dismissed one"]  # filed, or dismissed

    def setUp(self):
        """Sessions: Printer fix (idle) and Fleet audit (closed 20m ago), listed by printer-fleet; Badge work
        (idle), listed by its sub-project beta; Laptop refresh (waiting, a minute ago) and Badge scan (idle,
        10 hours ago), running; Toner order (closed 30m ago), Old audit (closed 10 days ago), and Dismissed
        one (closed an hour ago, dismissed). Notes: Printer idea (edited 15m ago), Cable plan (2 days),
        Old idea (9 days)."""
        TextualScreens.setUp(self)  # stand-ins for every reader, and a temp config.NOTES
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher, _ = dismissed_file(Path(tmp.name) / "dismissed.json")
        self.addCleanup(patcher.stop)
        closed = TextualScreens.closed
        self.by_name = {s["name"]: s for s in [
            session("Printer fix", mins=5), closed("Fleet audit", 20), session("Badge work", mins=2),
            session("Laptop refresh", state="waiting", mins=1), session("Badge scan", mins=600),
            closed("Toner order", 30), closed("Old audit", 10 * 1440), closed("Dismissed one", 60)]}
        self.sessions = list(self.by_name.values())
        roost.config.DISMISSED.write_text(json.dumps([self.by_name["Dismissed one"]["id"]]))
        proj = TextualScreens.proj("printer-fleet", [self.by_name["Printer fix"], self.by_name["Fleet audit"]], label="Printer fleet")
        sub = dict(TextualScreens.proj("printer-fleet/beta", [self.by_name["Badge work"]]), slug="beta", parent="printer-fleet",
                   title="Beta", type="feature", handed=None)
        self.projects = [dict(proj, subs=[sub])]

    def notes(self):
        now = time.time()
        self.note_paths = {}
        for title, mins in (("Printer idea", 15), ("Cable plan", 2 * 1440), ("Old idea", 9 * 1440)):
            path = roost.create_note(title, body="first line")
            os.utime(path, (now - mins * 60, now - mins * 60))
            self.note_paths[title] = str(path)

    def start(self, steps, size=(120, 40), notes=True):
        import unittest.mock
        if notes and not hasattr(self, "note_paths"):  # notes are made once per test; a second roost in the same test duplicated them
            self.notes()
        with unittest.mock.patch.multiple(roost, read_projects=lambda sessions: self.projects, read_config=lambda: {}):
            return self.run_app(list(steps), size=size)

    @staticmethod
    def on_unfiled():
        """A pilot step: the Sessions tab (tab from Projects), then → until its Unfiled view shows, at most once round."""
        async def step(pilot):
            await open_view("Sessions")(pilot)
            for _ in range(len(roost.VIEWS)):
                ui = pilot.app.ui
                if roost.VIEWS[ui.view] == "Unfiled":
                    break
                await pilot.press("right")
                await pilot.pause()
        return step

    @staticmethod
    def look(seen, label, get=None):
        """A step that keeps, under label: the screen's lines, and what get(app) says."""
        async def step(pilot):
            pilot.app.redraw()
            await pilot.pause()
            seen[label] = (screen_text(pilot.app), get(pilot.app) if get else None)
        return step

    @staticmethod
    def order(lines, names):
        """The row each name is on (None when it isn't there), the panel at the bottom left out."""
        return {n: next((i for i, l in enumerate(lines[:-10]) if re.search(rf"(^|\s){re.escape(n)}(\s|$)", l)), None) for n in names}

    def assert_listed_in_order(self, lines, names, running=2):
        """names are listed, top to bottom; the first running ones (any order among them) come first."""
        ys = self.order(lines, names)
        self.assertNotIn(None, ys.values(), (ys, "\n".join(lines)))
        got = [ys[n] for n in names]
        self.assertLess(max(got[:running]), min(got[running:]), ("running Sessions first", ys))
        self.assertEqual(got[running:], sorted(got[running:]), ("the rest newest first", ys))

    @staticmethod
    def menu(app):
        ui = app.ui
        c = ui.choosing
        return c and (c["title"], [o[0] for o in ui.choice_options() if not is_divider(o)])

    @staticmethod
    def labels(acts):
        return [label for label, fn in acts if (label, fn) != roost.DIVIDER]

    # the view

    def test_unfiled_is_the_sessions_tabs_third_view_and_not_a_tab(self):
        self.assertEqual(list(roost.VIEWS), ["Running", "Closed", "Unfiled"])
        self.assertEqual([t.name for t in roost.UI.TABS], list(TABS))

    def test_it_lists_every_unfiled_session_and_note_running_first_then_newest_first(self):
        seen = {}
        app = self.start([self.on_unfiled(), self.look(seen, "x")])
        lines, _ = seen["x"]
        self.assertEqual((app.ui.TABS[app.ui.tab].name, roost.VIEWS[app.ui.view]), ("Sessions", "Unfiled"))
        self.assert_listed_in_order(lines, self.LISTED)
        ys = self.order(lines, self.NEVER)
        self.assertEqual({n: y for n, y in ys.items() if y is not None}, {}, "nothing filed or dismissed")

    def test_each_row_shows_its_name_and_mark(self):
        seen = {}
        status = lambda app: {n: app.ui.status_cell(s)[0] for n, s in self.by_name.items()}
        self.start([self.on_unfiled(), self.look(seen, "x", status)])
        lines, marks = seen["x"]
        ys = self.order(lines, self.LISTED)
        for name, y in ys.items():
            self.assertIsNotNone(y, (name, "\n".join(lines)))
            row = lines[y]
            if name in self.by_name:
                self.assertIn(marks[name], row, f"{name}: its status as the Sessions list shows it")
                self.assertNotIn("✎", row, name)
            else:
                self.assertIn("✎", row, f"{name}: a note's mark")
        self.assertTrue(marks["Laptop refresh"].startswith("◆") and marks["Toner order"].endswith("closed"), marks)

    def test_nothing_unfiled(self):
        self.sessions = [self.by_name["Printer fix"], self.by_name["Fleet audit"], self.by_name["Dismissed one"]]
        seen = {}
        self.start([self.on_unfiled(), self.look(seen, "x")], notes=False)
        lines = seen["x"][0]
        self.assertTrue([l for l in lines if "Nothing" in l], "\n".join(lines))
        self.assertEqual({n: y for n, y in self.order(lines, self.NEVER).items() if y is not None}, {})

    # the keys

    def test_up_and_down_move_and_enter_opens_the_selected_items_actions(self):
        seen = {}

        def with_wants(app):
            ui = app.ui
            note = next(n for n in ui.notes if n["name"] == "Printer idea")
            return self.menu(app), self.labels(ui.note_actions(note)), \
                {n: self.labels(ui.session_actions(s)) for n, s in self.by_name.items()}
        self.start([self.on_unfiled(), self.keys("down", "down", "enter"), self.look(seen, "note", with_wants),
                    self.keys("escape", "up", "up", "enter"), self.look(seen, "session", with_wants)])
        menu, note_labels, _ = seen["note"][1]
        self.assertEqual(menu, ("Printer idea", note_labels), "the third row is the note Printer idea")
        self.assertIn("Move to a project", note_labels)
        menu, _, session_labels = seen["session"][1]
        self.assertIsNotNone(menu)
        title, labels = menu
        self.assertIn(title, ("Laptop refresh", "Badge scan"), "a running Session is on top")
        self.assertEqual(labels, session_labels[title])
        self.assertIn("Add to a project", labels)
        self.assertNotIn("Dismiss", labels, "only a closed Session offers Dismiss")
        self.assertTrue([l for l in note_labels if l.startswith(("Make it a project", "Delete"))], note_labels)

    def test_slash_searches_the_unfiled_view_by_name_and_esc_clears_it(self):
        seen = {}
        self.start([self.on_unfiled(), self.keys("slash", *"toner", "enter"), self.look(seen, "search"),
                    self.keys("escape"), self.look(seen, "cleared"),
                    self.keys("slash", *"cable"), self.look(seen, "typing"), self.keys("escape"), self.look(seen, "esc")])
        ys = self.order(seen["search"][0], ["Toner order", "Laptop refresh", "Printer idea"])
        self.assertIsNotNone(ys["Toner order"], "\n".join(seen["search"][0]))
        self.assertEqual((ys["Laptop refresh"], ys["Printer idea"]), (None, None))
        self.assertIsNotNone(self.order(seen["cleared"][0], ["Laptop refresh"])["Laptop refresh"], "esc clears the search")
        ys = self.order(seen["typing"][0], ["Cable plan", "Toner order"])
        self.assertIsNotNone(ys["Cable plan"])
        self.assertIsNone(ys["Toner order"])
        self.assertIsNotNone(self.order(seen["esc"][0], ["Toner order"])["Toner order"])

    # the mouse

    def rows(self):
        """The screen row of each listed item, from a first roost."""
        seen = {}
        self.start([self.on_unfiled(), self.look(seen, "x")])
        ys = self.order(seen["x"][0], self.LISTED)
        self.assertNotIn(None, ys.values(), "\n".join(seen["x"][0]))
        return ys

    def test_a_click_selects_a_row(self):
        ys, seen = self.rows(), {}
        self.start([self.on_unfiled(), lambda pilot: pilot.click(offset=(8, ys["Toner order"])),
                    self.look(seen, "clicked", self.menu), self.keys("enter"), self.look(seen, "x", self.menu)])
        self.assertIsNone(seen["clicked"][1], "a click only selects it")
        self.assertEqual(seen["x"][1][0], "Toner order")

    def test_a_double_click_runs_the_rows_first_action(self):
        import unittest.mock
        ys, edited, opened = self.rows(), [], []
        with unittest.mock.patch.object(roost.UI, "edit", lambda ui, p: edited.append(str(p))), \
                unittest.mock.patch.object(roost.UI, "open_one", lambda ui, s: opened.append(s["name"])):
            self.start([self.on_unfiled(), lambda pilot: pilot.click(offset=(8, ys["Printer idea"]), times=2)])
            self.assertEqual(edited, [self.note_paths["Printer idea"]], "a note's first action: Edit")
            self.start([self.on_unfiled(), lambda pilot: pilot.click(offset=(8, ys["Laptop refresh"]), times=2)])
        self.assertEqual(opened, ["Laptop refresh"], "a running Session's first action opens it")

    def test_a_right_click_opens_the_rows_menu(self):
        ys, seen = self.rows(), {}
        self.start([self.on_unfiled(), lambda pilot: pilot.click(offset=(8, ys["Toner order"]), button=3),
                    self.look(seen, "x", lambda app: (self.menu(app), self.labels(app.ui.session_actions(self.by_name["Toner order"]))))])
        menu, want = seen["x"][1]
        self.assertEqual(menu, ("Toner order", want))


    # the look

    def test_the_selected_row_is_a_green_mark_and_a_bold_green_name_with_no_background(self):
        seen = {}

        def rows(app):
            ui = app.ui
            kind, item = ui.urows[ui.usel]
            lines = screen_text(app)
            out = {}
            for y in ui.urow_y:
                name = ui.urows[ui.urow_y[y]][1]["name"]
                x = lines[y].index(name)
                out[name] = {"name": [style_at(app, y, x + i) for i in range(len(name))], "fills": backgrounds(app, y),
                             "mark": (lines[y][1], color_of(style_at(app, y, 1))), "line": lines[y]}
            return item["name"], out
        self.start([self.on_unfiled(), self.look(seen, "first", rows), self.keys("down"), self.look(seen, "second", rows)])
        green = roost.GREEN.lower()
        for when in ("first", "second"):
            sel, out = seen[when][1]
            with self.subTest(selected=sel):
                got = out[sel]
                self.assertEqual(got["fills"], set(), f"no background fill on the selected row: {got['line']!r}")
                self.assertEqual(got["mark"], ("›", green))
                self.assertTrue(all(color_of(st) == green and st.bold for st in got["name"]), "the name is bold green")
                for name, other in out.items():
                    if name != sel:
                        self.assertNotEqual(other["mark"][0], "›", name)
                        self.assertFalse([st for st in other["name"] if color_of(st) == green], f"{name} isn't selected")
        self.assertNotEqual(seen["first"][1][0], seen["second"][1][0], "the fixture: ↓ moved the selection")


if __name__ == "__main__":
    unittest.main()
