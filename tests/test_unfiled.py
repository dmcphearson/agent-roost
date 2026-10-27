from support import *  # noqa: F401,F403 (the shared namespace, helpers, and fixtures)
from support import own_tests  # noqa: F401
from test_screens import TextualScreens  # for their helpers; their tests run in test_screens.py

load_tests = own_tests(__name__)


@unittest.skipUnless(roost.App, "Textual isn't installed here: run the tests with ~/.roost/venv/bin/python")
class Unfiled(unittest.TestCase):
    """The Unfiled entry, first under the projects, and its view in the right pane: the Sessions no project
    (or sub-project) lists and the person hasn't dismissed, and the notes in ~/.roost/notes. Recent (uview 0)
    has the running Sessions, the ones closed in the last 7 days, and the notes edited in the last 7 days;
    All (uview 1) has every one. Running Sessions first, then the rest by last activity or edit, newest first."""
    run_app = TextualScreens.run_app
    keys, select = staticmethod(TextualScreens.keys), staticmethod(TextualScreens.select)
    RECENT = ["Laptop refresh", "Badge scan", "Printer idea", "Toner order", "Cable plan"]
    OLDER = ["Old idea", "Old audit"]  # in All only: a note edited 9 days ago, a Session closed 10 days ago
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
    def look(seen, label, get=None):
        """A step that keeps, under label: the screen's lines, and what get(app) says."""
        async def step(pilot):
            pilot.app.redraw()
            await pilot.pause()
            seen[label] = (screen_text(pilot.app), get(pilot.app) if get else None)
        return step

    @staticmethod
    def pane(lines):
        return [l[roost.SIDEBAR_W:] for l in lines]

    def order(self, lines, names):
        """The row each name is on in the pane (None when it isn't there)."""
        pane = self.pane(lines)
        return {n: next((i for i, l in enumerate(pane) if re.search(rf"(^|\s){re.escape(n)}(\s|$)", l)), None) for n in names}

    def assert_listed_in_order(self, lines, names, running=2):
        """names are in the pane, top to bottom; the first running ones (any order among them) come first."""
        ys = self.order(lines, names)
        self.assertNotIn(None, ys.values(), (ys, "\n".join(self.pane(lines))))
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

    # the entry and its view

    def test_unfiled_is_the_first_entry_and_a_view_after_projects(self):
        self.assertEqual(roost.ENTRIES, ("Unfiled", "Sessions", "Notes", "Stats"))
        self.assertEqual([t.name for t in roost.UI.TABS], ["Projects", "Unfiled", "Sessions", "Notes", "Stats"])

    def test_the_sidebar_shows_unfiled_first_under_the_projects_with_its_recent_count(self):
        seen = {}
        self.start([self.look(seen, "projects"), open_view("Unfiled"), self.look(seen, "unfiled", lambda app: (
            app.ui.TABS[app.ui.tab].name, app.ui.focus))])
        side = [l[:roost.SIDEBAR_W] for l in seen["projects"][0]]
        new = next(i for i, l in enumerate(side) if "+ New project" in l)
        self.assertIn("Unfiled", side[new + 2], "\n".join(side))
        self.assertIn("Sessions", side[new + 3], "\n".join(side))
        self.assertTrue(side[new + 2].rstrip(" │┃▏▕").endswith(f" {len(self.RECENT)}"),
                        f"the Recent count ({len(self.RECENT)}) at its right: {side[new + 2]!r}")
        self.assertEqual(seen["unfiled"][1], ("Unfiled", "pane"))
        pane = "\n".join(self.pane(seen["unfiled"][0]))
        self.assertIn(f"Recent ({len(self.RECENT)})", pane, "the sidebar's count is Recent's")

    def test_recent_and_all_are_labeled_with_their_counts(self):
        seen = {}
        self.start([open_view("Unfiled"), self.look(seen, "x")])
        pane = "\n".join(self.pane(seen["x"][0]))
        self.assertIn(f"Recent ({len(self.RECENT)})", pane)
        self.assertIn(f"All ({len(self.RECENT) + len(self.OLDER)})", pane)
        self.assertLess(pane.index("Recent ("), pane.index("All ("))

    def test_recent_lists_running_sessions_then_recent_ones_and_notes_newest_first(self):
        seen = {}
        self.start([open_view("Unfiled"), self.look(seen, "recent", lambda app: app.ui.uview)])
        lines, uview = seen["recent"]
        self.assertEqual(uview, 0)
        self.assert_listed_in_order(lines, self.RECENT)
        ys = self.order(lines, self.OLDER + self.NEVER)
        self.assertEqual({n: y for n, y in ys.items() if y is not None}, {}, "only Recent's, and nothing filed or dismissed")

    def test_right_and_left_switch_between_recent_and_all(self):
        seen = {}
        self.start([open_view("Unfiled"), self.keys("right"), self.look(seen, "all", lambda app: app.ui.uview),
                    self.keys("left"), self.look(seen, "recent", lambda app: app.ui.uview)])
        lines, uview = seen["all"]
        self.assertEqual(uview, 1)
        self.assert_listed_in_order(lines, self.RECENT[:2] + ["Printer idea", "Toner order", "Cable plan", "Old idea", "Old audit"])
        ys = self.order(lines, self.NEVER)
        self.assertEqual({n: y for n, y in ys.items() if y is not None}, {}, "nothing filed or dismissed, even in All")
        lines, uview = seen["recent"]
        self.assertEqual(uview, 0)
        self.assertEqual(self.order(lines, ["Old audit"])["Old audit"], None)

    def test_each_row_shows_its_name_when_and_mark(self):
        seen = {}
        status = lambda app: {n: app.ui.status_cell(s)[0] for n, s in self.by_name.items()}
        self.start([open_view("Unfiled"), self.keys("right"), self.look(seen, "all", status)])
        lines, marks = seen["all"]
        pane = self.pane(lines)
        ys = self.order(lines, self.RECENT + self.OLDER)
        for name, y in ys.items():
            self.assertIsNotNone(y, (name, "\n".join(pane)))
            row = pane[y]
            if name in self.by_name:
                s = self.by_name[name]
                self.assertIn(marks[name], row, f"{name}: its status as the Sessions list shows it")
                self.assertIn(roost.short_when(s["last"]), row, name)
                self.assertNotIn("✎", row, name)
            else:
                self.assertIn("✎", row, f"{name}: a note's mark")
                self.assertIn(roost.short_when(os.stat(self.note_paths[name]).st_mtime), row, name)
        self.assertTrue(marks["Laptop refresh"].startswith("◆") and marks["Toner order"].endswith("closed"), marks)

    def test_nothing_unfiled(self):
        self.sessions = [self.by_name["Printer fix"], self.by_name["Fleet audit"], self.by_name["Dismissed one"]]
        seen = {}
        self.start([open_view("Unfiled"), self.look(seen, "x")], notes=False)
        self.assertIn("Nothing unfiled.", "\n".join(self.pane(seen["x"][0])))

    # the keys in the pane

    def test_up_and_down_move_usel_and_enter_opens_the_selected_items_actions(self):
        seen = {}

        def with_wants(app):
            ui = app.ui
            note = next(n for n in ui.notes if n["name"] == "Printer idea")
            return ui.usel, self.menu(app), self.labels(ui.note_actions(note)), \
                {n: self.labels(ui.session_actions(s)) for n, s in self.by_name.items()}
        self.start([open_view("Unfiled"), self.look(seen, "start", lambda app: app.ui.usel), self.keys("down"),
                    self.look(seen, "down", lambda app: app.ui.usel), self.keys("down", "enter"), self.look(seen, "note", with_wants),
                    self.keys("escape", "up", "up"), self.look(seen, "top", lambda app: app.ui.usel), self.keys("enter"),
                    self.look(seen, "session", with_wants)])
        self.assertEqual((seen["start"][1], seen["down"][1], seen["top"][1]), (0, 1, 0))
        usel, menu, note_labels, _ = seen["note"][1]
        self.assertEqual(usel, 2)
        self.assertEqual(menu, ("Printer idea", note_labels), "the third Recent row is the note Printer idea")
        self.assertIn("Move to a project", note_labels)
        _, menu, _, session_labels = seen["session"][1]
        self.assertIsNotNone(menu)
        title, labels = menu
        self.assertIn(title, ("Laptop refresh", "Badge scan"), "a running Session is on top")
        self.assertEqual(labels, session_labels[title])
        self.assertIn("Add to a project", labels)

    def test_slash_searches_unfiled_by_name_and_esc_clears_it(self):
        seen = {}
        q = lambda app: (getattr(app.ui, "uquery", None), app.ui.focus)
        self.start([open_view("Unfiled"), self.keys("slash", *"toner", "enter"), self.look(seen, "search", q),
                    self.keys("escape"), self.look(seen, "cleared", q),
                    self.keys("slash", *"cable"), self.look(seen, "typing", q), self.keys("escape"), self.look(seen, "esc", q)])
        lines, (query, focus) = seen["search"]
        self.assertEqual((query, focus), ("toner", "pane"))
        ys = self.order(lines, ["Toner order", "Laptop refresh", "Printer idea"])
        self.assertIsNotNone(ys["Toner order"], "\n".join(self.pane(lines)))
        self.assertEqual((ys["Laptop refresh"], ys["Printer idea"]), (None, None))
        lines, (query, focus) = seen["cleared"]
        self.assertEqual((query, focus), ("", "pane"), "esc clears the search first, and the keys stay in the pane")
        self.assertIsNotNone(self.order(lines, ["Laptop refresh"])["Laptop refresh"])
        lines, (query, _) = seen["typing"]
        self.assertEqual(query, "cable")
        self.assertIsNotNone(self.order(lines, ["Cable plan"])["Cable plan"])
        self.assertIsNone(self.order(lines, ["Toner order"])["Toner order"])
        self.assertEqual(seen["esc"][1][0], "")

    # the mouse

    def rows(self):
        """The pane's row for each Recent item, from a first roost."""
        seen = {}
        self.start([open_view("Unfiled"), self.look(seen, "x")])
        ys = self.order(seen["x"][0], self.RECENT)
        self.assertNotIn(None, ys.values(), "\n".join(self.pane(seen["x"][0])))
        return ys

    def test_a_click_selects_a_row_and_moves_the_keys_into_the_pane(self):
        ys, seen = self.rows(), {}
        self.start([open_view("Unfiled"), self.keys("tab"), self.look(seen, "side", lambda app: app.ui.focus),  # the keys back in the sidebar
                    lambda pilot: pilot.click(offset=(roost.SIDEBAR_W + 8, ys["Toner order"])),
                    self.look(seen, "x", lambda app: (app.ui.usel, app.ui.focus, self.menu(app), app.ui.TABS[app.ui.tab].name))])
        self.assertEqual(seen["side"][1], "side")
        self.assertEqual(seen["x"][1], (self.RECENT.index("Toner order"), "pane", None, "Unfiled"))

    def test_a_double_click_runs_the_rows_first_action(self):
        import unittest.mock
        ys, edited, opened = self.rows(), [], []
        with unittest.mock.patch.object(roost.UI, "edit", lambda ui, p: edited.append(str(p))), \
                unittest.mock.patch.object(roost.UI, "open_one", lambda ui, s: opened.append(s["name"])):
            self.start([open_view("Unfiled"), lambda pilot: pilot.click(offset=(roost.SIDEBAR_W + 8, ys["Printer idea"]), times=2)])
            self.assertEqual(edited, [self.note_paths["Printer idea"]], "a note's first action: Edit")
            self.start([open_view("Unfiled"), lambda pilot: pilot.click(offset=(roost.SIDEBAR_W + 8, ys["Laptop refresh"]), times=2)])
        self.assertEqual(opened, ["Laptop refresh"], "a running Session's first action opens it")

    def test_a_right_click_opens_the_rows_menu(self):
        ys, seen = self.rows(), {}
        self.start([open_view("Unfiled"), lambda pilot: pilot.click(offset=(roost.SIDEBAR_W + 8, ys["Toner order"]), button=3),
                    self.look(seen, "x", lambda app: (self.menu(app), self.labels(app.ui.session_actions(self.by_name["Toner order"]))))])
        menu, want = seen["x"][1]
        self.assertEqual(menu, ("Toner order", want))


if __name__ == "__main__":
    unittest.main()
