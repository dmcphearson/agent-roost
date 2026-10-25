from support import *  # noqa: F401,F403 (the shared namespace, helpers, and fixtures)
from support import _SCRATCH, own_tests  # noqa: F401
from test_screens import TextualScreens  # for their helpers; their tests run in test_screens.py

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
        self.assertEqual(meta, {"summary": 'Say "hi" to it.', "status": "active", "sessions": []})
        self.assertTrue((folder / "handoffs").is_dir())
        self.assertIn("## Current state", (folder / "README.md").read_text())

    def test_create_refuses_what_it_cant_do_safely(self):
        self.project("taken")
        for pid, summary in [("taken", "s"), ("a/b", "s"), ("_x", "s"), ("two words", "s"), ("ok", " ")]:
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
        with self.assertRaises(SystemExit):
            roost.run_command("project", ["new", "2026-10-y"])  # --summary is required

    def ui(self):
        ui = object.__new__(roost.UI)
        ui.sessions, ui.msg, ui.page, ui.last_click = [], "", 10, (0, -1)
        ui.psel, ui.ptop, ui.prow_y, ui.asking = 0, 0, {}, None
        ui.pquery, ui.searching = "", False
        ui.folded, ui.expanded = {"Inactive", "Closed"}, set()
        ui.unwrapped = set()
        ui.choosing = ui.confirm = ui.term = None
        ui.summaries, ui.plugin_ok = {}, True
        ui.load_projects()
        return ui

    def shown(self, ui):
        return [(r.kind, r.item["id"]) for r in ui.project_rows()]

    def test_a_click_where_a_projects_arrow_was_only_selects_it(self):
        """Spec 1: a project row never expands, so a click at its left edge (where ▸ was) adds no rows,
        once or twice, and opens no menu."""
        self.project("b", handoffs=[time.strftime("%Y-%m-%d-%H%M") + "-aaaaaaaa.md"])
        ui = self.ui()
        ui.sessions = [dict(session("Password Manager Review"), id="aa1b9a71-0000")]
        ui.load_projects()
        ui.prow_y = {6: 1}  # the project's row, as draw left it
        click = ("mouse", 3, 6, roost.curses.BUTTON1_CLICKED)
        for _ in range(2):  # two separate single clicks: a quick second one would be a double-click
            ui.last_click = (0, -1)
            ui.keys_projects(click, ui.project_rows())
            self.assertEqual((self.shown(ui), ui.choosing), ([("head", "Recent"), ("project", "b"), ("new", "+ New project")], None))
        ui.keys_projects(("mouse", 20, 6, roost.curses.BUTTON1_CLICKED), ui.project_rows())  # the name only selects it
        self.assertEqual(self.shown(ui), [("head", "Recent"), ("project", "b"), ("new", "+ New project")])

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
        self.assertEqual(self.shown(ui), [("head", "Recent"), ("project", "b"), ("new", "+ New project")])
        ui.psel = 1
        ui.keys_projects("left", ui.project_rows())
        self.assertEqual(self.shown(ui), [("head", "Recent"), ("project", "b"), ("new", "+ New project")])

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
        only = [("head", "Recent"), ("project", "b"), ("new", "+ New project")]
        ui.psel = 1
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
        ui.psel = 1
        ui.keys_projects("right", ui.project_rows())
        acts = labels(ui, ui.keys_projects, ui.project_rows())
        self.assertIn("Show its Sessions", acts)
        self.assertNotIn("Hide its Sessions", acts)
        ui.prow_y = {6: 1}  # the project's row, as draw left it
        ui.keys_projects(("mouse", 3, 6, roost.curses.BUTTON1_CLICKED), ui.project_rows())
        self.assertEqual(self.shown(ui), [("head", "Recent"), ("project", "b"), ("new", "+ New project")])

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

    def test_recent_projects_show_and_the_rest_fold_away(self):
        self.project("a", README.replace("status: active", "status: closed"))
        self.project("b", handoffs=[time.strftime("%Y-%m-%d-%H%M") + "-aaaaaaaa.md"])
        self.project("c")
        ui = self.ui()
        self.assertEqual(self.shown(ui), [("head", "Recent"), ("project", "b"), ("head", "Inactive"), ("head", "Closed"),
                                          ("new", "+ New project")])  # + New project, last
        ui.psel = 2
        ui.keys_projects("enter", ui.project_rows())  # enter on a header opens it
        self.assertIn(("project", "c"), self.shown(ui))
        ui.psel = 4
        ui.keys_projects("right", ui.project_rows())  # → opens Closed, at the bottom
        self.assertEqual(self.shown(ui)[-3:-1], [("head", "Closed"), ("project", "a")])
        ui.keys_projects("left", ui.project_rows())  # ← closes it
        self.assertEqual(self.shown(ui)[-2], ("head", "Closed"))

    def test_search_keeps_matching_rows_unfolded(self):
        self.project("printers", README.replace("Two password managers compared: cost and rollout.", "Fix the office printers."))
        self.project("vpn")
        ui = self.ui()
        ui.notes = []
        ui.pquery = "PRINTER"
        self.assertEqual(self.shown(ui), [("head", "Inactive"), ("project", "printers")])

    def test_show_its_sessions_opens_the_sessions_tab_on_all_searching_for_the_project(self):
        """Spec 3: Show its Sessions switches to the Sessions tab, sets the view to All, and prefills its
        search with project:<folder id>. The Projects tab gets no rows, and there's no Hide its Sessions."""
        self.project("b", handoffs=[time.strftime("%Y-%m-%d-%H%M") + "-aaaaaaaa.md"])
        ui = self.ui()
        ui.tab, ui.view, ui.query, ui.sel, ui.top = [t.name for t in ui.TABS].index("Projects"), RUNNING, "", 0, 0
        ui.sessions = [dict(session("Mine"), id="aa1b9a71-0000"), dict(session("Old one", state="closed", kind=None, mins=60), id="bb2c0000-0000"),
                       session("Loose", state="waiting")]
        ui.load_projects()
        ui.psel = 1
        act(ui, ui.keys_projects, ui.project_rows(), "Show its Sessions")
        self.assertEqual((ui.TABS[ui.tab].name, ui.view, ui.query), ("Sessions", ALL, "project:b"))
        self.assertEqual(self.shown(ui), [("head", "Recent"), ("project", "b"), ("new", "+ New project")])
        self.assertFalse([l for l in labels(ui, ui.keys_projects, ui.project_rows()) if l.startswith("Hide its Sessions")])

    def test_show_its_sessions_comes_after_start_a_session_or_first(self):
        """Spec 3: on an open project, Show its Sessions is right after Start a Session; on a closed
        one (no Start a Session), it's first."""
        self.project("b", handoffs=[time.strftime("%Y-%m-%d-%H%M") + "-aaaaaaaa.md"])
        self.project("c", README.replace("status: active", "status: closed"))
        ui = self.ui()
        ui.folded = set()
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
        ui.folded = set()  # its close-out record puts it under Closed
        ui.psel = [r.item["id"] for r in ui.project_rows()].index("b")
        act(ui, ui.keys_projects, ui.project_rows(), "Rename")
        self.assertEqual(ui.asking["title"], "Rename b (its folder)")
        typed(ui, "\x7f" + "taken\n")
        self.assertIn("taken already exists", ui.msg)
        self.assertTrue(folder.exists())  # nothing changed
        ui.psel = [r.item["id"] for r in ui.project_rows()].index("b")
        act(ui, ui.keys_projects, ui.project_rows(), "Rename")
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
        ui.folded = set()
        ui.psel = 1
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
        self.assertEqual(roost.frontmatter((self.root / "2026-10-z" / "README.md").read_text())["summary"], "One line.")
        self.assertEqual(ui.project_rows()[ui.psel].item["id"], "2026-10-z")

    def test_esc_cancels_the_new_project_prompt(self):
        ui = self.ui()
        ui.keys_projects("enter", [])
        typed(ui, "x\x1b")
        self.assertEqual((ui.asking, list(self.root.iterdir())), (None, []))

    def test_demo_has_open_closed_and_never_worked_projects(self):
        ps = roost.demo_projects(roost.demo_sessions())
        self.assertTrue(any(p["closed"] for p in ps) and any(not p["last"] for p in ps))


class ProjectArrows(unittest.TestCase):
    def test_tags_are_read_from_a_list_or_a_line(self):
        self.assertEqual(roost.tag_list("[ideas, q4]"), ["ideas", "q4"])
        self.assertEqual(roost.tag_list(["a"]), ["a"])


@unittest.skipUnless(roost.App, "Textual isn't installed here: run the tests with ~/.roost/venv/bin/python")
class ProjectRows(unittest.TestCase):
    """The Projects tab with the NAME as its most readable part, on a real Textual screen."""
    setUp = TextualScreens.setUp
    closed = staticmethod(TextualScreens.closed)
    proj = staticmethod(TextualScreens.proj)
    select = staticmethod(TextualScreens.select)
    keys = staticmethod(TextualScreens.keys)
    TITLES = [("NAME", "NAME"), ("STATUS", "STAT"), ("SESSIONS", "SESS"), ("CATEGORY", "CATE"), ("TAGS", "TAGS"),
              ("LAST WORKED", "LAST")]
    PAST = datetime(2025, 1, 5, 9, 30).timestamp()  # MDY 01/05/25, DMY 05/01/25

    def run_on(self, projects, steps=(), size=(160, 30), tab="Projects", config=None):
        import asyncio, unittest.mock
        settings = dict(config or {}, tab=tab)
        with unittest.mock.patch.multiple(roost, read_projects=lambda sessions: projects, read_config=lambda: settings):
            app = roost.RoostApp(self.sessions, "")

            async def go():
                async with app.run_test(size=size) as pilot:
                    await pilot.pause()
                    for step in [self.select(lambda ui: ui.folded.discard("Inactive"))] + list(steps):  # past dates list as Inactive
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
    def head_index(lines):
        return next(i for i, l in enumerate(lines) if "NAME" in l and "STAT" in l)

    def columns(self, header):
        """Where each column's title starts, for the titles drawn, in order."""
        x, at = {}, 0
        for title, prefix in self.TITLES:
            i = header.find(prefix, at)
            if i >= 0:
                x[title], at = i, i + 1
        return x

    def widths(self, x):
        """Each column's width, from its title to the next column's, when both are drawn."""
        names = [t for t, _ in self.TITLES]
        return {t: x[n] - x[t] for t, n in zip(names, names[1:]) if t in x and n in x}

    def listing(self, lines):
        """The list's lines: under the column titles, above the panel's rule."""
        h = self.head_index(lines)
        edge = next(i for i in range(h + 1, len(lines)) if lines[i].strip() and set(lines[i].strip()) == {"─"})
        return lines[h + 1:edge]

    @staticmethod
    def row_of(lines, text):
        return next(i for i, l in enumerate(lines) if text in l)

    def project(self, pid, linked=(), label=None, status="active", category="", tags=(), last=None, covered=()):
        p = self.proj(pid, list(linked), covered=covered, label=label)
        p.update(status=status, category=category, tags=list(tags), last=time.time() if last is None else last)
        return p

    # 1. Dates

    def test_last_worked_is_a_time_today_and_a_short_date_otherwise(self):
        import unittest.mock
        now = time.time()
        projects = [self.project("today-one", status="active", last=now), self.project("older-one", status="active", last=self.PAST)]
        for order, shown in (("MDY", "01/05/25"), ("DMY", "05/01/25")):
            with unittest.mock.patch.object(FMT, "date_order", lambda o=order: o):
                lines = self.run_on(projects).lines
            x = self.columns(lines[self.head_index(lines)])
            today, older = (lines[self.row_of(lines[:-10], pid)] for pid in ("today-one", "older-one"))
            self.assertEqual(today[x["LAST WORKED"]:].strip(), clock(now), order)
            self.assertEqual(older[x["LAST WORKED"]:].strip(), shown, order)

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
        status = next(l for l in app.lines[-10:] if "Last worked" in l)
        self.assertRegex(status, r"Last worked +05/01/25(\s|$)")
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

    # 2. SESSIONS on two lines

    def two_line_fleet(self):
        """alpha: idle Printer fix and closed Toner order (○1, then 1 unwrapped); bravo: waiting Laptop
        refresh (◆1, nothing unwrapped); charlie: no Sessions; delta: closed Desk order only (1 unwrapped,
        nothing running); echo: no Sessions."""
        toner, desk = self.closed("Toner order", 30), self.closed("Desk order", 40)
        self.sessions = self.sessions + [toner, desk]
        now = time.time()
        return [self.project("alpha", [self.sessions[0], toner], last=now),
                self.project("bravo", [self.sessions[1]], last=now - 60),
                self.project("charlie", [], last=now - 120),
                self.project("delta", [desk], last=now - 180),
                self.project("echo", [], last=now - 240)]

    def test_unwrapped_is_on_a_second_line_under_the_running_counts(self):
        lines = self.run_on(self.two_line_fleet()).lines
        x = self.columns(lines[self.head_index(lines)])
        a, b, c = (self.row_of(lines[:-10], pid) for pid in ("alpha", "bravo", "charlie"))
        self.assertTrue(lines[a][x["SESSIONS"]:].startswith("○1"), lines[a])
        self.assertNotIn("unwrapped", lines[a])
        self.assertEqual(lines[a + 1].strip(), "1 unwrapped")
        self.assertEqual(lines[a + 1].index("1 unwrapped"), x["SESSIONS"])
        self.assertEqual(b, a + 2)  # alpha takes two lines
        self.assertTrue(lines[b][x["SESSIONS"]:].startswith("◆1"), lines[b])
        self.assertEqual(c, b + 1)  # bravo takes one
        d, e = (self.row_of(lines[:-10], pid) for pid in ("delta", "echo"))
        self.assertEqual(lines[d][x["SESSIONS"]:x["CATEGORY"]].strip(), "1 unwrapped")  # nothing running: first line
        self.assertEqual(e, d + 1)  # so delta takes one line too

    def test_a_click_on_either_line_selects_the_project(self):
        projects = self.two_line_fleet()
        lines = self.run_on(projects).lines
        a = self.row_of(lines[:-10], "alpha")
        sx = self.columns(lines[self.head_index(lines)])["SESSIONS"]
        for y in (a, a + 1):
            app = self.run_on(projects, [self.on_project("charlie"), lambda pilot, y=y: pilot.click(offset=(sx + 2, y))])
            self.assertEqual(self.chosen(app), "alpha", y)

    def test_down_from_a_two_line_row_selects_the_next_row(self):
        app = self.run_on(self.two_line_fleet(), [self.on_project("alpha"), self.keys("down")])
        self.assertEqual(self.chosen(app), "bravo")

    def test_scrolling_down_shows_both_lines_of_the_selected_row(self):
        now, projects = time.time(), []
        self.sessions = []
        for i in range(14):
            live, gone = session(f"Live {i:02d}", mins=i), self.closed(f"Gone {i:02d}", 30 + i)
            self.sessions += [live, gone]
            projects.append(self.project(f"proj-{i:02d}", [live, gone], label=f"Project {i:02d}", last=now - i * 60))
        seen = []

        async def look(pilot):
            pilot.app.redraw()
            await pilot.pause()
            seen.append((self.chosen(pilot.app), screen_text(pilot.app)))
        steps = []
        for _ in range(15):
            steps += [self.keys("down"), look]
        self.run_on(projects, steps)
        scrolled = False
        for pid, lines in seen:
            if not pid.startswith("proj-"):
                continue
            listed = self.listing(lines)
            label = f"Project {pid[-2:]}"
            i = next((i for i, l in enumerate(listed) if label in l), None)
            self.assertIsNotNone(i, (pid, listed))
            self.assertLess(i + 1, len(listed), (pid, listed))
            self.assertEqual(listed[i + 1].strip(), "1 unwrapped", (pid, listed))
            scrolled = scrolled or not any("Project 00" in l for l in listed)
        self.assertTrue(scrolled, "the list never scrolled: the fixture needs more projects")

    # 3. Widths

    def drawn_widths(self, projects, w):
        """The Projects tab in a window w wide: each column's start, the five widths read off the column
        titles, LAST WORKED's width (it has no column after it, so it is read from the list's own
        widths), the list's width, and the drawn lines."""
        seen = {}

        async def last(pilot):
            region = pilot.app.query_one(roost.ProjectList)
            seen["list"] = region.size.width
            seen["LAST WORKED"] = pilot.app.ui.project_widths(pilot.app.ui.prows, region.size.width)["LAST WORKED"]
        lines = self.run_on(projects, [last], size=(w, 30)).lines
        x = self.columns(lines[self.head_index(lines)])
        wd = dict(self.widths(x), **{"LAST WORKED": seen["LAST WORKED"]})
        return x, wd, seen["list"], lines

    SIX = ("NAME", "STATUS", "SESSIONS", "CATEGORY", "TAGS", "LAST WORKED")

    def six(self, wd):
        return tuple(wd.get(t) for t in self.SIX)

    def test_in_a_wide_window_name_is_its_target_and_the_rest_is_spread(self):
        """160 and 170 columns, one name of 79: NAME grows past 48 to its longest + 2 (81) and the rest
        of the spare width is spread across all six columns, leftmost first. Before spare width:
        NAME 48, STATUS 8, SESSIONS 12, CATEGORY 14, TAGS 14, LAST WORKED 12 = 108, + 6 for the
        offset and margin. 160: 46 spare, 33 to NAME, 13 = 2 each + 1 to NAME. 170: 56 spare, 33 to
        NAME, 23 = 3 each + 1 to NAME through TAGS."""
        long_name = "Quarterly laptop refresh, printer fleet consolidation, and badge reader rollout"
        p = self.project("wide", label=long_name, status="active", category="ops", tags=["q4"], last=self.PAST)
        for w, want in ((160, (84, 10, 14, 16, 16, 14)), (170, (85, 12, 16, 18, 18, 15))):
            x, wd, width, lines = self.drawn_widths([p], w)
            header = lines[self.head_index(lines)]
            self.assertEqual([t for t, _ in self.TITLES], list(x), (w, header))
            self.assertIn("LAST WORKED", header)
            self.assertEqual(self.six(wd), want, w)
            self.assertEqual(5 + sum(wd.values()) + 1, width, w)  # the columns fill the window
            row = lines[self.row_of(lines[:-10], long_name[:20])]
            self.assertIn(long_name, row)
            self.assertTrue(SHORT_DATE.fullmatch(row[x["LAST WORKED"]:].strip()), row)

    def test_name_is_its_longest_name_plus_two_with_no_cap(self):
        """Before spare width NAME is its longest name + 2 (29 here); with STATUS 8 and 12, 14, 14, 12 that
        is 89, + 6 = 95: at 101 the 6 spare are one each (roost draws nothing under 100). At 250, 155 spare: NAME is already whole and nothing
        needs CATEGORY or TAGS to grow, so it is 25 each + 1 to NAME through TAGS. A name of 65 has no
        cap: NAME 67 once there is room (114 + 19 = 133), and at 250 the 117 left after NAME's 19 is 19
        each + 1 to NAME, STATUS, SESSIONS."""
        names = ["Printer fleet consolidation", "Laptop refresh"]  # the longest is 27
        projects = [self.project(f"p{i}", label=n, last=time.time() - i) for i, n in enumerate(names)]
        for w, want in ((101, (30, 9, 13, 15, 15, 13)), (250, (55, 34, 38, 40, 40, 37))):
            self.assertEqual(self.six(self.drawn_widths(projects, w)[1]), want, w)
        longer = projects + [self.project("p9", label="x" * 47 + " and a little more")]  # 65
        for w, want in ((133, (67, 8, 12, 14, 14, 12)), (250, (87, 28, 32, 33, 33, 31))):
            self.assertEqual(self.six(self.drawn_widths(longer, w)[1]), want, w)

    def test_projects_name_ignores_the_name_width_setting(self):
        names = ["Printer fleet consolidation", "Laptop refresh"]
        projects = [self.project(f"p{i}", label=n, last=time.time() - i) for i, n in enumerate(names)]
        drawn = {}
        for setting in (None, 16, 60):
            lines = self.run_on(projects, config={} if setting is None else {"name_width": setting}).lines
            drawn[setting] = self.widths(self.columns(lines[self.head_index(lines)]))["NAME"]
        self.assertEqual(drawn[16], drawn[None], drawn)
        self.assertEqual(drawn[60], drawn[None], drawn)

    LONG_NAME = "Laptop refresh and printer fleet consolidation for two office"  # 61: NAME 63 whole
    LONG_CAT = "vendor-evaluation-and-procureme"  # 31: CATEGORY 32 whole
    LONG_TAGS = ["alpha", "bravo", "charlie", "delta"]  # "alpha, bravo, charlie, delta", 28: TAGS 29 whole

    def long_values(self):
        return [self.project("long-values", label=self.LONG_NAME, status="active", category=self.LONG_CAT,
                             tags=self.LONG_TAGS, last=self.PAST)]

    def test_a_window_exactly_wide_enough_leaves_nothing_spare(self):
        """Before spare width: NAME 48 (a name of 61, capped until there is spare), STATUS 8, SESSIONS 12,
        CATEGORY 14, TAGS 14, LAST WORKED 12 = 108; with the offset and margin, 114 columns. At 114
        those are the widths drawn and LAST WORKED's title is whole; one column fewer narrows LAST
        WORKED (as today); one column more goes to NAME."""
        p = self.long_values()
        x, wd, width, lines = self.drawn_widths(p, 114)
        self.assertEqual(self.six(wd), (48, 8, 12, 14, 14, 12))
        self.assertIn("LAST WORKED", lines[self.head_index(lines)])
        x, wd, width, lines = self.drawn_widths(p, 113)
        self.assertEqual(self.six(wd), (48, 8, 12, 14, 14, 11))
        x, wd, width, lines = self.drawn_widths(p, 115)
        self.assertEqual(self.six(wd), (49, 8, 12, 14, 14, 12))

    def test_name_grows_past_48_before_any_other_column_grows(self):
        """A name of 61 (NAME whole at 63), with long CATEGORY and TAGS waiting: the first 15 spare
        columns all go to NAME, and CATEGORY and TAGS stay 14 and cut until NAME is whole."""
        p = self.long_values()
        for w, name_w in ((114 + 7, 55), (114 + 15, 63)):
            x, wd, width, lines = self.drawn_widths(p, w)
            self.assertEqual(self.six(wd), (name_w, 8, 12, 14, 14, 12), w)
            row = lines[self.row_of(lines[:-10], self.LONG_NAME[:20])]
            self.assertTrue(row[x["CATEGORY"]:x["TAGS"]].rstrip().endswith("…"), (w, row))
            self.assertTrue(row[x["TAGS"]:x["LAST WORKED"]].rstrip().endswith("…"), (w, row))
            if name_w == 63:
                self.assertIn(self.LONG_NAME, row)

    def test_category_and_tags_grow_to_show_long_values_once_name_is_whole(self):
        """NAME is whole at 129 columns. The next spare columns go to CATEGORY and TAGS, split evenly while
        both need more (10 spare: 5 each, 19 and 19), until each shows its longest value whole: CATEGORY
        its 31 + 1, TAGS its 28 + 1 (129 + 18 + 15 = 162). Nothing else grows meanwhile."""
        p = self.long_values()
        x, wd, width, lines = self.drawn_widths(p, 129 + 10)
        self.assertEqual(self.six(wd), (63, 8, 12, 19, 19, 12))
        x, wd, width, lines = self.drawn_widths(p, 162)
        self.assertEqual(self.six(wd), (63, 8, 12, 32, 29, 12))
        row = lines[self.row_of(lines[:-10], self.LONG_NAME[:20])]
        self.assertEqual(row[x["CATEGORY"]:x["TAGS"]].rstrip(), self.LONG_CAT)
        self.assertEqual(row[x["TAGS"]:x["LAST WORKED"]].rstrip(), ", ".join(self.LONG_TAGS))
        self.assertEqual(5 + sum(wd.values()) + 1, width)  # every column whole: they fill the window exactly

    def test_an_odd_category_and_tags_split_gives_the_extra_column_to_category(self):
        """NAME is whole at 129; 11 spare after that, both still needing more: CATEGORY 6, TAGS 5."""
        x, wd, width, lines = self.drawn_widths(self.long_values(), 129 + 11)
        self.assertEqual(self.six(wd), (63, 8, 12, 20, 19, 12))

    def test_once_category_or_tags_is_whole_the_other_takes_the_rest_before_the_even_spread(self):
        """NAME is whole at 129. CATEGORY of 17 needs 18 (4 more), TAGS of 28 needs 29 (15 more): with 12
        spare, CATEGORY stops at 18 and TAGS takes the other 8; with 19 both are whole and nothing is
        spread; 6 more is then one each. The other way round, TAGS "alpha, bravo, q4" (16) needs 17 (3
        more): with 10 spare TAGS stops at 17 and CATEGORY of 31 takes the other 7."""
        def one(category, tags):
            return [self.project("long-values", label=self.LONG_NAME, status="active", category=category,
                                 tags=tags, last=self.PAST)]
        short_cat = one("vendor-evaluation", self.LONG_TAGS)
        for w, want in ((129 + 12, (63, 8, 12, 18, 22, 12)), (129 + 19, (63, 8, 12, 18, 29, 12)),
                        (129 + 19 + 6, (64, 9, 13, 19, 30, 13))):
            self.assertEqual(self.six(self.drawn_widths(short_cat, w)[1]), want, w)
        short_tags = one(self.LONG_CAT, ["alpha", "bravo", "q4"])
        self.assertEqual(self.six(self.drawn_widths(short_tags, 129 + 10)[1]), (63, 8, 12, 21, 17, 12))

    def test_what_is_left_is_spread_evenly_across_all_six_columns_leftmost_first(self):
        """Once NAME, CATEGORY, and TAGS are whole (162 columns), what is left goes to all six evenly; a
        remainder goes one each to the leftmost columns. Short values that never needed to grow (CATEGORY
        and TAGS of a few letters) get the same share from 14."""
        p = self.long_values()
        for w, want in ((162 + 6, (64, 9, 13, 33, 30, 13)),
                        (162 + 6 * 3 + 5, (67, 12, 16, 36, 33, 15)),  # 3 each, then NAME to TAGS 1 more
                        (162 + 1, (64, 8, 12, 32, 29, 12))):  # a single column: NAME's
            x, wd, width, lines = self.drawn_widths(p, w)
            self.assertEqual(self.six(wd), want, w)
            self.assertEqual(5 + sum(wd.values()) + 1, width, w)
        short = [self.project("short-values", label="Short", status="active", category="ops", tags=["q4"], last=self.PAST)]
        # before spare: NAME 16, STATUS 8, 12, 14, 14, 12 = 76, + 6 = 82; 82 + 6 * 5 + 3 = 115
        x, wd, width, lines = self.drawn_widths(short, 115)
        self.assertEqual(self.six(wd), (22, 14, 18, 19, 19, 17))

    def test_long_category_and_tags_are_cut_with_an_ellipsis(self):
        """In a window with no spare width: a name of 40 (NAME 42), STATUS 8, 12, 14, 14, 12, + 6 = 108."""
        cat, tags = "vendor-evaluation-and-procurement", ["alpha", "bravo", "charlie", "delta"]
        label = "Vendor evaluation and procurement review"  # 40: long enough that the exact fit is over 100
        projects = [self.project("long-one", label=label, category=cat, tags=tags),
                    self.project("short-one", category="ops", tags=["q4"])]
        lines = self.run_on(projects, size=(108, 30)).lines
        x = self.columns(lines[self.head_index(lines)])
        long_row, short_row = (lines[self.row_of(lines[:-10], text)] for text in (label, "short-one"))
        for value, start, end in ((cat, x["CATEGORY"], x["TAGS"]), (", ".join(tags), x["TAGS"], x["LAST WORKED"])):
            cell = long_row[start:end].rstrip()
            self.assertTrue(cell.endswith("…"), cell)
            self.assertTrue(value.startswith(cell[:-1]) and len(cell) > 1, cell)
            self.assertLessEqual(len(cell), 14)
        self.assertEqual(short_row[x["CATEGORY"]:x["TAGS"]].strip(), "ops")
        self.assertEqual(short_row[x["TAGS"]:x["LAST WORKED"]].strip(), "q4")

    def test_the_column_titles_line_up_with_their_columns(self):
        import unittest.mock
        p = self.project("lined-up", [self.sessions[1]], label="Lined up", status="active", category="ops", tags=["q4"],
                         last=self.PAST)
        with unittest.mock.patch.object(FMT, "date_order", lambda: "MDY"):
            lines = self.run_on([p]).lines
        x = self.columns(lines[self.head_index(lines)])
        row = lines[self.row_of(lines[:-10], "Lined up")]
        for title, text in (("NAME", "Lined up"), ("STATUS", "active"), ("SESSIONS", "◆1"), ("CATEGORY", "ops"),
                            ("TAGS", "q4"), ("LAST WORKED", "01/05/25")):
            self.assertEqual(row[x[title]:x[title] + len(text)], text, (title, row))

    def test_columns_give_up_width_in_order_as_the_window_narrows(self):
        """From the window exactly wide enough for every column (NAME 48 + STATUS 61 + 12 + 14 + 14 + 12,
        + 6 = 167: no spare width) down to 100, one long name and a long status: LAST WORKED goes first
        (and isn't drawn), then SESSIONS, then TAGS, then CATEGORY, then STATUS; NAME holds its target
        (48) throughout. A narrowed column's title is cut with "…". LAST WORKED is 12 wide: once SESSIONS
        is whole, 12 columns more fit everything."""
        name = "Quarterly laptop refresh and printer fleet consolidation for every office"
        status = "waiting on the vendor's revised quote for the second office"
        cat, tags = "vendor-evaluation", ["alpha", "bravo", "charlie"]
        p = self.project("narrowing", label=name, status=status, category=cat, tags=tags, last=self.PAST)
        frames = {}

        async def sweep(pilot):
            for w in range(EXACT, 99, -1):
                await pilot.resize_terminal(w, 30)
                await pilot.pause()
                frames[w] = screen_text(pilot.app)
        S = len(status) + 2
        EXACT = 48 + S + 12 + 14 + 14 + 12 + 6
        self.run_on([p], [sweep], size=(EXACT, 30))

        def frame(w):
            lines = frames[w]
            x = self.columns(lines[self.head_index(lines)])
            row = lines[self.row_of(lines[:-10], name[:12])]
            return x, self.widths(x), row
        x, wd, row = frame(EXACT)
        self.assertEqual(wd["STATUS"], S)
        cat_full, tags_full = row[x["CATEGORY"]:x["CATEGORY"] + 14].rstrip(), row[x["TAGS"]:x["TAGS"] + 14].rstrip()
        phases, cut_sessions = set(), []
        for w in sorted(frames, reverse=True):
            x, wd, row = frame(w)
            lw, ses, tg, ct = ("LAST WORKED" in x), ("SESSIONS" in x), ("TAGS" in x), ("CATEGORY" in x)
            cat_ok = ct and row[x["CATEGORY"]:x["CATEGORY"] + 14].rstrip() == cat_full
            tags_ok = tg and row[x["TAGS"]:x["TAGS"] + 14].rstrip() == tags_full
            status_ok = status in row
            name_w = wd["NAME"]
            if lw:  # LAST WORKED still has width: every other column is whole
                self.assertEqual((wd.get("SESSIONS"), wd.get("CATEGORY"), wd.get("TAGS")), (12, 14, 14), w)
                self.assertTrue(status_ok, w)
            else:
                self.assertNotRegex(row, SHORT_DATE, w)
                self.assertNotRegex(row, r"\d:\d\d (AM|PM)", w)
            if ses:  # SESSIONS has width: TAGS, CATEGORY, STATUS are whole
                self.assertTrue(tags_ok and cat_ok and status_ok, (w, row))
            if tg:  # TAGS has width: CATEGORY and STATUS are whole
                self.assertTrue(cat_ok and status_ok, (w, row))
            if ct:  # CATEGORY has width: STATUS is whole
                self.assertTrue(status_ok, (w, row))
            self.assertEqual(name_w, 48, w)  # others are still there: NAME holds its target
            header = frames[w][self.head_index(frames[w])]
            for title, _ in self.TITLES:
                if title in wd and wd[title] < len(title):  # a narrowed title is cut with …
                    cell = header[x[title]:x[title] + wd[title]].rstrip()
                    self.assertTrue(cell.endswith("…") and title.startswith(cell[:-1]), (w, title, cell))
            if ses and wd.get("SESSIONS", 12) < 12:
                cut_sessions.append(w)
            phases.add("all" if lw else "sessions" if ses else "tags" if tg else "category" if ct else "status")
        self.assertEqual(phases, {"all", "sessions", "tags", "category", "status"})
        whole = max(cut_sessions) + 1  # the narrowest window with SESSIONS whole: LAST WORKED has nothing
        look = lambda w: (frames[w][self.head_index(frames[w])].rstrip(), frame(w)[2].rstrip())
        self.assertEqual(look(whole + 12), look(EXACT))  # LAST WORKED's 12 columns fit everything
        last_title = look(whole + 10)[0][x_lw:] if (x_lw := self.columns(look(whole + 10)[0]).get("LAST WORKED")) else ""
        self.assertTrue(last_title.endswith("…") and "LAST WORKED".startswith(last_title[:-1]), last_title)  # 10 of 12: cut



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
            self.assertEqual({k: meta.get(k) for k in ("summary", "type", "status", "sessions")},
                             {"summary": f"The {slug} work.", "type": kind, "status": "active", "sessions": []})
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
        self.assertEqual(roost.frontmatter((self.root / "rollout" / "sub" / "alpha" / "README.md").read_text())["summary"],
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
            if kind in ("project", "head", "new"):
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
        self.assertIn("blocked", got.get("Status", ""), fields)

    def test_a_sub_rows_actions_start_a_session_in_it(self):
        calls = self.capture_start()
        ui = self.fixture()
        self.select(ui, "project", "rollout")
        ui.keys_projects("right", ui.project_rows())
        rows = self.select(ui, "sub", "rollout/alpha")
        acts = labels(ui, ui.keys_projects, rows)
        for want in ("Start a Session", "Show its folder in Finder", "New sub-project…"):
            self.assertTrue([l for l in acts if l.startswith(want)], (want, acts))
        act(ui, ui.keys_projects, ui.project_rows(), "Start a Session")
        self.assertEqual([c[0] for c in calls], ["rollout/alpha"])

    def test_a_projects_actions_offer_new_sub_project_and_start_one_in_a_sub_project(self):
        calls = self.capture_start()
        ui = self.fixture()
        rows = self.select(ui, "project", "other")
        acts = labels(ui, ui.keys_projects, rows)
        self.assertIn("New sub-project…", acts)
        self.assertFalse([l for l in acts if l.startswith("Start a Session in a sub-project")], acts)  # none open
        rows = self.select(ui, "project", "rollout")
        acts = labels(ui, ui.keys_projects, rows)
        self.assertIn("New sub-project…", acts)
        self.assertIn("Start a Session in a sub-project…", acts)
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
        self.assertIn("New sub-project…", labels(ui, ui.keys_projects, self.select(ui, "project", "rollout")))
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
        self.assertEqual((meta.get("type"), meta.get("summary")), ("chore", "Tidy the cables."))
        self.assertTrue(ui.msg.startswith("Created"), ui.msg)
        self.assertIn(("sub", "rollout/tidy-cables"), self.after(ui, "rollout"))  # its parent is unfolded
        r = ui.project_rows()[ui.psel]
        self.assertEqual((r.kind, r.item["id"]), ("sub", "rollout/tidy-cables"))

    def test_new_sub_project_with_a_bad_slug_creates_nothing(self):
        ui = self.fixture()
        before = sorted(p.name for p in (self.root / "rollout" / "sub").iterdir())
        self.assertIn("New sub-project…", labels(ui, ui.keys_projects, self.select(ui, "project", "rollout")))
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


class ProjectDelete(unittest.TestCase):
    """Delete… on a project or sub-project: a confirm box, then the id typed exactly, then its folder goes
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
        """Select the row and choose Delete… from its actions."""
        if kind == "sub":
            self.select(ui, "project", rid.split("/")[0])
            if ("sub", rid) not in self.after(ui, rid.split("/")[0]):
                ui.keys_projects("right", ui.project_rows())
        act(ui, ui.keys_projects, self.select(ui, kind, rid), "Delete…")

    def ids(self, ui):
        return [r.item["id"] for r in ui.project_rows() if r.kind in ("project", "sub")]

    def fixture(self):
        ui = SubProjectRows.fixture(self)
        ui.DIM = ui.GREEN = ui.YELLOW = ui.RED = 0  # confirm_text's colors, set by curses in the app
        return ui

    def test_a_real_projects_actions_include_delete(self):
        ui = self.fixture()
        self.assertIn("Delete…", labels(ui, ui.keys_projects, self.select(ui, "project", "rollout")))
        p = next(x for x in ui.projects if x["id"] == "rollout")
        demo_like = dict(p, path=str(Path(self.tmp.name) / "not-a-folder"))
        self.assertNotIn("Delete…", [l for l, _ in ui.project_actions(demo_like)])  # like Rename…: real folders only

    def test_a_sub_projects_actions_include_delete(self):
        ui = self.fixture()
        self.select(ui, "project", "rollout")
        ui.keys_projects("right", ui.project_rows())
        self.assertIn("Delete…", labels(ui, ui.keys_projects, self.select(ui, "sub", "rollout/alpha")))

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
    """Rename… on a sub-project: a prompt starting with its slug renames <project>/sub/<old> to sub/<new>."""
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
        act(ui, ui.keys_projects, self.select(ui, "sub", rid), "Rename…")

    def test_a_sub_projects_actions_include_rename(self):
        ui = self.fixture()
        self.select(ui, "project", "rollout")
        ui.keys_projects("right", ui.project_rows())
        self.assertIn("Rename…", labels(ui, ui.keys_projects, self.select(ui, "sub", "rollout/alpha")))

    def test_rename_starts_with_the_slug_and_renames_its_folder(self):
        ui = self.fixture()
        self.open_rename(ui)
        self.assertIsNotNone(ui.asking)
        self.assertEqual(ui.asking["text"], "alpha")
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
            self.answer(ui, bad)
            self.assertTrue(ui.msg.startswith("Couldn't rename it"), (bad, ui.msg))
            self.assertEqual(sorted(p.name for p in (self.root / "rollout" / "sub").iterdir()), before, bad)
            self.assertIn("\n# alpha\n", (self.root / "rollout" / "sub" / "alpha" / "README.md").read_text(), bad)
            self.assertIn("\n# beta\n", (self.root / "rollout" / "sub" / "beta" / "README.md").read_text(), bad)

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


if __name__ == "__main__":
    unittest.main()
