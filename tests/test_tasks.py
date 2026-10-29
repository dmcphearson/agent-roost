from support import *  # noqa: F401,F403 (the shared namespace, helpers, and fixtures)
from support import own_tests  # noqa: F401
from test_projects import Projects, SubProjects  # for their helpers; their tests run in test_projects.py

load_tests = own_tests(__name__)

SESSION_A = "aaaa1111-2222-3333-4444-555555555555"
SESSION_B = "bbbb1111-2222-3333-4444-555555555555"
STATUSES = ("todo", "in-progress", "in-review", "done", "blocked")
OPEN_ORDER = ("in-progress", "blocked", "in-review", "todo")
STAMP = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d)\b")


def meta_of(text):
    """A task file's frontmatter as {key: value}: its plain `key: value` lines, quotes taken off."""
    lines = text.splitlines()
    assert lines and lines[0] == "---", f"no frontmatter: {text!r}"
    out = {}
    for line in lines[1:lines.index("---", 1)]:
        key, _, value = line.partition(":")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


def body_of(text):
    """What follows a task file's frontmatter."""
    lines = text.splitlines()
    return "\n".join(lines[lines.index("---", 1) + 1:])


class Tasks(unittest.TestCase):
    """roost task, on a temp Projects folder: printer-fleet, with a sub-project badge-printers, and quiet-one.
    A task is tasks/<n>-<slug>.md in its project's folder (or its sub-project's), its id <project>/<n> (or
    <project>/<slug>/<n>); frontmatter title, status, created, updated, and claimed_by only while claimed;
    its text, then ## Notes."""
    project = Projects.project
    sub = SubProjects.sub

    def setUp(self):
        SubProjects.setUp(self)
        self.fleet = self.project("printer-fleet")
        self.badge = self.sub("printer-fleet", "badge-printers")
        self.project("quiet-one")

    def tearDown(self):
        SubProjects.tearDown(self)

    def task(self, argv, stdin=""):
        """(exit code, stdout, stderr) of roost task <argv>, with stdin given; an argparse refusal counts as its exit code."""
        self.assertIn("task", roost.COMMANDS, "no roost task command")
        out, err = io.StringIO(), io.StringIO()
        saved = roost.sys.stdin
        roost.sys.stdin = io.StringIO(stdin)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    code = roost.run_command("task", argv)
                except SystemExit as e:
                    code = e.code
        finally:
            roost.sys.stdin = saved
        return code, out.getvalue(), err.getvalue()

    def ok(self, argv, stdin=""):
        code, out, err = self.task(argv, stdin)
        self.assertEqual(code, 0, (argv, out, err))
        return out

    def new(self, where, title, *text):
        """Make a task with roost task new; its id, as printed."""
        return self.ok(["new", where, title, *text]).strip()

    def refused(self, argv, action, *names, stdin=""):
        """roost task <argv> exits 1 with `roost task <action>: <reason>` on stderr, the reason naming each of names."""
        code, out, err = self.task(argv, stdin)
        self.assertEqual(code, 1, (argv, out, err))
        line = err.strip()
        self.assertTrue(line.startswith(f"roost task {action}: ") and len(line) > len(f"roost task {action}: "), line)
        for n in names:
            self.assertIn(n, line)
        return line

    def files(self, folder):
        return sorted(p.name for p in (folder / "tasks").glob("*.md")) if (folder / "tasks").is_dir() else []

    def file_of(self, folder, n):
        [path] = [p for p in (folder / "tasks").glob(f"{n}-*.md")]
        return path

    def age(self, path):
        """Put an old updated: in the task's file, so a change that sets it shows."""
        path.write_text(re.sub(r"(?m)^updated:.*$", "updated: 2001-01-01", path.read_text(), count=1))
        self.assertEqual(meta_of(path.read_text())["updated"], "2001-01-01", "the fixture: updated is old")

    # 1. Files and ids

    def test_new_makes_the_first_task_file_and_prints_its_id(self):
        self.assertEqual(self.new("printer-fleet", "Fix the printer"), "printer-fleet/1")
        self.assertEqual(self.files(self.fleet), ["1-fix-the-printer.md"])

    def test_numbers_are_one_more_than_the_highest_in_the_folder(self):
        self.assertEqual(self.new("printer-fleet", "First one"), "printer-fleet/1")
        self.assertEqual(self.new("printer-fleet", "Second one"), "printer-fleet/2")
        (self.fleet / "tasks" / "5-made-by-hand.md").write_text((self.fleet / "tasks" / "2-second-one.md").read_text())
        self.assertEqual(self.new("printer-fleet", "After the gap"), "printer-fleet/6")
        self.assertEqual(self.new("quiet-one", "Its own numbers"), "quiet-one/1", "numbered within each folder")

    def test_a_sub_projects_task_is_in_its_own_tasks_folder_with_a_three_part_id(self):
        self.assertEqual(self.new("printer-fleet", "Parent task"), "printer-fleet/1")
        self.assertEqual(self.new("printer-fleet/badge-printers", "Badge task"), "printer-fleet/badge-printers/1")
        self.assertEqual(self.files(self.badge), ["1-badge-task.md"])
        self.assertEqual(self.files(self.fleet), ["1-parent-task.md"])
        self.assertIn("title: Badge task", self.ok(["show", "printer-fleet/badge-printers/1"]))

    def test_the_slug_is_lowercase_letters_digits_and_hyphens_at_most_40(self):
        self.new("printer-fleet", "Replace the 3 oldest printers: Floor 2 & Floor 4, before the quarter closes!")
        [name] = self.files(self.fleet)
        slug = re.fullmatch(r"1-(.*)\.md", name).group(1)
        self.assertRegex(slug, r"^[a-z0-9-]{1,40}$")
        self.assertTrue(slug.startswith("replace-the-3-oldest"), slug)

    def test_an_unknown_project_is_refused_and_writes_nothing(self):
        before = self.tree()
        self.refused(["new", "no-such-project", "A task"], "new")
        self.refused(["new", "printer-fleet/no-such-sub", "A task"], "new")
        self.assertEqual(self.tree(), before)

    tree = SubProjects.tree

    # 2. Frontmatter and body

    def test_a_new_task_is_todo_unclaimed_with_its_text_then_notes(self):
        tid = self.new("printer-fleet", "Fix the printer", "Swap", "the", "fuser.")
        text = self.file_of(self.fleet, 1).read_text()
        meta = meta_of(text)
        self.assertEqual((meta["title"], meta["status"]), ("Fix the printer", "todo"))
        self.assertTrue(meta["created"] and meta["updated"], meta)
        self.assertNotIn("claimed_by", meta, "claimed_by only while claimed")
        self.assertEqual(set(meta), {"title", "status", "created", "updated"})
        body = body_of(text)
        self.assertIn("Swap the fuser.", body)
        self.assertRegex(body, r"(?m)^## Notes\s*$")
        self.assertLess(body.index("Swap the fuser."), body.index("## Notes"), "the text, then ## Notes")
        self.assertEqual(tid, "printer-fleet/1")

    def test_the_text_may_come_on_stdin(self):
        self.ok(["new", "printer-fleet", "Piped task"], stdin="From stdin.\n")
        self.assertIn("From stdin.", body_of(self.file_of(self.fleet, 1).read_text()))

    # 3 and 9. Statuses

    def test_status_prints_the_status_and_sets_each_allowed_one(self):
        tid = self.new("printer-fleet", "A task")
        self.assertEqual(self.ok(["status", tid]).strip(), "todo")
        for value in STATUSES:
            with self.subTest(status=value):
                self.ok(["status", tid, value])
                self.assertEqual(meta_of(self.file_of(self.fleet, 1).read_text())["status"], value)
                self.assertEqual(self.ok(["status", tid]).strip(), value)

    def test_any_other_status_is_refused_and_leaves_the_task(self):
        tid = self.new("printer-fleet", "A task")
        before = self.file_of(self.fleet, 1).read_text()
        for value in ("closed", "Done", "in progress", "wip"):
            with self.subTest(status=value):
                self.refused(["status", tid, value], "status")
                self.assertEqual(self.file_of(self.fleet, 1).read_text(), before)

    def test_setting_todo_releases_the_claim_and_other_statuses_keep_it(self):
        tid = self.new("printer-fleet", "A task")
        self.ok(["claim", tid, "--session", SESSION_A])
        for value in ("in-review", "blocked", "done", "in-progress"):
            with self.subTest(status=value):
                self.ok(["status", tid, value])
                self.assertEqual(meta_of(self.file_of(self.fleet, 1).read_text()).get("claimed_by"), SESSION_A)
        self.ok(["status", tid, "todo"])
        self.assertNotIn("claimed_by", meta_of(self.file_of(self.fleet, 1).read_text()))
        self.ok(["claim", tid, "--session", SESSION_B])  # released: another Session may claim it
        self.assertEqual(meta_of(self.file_of(self.fleet, 1).read_text())["claimed_by"], SESSION_B)

    # 6. Show

    def test_show_prints_the_tasks_file(self):
        tid = self.new("printer-fleet", "Fix the printer", "Swap the fuser.")
        self.assertEqual(self.ok(["show", tid]).rstrip("\n"), self.file_of(self.fleet, 1).read_text().rstrip("\n"))

    def test_an_unknown_task_is_refused(self):
        self.new("printer-fleet", "A task")
        for action, extra in (("show", []), ("status", []), ("claim", ["--session", SESSION_A]),
                              ("note", ["text", "--session", SESSION_A])):
            with self.subTest(action=action):
                self.refused([action, "printer-fleet/9", *extra], action)
                self.refused([action, "no-such-project/1", *extra], action)

    # 7. Claim

    def test_claim_sets_claimed_by_and_moves_it_to_in_progress(self):
        tid = self.new("printer-fleet", "A task")
        self.ok(["claim", tid, "--session", SESSION_A])
        meta = meta_of(self.file_of(self.fleet, 1).read_text())
        self.assertEqual((meta["claimed_by"], meta["status"]), (SESSION_A, "in-progress"))

    def test_the_same_session_may_claim_again(self):
        tid = self.new("printer-fleet", "A task")
        self.ok(["claim", tid, "--session", SESSION_A])
        self.ok(["claim", tid, "--session", SESSION_A])
        self.assertEqual(meta_of(self.file_of(self.fleet, 1).read_text())["claimed_by"], SESSION_A)

    def test_claiming_a_task_another_session_holds_is_refused_naming_it(self):
        tid = self.new("printer-fleet", "A task")
        self.ok(["claim", tid, "--session", SESSION_A])
        before = self.file_of(self.fleet, 1).read_text()
        self.refused(["claim", tid, "--session", SESSION_B], "claim", SESSION_A)
        self.assertEqual(self.file_of(self.fleet, 1).read_text(), before)

    # 8. Notes

    def test_a_note_is_appended_under_notes_dated_with_the_session_and_text(self):
        tid = self.new("printer-fleet", "A task", "The task's text.")
        self.ok(["note", tid, "First finding.", "--session", SESSION_A])
        self.ok(["note", tid, "--session", SESSION_B], stdin="Second, from stdin.\n")
        body = body_of(self.file_of(self.fleet, 1).read_text())
        notes = body[body.index("## Notes"):]
        self.assertLess(body.index("The task's text."), body.index("## Notes"))
        lines = [l for l in notes.splitlines()[1:] if l.strip()]
        self.assertEqual(len(lines), 2, notes)
        for line, sid, text in zip(lines, (SESSION_A, SESSION_B), ("First finding.", "Second, from stdin.")):
            with self.subTest(note=text):
                stamp = STAMP.match(line.lstrip("-* "))
                self.assertIsNotNone(stamp, f"the note starts with YYYY-MM-DD HH:MM: {line!r}")
                when = datetime.strptime(stamp.group(1), "%Y-%m-%d %H:%M")
                self.assertLess(abs((datetime.now() - when).total_seconds()), 180, "the local date and time now")
                self.assertIn(sid, line)
                self.assertIn(text, line)
                self.assertLess(line.index(sid), line.index(text), "the Session, then the text")

    # 10. Every change updates updated

    def test_every_change_updates_updated(self):
        tid = self.new("printer-fleet", "A task")
        path = self.file_of(self.fleet, 1)
        for argv in (["claim", tid, "--session", SESSION_A], ["note", tid, "A note.", "--session", SESSION_A],
                     ["status", tid, "in-review"], ["status", tid, "todo"]):
            with self.subTest(change=argv[0]):
                self.age(path)
                self.ok(argv)
                self.assertNotEqual(meta_of(path.read_text())["updated"], "2001-01-01", argv)

    # 5. List

    def listed(self, out):
        """Each task id in list's output, with its status (the last status named on a line above it) and its line."""
        got, status = {}, None
        for line in out.splitlines():
            ids = re.findall(r"printer-fleet/\d+", line)
            if not ids:
                named = [s for s in STATUSES if re.search(rf"(^|[^\w-]){re.escape(s)}([^\w-]|$)", line)]
                if named:
                    status = named[0]
                continue
            got[ids[0]] = (status, line)
        return got

    def test_list_groups_the_open_tasks_by_status_in_order_with_their_titles_and_claims(self):
        ids = {}
        for title, status in (("Waiting on a quote", "todo"), ("Swap the fuser", "in-progress"), ("Vendor reply", "blocked"),
                              ("Driver review", "in-review"), ("Old cleanup", "done"), ("Second todo", "todo")):
            tid = self.new("printer-fleet", title)
            if status == "in-progress":
                self.ok(["claim", tid, "--session", SESSION_A])
            elif status != "todo":
                self.ok(["status", tid, status])
            ids[title] = (tid, status)
        out = self.ok(["list", "printer-fleet"])
        got = self.listed(out)
        want = {tid: status for tid, status in ids.values() if status != "done"}
        self.assertEqual({tid: s for tid, (s, _) in got.items()}, want, out)
        order = [got[tid][0] for tid in got]  # in the order list printed them
        self.assertEqual([s for s in OPEN_ORDER if s in order], list(dict.fromkeys(order)), f"the groups in order:\n{out}")
        for title, (tid, status) in ids.items():
            if status != "done":
                self.assertIn(title, got[tid][1])
        self.assertIn(SESSION_A, got[ids["Swap the fuser"][0]][1], "the Session holding it, its full id")
        self.assertNotIn(SESSION_A, got[ids["Waiting on a quote"][0]][1])
        self.assertNotIn("Old cleanup", out, "done tasks are left out")

    def test_list_all_adds_the_done_tasks(self):
        done = self.new("printer-fleet", "Old cleanup")
        self.ok(["status", done, "done"])
        self.new("printer-fleet", "Still open")
        got = self.listed(self.ok(["list", "printer-fleet", "--all"]))
        self.assertEqual(got[done][0], "done")
        self.assertIn("Old cleanup", got[done][1])
        self.assertIn("printer-fleet/2", got)

    def test_list_of_a_sub_project_lists_its_own_tasks(self):
        self.new("printer-fleet", "Parent task")
        self.new("printer-fleet/badge-printers", "Badge task")
        out = self.ok(["list", "printer-fleet/badge-printers"])
        self.assertIn("printer-fleet/badge-printers/1", out)
        self.assertIn("Badge task", out)
        self.assertNotIn("Parent task", out)

    def test_list_of_an_unknown_project_is_refused(self):
        self.refused(["list", "no-such-project"], "list")

    # 11. Help

    def test_roost_help_lists_the_task_command(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            importlib.import_module("roost.cli").main(["--help"])
        self.assertRegex(out.getvalue(), r"(?m)^\s+task\s+\S")


if __name__ == "__main__":
    unittest.main()
