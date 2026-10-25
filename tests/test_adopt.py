from support import *  # noqa: F401,F403 (the shared namespace, helpers, and fixtures)
from support import _SCRATCH, own_tests  # noqa: F401
from test_handoffs import Handoffs  # for their helpers; their tests run in test_handoffs.py
from test_projects import Projects  # for their helpers; their tests run in test_projects.py

load_tests = own_tests(__name__)


class Adopt(unittest.TestCase):
    """#54: roost adopt plans each folder, the person approves, and applying archives what it replaces."""
    setUp_handoffs, tearDown = Handoffs.setUp, Handoffs.tearDown
    urlopen, transcript = Handoffs.urlopen, Handoffs.transcript

    def setUp(self):
        self.setUp_handoffs()
        self.saved_plans = roost.ADOPT_PLANS
        roost.ADOPT_PLANS = Path(self.tmp.name) / "adopt"
        self.old = self.root / "2026-09-printer-rollout"
        (self.old / "badge-printers").mkdir(parents=True)
        (self.old / "README.md").write_text(f"---\ncategory: infrastructure\nsummary: Printers\nsessions:\n"
                                            f"  - name: Printer fix\n    id: {SID}\n---\n\n# Printer rollout\n\nOld notes.\n")
        for name, text in (("notes.md", "Working notes."), ("notes-backup.md", "Older notes."), ("HANDOFF.md", "Start here."),
                           ("export.csv", "a,b\n1,2\n"), ("badge-printers/plan.md", "Badges."), (".DS_Store", "x")):
            (self.old / name).write_text(text)
        (self.old / "report.pdf").write_bytes(b"%PDF-1.4 binary")
        self.addCleanup(setattr, roost, "ADOPT_PLANS", self.saved_plans)

    def plan(self, body=PLAN_BODY):
        return roost.parse_plan(roost.adopt_plan_text(self.old, body))

    def files(self, *folders):
        return sorted(str(p.relative_to(self.root)) for f in folders for p in f.rglob("*") if p.is_file())

    def test_the_new_name_drops_the_date(self):
        self.assertEqual(roost.adopt_name("2026-09-mdm-terraform-adoption"), "mdm-terraform-adoption")
        self.assertEqual(roost.adopt_name("printer-fleet-2"), "printer-fleet-2")

    def test_the_planner_sees_the_files_and_the_text_of_small_ones(self):
        text = roost.adopt_input(self.old)
        self.assertIn("Folder: 2026-09-printer-rollout", text)
        self.assertIn("- report.pdf (15 bytes)", text)
        self.assertNotIn("%PDF", text)
        self.assertIn("=== notes.md ===\nWorking notes.", text)
        self.assertIn("=== badge-printers/plan.md ===", text)
        self.assertNotIn(".DS_Store", text)
        self.assertGreater(roost.adopt_estimate(text, "claude-opus-5-5", roost.parse_prices(PRICE_PAGE)), 0)

    def test_a_plan_reads_back_with_its_readme_whole(self):
        plan = self.plan()
        self.assertEqual(plan["rename"], "printer-rollout")
        self.assertEqual(plan["moves"], [("export.csv", "data/export.csv"), ("report.pdf", "out/report.pdf")])
        self.assertEqual(plan["archive"], [("notes-backup.md", "an older copy of notes.md")])
        self.assertEqual(plan["split"], [("badge-printers", "badge-printers", "a separate rollout with its own finish line")])
        self.assertEqual(plan["first"][0], "HANDOFF.md")
        self.assertIn("### Next\n- Deploy", plan["first"][1])
        self.assertTrue(plan["readme"].startswith("# Printer rollout"))
        self.assertIn("## Files", plan["readme"])  # its own headings stay in it
        self.assertEqual(roost.plan_problems(self.old, plan), [])
        both = self.plan(PLAN_BODY.replace("## Split out", "- `HANDOFF.md`: replaced by the First Handoff\n## Split out"))
        self.assertEqual(both["archive"], plan["archive"])  # archived once, by the First Handoff step
        empty = roost.parse_plan("## Rename\nx\n## Summary\nS\n## Moves\nNone.\n## Archive\nNone.\n## README\n## Current state\n")
        self.assertEqual((empty["moves"], empty["archive"], empty["first"]), ([], [], None))

    def test_problems_stop_a_plan(self):
        (self.root / "printer-rollout").mkdir()
        bad = PLAN_BODY.replace("`notes-backup.md`", "`../elsewhere.md`").replace("`export.csv`", "`gone.csv`") \
                       .replace("## Current state", "## State")
        problems = " | ".join(roost.plan_problems(self.old, self.plan(bad)))
        for said in ("printer-rollout already exists", "outside the project folder", "`gone.csv` isn't a file",
                     "no ## Current state"):
            self.assertIn(said, problems)
        self.assertIn("never archived", " ".join(roost.plan_problems(self.old, self.plan(
            PLAN_BODY.replace("`notes-backup.md`", "`README.md`")))))
        with self.assertRaises(ValueError):
            roost.apply_plan(self.old, self.plan(bad))
        self.assertTrue((self.old / "export.csv").exists())  # nothing changed

    def test_two_moves_to_one_path_stop_the_plan(self):
        bad = self.plan(PLAN_BODY.replace("`out/report.pdf`", "`data/export.csv`"))
        self.assertIn("more than one Move", " ".join(roost.plan_problems(self.old, bad)))
        with self.assertRaises(ValueError):
            roost.apply_plan(self.old, bad)
        self.assertTrue((self.old / "export.csv").exists() and (self.old / "report.pdf").exists())

    def test_a_move_onto_a_file_already_there_stops_the_plan(self):
        bad = self.plan(PLAN_BODY.replace("`out/report.pdf`", "`notes.md`"))
        self.assertIn("`notes.md` already exists", " ".join(roost.plan_problems(self.old, bad)))
        with self.assertRaises(ValueError):
            roost.apply_plan(self.old, bad)
        self.assertEqual((self.old / "notes.md").read_text(), "Working notes.")  # never replaced
        self.assertTrue((self.old / "report.pdf").exists())

    def texts(self):
        return [p.read_bytes() for p in self.root.rglob("*") if p.is_file()]

    def test_a_move_never_replaces_a_file_the_apply_archives_earlier(self):
        """Archive runs before Moves, so a Move into today's _archive/ onto an archived file is refused."""
        today = datetime.now().strftime("%Y-%m-%d")
        bad = self.plan(PLAN_BODY.replace("`out/report.pdf`", f"`_archive/{today}/notes-backup.md`"))
        self.assertTrue(roost.plan_problems(self.old, bad))
        with self.assertRaises(ValueError):
            roost.apply_plan(self.old, bad, today=today)
        self.assertIn(b"Older notes.", self.texts())  # the backup keeps its content, wherever it is
        self.assertIn(b"%PDF-1.4 binary", self.texts())

    def test_a_move_never_replaces_the_first_handoff_the_apply_writes_earlier(self):
        """The First Handoff is written before Moves, so a Move onto its file in handoffs/ is refused."""
        stem = f"{datetime.fromtimestamp((self.old / 'HANDOFF.md').stat().st_mtime):%Y-%m-%d-%H%M}-adopted"
        bad = self.plan(PLAN_BODY.replace("`out/report.pdf`", f"`handoffs/{stem}.md`"))
        self.assertTrue(roost.plan_problems(self.old, bad))
        with self.assertRaises(ValueError):
            roost.apply_plan(self.old, bad)
        handoffs = [p for p in self.root.rglob(f"handoffs/{stem}.md")]
        for h in handoffs:  # if one was written, it is still the First Handoff
            self.assertIn("Packaged the driver", h.read_text(errors="replace"))
        self.assertIn(b"Start here.", self.texts())  # the hand-written handoff is still somewhere
        self.assertIn(b"%PDF-1.4 binary", self.texts())

    def test_one_file_in_two_steps_stops_the_plan(self):
        bad = self.plan(PLAN_BODY.replace("- `notes-backup.md`: an older copy", "- `export.csv`: an older copy"))
        self.assertIn("`export.csv` is in more than one step", " ".join(roost.plan_problems(self.old, bad)))
        with self.assertRaises(ValueError):
            roost.apply_plan(self.old, bad)
        self.assertTrue((self.old / "export.csv").exists())

    def test_handoffs_and_the_archive_are_never_archived(self):
        (self.old / "handoffs").mkdir()
        (self.old / "handoffs" / "old.md").write_text("A Handoff.")
        (self.old / "_archive" / "2026-01-01").mkdir(parents=True)
        (self.old / "_archive" / "2026-01-01" / "notes.md").write_text("Archived.")
        for rel in ("handoffs", "handoffs/old.md", "_archive", "_archive/2026-01-01/notes.md"):
            bad = self.plan(PLAN_BODY.replace("`notes-backup.md`", f"`{rel}`"))
            self.assertIn("never archived", " ".join(roost.plan_problems(self.old, bad)), rel)
            with self.assertRaises(ValueError):
                roost.apply_plan(self.old, bad)
        self.assertEqual((self.old / "handoffs" / "old.md").read_text(), "A Handoff.")
        self.assertEqual((self.old / "_archive" / "2026-01-01" / "notes.md").read_text(), "Archived.")

    def test_a_name_another_plan_takes_is_not_free(self):
        """In one roost adopt run, a name an earlier plan took can't be the rename or a split's name."""
        plan = self.plan()
        self.assertEqual(roost.plan_problems(self.old, plan, taken={"other-project"}), [])
        self.assertIn("Rename: printer-rollout already exists",
                      " ".join(roost.plan_problems(self.old, plan, taken={"printer-rollout"})))
        self.assertIn("Split: badge-printers isn't a free project name",
                      " ".join(roost.plan_problems(self.old, plan, taken={"badge-printers"})))

    def test_a_bad_status_or_a_first_handoff_without_next_stops_the_plan(self):
        bad = self.plan(PLAN_BODY.replace("## Status\nactive", "## Status\n**active** ✅"))
        self.assertTrue([p for p in roost.plan_problems(self.old, bad) if p.startswith("Status")])
        with self.assertRaises(ValueError):
            roost.apply_plan(self.old, bad)
        no_next = self.plan(PLAN_BODY.replace("### Next\n- Deploy to the second office\n", ""))
        self.assertIn("First Handoff has no ### Next", " ".join(roost.plan_problems(self.old, no_next)))
        with self.assertRaises(ValueError):
            roost.apply_plan(self.old, no_next)
        self.assertTrue((self.old / "HANDOFF.md").exists())  # nothing changed

    def test_secrets_never_reach_the_new_readme_or_the_first_handoff(self):
        key, value = "AKIAABCDEFGHIJKLMNOP", "s3cr3t-value"
        body = (PLAN_BODY.replace("- Packaged the driver", f"- Packaged the driver with {key} and token={value}")
                         .replace("Driver packaged;", f"Driver packaged with {key} and token={value};"))
        new = roost.apply_plan(self.old, self.plan(body), today="2026-10-09")
        [h] = (new / "handoffs").iterdir()
        self.assertIn("Packaged the driver with", h.read_text())
        self.assertIn("Driver packaged with", (new / "README.md").read_text())
        for p in [p for p in new.rglob("*") if p.is_file()]:
            self.assertNotIn(key, p.read_text(errors="replace"), p)
            self.assertNotIn(value, p.read_text(errors="replace"), p)

    def test_applying_moves_archives_and_renames_and_deletes_nothing(self):
        before = len(self.files(self.old))
        new = roost.apply_plan(self.old, self.plan(), today="2026-10-09")
        self.assertEqual(new, self.root / "printer-rollout")
        self.assertFalse(self.old.exists())
        self.assertTrue((new / "data" / "export.csv").exists())
        self.assertTrue((new / "out" / "report.pdf").exists())
        arch = new / "_archive" / "2026-10-09"
        self.assertEqual(sorted(p.name for p in arch.iterdir()), ["HANDOFF.md", "README.md", "notes-backup.md"])
        self.assertIn("Old notes.", (arch / "README.md").read_text())
        [h] = (new / "handoffs").iterdir()
        self.assertTrue(roost.handoff_time(h))
        self.assertIn("- Packaged the driver", h.read_text())
        text = (new / "README.md").read_text()
        meta = roost.frontmatter(text)
        self.assertEqual((meta["category"], meta["status"]), ("infrastructure", "active"))
        self.assertEqual(meta["description"], "Roll the new printer driver out to every office Mac.")
        self.assertEqual(meta["sessions"], [{"name": "Printer fix", "id": SID}])  # Sessions stay linked through their ids
        self.assertIn("Driver packaged", text)
        self.assertNotIn("Old notes.", text)
        split = self.root / "badge-printers"
        self.assertTrue((split / "plan.md").exists())
        self.assertIn("separate rollout", roost.frontmatter((split / "README.md").read_text())["description"])
        # every file is still somewhere: moved, archived, or split out (plus the new README copy and Handoff)
        self.assertEqual(len(self.files(new, split)), before + 3)

    def test_adopt_writes_a_description_line_and_no_summary_line(self):
        """The README had a legacy summary: line; the adopted one, and a split-out project's, have one description: line."""
        new = roost.apply_plan(self.old, self.plan(), today="2026-10-09")
        for folder in (new, self.root / "badge-printers"):
            front = (folder / "README.md").read_text().split("\n---\n", 1)[0]
            self.assertEqual(len(re.findall(r"(?m)^description\s*:", front)), 1, folder.name)
            self.assertNotRegex(front, r"(?m)^summary\s*:")
        self.assertEqual(roost.read_project(new)["summary"], "Roll the new printer driver out to every office Mac.")

    def test_adopting_a_readme_with_no_frontmatter_starts_one_with_a_description_line(self):
        (self.old / "README.md").write_text("# Printer rollout\n\nOld notes.\n")
        new = roost.apply_plan(self.old, self.plan(), today="2026-10-09")
        front = (new / "README.md").read_text().split("\n---\n", 1)[0]
        self.assertEqual(len(re.findall(r"(?m)^description\s*:", front)), 1)
        self.assertNotRegex(front, r"(?m)^summary\s*:")
        self.assertEqual(roost.read_project(new)["summary"], "Roll the new printer driver out to every office Mac.")

    def test_a_second_adopt_the_same_day_archives_beside_the_first(self):
        (self.old / "_archive" / "2026-10-09").mkdir(parents=True)
        (self.old / "_archive" / "2026-10-09" / "README.md").write_text("The first adopt's README.\n")
        new = roost.apply_plan(self.old, self.plan(), today="2026-10-09")
        self.assertEqual((new / "_archive" / "2026-10-09" / "README.md").read_text(), "The first adopt's README.\n")
        arch = new / "_archive" / "2026-10-09-2"
        self.assertIn("Old notes.", (arch / "README.md").read_text())
        self.assertTrue((arch / "HANDOFF.md").exists())
        [h] = (new / "handoffs").iterdir()
        self.assertIn("now in `_archive/2026-10-09-2/`", h.read_text())

    def test_backfill_writes_handoffs_oldest_first_dated_by_each_session(self):
        ran = []
        saved = roost.summarize
        roost.summarize = lambda sid, model, cap, readme=True, at=None: ran.append((sid, readme, at)) or (Path("/x/h.md"), [], 0.5, None)
        try:
            self.transcript(TRANSCRIPT)
            spent = roost.backfill(self.old, "claude-sonnet-5-5", 5.0, say=lambda *a: None)
        finally:
            roost.summarize = saved
        self.assertEqual(spent, 0.5)
        self.assertEqual([(sid, readme) for sid, readme, _ in ran], [(SID, False)])
        self.assertTrue(ran[0][2])

    def test_write_handoff_can_be_dated_by_when_the_session_worked(self):
        (self.old / "README.md").write_text("---\nsummary: Printers\nsessions: []\n---\n")
        path, _ = roost.write_handoff(SID, DRAFT, now=time.mktime((2026, 10, 9, 16, 5, 0, 0, 0, -1)),
                                      at=time.mktime((2026, 9, 1, 8, 30, 0, 0, 0, -1)))
        self.assertEqual(path.name, "2026-09-01-0830-11111111.md")

    def test_old_archives_are_suggested_for_clearing(self):
        arch = self.old / "_archive"
        now = time.mktime((2026, 10, 15, 12, 0, 0, 0, 0, -1))
        for d in ("2026-10-09", "2026-10-09-2", "2026-10-14", "notes"):
            (arch / d).mkdir(parents=True)
        self.assertEqual(roost.archives_due(self.old, now), [("2026-10-09", 6), ("2026-10-09-2", 6)])
        roost.save_config(adopt_archive_days=10)
        self.assertEqual(roost.archives_due(self.old, now), [])
        self.assertNotIn("_archive", roost.adopt_input(self.old))

    def run_adopt(self, answers, *argv):
        said, calls = [], []
        saved = roost.input if hasattr(roost, "input") else None, roost.claude_print, roost.sys.stdin
        roost.input = lambda prompt: said.append(prompt) or answers.pop(0)
        roost.claude_print = lambda model, system, prompt, budget: calls.append(prompt) or (PLAN_BODY, 0.3)
        roost.sys.stdin = type("TTY", (), {"isatty": lambda self: True})()
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                code = roost.run_command("adopt", list(argv))
        finally:
            if saved[0] is None:
                del roost.input
            else:
                roost.input = saved[0]
            roost.claude_print, roost.sys.stdin = saved[1], saved[2]
        return code, out.getvalue(), calls

    def test_adopt_asks_before_spending_and_before_applying(self):
        code, out, calls = self.run_adopt(["n"], "2026-09-printer-rollout")
        self.assertEqual((code, calls), (0, []))
        self.assertIn("Nothing ran", out)
        code, out, calls = self.run_adopt(["y", "s"], "2026-09-printer-rollout")
        self.assertEqual(len(calls), 1)
        self.assertTrue(self.old.exists())  # skipped: nothing changed
        self.assertTrue((roost.ADOPT_PLANS / "2026-09-printer-rollout.md").exists())
        code, out, calls = self.run_adopt(["y", "a"], "2026-09-printer-rollout")
        self.assertEqual(calls, [])  # the saved plan is reused
        self.assertIn("Applied", out)
        self.assertTrue((self.root / "printer-rollout" / "handoffs").is_dir())
        self.assertFalse((roost.ADOPT_PLANS / "2026-09-printer-rollout.md").exists())
        code, out, _ = self.run_adopt([])
        self.assertIn("project layout already", out)  # it and the project split out of it have handoffs/ now

    def test_adopt_from_a_projects_actions(self):
        """Plan in the background, read it in the editor, apply, then write its Handoffs: each step asks."""
        self.transcript(TRANSCRIPT)
        (self.root / "printers" / "README.md").write_text("---\nsummary: Other\nsessions: []\n---\n")  # one project lists it
        ui = Projects.ui(self)
        ui.folded, ui.refresh, ui.editor_name = set(), lambda: None, lambda: "micro"
        ui.GREEN = ui.YELLOW = ui.RED = 0
        edited, ran = [], []
        ui.edit = edited.append
        sync = type("Sync", (), {"__init__": lambda self, target, daemon: setattr(self, "run", target),
                                 "start": lambda self: self.run()})
        saved = roost.claude_print, roost.threading.Thread, roost.summarize
        roost.claude_print = lambda model, system, prompt, budget: (PLAN_BODY, 0.3)
        roost.threading.Thread = sync
        roost.summarize = lambda sid, model, cap, readme=True, at=None: ran.append((sid, readme)) or (Path("/x/h.md"), [], 0.5, None)
        self.addCleanup(lambda: (setattr(roost, "claude_print", saved[0]), setattr(roost.threading, "Thread", saved[1]),
                                 setattr(roost, "summarize", saved[2])))
        ui.psel = [r.item["id"] for r in ui.project_rows()].index("2026-09-printer-rollout")
        act(ui, ui.keys_projects, ui.project_rows(), "Adopt it")
        self.assertIn("Plan how to adopt", " ".join([ui.adopt_prompt(*ui.confirm)[0]] + ui.adopt_prompt(*ui.confirm)[1]))
        ui.keys_confirm("y")
        self.assertFalse((self.old / "handoffs").exists())  # planning changes nothing
        ui.report_summaries()
        self.assertEqual(ui.confirm[0], "adopt-review")
        ui.keys_confirm("enter")
        self.assertEqual(edited, [str(roost.ADOPT_PLANS / "2026-09-printer-rollout.md")])
        self.assertIn("renames the folder printer-rollout", " ".join([ui.adopt_prompt(*ui.confirm)[0]] + ui.adopt_prompt(*ui.confirm)[1]))
        ui.keys_confirm("y")
        self.assertTrue((self.root / "printer-rollout" / "handoffs").is_dir())
        self.assertEqual(ui.confirm and ui.confirm[0], "adopt-handoffs", ui.msg)
        self.assertIn("each of its 1 Session", " ".join([ui.adopt_prompt(*ui.confirm)[0]] + ui.adopt_prompt(*ui.confirm)[1]))
        ui.keys_confirm("y")
        ui.report_summaries()
        self.assertEqual(ran, [(SID, False)])
        self.assertIn("Wrote 1 Handoff for Printer rollout", ui.msg)  # by its README's heading
        ui.psel = [r.item["id"] for r in ui.project_rows()].index("printer-rollout")
        self.assertFalse(any(l.startswith("Adopt it") for l in labels(ui, ui.keys_projects, ui.project_rows())))

    # One roost adopt run: once a plan has been shown, its Rename and Split names are claimed for the rest of the
    # run, applied or skipped. Both plans are saved, so no model call runs; ask() gives the person's answers.

    LABELS_BODY = """## Summary
Label printers for the warehouse.
## Status
active
## Moves
None.
## Archive
None.
## Split out
{split}
## First Handoff
None.
## README
# Label printers

## Current state

Two printers ordered.
"""

    def second_folder(self, rename=None, split="None."):
        """A second folder to adopt in the same run, with its plan saved (and the first folder's plan saved too)."""
        other = self.root / "2026-08-label-printers"
        (other / "badges").mkdir(parents=True)
        (other / "README.md").write_text("---\nsummary: Labels\nsessions: []\n---\n\n# Label printers\n")
        (other / "badges" / "plan.md").write_text("Badges.")
        roost.ADOPT_PLANS.mkdir(parents=True, exist_ok=True)
        (roost.ADOPT_PLANS / f"{self.old.name}.md").write_text(roost.adopt_plan_text(self.old, PLAN_BODY))
        text = roost.adopt_plan_text(other, self.LABELS_BODY.format(split=split))
        if rename:
            text = text.replace("## Rename\n\nlabel-printers\n", f"## Rename\n\n{rename}\n")
        (roost.ADOPT_PLANS / f"{other.name}.md").write_text(text)
        self.assertEqual(roost.plan_problems(other, roost.parse_plan(text)), [])  # on its own, it could be applied
        return other

    def adopt_batch(self, answers, editor=None):
        """roost adopt on both folders, the first then the second, answering each ask() in turn ("q" once out).
        editor, if given, is what "e" does to the plan file. Returns the output."""
        import unittest.mock
        asked = []
        tty = type("TTY", (), {"isatty": lambda self: True})()
        adopt = sys.modules[roost.adopt_command.__module__]
        patches = [unittest.mock.patch.object(roost, "ask", lambda prompt: asked.append(prompt) or (answers.pop(0) if answers else "q")),
                   unittest.mock.patch.object(roost.sys, "stdin", tty),
                   unittest.mock.patch.object(roost, "claude_print", lambda *a: self.fail("a saved plan needs no model call"))]
        if editor:
            patches += [unittest.mock.patch.object(adopt, "open", lambda *a, **k: contextlib.nullcontext(), create=True),
                        unittest.mock.patch.object(roost.subprocess, "run", lambda cmd, **k: editor(Path(cmd[-1])))]
        out = io.StringIO()
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            with contextlib.redirect_stdout(out):
                roost.run_command("adopt", [self.old.name, "2026-08-label-printers"])
        return out.getvalue()

    @staticmethod
    def problems_shown(out, folder):
        """The ⚠ lines printed under folder's plan."""
        part = out.split(f"# Adopt plan for {folder}", 1)[1]
        return [l for l in part.splitlines() if l.strip().startswith("⚠")]

    def test_a_skipped_plans_rename_is_taken_for_the_rest_of_the_run(self):
        other = self.second_folder(rename="printer-rollout")  # what the first plan renames its folder to
        out = self.adopt_batch(["y", "s", "a", "s"])  # plan both · skip the first · apply the second · skip it
        self.assertTrue(self.old.is_dir())  # the first was skipped
        self.assertFalse((self.root / "printer-rollout").exists())  # the second wasn't applied under its name
        self.assertTrue(other.is_dir() and not (other / "handoffs").exists())
        self.assertTrue((roost.ADOPT_PLANS / f"{other.name}.md").exists())  # kept for next time
        self.assertTrue([l for l in self.problems_shown(out, other.name) if "printer-rollout" in l], out)

    def test_a_skipped_plans_split_name_is_taken_for_the_rest_of_the_run(self):
        other = self.second_folder(split="- `badges/` -> `badge-printers`: its own rollout")  # the first plan's Split name
        out = self.adopt_batch(["y", "s", "a", "s"])
        self.assertTrue(self.old.is_dir())
        self.assertFalse((self.root / "badge-printers").exists())  # not split out to it
        self.assertFalse((self.root / "label-printers").exists())  # nor applied at all
        self.assertTrue((other / "badges" / "plan.md").exists())
        self.assertTrue([l for l in self.problems_shown(out, other.name) if "badge-printers" in l], out)

    def test_a_plan_shown_again_after_an_edit_is_not_blocked_by_its_own_names(self):
        other = self.second_folder(split="- `badges/` -> `label-badges`: its own rollout")
        edited = []

        def edit(path):
            edited.append(path.name)
            path.write_text(path.read_text().replace("Two printers ordered.", "Two printers ordered and paid for."))
        out = self.adopt_batch(["y", "s", "e", "a"], editor=edit)  # skip the first · edit the second · apply it
        self.assertEqual(edited, [f"{other.name}.md"])
        self.assertEqual(self.problems_shown(out, other.name), [])  # neither showing names a problem
        new = self.root / "label-printers"
        self.assertTrue((new / "handoffs").is_dir(), out)  # applied, renamed to its own name
        self.assertTrue((self.root / "label-badges" / "plan.md").exists())  # and split out to its own name
        self.assertIn("paid for", (new / "README.md").read_text())  # the edited plan is the one applied

    def test_adopt_status_is_one_or_two_lowercase_words(self):
        status = lambda s: [p for p in roost.plan_problems(self.old, self.plan(PLAN_BODY.replace("## Status\nactive", f"## Status\n{s}")))
                            if p.startswith("Status")]
        for good in ("active", "on hold"):
            self.assertEqual(status(good), [], good)
        for bad in ("blocked on vendor", "**active**", "*on hold*", "`active`", "active ✅", "✅"):
            self.assertTrue(status(bad), bad)
        self.assertIn("one or two lowercase words", " ".join(status("blocked on vendor")))
        self.assertIn("one or two lowercase words", roost.ADOPT_PROMPT.split("## Status", 1)[1].split("##", 1)[0].lower())


if __name__ == "__main__":
    unittest.main()
