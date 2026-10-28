from support import *  # noqa: F401,F403 (the shared namespace, helpers, and fixtures)
from support import _SCRATCH, own_tests  # noqa: F401
from test_screens import TextualScreens  # for their helpers; their tests run in test_screens.py
from datetime import date, timedelta

load_tests = own_tests(__name__)


class Projects(unittest.TestCase):
    """The Projects folder, read from temp folders only."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.saved = roost.CONFIG, roost.DONE
        roost.CONFIG, roost.DONE = base / "config.json", base / "_done"
        self.root = base / "projects"
        self.root.mkdir()
        roost.save_config(projects_dir=str(self.root))

    def tearDown(self):
        roost.CONFIG, roost.DONE = self.saved
        self.tmp.cleanup()

    def project(self, name, text=README, handoffs=()):
        folder = self.root / name
        (folder / "handoffs").mkdir(parents=True)
        (folder / "README.md").write_text(text)
        for h in handoffs:
            (folder / "handoffs" / h).write_text("x")
        return folder

    def test_frontmatter_reads_it_workbench_format(self):
        meta = roost.frontmatter(README)
        self.assertEqual(meta["summary"], "Two password managers compared: cost and rollout.")
        self.assertEqual((meta["status"], meta["category"]), ("active", "vendor-reviews"))
        self.assertEqual([x["id"] for x in meta["sessions"]], ["aa1b9a71-0000", "bb2c0000-0000"])
        self.assertEqual(roost.frontmatter("---\nsummary: Plain one\nsessions: []\n---\n")["sessions"], [])
        self.assertEqual(roost.frontmatter("# no frontmatter\nsummary: x"), {})

    def test_last_worked_is_the_newest_session_or_handoff(self):
        self.project("2026-09-a", handoffs=["2026-10-07-1612-aa1b9a71.md", "2026-10-01-0900-aa1b9a71.md"])
        p = roost.read_projects()[0]
        self.assertEqual(roost.worked(p["last"]), "Oct 7, 4:12 pm")
        later = time.time()
        p = roost.read_projects([{"id": "x", "ids": ["x", "bb2c0000-0000"], "last": later}])[0]
        self.assertEqual(p["last"], later)

    def test_newest_work_first_then_newest_name(self):
        self.project("2026-01-old", handoffs=["2026-10-08-1000-aaaaaaaa.md"])
        self.project("2026-05-never", "---\nsummary: s\n---\n")
        self.project("2026-09-never", "---\nsummary: s\n---\n")
        (self.root / "_By Category").mkdir()
        self.assertEqual([p["id"] for p in roost.read_projects()], ["2026-01-old", "2026-09-never", "2026-05-never"])

    def test_closed_by_status_or_close_out_file(self):
        self.project("a", README.replace("status: active", "status: closed"))
        self.project("b")
        self.project("c")
        roost.DONE.mkdir()
        (roost.DONE / "c.md").write_text("summary")
        self.assertEqual({p["id"]: p["closed"] for p in roost.read_projects()}, {"a": True, "b": False, "c": True})

    def test_a_missing_folder_reads_as_none(self):
        roost.save_config(projects_dir=str(self.root / "nope"))
        self.assertIsNone(roost.read_projects())

    def test_reading_changes_nothing(self):
        self.project("2026-09-a", handoffs=["2026-10-07-1612-aa1b9a71.md"])
        (self.root / "loose.md").write_text("not a project")
        before = {p: (p.read_bytes() if p.is_file() else None, p.stat().st_mtime) for p in self.root.rglob("*")}
        roost.read_projects([{"id": "aa1b9a71-0000", "last": 1.0}])
        self.assertEqual(before, {p: (p.read_bytes() if p.is_file() else None, p.stat().st_mtime) for p in self.root.rglob("*")})

    def test_create_writes_the_project_layout(self):
        folder = roost.create_project("2026-10-new", 'Say "hi"  to   it.')
        meta = roost.frontmatter((folder / "README.md").read_text())
        self.assertEqual(meta, {"description": 'Say "hi" to it.', "status": "active", "sessions": []})
        self.assertTrue((folder / "handoffs").is_dir())
        self.assertIn("## Current state", (folder / "README.md").read_text())

    def test_create_refuses_what_it_cant_do_safely(self):
        self.project("taken")
        for pid, summary in [("taken", "s"), ("a/b", "s"), ("_x", "s"), ("two words", "s")]:
            with self.assertRaises(ValueError):
                roost.create_project(pid, summary)
        self.assertEqual(roost.frontmatter((self.root / "taken" / "README.md").read_text())["category"], "vendor-reviews")
        roost.save_config(projects_dir=str(self.root / "nope"))
        with self.assertRaises(ValueError):
            roost.create_project("ok", "s")
        self.assertFalse((self.root / "nope").exists())

    def test_project_new_command(self):
        self.assertEqual(roost.run_command("project", ["new", "2026-10-x", "--summary", "A thing."]), 0)
        self.assertTrue((self.root / "2026-10-x" / "README.md").exists())
        self.assertEqual(roost.run_command("project", ["new", "2026-10-x", "--summary", "Again."]), 1)
        self.assertEqual(roost.run_command("project", ["new", "2026-10-y"]), 0)  # a description is optional

    def ui(self):
        ui = object.__new__(roost.UI)
        ui.sessions, ui.msg, ui.page, ui.last_click = [], "", 10, (0, -1)
        ui.psel, ui.ptop, ui.prow_y, ui.asking = 0, 0, {}, None
        ui.pquery, ui.searching = "", False
        ui.folded, ui.expanded = {"Inactive", "Closed"}, set()
        ui.choosing = ui.confirm = ui.term = None
        ui.summaries, ui.plugin_ok = {}, True
        ui.focus = "side"  # as roost starts: the keys go to the sidebar
        ui.load_projects()
        return ui

    def shown(self, ui):
        return [(r.kind, None if r.kind == "all" else r.item["id"]) for r in ui.project_rows()]

    def test_a_click_where_a_projects_arrow_was_only_selects_it(self):
        """Spec 1: a project row never expands, so a click at its left edge (where ▸ was) adds no rows,
        once or twice, and opens no menu."""
        self.project("b", handoffs=[time.strftime("%Y-%m-%d-%H%M") + "-aaaaaaaa.md"])
        ui = self.ui()
        ui.sessions = [dict(session("Password Manager Review"), id="aa1b9a71-0000")]
        ui.load_projects()
        ui.prow_y = {6: 0}  # the project's row, as draw left it
        click = ("mouse", 3, 6, roost.curses.BUTTON1_CLICKED)
        for _ in range(2):  # two separate single clicks: a quick second one would be a double-click
            ui.last_click = (0, -1)
            ui.keys_projects(click, ui.project_rows())
            self.assertEqual((self.shown(ui), ui.choosing), ([("project", "b"), ("all", None), ("new", "+ New project")], None))
        ui.keys_projects(("mouse", 20, 6, roost.curses.BUTTON1_CLICKED), ui.project_rows())  # the name only selects it
        self.assertEqual(self.shown(ui), [("project", "b"), ("all", None), ("new", "+ New project")])

    def test_no_session_rows_under_a_project_even_once_marked_open(self):
        """Spec 1: no Session row under a project in any state of the UI, even with the project in
        ui.expanded (as an older roost left it), and ← on the project changes nothing."""
        folder = self.project("b", handoffs=[time.strftime("%Y-%m-%d-%H%M") + "-aaaaaaaa.md"])
        end = time.time() - 3600
        iso = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        (folder / "handoffs" / "2026-10-01-0900-aa1b9a71.md").write_text(
            f"---\nsession: aa1b9a71-0000\nname: x\nfrom: \nto: {iso(end)}\n---\n\nDone.\n")
        (folder / "handoffs" / "2026-10-01-0901-bb2c0000.md").write_text(
            f"---\nsession: bb2c0000-0000\nname: x\nfrom: \nto: {iso(end - 7200)}\n---\n\nEarlier.\n")
        ui = self.ui()
        ui.sessions = [dict(session("Password Manager Review", state="closed", kind=None), id="aa1b9a71-0000", active=end),
                       dict(session("Second look", state="closed", kind=None), id="bb2c0000-0000", active=end)]  # worked on after
        ui.load_projects()
        ui.expanded = {"b"}
        self.assertEqual(self.shown(ui), [("project", "b"), ("all", None), ("new", "+ New project")])
        ui.psel = 0
        ui.keys_projects("left", ui.project_rows())
        self.assertEqual(self.shown(ui), [("project", "b"), ("all", None), ("new", "+ New project")])

    def wrapped_project(self, ids):
        """Project b listing ids, with a Handoff that covers aa1b9a71-0000 to its last message (an hour ago)
        and one that stops before bb2c0000-0000's; returns that hour-ago time."""
        listed = "".join(f"  - name: {i}\n    id: {i}\n" for i in ids)
        folder = self.project("b", README.split("sessions:\n")[0] + "sessions:\n" + listed + "---\n\n# B\n",
                              handoffs=[time.strftime("%Y-%m-%d-%H%M") + "-aaaaaaaa.md"])
        end = time.time() - 3600
        iso = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        (folder / "handoffs" / "2026-10-01-0900-aa1b9a71.md").write_text(
            f"---\nsession: aa1b9a71-0000\nname: x\nfrom: \nto: {iso(end)}\n---\n\nDone.\n")
        (folder / "handoffs" / "2026-10-01-0901-bb2c0000.md").write_text(
            f"---\nsession: bb2c0000-0000\nname: x\nfrom: \nto: {iso(end - 7200)}\n---\n\nEarlier.\n")
        return end

    def test_no_session_of_any_kind_gets_a_row_under_its_project(self):
        """Spec 1: running, idle, closed, and wrapped-up Sessions alike get no row under their project;
        → and enter on the project add none."""
        end = self.wrapped_project(["aa1b9a71-0000", "bb2c0000-0000", "cc3d0000-0000", "dd4e0000-0000", "ee5f0000-0000"])
        ui = self.ui()
        ui.sessions = [dict(session("Wrapped", state="closed", kind=None, mins=60), id="aa1b9a71-0000", active=end),
                       dict(session("Worked after", state="closed", kind=None, mins=50), id="bb2c0000-0000"),
                       dict(session("Idle one", mins=5), id="cc3d0000-0000"),
                       dict(session("No Handoff", state="closed", kind=None, mins=90), id="dd4e0000-0000"),
                       dict(session("Asking", state="waiting", mins=1), id="ee5f0000-0000")]
        ui.load_projects()
        self.assertEqual([ui.wrapped_up(ui.projects[0], s) for s in ui.sessions], [True, False, False, False, False])
        only = [("project", "b"), ("all", None), ("new", "+ New project")]
        ui.psel = 0
        ui.keys_projects("right", ui.project_rows())
        self.assertEqual(self.shown(ui), only)
        ui.keys_projects("enter", ui.project_rows())  # its actions, a menu: no rows
        ui.choosing = None
        self.assertEqual(self.shown(ui), only)

    def test_a_project_whose_sessions_are_all_wrapped_up_offers_show_its_sessions(self):
        """Spec 1 and 3: with only wrapped-up Sessions, →, and a click where its marker was, add no rows;
        its actions still offer Show its Sessions, since it lists a Session."""
        end = self.wrapped_project(["aa1b9a71-0000"])
        ui = self.ui()
        ui.sessions = [dict(session("Wrapped", state="closed", kind=None, mins=60), id="aa1b9a71-0000", active=end)]
        ui.load_projects()
        self.assertTrue(ui.wrapped_up(ui.projects[0], ui.sessions[0]))  # the fixture really is wrapped up
        ui.psel = 0
        ui.keys_projects("right", ui.project_rows())
        acts = labels(ui, ui.keys_projects, ui.project_rows())
        self.assertIn("Show its Sessions", acts)
        self.assertNotIn("Hide its Sessions", acts)
        ui.prow_y = {6: 0}  # the project's row, as draw left it
        ui.keys_projects(("mouse", 3, 6, roost.curses.BUTTON1_CLICKED), ui.project_rows())
        self.assertEqual(self.shown(ui), [("project", "b"), ("all", None), ("new", "+ New project")])

    def test_a_wrapped_up_session_doesnt_change_the_sessions_column(self):
        """SESSIONS counts running Sessions; a wrapped-up one is closed, so linking it changes nothing."""
        end = self.wrapped_project(["aa1b9a71-0000", "cc3d0000-0000"])
        ui = self.ui()
        ui.sessions = [dict(session("Asking", state="waiting"), id="cc3d0000-0000"),
                       dict(session("Wrapped", state="closed", kind=None, mins=60), id="aa1b9a71-0000", active=end)]
        ui.load_projects()
        self.assertTrue(ui.wrapped_up(ui.projects[0], ui.sessions[1]))
        self.assertEqual(ui.live_counts(ui.projects[0]), ui.live_counts({"sessions": ["cc3d0000-0000"]}))
        self.assertEqual(ui.live_counts(ui.projects[0]), [("input", 1)])

    def test_search_keeps_matching_rows_unfolded(self):
        self.project("printers", README.replace("Two password managers compared: cost and rollout.", "Fix the office printers."))
        self.project("vpn")
        ui = self.ui()
        ui.notes = []
        ui.pquery = "PRINTER"
        self.assertEqual(self.shown(ui), [("project", "printers")])

    def test_show_its_sessions_opens_the_sessions_tab_on_running_searching_for_the_project(self):
        """Spec 3: Show its Sessions switches to the Sessions tab, on Running when one of its Sessions is
        running, and prefills its search with project:<folder id>. The Projects tab gets no rows, and
        there's no Hide its Sessions."""
        self.project("b", handoffs=[time.strftime("%Y-%m-%d-%H%M") + "-aaaaaaaa.md"])
        ui = self.ui()
        ui.tab, ui.view, ui.query, ui.sel, ui.top = [t.name for t in ui.TABS].index("Projects"), RUNNING, "", 0, 0
        ui.sessions = [dict(session("Mine"), id="aa1b9a71-0000"), dict(session("Old one", state="closed", kind=None, mins=60), id="bb2c0000-0000"),
                       session("Loose", state="waiting")]
        ui.load_projects()
        ui.psel = 0
        act(ui, ui.keys_projects, ui.project_rows(), "Show its Sessions")
        self.assertEqual((ui.TABS[ui.tab].name, roost.VIEWS[ui.view], ui.query), ("Sessions", "Running", "project:b"))
        self.assertEqual(self.shown(ui), [("project", "b"), ("all", None), ("new", "+ New project")])
        self.assertFalse([l for l in labels(ui, ui.keys_projects, ui.project_rows()) if l.startswith("Hide its Sessions")])

    def test_show_its_sessions_opens_on_closed_when_none_of_its_sessions_is_running(self):
        """Spec 3: with every Session it lists closed, Show its Sessions opens Sessions on Closed, searching
        project:<folder id>, even while a Session it doesn't list is running."""
        self.project("b", handoffs=[time.strftime("%Y-%m-%d-%H%M") + "-aaaaaaaa.md"])
        ui = self.ui()
        ui.folded = set()
        ui.tab, ui.view, ui.query, ui.sel, ui.top = [t.name for t in ui.TABS].index("Projects"), RUNNING, "", 0, 0
        ui.sessions = [dict(session("Mine", state="closed", kind=None, mins=30), id="aa1b9a71-0000"),
                       dict(session("Old one", state="closed", kind=None, mins=60), id="bb2c0000-0000"),
                       session("Loose", state="waiting")]
        ui.load_projects()
        ui.psel = next(i for i, r in enumerate(ui.project_rows()) if r.kind == "project" and r.item["id"] == "b")
        act(ui, ui.keys_projects, ui.project_rows(), "Show its Sessions")
        self.assertEqual((ui.TABS[ui.tab].name, roost.VIEWS[ui.view], ui.query), ("Sessions", "Closed", "project:b"))

    def test_show_its_sessions_comes_after_start_a_session_or_first(self):
        """Spec 3: on an open project, Show its Sessions is right after Start a Session; on a closed
        one (no Start a Session), it's first."""
        self.project("b", handoffs=[time.strftime("%Y-%m-%d-%H%M") + "-aaaaaaaa.md"])
        self.project("c", README.replace("status: active", "status: closed"))
        ui = self.ui()
        ui.pquery = "rollout"  # both summaries hold it; c is closed, and only a search lists a closed one in the sidebar
        ui.sessions = [dict(session("Mine"), id="aa1b9a71-0000")]
        ui.load_projects()
        rows = ui.project_rows()
        ui.psel = next(i for i, r in enumerate(rows) if r.kind == "project" and r.item["id"] == "b")
        acts = labels(ui, ui.keys_projects, rows)
        self.assertTrue(acts[0].startswith("Start a Session"), acts)
        self.assertEqual(acts[1], "Show its Sessions")
        ui.psel = next(i for i, r in enumerate(rows) if r.kind == "project" and r.item["id"] == "c")
        acts = labels(ui, ui.keys_projects, rows)
        self.assertEqual(acts[0], "Show its Sessions")
        self.assertFalse([l for l in acts if l.startswith("Start a Session")])

    def test_sessions_no_project_lists_stay_on_the_sessions_tab(self):
        ui = self.ui()
        ui.sessions = [session("Asks", state="waiting"), session("Sits", mins=5)]
        self.assertEqual([(r.kind, r.item["id"]) for r in ui.project_rows()], [("new", "+ New project")])
        ui.keys_projects("enter", ui.project_rows())  # + New project
        self.assertEqual(ui.asking["title"], "New project: its name")  # in a box, as a new note asks

    def test_set_status_changes_only_that_line(self):
        folder = self.project("a")
        before = (folder / "README.md").read_text()
        self.assertEqual(roost.set_status(folder, "  Blocked  on   vendor "), "active")
        self.assertEqual((folder / "README.md").read_text(), before.replace("status: active", "status: blocked on vendor"))
        bare = self.project("b", "---\nsummary: s\nsessions: []\n---\n\n# B\nstatus: not frontmatter\n")
        self.assertEqual(roost.set_status(bare, "closed"), "")
        self.assertEqual((bare / "README.md").read_text(), "---\nstatus: closed\nsummary: s\nsessions: []\n---\n\n# B\nstatus: not frontmatter\n")
        self.assertTrue(roost.read_project(bare)["closed"])
        for bad in ["", "x: y", "9lives", "x" * 41, "#closed"]:
            with self.assertRaises(ValueError):
                roost.set_status(bare, bad)
        roost.set_status(bare, "on\nhold")  # a newline can't add a frontmatter line
        self.assertEqual(roost.frontmatter((bare / "README.md").read_text())["status"], "on hold")
        self.assertEqual(sorted(p.name for p in bare.iterdir()), ["README.md", "handoffs"])

    def test_status_command_prints_or_sets_it(self):
        folder = self.project("p")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(roost.run_command("project", ["status", "p"]), 0)
            self.assertEqual(roost.run_command("project", ["status", "p", "blocked"]), 0)
        self.assertEqual(out.getvalue(), "active\np: status active → blocked\n")
        self.assertEqual(roost.read_project(folder)["status"], "blocked")
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(roost.run_command("project", ["status", "nope", "closed"]), 1)
            self.assertEqual(roost.run_command("project", ["status", "p", "Not: ok"]), 1)
        self.assertIn("no project or Session recap named 'nope'", err.getvalue())
        self.assertEqual(roost.read_project(folder)["status"], "blocked")

    def test_edit_its_tags_picks_from_the_tags_in_use(self):
        self.project("a", handoffs=[time.strftime("%Y-%m-%d-%H%M") + "-aaaaaaaa.md"])
        self.project("b", "---\nsummary: s\ntags: [printers, network]\nsessions: []\n---\n\n# B\n")
        ui = self.ui()
        self.assertEqual(next(p for p in ui.projects if p["id"] == "a")["tags"], [])  # its category isn't a tag
        ui.psel = [r.item["id"] for r in ui.project_rows()].index("a")
        act(ui, ui.keys_projects, ui.project_rows(), "Edit its tags")
        self.assertEqual([o[0] for o in ui.choice_options()], ["network", "printers", "Save"])
        for k in names("print"):
            ui.keys_choice(k)
        ui.keys_choice("enter")  # ticks printers
        self.assertEqual(ui.choosing["multi"], ["printers"])
        for _ in "print":
            ui.keys_choice("backspace")
        for k in names("Macs"):
            ui.keys_choice(k)
        self.assertEqual([o[0] for o in ui.choice_options()], ["+ New tag: macs", "Save"])
        ui.keys_choice("enter")  # adds macs, ticked
        self.assertEqual((ui.choosing["multi"], ui.choosing["typed"]), (["printers", "macs"], ""))
        ui.keys_choice("enter")  # a new tag leaves the cursor on Save
        text = (self.root / "a" / "README.md").read_text()
        self.assertEqual(text, README.replace("category: vendor-reviews\n", "tags: [printers, macs]\ncategory: vendor-reviews\n"))
        self.assertEqual(next(p for p in ui.projects if p["id"] == "a")["tags"], ["printers", "macs"])
        act(ui, ui.keys_projects, ui.project_rows(), "Edit its tags")
        self.assertEqual(ui.choosing["multi"], ["printers", "macs"])
        ui.keys_choice("escape")
        self.assertIsNone(ui.choosing)

    def test_set_its_category_picks_one_in_use_or_a_new_one(self):
        self.project("a", handoffs=[time.strftime("%Y-%m-%d-%H%M") + "-aaaaaaaa.md"])
        self.project("b", README.replace("vendor-reviews", "ai-tools"))
        ui = self.ui()
        ui.psel = [r.item["id"] for r in ui.project_rows()].index("a")
        (self.root / "CATEGORIES.md").write_text("# Categories\n\n- incident-notes: what went wrong and why\n- `ai-tools`\nprose\n")
        act(ui, ui.keys_projects, ui.project_rows(), "Set its category")
        self.assertEqual([o[0] for o in ui.choice_options()],
                         ["incident-notes  what went wrong and why", "ai-tools", "vendor-reviews  · current", "No category"])
        ui.keys_choice("down")
        ui.keys_choice("enter")
        self.assertIn("category: ai-tools\n", (self.root / "a" / "README.md").read_text())
        self.assertEqual(ui.msg, "Category: ai-tools")
        act(ui, ui.keys_projects, ui.project_rows(), "Set its category")
        for k in names("Networking"):
            ui.keys_choice(k)
        self.assertEqual([o[0] for o in ui.choice_options()], ["+ New category: networking"])
        ui.keys_choice("enter")
        self.assertEqual(roost.read_project(self.root / "a")["category"], "networking")
        self.assertEqual(roost.read_categories(), [("incident-notes", "what went wrong and why"), ("ai-tools", ""), ("networking", "")])
        (self.root / "CATEGORIES.md").unlink()
        roost.add_category("first")
        self.assertEqual(roost.read_categories(), [("first", "")])

    def test_rename_a_project_from_its_actions(self):
        folder = self.project("b", README.replace("# Title", "# b"), handoffs=[time.strftime("%Y-%m-%d-%H%M") + "-aaaaaaaa.md"])
        (folder / "notes").mkdir()
        (folder / "notes" / "idea.md").write_text("---\ntitle: Idea\nproject: b\n---\n\nText\n")
        roost.DONE.mkdir(parents=True, exist_ok=True)
        (roost.DONE / "b.md").write_text("closed")
        self.project("taken")
        ui = self.ui()
        ui.pquery = "rollout"  # its close-out record closes it: only a search lists it in the sidebar (every README here holds it)
        ui.psel = [r.item["id"] for r in ui.project_rows()].index("b")
        act(ui, ui.keys_projects, ui.project_rows(), "Rename")
        self.assertIn("label", ui.asking["title"].lower())  # two prompts: its label, then its folder's name
        typed(ui, "\n")  # its label stays b
        self.assertIn("folder name", ui.asking["title"].lower())
        typed(ui, "\x7f" + "taken\n")
        self.assertIn("taken already exists", ui.msg)
        self.assertTrue(folder.exists())  # nothing changed
        ui.psel = [r.item["id"] for r in ui.project_rows()].index("b")
        act(ui, ui.keys_projects, ui.project_rows(), "Rename")
        typed(ui, "\n")
        typed(ui, "\x7f" + "vendor-track\n")
        new = self.root / "vendor-track"
        self.assertEqual((ui.msg, folder.exists(), new.is_dir()), ("Renamed b to vendor-track", False, True))
        self.assertIn("\n# vendor-track\n", (new / "README.md").read_text())
        self.assertEqual(roost.read_note(new / "notes" / "idea.md")["project"], "vendor-track")
        self.assertTrue((roost.DONE / "vendor-track.md").exists())
        ui.sessions = [dict(session("Live", state="idle"), id="aa1b9a71-0000")]
        ui.load_projects()
        ui.psel = [r.item["id"] for r in ui.project_rows()].index("vendor-track")
        act(ui, ui.keys_projects, ui.project_rows(), "Rename")
        self.assertIn("Live is running in it", ui.msg)

    def test_set_category_changes_only_that_line(self):
        folder = self.project("a")
        before = (folder / "README.md").read_text()
        roost.set_category(folder, "ai-tools")
        after = (folder / "README.md").read_text()
        self.assertEqual(after, before.replace("category: vendor-reviews", "category: ai-tools"))
        bare = self.project("b", "---\nsummary: s\nsessions: []\n---\n\n# B\n")
        roost.set_category(bare, "hardening")
        self.assertEqual((bare / "README.md").read_text(), "---\ncategory: hardening\nsummary: s\nsessions: []\n---\n\n# B\n")
        roost.set_category(bare, "")
        self.assertEqual((bare / "README.md").read_text(), "---\nsummary: s\nsessions: []\n---\n\n# B\n")
        for bad in ["Has Spaces", "x\ny"]:
            with self.assertRaises(ValueError):
                roost.set_category(bare, bad)
        with self.assertRaises(ValueError):
            roost.set_category(self.project("c", "# no frontmatter\n"), "x")

    def test_set_tags_replaces_a_block_list(self):
        folder = self.project("b", "---\nsummary: s\ntags:\n  - one\n  - two\nsessions: []\n---\n\n# B\n")
        roost.set_tags(folder, ["three"])
        self.assertEqual((folder / "README.md").read_text(), "---\nsummary: s\ntags: [three]\nsessions: []\n---\n\n# B\n")
        roost.set_tags(folder, [])
        self.assertEqual(roost.read_project(folder)["tags"], [])

    def test_sessions_column_counts_running_sessions(self):
        ui = self.ui()
        ui.sessions = [dict(session("A", state="waiting"), id="aa1b9a71-0000"), dict(session("B", state="busy"), id="bb2c0000-0000"),
                       dict(session("C", state="closed", kind=None), id="cc")]
        self.assertEqual(ui.live_counts({"sessions": ["aa1b9a71-0000", "bb2c0000-0000", "cc"]}), [("input", 1), ("working", 1)])

    def test_summaries_lose_their_markdown(self):
        self.assertEqual(roost.plain_text("## Where you stand\n**Most** of it is `done`."), "Where you stand Most of it is done.")

    def test_a_project_with_no_sessions_doesnt_expand(self):
        self.project("b", "---\nsummary: s\n---\n")
        ui = self.ui()
        ui.psel = 0
        acts = labels(ui, ui.keys_projects, ui.project_rows())
        self.assertNotIn("Show its Sessions", acts)
        self.assertEqual(acts[0], "Start a Session")

    def test_a_projects_label_is_its_readmes_heading(self):
        self.assertEqual(roost.heading("---\nsummary: x\n---\n\n# Roost UX\n\n## Next\n"), "Roost UX")
        self.assertEqual(roost.heading("```\n# not a heading\n```\n# After the fence\n"), "After the fence")
        self.assertEqual(roost.heading("---\n# a comment in frontmatter\n---\nNo heading.\n"), "")
        (self.root / "roost-ux").mkdir()
        (self.root / "roost-ux" / "README.md").write_text("---\nsummary: s\n---\n")
        p = roost.read_project(self.root / "roost-ux")
        self.assertEqual((p["label"], roost.label_of(p)), ("roost-ux", "roost-ux"))  # no heading: its folder's name

    def test_enter_on_an_empty_list_creates_a_project(self):
        ui = self.ui()
        ui.keys_projects("enter", [])
        self.assertIsNotNone(ui.asking)
        typed(ui, "Zed\n\x152026-10-z\nOne line.\n")  # its name, its folder (made from the name, edited), its summary
        self.assertIsNone(ui.asking)
        self.assertEqual(roost.read_project(self.root / "2026-10-z")["label"], "Zed")
        self.assertEqual(roost.frontmatter((self.root / "2026-10-z" / "README.md").read_text())["description"], "One line.")
        self.assertEqual(ui.project_rows()[ui.psel].item["id"], "2026-10-z")

    def test_esc_cancels_the_new_project_prompt(self):
        ui = self.ui()
        ui.keys_projects("enter", [])
        typed(ui, "x\x1b")
        self.assertEqual((ui.asking, list(self.root.iterdir())), (None, []))

    def test_demo_has_open_closed_and_never_worked_projects(self):
        ps = roost.demo_projects(roost.demo_sessions())
        self.assertTrue(any(p["closed"] for p in ps) and any(not p["last"] for p in ps))

    def test_demo_has_an_unfiled_session_and_its_handoffs_carry_a_path(self):
        """roost --demo shows the Unfiled entry with something in it, and its Handoffs are (time, title, path)."""
        sessions = roost.demo_sessions()
        ps = roost.demo_projects(sessions)
        listed = {i for p in ps for x in [p] + list(p.get("subs") or []) for i in x["sessions"]}
        self.assertTrue([s for s in sessions if not set(s.get("ids") or [s["id"]]) & listed], "no demo Session is unfiled")
        handoffs = [h for p in ps for h in p["handoffs"]]
        self.assertTrue(handoffs)
        for h in handoffs:
            self.assertEqual(len(h), 3, h)
            self.assertIsInstance(h[2], str, h)


class ProjectArrows(unittest.TestCase):
    def test_tags_are_read_from_a_list_or_a_line(self):
        self.assertEqual(roost.tag_list("[ideas, q4]"), ["ideas", "q4"])
        self.assertEqual(roost.tag_list(["a"]), ["a"])


@unittest.skipUnless(roost.App, "Textual isn't installed here: run the tests with ~/.roost/venv/bin/python")
class ProjectRows(unittest.TestCase):
    """The Projects tab's rows and dates, on a real Textual screen."""
    setUp = TextualScreens.setUp
    closed = staticmethod(TextualScreens.closed)
    proj = staticmethod(TextualScreens.proj)
    select = staticmethod(TextualScreens.select)
    keys = staticmethod(TextualScreens.keys)
    PAST = datetime(2025, 1, 5, 9, 30).timestamp()  # MDY 01/05/25, DMY 05/01/25

    def run_on(self, projects, steps=(), size=(160, 30), tab="Projects", config=None):
        import asyncio, unittest.mock
        settings = dict(config or {}, tab=tab)
        with unittest.mock.patch.multiple(roost, read_projects=lambda sessions: projects, read_config=lambda: settings):
            app = roost.RoostApp(self.sessions, "")

            async def go():
                async with app.run_test(size=size) as pilot:
                    await pilot.pause()
                    for step in [self.select(lambda ui: None)] + list(steps):  # a draw first, then the test's steps
                        await step(pilot)
                        await pilot.pause()
                    app.lines = screen_text(app)
            asyncio.run(go())
        return app

    def on_project(self, pid):
        return self.select(lambda ui: setattr(ui, "psel", next(i for i, r in enumerate(ui.project_rows())
                                                               if r.kind == "project" and r.item["id"] == pid)))

    @staticmethod
    def chosen(app):
        r = app.ui.project_rows()[app.ui.psel]
        return r.item["id"] if r.kind == "project" else r.kind

    @staticmethod
    def sidebar(lines):
        """The sidebar's part of each line: its first SIDEBAR_W columns."""
        return [l[:roost.SIDEBAR_W] for l in lines]

    @staticmethod
    def row_of(lines, text):
        return next(i for i, l in enumerate(lines) if text in l)

    def project(self, pid, linked=(), label=None, status="active", category="", tags=(), last=None, covered=()):
        p = self.proj(pid, list(linked), covered=covered, label=label)
        p.update(status=status, category=category, tags=list(tags), last=time.time() if last is None else last)
        return p

    # 1. Dates

    def test_last_worked_is_a_time_today_and_a_short_date_otherwise(self):
        """The selected project's Last worked, in the detail pane: a time today, else a short date in the
        person's date order."""
        import unittest.mock
        now = time.time()
        projects = [self.project("today-one", status="active", last=now), self.project("older-one", status="active", last=self.PAST)]
        for order, shown in (("MDY", "01/05/25"), ("DMY", "05/01/25")):
            for pid, want in (("today-one", clock(now)), ("older-one", shown)):
                with unittest.mock.patch.object(FMT, "date_order", lambda o=order: o):
                    lines = self.run_on(projects, [self.on_project(pid)]).lines
                self.assertTrue([l for l in lines if re.search(rf"· worked {re.escape(want)}(\s|$)", l)], (order, pid, lines))

    def test_the_projects_card_dates_are_short(self):
        import unittest.mock
        p = self.project("older-one", last=self.PAST)
        seen = {}

        async def panels(pilot):
            ui = pilot.app.ui
            s = dict(self.sessions[0], last=self.PAST)
            recap = {"id": "printer-recap", "last": self.PAST, "sessions": [], "path": "/nowhere/printer-recap.md", "summary": ""}
            for kind, item in (("session", s), ("recap", recap)):
                ui.psel = 0
                seen[kind] = dict(ui.project_panel([roost.Row("Recent", kind, item, 0)], 30, 160)[1])["Status"]
        with unittest.mock.patch.object(FMT, "date_order", lambda: "DMY"):
            app = self.run_on([p], [self.on_project("older-one"), panels])
        status = next(l for l in app.lines if "· worked" in l)
        self.assertRegex(status, r"· worked 05/01/25(\s|$)")
        self.assertRegex(seen["session"], r"Last active +05/01/25(\s|$)")
        self.assertRegex(seen["recap"], r"Last worked +05/01/25(\s|$)")

    def test_the_notes_list_and_card_dates_are_short(self):
        import unittest.mock
        old = roost.create_note("Printer idea", body="first line")
        os.utime(old, (self.PAST, self.PAST))
        new = roost.create_note("Laptop budget", body="first line")
        made = Path(new).stat().st_mtime
        with unittest.mock.patch.object(FMT, "date_order", lambda: "DMY"):
            lines = self.run_on([], tab="Notes").lines
            self.assertTrue(any(re.search(rf"Laptop budget +{clock(made)}(\s|$)", l) for l in lines), lines)
            self.assertTrue(any(re.search(r"Printer idea +05/01/25(\s|$)", l) for l in lines), lines)
            app = self.run_on([], [self.select(lambda ui: setattr(ui, "nsel", next(
                i for i, r in enumerate(ui.nrows) if r.get("name") == "Printer idea")))], tab="Notes")
        self.assertTrue(any(re.search(r"Edited +05/01/25(\s|$)", l) for l in app.lines), app.lines)

    def test_the_sessions_tab_keeps_last_active_relative(self):
        lines = self.run_on([], tab="Sessions").lines
        line = next(l for l in lines[:-10] if "Printer fix" in l)
        self.assertIn("5m ago", line)
        self.assertNotRegex(line, r"\d:\d\d (AM|PM)")

    # 2. The sidebar

    def test_moving_down_a_long_list_keeps_the_selected_row_in_the_sidebar(self):
        """More projects than the sidebar has rows: as down moves through them, the selected project's
        name is always in the sidebar, and the list scrolls to show it."""
        now, projects = time.time(), []
        for i in range(40):
            projects.append(self.project(f"proj-{i:02d}", label=f"Project {i:02d}", last=now - i * 60))
        seen = []

        async def look(pilot):
            pilot.app.redraw()
            await pilot.pause()
            seen.append((self.chosen(pilot.app), self.sidebar(screen_text(pilot.app))))
        search = self.select(lambda ui: setattr(ui, "pquery", "proj-"))  # every one: no cap of ten in a search
        steps = [search, self.on_project("proj-00"), look]
        for _ in range(40):
            steps += [self.keys("down"), look]
        self.run_on(projects, steps, size=(120, 30))
        scrolled = False
        for pid, side in seen:
            if not pid.startswith("proj-"):
                continue
            self.assertTrue([l for l in side if f"Project {pid[-2:]}" in l], (pid, side))
            scrolled = scrolled or not any("Project 00" in l for l in side)
        self.assertTrue(scrolled, "the list never scrolled: the fixture needs more projects")
        self.assertEqual(seen[-1][0], "proj-39")  # down reached the last match, at the bottom (a search has no + New project)

    def test_the_name_width_setting_doesnt_change_the_sidebar(self):
        names = ["Printer fleet consolidation for every office", "Laptop refresh"]
        projects = [self.project(f"p{i}", label=n, last=time.time() - i) for i, n in enumerate(names)]
        drawn = {}
        for setting in (None, 16, 60):
            lines = self.run_on(projects, config={} if setting is None else {"name_width": setting}).lines
            drawn[setting] = self.sidebar(lines)
        self.assertEqual(drawn[16], drawn[None])
        self.assertEqual(drawn[60], drawn[None])


@unittest.skipUnless(roost.App, "Textual isn't installed here: run the tests with ~/.roost/venv/bin/python")
class TwoPanes(unittest.TestCase):
    """The Projects tab in two panes, under the search row: the sidebar on the left, SIDEBAR_W columns
    wide, listing project_rows() with a marker at a row's right end, and the selected row's detail pane
    on the right, taking the rest of the width."""
    run_on, on_project, chosen = ProjectRows.run_on, ProjectRows.on_project, staticmethod(ProjectRows.chosen)
    select, keys, sidebar = staticmethod(TextualScreens.select), staticmethod(TextualScreens.keys), staticmethod(ProjectRows.sidebar)
    SIZE = (120, 40)
    DESCRIPTION = ("Consolidate the office printers onto one driver, retire the two oldest models, and move toner "
                   "orders to the new supplier before the quarter closes.")

    def setUp(self):
        """Read from a temp Projects folder: Printer fleet (a running Session and a Handoff: ●), Restore
        drill (a Handoff, no Sessions: ◆), Quiet one (neither), Badge rollout (a Handoff, so ◆ itself, with
        sub-projects Badge Printers, running, and beta, ready: ● ◆ while folded), and Old network, closed."""
        Projects.setUp(self)  # temp Projects folder, read here before the screens' stand-ins replace the readers
        self.addCleanup(Projects.tearDown, self)
        fix, laptop = session("Printer fix", mins=5, tokens=41000), session("Laptop refresh", state="waiting")

        def readme(label, ids=(), status="active", category="", tags=(), description="s"):
            listed = "".join(f"\n  - name: x\n    id: {i}" for i in ids) or " []"
            return (f"---\ndescription: {json.dumps(description)}\nstatus: {status}\n"
                    + (f"category: {category}\n" if category else "") + (f"tags: [{', '.join(tags)}]\n" if tags else "")
                    + f"sessions:{listed}\n---\n\n# {label}\n")
        Projects.project(self, "fleet", readme("Printer fleet", [fix["id"]], "awaiting-vendor", "procurement", ["zq-tag"],
                                               self.DESCRIPTION), handoffs=[TODAY()])
        Projects.project(self, "drill", readme("Restore drill", category="drills"), handoffs=[TODAY()])
        Projects.project(self, "quiet", readme("Quiet one"))
        Projects.project(self, "old", readme("Old network", status="closed"), handoffs=[TODAY()])
        Projects.project(self, "rollout", readme("Badge rollout"), handoffs=[TODAY()])
        alpha = SubProjects.sub(self, "rollout", "alpha", sessions=[("Laptop refresh", laptop["id"])])
        (alpha / "README.md").write_text((alpha / "README.md").read_text().replace("\n# alpha\n", "\n# Badge Printers\n"))
        SubProjects.sub(self, "rollout", "beta", handoffs=[(TODAY(), "x")])
        self.projects = roost.read_projects([fix, laptop])
        TextualScreens.setUp(self)  # self.sessions: Printer fix (idle) and Laptop refresh (waiting), the same ids

    @staticmethod
    def at(lines, text):
        return next((i for i, l in enumerate(lines) if text in l), None)

    @staticmethod
    def grab(seen, label):
        """A step that keeps the screen's lines and its bold runs under label."""
        async def step(pilot):
            pilot.app.redraw()
            await pilot.pause()
            seen[label] = (screen_text(pilot.app), bold_text(pilot.app))
        return step

    @staticmethod
    def detail_name(bold):
        """The bold text the detail pane draws (right of the sidebar): the selected row's name first."""
        return [t for y, x, t in sorted(bold) if x >= roost.SIDEBAR_W]

    @staticmethod
    def row_name(r):
        """The name the detail pane gives a row."""
        return {"project": lambda: roost.label_of(r.item), "sub": lambda: r.item["id"], "head": lambda: r.sec,
                "new": lambda: "+ New project"}[r.kind]()

    def test_the_sidebar_is_on_the_left_and_the_detail_pane_beside_it_under_the_search_row(self):
        W = roost.SIDEBAR_W
        self.assertEqual(W, 34)
        seen = {}
        self.run_on(self.projects, [self.on_project("fleet"), self.grab(seen, "fleet")], size=self.SIZE)
        lines, bold = seen["fleet"]
        search = self.at(lines, "Agent Roost")  # the top bar: with no search, the only row above the content
        self.assertIsNotNone(search, lines)
        side = self.sidebar(lines)
        ys = {}
        for label in ("Printer fleet", "Restore drill", "Badge rollout", "+ New project"):
            ys[label] = self.at(side, label)
            self.assertIsNotNone(ys[label], (label, side))
            self.assertGreater(ys[label], search, label)
            self.assertLess(side[ys[label]].index(label), 8, (label, side[ys[label]]))  # from the left edge
        named = [(y, x) for y, x, t in bold if "Printer fleet" in t and x >= W]
        self.assertTrue(named, f"no bold Printer fleet right of the sidebar: {bold}")
        y, x = named[0]
        self.assertGreater(y, search)
        self.assertLess(y, ys["+ New project"], "the detail pane is beside the sidebar, not under it")
        for label, value in (("Category", "procurement"), ("Tags", "zq-tag"), ("Description", self.DESCRIPTION[:30])):
            i = next((i for i, l in enumerate(lines) if value in l[W:]), None)
            self.assertIsNotNone(i, (value, lines))
            self.assertIn(label, lines[i][W:], lines[i])
            self.assertGreater(i, search)
        line = next(l for l in lines if self.DESCRIPTION[:30] in l)
        self.assertGreater(len(line), 100, "the detail pane takes the rest of the width: " + line)

    def test_the_sidebar_lists_the_rows_with_no_column_titles_or_cells(self):
        W = roost.SIDEBAR_W
        seen = {}
        app = self.run_on(self.projects, [self.on_project("drill"), self.grab(seen, "drill")], size=self.SIZE)
        lines, _ = seen["drill"]
        side = self.sidebar(lines)
        for title in ("NAME", "STATUS", "SESSIONS", "CATEGORY", "TAGS", "LAST WORKED"):
            self.assertFalse([l for l in lines if re.search(rf"\b{title}\b", l)], title)
        for cell in ("awaiting-vendor", "procurement", "zq-tag"):  # Printer fleet's status, category, and tag
            self.assertFalse([l for l in lines if cell in l], cell)
        for l in side:
            self.assertNotRegex(l, r"[○◆●]\d", "no running counts in the sidebar")
            self.assertNotRegex(l, SHORT_DATE)
            self.assertNotRegex(l, r"\d:\d\d (AM|PM)")
        ui, last = app.ui, -1
        rows = ui.project_rows()
        self.assertEqual([r.kind for r in rows if r.kind in ("head", "all", "new")], ["all", "new"])
        for r in rows:  # top to bottom, as project_rows() lists them
            if r.kind == "all":
                want = f"All projects ({len(self.projects)})"
            elif r.kind == "project":
                want = ("▸ " if any(not s["closed"] for s in r.item.get("subs") or []) else "") + roost.label_of(r.item)
            else:
                want = "+ New project"
            y = next((i for i, l in enumerate(side) if i > last and want in l), None)
            self.assertIsNotNone(y, (want, side))
            last = y

    def test_sub_project_rows_show_their_label_indented_under_their_project(self):
        seen = {}
        self.run_on(self.projects, [self.select(lambda ui: ui.subs_open.add("rollout")), self.grab(seen, "open")],
                    size=self.SIZE)
        side = self.sidebar(seen["open"][0])
        p = self.at(side, "▾ Badge rollout")
        self.assertIsNotNone(p, side)
        self.assertTrue(side[p + 1].strip().startswith("Badge Printers"), side[p + 1])
        self.assertTrue(side[p + 2].strip().startswith("beta"), side[p + 2])
        name_x = side[p].index("Badge rollout")
        self.assertGreater(side[p + 1].index("Badge Printers"), name_x)
        self.assertGreater(side[p + 2].index("beta"), name_x)

    def test_a_marker_sits_at_the_right_end_of_its_sidebar_row(self):
        W = roost.SIDEBAR_W
        seen = {}
        app = self.run_on(self.projects, [self.on_project("quiet"), self.grab(seen, "folded"),
                                          self.select(lambda ui: ui.subs_open.add("rollout")), self.grab(seen, "open")],
                          size=self.SIZE)
        cases = {"folded": [("Printer fleet", "●"), ("Restore drill", "◆"), ("Badge rollout", "● ◆"), ("Quiet one", "")],
                 "open": [("Badge rollout", "◆"), ("Badge Printers", "●")]}
        for when, marks in cases.items():
            side = self.sidebar(seen[when][0])
            for label, mark in marks:
                y = self.at(side, label)
                self.assertIsNotNone(y, (label, side))
                row = side[y]
                end = row.rstrip(" │┃▏▕")
                if mark:
                    self.assertTrue(end.endswith(mark), (when, label, row))
                    self.assertFalse(set("●◆") & set(end[:-len(mark)]), (when, label, row))  # only the marker
                    self.assertGreaterEqual(len(end), W - 4, (when, label, row))  # at the right end, not after the name
                else:
                    self.assertFalse(set("●◆") & set(row), (when, label, row))
        beta = next(r for r in app.ui.project_rows() if r.kind == "sub" and r.item["slug"] == "beta")
        self.assertEqual(app.ui.markers(beta), "◆")

    def test_up_and_down_move_the_selection_and_the_detail_pane_shows_the_selected_row(self):
        seen, at = {}, {}

        async def where(pilot):
            at["rows"] = pilot.app.ui.project_rows()
            at["start"] = pilot.app.ui.psel
        self.run_on(self.projects, [self.on_project("fleet"), where, self.grab(seen, "at"), self.keys("down"),
                                    self.grab(seen, "down"), self.keys("up"), self.grab(seen, "up")], size=self.SIZE)
        rows, i = at["rows"], at["start"]
        self.assertEqual(self.detail_name(seen["at"][1])[0], "Printer fleet")
        self.assertEqual(self.detail_name(seen["down"][1])[0], self.row_name(rows[i + 1]))
        self.assertEqual(self.detail_name(seen["up"][1])[0], "Printer fleet")

    def test_right_and_left_open_and_close_a_projects_sub_projects_in_the_sidebar(self):
        seen = {}
        self.run_on(self.projects, [self.on_project("rollout"), self.keys("right"), self.grab(seen, "right"),
                                    self.keys("left"), self.grab(seen, "left")], size=self.SIZE)
        self.assertIsNotNone(self.at(self.sidebar(seen["right"][0]), "Badge Printers"))
        self.assertIsNone(self.at(self.sidebar(seen["left"][0]), "Badge Printers"))

    def test_enter_opens_the_selected_rows_menu(self):
        app = self.run_on(self.projects, [self.on_project("drill"), self.keys("enter")], size=self.SIZE)
        self.assertTrue(app.ui.choosing)
        self.assertEqual(app.ui.choosing["title"], "Restore drill")

    def test_a_click_selects_a_sidebar_row_a_double_click_runs_its_first_action_and_a_right_click_opens_its_menu(self):
        import unittest.mock
        seen = {}
        self.run_on(self.projects, [self.on_project("fleet"), self.grab(seen, "before")], size=self.SIZE)
        y = self.at(self.sidebar(seen["before"][0]), "Restore drill")
        self.assertIsNotNone(y)
        app = self.run_on(self.projects, [self.on_project("fleet"), lambda pilot: pilot.click(offset=(6, y)),
                                          self.grab(seen, "clicked")], size=self.SIZE)
        self.assertEqual(self.chosen(app), "drill")
        self.assertIsNone(app.ui.choosing)
        self.assertEqual(self.detail_name(seen["clicked"][1])[0], "Restore drill")
        ran = []
        with unittest.mock.patch.object(roost.UI, "row_actions", lambda ui, r: [("First", lambda: ran.append(r.item["id"]))]):
            self.run_on(self.projects, [self.on_project("fleet"), lambda pilot: pilot.click(offset=(6, y), times=2)],
                        size=self.SIZE)
        self.assertEqual(ran, ["drill"])
        app = self.run_on(self.projects, [self.on_project("fleet"), lambda pilot: pilot.click(offset=(6, y), button=3)],
                          size=self.SIZE)
        self.assertTrue(app.ui.choosing)
        self.assertEqual(app.ui.choosing["title"], "Restore drill")


class SubProjects(unittest.TestCase):
    """Sub-projects: <project>/sub/<slug>/ with its own README and handoffs/, id <project>/<slug>."""
    def setUp(self):
        Projects.setUp(self)
        self.saved_transcripts = roost.PROJECTS
        roost.PROJECTS = Path(self.tmp.name) / "transcripts"  # session_name() reads here, never the real ones

    def tearDown(self):
        roost.PROJECTS = self.saved_transcripts
        Projects.tearDown(self)

    project = Projects.project

    def sub(self, parent, slug, type="feature", status="active", summary="s", sessions=(), body="", handoffs=()):
        """A sub-project folder made by hand, in the shape roost project new writes."""
        folder = self.root / parent / "sub" / slug
        (folder / "handoffs").mkdir(parents=True)
        listed = "".join(f"\n  - name: {n}\n    id: {i}" for n, i in sessions) or " []"
        (folder / "README.md").write_text(f"---\nsummary: {json.dumps(summary)}\ntype: {type}\nstatus: {status}\n"
                                          f"sessions:{listed}\n---\n\n# {slug}\n\n{body}\n")
        for name, text in handoffs:
            (folder / "handoffs" / name).write_text(text)
        return folder

    def run_cli(self, argv):
        """(exit code, stdout, stderr) of roost project <argv>; an argparse refusal counts as its exit code."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = roost.run_command("project", argv)
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def tree(self):
        base = Path(self.tmp.name)
        return {p: (p.read_bytes() if p.is_file() else None) for p in base.rglob("*") if p.name != "config.json"}

    # roost project new <project>/<slug>

    def test_new_sub_project_writes_its_readme_and_handoffs(self):
        self.project("rollout")
        for slug, kind in [("alpha", "feature"), ("fix-1", "bugfix"), ("2nd-look", "research"), ("tidy", "chore")]:
            code, _, err = self.run_cli(["new", f"rollout/{slug}", "--type", kind, "--summary", f"The {slug}  work."])
            self.assertEqual(code, 0, err)
            folder = self.root / "rollout" / "sub" / slug
            self.assertTrue((folder / "handoffs").is_dir(), slug)
            meta = roost.frontmatter((folder / "README.md").read_text())
            self.assertEqual({k: meta.get(k) for k in ("description", "type", "status", "sessions")},
                             {"description": f"The {slug} work.", "type": kind, "status": "active", "sessions": []})
        self.assertFalse((self.root / "rollout" / "alpha").exists())  # under sub/, not beside it

    def test_new_sub_project_refuses_and_creates_nothing(self):
        self.project("rollout")
        self.assertEqual(self.run_cli(["new", "rollout/alpha", "--type", "feature", "--summary", "First."])[0], 0)
        refused = [
            (["new", "rollout/beta", "--type", "epic", "--summary", "s"], "unknown type"),
            (["new", "rollout/beta", "--summary", "s"], "no --type for a sub-project"),
            (["new", "toplevel", "--type", "feature", "--summary", "s"], "--type for a top-level project"),
            (["new", "nope/beta", "--type", "feature", "--summary", "s"], "parent doesn't exist"),
            (["new", "rollout/Beta", "--type", "feature", "--summary", "s"], "uppercase slug"),
            (["new", "rollout/-beta", "--type", "feature", "--summary", "s"], "slug starting with a hyphen"),
            (["new", "rollout/be_ta", "--type", "feature", "--summary", "s"], "underscore in slug"),
            (["new", "rollout/be.ta", "--type", "feature", "--summary", "s"], "dot in slug"),
            (["new", "rollout/alpha/deeper", "--type", "feature", "--summary", "s"], "sub-project of a sub-project"),
            (["new", "../beta", "--type", "feature", "--summary", "s"], ".. as the project"),
            (["new", "rollout/..", "--type", "feature", "--summary", "s"], ".. as the slug"),
            (["new", "rollout/../beta", "--type", "feature", "--summary", "s"], ".. in the middle"),
            (["new", "rollout/alpha", "--type", "bugfix", "--summary", "Again."], "already exists"),
        ]
        before = self.tree()
        for argv, why in refused:
            code, _, err = self.run_cli(argv)
            self.assertNotIn(code, (0, None), why)
            self.assertTrue(err.strip(), why)
            self.assertEqual(self.tree(), before, why)
        self.assertEqual(roost.frontmatter((self.root / "rollout" / "sub" / "alpha" / "README.md").read_text())["description"],
                         "First.")

    def test_a_top_level_project_new_still_needs_no_type(self):
        self.project("rollout")
        self.assertEqual(self.run_cli(["new", "rollout/alpha", "--type", "chore", "--summary", "Sub."])[0], 0)
        code, _, err = self.run_cli(["new", "plain", "--summary", "Top."])
        self.assertEqual(code, 0, err)
        self.assertNotIn("type", roost.frontmatter((self.root / "plain" / "README.md").read_text()))

    # finding and reading them

    def test_find_project_finds_a_sub_project_by_its_id(self):
        self.project("rollout")
        folder = self.sub("rollout", "alpha")
        self.assertEqual(roost.find_project("rollout/alpha"), folder)
        for bad in ["rollout/nope", "rollout/../rollout", "rollout/..", "../rollout/alpha", "rollout/alpha/x", "rollout/sub/alpha"]:
            with self.assertRaises(ValueError, msg=bad):
                roost.find_project(bad)

    def test_read_subprojects_lists_each_sub_project_folder(self):
        parent = self.project("rollout")
        a = self.sub("rollout", "alpha", "feature", "active", "Badge printers.", [("One", "cc3d0000-1111")])
        b = self.sub("rollout", "beta", "bugfix", "done", "Toner alerts.")
        (parent / "sub" / "_draft").mkdir()
        (parent / "sub" / ".hidden").mkdir()
        subs = {s["id"]: s for s in roost.read_subprojects(parent)}
        self.assertEqual(set(subs), {"rollout/alpha", "rollout/beta"})
        want = {"rollout/alpha": ("alpha", "feature", "active", "Badge printers.", a, ["cc3d0000-1111"]),
                "rollout/beta": ("beta", "bugfix", "done", "Toner alerts.", b, [])}
        for sid, (slug, kind, status, summary, folder, sessions) in want.items():
            s = subs[sid]
            self.assertEqual((s["slug"], s["type"], s["status"], s["summary"], Path(s["path"]), s["sessions"], s["parent"]),
                             (slug, kind, status, summary, folder, sessions, "rollout"))
        self.assertEqual(roost.read_subprojects(self.project("bare")), [])

    def test_sub_projects_never_appear_as_projects(self):
        self.project("rollout")
        self.sub("rollout", "alpha", sessions=[("One", "cc3d0000-1111")])
        self.assertEqual([p["id"] for p in roost.read_projects()], ["rollout"])
        self.assertNotIn("cc3d0000-1111", roost.read_projects()[0]["sessions"])

    # roost project brief <project>/<slug>

    def brief_fixture(self):
        self.project("rollout", "---\nsummary: \"PARENTSUM wide rollout.\"\nstatus: active\nsessions: []\n---\n\n"
                                "# Rollout\n\nPARENTBODY notes.\n")
        (self.root / "rollout" / "handoffs" / "2026-10-05-0900-aaaaaaaa.md").write_text("PARENTHANDOFF text\n")
        folder = self.sub("rollout", "alpha", "feature", "active", "SUBSUM badge printers.", body="SUBBODY notes.",
                          handoffs=[("2026-10-06-0900-bbbbbbbb.md", "SUBHANDOFF text\n")])
        self.sub("rollout", "beta", "bugfix", "blocked", "BETASUM toner alerts.")
        self.sub("rollout", "gamma", "research", "done", "GAMMASUM old survey.")
        return folder

    def test_sub_project_brief_is_parent_then_sub_then_handoffs_then_open_siblings(self):
        text = roost.brief(self.brief_fixture())
        context = next((l for l in text.splitlines() if l.startswith("#") and "context" in l.lower() and "rollout" in l), None)
        self.assertIsNotNone(context, "a heading naming rollout as the wider context")
        order = [text.index(context), text.index("PARENTSUM"), text.index("PARENTBODY"), text.index("SUBSUM"),
                 text.index("SUBBODY"), text.index("SUBHANDOFF"), text.index("BETASUM")]
        self.assertEqual(order, sorted(order))
        beta = next(l for l in text.splitlines() if "BETASUM" in l)
        for part in ("beta", "bugfix", "blocked"):
            self.assertIn(part, beta)
        self.assertNotIn("PARENTHANDOFF", text)
        self.assertNotIn("GAMMASUM", text)  # done siblings aren't listed
        self.assertNotIn("SUBSUM", text[text.index("SUBHANDOFF"):])  # nor the sub-project itself

    def test_sub_project_brief_command(self):
        self.brief_fixture()
        code, out, err = self.run_cli(["brief", "rollout/alpha"])
        self.assertEqual(code, 0, err)
        self.assertLess(out.index("PARENTSUM"), out.index("SUBSUM"))
        self.assertLess(out.index("SUBHANDOFF"), out.index("BETASUM"))

    def test_brief_session_lists_it_in_the_sub_project_and_off_the_parent(self):
        parent = self.project("rollout")  # README lists aa1b9a71-0000 and bb2c0000-0000
        folder = self.sub("rollout", "alpha")
        code, _, err = self.run_cli(["brief", "rollout/alpha", "--session", "bb2c0000-0000"])
        self.assertEqual(code, 0, err)
        self.assertEqual([x["id"] for x in roost.frontmatter((folder / "README.md").read_text())["sessions"]],
                         ["bb2c0000-0000"])
        self.assertEqual((parent / "README.md").read_text(), README.replace("  - name: Second look\n    id: bb2c0000-0000\n", ""))

    # roost project status <project>/<slug>

    def test_status_of_a_sub_project_sets_and_prints_only_its_own(self):
        parent = self.project("rollout")
        folder = self.sub("rollout", "alpha")
        before = (parent / "README.md").read_text()
        code, _, err = self.run_cli(["status", "rollout/alpha", "done"])
        self.assertEqual(code, 0, err)
        self.assertEqual(roost.frontmatter((folder / "README.md").read_text())["status"], "done")
        self.assertEqual((parent / "README.md").read_text(), before)
        code, out, err = self.run_cli(["status", "rollout/alpha"])
        self.assertEqual((code, out), (0, "done\n"), err)

    # roost project list

    def test_list_shows_open_sub_projects_indented_under_their_parent(self):
        self.project("rollout")
        self.project("other", "---\nsummary: Other work.\nstatus: active\nsessions: []\n---\n")
        self.sub("rollout", "alpha", "feature", "active", "Badge printers.")
        self.sub("rollout", "beta", "bugfix", "done", "Toner alerts.")
        for flags, subs in [([], ["rollout/alpha"]), (["--all"], ["rollout/alpha", "rollout/beta"])]:
            code, out, err = self.run_cli(["list"] + flags)
            self.assertEqual(code, 0, err)
            lines = out.splitlines()
            at = next(i for i, l in enumerate(lines) if l.startswith("rollout "))
            after = lines[at + 1:at + 1 + len(subs)]
            self.assertTrue(all(l[:1].isspace() for l in after), after)
            self.assertEqual(sorted(next(s for s in subs if s in l) for l in after if any(s in l for s in subs)), subs)
            if not flags:
                self.assertNotIn("rollout/beta", out)


class SubProjectRows(unittest.TestCase):
    """Sub-projects on the Projects tab: rows under their project, folded until opened, with a panel,
    actions, a New sub-project flow, and search."""
    setUp, tearDown, project, sub = SubProjects.setUp, SubProjects.tearDown, Projects.project, SubProjects.sub
    shown = Projects.shown

    def ui(self):
        ui = Projects.ui(self)
        ui.term = "ghostty"
        return ui

    def fixture(self):
        """Project rollout (worked on today) with open alpha and beta, and done gamma; project other with none."""
        self.project("rollout", handoffs=[time.strftime("%Y-%m-%d-%H%M") + "-aaaaaaaa.md"])
        self.project("other", "---\nsummary: Other work.\nstatus: active\nsessions: []\n---\n",
                     handoffs=[time.strftime("%Y-%m-%d-%H%M") + "-bbbbbbbb.md"])
        self.sub("rollout", "beta", "bugfix", "blocked", "Toner alerts.")
        self.sub("rollout", "alpha", "feature", "active", "Badge printers.")
        self.sub("rollout", "gamma", "research", "done", "Old survey.")
        ui = self.ui()
        return ui

    def select(self, ui, kind, rid):
        rows = ui.project_rows()
        at = [i for i, r in enumerate(rows) if r.kind == kind and r.item["id"] == rid]
        self.assertTrue(at, f"no {kind} row {rid} in {self.shown(ui)}")
        ui.psel = at[0]
        return rows

    def after(self, ui, rid):
        """The (kind, id) rows right after project rid, up to the next project, header, or + New project."""
        rows = self.shown(ui)
        at = rows.index(("project", rid))
        out = []
        for kind, i in rows[at + 1:]:
            if kind in ("project", "head", "all", "new"):
                break
            out.append((kind, i))
        return out

    def capture_start(self):
        """Patch terminals.start_session; returns the list of the args each call got."""
        import unittest.mock
        calls = []
        patcher = unittest.mock.patch.object(importlib.import_module("roost.core.terminals"), "start_session",
                                             lambda *a, **kw: calls.append(a) or "Started it")
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def test_sub_projects_are_folded_until_right_and_left_folds_them_again(self):
        ui = self.fixture()
        self.assertEqual(self.after(ui, "rollout"), [])  # folded by default
        self.select(ui, "project", "rollout")
        ui.keys_projects("right", ui.project_rows())
        self.assertEqual(self.after(ui, "rollout"), [("sub", "rollout/alpha"), ("sub", "rollout/beta")])  # slug order, no done one
        sub_rows = [r for r in ui.project_rows() if r.kind == "sub"]
        self.assertEqual([(r.item["slug"], r.item["type"]) for r in sub_rows], [("alpha", "feature"), ("beta", "bugfix")])
        ui.keys_projects("left", ui.project_rows())  # on the project
        self.assertEqual(self.after(ui, "rollout"), [])
        self.assertEqual(ui.project_rows()[ui.psel].item["id"], "rollout")
        ui.keys_projects("right", ui.project_rows())
        self.select(ui, "sub", "rollout/beta")
        ui.keys_projects("left", ui.project_rows())  # on one of its sub rows
        self.assertEqual(self.after(ui, "rollout"), [])
        r = ui.project_rows()[ui.psel]
        self.assertEqual((r.kind, r.item["id"]), ("project", "rollout"))

    def test_a_projects_panel_counts_its_open_sub_projects(self):
        ui = self.fixture()
        rows = self.select(ui, "project", "rollout")
        _, fields = ui.project_panel(rows, 40, 120)
        subs = [str(v) for label, v in fields if label == "Sub-projects"]
        self.assertEqual(len(subs), 1, fields)
        self.assertIn("2 open", subs[0])
        rows = self.select(ui, "project", "other")
        _, fields = ui.project_panel(rows, 40, 120)
        self.assertNotIn("Sub-projects", [label for label, _ in fields])

    def test_a_sub_rows_panel_names_its_id_type_and_status(self):
        ui = self.fixture()
        self.select(ui, "project", "rollout")
        ui.keys_projects("right", ui.project_rows())
        rows = self.select(ui, "sub", "rollout/beta")
        name, fields = ui.project_panel(rows, 40, 120)
        self.assertEqual(name, "rollout/beta")
        got = {label: str(v) for label, v in fields}
        self.assertIn("bugfix", got.get("Type", ""), fields)
        self.assertNotIn("Status", got, fields)  # its status is on the name line

    def test_a_sub_rows_actions_start_a_session_in_it(self):
        calls = self.capture_start()
        ui = self.fixture()
        self.select(ui, "project", "rollout")
        ui.keys_projects("right", ui.project_rows())
        rows = self.select(ui, "sub", "rollout/alpha")
        acts = labels(ui, ui.keys_projects, rows)
        for want in ("Start a Session", "Show its folder in Finder"):
            self.assertTrue([l for l in acts if l.startswith(want)], (want, acts))
        self.assertNotIn("New sub-project", acts)  # only in its parent's actions
        act(ui, ui.keys_projects, ui.project_rows(), "Start a Session")
        self.assertEqual([c[0] for c in calls], ["rollout/alpha"])

    def test_a_projects_actions_offer_new_sub_project_and_start_one_in_a_sub_project(self):
        calls = self.capture_start()
        ui = self.fixture()
        rows = self.select(ui, "project", "other")
        acts = labels(ui, ui.keys_projects, rows)
        self.assertIn("New sub-project", acts)
        self.assertFalse([l for l in acts if l.startswith("Start a Session in a sub-project")], acts)  # none open
        rows = self.select(ui, "project", "rollout")
        acts = labels(ui, ui.keys_projects, rows)
        self.assertIn("New sub-project", acts)
        self.assertIn("Start a Session in a sub-project", acts)
        act(ui, ui.keys_projects, rows, "Start a Session in a sub-project")
        opts = [l for l, _ in ui.choice_options()]
        for slug in ("alpha", "beta"):
            self.assertEqual(len([l for l in opts if slug in l]), 1, (slug, opts))
        self.assertFalse([l for l in opts if "gamma" in l], opts)  # done: not offered
        ui.choosing["sel"] = next(i for i, l in enumerate(opts) if "beta" in l)
        ui.keys_choice("enter")
        self.assertEqual([c[0] for c in calls], ["rollout/beta"])

    def test_new_sub_project_picks_a_type_then_asks_its_slug_and_summary(self):
        ui = self.fixture()
        self.assertIn("New sub-project", labels(ui, ui.keys_projects, self.select(ui, "project", "rollout")))
        act(ui, ui.keys_projects, self.select(ui, "project", "rollout"), "New sub-project")
        self.assertIsNotNone(ui.choosing)
        opts = [l for l, _ in ui.choice_options()]
        self.assertEqual(len(opts), 4, opts)
        for kind in roost.SUB_TYPES:
            self.assertEqual(len([l for l in opts if kind in l]), 1, (kind, opts))
        ui.choosing["sel"] = next(i for i, l in enumerate(opts) if "chore" in l)
        ui.keys_choice("enter")
        self.assertIsNone(ui.choosing)
        self.assertIsNotNone(ui.asking)  # its slug
        typed(ui, "\x15tidy-cables\n")
        self.assertIsNotNone(ui.asking)  # its summary
        typed(ui, "\x15Tidy the cables.\n")
        self.assertIsNone(ui.asking)
        readme = self.root / "rollout" / "sub" / "tidy-cables" / "README.md"
        self.assertTrue(readme.is_file())
        meta = roost.frontmatter(readme.read_text())
        self.assertEqual((meta.get("type"), meta.get("description")), ("chore", "Tidy the cables."))
        self.assertTrue(ui.msg.startswith("Created"), ui.msg)
        self.assertIn(("sub", "rollout/tidy-cables"), self.after(ui, "rollout"))  # its parent is unfolded
        r = ui.project_rows()[ui.psel]
        self.assertEqual((r.kind, r.item["id"]), ("sub", "rollout/tidy-cables"))

    def test_new_sub_project_with_a_bad_slug_creates_nothing(self):
        ui = self.fixture()
        before = sorted(p.name for p in (self.root / "rollout" / "sub").iterdir())
        self.assertIn("New sub-project", labels(ui, ui.keys_projects, self.select(ui, "project", "rollout")))
        act(ui, ui.keys_projects, self.select(ui, "project", "rollout"), "New sub-project")
        opts = [l for l, _ in ui.choice_options()]
        ui.choosing["sel"] = next(i for i, l in enumerate(opts) if "feature" in l)
        ui.keys_choice("enter")
        typed(ui, "\x15Bad Slug\n")
        if ui.asking:  # refused at the slug or after the summary: either way, nothing is made
            typed(ui, "\x15A summary.\n")
        self.assertEqual(sorted(p.name for p in (self.root / "rollout" / "sub").iterdir()), before)
        self.assertTrue(ui.msg.startswith("Couldn't create it"), ui.msg)

    def test_search_shows_a_matching_sub_project_under_its_parent(self):
        ui = self.fixture()
        ui.notes = []
        for q, want in [("alpha", "rollout/alpha"), ("TONER", "rollout/beta"), ("type:bugfix", "rollout/beta")]:
            ui.pquery = q
            rows = [x for x in self.shown(ui) if x[0] != "head"]
            self.assertEqual(rows, [("project", "rollout"), ("sub", want)], q)


class ProjectMenuGroups(unittest.TestCase):
    """A project's and a sub-project's actions, in groups split by dividers (| here): work on it,
    then (a project's) New sub-project, then organize it, then delete it."""
    setUp, tearDown, project, sub = SubProjects.setUp, SubProjects.tearDown, Projects.project, SubProjects.sub
    ui, select, shown = SubProjectRows.ui, SubProjectRows.select, Projects.shown

    def fixture(self):
        """Project rollout (listing aa1b9a71-0000, a live Session) with open alpha (listing cc3d0000-1111,
        another) and beta; project legacy, not in roost's shape yet (no handoffs/) with a due _archive folder."""
        self.project("rollout", handoffs=[time.strftime("%Y-%m-%d-%H%M") + "-aaaaaaaa.md"])
        self.sub("rollout", "alpha", "feature", "active", "Badge printers.", [("Sub one", "cc3d0000-1111")])
        self.sub("rollout", "beta", "bugfix", "active", "Toner alerts.")
        legacy = self.root / "legacy"
        (legacy / "_archive" / "2020-01-01").mkdir(parents=True)
        (legacy / "README.md").write_text("---\nsummary: Older work.\nstatus: active\nsessions: []\n---\n\n# Legacy\n")
        ui = self.ui()
        ui.sessions = [dict(session("Password Manager Review"), id="aa1b9a71-0000"), dict(session("Sub one"), id="cc3d0000-1111")]
        ui.load_projects()
        return ui

    def test_a_projects_actions_in_groups(self):
        ui = self.fixture()
        rows = self.select(ui, "project", "rollout")
        self.assertEqual(labels(ui, ui.keys_projects, rows),
                         ["Start a Session", "Start a Session in a sub-project", "Show its Sessions", DIV,
                          "New sub-project", DIV,
                          "Set its category", "Edit its tags", "Edit its description", "Rename", "Show its folder in Finder", DIV,
                          "Delete"])

    def test_adopt_and_a_due_archive_go_in_the_delete_group(self):
        ui = self.fixture()
        ui.folded = set()  # no work on it yet: it's under Inactive
        rows = self.select(ui, "project", "legacy")
        self.assertEqual(labels(ui, ui.keys_projects, rows),
                         ["Start a Session", DIV,
                          "New sub-project", DIV,
                          "Set its category", "Edit its tags", "Edit its description", "Rename", "Show its folder in Finder", DIV,
                          "Adopt it: plan its README and files, then write its Handoffs",
                          "Move _archive/2020-01-01 to the Trash", "Delete"])

    def test_a_double_click_runs_the_first_real_action_when_the_first_group_is_empty(self):
        """A closed project with no open sub-projects and no linked Session has nothing to work on, so its
        actions start with the New sub-project group: a double-click runs that, never a divider."""
        self.project("shut", "---\nsummary: Finished work.\nstatus: closed\nsessions: []\n---\n\n# Shut\n")
        ui = self.ui()
        ui.pquery = "shut"  # closed: only a search lists it in the sidebar
        rows = self.select(ui, "project", "shut")
        self.assertEqual(labels(ui, ui.keys_projects, rows)[0], "New sub-project")
        ui.prow_y = {6: ui.psel}  # the project's row, as draw left it
        ui.keys_projects(("mouse", 20, 6, roost.curses.BUTTON1_DOUBLE_CLICKED), ui.project_rows())
        self.assertTrue(ui.choosing and ui.choosing["title"].startswith("New sub-project of"), ui.choosing)

    def test_a_sub_projects_actions_in_groups(self):
        ui = self.fixture()
        self.select(ui, "project", "rollout")
        ui.keys_projects("right", ui.project_rows())
        rows = self.select(ui, "sub", "rollout/alpha")
        self.assertEqual(labels(ui, ui.keys_projects, rows),
                         ["Start a Session", "Show its Sessions", DIV,
                          "Edit its tags", "Edit its description", "Rename", "Show its folder in Finder", DIV,
                          "Delete"])


    def test_no_action_label_has_an_ellipsis(self):
        """A menu label is just its action's name: none ends in, or holds, "…" (a prompt or confirm may follow
        or not). Rows: a project with open sub-projects and a linked Session, one with a due _archive that
        isn't adopted, and a sub-project."""
        ui = self.fixture()
        ui.folded = set()
        seen = {}
        seen["rollout"] = labels(ui, ui.keys_projects, self.select(ui, "project", "rollout"))
        seen["legacy"] = labels(ui, ui.keys_projects, self.select(ui, "project", "legacy"))
        self.select(ui, "project", "rollout")
        ui.keys_projects("right", ui.project_rows())
        seen["rollout/alpha"] = labels(ui, ui.keys_projects, self.select(ui, "sub", "rollout/alpha"))
        for row, acts in seen.items():
            real = [a for a in acts if a != DIV]
            self.assertTrue(real, row)
            self.assertEqual([a for a in real if "…" in a], [], row)

class ProjectDelete(unittest.TestCase):
    """Delete on a project or sub-project: a confirm box, then the id typed exactly, then its folder goes
    to the Trash (through adopt.trash, patched here to move it into a temp folder)."""
    project, sub, shown, select, after = Projects.project, SubProjects.sub, Projects.shown, SubProjectRows.select, SubProjectRows.after
    ui = SubProjectRows.ui

    def setUp(self):
        import shutil
        import unittest.mock
        SubProjects.setUp(self)
        self.bin = Path(self.tmp.name) / "trash-bin"
        self.bin.mkdir()
        self.trashed = []

        def trash(path):
            self.trashed.append(Path(path))
            dst = self.bin / f"{len(self.trashed)}-{Path(path).name}"
            shutil.move(str(path), str(dst))
            return dst
        patcher = unittest.mock.patch.object(importlib.import_module("roost.core.adopt"), "trash", trash)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        SubProjects.tearDown(self)

    @staticmethod
    def answer(ui, text):
        """Enter text at the open one-line prompt, as enter does."""
        a, ui.asking = ui.asking, None
        return a["done"](text)

    def fresh(self):
        """A new temp Projects folder (and Trash stand-in), for each case of a loop."""
        self.doCleanups()
        self.tearDown()
        self.setUp()

    def open_delete(self, ui, kind, rid):
        """Select the row and choose Delete from its actions."""
        if kind == "sub":
            self.select(ui, "project", rid.split("/")[0])
            if ("sub", rid) not in self.after(ui, rid.split("/")[0]):
                ui.keys_projects("right", ui.project_rows())
        act(ui, ui.keys_projects, self.select(ui, kind, rid), "Delete")

    def ids(self, ui):
        return [r.item["id"] for r in ui.project_rows() if r.kind in ("project", "sub")]

    def fixture(self):
        ui = SubProjectRows.fixture(self)
        ui.DIM = ui.GREEN = ui.YELLOW = ui.RED = 0  # confirm_text's colors, set by curses in the app
        return ui

    def test_a_real_projects_actions_include_delete(self):
        ui = self.fixture()
        self.assertIn("Delete", labels(ui, ui.keys_projects, self.select(ui, "project", "rollout")))
        p = next(x for x in ui.projects if x["id"] == "rollout")
        demo_like = dict(p, path=str(Path(self.tmp.name) / "not-a-folder"))
        self.assertNotIn("Delete", [l for l, _ in ui.project_actions(demo_like)])  # like Rename: real folders only

    def test_a_sub_projects_actions_include_delete(self):
        ui = self.fixture()
        self.select(ui, "project", "rollout")
        ui.keys_projects("right", ui.project_rows())
        self.assertIn("Delete", labels(ui, ui.keys_projects, self.select(ui, "sub", "rollout/alpha")))

    def test_delete_on_a_project_opens_a_confirm_naming_it_and_its_sub_projects(self):
        ui = self.fixture()
        self.open_delete(ui, "project", "rollout")
        self.assertIsNotNone(ui.confirm)
        self.assertIsNone(ui.asking)
        title, body, _, _ = ui.confirm_text()
        self.assertIn("rollout", title)
        self.assertIn("Trash", title)
        text = " ".join(body)
        self.assertIn("3", text)  # alpha, beta, and done gamma: every sub/ folder goes with it
        self.assertIn("sub-project", text)
        self.assertTrue((self.root / "rollout").is_dir())
        self.assertEqual(self.trashed, [])

    def test_delete_on_a_sub_project_opens_a_confirm_naming_it(self):
        ui = self.fixture()
        self.open_delete(ui, "sub", "rollout/alpha")
        self.assertIsNotNone(ui.confirm)
        title, _, _, _ = ui.confirm_text()
        self.assertIn("rollout/alpha", title)
        self.assertIn("Trash", title)
        self.assertTrue((self.root / "rollout" / "sub" / "alpha").is_dir())
        self.assertEqual(self.trashed, [])

    def test_any_key_but_y_at_the_confirm_cancels(self):
        for key in ("n", "escape", "enter", "x"):
            self.fresh()
            ui = self.fixture()
            self.open_delete(ui, "project", "rollout")
            ui.keys_confirm(key)
            self.assertIsNone(ui.asking, key)
            self.assertIn("Cancelled", ui.msg, key)
            self.assertTrue((self.root / "rollout").is_dir(), key)
            self.assertEqual(self.trashed, [], key)

    def test_y_at_the_confirm_asks_for_the_id_typed(self):
        for key, kind, rid in (("y", "project", "rollout"), ("Y", "sub", "rollout/beta")):
            self.fresh()
            ui = self.fixture()
            self.open_delete(ui, kind, rid)
            ui.keys_confirm(key)
            self.assertIsNotNone(ui.asking, (key, rid))
            self.assertEqual(ui.asking["text"], "", rid)
            self.assertIn(rid, ui.asking["title"] or "", rid)
            self.assertTrue((self.root / "rollout" / "sub" / "beta").is_dir())
            self.assertEqual(self.trashed, [], rid)

    def test_typing_anything_but_the_exact_id_cancels(self):
        for kind, rid, wrong in (("project", "rollout", "other"), ("project", "rollout", "Rollout"), ("project", "rollout", ""),
                                 ("sub", "rollout/alpha", "alpha"), ("sub", "rollout/alpha", "ROLLOUT/ALPHA"), ("sub", "rollout/alpha", "")):
            self.fresh()
            ui = self.fixture()
            self.open_delete(ui, kind, rid)
            ui.keys_confirm("y")
            self.answer(ui, wrong)
            self.assertEqual(self.trashed, [], (rid, wrong))
            self.assertTrue((self.root / "rollout" / "sub" / "alpha").is_dir(), (rid, wrong))
            self.assertRegex(ui.msg, r"(?i)match|cancel", (rid, wrong))
            self.assertIn("rollout", self.ids(ui), (rid, wrong))

    def test_the_exact_id_moves_a_project_and_its_sub_projects_to_the_trash(self):
        ui = self.fixture()
        folder = self.root / "rollout"
        self.open_delete(ui, "project", "rollout")
        ui.keys_confirm("y")
        self.answer(ui, "  rollout \n")
        self.assertFalse(folder.exists())
        self.assertIn(folder, self.trashed)
        moved = sorted(p.name for d in self.bin.iterdir() for p in (d / "sub").iterdir()) if self.bin.exists() else []
        self.assertEqual(moved, ["alpha", "beta", "gamma"])  # its sub/ folders went with it
        self.assertIn("Trash", ui.msg)
        self.assertIn("rollout", ui.msg)
        self.assertNotIn("rollout", self.ids(ui))
        self.assertFalse([i for i in self.ids(ui) if i.startswith("rollout/")])
        self.assertIn("other", self.ids(ui))
        self.assertTrue((self.root / "other").is_dir())

    def test_the_exact_id_moves_only_that_sub_project_to_the_trash(self):
        ui = self.fixture()
        folder = self.root / "rollout" / "sub" / "alpha"
        self.open_delete(ui, "sub", "rollout/alpha")
        ui.keys_confirm("y")
        self.answer(ui, " rollout/alpha ")
        self.assertFalse(folder.exists())
        self.assertEqual(self.trashed, [folder])
        self.assertIn("Trash", ui.msg)
        self.assertIn("rollout/alpha", ui.msg)
        for kept in ("rollout", "rollout/sub/beta", "rollout/sub/gamma"):
            self.assertTrue((self.root / kept).is_dir(), kept)
        self.assertTrue((self.root / "rollout" / "README.md").is_file())
        if "rollout" not in ui.subs_open:
            self.select(ui, "project", "rollout")
            ui.keys_projects("right", ui.project_rows())
        self.assertNotIn("rollout/alpha", self.ids(ui))
        self.assertIn("rollout", self.ids(ui))
        self.assertIn("rollout/beta", self.ids(ui))


class SubProjectRename(unittest.TestCase):
    """Rename on a sub-project: a Label prompt starting with its heading (its slug by default), then a Folder
    name prompt starting with its slug; a new folder name renames <project>/sub/<old> to sub/<new>."""
    setUp, tearDown = SubProjects.setUp, SubProjects.tearDown
    project, sub, shown, select, after = Projects.project, SubProjects.sub, Projects.shown, SubProjectRows.select, SubProjectRows.after
    ui, fixture = SubProjectRows.ui, SubProjectRows.fixture
    answer, ids = staticmethod(ProjectDelete.answer), ProjectDelete.ids

    def fresh(self):
        """A new temp Projects folder, for each case of a loop."""
        self.tearDown()
        self.setUp()

    def open_rename(self, ui, rid="rollout/alpha"):
        if "rollout" not in ui.subs_open:
            self.select(ui, "project", "rollout")
            ui.keys_projects("right", ui.project_rows())
        act(ui, ui.keys_projects, self.select(ui, "sub", rid), "Rename")

    def test_a_sub_projects_actions_include_rename(self):
        ui = self.fixture()
        self.select(ui, "project", "rollout")
        ui.keys_projects("right", ui.project_rows())
        self.assertIn("Rename", labels(ui, ui.keys_projects, self.select(ui, "sub", "rollout/alpha")))

    def test_rename_starts_with_the_slug_and_renames_its_folder(self):
        ui = self.fixture()
        self.open_rename(ui)
        self.assertIsNotNone(ui.asking)
        self.assertEqual(ui.asking["text"], "alpha")  # its label: its heading, which is its slug
        self.answer(ui, "alpha")
        self.assertIsNotNone(ui.asking)
        self.assertEqual(ui.asking["text"], "alpha")  # its folder's name
        self.answer(ui, "delta")
        sub = self.root / "rollout" / "sub"
        self.assertFalse((sub / "alpha").exists())
        self.assertTrue((sub / "delta").is_dir())
        self.assertTrue((self.root / "rollout" / "README.md").is_file())
        if "rollout" not in ui.subs_open:
            self.select(ui, "project", "rollout")
            ui.keys_projects("right", ui.project_rows())
        self.assertIn("rollout/delta", self.ids(ui))
        self.assertNotIn("rollout/alpha", self.ids(ui))
        self.assertIn("rollout", self.ids(ui))

    def test_rename_rewrites_a_heading_that_was_the_old_slug(self):
        ui = self.fixture()
        self.open_rename(ui)
        self.answer(ui, "alpha")  # its label unchanged: the heading follows the folder
        self.answer(ui, "delta")
        text = (self.root / "rollout" / "sub" / "delta" / "README.md").read_text()
        self.assertIn("\n# delta\n", text)
        self.assertNotIn("# alpha", text)

    def test_rename_refuses_a_bad_slug_or_a_siblings_name(self):
        for bad in ("Bad Name", "beta"):
            self.fresh()
            ui = self.fixture()
            before = sorted(p.name for p in (self.root / "rollout" / "sub").iterdir())
            self.open_rename(ui)
            self.answer(ui, "alpha")
            self.answer(ui, bad)
            self.assertTrue(ui.msg.startswith("Couldn't rename it"), (bad, ui.msg))
            self.assertEqual(sorted(p.name for p in (self.root / "rollout" / "sub").iterdir()), before, bad)
            self.assertIn("\n# alpha\n", (self.root / "rollout" / "sub" / "alpha" / "README.md").read_text(), bad)
            self.assertIn("\n# beta\n", (self.root / "rollout" / "sub" / "beta" / "README.md").read_text(), bad)

    def test_a_new_label_moves_a_folder_named_after_the_old_one(self):
        """Its folder name was slug(its label): a new label starts the Folder name prompt at slug(new label)."""
        ui = self.fixture()
        self.open_rename(ui)
        self.answer(ui, "Badge Printers")
        self.assertIn("folder name", (ui.asking or {}).get("title", "").lower(), ui.asking)
        self.assertEqual(ui.asking["text"], "badge-printers")
        self.answer(ui, "badge-printers")
        sub = self.root / "rollout" / "sub"
        self.assertFalse((sub / "alpha").exists())
        self.assertEqual(roost.heading((sub / "badge-printers" / "README.md").read_text()), "Badge Printers")

    def test_a_new_label_alone_changes_only_its_heading(self):
        ui = self.fixture()
        before = (self.root / "rollout" / "sub" / "alpha" / "README.md").read_text()
        self.open_rename(ui)
        self.answer(ui, "Badge Printers")
        self.answer(ui, "alpha")  # the folder name put back as it was
        self.assertEqual((self.root / "rollout" / "sub" / "alpha" / "README.md").read_text(),
                         before.replace("\n# alpha\n", "\n# Badge Printers\n"))
        self.assertEqual(sorted(p.name for p in (self.root / "rollout" / "sub").iterdir()), ["alpha", "beta", "gamma"])

    def test_rename_refuses_while_a_linked_session_is_running(self):
        self.project("rollout", handoffs=[time.strftime("%Y-%m-%d-%H%M") + "-aaaaaaaa.md"])
        self.sub("rollout", "alpha", sessions=[("Live", "cc3d0000-0000")])
        ui = self.ui()
        ui.sessions = [dict(session("Live", state="idle"), id="cc3d0000-0000")]
        ui.load_projects()
        self.open_rename(ui)
        self.assertIsNone(ui.asking)
        self.assertIn("Live is running in it", ui.msg)
        self.assertTrue((self.root / "rollout" / "sub" / "alpha").is_dir())


TODAY = lambda: time.strftime("%Y-%m-%d-%H%M") + "-aaaaaaaa.md"  # a Handoff written now: its project is Recent
SUMMARY_LINE = re.compile(r"(?m)^summary\s*:")
DESCRIPTION_LINE = re.compile(r"(?m)^description\s*:")


class Descriptions(unittest.TestCase):
    """A project's (and sub-project's) description: the README's description: line, read with a legacy
    summary: line as the fallback, optional when it's made, and set by set_description or roost project description."""
    setUp, tearDown, project, sub = SubProjects.setUp, SubProjects.tearDown, Projects.project, SubProjects.sub
    run_cli = SubProjects.run_cli

    def readme(self, folder):
        return (Path(folder) / "README.md").read_text()

    def test_read_takes_description_then_a_legacy_summary(self):
        self.project("new", '---\ndescription: "Current text."\nstatus: active\nsessions: []\n---\n\n# New\n')
        self.project("old", '---\nsummary: "Legacy text."\nstatus: active\nsessions: []\n---\n\n# Old\n')
        self.project("both", '---\ndescription: "Wins."\nsummary: "Loses."\nsessions: []\n---\n')
        self.project("none", "---\nstatus: active\nsessions: []\n---\n")
        self.assertEqual({p["id"]: p["summary"] for p in roost.read_projects()},
                         {"new": "Current text.", "old": "Legacy text.", "both": "Wins.", "none": ""})
        self.assertEqual(roost.read_project(self.root / "new")["summary"], "Current text.")
        self.sub("new", "alpha", summary="Legacy sub.")
        beta = self.sub("new", "beta")
        (beta / "README.md").write_text(self.readme(beta).replace('summary: "s"', 'description: "Current sub."'))
        self.assertEqual({s["slug"]: s["summary"] for s in roost.read_subprojects(self.root / "new")},
                         {"alpha": "Legacy sub.", "beta": "Current sub."})

    def test_create_project_needs_no_description(self):
        for folder in (roost.create_project("bare"), roost.create_project("empty", "")):
            text = self.readme(folder)
            self.assertIn('\ndescription: ""\n', text)
            self.assertNotRegex(text, SUMMARY_LINE)
            self.assertEqual(roost.read_project(folder)["summary"], "")
        said = 'Say "hi" here.'
        folder = roost.create_project("said", said, label="Said It")
        text = self.readme(folder)
        self.assertIn(f"\ndescription: {json.dumps(said)}\n", text)  # JSON-quoted, like the summary: line was
        self.assertNotRegex(text, SUMMARY_LINE)
        self.assertEqual((roost.read_project(folder)["summary"], roost.read_project(folder)["label"]), (said, "Said It"))

    def test_create_subproject_needs_no_description(self):
        self.project("rollout")
        for folder in (roost.create_subproject("rollout/alpha", "feature"), roost.create_subproject("rollout/beta", "chore", "")):
            text = self.readme(folder)
            self.assertIn('\ndescription: ""\n', text)
            self.assertNotRegex(text, SUMMARY_LINE)
        folder = roost.create_subproject("rollout/gamma", "bugfix", "Toner alerts.")
        self.assertIn('\ndescription: "Toner alerts."\n', self.readme(folder))
        self.assertEqual({s["slug"]: s["summary"] for s in roost.read_subprojects(self.root / "rollout")},
                         {"alpha": "", "beta": "", "gamma": "Toner alerts."})

    def test_set_description_replaces_converts_or_adds_its_line(self):
        cur = self.project("cur", '---\ndescription: "Old."\nstatus: active\nsessions: []\n---\n\n# Cur\n\ndescription: body text\n')
        said = 'New "text".'
        roost.set_description(cur, said)
        self.assertEqual(self.readme(cur), f"---\ndescription: {json.dumps(said)}\n"
                                           "status: active\nsessions: []\n---\n\n# Cur\n\ndescription: body text\n")
        legacy = self.project("legacy")  # README: category, summary, status, sessions
        roost.set_description(legacy, "Fresh.")
        self.assertEqual(self.readme(legacy), README.replace('summary: "Two password managers compared: cost and rollout."',
                                                             'description: "Fresh."'))
        bare = self.project("bare", "---\nstatus: active\nsessions: []\n---\n\n# Bare\n")
        roost.set_description(bare, "Added.")
        self.assertEqual(self.readme(bare), '---\ndescription: "Added."\nstatus: active\nsessions: []\n---\n\n# Bare\n')
        roost.set_description(bare, "")
        self.assertEqual(self.readme(bare), '---\ndescription: ""\nstatus: active\nsessions: []\n---\n\n# Bare\n')
        self.assertEqual(roost.read_project(bare)["summary"], "")
        for folder in (cur, legacy, bare):
            fm = self.readme(folder).split("\n---\n", 1)[0]
            self.assertEqual((len(DESCRIPTION_LINE.findall(fm)), len(SUMMARY_LINE.findall(fm))), (1, 0), folder.name)
        (self.root / "no-readme").mkdir()
        with self.assertRaises(ValueError):
            roost.set_description(self.root / "no-readme", "x")
        self.assertEqual(list((self.root / "no-readme").iterdir()), [])

    def test_set_label_replaces_the_first_heading_or_adds_one(self):
        folder = self.project("a")  # README: # Title
        before = self.readme(folder)
        roost.set_label(folder, "  Password   managers ")
        self.assertEqual(self.readme(folder), before.replace("\n# Title\n", "\n# Password managers\n"))
        two = self.project("b", "---\nsessions: []\n---\n\n# First\n\nText.\n\n# Second\n")
        roost.set_label(two, "One")
        self.assertEqual(self.readme(two), "---\nsessions: []\n---\n\n# One\n\nText.\n\n# Second\n")
        none = self.project("c", "---\nstatus: active\nsessions: []\n---\n\nJust text.\n")
        roost.set_label(none, "Named")
        text = self.readme(none)
        self.assertEqual(roost.heading(text), "Named")
        self.assertTrue(text.startswith("---\nstatus: active\nsessions: []\n---\n"), text)
        self.assertLess(text.index("# Named"), text.index("Just text."))
        self.assertEqual(roost.read_project(none)["label"], "Named")
        for bad in ("", "   "):
            before = self.readme(two)
            with self.assertRaises(ValueError, msg=repr(bad)):
                roost.set_label(two, bad)
            self.assertEqual(self.readme(two), before, repr(bad))

    def test_project_new_takes_an_optional_description(self):
        self.project("rollout")
        for argv, folder, want in [(["new", "plain"], self.root / "plain", ""),
                                   (["new", "said", "--description", "Said it."], self.root / "said", "Said it."),
                                   (["new", "legacy", "--summary", "Old flag."], self.root / "legacy", "Old flag."),
                                   (["new", "rollout/alpha", "--type", "feature"], self.root / "rollout" / "sub" / "alpha", ""),
                                   (["new", "rollout/beta", "--type", "bugfix", "--description", "Toner."],
                                    self.root / "rollout" / "sub" / "beta", "Toner.")]:
            code, _, err = self.run_cli(argv)
            self.assertEqual(code, 0, (argv, err))
            self.assertEqual(roost.read_project(folder)["summary"], want, argv)
            self.assertNotRegex(self.readme(folder), SUMMARY_LINE)
        code, out, _ = self.run_cli(["new", "--help"])
        self.assertEqual(code, 0)
        self.assertIn("--description", out)
        self.assertNotIn("--summary", out)  # it still works, but isn't offered

    def test_description_command_prints_or_sets_it(self):
        folder = self.project("p", "---\nstatus: active\nsessions: []\n---\n\n# P\n")
        code, out, err = self.run_cli(["description", "p"])
        self.assertEqual((code, out.strip()), (0, "(no description)"), err)
        code, _, err = self.run_cli(["description", "p", "What it is."])
        self.assertEqual(code, 0, err)
        self.assertEqual(roost.frontmatter(self.readme(folder))["description"], "What it is.")
        self.assertEqual(self.run_cli(["description", "p"])[1].strip(), "What it is.")
        self.project("legacy")
        self.assertEqual(self.run_cli(["description", "legacy"])[1].strip(), "Two password managers compared: cost and rollout.")
        sub = self.sub("p", "alpha", summary="Sub text.")
        parent = self.readme(folder)
        code, _, err = self.run_cli(["description", "p/alpha", "New sub text."])
        self.assertEqual(code, 0, err)
        self.assertEqual(roost.read_project(sub)["summary"], "New sub text.")
        self.assertEqual(self.run_cli(["description", "p/alpha"])[1].strip(), "New sub text.")
        self.assertEqual(self.readme(folder), parent)
        for argv in (["description", "nope"], ["description", "nope", "x"]):
            code, _, err = self.run_cli(argv)
            self.assertEqual(code, 1, argv)
            self.assertTrue(err.strip(), argv)


class DescriptionsOnTheTab(unittest.TestCase):
    """The Projects tab: an optional description when a project or sub-project is made, a Description
    field in the panel, and Edit its description in the organize group."""
    setUp, tearDown, project, sub = SubProjects.setUp, SubProjects.tearDown, Projects.project, SubProjects.sub
    ui, fixture, select, after = SubProjectRows.ui, SubProjectRows.fixture, SubProjectRows.select, SubProjectRows.after
    answer = staticmethod(ProjectDelete.answer)
    shown = Projects.shown

    def open_sub(self, ui, rid):
        if ("sub", rid) not in self.after(ui, rid.split("/")[0]):
            self.select(ui, "project", rid.split("/")[0])
            ui.keys_projects("right", ui.project_rows())
        return self.select(ui, "sub", rid)

    def assert_optional_description_title(self, ui):
        title = ((ui.asking or {}).get("title") or "").lower()
        self.assertIn("description", title)
        self.assertIn("optional", title)
        self.assertNotIn("summary", title)

    def test_new_project_asks_an_optional_description_last(self):
        ui = self.ui()
        ui.keys_projects("enter", [])
        typed(ui, "Zed\n")  # its name
        typed(ui, "\n")  # its folder, zed
        self.assert_optional_description_title(ui)
        typed(ui, "\n")  # no description
        self.assertIsNone(ui.asking)
        self.assertFalse(ui.msg.startswith(("Cancelled", "Couldn't")), ui.msg)
        self.assertEqual(roost.read_project(self.root / "zed")["summary"], "")
        self.assertIn('\ndescription: ""\n', (self.root / "zed" / "README.md").read_text())

    def test_new_sub_project_asks_an_optional_description_last(self):
        ui = self.fixture()
        act(ui, ui.keys_projects, self.select(ui, "project", "rollout"), "New sub-project")
        ui.choosing["sel"] = next(i for i, (l, _) in enumerate(ui.choice_options()) if "chore" in l)
        ui.keys_choice("enter")
        typed(ui, "\x15tidy\n")  # its slug
        self.assert_optional_description_title(ui)
        typed(ui, "\x15\n")  # no description
        self.assertIsNone(ui.asking)
        self.assertTrue(ui.msg.startswith("Created"), ui.msg)
        folder = self.root / "rollout" / "sub" / "tidy"
        self.assertEqual(roost.read_project(folder)["summary"], "")
        self.assertIn('\ndescription: ""\n', (folder / "README.md").read_text())

    def test_the_panel_field_is_description_and_says_when_there_is_none(self):
        self.project("rollout", "---\nstatus: active\nsessions: []\n---\n\n# Rollout\n", handoffs=[TODAY()])
        self.project("said", '---\ndescription: "Said text."\nstatus: active\nsessions: []\n---\n\n# Said\n', handoffs=[TODAY()])
        self.sub("rollout", "alpha", summary="")
        self.sub("rollout", "beta", summary="Toner alerts.")
        ui = self.ui()
        for kind, rid, want in [("project", "rollout", "no description yet"), ("project", "said", "Said text."),
                                ("sub", "rollout/alpha", "no description yet"), ("sub", "rollout/beta", "Toner alerts.")]:
            rows = self.open_sub(ui, rid) if kind == "sub" else self.select(ui, kind, rid)
            _, fields = ui.project_panel(rows, 40, 120)
            named = dict((label, str(v)) for label, v in fields if label)
            self.assertNotIn("Summary", named, (rid, fields))
            self.assertIn(want, named.get("Description", ""), (rid, fields))

    def test_edit_its_description_starts_with_it_then_sets_or_clears_it(self):
        ui = self.fixture()  # rollout's README has a legacy summary: line; alpha's is Badge printers.
        readme = self.root / "rollout" / "README.md"
        act(ui, ui.keys_projects, self.select(ui, "project", "rollout"), "Edit its description")
        self.assertEqual(ui.asking["text"], "Two password managers compared: cost and rollout.")
        self.answer(ui, "Printers for every office.")
        self.assertEqual(roost.frontmatter(readme.read_text())["description"], "Printers for every office.")
        self.assertNotRegex(readme.read_text(), SUMMARY_LINE)
        self.assertEqual(next(p for p in ui.projects if p["id"] == "rollout")["summary"], "Printers for every office.")
        act(ui, ui.keys_projects, self.select(ui, "project", "rollout"), "Edit its description")
        self.assertEqual(ui.asking["text"], "Printers for every office.")
        self.answer(ui, "")
        self.assertEqual(roost.frontmatter(readme.read_text())["description"], "")
        sub = self.root / "rollout" / "sub" / "alpha" / "README.md"
        act(ui, ui.keys_projects, self.open_sub(ui, "rollout/alpha"), "Edit its description")
        self.assertEqual(ui.asking["text"], "Badge printers.")
        self.answer(ui, "Badge printers, every site.")
        self.assertEqual(roost.frontmatter(sub.read_text())["description"], "Badge printers, every site.")
        act(ui, ui.keys_projects, self.open_sub(ui, "rollout/alpha"), "Edit its description")
        self.answer(ui, "")
        self.assertEqual(roost.read_project(sub.parent)["summary"], "")


class ProjectRename(unittest.TestCase):
    """Rename on a project: a Label prompt (its README heading), then a Folder name prompt. The folder name
    starts as slug(new label) when it was slug(old label) and the label changed, else as it is."""
    setUp, tearDown, project = SubProjects.setUp, SubProjects.tearDown, Projects.project
    ui, select = SubProjectRows.ui, SubProjectRows.select
    answer = staticmethod(ProjectDelete.answer)
    shown = Projects.shown

    def fresh(self):
        self.tearDown()
        self.setUp()

    def make(self, pid="printer-rollout", heading="Printer Rollout"):
        return self.project(pid, README.replace("# Title", f"# {heading}"), handoffs=[TODAY()])

    def open_rename(self, ui, pid="printer-rollout"):
        act(ui, ui.keys_projects, self.select(ui, "project", pid), "Rename")

    def folder_prompt(self, ui):
        self.assertIsNotNone(ui.asking)
        self.assertIn("folder name", (ui.asking.get("title") or "").lower(), ui.asking)
        return ui.asking["text"]

    def test_rename_asks_its_label_then_its_folder_name(self):
        self.make()
        ui = self.ui()
        self.open_rename(ui)
        self.assertIsNotNone(ui.asking)
        self.assertIn("label", (ui.asking.get("title") or "").lower(), ui.asking)
        self.assertEqual(ui.asking["text"], "Printer Rollout")
        self.answer(ui, "Printer Rollout")
        self.assertEqual(self.folder_prompt(ui), "printer-rollout")

    def test_a_new_label_moves_a_folder_named_after_the_old_one(self):
        self.make()
        ui = self.ui()
        self.open_rename(ui)
        self.answer(ui, "Badge Printers")
        self.assertEqual(self.folder_prompt(ui), "badge-printers")
        self.answer(ui, "badge-printers")
        self.assertFalse((self.root / "printer-rollout").exists())
        self.assertEqual((self.root / "badge-printers" / "README.md").read_text(), README.replace("# Title", "# Badge Printers"))

    def test_a_new_label_keeps_a_folder_name_not_made_from_it(self):
        folder = self.make("b", "Vendor Track")
        ui = self.ui()
        self.open_rename(ui, "b")
        self.answer(ui, "Vendor  Review")
        self.assertEqual(self.folder_prompt(ui), "b")
        self.answer(ui, "b")
        self.assertEqual((folder / "README.md").read_text(), README.replace("# Title", "# Vendor Review"))
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["b"])
        self.assertEqual(next(p for p in ui.projects if p["id"] == "b")["label"], "Vendor Review")

    def test_nothing_changed_writes_nothing(self):
        folder = self.make()
        before = (folder / "README.md").read_text()
        ui = self.ui()
        self.open_rename(ui)
        self.answer(ui, "Printer Rollout")
        self.assertEqual(self.folder_prompt(ui), "printer-rollout")
        self.answer(ui, "printer-rollout")
        self.assertIsNone(ui.asking)
        self.assertRegex(ui.msg, r"(?i)nothing|no change")
        self.assertEqual((folder / "README.md").read_text(), before)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["printer-rollout"])

    def test_an_empty_label_cancels(self):
        folder = self.make()
        before = (folder / "README.md").read_text()
        ui = self.ui()
        self.open_rename(ui)
        self.answer(ui, "   ")
        self.assertIsNone(ui.asking)
        self.assertRegex(ui.msg, r"(?i)cancel")
        self.assertEqual((folder / "README.md").read_text(), before)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["printer-rollout"])

    def test_a_refused_folder_name_changes_nothing_not_even_the_label(self):
        for bad in ("Bad Name", "taken"):
            self.fresh()
            folder = self.make()
            self.project("taken")
            before = (folder / "README.md").read_text()
            ui = self.ui()
            self.open_rename(ui)
            self.answer(ui, "New Name")
            self.folder_prompt(ui)
            self.answer(ui, bad)
            self.assertTrue(ui.msg.startswith("Couldn't rename it"), (bad, ui.msg))
            self.assertEqual((folder / "README.md").read_text(), before, bad)
            self.assertEqual((self.root / "taken" / "README.md").read_text(), README, bad)
            self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["printer-rollout", "taken"], bad)


@unittest.skipUnless(roost.App, "Textual isn't installed here: run the tests with ~/.roost/venv/bin/python")
class SubProjectLabels(unittest.TestCase):
    """A sub-project's row on the Projects tab shows its label (its README heading); its id stays
    <project>/<slug>, and the Sessions tab's PROJECT column shows that id."""
    run_on, sidebar = ProjectRows.run_on, staticmethod(ProjectRows.sidebar)
    select = staticmethod(TextualScreens.select)

    def setUp(self):
        Projects.setUp(self)  # temp Projects folder, read here before the screens' stand-ins replace the readers
        self.addCleanup(Projects.tearDown, self)
        work = session("Badge work")
        Projects.project(self, "rollout", handoffs=[TODAY()])
        folder = SubProjects.sub(self, "rollout", "alpha", summary="Badge printers.", sessions=[("Badge work", work["id"])])
        (folder / "README.md").write_text((folder / "README.md").read_text().replace("\n# alpha\n", "\n# Badge Printers\n"))
        self.projects = roost.read_projects([work])
        TextualScreens.setUp(self)
        self.sessions = [work]

    def test_a_sub_row_shows_its_label(self):
        app = self.run_on(self.projects, [self.select(lambda ui: ui.subs_open.add("rollout"))])
        self.assertEqual([r.item["id"] for r in app.ui.project_rows() if r.kind == "sub"], ["rollout/alpha"])
        rows = self.sidebar(app.lines)
        self.assertTrue([l for l in rows if "Badge Printers" in l], rows)

    def test_the_sessions_tab_names_a_sub_project_by_its_id(self):
        seen = {}

        async def look(pilot):
            seen.update({s["name"]: s.get("proj") for s in pilot.app.ui.srows})
        app = self.run_on(self.projects, [look], tab="Sessions")
        self.assertEqual(seen.get("Badge work"), "rollout/alpha")
        row = next(l for l in app.lines if "Badge work" in l)
        self.assertIn("rollout/alpha", row)
        self.assertNotIn("Badge Printers", row)


class ProjectMarkers(unittest.TestCase):
    """markers(r), the marker at a sidebar row's right end: ● when a Session it lists is running; ◆ when
    it's ready to pick up (open, nothing it lists running, a Handoff, and its newest Session, if any,
    wrapped up); a folded project adds its open sub-projects' markers, ● first."""
    setUp, tearDown, project, sub = SubProjects.setUp, SubProjects.tearDown, Projects.project, SubProjects.sub
    wrapped_project = Projects.wrapped_project

    def ui(self, sessions=()):
        ui = Projects.ui(self)
        ui.folded = set()
        ui.sessions = list(sessions)
        ui.load_projects()
        return ui

    @staticmethod
    def marks(ui):
        """{(kind, id): its markers} for every row the tab lists."""
        return {(r.kind, None if r.kind == "all" else r.item["id"]): ui.markers(r) for r in ui.project_rows()}

    @staticmethod
    def readme(ids=(), status="active"):
        listed = "".join(f"\n  - name: {i}\n    id: {i}" for i in ids) or " []"
        return f"---\nsummary: s\nstatus: {status}\nsessions:{listed}\n---\n\n# Title\n"

    def test_a_project_with_a_running_session_shows_a_dot(self):
        """Any state but closed is running; with something running, a Handoff doesn't add ◆."""
        self.project("b", self.readme(["aa1b9a71-0000", "bb2c0000-0000"]), handoffs=[TODAY()])
        for state in ("idle", "busy", "waiting", "blocked"):
            ui = self.ui([dict(session("Live", state=state), id="aa1b9a71-0000"),
                          dict(session("Gone", state="closed", kind=None, mins=90), id="bb2c0000-0000")])
            self.assertEqual(self.marks(ui)[("project", "b")], "●", state)

    def test_a_project_with_handoffs_and_no_sessions_is_ready(self):
        self.project("b", self.readme(), handoffs=[TODAY()])
        self.project("c", self.readme())  # no Handoff: nothing to pick up
        m = self.marks(self.ui())
        self.assertEqual((m[("project", "b")], m[("project", "c")]), ("◆", ""))

    def test_a_project_is_ready_only_when_its_newest_session_is_wrapped_up(self):
        end = self.wrapped_project(["aa1b9a71-0000", "bb2c0000-0000"])
        wrapped = dict(session("Wrapped", state="closed", kind=None, mins=60), id="aa1b9a71-0000", active=end)
        # bb2c0000-0000's Handoff stops three hours ago, so it covers neither closed Session below
        ui = self.ui([wrapped, dict(session("Older", state="closed", kind=None, mins=90), id="bb2c0000-0000")])
        self.assertEqual([ui.wrapped_up(ui.projects[0], s) for s in ui.sessions], [True, False])  # the fixture
        self.assertEqual(self.marks(ui)[("project", "b")], "◆")  # the newest is wrapped up; an older one isn't
        ui = self.ui([wrapped, dict(session("Newer", state="closed", kind=None, mins=30), id="bb2c0000-0000")])
        self.assertEqual(self.marks(ui)[("project", "b")], "")  # the newest isn't wrapped up

    def test_a_closed_project_is_never_ready(self):
        self.project("b", self.readme(status="closed"), handoffs=[TODAY()])
        self.project("c", self.readme(["cc3d0000-0000"], status="closed"), handoffs=[TODAY()])
        ui = self.ui([dict(session("Live"), id="cc3d0000-0000")])
        ui.pquery = "title"  # both READMEs' heading: closed projects are only in a search's rows
        m = self.marks(ui)
        self.assertEqual((m[("project", "b")], m[("project", "c")]), ("", "●"))

    def test_the_all_projects_row_and_new_project_have_no_marker(self):
        self.project("b", self.readme(), handoffs=[TODAY()])  # ready: ◆ on its own row
        ui = self.ui()
        rows = [r for r in ui.project_rows() if r.kind in ("head", "all", "new")]
        self.assertEqual([r.kind for r in rows], ["all", "new"])
        self.assertEqual([ui.markers(r) for r in rows], ["", ""])

    def test_a_sub_project_row_has_its_own_marker_and_an_opened_project_only_its_own(self):
        self.project("rollout", self.readme())  # no Handoff, no Sessions: nothing of its own
        self.sub("rollout", "alpha", sessions=[("Live", "cc3d0000-0000")])
        self.sub("rollout", "beta", status="blocked", handoffs=[(TODAY(), "x")])  # open, though blocked
        self.sub("rollout", "delta")
        ui = self.ui([dict(session("Live"), id="cc3d0000-0000")])
        ui.subs_open.add("rollout")
        m = self.marks(ui)
        self.assertEqual([m[("sub", f"rollout/{s}")] for s in ("alpha", "beta", "delta")], ["●", "◆", ""])
        self.assertEqual(m[("project", "rollout")], "")

    def test_a_folded_project_shows_its_open_sub_projects_markers(self):
        self.project("rollout", self.readme())
        self.sub("rollout", "alpha", sessions=[("Live", "cc3d0000-0000")])
        self.sub("rollout", "beta", handoffs=[(TODAY(), "x")])
        self.project("other", self.readme())
        self.sub("other", "gamma", status="done", sessions=[("Busy", "dd4e0000-0000")], handoffs=[(TODAY(), "x")])  # done: not counted
        self.sub("other", "delta", sessions=[("Gone", "ee5f0000-0000")])  # closed, not wrapped up, no Handoff
        ui = self.ui([dict(session("Live"), id="cc3d0000-0000"), dict(session("Busy", state="busy"), id="dd4e0000-0000"),
                      dict(session("Gone", state="closed", kind=None), id="ee5f0000-0000")])
        m = self.marks(ui)
        self.assertEqual((m[("project", "rollout")], m[("project", "other")]), ("● ◆", ""))
        ui.subs_open.add("rollout")
        self.assertEqual(self.marks(ui)[("project", "rollout")], "")

    def test_a_folded_projects_own_marker_and_its_sub_projects_combine_dot_first(self):
        self.project("ready", self.readme(), handoffs=[TODAY()])  # ready itself, a running sub-project
        self.sub("ready", "alpha", sessions=[("Live", "cc3d0000-0000")])
        self.project("live", self.readme(["dd4e0000-0000"]))  # running itself, a ready sub-project
        self.sub("live", "beta", handoffs=[(TODAY(), "x")])
        self.project("both", self.readme(["ee5f0000-0000"]))  # running itself and in a sub-project: one ●
        self.sub("both", "gamma", sessions=[("Third", "ff600000-0000")])
        ui = self.ui([dict(session("Live"), id="cc3d0000-0000"), dict(session("Own"), id="dd4e0000-0000"),
                      dict(session("Mine"), id="ee5f0000-0000"), dict(session("Third"), id="ff600000-0000")])
        m = self.marks(ui)
        self.assertEqual([m[("project", p)] for p in ("ready", "live", "both")], ["● ◆", "● ◆", "●"])
        ui.subs_open.update({"ready", "live", "both"})
        m = self.marks(ui)
        self.assertEqual([m[("project", p)] for p in ("ready", "live", "both")], ["◆", "●", "●"])

    def test_the_demo_has_a_running_project_and_a_ready_one(self):
        ui = Projects.ui(self)
        ui.folded = set()
        ui.sessions = roost.demo_sessions()
        ui.projects = roost.demo_projects(ui.sessions)
        shown = {r.item["id"]: ui.markers(r) for r in ui.project_rows() if r.kind == "project"}
        self.assertTrue([p for p, m in shown.items() if "●" in m], shown)
        self.assertTrue([p for p, m in shown.items() if "◆" in m], shown)


class SessionsField(unittest.TestCase):
    """The detail pane's Sessions field for a project or sub-project: its running counts (◆ need input,
    ● working, ○ idle), then N unwrapped when there are any; none running when neither."""
    setUp, tearDown, project, sub = SubProjects.setUp, SubProjects.tearDown, Projects.project, SubProjects.sub
    ui, readme, wrapped_project = ProjectMarkers.ui, staticmethod(ProjectMarkers.readme), Projects.wrapped_project

    def fields(self, ui, kind, rid):
        rows = ui.project_rows()
        ui.psel = next(i for i, r in enumerate(rows) if r.kind == kind and r.item["id"] == rid)
        return ui.project_panel(rows, 40, 120)[1]

    def field(self, ui, kind, rid):
        fields = self.fields(ui, kind, rid)
        got = [str(v) for label, v in fields if label == "Sessions"]
        self.assertEqual(len(got), 1, fields)
        return got[0].strip()

    def test_running_counts_then_unwrapped(self):
        ids = ["aa1b9a71-0000", "bb2c0000-0000", "cc3d0000-0000", "dd4e0000-0000", "ee5f0000-0000"]
        self.project("b", self.readme(ids), handoffs=[TODAY()])
        ui = self.ui([dict(session("Asks", state="waiting"), id=ids[0]), dict(session("Works", state="busy"), id=ids[1]),
                      dict(session("Sits"), id=ids[2]), dict(session("Sits too"), id=ids[3]),
                      dict(session("Gone", state="closed", kind=None, mins=30), id=ids[4])])
        self.assertRegex(self.field(ui, "project", "b"), r"^◆1\W+●1\W+○2\W+1 unwrapped$")

    def test_only_unwrapped_sessions(self):
        self.project("b", self.readme(["aa1b9a71-0000", "bb2c0000-0000"]), handoffs=[TODAY()])
        ui = self.ui([dict(session("Gone", state="closed", kind=None, mins=30), id="aa1b9a71-0000"),
                      dict(session("Gone too", state="closed", kind=None, mins=40), id="bb2c0000-0000")])
        self.assertEqual(self.field(ui, "project", "b"), "2 unwrapped")

    def test_none_running_when_nothing_runs_and_nothing_is_unwrapped(self):
        self.project("c", self.readme())
        end = self.wrapped_project(["aa1b9a71-0000"])
        ui = self.ui([dict(session("Wrapped", state="closed", kind=None, mins=60), id="aa1b9a71-0000", active=end)])
        self.assertTrue(ui.wrapped_up(next(p for p in ui.projects if p["id"] == "b"), ui.sessions[0]))  # the fixture
        self.assertEqual(self.field(ui, "project", "b"), "none running")  # only a wrapped-up Session
        self.assertEqual(self.field(ui, "project", "c"), "none running")  # no Sessions at all

    def test_a_sub_projects_field_counts_its_own_sessions(self):
        self.project("rollout", self.readme(), handoffs=[TODAY()])
        self.sub("rollout", "alpha", sessions=[("Sits", "cc3d0000-0000")])
        self.sub("rollout", "beta")
        ui = self.ui([dict(session("Sits"), id="cc3d0000-0000")])
        ui.subs_open.add("rollout")
        self.assertEqual(self.field(ui, "sub", "rollout/alpha"), "○1")
        self.assertEqual(self.field(ui, "sub", "rollout/beta"), "none running")
        self.assertEqual(self.field(ui, "project", "rollout"), "none running")  # a sub-project's Sessions are its own


def handoff_name(ts, sid="aaaaaaaa"):
    """A Handoff's file name for a time: YYYY-MM-DD-HHMM-<session8>.md."""
    return time.strftime("%Y-%m-%d-%H%M", time.localtime(ts)) + f"-{sid}.md"


def month_start():
    """Midnight on the first of this month, as a datetime."""
    return datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def no_spend_reads(test):
    """Patch statsdata.gather_spend for one test: spend is never read from the person's transcripts."""
    import unittest.mock
    patcher = unittest.mock.patch.object(importlib.import_module("roost.core.statsdata"), "gather_spend",
                                         lambda table=None, now=None: {"rows": [], "hours": {}, "tokens": {},
                                                                       "unpriced": [], "priced": True})
    patcher.start()
    test.addCleanup(patcher.stop)


class SectionData(unittest.TestCase):
    """read_project's "handoffs" (time, title, path), newest first; "notes" (mtime, title, path), newest edit
    first; and "repo", the README's repo: line. Sub-projects get them too."""
    setUp, tearDown, project, sub = SubProjects.setUp, SubProjects.tearDown, Projects.project, SubProjects.sub

    def handoffs(self, folder, specs):
        """Write (ts, frontmatter text or None) Handoffs into folder/handoffs; returns their paths."""
        out = []
        for i, (ts, meta) in enumerate(specs):
            path = folder / "handoffs" / handoff_name(ts, f"{i:08x}")
            path.write_text((f"---\n{meta}---\n\n" if meta else "") + "Did the work.\n")
            out.append(path)
        return out

    def notes(self, folder, specs):
        """Write (name, text, mtime) notes into folder/notes; returns their paths."""
        (folder / "notes").mkdir(exist_ok=True)
        out = []
        for name, text, mtime in specs:
            path = folder / "notes" / name
            path.write_text(text)
            os.utime(path, (mtime, mtime))
            out.append(path)
        return out

    def test_handoffs_are_listed_newest_first_titled_by_rollup_then_name_then_file_stem(self):
        folder = self.project("rollout")
        now = time.time()
        paths = self.handoffs(folder, [(now - 3 * 86400, "session: aa1b9a71-0000\nname: Printer fix\n"),
                                       (now - 86400, "rollup: alpha\nname: Ignored name\n"),
                                       (now - 5 * 86400, None),
                                       (now - 2 * 86400, "session: bb2c0000-0000\n")])
        p = roost.read_project(folder)
        # each Handoff carries its file's path (a str) too, since step 3b-1: the detail pane opens it from there
        want = [(roost.handoff_time(paths[1]), "Roll-up of alpha", str(paths[1])), (roost.handoff_time(paths[3]), paths[3].stem, str(paths[3])),
                (roost.handoff_time(paths[0]), "Printer fix", str(paths[0])), (roost.handoff_time(paths[2]), paths[2].stem, str(paths[2]))]
        self.assertEqual([tuple(h) for h in p["handoffs"]], want)

    def test_notes_are_listed_newest_edit_first_titled_as_read_note_gives_them(self):
        folder = self.project("rollout")
        now = time.time()
        paths = self.notes(folder, [("budget.md", "---\ntitle: Toner budget\n---\n\nFigures.\n", now - 7200),
                                    ("check.md", "# Driver checklist\n\n- one\n", now - 60),
                                    ("plain.md", "", now - 86400)])
        p = roost.read_project(folder)
        got = [(t, title, str(path)) for t, title, path in p["notes"]]
        want = [(os.stat(x).st_mtime, roost.read_note(x)["name"], str(x)) for x in (paths[1], paths[0], paths[2])]
        self.assertEqual(got, want)
        self.assertEqual([title for _, title, _ in got], ["Driver checklist", "Toner budget", "plain"])

    def test_a_project_with_no_notes_folder_has_no_notes(self):
        folder = self.project("rollout")
        self.assertFalse((folder / "notes").exists())
        self.assertEqual(list(roost.read_project(folder)["notes"]), [])
        self.assertEqual(list(roost.read_project(folder)["handoffs"]), [])

    def test_repo_is_the_readmes_repo_line_or_empty(self):
        with_repo = self.project("tools", README.replace("status: active\n", "status: active\nrepo: ~/code/printer-tools\n"))
        without = self.project("other")
        self.assertEqual(roost.read_project(with_repo)["repo"], "~/code/printer-tools")
        self.assertEqual(roost.read_project(without)["repo"], "")

    def test_sub_projects_get_handoffs_notes_and_repo(self):
        self.project("rollout")
        alpha = self.sub("rollout", "alpha")
        (alpha / "README.md").write_text((alpha / "README.md").read_text().replace("status: active\n",
                                                                                   "status: active\nrepo: ~/code/badges\n"))
        now = time.time()
        [h] = self.handoffs(alpha, [(now - 3600, "name: Badge work\n")])
        [n] = self.notes(alpha, [("idea.md", "# Badge idea\n", now - 60)])
        [s] = [x for x in roost.read_subprojects(self.root / "rollout") if x["slug"] == "alpha"]
        # a Handoff is (time, title, path) since step 3b-1: the detail pane opens it from its path
        self.assertEqual([tuple(x) for x in s["handoffs"]], [(roost.handoff_time(h), "Badge work", str(h))])
        self.assertEqual([(title, str(path)) for _, title, path in s["notes"]], [("Badge idea", str(n))])
        self.assertEqual(s["repo"], "~/code/badges")


class DetailSections(unittest.TestCase):
    """detail_sections(r): (heading, lines) under a project's or sub-project's fields: Sub-projects (a
    project's open ones), Sessions, Handoffs, Notes, and Stats; none for headers and + New project."""
    setUp_base, tearDown, project, sub = SubProjects.setUp, SubProjects.tearDown, Projects.project, SubProjects.sub
    ui, readme = ProjectMarkers.ui, staticmethod(ProjectMarkers.readme)
    handoffs, notes = SectionData.handoffs, SectionData.notes

    def setUp(self):
        self.setUp_base()
        no_spend_reads(self)

    @staticmethod
    def row(ui, kind, rid):
        return next(r for r in ui.project_rows() if r.kind == kind and r.item["id"] == rid)

    def sections(self, ui, kind, rid):
        return [(h, list(lines)) for h, lines in ui.detail_sections(self.row(ui, kind, rid))]

    def section(self, ui, kind, rid, heading):
        got = [lines for h, lines in self.sections(ui, kind, rid) if h == heading]
        self.assertEqual(len(got), 1, (heading, self.sections(ui, kind, rid)))
        return got[0]

    @staticmethod
    def spend(ui, rows):
        """Spend data as load_stats leaves it, loaded."""
        ui.sjob = {"state": "done", "at": time.time(), "data": {"rows": rows, "hours": {}, "tokens": {},
                                                                 "unpriced": [], "priced": True}}

    # which sections, in order

    def test_new_project_has_no_sections(self):
        self.project("b", self.readme(), handoffs=[TODAY()])
        ui = self.ui()
        rows = [r for r in ui.project_rows() if r.kind in ("head", "new")]
        self.assertEqual([r.kind for r in rows], ["new"])
        self.assertEqual([list(ui.detail_sections(r)) for r in rows], [[]])

    def test_a_projects_sections_in_order_with_sub_projects_only_when_it_has_open_ones(self):
        self.project("rollout", self.readme(), handoffs=[TODAY()])
        self.sub("rollout", "alpha")
        self.project("plain", self.readme(), handoffs=[TODAY()])
        self.project("finished", self.readme(), handoffs=[TODAY()])
        self.sub("finished", "gamma", status="done")
        ui = self.ui()
        heads = lambda rid: [h for h, _ in self.sections(ui, "project", rid)]
        self.assertEqual(heads("rollout"), ["Sub-projects", "Sessions", "Handoffs", "Notes", "Stats"])
        ui.subs_open.add("rollout")  # opened in the sidebar: still listed
        self.assertEqual(heads("rollout"), ["Sub-projects", "Sessions", "Handoffs", "Notes", "Stats"])
        self.assertEqual(heads("plain"), ["Sessions", "Handoffs", "Notes", "Stats"])
        self.assertEqual(heads("finished"), ["Sessions", "Handoffs", "Notes", "Stats"])  # only a done one

    def test_a_sub_projects_sections_are_the_same_without_sub_projects(self):
        self.project("rollout", self.readme(), handoffs=[TODAY()])
        self.sub("rollout", "alpha")
        ui = self.ui()
        ui.subs_open.add("rollout")
        self.assertEqual([h for h, _ in self.sections(ui, "sub", "rollout/alpha")], ["Sessions", "Handoffs", "Notes", "Stats"])

    # 1. Sub-projects

    def test_sub_projects_lines_give_marks_label_type_status_and_state(self):
        self.project("rollout", self.readme(), handoffs=[TODAY()])
        alpha = self.sub("rollout", "alpha", type="feature", status="active", sessions=[("Live", "cc3d0000-0000")])
        (alpha / "README.md").write_text((alpha / "README.md").read_text().replace("\n# alpha\n", "\n# Badge Printers\n"))
        handed = time.time() - 2 * 86400
        self.sub("rollout", "beta", type="bugfix", status="blocked", handoffs=[(handoff_name(handed), "x")])
        self.sub("rollout", "delta", type="research", status="waiting-on-vendor")
        self.sub("rollout", "gamma", type="chore", status="done", handoffs=[(TODAY(), "x")])
        ui = self.ui([dict(session("Live"), id="cc3d0000-0000")])
        lines = self.section(ui, "project", "rollout", "Sub-projects")
        self.assertEqual(len(lines), 3, lines)  # the open ones; gamma is done
        subs = {s["slug"]: s for s in self.row(ui, "project", "rollout").item["subs"]}
        line = {slug: next((l for l in lines if label in l), None)
                for slug, label in (("alpha", "Badge Printers"), ("beta", "beta"), ("delta", "delta"))}
        for slug, l in line.items():
            self.assertIsNotNone(l, (slug, lines))
            mark = ui.markers(roost.Row("Recent", "sub", subs[slug], 0))
            self.assertIn(mark, l)
            self.assertIn(subs[slug]["type"], l)
            self.assertIn(subs[slug]["status"], l)
        self.assertEqual([ui.markers(roost.Row("Recent", "sub", subs[s], 0)) for s in ("alpha", "beta", "delta")],
                         ["●", "◆", ""])  # the fixture
        self.assertIn("●", line["alpha"])
        self.assertIn("running", line["alpha"])
        self.assertIn("◆", line["beta"])
        self.assertIn(f"ready · Handoff {roost.short_when(subs['beta']['handed'])}", line["beta"])
        self.assertNotIn("running", line["beta"])
        for word in ("running", "ready", "●", "◆"):
            self.assertNotIn(word, line["delta"])

    # 2. Sessions

    def test_sessions_lists_running_then_closed_in_the_last_7_days_newest_first_at_most_5(self):
        ids = [f"{i:02d}000000-0000" for i in range(8)]
        self.project("b", self.readme(ids), handoffs=[TODAY()])
        closed = lambda name, mins, sid: dict(session(name, state="closed", kind=None, mins=mins), id=sid, attach=None)
        listed = [dict(session("Toner order", state="busy", mins=2), id=ids[0]),
                  dict(session("Driver push", mins=4), id=ids[1]),
                  closed("Badge audit", 10, ids[2]), closed("Cable survey", 600, ids[3]),
                  closed("Paper restock", 3 * 1440, ids[4]), closed("Old fleet", 6 * 1440, ids[5]),
                  closed("Ancient one", 8 * 1440, ids[6])]
        ui = self.ui(listed + [dict(session("Not listed"), id="ff000000-0000")])
        lines = self.section(ui, "project", "b", "Sessions")
        want = listed[:5]  # the two running, then the three newest closed; Old fleet is cut by the 5
        self.assertEqual(len(lines), 5, lines)
        for l, s in zip(lines, want):
            self.assertIn(s["name"], l)
            self.assertIn(roost.short_when(s["last"]), l)
        self.assertFalse([l for l in lines if "Ancient one" in l or "Not listed" in l], lines)

    def test_running_sessions_come_before_newer_closed_ones(self):
        ids = ["aa000000-0000", "bb000000-0000"]
        self.project("b", self.readme(ids), handoffs=[TODAY()])
        ui = self.ui([dict(session("Just closed", state="closed", kind=None, mins=1), id=ids[0], attach=None),
                      dict(session("Still going", mins=90), id=ids[1])])
        lines = self.section(ui, "project", "b", "Sessions")
        self.assertEqual(len(lines), 2, lines)
        self.assertIn("Still going", lines[0])
        self.assertIn("Just closed", lines[1])

    def test_sessions_says_none_in_the_last_7_days(self):
        self.project("b", self.readme(["aa000000-0000"]), handoffs=[TODAY()])
        self.project("c", self.readme(), handoffs=[TODAY()])
        ui = self.ui([dict(session("Ancient one", state="closed", kind=None, mins=8 * 1440), id="aa000000-0000", attach=None)])
        self.assertEqual(self.section(ui, "project", "b", "Sessions"), ["none in the last 7 days"])
        self.assertEqual(self.section(ui, "project", "c", "Sessions"), ["none in the last 7 days"])

    def test_a_sub_projects_sessions_are_its_own(self):
        self.project("rollout", self.readme(["aa000000-0000"]), handoffs=[TODAY()])
        self.sub("rollout", "alpha", sessions=[("Badge work", "cc000000-0000")])
        ui = self.ui([dict(session("Parent work"), id="aa000000-0000"), dict(session("Badge work", mins=30), id="cc000000-0000")])
        ui.subs_open.add("rollout")
        lines = self.section(ui, "sub", "rollout/alpha", "Sessions")
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("Badge work", lines[0])

    # 3. Handoffs

    def test_handoffs_lists_the_newest_5_each_its_time_then_its_title(self):
        folder = self.project("b", self.readme())
        now = time.time()
        specs = [(now - (d + 1) * 86400, f"name: Handoff {d}\n") for d in range(6)]  # Handoff 0 is the newest
        paths = self.handoffs(folder, list(reversed(specs)))
        ui = self.ui()
        lines = self.section(ui, "project", "b", "Handoffs")
        self.assertEqual(len(lines), 5, lines)
        for d, l in enumerate(lines):
            when = roost.short_when(roost.handoff_time(next(p for p in paths if f"Handoff {d}" in p.read_text())))
            self.assertIn(when, l)
            self.assertIn(f"Handoff {d}", l)
            self.assertLess(l.index(when), l.index(f"Handoff {d}"), l)
        self.assertFalse([l for l in lines if "Handoff 5" in l])

    def test_no_handoffs_yet(self):
        self.project("b", self.readme())
        ui = self.ui()
        self.assertEqual(self.section(ui, "project", "b", "Handoffs"), ["No Handoffs yet"])

    # 4. Notes

    def test_notes_lists_the_newest_5_titles(self):
        folder = self.project("b", self.readme(), handoffs=[TODAY()])
        now = time.time()
        self.notes(folder, [(f"n{i}.md", f"# Note {i}\n", now - i * 3600) for i in range(6)])  # Note 0 is the newest
        ui = self.ui()
        lines = self.section(ui, "project", "b", "Notes")
        self.assertEqual(len(lines), 5, lines)
        for i, l in enumerate(lines):
            self.assertIn(f"Note {i}", l)
        self.assertFalse([l for l in lines if "Note 5" in l])

    def test_no_notes(self):
        self.project("b", self.readme(), handoffs=[TODAY()])
        ui = self.ui()
        self.assertEqual(self.section(ui, "project", "b", "Notes"), ["No notes"])

    # 5. Stats

    def test_stats_this_months_spend_on_the_project_or_its_sessions_and_its_sessions_this_month(self):
        listed = ["aa000000-0000", "bb000000-0000", "cc000000-0000"]
        self.project("fleet", self.readme(listed).replace("# Title", "# Printer fleet"), handoffs=[TODAY()])
        self.project("other", self.readme(), handoffs=[TODAY()])
        start = month_start()
        before = start - timedelta(days=1)
        ui = self.ui([dict(session("This month"), id=listed[0]),
                      dict(session("Early this month", state="closed", kind=None), id=listed[1], last=start.timestamp() + 60),
                      dict(session("Last month", state="closed", kind=None), id=listed[2], last=before.timestamp())])
        today, live = date.today().isoformat(), "Live work"  # days as gather_spend writes them
        self.spend(ui, [(today, live, "zz000000-0000", "fleet", 1.25),          # by its id
                        (today, live, "yy000000-0000", "Printer fleet", 2.00),  # by its label
                        (today, live, listed[1], None, 0.75),                   # by a Session it lists
                        (start.date().isoformat(), "Wrap-ups", listed[0], None, 0.50),      # the 1st counts
                        (before.date().isoformat(), live, listed[0], "fleet", 9.00),        # last month
                        (today, live, "xx000000-0000", "other", 4.00),          # another project
                        (today, live, "ww000000-0000", None, 3.00)])            # a Session it doesn't list
        lines = self.section(ui, "project", "fleet", "Stats")
        self.assertEqual(len(lines), 1, lines)
        self.assertRegex(lines[0], r"This month\s+\$4\.50 · 2 Sessions")

    def test_a_sub_projects_stats_are_its_own(self):
        self.project("rollout", self.readme(), handoffs=[TODAY()])
        self.sub("rollout", "alpha", sessions=[("A", "aa000000-0000"), ("B", "bb000000-0000")])
        ui = self.ui([dict(session("A"), id="aa000000-0000"), dict(session("B", mins=30), id="bb000000-0000")])
        ui.subs_open.add("rollout")
        today = date.today().isoformat()
        self.spend(ui, [(today, "Live work", "aa000000-0000", None, 0.40), (today, "Live work", "zz000000-0000", "rollout/alpha", 0.60),
                        (today, "Live work", "yy000000-0000", "rollout", 5.00)])
        self.assertRegex(self.section(ui, "sub", "rollout/alpha", "Stats")[0], r"This month\s+\$1\.00 · 2 Sessions")

    def test_a_spend_row_with_no_day_is_skipped(self):
        """local_day gives None for a timestamp it can't read: that row counts toward no month."""
        self.project("fleet", self.readme(["aa000000-0000"]), handoffs=[TODAY()])
        ui = self.ui([dict(session("A"), id="aa000000-0000")])
        self.spend(ui, [(None, "Live work", "aa000000-0000", "fleet", 7.00),
                        (date.today().isoformat(), "Live work", "aa000000-0000", "fleet", 0.25)])
        self.assertRegex(self.section(ui, "project", "fleet", "Stats")[0], r"This month\s+\$0\.25 · ")

    def test_stats_says_loading_until_spend_data_is_loaded(self):
        self.project("b", self.readme(), handoffs=[TODAY()])
        ui = self.ui()
        self.assertFalse(hasattr(ui, "sjob"))  # the fixture: never loaded
        self.assertEqual(self.section(ui, "project", "b", "Stats"), ["loading…"])
        ui.sjob = {"state": "running", "at": time.time(), "data": None}  # the first read, still running
        self.assertEqual(self.section(ui, "project", "b", "Stats"), ["loading…"])

    # the Repo field

    def test_a_repo_shows_as_a_field_when_the_readme_names_one(self):
        repo = str(Path.home() / "code" / "printer-tools")
        self.project("tools", self.readme().replace("status: active\n", f"status: active\nrepo: {repo}\n"), handoffs=[TODAY()])
        self.project("plain", self.readme(), handoffs=[TODAY()])
        ui = self.ui()
        fields = SessionsField.fields(self, ui, "project", "tools")
        self.assertIn(("Repo", roost.short(repo)), [(label, v) for label, v in fields])
        self.assertTrue(roost.short(repo).startswith("~/"))  # the fixture: short() shortens it
        self.assertNotIn("Repo", [label for label, _ in SessionsField.fields(self, ui, "project", "plain")])


@unittest.skipUnless(roost.App, "Textual isn't installed here: run the tests with ~/.roost/venv/bin/python")
class SectionsOnScreen(unittest.TestCase):
    """The detail pane at 120x40: the fields, then a blank line and each section, its heading bold and its
    lines indented under it; a key-hints line at the pane's bottom; the Stats line fills in once spend loads."""
    run_on, on_project = ProjectRows.run_on, ProjectRows.on_project
    select, keys, sidebar = staticmethod(TextualScreens.select), staticmethod(TextualScreens.keys), staticmethod(ProjectRows.sidebar)
    at, grab, SIZE, DESCRIPTION = staticmethod(TwoPanes.at), staticmethod(TwoPanes.grab), TwoPanes.SIZE, TwoPanes.DESCRIPTION
    read_project = staticmethod(importlib.import_module("roost.core.projectdata").read_project)  # before the stand-ins

    def setUp(self):
        """TwoPanes' folder, plus on Printer fleet: a named Handoff, a note, and a repo; spend that names it."""
        import threading, unittest.mock
        TwoPanes.setUp(self)
        fleet = self.root / "fleet"
        (fleet / "handoffs" / handoff_name(time.time() - 86400, "cc000000")).write_text("---\nname: Toner check\n---\n\nDone.\n")
        (fleet / "notes").mkdir()
        (fleet / "notes" / "idea.md").write_text("# Driver shortlist\n")
        readme = fleet / "README.md"
        readme.write_text(readme.read_text().replace("status: awaiting-vendor\n", "status: awaiting-vendor\nrepo: ~/code/fleet-tools\n"))
        p = next(p for p in self.projects if p["id"] == "fleet")
        p.update(self.read_project(fleet, self.sessions))
        self.release, self.reads = threading.Event(), []
        self.release.set()
        self.addCleanup(self.release.set)

        def gather(table=None, now=None):
            self.reads.append(1)
            self.release.wait(10)
            return {"rows": [(date.today().isoformat(), "Live work", "zz000000-0000", "fleet", 1.25)], "hours": {}, "tokens": {},
                    "unpriced": [], "priced": True}
        patcher = unittest.mock.patch.object(importlib.import_module("roost.core.statsdata"), "gather_spend", gather)
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    async def loaded(pilot):
        """Wait for the spend read to finish, then draw again."""
        import asyncio
        for _ in range(200):
            job = getattr(pilot.app.ui, "sjob", None)
            if job and job["state"] != "running":
                break
            await asyncio.sleep(0.01)
        pilot.app.redraw()
        await pilot.pause()

    @staticmethod
    def pane_bottom(lines):
        """The last screen row the sidebar's border runs down to: the detail pane's bottom row."""
        W = roost.SIDEBAR_W
        return max(i for i, l in enumerate(lines) if len(l) >= W and l[W - 1] in "▕│┃▏")

    def heading_at(self, bold, heading):
        """(y, x) of the bold heading right of the sidebar."""
        got = [(y, x) for y, x, t in bold if t == heading and x >= roost.SIDEBAR_W]
        self.assertEqual(len(got), 1, (heading, bold))
        return got[0]

    def assert_under(self, lines, bold, heading, text, before=None):
        """text is on a line under heading (and above the next heading, before), indented past it."""
        y, x = self.heading_at(bold, heading)
        stop = before or len(lines)
        i = next((i for i in range(y + 1, stop) if text in lines[i][roost.SIDEBAR_W:]), None)
        self.assertIsNotNone(i, (heading, text, lines[y:stop]))
        self.assertGreater(lines[i].index(text, roost.SIDEBAR_W), x, (heading, lines[i]))
        self.assertEqual(lines[y - 1][roost.SIDEBAR_W:].strip(), "", f"no blank line above {heading}")

    def test_the_detail_pane_draws_each_section_under_the_fields(self):
        seen = {}
        self.run_on(self.projects, [self.on_project("fleet"), self.loaded, self.grab(seen, "fleet")], size=self.SIZE)
        lines, bold = seen["fleet"]
        W = roost.SIDEBAR_W
        described = self.at(lines, TwoPanes.DESCRIPTION[:30])
        self.assertIsNotNone(described, lines)
        repo = next((l for l in lines if "~/code/fleet-tools" in l[W:]), "")
        self.assertIn("Repo", repo[W:], lines)
        heads = ["Sessions", "Handoffs", "Notes", "Stats"]
        ys = [self.heading_at(bold, h)[0] for h in heads]
        self.assertEqual(ys, sorted(ys), "the sections in order")
        self.assertGreater(ys[0], described, "the sections come after the fields")
        self.assertFalse([t for y, x, t in bold if t == "Sub-projects" and x >= W], "Printer fleet has no sub-projects")
        nxt = dict(zip(heads, ys[1:] + [None]))
        self.assert_under(lines, bold, "Sessions", "Printer fix", nxt["Sessions"])
        self.assert_under(lines, bold, "Handoffs", "Toner check", nxt["Handoffs"])
        self.assert_under(lines, bold, "Notes", "Driver shortlist", nxt["Notes"])
        self.assert_under(lines, bold, "Stats", "This month", nxt["Stats"])
        stats = next(l for l in lines[ys[3]:] if "This month" in l)
        self.assertRegex(stats, r"This month\s+\$1\.25 · 1 Sessions?")

    def test_a_projects_sub_projects_section_on_screen(self):
        seen = {}
        self.run_on(self.projects, [self.on_project("rollout"), self.loaded, self.grab(seen, "rollout")], size=self.SIZE)
        lines, bold = seen["rollout"]
        y, _ = self.heading_at(bold, "Sub-projects")
        sessions_y, _ = self.heading_at(bold, "Sessions")
        self.assertLess(y, sessions_y, "Sub-projects comes first")
        self.assert_under(lines, bold, "Sub-projects", "Badge Printers", sessions_y)
        self.assert_under(lines, bold, "Sub-projects", "beta", sessions_y)
        row = next(l for l in lines[y:sessions_y] if "Badge Printers" in l[roost.SIDEBAR_W:])
        self.assertIn("running", row)
        row = next(l for l in lines[y:sessions_y] if "beta" in l[roost.SIDEBAR_W:])
        self.assertIn("ready · Handoff", row)

    def test_the_pane_ends_with_a_key_hints_line(self):
        seen = {}
        W = roost.SIDEBAR_W
        self.run_on(self.projects, [self.on_project("rollout"), self.grab(seen, "folded"), self.keys("right"),
                                    self.grab(seen, "open"), self.on_project("fleet"), self.grab(seen, "none")], size=self.SIZE)
        for when, want, never in (("folded", "→ sub-projects", "hide sub-projects"), ("open", "← hide sub-projects", "→ sub-projects"),
                                  ("none", None, "sub-projects")):
            lines = seen[when][0]
            bottom = self.pane_bottom(lines)
            self.assertGreater(bottom, 30, lines)  # the fixture: the pane runs down the screen
            hints = lines[bottom][W:]
            self.assertIn("enter actions", hints, (when, lines))
            if want:
                self.assertIn(want, hints, when)
            self.assertNotIn(never, hints, when)
            self.assertFalse([l for l in lines[bottom - 3:bottom] if "enter actions" in l[W:]], "one hints line, at the bottom")

    def test_drawing_the_tab_starts_the_spend_read_and_stats_fill_in(self):
        self.release.clear()  # the read waits until the test lets it finish
        seen = {}

        async def finish(pilot):
            self.release.set()
        self.run_on(self.projects, [self.on_project("fleet"), self.grab(seen, "loading"), finish, self.loaded,
                                    self.grab(seen, "loaded")], size=self.SIZE)
        self.assertTrue(self.reads, "drawing the Projects tab didn't start the spend read")
        for when, want in (("loading", "loading…"), ("loaded", "This month")):
            lines, bold = seen[when]
            self.assert_under(lines, bold, "Stats", want)
        self.assertFalse([l for l in seen["loaded"][0] if "loading…" in l])


class DetailItems(unittest.TestCase):
    """ui.detail_items(r): what can be selected in a project's or sub-project's detail pane, in the order
    it's drawn: ("sub", sub dict) for each Sub-projects line, ("session", s) for each Sessions line,
    ("handoff", (time, title, path)) for each Handoffs line, and ("note", (mtime, title, path)) for each
    Notes line, exactly the ones detail_sections shows. [] for headers, + New project, and when there are none."""
    setUp_base, tearDown, project, sub = SubProjects.setUp, SubProjects.tearDown, Projects.project, SubProjects.sub
    setUp, ui, readme = DetailSections.setUp, ProjectMarkers.ui, staticmethod(ProjectMarkers.readme)
    handoffs, notes, row = SectionData.handoffs, SectionData.notes, staticmethod(DetailSections.row)
    SECTION_OF = {"sub": "Sub-projects", "session": "Sessions", "handoff": "Handoffs", "note": "Notes"}

    @staticmethod
    def text_of(kind, item):
        """The text an item's line shows: a sub-project's label, a Session's name, a Handoff's or note's title."""
        if kind == "sub":
            return item.get("title") or item["slug"]
        return item["name"] if kind == "session" else item[1]

    def items(self, ui, kind, rid):
        return [(k, tuple(x) if k in ("handoff", "note") else x) for k, x in ui.detail_items(self.row(ui, kind, rid))]

    def assert_matches_the_drawn_lines(self, ui, kind, rid):
        """Each kind's items are the lines its section draws, one for one, in order."""
        r = self.row(ui, kind, rid)
        drawn = dict(ui.detail_sections(r))
        items = self.items(ui, kind, rid)
        for k, heading in self.SECTION_OF.items():
            mine = [x for kk, x in items if kk == k]
            if not mine:
                continue
            lines = list(drawn.get(heading) or [])
            self.assertEqual(len(mine), len(lines), (heading, lines, mine))
            for x, line in zip(mine, lines):
                self.assertIn(self.text_of(k, x), line, heading)

    def fixture(self):
        """rollout: open sub-projects alpha (Badge Printers) and beta, gamma done; Sessions Live one (running),
        Closed recent (an hour ago) and Ancient (9 days ago); Handoffs Toner check (a day ago) and Driver
        push (3 days); notes Badge idea (a minute ago) and Cable plan (2 hours)."""
        ids = ["aa000000-0000", "bb000000-0000", "cc000000-0000"]
        folder = self.project("rollout", self.readme(ids))
        alpha = self.sub("rollout", "alpha")
        (alpha / "README.md").write_text((alpha / "README.md").read_text().replace("\n# alpha\n", "\n# Badge Printers\n"))
        self.sub("rollout", "beta")
        self.sub("rollout", "gamma", status="done")
        now = time.time()
        hs = self.handoffs(folder, [(now - 86400, "name: Toner check\n"), (now - 3 * 86400, "name: Driver push\n")])
        ns = self.notes(folder, [("a.md", "# Badge idea\n", now - 60), ("b.md", "# Cable plan\n", now - 7200)])
        closed = lambda name, mins, sid: dict(session(name, state="closed", kind=None, mins=mins), id=sid, attach=None)
        ui = self.ui([dict(session("Live one", mins=3), id=ids[0]), closed("Closed recent", 60, ids[1]),
                      closed("Ancient", 9 * 1440, ids[2])])
        return ui, hs, ns

    def test_a_projects_items_are_its_sub_projects_sessions_handoffs_and_notes_in_the_order_drawn(self):
        ui, hs, ns = self.fixture()
        items = self.items(ui, "project", "rollout")
        self.assertEqual([k for k, _ in items], ["sub", "sub", "session", "session", "handoff", "handoff", "note", "note"], items)
        subs = [x for k, x in items if k == "sub"]
        self.assertEqual(sorted(s["slug"] for s in subs), ["alpha", "beta"])  # gamma is done
        self.assertEqual([s["name"] for k, s in items if k == "session"], ["Live one", "Closed recent"])
        self.assertEqual([x for k, x in items if k == "handoff"],
                         [(roost.handoff_time(h), title, str(h)) for h, title in zip(hs, ("Toner check", "Driver push"))])
        self.assertEqual([x for k, x in items if k == "note"],
                         [(os.stat(n).st_mtime, title, str(n)) for n, title in zip(ns, ("Badge idea", "Cable plan"))])
        self.assert_matches_the_drawn_lines(ui, "project", "rollout")

    def test_a_handoffs_path_is_its_file_as_a_str(self):
        ui, hs, _ = self.fixture()
        paths = [x[2] for k, x in self.items(ui, "project", "rollout") if k == "handoff"]
        self.assertTrue(all(isinstance(p, str) for p in paths), paths)
        self.assertEqual(paths, [str(h) for h in hs])

    def test_a_sub_projects_items_are_its_own_sessions_handoffs_and_notes(self):
        self.project("rollout", self.readme(["aa000000-0000"]))
        alpha = self.sub("rollout", "alpha", sessions=[("Badge work", "cc000000-0000")])
        now = time.time()
        [h] = self.handoffs(alpha, [(now - 3600, "name: Badge handoff\n")])
        [n] = self.notes(alpha, [("idea.md", "# Badge idea\n", now - 60)])
        ui = self.ui([dict(session("Parent work"), id="aa000000-0000"), dict(session("Badge work", mins=30), id="cc000000-0000")])
        ui.subs_open.add("rollout")
        items = self.items(ui, "sub", "rollout/alpha")
        self.assertEqual([(k, x["name"] if k == "session" else x[1]) for k, x in items],
                         [("session", "Badge work"), ("handoff", "Badge handoff"), ("note", "Badge idea")])
        self.assertEqual(dict(items)["handoff"][2], str(h))
        self.assertEqual(dict(items)["note"][2], str(n))
        self.assert_matches_the_drawn_lines(ui, "sub", "rollout/alpha")

    def test_at_most_5_of_each_list_the_ones_drawn(self):
        ids = [f"{i:02d}000000-0000" for i in range(7)]
        folder = self.project("b", self.readme(ids))
        now = time.time()
        self.handoffs(folder, [(now - (d + 1) * 86400, f"name: Handoff {d}\n") for d in range(7)])
        self.notes(folder, [(f"n{i}.md", f"# Note {i}\n", now - i * 3600) for i in range(7)])
        ui = self.ui([dict(session(f"Run {i}", mins=i + 1), id=sid) for i, sid in enumerate(ids)])
        items = self.items(ui, "project", "b")
        for k in ("session", "handoff", "note"):
            self.assertEqual(sum(kk == k for kk, _ in items), 5, (k, items))
        self.assertEqual([x[1] for k, x in items if k == "handoff"], [f"Handoff {d}" for d in range(5)])
        self.assertEqual([x[1] for k, x in items if k == "note"], [f"Note {i}" for i in range(5)])
        self.assertEqual([x["name"] for k, x in items if k == "session"], [f"Run {i}" for i in range(5)])
        self.assert_matches_the_drawn_lines(ui, "project", "b")

    def test_none_for_headers_new_project_and_a_project_with_nothing_to_list(self):
        self.project("b", self.readme(["aa000000-0000"]))  # its one Session is older than the Sessions list's 7 days
        ui = self.ui([dict(session("Ancient one", state="closed", kind=None, mins=8 * 1440), id="aa000000-0000", attach=None)])
        rows = ui.project_rows()
        self.assertEqual(sorted({r.kind for r in rows}), ["all", "new", "project"])  # the fixture
        for r in rows:
            if r.kind != "all":  # the All projects row shows its own view, not a row's detail
                self.assertEqual(list(ui.detail_items(r)), [], r.kind)


@unittest.skipUnless(roost.App, "Textual isn't installed here: run the tests with ~/.roost/venv/bin/python")
class DetailPane(unittest.TestCase):
    """The detail pane's items, on a real screen (SectionsOnScreen's folder): on a project or sub-project
    row with items, tab moves the keys into the pane; there ↑↓ (home/end) move ui.dsel through
    detail_items, its line drawn selected; enter, a right-click, or a double-click acts on it; tab or esc
    goes back to the sidebar."""
    run_on, on_project = ProjectRows.run_on, ProjectRows.on_project
    select, keys = staticmethod(TextualScreens.select), staticmethod(TextualScreens.keys)
    SIZE, read_project, setUp = TwoPanes.SIZE, staticmethod(SectionsOnScreen.read_project), SectionsOnScreen.setUp
    text_of, DESCRIPTION = staticmethod(DetailItems.text_of), TwoPanes.DESCRIPTION

    def state(self, seen, label):
        """A step that keeps, under label: the focus, dsel, the selected row's items, the screen's lines, the
        pane's rows drawn selected, the menu up (its title and labels), and the view shown."""
        async def step(pilot):
            pilot.app.redraw()
            await pilot.pause()
            ui = pilot.app.ui
            r = ui.project_rows()[ui.psel]
            c = ui.choosing
            seen[label] = {"focus": ui.focus, "dsel": getattr(ui, "dsel", None), "psel": ui.psel,
                           "items": ui.detail_items(r) if hasattr(ui, "detail_items") else [],
                           "lines": screen_text(pilot.app), "raised": pane_raised(pilot.app),
                           "menu": c and (c["title"], [o[0] for o in ui.choice_options() if not is_divider(o)]),
                           "view": ui.TABS[ui.tab].name, "alert": ui.alerting}
        return step

    def item_ys(self, got):
        """The screen row of each item's line in the pane, top to bottom."""
        W, ys, last = roost.SIDEBAR_W, [], 0
        for kind, item in got["items"]:
            t = self.text_of(kind, item)
            y = next((i for i in range(last, len(got["lines"])) if t in got["lines"][i][W:]), None)
            self.assertIsNotNone(y, (t, got["lines"]))
            ys.append(y)
            last = y + 1
        return ys

    @staticmethod
    def labels(acts):
        return [label for label, fn in acts if (label, fn) != roost.DIVIDER]

    def index_of(self, kind, text):
        """A step that sets dsel on the item of kind whose line shows text."""
        def at(ui):
            r = ui.project_rows()[ui.psel]
            ui.dsel = next(i for i, (k, x) in enumerate(ui.detail_items(r)) if k == kind and self.text_of(k, x) == text)
        return self.select(at)

    def fleet_items(self):
        """The fixture: Printer fleet's items, as detail_items gives them."""
        seen = {}
        self.run_on(self.projects, [self.on_project("fleet"), self.state(seen, "x")], size=self.SIZE)
        self.assertTrue(seen["x"]["items"], "no detail items for Printer fleet (ui.detail_items)")
        return seen["x"]

    # focus

    def test_tab_moves_the_keys_into_the_pane_on_a_project_with_items_and_tab_shift_tab_or_esc_back(self):
        seen, keys = {}, ["tab", "tab", "shift+tab", "escape", "shift+tab", "shift+tab", "tab", "escape"]
        steps = [self.on_project("fleet"), self.state(seen, "start")]
        for i, k in enumerate(keys):
            steps += [self.keys(k), self.state(seen, i)]
        self.run_on(self.projects, steps, size=self.SIZE)
        self.assertTrue(seen["start"]["items"], "the fixture: Printer fleet has items")
        self.assertEqual(seen["start"]["focus"], "side")
        self.assertEqual([seen[i]["focus"] for i in range(len(keys))],
                         ["pane", "side", "pane", "side", "pane", "side", "pane", "side"])
        self.assertEqual({seen[i]["view"] for i in range(len(keys))}, {"Projects"})
        self.assertEqual({seen[i]["psel"] for i in range(len(keys))}, {seen["start"]["psel"]}, "the sidebar's selection stays")

    def test_tab_does_nothing_on_a_project_with_no_items(self):
        seen = {}
        self.run_on(self.projects, [self.on_project("quiet"), self.state(seen, "start"), self.keys("tab"), self.state(seen, "tab"),
                                    self.keys("shift+tab"), self.state(seen, "shift+tab")], size=self.SIZE)
        self.assertTrue(hasattr(roost.UI, "detail_items"), "no ui.detail_items")
        self.assertEqual(seen["start"]["items"], [], "the fixture: Quiet one has nothing to list")
        self.assertEqual((seen["tab"]["focus"], seen["shift+tab"]["focus"]), ("side", "side"))

    def test_tab_moves_the_keys_into_the_pane_on_a_sub_project_with_items(self):
        seen = {}

        def on_alpha(ui):
            ui.subs_open.add("rollout")
            ui.psel = next(i for i, r in enumerate(ui.project_rows()) if r.kind == "sub" and r.item["slug"] == "alpha")
        self.run_on(self.projects, [self.select(on_alpha), self.state(seen, "start"), self.keys("tab"), self.state(seen, "tab")],
                    size=self.SIZE)
        self.assertEqual([k for k, _ in seen["start"]["items"]], ["session"], "the fixture: Badge Printers lists Laptop refresh")
        self.assertEqual(seen["tab"]["focus"], "pane")

    # moving in the pane

    def test_up_down_home_and_end_move_dsel_through_the_items_without_wrapping(self):
        seen, keys = {}, ["down", "down", "down", "down", "down", "up", "home", "up", "end", "down"]
        steps = [self.on_project("fleet"), self.keys("tab"), self.state(seen, "tab")]
        for i, k in enumerate(keys):
            steps += [self.keys(k), self.state(seen, i)]
        self.run_on(self.projects, steps, size=self.SIZE)
        n = len(seen["tab"]["items"])
        self.assertEqual(n, 4, "the fixture: Printer fix, two Handoffs, and Driver shortlist")
        self.assertEqual(seen["tab"]["dsel"], 0)
        self.assertEqual([seen[i]["dsel"] for i in range(len(keys))], [1, 2, 3, 3, 3, 2, 0, 0, 3, 3])
        self.assertEqual({seen[i]["focus"] for i in range(len(keys))}, {"pane"})
        self.assertEqual({seen[i]["psel"] for i in range(len(keys))}, {seen["tab"]["psel"]}, "↑↓ move in the pane, not the sidebar")

    def test_the_selected_items_line_is_drawn_selected_only_while_the_pane_has_the_keys(self):
        seen = {}
        self.run_on(self.projects, [self.on_project("fleet"), self.state(seen, "side"), self.keys("tab"), self.state(seen, 0),
                                    self.keys("down"), self.state(seen, 1), self.keys("down"), self.state(seen, 2),
                                    self.keys("down"), self.state(seen, 3), self.keys("escape"), self.state(seen, "back")],
                    size=self.SIZE)
        ys = self.item_ys(seen["side"])
        self.assertEqual(len(ys), 4, seen["side"]["items"])
        self.assertFalse(seen["side"]["raised"] & set(ys), "no item line is selected while the sidebar has the keys")
        for i in range(4):
            self.assertEqual(self.item_ys(seen[i]), ys, "the lines stay put")
            self.assertEqual(seen[i]["raised"] & set(ys), {ys[i]}, (i, seen[i]["lines"][ys[i]]))
        self.assertFalse(seen["back"]["raised"] & set(ys), "esc: back to the sidebar, nothing selected in the pane")

    def test_changing_the_sidebars_selection_resets_dsel(self):
        seen = {}
        self.run_on(self.projects, [self.on_project("fleet"), self.keys("tab", "down", "down"), self.state(seen, "moved"),
                                    self.keys("escape", "down"), self.state(seen, "next row"), self.keys("up"), self.state(seen, "back"),
                                    self.keys("tab"), self.state(seen, "pane")], size=self.SIZE)
        self.assertEqual(seen["moved"]["dsel"], 2)
        self.assertNotEqual(seen["next row"]["psel"], seen["moved"]["psel"])
        self.assertEqual(seen["next row"]["dsel"], 0)
        self.assertEqual(seen["back"]["psel"], seen["moved"]["psel"])
        self.assertEqual((seen["pane"]["focus"], seen["pane"]["dsel"]), ("pane", 0))

    # enter: the item's menu

    def test_enter_on_a_session_opens_its_session_actions(self):
        seen, want = {}, {}

        async def expect(pilot):
            ui = pilot.app.ui
            s = next(s for s in ui.sessions if s["name"] == "Printer fix")
            want["labels"] = self.labels(ui.session_actions(s))
        self.run_on(self.projects, [self.on_project("fleet"), self.keys("tab"), self.index_of("session", "Printer fix"),
                                    expect, self.keys("enter"), self.state(seen, "menu")], size=self.SIZE)
        self.assertEqual(seen["menu"]["menu"], ("Printer fix", want["labels"]))

    def test_enter_on_a_note_opens_that_notes_actions_with_its_project(self):
        seen, want = {}, {}

        async def expect(pilot):
            ui = pilot.app.ui
            path = self.root / "fleet" / "notes" / "idea.md"
            want["labels"] = self.labels(ui.note_actions(roost.read_note(path, "fleet")))
        self.run_on(self.projects, [self.on_project("fleet"), self.keys("tab"), self.index_of("note", "Driver shortlist"),
                                    expect, self.keys("enter"), self.state(seen, "menu")], size=self.SIZE)
        title, labels = seen["menu"]["menu"]
        self.assertEqual((title, labels), ("Driver shortlist", want["labels"]))
        self.assertIn("Move to another project", labels, "a project's note moves to another one")
        self.assertNotIn("Make it a project", labels, "it's in a project already")

    def test_enter_on_a_sub_project_opens_its_sub_project_actions(self):
        seen, want = {}, {}

        async def expect(pilot):
            ui = pilot.app.ui
            p = next(p for p in ui.projects if p["id"] == "rollout")
            alpha = next(s for s in p["subs"] if s["slug"] == "alpha")
            # titled as its own row's menu in the sidebar is (label_of), or by the label its line shows
            want["titles"], want["labels"] = {roost.label_of(alpha), alpha.get("title")}, self.labels(ui.sub_actions(alpha))
        self.run_on(self.projects, [self.on_project("rollout"), self.keys("tab"), self.index_of("sub", "Badge Printers"),
                                    expect, self.keys("enter"), self.state(seen, "menu")], size=self.SIZE)
        self.assertIsNotNone(seen["menu"]["menu"])
        title, labels = seen["menu"]["menu"]
        self.assertIn(title, want["titles"])
        self.assertEqual(labels, want["labels"])

    def test_enter_on_a_handoff_opens_open_it_and_show_it_in_finder(self):
        import unittest.mock
        path = str(next((self.root / "fleet" / "handoffs").glob("*-cc000000.md")))  # Toner check
        edited, shown = [], []
        for label, got in (("Open it", edited), ("Show it in Finder", shown)):
            seen = {}

            async def pick(pilot, label=label):
                ui = pilot.app.ui
                at = next((i for i, o in enumerate(ui.choice_options()) if o[0] == label), None) if ui.choosing else None
                if at is not None:  # else the menu's assertion below says what came up instead
                    ui.choosing["sel"] = at
                    await pilot.press("enter")
            with unittest.mock.patch.object(roost.UI, "edit", lambda ui, p: edited.append(str(p))), \
                    unittest.mock.patch.object(roost.UI, "show_in_finder", lambda ui, p, reveal=False: shown.append(str(p))):
                self.run_on(self.projects, [self.on_project("fleet"), self.keys("tab"), self.index_of("handoff", "Toner check"),
                                            self.keys("enter"), self.state(seen, "menu"), pick], size=self.SIZE)
            self.assertEqual(seen["menu"]["menu"], ("Toner check", ["Open it", "Show it in Finder"]))
            self.assertEqual(got, [path], label)
        self.assertEqual((edited, shown), ([path], [path]), "each action did only its own thing")

    # the mouse

    def test_a_click_on_an_items_line_selects_it_and_moves_the_keys_into_the_pane(self):
        before = self.fleet_items()
        ys = self.item_ys(before)
        note = next(i for i, (k, _) in enumerate(before["items"]) if k == "note")
        seen = {}
        self.run_on(self.projects, [self.on_project("fleet"), lambda pilot: pilot.click(offset=(roost.SIDEBAR_W + 8, ys[note])),
                                    self.state(seen, "clicked")], size=self.SIZE)
        got = seen["clicked"]
        self.assertEqual((got["focus"], got["dsel"], got["menu"]), ("pane", note, None))
        self.assertEqual(got["raised"] & set(ys), {ys[note]})

    def test_a_double_click_on_an_items_line_runs_its_first_action(self):
        import unittest.mock
        before = self.fleet_items()
        ys = self.item_ys(before)
        at = next(i for i, (k, x) in enumerate(before["items"]) if k == "handoff" and x[1] == "Toner check")
        edited = []
        with unittest.mock.patch.object(roost.UI, "edit", lambda ui, p: edited.append(str(p))):
            self.run_on(self.projects, [self.on_project("fleet"),
                                        lambda pilot: pilot.click(offset=(roost.SIDEBAR_W + 8, ys[at]), times=2)], size=self.SIZE)
        self.assertEqual(edited, [before["items"][at][1][2]], "Open it: the Handoff's file in the editor")

    def test_a_right_click_on_an_items_line_opens_its_menu(self):
        before = self.fleet_items()
        ys = self.item_ys(before)
        at = next(i for i, (k, x) in enumerate(before["items"]) if k == "session")
        seen = {}
        self.run_on(self.projects, [self.on_project("fleet"), lambda pilot: pilot.click(offset=(roost.SIDEBAR_W + 8, ys[at]), button=3),
                                    self.state(seen, "menu")], size=self.SIZE)
        self.assertIsNotNone(seen["menu"]["menu"])
        self.assertEqual(seen["menu"]["menu"][0], "Printer fix")
        self.assertEqual(seen["menu"]["dsel"], at)

    def test_a_click_on_a_line_that_isnt_an_item_does_nothing_harmful(self):
        before = self.fleet_items()
        W = roost.SIDEBAR_W
        heading = next(i for i, l in enumerate(before["lines"]) if l[W:].strip() == "Handoffs")
        seen = {}
        self.run_on(self.projects, [self.on_project("fleet"), lambda pilot: pilot.click(offset=(W + 6, heading)),
                                    self.state(seen, "heading")], size=self.SIZE)
        got = seen["heading"]
        self.assertEqual((got["menu"], got["alert"], got["view"]), (None, None, "Projects"))
        self.assertIn(got["dsel"], (None, 0), "the selection stays where it was")
        seen = {}
        self.run_on(self.projects, [self.on_project("quiet"), lambda pilot: pilot.click(offset=(W + 6, heading)),
                                    self.state(seen, "quiet")], size=self.SIZE)
        got = seen["quiet"]
        self.assertEqual((got["focus"], got["menu"], got["alert"]), ("side", None, None), "nothing to select: the keys stay in the sidebar")


class StatusLine(unittest.TestCase):
    """status_line(): (left, right) for the line above the key bar; the right says how many Sessions are running."""
    setUp, tearDown = Projects.setUp, Projects.tearDown

    def test_it_counts_the_sessions_that_arent_closed(self):
        ui = Projects.ui(self)
        ui.sessions = [session("A", state="busy"), session("B", state="waiting"), session("C"),
                       session("D", state="closed", kind=None)]
        self.assertEqual(ui.status_line(), ("", "3 running"))
        ui.sessions = [session("D", state="closed", kind=None)]
        self.assertEqual(ui.status_line(), ("", "nothing running"))
        ui.sessions = []
        self.assertEqual(ui.status_line(), ("", "nothing running"))


class RecentProjects(unittest.TestCase):
    """The sidebar's projects with no search: the ten most recently active open ones, newest first (one never
    worked on after those that were), each followed by its open sub-projects once → opens it; then the All
    projects row; then + New project. No section headers, and no closed project. A search lists every match,
    open ones newest first then closed ones, with no cap, no headers, and no All projects row."""
    setUp, tearDown, project, sub = SubProjects.setUp, SubProjects.tearDown, Projects.project, SubProjects.sub
    ui, shown = Projects.ui, Projects.shown
    DAY = 86400

    @staticmethod
    def readme(label, summary="Printer work.", status="active"):
        return f"---\nsummary: {json.dumps(summary)}\nstatus: {status}\nsessions: []\n---\n\n# {label}\n"

    def many(self):
        """Thirteen open projects: open-00 to open-11, worked on 0, 3, 6, … 33 days ago (so Active and
        Inactive ones mix), and never-one, never worked on; shut-a, closed, worked on an hour ago (newer than
        most open ones); shut-b, closed, 40 days ago; decoy, open, worked on 50 days ago, the one whose summary
        holds no "printer". Fifteen projects in all; ids open-NN in the order they should list."""
        now = time.time()
        for i in range(12):
            self.project(f"open-{i:02d}", self.readme(f"Open {i:02d}"), handoffs=[handoff_name(now - i * 3 * self.DAY - 60)])
        self.project("never-one", self.readme("Never one"))
        self.project("shut-a", self.readme("Shut a", status="closed"), handoffs=[handoff_name(now - 3600)])
        self.project("shut-b", self.readme("Shut b", status="closed"), handoffs=[handoff_name(now - 40 * self.DAY)])
        self.project("decoy", self.readme("Decoy", "Laptop work."), handoffs=[handoff_name(now - 50 * self.DAY)])
        ui = self.ui()
        ui.notes = []
        self.assertEqual(len(ui.projects), 16)  # the fixture
        return ui

    def test_the_sidebar_lists_the_ten_most_recent_open_projects_then_all_projects_then_new_project(self):
        ui = self.many()
        self.assertEqual(self.shown(ui), [("project", f"open-{i:02d}") for i in range(10)]
                         + [("all", None), ("new", "+ New project")])

    def test_ten_or_fewer_open_projects_all_show_and_all_projects_still_follows(self):
        now = time.time()
        self.project("older", self.readme("Older"), handoffs=[handoff_name(now - 30 * self.DAY)])
        self.project("newer", self.readme("Newer"), handoffs=[handoff_name(now - 60)])
        self.project("never-one", self.readme("Never one"))  # never worked on: after the ones that were
        self.project("shut", self.readme("Shut", status="closed"), handoffs=[handoff_name(now - 30)])  # closed: not listed
        ui = self.ui()
        self.assertEqual(self.shown(ui), [("project", "newer"), ("project", "older"), ("project", "never-one"),
                                          ("all", None), ("new", "+ New project")])

    def test_an_opened_projects_sub_projects_follow_it_and_dont_count_toward_the_ten(self):
        ui = self.many()
        self.sub("open-03", "alpha")
        self.sub("open-03", "beta")
        ui.load_projects()
        ui.psel = self.shown(ui).index(("project", "open-03"))
        ui.keys_projects("right", ui.project_rows())
        self.assertEqual(self.shown(ui), [("project", f"open-{i:02d}") for i in range(4)]
                         + [("sub", "open-03/alpha"), ("sub", "open-03/beta")]
                         + [("project", f"open-{i:02d}") for i in range(4, 10)]
                         + [("all", None), ("new", "+ New project")])

    def test_a_search_lists_every_match_open_newest_first_then_closed_with_no_all_projects_row(self):
        ui = self.many()
        ui.pquery = "printer"
        self.assertEqual(self.shown(ui), [("project", f"open-{i:02d}") for i in range(12)]
                         + [("project", "never-one"), ("project", "shut-a"), ("project", "shut-b")])


@unittest.skipUnless(roost.App, "Textual isn't installed here: run the tests with ~/.roost/venv/bin/python")
class AllProjects(unittest.TestCase):
    """The All projects row under the sidebar's projects, All projects (N) with N every project open or
    closed, and the view it shows in the right pane (ui.entry "All projects"): Active (N), Inactive (N), and
    Closed (N), each newest first, with ↑↓ over the projects, enter for one's actions, → for its detail, esc
    back, / to filter."""
    run_on, chosen, sidebar = ProjectRows.run_on, staticmethod(ProjectRows.chosen), staticmethod(ProjectRows.sidebar)
    select, keys, proj = staticmethod(TextualScreens.select), staticmethod(TextualScreens.keys), staticmethod(TextualScreens.proj)
    grab, detail_name = staticmethod(TwoPanes.grab), staticmethod(TwoPanes.detail_name)
    SIZE = (120, 40)
    DAY = 86400
    LISTED = ["Active (3)", "Toner order", "Printer fleet", "Badge rollout", "Inactive (2)", "VPN refresh", "Wiki cleanup",
              "Closed (2)", "Laptop refresh", "Old audit"]  # what the view lists for the fixture, top to bottom

    def setUp(self):
        TextualScreens.setUp(self)
        now = time.time()

        def project(pid, label, days, closed=False, summary="", tags=()):
            p = self.proj(pid, [], label=label)
            p.update(last=now - days * self.DAY, closed=closed, status="closed" if closed else "active",
                     summary=summary, tags=list(tags))
            return p
        # Active: worked on in the last RECENT_PROJECT_DAYS days; Inactive: open, older; Closed. Laptop refresh is
        # closed but newer than two Active ones; Wiki cleanup's summary and Old audit's tag hold "toner".
        self.projects = [project("toner", "Toner order", 0.05), project("laptops", "Laptop refresh", 0.1, closed=True),
                         project("fleet", "Printer fleet", 2), project("badges", "Badge rollout", 5),
                         project("vpn", "VPN refresh", 20), project("wiki", "Wiki cleanup", 60, summary="Toner budget notes."),
                         project("audit", "Old audit", 90, closed=True, tags=["toner-vendor"])]

    @staticmethod
    def all_row(ui):
        """The index of the All projects row in the sidebar's rows."""
        rows = ui.project_rows()
        at = [i for i, r in enumerate(rows) if r.kind == "all"]
        assert at, f"no All projects row in the sidebar: {[r.kind for r in rows]}"
        return at[0]

    def above_all(self):
        """A step that selects the sidebar row just above All projects: the last project."""
        return self.select(lambda ui: setattr(ui, "psel", self.all_row(ui) - 1))

    def to_all(self):
        """Steps that move the sidebar's selection down onto All projects."""
        return [self.above_all(), self.keys("down")]

    def into_view(self):
        """Steps that show All projects and move the keys into its pane (enter on the row)."""
        return self.to_all() + [self.keys("enter")]

    @staticmethod
    def state(seen, label):
        """A step that keeps, under label: the entry shown, the focus, the selected sidebar row, the menu up
        (its title and labels), the prompt up, and the screen's lines and bold runs."""
        async def step(pilot):
            pilot.app.redraw()
            await pilot.pause()
            ui = pilot.app.ui
            rows = ui.project_rows()
            r = rows[ui.psel] if rows and ui.psel < len(rows) else None
            seen[label] = {"entry": ui.entry, "focus": ui.focus, "row": r and (r.kind, r.item.get("id")),
                           "menu": ui.choosing and (ui.choosing["title"], [l for l, _ in ui.choosing["options"]]),
                           "asking": ui.asking, "lines": screen_text(pilot.app), "bold": bold_text(pilot.app)}
        return step

    @staticmethod
    def pane(lines):
        """The right pane's part of each line, stripped, with a selection mark taken off."""
        return [l[roost.SIDEBAR_W:].strip().lstrip("›").strip() for l in lines]

    def listed(self, lines):
        """The pane's section headers and project names, top to bottom (the fixture's labels only)."""
        labels = {p["label"] for p in self.projects}
        out = []
        for l in self.pane(lines):
            if re.fullmatch(r"(Active|Inactive|Closed) \(\d+\)", l):
                out.append(l)
            else:
                out += [n for n in labels if l.startswith(n)]
        return out

    def test_down_from_the_last_project_selects_all_projects_and_shows_its_view(self):
        seen = {}
        self.run_on(self.projects, self.to_all() + [self.state(seen, "on")], size=self.SIZE)
        got = seen["on"]
        self.assertEqual((got["entry"], got["focus"]), ("All projects", "side"))
        self.assertIn("All projects (7)", "\n".join(self.sidebar(got["lines"])), "every project, open and closed")
        self.assertEqual(self.listed(got["lines"]), self.LISTED)
        self.assertEqual(roost.ENTRIES, ENTRIES, "All projects isn't one of the entries")
        side = self.sidebar(got["lines"])
        ys = [next((i for i, l in enumerate(side) if re.fullmatch(rf"{name}( +\d+)?", l.strip(" ▕"))), None) for name in ENTRIES]
        self.assertTrue(all(ys) and ys == sorted(ys), (ys, side))  # the entries still follow under the projects
        self.assertLess(next(i for i, l in enumerate(side) if "All projects (" in l), ys[0])

    def test_up_from_all_projects_goes_back_to_the_last_project(self):
        seen = {}
        app = self.run_on(self.projects, self.to_all() + [self.keys("up"), self.state(seen, "up")], size=self.SIZE)
        self.assertFalse(seen["up"]["entry"])
        self.assertEqual(self.chosen(app), "wiki")  # the oldest of the five open projects

    def test_down_from_all_projects_goes_to_new_project_then_the_entries_and_up_comes_back(self):
        seen = {}
        self.run_on(self.projects, self.to_all() + [self.keys("down"), self.state(seen, "new"), self.keys("up"),
                                                    self.state(seen, "back"), self.keys("down", "down"),
                                                    self.state(seen, "entry")], size=self.SIZE)
        self.assertEqual((seen["new"]["entry"], seen["new"]["row"][0]), (None, "new"))
        self.assertEqual(seen["back"]["entry"], "All projects")
        self.assertEqual(seen["entry"]["entry"], ENTRIES[0])

    def test_a_click_on_all_projects_shows_its_view(self):
        seen = {}
        self.run_on(self.projects, [self.state(seen, "before")], size=self.SIZE)
        y = next((i for i, l in enumerate(self.sidebar(seen["before"]["lines"])) if "All projects (" in l), None)
        self.assertIsNotNone(y, self.sidebar(seen["before"]["lines"]))
        self.run_on(self.projects, [lambda pilot: pilot.click(offset=(6, y)), self.state(seen, "clicked")], size=self.SIZE)
        got = seen["clicked"]
        self.assertEqual((got["entry"], got["menu"], got["asking"]), ("All projects", None, None))
        self.assertEqual(self.listed(got["lines"]), self.LISTED)

    def test_enter_tab_or_right_on_all_projects_moves_the_keys_into_its_pane(self):
        for key in ("enter", "tab", "right"):
            seen = {}
            self.run_on(self.projects, self.to_all() + [self.keys(key), self.state(seen, key)], size=self.SIZE)
            got = seen[key]
            with self.subTest(key=key):
                self.assertEqual((got["entry"], got["focus"], got["menu"], got["asking"]), ("All projects", "pane", None, None))
                self.assertEqual(self.listed(got["lines"]), self.LISTED)

    def test_all_projects_lists_active_inactive_and_closed_with_counts_newest_first(self):
        seen = {}
        self.run_on(self.projects, self.into_view() + [self.state(seen, "view")], size=self.SIZE)
        self.assertEqual(self.listed(seen["view"]["lines"]), self.LISTED)

    def test_a_section_with_nothing_in_it_is_left_out(self):
        seen = {}
        self.projects = [p for p in self.projects if not p["closed"] and p["id"] not in ("vpn", "wiki")]  # Active only
        self.run_on(self.projects, self.into_view() + [self.state(seen, "view")], size=self.SIZE)
        self.assertEqual(self.listed(seen["view"]["lines"]), ["Active (3)", "Toner order", "Printer fleet", "Badge rollout"])
        pane = "\n".join(self.pane(seen["view"]["lines"]))
        self.assertNotIn("Inactive (", pane)
        self.assertNotIn("Closed (", pane)

    def test_up_and_down_move_over_the_projects_not_the_headers_and_enter_opens_its_actions(self):
        import unittest.mock
        seen = {}
        acts = lambda ui, p: [(f"Act on {p['id']}", lambda: None), ("Second", lambda: None)]  # one menu per project
        with unittest.mock.patch.object(roost.UI, "project_actions", acts):
            self.run_on(self.projects, self.into_view() + [
                self.keys("enter"), self.state(seen, "first"), self.keys("escape"),
                self.keys("down", "down", "down"), self.keys("enter"), self.state(seen, "inactive"), self.keys("escape"),
                self.keys("up"), self.keys("enter"), self.state(seen, "back"), self.keys("escape"),
                self.keys("down", "down", "down"), self.keys("enter"), self.state(seen, "closed"), self.keys("escape"),
                self.keys("up", "up", "up", "up", "up", "up", "up"), self.keys("enter"), self.state(seen, "top")],
                size=self.SIZE)
        self.assertEqual(seen["first"]["menu"], ("Toner order", ["Act on toner", "Second"]))  # the newest, at the top
        self.assertEqual(seen["inactive"]["menu"][0], "VPN refresh", "↓ from the last Active one skips the Inactive header")
        self.assertEqual(seen["back"]["menu"][0], "Badge rollout")
        self.assertEqual(seen["closed"]["menu"][0], "Laptop refresh", "↓ skips the Closed header")
        self.assertEqual(seen["top"]["menu"][0], "Toner order", "↑ stops at the first project, never on the Active header")
        self.assertEqual(seen["top"]["focus"], "pane")

    def test_enter_offers_the_same_actions_as_the_projects_sidebar_row(self):
        seen = {}

        async def sidebar_actions(pilot):
            ui = pilot.app.ui
            seen["sidebar"] = [l for l, _ in ui.row_actions(next(r for r in ui.project_rows() if r.item.get("id") == "fleet"))]
        self.run_on(self.projects, [sidebar_actions] + self.into_view() + [self.keys("down"), self.keys("enter"),
                                                                           self.state(seen, "menu")], size=self.SIZE)
        self.assertEqual(seen["menu"]["menu"], ("Printer fleet", seen["sidebar"]))

    def test_right_shows_the_selected_projects_detail_and_esc_goes_back_to_the_list_with_it_selected(self):
        seen = {}
        self.run_on(self.projects, self.into_view() + [self.keys("down"), self.keys("right"), self.state(seen, "detail"),
                                                       self.keys("escape"), self.state(seen, "list"), self.keys("enter"),
                                                       self.state(seen, "menu")], size=self.SIZE)
        detail = seen["detail"]
        pane = "\n".join(self.pane(detail["lines"]))
        self.assertEqual(self.detail_name(detail["bold"])[0], "Printer fleet", "its name first, as the sidebar's detail draws it")
        self.assertRegex(pane, r"Folder +/nowhere/fleet +Id +fleet")
        for heading in ("Sessions", "Handoffs", "Notes"):
            self.assertIn(heading, self.detail_name(detail["bold"]), f"its {heading} section")
        self.assertNotIn("Active (", pane, "the detail in place of the list")
        self.assertEqual((detail["entry"], detail["focus"]), ("All projects", "pane"))
        self.assertEqual(self.listed(seen["list"]["lines"]), self.LISTED)
        self.assertEqual((seen["list"]["entry"], seen["list"]["focus"]), ("All projects", "pane"))
        self.assertEqual(seen["menu"]["menu"][0], "Printer fleet", "the same project still selected")

    def test_esc_from_the_list_returns_the_keys_to_the_sidebar(self):
        seen = {}
        self.run_on(self.projects, self.into_view() + [self.keys("escape"), self.state(seen, "esc")], size=self.SIZE)
        self.assertEqual((seen["esc"]["entry"], seen["esc"]["focus"]), ("All projects", "side"))

    def test_slash_filters_the_list_by_name_summary_and_tags(self):
        seen = {}
        self.run_on(self.projects, self.into_view() + [self.keys("slash"), self.keys(*"toner"), self.keys("enter"),
                                                       self.state(seen, "filtered")], size=self.SIZE)
        self.assertEqual(self.listed(seen["filtered"]["lines"]),
                         ["Active (1)", "Toner order", "Inactive (1)", "Wiki cleanup", "Closed (1)", "Old audit"])

    def test_a_double_click_on_a_project_in_the_list_runs_its_first_action(self):
        import unittest.mock
        seen, ran = {}, []
        self.run_on(self.projects, self.to_all() + [self.state(seen, "view")], size=self.SIZE)
        y = next((i for i, l in enumerate(self.pane(seen["view"]["lines"])) if l.startswith("VPN refresh")), None)
        self.assertIsNotNone(y, self.pane(seen["view"]["lines"]))
        acts = lambda ui, p: [("First", lambda: ran.append(p["id"])), ("Second", lambda: ran.append("second"))]
        with unittest.mock.patch.object(roost.UI, "project_actions", acts):
            self.run_on(self.projects, self.to_all() + [lambda pilot: pilot.click(offset=(roost.SIDEBAR_W + 8, y), times=2)],
                        size=self.SIZE)
        self.assertEqual(ran, ["vpn"])


if __name__ == "__main__":
    unittest.main()


# ---------- the detail layout: the top area, the name line, the fields, Current state and Next, the newest
# Handoff's lines, and the Stats sparkline

STATE_TEXT = ("Drivers are packaged for both offices and the north office already runs on the new toner supplier, "
              "while the south office waits on a vendor quote before its printers move over.")
NEXT_TEXT = "Chase the south office quote, then book the printer swap for a quiet afternoon."
DRILL_NEXT = "Run the restore drill again once the backup window moves."
DID = ["Swapped the toner in the north office.", "Updated the driver list.", "Filed the vendor ticket."]


def words(text):
    return " ".join(text.split())


class StateAndNextData(unittest.TestCase):
    """read_project's "state" and "next": the README's text under ## Current state and ## Next, up to the
    next ## heading, stripped; "" when the heading is missing. Sub-projects get them too."""
    setUp, tearDown, project, sub = SubProjects.setUp, SubProjects.tearDown, Projects.project, SubProjects.sub
    FRONT = "---\nstatus: active\nsessions: []\n---\n\n# Printer fleet\n\nIntro text.\n\n"

    def test_the_text_under_each_heading_up_to_the_next_level_two_heading(self):
        folder = self.project("fleet", self.FRONT + "## Current state\n\nNorth office done.\n\n### Detail\nSouth waits on a quote.\n\n"
                                                    "## Next\n\n- Chase the quote\n- Book the swap\n\n## Key decisions\n\nIgnored text.\n")
        p = roost.read_project(folder)
        self.assertEqual(p["state"], "North office done.\n\n### Detail\nSouth waits on a quote.")  # a ### heading stays in it
        self.assertEqual(p["next"], "- Chase the quote\n- Book the swap")

    def test_a_last_section_runs_to_the_end_and_a_missing_one_is_empty(self):
        only_next = self.project("drill", self.FRONT + "## Next\n\nRun it again.\n")
        neither = self.project("plain")  # the shared README: no such headings
        self.assertEqual((roost.read_project(only_next)["state"], roost.read_project(only_next)["next"]), ("", "Run it again."))
        self.assertEqual((roost.read_project(neither)["state"], roost.read_project(neither)["next"]), ("", ""))

    def test_a_sub_projects_state_and_next(self):
        self.project("rollout")
        self.sub("rollout", "alpha", body="## Current state\n\nBadge printers ordered.\n\n## Next\n\nInstall them.\n")
        self.sub("rollout", "beta")
        subs = {s["slug"]: s for s in roost.read_subprojects(self.root / "rollout")}
        self.assertEqual((subs["alpha"]["state"], subs["alpha"]["next"]), ("Badge printers ordered.", "Install them."))
        self.assertEqual((subs["beta"]["state"], subs["beta"]["next"]), ("", ""))


class LatestHandoffData(unittest.TestCase):
    """read_project's "handoff_lines": up to 3 lines from the newest Handoff's body, for the detail pane to
    show under its row: the first non-empty lines under ### What I did (a roll-up's ## or ### What shipped),
    else under ## Summary, each stopping at the next heading; [] when there's no Handoff or none of those
    headings. Only the newest Handoff counts."""
    setUp, tearDown, project, sub = SubProjects.setUp, SubProjects.tearDown, Projects.project, SubProjects.sub

    def write(self, folder, ago, body, meta="name: Printer work\n", sid="aaaaaaaa"):
        path = folder / "handoffs" / handoff_name(time.time() - ago, sid)
        path.write_text(f"---\n{meta}---\n\n{body}")
        return path

    def lines(self, body, meta="name: Printer work\n"):
        """handoff_lines of a new project whose one Handoff has this body."""
        self.made = getattr(self, "made", 0) + 1
        folder = self.project(f"fleet-{self.made}")
        self.write(folder, 3600, body, meta)
        return roost.read_project(folder)["handoff_lines"]

    def test_the_first_three_non_empty_lines_under_what_i_did(self):
        body = ("### What I did\n\n" + DID[0] + "\n\n" + DID[1] + "\n" + DID[2] + "\nA fourth line stays hidden.\n"
                "### What worked\nRestarting them helped.\n")
        self.assertEqual(self.lines(body), DID)

    def test_the_lines_stop_at_the_next_heading(self):
        self.assertEqual(self.lines("### What I did\nOnly this one.\n\n### What worked\nRestarting them helped.\n"), ["Only this one."])

    def test_a_rollup_takes_what_shipped_at_either_level(self):
        for level in ("##", "###"):
            body = f"{level} What shipped\n\nBadge printers in every office.\nToner alerts.\n\n{level} Open questions\nNone.\n"
            self.assertEqual(self.lines(body, meta="rollup: alpha\n"), ["Badge printers in every office.", "Toner alerts."], level)

    def test_summary_is_the_fallback_and_what_i_did_wins_over_it(self):
        self.assertEqual(self.lines("## Summary\nSummary line one.\nSummary line two.\n\n## Details\nNot this.\n"),
                         ["Summary line one.", "Summary line two."])
        self.assertEqual(self.lines("## Summary\nSummary line.\n\n### What I did\nThe work itself.\n"), ["The work itself."])

    def test_none_without_a_handoff_or_any_of_the_headings_and_only_the_newest_counts(self):
        folder = self.project("fleet")
        self.assertEqual(list(roost.read_project(folder)["handoff_lines"]), [])
        self.write(folder, 2 * 86400, "### What I did\nOlder work.\n", sid="bbbbbbbb")
        self.write(folder, 3600, "Just some text.\n")
        self.assertEqual(list(roost.read_project(folder)["handoff_lines"]), [], "the newest has none of the headings")

    def test_a_sub_projects_are_its_own(self):
        self.project("rollout")
        alpha = self.sub("rollout", "alpha")
        self.write(alpha, 3600, "### What I did\nBadge work done.\n")
        [s] = [x for x in roost.read_subprojects(self.root / "rollout") if x["slug"] == "alpha"]
        self.assertEqual(s["handoff_lines"], ["Badge work done."])


class DetailFields(unittest.TestCase):
    """project_panel's fields for a project, in order, each only when it has something: Description (always:
    "no description yet" when empty), Sessions (the only Sessions count), Repo, Category and Tags on one row
    (whichever is set; no row when neither), then Folder (the path alone). No Status field: the status and
    when it was worked are on the name line. A sub-project gets the same rules, and keeps Type."""
    setUp, tearDown, project, sub = SubProjects.setUp, SubProjects.tearDown, Projects.project, SubProjects.sub
    ui = ProjectMarkers.ui

    @staticmethod
    def readme(description="Printers for both offices.", category="", tags=(), repo=""):
        return (f"---\ndescription: {json.dumps(description)}\nstatus: active\n" + (f"category: {category}\n" if category else "")
                + (f"tags: [{', '.join(tags)}]\n" if tags else "") + (f"repo: {repo}\n" if repo else "")
                + "sessions: []\n---\n\n# Printer fleet\n")

    @staticmethod
    def rows(ui, kind, rid):
        """Each field as one line of text, its label then its value."""
        rows = ui.project_rows()
        ui.psel = next(i for i, r in enumerate(rows) if r.kind == kind and r.item["id"] == rid)
        return [f"{label}  {value}".strip() for label, value in ui.project_panel(rows, 40, 120)[1]]

    def index(self, rows, pattern):
        got = [i for i, r in enumerate(rows) if re.match(pattern, r)]
        self.assertEqual(len(got), 1, (pattern, rows))
        return got[0]

    def assert_no_old_fields(self, rows):
        self.assertFalse([r for r in rows if r.startswith("Status")], rows)
        self.assertFalse([r for r in rows if "Last worked" in r or re.search(r"\bSessions\s+\d", r)], rows)
        self.assertFalse([r for r in rows if re.search(r"\bId\b", r)], rows)

    def test_a_projects_fields_in_order(self):
        repo = str(Path.home() / "code" / "fleet-tools")
        folder = self.project("fleet", self.readme(category="procurement", tags=("zq-tag", "toner"), repo=repo), handoffs=[TODAY()])
        rows = self.rows(self.ui(), "project", "fleet")
        at = [self.index(rows, p) for p in (r"Description\b", r"Sessions\b", r"Repo\b", r"Category\b", r"Folder\b")]
        self.assertEqual(at, sorted(at), rows)
        self.assertEqual(at[0], 0, "Description comes first")
        self.assertRegex(rows[at[3]], r"^Category\s+procurement\s+Tags\s+zq-tag, toner$")
        self.assertRegex(rows[at[4]], rf"^Folder\s+{re.escape(roost.short(str(folder)))}$")
        self.assertFalse([r for r in rows if r.startswith("Tags")], "Tags is on Category's row")
        self.assert_no_old_fields(rows)

    def test_category_and_tags_share_a_row_that_goes_when_both_are_empty(self):
        self.project("cat", self.readme(category="drills"), handoffs=[TODAY()])
        self.project("tagged", self.readme(tags=("toner",)), handoffs=[TODAY()])
        self.project("bare", self.readme(), handoffs=[TODAY()])
        ui = self.ui()
        rows = self.rows(ui, "project", "cat")
        self.assertRegex(rows[self.index(rows, r"Category\b")], r"^Category\s+drills$")
        self.assertFalse([r for r in rows if "Tags" in r], rows)
        rows = self.rows(ui, "project", "tagged")
        self.assertRegex(rows[self.index(rows, r"Tags\b")], r"^Tags\s+toner$")
        self.assertFalse([r for r in rows if "Category" in r], rows)
        rows = self.rows(ui, "project", "bare")
        self.assertFalse([r for r in rows if "Category" in r or "Tags" in r], rows)

    def test_description_always_shows_first(self):
        self.project("fleet", self.readme(description=""), handoffs=[TODAY()])
        rows = self.rows(self.ui(), "project", "fleet")
        self.assertRegex(rows[0], r"^Description\s+no description yet$")

    def test_a_sub_projects_fields_keep_type_and_follow_the_same_rules(self):
        self.project("rollout", self.readme(), handoffs=[TODAY()])
        beta = self.sub("rollout", "beta", type="bugfix", status="blocked", summary="Toner alerts.")
        (beta / "README.md").write_text((beta / "README.md").read_text().replace("status: blocked\n", "status: blocked\ntags: [toner]\n"))
        ui = self.ui()
        ui.subs_open.add("rollout")
        rows = self.rows(ui, "sub", "rollout/beta")
        self.assertRegex(rows[self.index(rows, r"Type\b")], r"^Type\s+bugfix$")
        at = [self.index(rows, p) for p in (r"Description\b", r"Sessions\b", r"Tags\b", r"Folder\b")]
        self.assertEqual(at, sorted(at), rows)
        self.assertRegex(rows[at[2]], r"^Tags\s+toner$")
        self.assertRegex(rows[at[3]], rf"^Folder\s+{re.escape(roost.short(str(beta)))}$")
        self.assert_no_old_fields(rows)


class StatsSparkline(unittest.TestCase):
    """The Stats line: This month  $X · N Sessions, then a 30-character sparkline of the project's own daily
    spend over the last 30 days (oldest left, today right) in  ▁▂▃▄▅▆▇█ scaled to the biggest day, a day with
    no spend a space. The same matching as the month's spend; worked out once per spend read, like it."""
    setUp_base, tearDown, project, sub = SubProjects.setUp, SubProjects.tearDown, Projects.project, SubProjects.sub
    setUp, ui, readme = DetailSections.setUp, ProjectMarkers.ui, staticmethod(ProjectMarkers.readme)
    section, spend, row = DetailSections.section, staticmethod(DetailSections.spend), staticmethod(DetailSections.row)
    sections = DetailSections.sections

    @staticmethod
    def day(ago):
        return (date.today() - timedelta(days=ago)).isoformat()

    def test_the_sparkline_follows_the_month_line(self):
        listed = ["aa000000-0000"]
        self.project("fleet", self.readme(listed).replace("# Title", "# Printer fleet"), handoffs=[TODAY()])
        self.project("other", self.readme(), handoffs=[TODAY()])
        ui = self.ui([dict(session("Live"), id=listed[0])])
        self.spend(ui, [(self.day(0), "Live work", "zz000000-0000", "fleet", 2.00),           # today, the biggest day: █
                        (self.day(29), "Live work", "yy000000-0000", "Printer fleet", 1.00),  # the oldest day, by its label: ▄
                        (self.day(3), "Live work", listed[0], None, 0.50),                    # a day's rows add up:
                        (self.day(3), "Wrap-ups", "xx000000-0000", "fleet", 0.50),            # 1.00 on it, ▄
                        (self.day(10), "Live work", "ww000000-0000", "other", 5.00),          # another project's
                        (self.day(30), "Live work", "vv000000-0000", "fleet", 9.00),          # before the 30 days
                        (None, "Live work", "uu000000-0000", "fleet", 7.00)])                 # no day
        [line] = self.section(ui, "project", "fleet", "Stats")
        want = [" "] * 30
        want[29], want[0], want[26] = "█", "▄", "▄"
        self.assertEqual(line[-30:], "".join(want), line)
        self.assertRegex(line[:-30].rstrip(), r"^This month  \$\d+\.\d\d · \d+ Sessions?$", "the month line reads as before")

    def test_no_spend_in_30_days_is_a_blank_sparkline(self):
        self.project("fleet", self.readme(), handoffs=[TODAY()])
        ui = self.ui()
        self.spend(ui, [(self.day(40), "Live work", "zz000000-0000", "fleet", 3.00)])
        [line] = self.section(ui, "project", "fleet", "Stats")
        self.assertEqual(line[-30:], " " * 30, line)
        self.assertRegex(line[:-30].rstrip(), r"^This month  \$0\.00 · 0 Sessions$", line)
        self.assertGreaterEqual(len(line), len("This month  $0.00 · 0 Sessions") + 30, line)

    def test_it_is_worked_out_once_per_spend_read(self):
        class Rows(list):
            """Spend rows that count how often they're read through."""
            reads = 0

            def __iter__(self):
                Rows.reads += 1
                return super().__iter__()
        self.project("fleet", self.readme(), handoffs=[TODAY()])
        ui = self.ui()
        self.spend(ui, Rows([(self.day(0), "Live work", "zz000000-0000", "fleet", 2.00)]))
        first = self.section(ui, "project", "fleet", "Stats")[0]
        self.assertTrue(first.endswith("█"), first)  # the sparkline is there
        before = Rows.reads
        for _ in range(3):  # drawn again, with the same spend read
            self.assertEqual(self.section(ui, "project", "fleet", "Stats")[0], first)
        self.assertEqual(Rows.reads, before, "the spend rows were read again for a draw")


@unittest.skipUnless(roost.App, "Textual isn't installed here: run the tests with ~/.roost/venv/bin/python")
class DetailLayout(unittest.TestCase):
    """The detail layout on a real screen (SectionsOnScreen's folder, with Current state and Next on Printer
    fleet, a Next alone on Restore drill, and bodies on Printer fleet's Handoffs: Toner swap, the newest, and
    Toner check). The top bar is the only row above the content; the sidebar's first row and the detail's
    name line are on screen row 2 on every view."""
    run_on, on_project = ProjectRows.run_on, ProjectRows.on_project
    select, keys, sidebar = staticmethod(TextualScreens.select), staticmethod(TextualScreens.keys), staticmethod(ProjectRows.sidebar)
    at, SIZE, WIDE, DESCRIPTION = staticmethod(TwoPanes.at), (120, 48), (180, 48), TwoPanes.DESCRIPTION
    read_project = staticmethod(SectionsOnScreen.read_project)
    index_of, text_of = DetailPane.index_of, staticmethod(DetailPane.text_of)

    def setUp(self):
        SectionsOnScreen.setUp(self)
        fleet, drill = self.root / "fleet", self.root / "drill"
        readme = fleet / "README.md"
        readme.write_text(readme.read_text() + f"\n## Current state\n\n{STATE_TEXT}\n\n## Next\n\n{NEXT_TEXT}\n\n"
                                               "## Key decisions\n\nNone yet.\n")
        (drill / "README.md").write_text((drill / "README.md").read_text() + f"\n## Next\n\n{DRILL_NEXT}\n")
        older = next((fleet / "handoffs").glob("*-cc000000.md"))
        older.write_text("---\nname: Toner check\n---\n\n### What I did\nOlder work stays hidden.\n")
        newest = next(h for h in (fleet / "handoffs").glob("*.md") if h != older)
        newest.write_text("---\nname: Toner swap\n---\n\n### What I did\n\n" + DID[0] + "\n\n" + DID[1] + "\n" + DID[2]
                          + "\nA fourth line stays hidden.\n### What worked\nRestarting them helped.\n")
        for p in self.projects:
            if p["id"] in ("fleet", "drill"):
                p.update(self.read_project(self.root / p["id"], self.sessions))

    def grab(self, seen, label):
        """A step that keeps the screen's lines, its bold runs, its dim runs, the rows drawn selected in the
        pane, and a few of the ui's facts under label."""
        async def step(pilot):
            pilot.app.redraw()
            await pilot.pause()
            ui = pilot.app.ui
            r = ui.project_rows()[ui.psel]
            seen[label] = {"lines": screen_text(pilot.app), "bold": bold_text(pilot.app), "dim": dim_text(pilot.app),
                           "raised": pane_raised(pilot.app), "items": ui.detail_items(r) if r.kind in ("project", "sub") else [],
                           "dsel": getattr(ui, "dsel", None), "focus": ui.focus, "menu": ui.choosing and ui.choosing["title"],
                           "folder": roost.short(str(roost.projects_dir())), "projects": ui.projects}
        return step

    @staticmethod
    def first_sidebar_row(lines):
        """The first screen row under the top bar where the sidebar draws anything."""
        side = ProjectRows.sidebar(lines)
        return next((i for i in range(1, len(side)) if side[i].strip(" │┃▏▕")), None)

    @staticmethod
    def name_at(got):
        """(y, x, text) of the detail pane's first bold run: the selected row's name."""
        return min(((y, x, t) for y, x, t in got["bold"] if x >= roost.SIDEBAR_W and y > 0), default=None)

    def bold_at(self, got, text):
        found = [(y, x) for y, x, t in got["bold"] if t == text and x >= roost.SIDEBAR_W]
        self.assertEqual(len(found), 1, (text, got["bold"]))
        return found[0]

    def field_y(self, got, label):
        W = roost.SIDEBAR_W
        return next((i for i, l in enumerate(got["lines"]) if l[W:].strip().startswith(label)), None)

    # 1. The top area

    def test_the_top_bar_is_the_only_row_above_the_content(self):
        seen = {}
        self.run_on(self.projects, [self.on_project("fleet"), self.grab(seen, "fleet")], size=self.SIZE)
        got = seen["fleet"]
        lines, W = got["lines"], roost.SIDEBAR_W
        self.assertTrue(lines[0].startswith(" Agent Roost"), lines[0])
        self.assertTrue(lines[0].rstrip().endswith(got["folder"]), f"the projects folder at the top bar's right: {lines[0]!r}")
        self.assertTrue([t for y, x, t in got["dim"] if y == 0 and got["folder"] in t], "the folder is dim")
        self.assertFalse([l for l in lines if re.search(r"\d+ projects · \d+ closed", l)], "no project counts")
        self.assertEqual(lines[1].strip(" │┃▏▕"), "", "one blank row under the top bar")
        self.assertEqual(self.first_sidebar_row(lines), 2, self.sidebar(lines)[:5])
        y, x, name = self.name_at(got)
        self.assertEqual((y, name), (2, "Printer fleet"), "the name line is level with the sidebar's first row")
        self.assertGreaterEqual(x, W)

    def test_the_search_row_shows_only_while_searching_or_with_a_search_set(self):
        seen = {}
        self.run_on(self.projects, [self.on_project("fleet"), self.keys("/"), self.grab(seen, "typing"), self.keys("f", "l", "e"),
                                    self.keys("enter"), self.grab(seen, "kept"), self.keys("/", "escape"), self.grab(seen, "cleared")],
                    size=self.SIZE)
        W, top = roost.SIDEBAR_W, slice(1, 6)  # the rows under the top bar (the key bar has a / too)
        self.assertTrue([l for l in seen["typing"]["lines"][top] if "/ " in l[W:]], "the search row, while typing")
        self.assertTrue([l for l in seen["kept"]["lines"][top] if "/ fle" in l[W:]], "the search row, with a search set")
        got = seen["cleared"]
        self.assertFalse([l for l in got["lines"][top] if "/ " in l[W:]], "no search row once it's cleared")
        self.assertEqual(self.name_at(got)[0], 2, got["lines"][:5])

    def test_the_sidebars_first_row_is_on_the_same_screen_row_on_every_view(self):
        seen = {}
        steps = [self.on_project("fleet")]
        for name in ("Projects",) + ENTRIES + ("Projects",):
            steps += [open_view(name), self.grab(seen, name)]
        self.run_on(self.projects, steps, size=self.SIZE)
        got = {name: self.first_sidebar_row(seen[name]["lines"]) for name in seen}
        self.assertEqual(got, {name: 2 for name in seen})

    # 2. The name line

    def test_the_name_line_is_the_label_then_its_status_and_when_it_was_worked(self):
        seen = {}
        self.run_on(self.projects, [self.on_project("fleet"), self.grab(seen, "fleet"), self.on_project("quiet"),
                                    self.grab(seen, "quiet")], size=self.SIZE)
        fleet = next(p for p in seen["fleet"]["projects"] if p["id"] == "fleet")
        for label, status, when in (("fleet", "awaiting-vendor", f"worked {roost.short_when(fleet['last'])}"),
                                    ("quiet", "active", "never worked")):
            got = seen[label]
            y, x, name = self.name_at(got)
            line = got["lines"][y]
            self.assertRegex(line[x:], rf"^{re.escape(name)}\s+{re.escape(status)} · {re.escape(when)}$", label)
            self.assertTrue([t for yy, xx, t in got["dim"] if yy == y and xx > x and f"{status} · {when}" in t],
                            f"{label}: the status and when are dim: {got['dim']}")
            W = roost.SIDEBAR_W
            self.assertFalse([l for l in got["lines"] if l[W:].strip().startswith("Status") or "Last worked" in l], label)

    def test_a_sub_projects_name_line(self):
        seen = {}

        def on_alpha(ui):
            ui.subs_open.add("rollout")
            ui.psel = next(i for i, r in enumerate(ui.project_rows()) if r.kind == "sub" and r.item["slug"] == "alpha")
        self.run_on(self.projects, [self.select(on_alpha), self.grab(seen, "alpha")], size=self.SIZE)
        got = seen["alpha"]
        alpha = next(s for p in got["projects"] if p["id"] == "rollout" for s in p["subs"] if s["slug"] == "alpha")
        y, x, name = self.name_at(got)
        self.assertEqual(y, 2)
        self.assertRegex(got["lines"][y][x:], rf"^{re.escape(name)}\s+active · worked {re.escape(roost.short_when(alpha['last']))}$")
        W = roost.SIDEBAR_W
        self.assertFalse([l for l in got["lines"] if l[W:].strip().startswith("Status")])
        self.assertTrue([l for l in got["lines"] if re.match(r"Type\s+feature$", l[W:].strip())], "Type stays")

    # 3. Current state and Next

    def test_narrower_than_110_they_come_after_the_fields_and_before_the_sections(self):
        seen = {}
        self.run_on(self.projects, [self.on_project("fleet"), self.loaded, self.grab(seen, "fleet"),
                                    self.on_project("drill"), self.grab(seen, "drill"),
                                    self.on_project("quiet"), self.grab(seen, "quiet")], size=self.SIZE)
        W, got = roost.SIDEBAR_W, seen["fleet"]
        lines = got["lines"]
        sy, sx = self.bold_at(got, "Current state")
        ny, nx = self.bold_at(got, "Next")
        sessions_y, _ = self.bold_at(got, "Sessions")
        self.assertGreater(sy, self.field_y(got, "Folder"), "after the fields")
        self.assertLess(sy, ny)
        self.assertLess(ny, sessions_y, "before the sections")
        self.assertEqual(words(" ".join(l[W:] for l in lines[sy + 1:ny])), words(STATE_TEXT), "the whole text, wrapped")
        self.assertGreater(sum(bool(l[W:].strip()) for l in lines[sy + 1:ny]), 1, "wrapped over lines")
        self.assertEqual(words(" ".join(l[W:] for l in lines[ny + 1:sessions_y])), words(NEXT_TEXT))
        drill = seen["drill"]
        self.assertFalse([t for y, x, t in drill["bold"] if t == "Current state"], "an empty one is left out")
        dy, _ = self.bold_at(drill, "Next")
        self.assertIn(words(DRILL_NEXT), words(" ".join(l[W:] for l in drill["lines"][dy + 1:dy + 4])))
        self.assertFalse([t for y, x, t in seen["quiet"]["bold"] if t in ("Current state", "Next") and x >= W])

    def test_at_110_columns_and_wider_they_are_a_right_hand_column_beside_the_fields_and_sections(self):
        seen = {}
        self.run_on(self.projects, [self.on_project("fleet"), self.loaded, self.grab(seen, "fleet")], size=self.WIDE)
        W, got = roost.SIDEBAR_W, seen["fleet"]
        lines = got["lines"]
        sy, sx = self.bold_at(got, "Current state")
        ny, nx = self.bold_at(got, "Next")
        sessions_y, sessions_x = self.bold_at(got, "Sessions")
        self.assertGreaterEqual(sx, W + 50, "in a right-hand column")
        self.assertEqual(nx, sx, "Next under Current state, in the same column")
        self.assertLess(sy, ny)
        self.assertLessEqual(sy, self.field_y(got, "Folder"), "beside the fields, not under them")
        self.assertLess(sessions_x, sx, "the sections stay in the left column")
        self.assertEqual(words(" ".join(l[sx:] for l in lines[sy + 1:ny])), words(STATE_TEXT), "wrapped to the column")
        end = next(i for i in range(ny + 1, len(lines)) if not lines[i][sx:].strip())
        self.assertEqual(words(" ".join(l[sx:] for l in lines[ny + 1:end])), words(NEXT_TEXT))
        described = next((l for l in lines if TwoPanes.DESCRIPTION[:30] in l), None)
        self.assertIsNotNone(described, "the description, in the left column")
        self.assertLess(described.index(TwoPanes.DESCRIPTION[:30]), sx)

    def assert_items_selectable(self, size):
        """In this layout: tab then ↓ draw each item's line selected in turn, and a click on the note's line
        selects it."""
        seen, W = {}, roost.SIDEBAR_W
        steps = [self.on_project("fleet"), self.loaded, self.grab(seen, "side"), self.keys("tab")]
        for i in range(4):
            steps += [self.grab(seen, i), self.keys("down")]
        self.run_on(self.projects, steps, size=size)
        got = seen["side"]
        sy, sx = self.bold_at(got, "Current state")  # the layout this test is about is drawn
        stop = sx if sx > W + 40 else None  # wide: the items are in the left column
        ys, last = [], 0
        for kind, item in got["items"]:
            t = self.text_of(kind, item)
            y = next((i for i in range(last, len(got["lines"])) if t in got["lines"][i][W:stop]), None)
            self.assertIsNotNone(y, (t, got["lines"]))
            ys.append(y)
            last = y + 1
        self.assertEqual(len(ys), 4, got["items"])  # Printer fix, Toner swap, Toner check, Driver shortlist
        for i in range(4):
            self.assertEqual(seen[i]["raised"] & set(ys), {ys[i]}, (size, i, seen[i]["lines"][ys[i]]))
        note = next(i for i, (k, _) in enumerate(got["items"]) if k == "note")
        clicked = {}
        self.run_on(self.projects, [self.on_project("fleet"), self.loaded,
                                    lambda pilot: pilot.click(offset=(W + 8, ys[note])), self.grab(clicked, "x")], size=size)
        self.assertEqual((clicked["x"]["focus"], clicked["x"]["dsel"], clicked["x"]["menu"]), ("pane", note, None), size)

    def test_the_items_stay_selectable_with_state_and_next_below_the_fields(self):
        self.assert_items_selectable(self.SIZE)

    def test_the_items_stay_selectable_with_state_and_next_in_a_column(self):
        self.assert_items_selectable(self.WIDE)

    # 4. The newest Handoff's lines

    def test_the_newest_handoffs_row_is_followed_by_its_first_lines_dim_and_indented(self):
        seen = {}
        self.run_on(self.projects, [self.on_project("fleet"), self.loaded, self.grab(seen, "fleet")], size=self.SIZE)
        W, got = roost.SIDEBAR_W, seen["fleet"]
        lines = got["lines"]
        hy, _ = self.bold_at(got, "Handoffs")
        ty = next(i for i in range(hy + 1, len(lines)) if "Toner swap" in lines[i][W:])
        start = W + len(lines[ty][W:]) - len(lines[ty][W:].lstrip())  # where the Handoff's row starts
        for i, text in enumerate(DID, 1):
            self.assertIn(text, lines[ty + i][W:], (i, lines[ty:ty + 5]))
            self.assertGreater(lines[ty + i].index(text), start, "indented under the Handoff's row")
            self.assertTrue([t for y, x, t in got["dim"] if y == ty + i and text in t], f"{text!r} is dim")
        self.assertIn("Toner check", lines[ty + 4][W:], "then the next Handoff's row")
        for hidden in ("A fourth line stays hidden.", "Restarting them helped.", "Older work stays hidden."):
            self.assertFalse([l for l in lines if hidden in l], hidden)

    def test_the_handoffs_lines_arent_items(self):
        seen = {}
        self.run_on(self.projects, [self.on_project("fleet"), self.loaded, self.grab(seen, "side"), self.keys("tab"),
                                    self.index_of("handoff", "Toner swap"), self.grab(seen, "swap"), self.keys("down"),
                                    self.grab(seen, "down")], size=self.SIZE)
        W, lines = roost.SIDEBAR_W, seen["side"]["lines"]
        ty = next(i for i, l in enumerate(lines) if "Toner swap" in l[W:])
        self.assertIn(DID[0], lines[ty + 1][W:], "the fixture: Toner swap's lines are drawn")
        cy = next(i for i, l in enumerate(lines) if "Toner check" in l[W:])
        self.assertEqual([x[1] for k, x in seen["side"]["items"] if k == "handoff"], ["Toner swap", "Toner check"])
        band = set(range(ty, cy + 1))
        self.assertEqual(seen["swap"]["raised"] & band, {ty})
        self.assertEqual(seen["down"]["raised"] & band, {cy}, "↓ goes from Toner swap straight to Toner check")
        check = next(i for i, (k, x) in enumerate(seen["side"]["items"]) if k == "handoff" and x[1] == "Toner check")
        for y, want in ((ty + 1, None), (cy, check)):
            clicked = {}
            self.run_on(self.projects, [self.on_project("fleet"), self.loaded, lambda pilot, y=y: pilot.click(offset=(W + 8, y)),
                                        self.grab(clicked, "x")], size=self.SIZE)
            got = clicked["x"]
            if want is None:  # a click on one of the lines: nothing is selected or opened
                self.assertEqual((got["menu"], got["focus"]), (None, "side"), got["lines"][y])
                self.assertFalse(got["raised"] & band)
            else:
                self.assertEqual((got["focus"], got["dsel"]), ("pane", want))

    loaded = staticmethod(SectionsOnScreen.loaded)
