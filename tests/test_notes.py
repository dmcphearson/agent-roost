from support import *  # noqa: F401,F403 (the shared namespace, helpers, and fixtures)
from support import _SCRATCH, own_tests  # noqa: F401
from test_projects import Projects, ProjectRows  # for their helpers; their tests run in test_projects.py
from test_screens import TextualScreens  # for its helpers; its tests run in test_screens.py

load_tests = own_tests(__name__)


class Notes(unittest.TestCase):
    """2.3: the person's own notes and the Notes tab, in temp folders only."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.saved = roost.CONFIG, roost.DONE, roost.NOTES, roost.osa, roost.claude_settings, roost.trash, roost.PLUGIN
        roost.CONFIG, roost.DONE, roost.NOTES = base / "config.json", base / "_done", base / "notes"
        roost.claude_settings, roost.PLUGIN = (lambda: {}), base / "plugin"
        (roost.PLUGIN / ".claude-plugin").mkdir(parents=True)
        (roost.PLUGIN / ".claude-plugin" / "plugin.json").write_text("{}")
        self.root = base / "projects"
        self.root.mkdir()
        roost.save_config(projects_dir=str(self.root))
        self.scripts, self.trashed = [], []
        roost.osa = lambda script, *args: self.scripts.append(args) or "ok"
        roost.trash = lambda path: self.trashed.append(path) or Path(path).unlink()

    def tearDown(self):
        roost.CONFIG, roost.DONE, roost.NOTES, roost.osa, roost.claude_settings, roost.trash, roost.PLUGIN = self.saved
        self.tmp.cleanup()

    def project(self, name):
        folder = self.root / name
        (folder / "handoffs").mkdir(parents=True)
        (folder / "README.md").write_text("---\nsummary: s\nstatus: active\nsessions: []\n---\n\n# x\n")
        return folder

    def ui(self):
        ui = Projects.ui(self)
        ui.term, ui.tab, ui.helping, ui.sel, ui.query, ui.warning = "ghostty", 2, False, 0, "", ""
        ui.nsel, ui.ntop, ui.nrow_y, ui.nquery, ui.quit_anyway, ui.nfolded = 0, 0, {}, "", False, {"Recaps"}
        ui.nreading, ui.nscroll, ui.npane, ui.nlist_w, ui.TABS = False, 0, (4, 40, 10, 40), 200, roost.UI.TABS
        ui.focus = "pane"  # the Notes tab shown (tab 2), so the keys go to it
        ui.edited = []
        ui.edit = lambda path: ui.edited.append(path)  # no editor in tests
        return ui

    def test_a_new_note_has_its_frontmatter_and_title(self):
        path = roost.create_note("  Printer   driver: idea ", today=roost.datetime(2026, 10, 10))
        self.assertEqual(path, roost.NOTES / "printer-driver-idea.md")
        self.assertEqual(path.read_text(), '---\ntitle: "Printer driver: idea"\ncreated: 2026-10-10\nproject:\n'
                                           "sessions: []\ntags: []\n---\n\n# Printer driver: idea\n\n")
        self.assertEqual(roost.create_note("Printer driver idea"), roost.NOTES / "printer-driver-idea-2.md")
        self.assertEqual(roost.create_note("!!!").name, "note.md")
        with self.assertRaises(ValueError):
            roost.create_note("   ")

    def test_moving_a_note_into_a_project_sets_its_project(self):
        path, folder = roost.create_note("Idea"), self.project("p")
        dest = roost.file_note(path, folder)
        self.assertEqual((dest, path.exists()), (folder / "notes" / "idea.md", False))
        self.assertEqual(roost.frontmatter(dest.read_text())["project"], "p")
        self.assertEqual(dest.read_text().count("project:"), 1)
        with self.assertRaises(ValueError):
            roost.file_note(roost.create_note("Idea"), folder)  # never replaces a note

    def test_moving_a_note_with_no_project_line_adds_one(self):
        roost.NOTES.mkdir(parents=True, exist_ok=True)
        path = roost.NOTES / "plain.md"
        path.write_text("---\ntitle: Plain\ntags: []\n---\n\n# Plain\n")
        dest = roost.file_note(path, self.project("p"))
        meta = roost.frontmatter(dest.read_text())
        self.assertEqual((meta["project"], meta["title"]), ("p", "Plain"))
        self.assertIn("# Plain", dest.read_text())

    def test_pickup_and_brief_take_a_notes_path(self):
        path = roost.create_note("Idea")
        self.assertEqual(roost.find_project(roost.short(str(path))), path)
        self.assertIn("# Note: Idea", roost.brief(path))
        with self.assertRaises(ValueError):
            roost.find_project(str(self.root / "elsewhere.md"))  # only notes roost lists
        folder = self.project("p")
        roost.file_note(roost.create_note("Vendor list"), folder)
        self.assertIn("- notes/vendor-list.md: Vendor list", roost.brief(folder))

    def test_starting_a_session_from_a_note_lists_it_there(self):
        path = roost.create_note("Idea")
        msg = roost.start_session(str(path), "/tmp", "ghostty", name="Idea")
        self.assertEqual(msg, "Started a Session for Idea in /tmp")
        self.assertIn(f"/roost:pickup {path}", self.scripts[0][1])
        self.assertEqual(len(roost.frontmatter(path.read_text())["sessions"]), 1)

    def test_the_editor_setting_then_visual_then_editor_then_micro(self):
        saved, env = roost.shutil.which, dict(os.environ)
        try:
            installed = {"micro", "vim", "code", "nano"}
            roost.shutil.which = lambda cmd: cmd if cmd in installed else None
            os.environ.pop("VISUAL", None); os.environ.pop("EDITOR", None)
            self.assertEqual(roost.editor(), (["micro"], True))
            os.environ["EDITOR"] = "nano"
            self.assertEqual(roost.editor(), (["nano"], True))
            os.environ["VISUAL"] = "code -w"
            self.assertEqual(roost.editor(), (["code", "-w"], False))  # its own window: roost doesn't wait
            roost.save_config(editor="vim", editor_wait=False)
            self.assertEqual(roost.editor(), (["vim"], False))
            roost.save_config(editor="gone", editor_wait=None)
            installed.clear()
            self.assertIsNone(roost.editor())  # never falls back to pico
        finally:
            roost.shutil.which = saved
            os.environ.clear(); os.environ.update(env)

    def test_ctrl_n_from_any_tab_asks_then_opens_it(self):
        ui = self.ui()
        ui.tab = 0
        ui.dispatch("ctrl+n", [])
        self.assertEqual([label.split()[0] for label, _ in ui.choosing["options"]], ["Project", "Session", "Note"])
        ui.dispatch("N", [])  # shift+n picks Note
        self.assertEqual(ui.asking["title"], "New note: its title")  # in a box, not on the bottom row
        typed(ui, "Q4 ideas\n")
        self.assertEqual(ui.edited, [str(roost.NOTES / "q4-ideas.md")])

    def test_ctrl_n_makes_a_project_or_starts_a_session_in_a_folder(self):
        import unittest.mock
        ui = self.ui()
        ui.dispatch("ctrl+n", [])
        ui.dispatch("P", [])
        self.assertEqual(ui.asking["title"], "New project: its name")
        ui.dispatch("escape", [])
        folder = tempfile.mkdtemp()
        self.addCleanup(os.rmdir, folder)
        started = []
        with unittest.mock.patch.object(roost, "start_plain", lambda cwd, term: started.append(cwd) or {"start": ["claude"], "cwd": cwd}):
            ui.dispatch("ctrl+n", [])
            ui.dispatch("s", [])
            self.assertEqual(ui.asking["title"], "New Session: in which folder")
            ui.asking["text"] = folder
            self.assertEqual(ui.dispatch("enter", []), {"start": ["claude"], "cwd": os.path.realpath(folder)})
            ui.dispatch("ctrl+n", []); ui.dispatch("S", [])
            ui.asking["text"] = folder + "/nope"
            self.assertIsNone(ui.dispatch("enter", []))
        self.assertIn("No folder at", ui.msg)
        self.assertEqual(started, [os.path.realpath(folder)])

    def test_a_plain_session_runs_claude_in_its_folder(self):
        import unittest.mock
        with unittest.mock.patch.object(roost, "plugin_args", lambda: ["--plugin-dir", "/p"]):
            self.assertEqual(roost.start_plain("/work", None), {"start": ["claude", "--plugin-dir", "/p"], "cwd": "/work"})
        def none():
            raise ValueError("no plugin")
        with unittest.mock.patch.object(roost, "plugin_args", none):
            self.assertEqual(roost.start_plain("/work", None)["start"], ["claude"])

    def test_the_notes_tab_lists_new_note_last_and_searches(self):
        roost.create_note("Laptop budget")
        roost.create_note("Printer idea")
        ui = self.ui()
        self.assertEqual([r["id"] for r in ui.note_rows()][-1], "+ New note")  # last, under the notes
        ui.nquery = "lapt"
        self.assertEqual([r["name"] for r in ui.note_rows() if r.get("path")], ["Laptop budget"])
        ui.nquery = ""
        ui.nsel = len(ui.note_rows()) - 1
        ui.keys_notes("enter", ui.note_rows())  # enter on + New note
        self.assertEqual(ui.asking["title"], "New note: its title")

    def test_the_notes_search_matches_tags(self):
        roost.set_tags(roost.create_note("Laptop budget"), ["backlog"])
        roost.create_note("Printer idea")
        ui = self.ui()
        ui.nquery = "backlog"
        self.assertEqual([r["name"] for r in ui.note_rows() if r.get("path")], ["Laptop budget"])

    def test_notes_are_listed_as_backlog_recaps_then_notes_with_recaps_folded(self):
        roost.create_note("Plain idea")
        roost.set_tags(roost.create_note("Maybe a project"), ["backlog"])
        roost.set_tags(roost.create_note("Both"), ["recap", "backlog"])  # backlog wins
        roost.set_tags(roost.create_note("A recap"), ["recap"])
        ui = self.ui()
        shape = lambda: [r.get("head") and f"{r['head']} ({r['count']})" or r.get("name") or r["id"] for r in ui.note_rows()]
        self.assertEqual(shape(), ["Backlog (2)", "Both", "Maybe a project", "Recaps (1)", "Notes (1)", "Plain idea", "+ New note"])
        ui.nsel = 3
        ui.keys_notes("enter", ui.note_rows())  # enter on a header opens it
        self.assertIn("A recap", shape())
        ui.keys_notes("left", ui.note_rows())  # ← closes it
        self.assertNotIn("A recap", shape())
        ui.keys_notes("right", ui.note_rows())  # → opens it
        self.assertIn("A recap", shape())
        ui.nsel = 0
        ui.keys_notes("enter", ui.note_rows())
        self.assertEqual(shape()[:2], ["Backlog (2)", "Recaps (1)"])
        ui.nquery = "recap"  # a search opens every section
        self.assertEqual(shape(), ["Backlog (1)", "Both", "Recaps (1)", "A recap", "+ New note"])
        ui.nquery, ui.nsel = "", 0
        self.assertEqual(ui.note_facts(ui.note_rows()[0])[0][0][0], "Section")

    def test_making_a_project_from_a_note(self):
        ui = self.ui()
        roost.create_note("Printer rollout")
        ui.load_projects()
        ui.nsel = 1  # the first note, under its header
        act(ui, lambda k, rows: ui.keys_notes(k, ui.note_rows()), None, "Make it a project")
        self.assertEqual(ui.asking["text"], "Printer rollout")  # its name, then its folder, then its summary
        typed(ui, "\n")
        self.assertEqual(ui.asking["text"], "printer-rollout")
        typed(ui, "\n\n")
        self.assertTrue((self.root / "printer-rollout" / "notes" / "printer-rollout.md").exists())
        self.assertEqual(roost.read_project(self.root / "printer-rollout")["summary"], "Printer rollout")

    def test_right_reads_a_note_and_left_or_esc_goes_back(self):
        path = roost.create_note("Idea")
        path.write_text(path.read_text() + "- a list item long enough to wrap onto a second line here\n")
        ui = self.ui()
        ui.load_projects()
        ui.nsel = 1
        rows = ui.note_rows()
        ui.keys_notes("right", rows)
        self.assertTrue(ui.nreading)
        ui.keys_notes("down", rows)
        self.assertEqual((ui.nsel, ui.nscroll), (1, 1))  # ↓ scrolls the text, not the list
        ui.keys_notes("left", rows)
        self.assertFalse(ui.nreading)
        ui.nreading = True
        ui.dispatch("escape", rows)
        self.assertFalse(ui.nreading)

    def test_a_note_opens_with_the_cursor_on_its_last_line(self):
        path = roost.create_note("Idea")  # frontmatter, the title, then a blank line: 10 lines
        p = str(path)
        self.assertEqual(roost.at_end(["micro"], p), ["micro", p, "+10"])
        self.assertEqual(roost.at_end(["/usr/bin/vim"], p), ["/usr/bin/vim", "+10", p])
        self.assertEqual(roost.at_end(["code", "-w"], p), ["code", "-w", "-g", f"{p}:10"])
        self.assertEqual(roost.at_end(["ed"], p), ["ed", p])

    def test_the_scratchpad_lists_loose_notes_only(self):
        loose = roost.create_note("Laptop budget", today=roost.datetime(2026, 10, 1))
        loose.write_text(loose.read_text() + "Six laptops are old.\n")
        (self.project("p") / "notes").mkdir()
        (self.root / "p" / "notes" / "filed.md").write_text("# Filed\n")
        (roost.NOTES / "hand.md").write_text("# Hand written\n\nNo frontmatter here.\n")
        os.utime(loose, (1, 1))
        notes = roost.read_notes()
        self.assertEqual([n["name"] for n in notes], ["Hand written", "Laptop budget"])
        self.assertEqual((notes[1]["created"], notes[1]["lines"]), ("2026-10-01", ["Six laptops are old."]))
        self.assertEqual(notes[0]["summary"], "No frontmatter here.")

    def test_a_notes_tags(self):
        path = roost.create_note("Idea")
        ui = self.ui()
        ui.load_projects()
        ui.nsel = next(i for i, n in enumerate(ui.note_rows()) if n.get("path") == str(path))
        act(ui, lambda k, rows: ui.keys_notes(k, ui.note_rows()), None, "Edit its tags")
        for word in ["ideas", "Printers"]:
            for k in names(word):
                ui.keys_choice(k)
            ui.keys_choice("enter")
        ui.keys_choice("enter")  # Save
        self.assertIn("tags: [ideas, printers]\n", path.read_text())
        self.assertEqual(next(n for n in ui.notes if n["path"] == str(path))["tags"], ["ideas", "printers"])

    def test_a_notes_actions(self):
        ui = self.ui()
        roost.create_note("Idea")
        ui.load_projects()
        ui.nsel = 1
        keys = lambda k, rows: ui.keys_notes(k, ui.note_rows())
        self.assertEqual(labels(ui, keys, None)[1:], ["Start a Session from it", DIV, "Make it a project", "Edit its tags",
                                                      "Show it in Finder", DIV, "Delete to the Trash"])
        self.project("p")
        ui.load_projects()
        act(ui, keys, None, "Move to a project")
        ui.keys_choice("enter")
        self.assertEqual(ui.note_rows()[1:], [])  # moved notes leave the scratchpad
        self.assertTrue((self.root / "p" / "notes" / "idea.md").exists())
        roost.create_note("Another")
        ui.load_projects()
        ui.nsel = 1
        act(ui, keys, None, "Delete to the Trash")
        ui.keys_confirm("y")
        self.assertEqual((len(self.trashed), ui.note_rows()[1:]), (1, []))

    def test_a_notes_actions_in_groups(self):
        """Work on it, then organize it, then delete it, split by dividers (| here)."""
        ui = self.ui()
        roost.create_note("Idea")
        self.project("p")
        ui.load_projects()
        ui.nsel = 1
        keys = lambda k, rows: ui.keys_notes(k, ui.note_rows())
        self.assertEqual(labels(ui, keys, None),
                         [f"Edit in {ui.editor_name().split()[0]}", "Start a Session from it", DIV,
                          "Move to a project", "Make it a project", "Edit its tags", "Show it in Finder", DIV,
                          "Delete to the Trash"])

    def test_no_action_label_has_an_ellipsis(self):
        """A menu label is just its action's name: none ends in, or holds, "…". Row: a note in no project,
        with a project it could move to."""
        ui = self.ui()
        roost.create_note("Idea")
        self.project("p")
        ui.load_projects()
        ui.nsel = 1
        keys = lambda k, rows: ui.keys_notes(k, ui.note_rows())
        real = [a for a in labels(ui, keys, None) if a != DIV]
        self.assertTrue(real)
        self.assertEqual([a for a in real if "…" in a], [])

    def test_roost_note_saves_from_the_command_line(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(roost.run_command("note", ["Printer idea", "Push", "it", "from", "the", "MDM."]), 0)
        path = roost.NOTES / "printer-idea.md"
        self.assertEqual(out.getvalue().strip(), f"Saved {roost.short(str(path))}")
        self.assertTrue(path.read_text().endswith("# Printer idea\n\nPush it from the MDM.\n"))
        self.assertEqual(roost.read_notes()[0]["lines"], ["Push it from the MDM."])
        saved = roost.sys.stdin
        try:
            roost.sys.stdin = io.StringIO("From stdin.\n")
            self.assertEqual(roost.run_command("note", ["Piped"]), 0)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(roost.run_command("note", [" "]), 1)
        finally:
            roost.sys.stdin = saved
        self.assertIn("From stdin.", (roost.NOTES / "piped.md").read_text())


@unittest.skipUnless(roost.App, "Textual isn't installed here: run the tests with ~/.roost/venv/bin/python")
class NotesCreated(unittest.TestCase):
    """The selected note's card footer shows Created as a short date in the locale's order, like Edited,
    but never a time: a created: line is a plain date."""
    setUp = ProjectRows.setUp
    select = staticmethod(ProjectRows.select)
    run_on = ProjectRows.run_on

    def note(self, name, created=None):
        """A note written by hand; created=None leaves out the created: line."""
        roost.NOTES.mkdir(parents=True, exist_ok=True)
        line = "" if created is None else f"created: {created}\n"
        (roost.NOTES / f"{roost.slug(name)}.md").write_text(f"---\ntitle: {name}\n{line}tags: []\n---\n\n# {name}\n")

    def footer(self, name, order):
        """The drawn screen's line with Created on it, with the note called name selected."""
        import unittest.mock
        pick = self.select(lambda ui: setattr(ui, "nsel", next(i for i, r in enumerate(ui.nrows) if r.get("name") == name)))
        with unittest.mock.patch.object(FMT, "date_order", lambda: order):
            lines = self.run_on([], [pick], tab="Notes").lines
        return next((l for l in lines if "Created" in l), "\n".join(lines))

    @staticmethod
    def today(order):
        d = datetime.now()
        return f"{d:%d/%m/%y}" if order == "DMY" else f"{d:%m/%d/%y}"

    def test_created_shows_as_a_short_date_month_or_day_first(self):
        self.note("Printer idea", "2026-10-12")
        for order, shown in (("MDY", "10/12/26"), ("DMY", "12/10/26")):
            line = self.footer("Printer idea", order)
            self.assertRegex(line, rf"Created +{shown}(\s|$)", order)
            self.assertNotIn("2026-10-12", line, order)

    def test_created_with_a_time_after_its_date_shows_the_date_alone(self):
        self.note("Printer idea", "2026-10-12T09:30")
        line = self.footer("Printer idea", "MDY")
        self.assertRegex(line, r"Created +10/12/26(\s|$)")
        self.assertNotRegex(line, r"Created +\S*\s*\d?\d:\d\d")

    def test_a_note_created_today_shows_todays_date_not_a_time(self):
        roost.create_note("Laptop budget")  # created: today's date
        for order in ("MDY", "DMY"):
            line = self.footer("Laptop budget", order)
            self.assertRegex(line, rf"Created +{re.escape(self.today(order))}(\s|$)", order)
            self.assertNotRegex(line, r"Created +\d?\d:\d\d", order)

    def test_a_created_value_that_isnt_a_date_shows_as_written(self):
        self.note("Printer idea", "last spring")
        self.assertRegex(self.footer("Printer idea", "DMY"), r"Created +last spring(\s|$)")

    def test_a_note_without_created_shows_its_files_date_short(self):
        self.note("Hand written")  # no created: line, so the file's own date: made just now
        for order in ("MDY", "DMY"):
            line = self.footer("Hand written", order)
            self.assertRegex(line, rf"Created +{re.escape(self.today(order))}(\s|$)", order)
            self.assertNotRegex(line, r"Created +\d{4}-\d\d-\d\d", order)


if __name__ == "__main__":
    unittest.main()


def body(n, word="Line"):
    """n numbered lines, each its own: Line 001, Line 002, …"""
    return [f"{word} {i:03d}" for i in range(1, n + 1)]


class NotePreview(unittest.TestCase):
    """While the list has the focus, the card shows only the first NOTE_PREVIEW_LINES (80) lines of a
    note, then a line saying how many more there are and that → reads the whole note. note_markdown(r,
    limit) gives that text; with no limit, the whole note."""

    def test_the_preview_limit_is_80_lines(self):
        self.assertEqual(roost.NOTE_PREVIEW_LINES, 80)

    def test_a_long_note_is_cut_to_its_first_80_lines_then_says_how_many_more(self):
        r = {"lines": body(200)}
        cut = roost.note_markdown(r, limit=roost.NOTE_PREVIEW_LINES)
        whole = roost.note_markdown(r)
        self.assertEqual(cut.splitlines()[:80], whole.splitlines()[:80])
        self.assertNotIn("Line 081", cut)
        self.assertNotIn("Line 200", cut)
        tail = [l for l in cut.splitlines()[80:] if l.strip()]
        self.assertEqual(len(tail), 1, tail)  # one footer line, nothing more of the note
        self.assertRegex(tail[0], r"\b120 more lines\b")
        self.assertIn("→ reads the whole note", tail[0])

    def test_the_cut_counts_the_lines_roost_shows(self):
        """The limit applies to the Markdown the card would show (task boxes and all), not a second copy."""
        r = {"lines": [f"- [ ] task {i:03d}" for i in range(1, 101)]}
        cut = roost.note_markdown(r, limit=80)
        self.assertEqual(cut.splitlines()[:80], roost.note_markdown(r).splitlines()[:80])
        self.assertIn("- ☐ task 080", cut)
        self.assertNotIn("task 081", cut)
        self.assertRegex(cut, r"\b20 more lines\b")

    def test_a_note_of_80_lines_or_fewer_shows_whole_with_no_footer(self):
        for n in (1, 79, 80):
            r = {"lines": body(n)}
            with self.subTest(lines=n):
                cut = roost.note_markdown(r, limit=80)
                self.assertEqual(cut, roost.note_markdown(r))
                self.assertNotIn("more lines", cut)
                self.assertNotIn("reads the whole note", cut)

    def test_no_limit_is_the_whole_note(self):
        text = roost.note_markdown({"lines": body(200)})
        self.assertEqual(text.splitlines(), body(200))
        self.assertNotIn("more lines", text)


@unittest.skipUnless(roost.App, "Textual isn't installed here: run the tests with ~/.roost/venv/bin/python")
class NoteCardRendering(unittest.TestCase):
    """The Notes tab's card on a real Textual screen: the preview while in the list, the whole note once
    reading, and one render per run of quick moves (the Reader waits NOTE_SETTLE after the last one).
    What the card renders is read from the calls to its Markdown widget's update."""
    setUp = TextualScreens.setUp
    keys = staticmethod(TextualScreens.keys)
    SETTLED = 0.5  # NOTE_SETTLE is patched to this so a slow machine's key presses still land inside it

    def test_the_settle_is_0_15_seconds(self):
        self.assertEqual(roost.NOTE_SETTLE, 0.15)

    def run_notes(self, steps, settle=None):
        """roost on the Notes tab, recording each text the card's Markdown is given, in self.rendered."""
        import asyncio, unittest.mock
        from textual.widgets import Markdown
        self.rendered = []
        real = Markdown.update

        def spy(md, text):
            if isinstance(md.parent, roost.Reader):
                self.rendered.append(text)
            return real(md, text)
        patches = [unittest.mock.patch.object(Markdown, "update", spy),
                   unittest.mock.patch.multiple(roost, read_config=lambda: {"tab": "Notes"})]
        steps = [open_view("Notes")] + list(steps)  # roost starts on the Notes tab, as remembered
        if settle is not None:
            # in the module that defines it; create: before the fix there's none, and the test fails on what the card does
            patches.append(unittest.mock.patch.object(importlib.import_module("roost.tabs.notes"), "NOTE_SETTLE", settle, create=True))
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            app = roost.RoostApp(self.sessions, "")

            async def go():
                async with app.run_test(size=(160, 40)) as pilot:
                    await pilot.pause()
                    for step in steps:
                        await step(pilot)
                    app.lines = screen_text(app)
            asyncio.run(go())
        return app

    def wait(self, seconds):
        async def step(pilot):
            await pilot.pause(seconds)
        return step

    def mark(self, label, seen):
        """A step that notes how many renders there have been so far, and the last text rendered."""
        async def step(pilot):
            seen[label] = (len(self.rendered), self.rendered[-1] if self.rendered else None)
        return step

    def notes(self, count):
        """count notes, newest first in the list: Note 1 (newest) … Note count, each body naming its note."""
        now = time.time()
        for i in range(1, count + 1):
            path = roost.create_note(f"Note {i}", body=f"Body of note {i}")
            os.utime(path, (now - i * 60, now - i * 60))

    # 1. The preview limit

    def test_in_the_list_the_card_gets_the_preview_and_after_right_the_whole_note(self):
        roost.create_note("Long note", body="\n".join(body(200)))
        seen = {}
        app = self.run_notes([self.wait(1.0), self.mark("list", seen), self.keys("right"), self.wait(1.0),
                              self.mark("reading", seen), self.keys("end"), self.wait(0.5)])
        _, listed = seen["list"]
        self.assertIsNotNone(listed, "the card never rendered the note")
        self.assertIn("Line 080", listed)
        self.assertNotIn("Line 081", listed)
        self.assertNotIn("Line 200", listed)
        self.assertRegex(listed, r"\b120 more lines\b")
        self.assertIn("→ reads the whole note", listed)
        self.assertTrue(app.ui.nreading)
        _, reading = seen["reading"]
        self.assertIn("Line 200", reading)
        self.assertNotIn("more lines", reading)
        self.assertTrue(any("Line 200" in l for l in app.lines), app.lines)  # scrolled to the end, the last line shows

    def test_back_in_the_list_the_card_shows_the_preview_again(self):
        roost.create_note("Long note", body="\n".join(body(200)))
        seen = {}
        self.run_notes([self.wait(1.0), self.keys("right"), self.wait(1.0), self.keys("left"), self.wait(1.0),
                        self.mark("back", seen)])
        _, back = seen["back"]
        self.assertNotIn("Line 200", back)
        self.assertRegex(back, r"\b120 more lines\b")

    def test_a_short_note_shows_whole_in_the_list(self):
        roost.create_note("Short note", body="\n".join(body(30)))
        seen = {}
        self.run_notes([self.wait(1.0), self.mark("list", seen)])
        _, listed = seen["list"]
        self.assertIn("Line 030", listed)
        self.assertNotIn("more lines", listed)

    # 2. Settle before rendering

    def test_five_quick_downs_render_the_card_once_with_the_fifth_note_down(self):
        self.notes(8)
        seen = {}

        async def start(pilot):
            seen["start"] = (pilot.app.ui.nsel, len(self.rendered))
        app = self.run_notes([self.wait(1.0), start, self.keys("down", "down", "down", "down", "down"),
                              self.mark("pressed", seen), self.wait(self.SETTLED + 0.5), self.mark("settled", seen)],
                             settle=self.SETTLED)
        ui = app.ui
        at, before = seen["start"]
        self.assertEqual(ui.nsel, at + 5)
        name = ui.nrows[ui.nsel]["name"]
        self.assertEqual(name, "Note 6")  # five down from Note 1
        count, last = seen["settled"]
        self.assertEqual(count - before, 1, self.rendered[before:])  # one render for the run of presses
        self.assertIn("Body of note 6", last)
        self.assertTrue(any("Body of note 6" in l for l in app.lines), app.lines)

    def test_one_move_waits_for_the_settle_before_rendering(self):
        self.notes(3)
        seen = {}
        self.run_notes([self.wait(1.0), self.mark("start", seen), self.keys("down"), self.mark("moved", seen),
                        self.wait(self.SETTLED + 0.5), self.mark("settled", seen)], settle=self.SETTLED)
        self.assertEqual(seen["moved"][0], seen["start"][0], "the card rendered before the selection settled")
        self.assertEqual(seen["settled"][0], seen["start"][0] + 1)
        self.assertIn("Body of note 2", seen["settled"][1])


@unittest.skipUnless(roost.App, "Textual isn't installed here: run the tests with ~/.roost/venv/bin/python")
class NoteListLook(unittest.TestCase):
    """The Notes list on a real screen, with the keys in the list: the selected note's row has no background
    fill; a green ▌ at the list's left edge marks it, and its name is bold green."""
    setUp = TextualScreens.setUp
    keys = staticmethod(TextualScreens.keys)

    def run_notes(self, steps):
        import unittest.mock
        now = time.time()
        for i, title in enumerate(("Printer idea", "Laptop idea", "Toner idea")):
            path = roost.create_note(title, body="first line")
            os.utime(path, (now - (i + 1) * 60, now - (i + 1) * 60))
        with unittest.mock.patch.multiple(roost, read_config=lambda: {"tab": "Notes"}):
            return TextualScreens.run_app(self, [open_view("Notes")] + list(steps), size=(120, 40))

    @staticmethod
    def rows(seen, label):
        """A step that keeps, under label: the selected note's name, and for each note row on screen its line,
        its name's styles, its ▌ marks, and its background colors across the list."""
        async def step(pilot):
            pilot.app.redraw()
            await pilot.pause()
            app, ui = pilot.app, pilot.app.ui
            box = app.query_one(roost.NoteList).region
            lines, out = screen_text(app), {}
            for y, idx in ui.nrow_y.items():
                r = ui.nrows[idx]
                if r.get("new") or r.get("head"):
                    continue
                x = lines[y].index(r["name"], box.x)
                out[r["name"]] = {"line": lines[y][box.x:box.right], "name_x": x,
                                  "name": [style_at(app, y, x + i) for i in range(len(r["name"]))],
                                  "bars": [(i, color_of(style_at(app, y, i))) for i in range(box.x, box.right) if lines[y][i:i + 1] == "▌"],
                                  "first": next((i for i in range(box.x, box.right) if lines[y][i:i + 1].strip()), None),
                                  "fills": backgrounds(app, y, box.x, box.right)}
            seen[label] = (ui.nrows[ui.nsel].get("name"), ui.nreading, out)
        return step

    def test_the_selected_note_has_no_fill_a_green_bar_at_the_lists_left_edge_and_a_bold_green_name(self):
        seen = {}
        self.run_notes([self.rows(seen, "first"), self.keys("down"), self.rows(seen, "second")])
        green = roost.GREEN.lower()
        self.assertNotEqual(seen["first"][0], seen["second"][0], "the fixture: ↓ moved to another note")
        for when in ("first", "second"):
            sel, reading, out = seen[when]
            with self.subTest(selected=sel):
                self.assertFalse(reading, "the fixture: the keys are in the list")
                got = out[sel]
                self.assertEqual(got["fills"], set(), f"no background fill on the selected row: {got['line']!r}")
                self.assertEqual(len(got["bars"]), 1, got["line"])
                bx, color = got["bars"][0]
                self.assertEqual(color, green, "the ▌ is green")
                self.assertEqual(bx, got["first"], f"the ▌ is the first thing on the row, at the list's left: {got['line']!r}")
                self.assertLess(bx, got["name_x"])
                self.assertTrue(all(st and st.bold and color_of(st) == green for st in got["name"]), "the name is bold green")
                for name, other in out.items():
                    if name != sel:
                        self.assertEqual(other["bars"], [], f"{name} has no ▌")
                        self.assertFalse([st for st in other["name"] if color_of(st) == green], f"{name} isn't green")
