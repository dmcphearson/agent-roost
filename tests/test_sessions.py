from support import *  # noqa: F401,F403 (the shared namespace, helpers, and fixtures)
from support import _SCRATCH, own_tests  # noqa: F401
from test_projects import Projects, ProjectRows, SubProjects  # for their helpers; their tests run in test_projects.py
from test_screens import TextualScreens  # for its helpers; its tests run in test_screens.py

load_tests = own_tests(__name__)


class Sections(unittest.TestCase):
    def test_group_order_is_open_pinned_background_closed(self):
        rows = [
            session("closed", state="closed", kind=None),
            session("background"),
            session("pinned", pinned=True),
            session("interactive", kind="interactive"),
            session("pinned but open", pinned=True, attached=True),
        ]
        self.assertEqual([roost.group(s) for s in rows], [3, 2, 1, 0, 0])

    def test_running_view_hides_closed(self):
        rows = roost.visible([session("a"), session("b", state="closed", kind=None)], RUNNING, "")
        self.assertEqual([s["name"] for s in rows], ["a"])

    def test_sections_stay_in_order_whatever_the_sort(self):
        rows = [session("zeta", kind="interactive"), session("alpha"), session("mid", pinned=True)]
        for col in roost.SORTS:
            for desc in (False, True):
                got = roost.visible(rows, RUNNING, "", (col, desc))  # all three are running
                self.assertEqual([roost.group(s) for s in got], sorted(roost.group(s) for s in got), (col, desc))


class Sorting(unittest.TestCase):
    def test_default_is_most_recent_first(self):
        rows = roost.visible([session("old", mins=50), session("new", mins=1)], RUNNING, "")
        self.assertEqual([s["name"] for s in rows], ["new", "old"])

    def test_sort_by_context_descending(self):
        rows = [session("small", tokens=10), session("big", tokens=900)]
        got = roost.visible(rows, RUNNING, "", ("CONTEXT", True))
        self.assertEqual([s["name"] for s in got], ["big", "small"])

    def test_sort_by_name_ascending_ignores_case(self):
        rows = [session("bravo"), session("Alpha")]
        got = roost.visible(rows, RUNNING, "", ("NAME", False))
        self.assertEqual([s["name"] for s in got], ["Alpha", "bravo"])


class Views(unittest.TestCase):
    """The Sessions entry is a simple history: Running and Closed, and nothing else."""
    def test_the_views_are_running_then_closed(self):
        self.assertEqual(roost.VIEWS, ["Running", "Closed"])

    def test_every_session_is_in_exactly_one_view(self):
        """Running: every Session that isn't closed, and one roost is wrapping up (keep). Closed: every
        other closed Session, however long ago it closed and however big it is."""
        now = time.time()
        rows = [session("idle"), session("asks", state="waiting"), session("works", state="busy"),
                dict(session("wrapping", state="closed", kind=None), id="keep-0000"),
                session("just closed", state="closed", kind=None),
                session("closed weeks ago", state="closed", kind=None, mins=60 * 24 * 40),
                dict(session("big and cold", state="closed", kind=None, tokens=900_000), last=now - 9 * 86400)]
        keep = {"keep-0000"}
        running, closed = roost.VIEWS.index("Running"), roost.VIEWS.index("Closed")
        for s in rows:
            views = [roost.VIEWS[i] for i in range(len(roost.VIEWS)) if roost.in_view(s, i, keep)]
            want = "Closed" if s["state"] == "closed" and s["id"] not in keep else "Running"
            self.assertEqual(views, [want], s["name"])
        self.assertEqual({s["name"] for s in roost.visible(rows, running, "", keep=keep)}, {"idle", "asks", "works", "wrapping"})
        self.assertEqual({s["name"] for s in roost.visible(rows, closed, "", keep=keep)},
                         {"just closed", "closed weeks ago", "big and cold"})

    def test_closed_lists_newest_first_under_the_default_sort(self):
        rows = [session("middle", state="closed", kind=None, mins=60 * 24 * 3),
                session("newest", state="closed", kind=None, mins=5),
                session("oldest", state="closed", kind=None, mins=60 * 24 * 30), session("running", mins=1)]
        got = roost.visible(rows, roost.VIEWS.index("Closed"), "")
        self.assertEqual([s["name"] for s in got], ["newest", "middle", "oldest"])

    def starting_view(self, sessions):
        """The view setup() opens Sessions on, by its label."""
        import unittest.mock
        ui = object.__new__(roost.UI)
        with unittest.mock.patch.multiple(roost, read_config=lambda: {}):
            ui.setup(sessions, "")
        return roost.VIEWS[ui.view]

    def test_roost_opens_sessions_on_running_when_one_is_running_else_on_closed(self):
        closed = [session("gone", state="closed", kind=None), session("long gone", state="closed", kind=None, mins=60 * 24 * 20)]
        self.assertEqual(self.starting_view(closed + [session("live", state="waiting")]), "Running")
        self.assertEqual(self.starting_view(closed), "Closed")


class Search(unittest.TestCase):
    def test_all_words_must_match(self):
        s = session("Vendor security review", cwd="/x/security")
        self.assertTrue(roost.matches(s, "vendor review"))
        self.assertFalse(roost.matches(s, "vendor laptop"))


class Pins(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = roost.PINS, roost.JOBS
        roost.JOBS = Path(self.tmp.name)
        roost.PINS = roost.JOBS / "pins.json"

    def tearDown(self):
        roost.PINS, roost.JOBS = self.saved
        self.tmp.cleanup()

    def test_pin_and_unpin_round_trip(self):
        roost.set_pin("abc12345", True)
        roost.set_pin("abc12345", True)  # pinning twice doesn't duplicate
        self.assertEqual(json.loads(roost.PINS.read_text()), ["abc12345"])
        roost.set_pin("abc12345", False)
        self.assertEqual(json.loads(roost.PINS.read_text()), [])
        self.assertFalse(Path(str(roost.PINS) + ".lock").exists())

    def test_stale_lock_is_cleared(self):
        lock = Path(str(roost.PINS) + ".lock")
        lock.mkdir()
        old = time.time() - 60
        import os
        os.utime(lock, (old, old))
        roost.set_pin("abc12345", True)
        self.assertEqual(json.loads(roost.PINS.read_text()), ["abc12345"])


class Rename(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = roost.PROJECTS
        roost.PROJECTS = Path(self.tmp.name)
        self.sid = "1234abcd-0000-0000-0000-000000000000"
        d = roost.PROJECTS / "-proj"; d.mkdir()
        self.path = d / f"{self.sid}.jsonl"
        self.path.write_text(json.dumps({"type": "custom-title", "customTitle": "Old name", "sessionId": self.sid}) + "\n")

    def tearDown(self):
        roost.PROJECTS = self.saved
        self.tmp.cleanup()

    def closed(self):
        s = roost.parse(self.path)
        s.update(state="closed", kind=None, attach=None)
        return s

    def test_closed_rename_is_read_back(self):
        roost.rename_session(self.closed(), "  New   name ")
        self.assertEqual(roost.parse(self.path)["name"], "New name")

    def test_empty_name_refused(self):
        with self.assertRaises(RuntimeError):
            roost.rename_session(self.closed(), "   ")

    def test_open_window_refused(self):
        s = self.closed(); s.update(kind="interactive", state="idle")
        with self.assertRaises(RuntimeError):
            roost.rename_session(s, "x")
        self.assertEqual(roost.parse(self.path)["name"], "Old name")

    def test_busy_background_refused_without_restart(self):
        s = self.closed(); s.update(kind="background", state="busy", attach="1234abcd")
        called = []
        saved = roost.stop_session
        roost.stop_session = lambda job: called.append(job)
        try:
            with self.assertRaises(RuntimeError):
                roost.rename_session(s, "x")
        finally:
            roost.stop_session = saved
        self.assertEqual(called, [])


class Refresh(unittest.TestCase):
    def setUp(self):
        self.saved = (roost.load, roost.live_sessions, roost.read_pins)
        roost.read_pins = lambda: set()

    def tearDown(self):
        roost.load, roost.live_sessions, roost.read_pins = self.saved

    def test_regather_picks_up_new_transcript_data(self):
        a = dict(session("a"), ids=["a-id"], id="a-id", tokens=10, summary="old")
        roost.load = lambda days: [dict(a, tokens=99, summary="new")]
        roost.live_sessions = lambda: {"a-id": {"sessionId": "a-id", "status": "busy", "kind": "background", "id": "a-id"}}
        got = roost.regather([a])
        self.assertEqual((got[0]["tokens"], got[0]["summary"], got[0]["state"]), (99, "new", "busy"))

    def test_regather_keeps_opening_window(self):
        a = dict(session("a"), ids=["a-id"], id="a-id", opening=time.time())
        roost.load = lambda days: [dict(a, opening=None)]
        roost.live_sessions = lambda: {}
        got = roost.regather([a])
        self.assertEqual(got[0]["state"], "opening")

    def test_busy_after_new_turn_is_not_watching(self):
        # transcript now shows a fresh assistant turn (turn_done False): must read as working
        a = dict(session("a", mins=30), ids=["a-id"], id="a-id", turn_done=True)
        roost.load = lambda days: [dict(a, turn_done=False)]
        roost.live_sessions = lambda: {"a-id": {"sessionId": "a-id", "status": "busy", "kind": "background", "id": "a-id"}}
        self.assertEqual(roost.regather([a])[0]["state"], "busy")


class Retention(unittest.TestCase):
    DAY = 86400

    def closed(self, age_days, entrypoint="cli"):
        s = session("x", state="closed", kind=None)
        s.update(last=1_000_000_000 - age_days * self.DAY, entrypoint=entrypoint)
        return s

    def left(self, s, rules=(30, None)):
        return roost.days_left(s, rules, now=1_000_000_000)

    def test_counts_down_from_last_use(self):
        self.assertAlmostEqual(self.left(self.closed(18)), 12)

    def test_running_sessions_have_no_countdown(self):
        self.assertIsNone(self.left(session("x", state="idle")))

    def test_desktop_sessions_are_kept_unless_configured(self):
        self.assertEqual(self.left(self.closed(90, "claude-desktop")), float("inf"))
        self.assertAlmostEqual(self.left(self.closed(5, "claude-desktop"), (30, 7)), 2)

    def test_custom_period(self):
        self.assertAlmostEqual(self.left(self.closed(18), (60, None)), 42)

    def test_overdue_is_negative(self):
        self.assertLess(self.left(self.closed(31)), 0)

    def test_settings_priority_and_default(self):
        with tempfile.TemporaryDirectory() as d:
            managed, user = Path(d) / "managed.json", Path(d) / "user.json"
            saved = roost.RETENTION_SETTINGS
            roost.RETENTION_SETTINGS = [managed, user]
            try:
                self.assertEqual(roost.retention(), (30, None))
                user.write_text(json.dumps({"cleanupPeriodDays": 60}))
                self.assertEqual(roost.retention(), (60, None))
                managed.write_text(json.dumps({"cleanupPeriodDays": 14, "desktopSessionCleanupPeriodDays": 90}))
                self.assertEqual(roost.retention(), (14, 90))
            finally:
                roost.RETENTION_SETTINGS = saved

    def test_parse_reads_entrypoint(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "abc.jsonl"
            p.write_text(json.dumps({"type": "user", "entrypoint": "claude-desktop", "uuid": "u1"}, separators=(",", ":")) + "\n")
            self.assertEqual(roost.parse(p)["entrypoint"], "claude-desktop")

    def test_resumes_in_launch_folder_after_cd(self):
        with tempfile.TemporaryDirectory() as d:
            sub = Path(d) / "projects" / "x"
            sub.mkdir(parents=True)
            p = Path(d) / "abc.jsonl"
            recs = [{"type": "user", "cwd": d, "uuid": "u1"}, {"type": "user", "cwd": str(sub), "uuid": "u2"}]
            p.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in recs))
            s = roost.parse(p)
            self.assertEqual(s["cwd"], str(sub))
            self.assertEqual(roost.launch_dir(s), d)
            self.assertEqual(roost.launch_dir({"cwd": str(sub)}), str(sub))  # older index entries


class Governor(unittest.TestCase):
    NOW = 2_000_000_000

    def sess(self, **kw):
        s = {"tokens": 200_000, "state": "closed", "last": self.NOW - 600, "reply_at": self.NOW - 600, "ttl": 3600}
        s.update(kw)
        return s

    def test_cache_warm_then_cold(self):
        self.assertEqual(roost.cache_left(self.sess(), self.NOW), 3000)
        self.assertAlmostEqual(roost.next_cost(self.sess(), self.NOW), 0.1)
        cold = self.sess(reply_at=self.NOW - 4000)
        self.assertLess(roost.cache_left(cold, self.NOW), 0)
        self.assertEqual(roost.next_cost(cold, self.NOW), 2.0)
        self.assertEqual(roost.next_cost(self.sess(reply_at=self.NOW - 400, ttl=300), self.NOW), 1.25)

    def test_no_context_has_no_cache(self):
        self.assertIsNone(roost.cache_left(self.sess(tokens=0), self.NOW))
        self.assertIsNone(roost.next_cost(self.sess(tokens=0), self.NOW))

    def test_working_session_stays_warm(self):
        self.assertGreater(roost.cache_left(self.sess(state="busy", reply_at=self.NOW - 9999), self.NOW), 0)

    def test_stale_needs_big_idle_and_not_working(self):
        old = self.NOW - 3 * 86400
        self.assertTrue(roost.stale(self.sess(last=old), self.NOW))
        self.assertFalse(roost.stale(self.sess(last=old, tokens=100_000), self.NOW))
        self.assertFalse(roost.stale(self.sess(), self.NOW))
        self.assertFalse(roost.stale(self.sess(last=old, state="busy"), self.NOW))
        self.assertTrue(roost.stale(self.sess(last=old, state="idle"), self.NOW))  # pinned but untouched

    def test_a_session_being_summarized_stays_in_running(self):
        s = self.sess(id="abc", state="closed", last=time.time())  # stopped so its Handoff can be written
        running = roost.VIEWS.index("Running")
        self.assertFalse(roost.in_view(s, running))
        self.assertTrue(roost.in_view(s, running, keep={"abc"}))

    def test_a_session_being_summarized_sits_in_wrapping_up_after_closed(self):
        now = time.time()
        wrapping = self.sess(id="abc", state="closed", kind=None, last=now)  # stopped so its Handoff can be written
        bg = self.sess(id="bg", state="idle", kind="background", last=now - 60)
        closed = self.sess(id="old", state="closed", kind=None, last=now - 120)
        self.assertEqual(roost.SECTIONS[roost.group(wrapping, keep={"abc"})], "Wrapping up")
        self.assertEqual(roost.SECTIONS[roost.group(wrapping)], "Closed")  # once the run ends, it moves to Closed
        rows = roost.visible([wrapping, bg, closed], CLOSED, "", keep={"abc"})
        self.assertEqual([s["id"] for s in rows], ["old"])  # while roost wraps it up it counts as running, not closed
        self.assertEqual([s["id"] for s in roost.visible([wrapping, bg, closed], RUNNING, "", keep={"abc"})], ["bg", "abc"])

    def test_parse_reads_cache_lifetime_and_times(self):
        recs = [{"type": "user", "uuid": "u1", "timestamp": "2026-10-06T10:00:00.000Z"},
                {"type": "assistant", "timestamp": "2026-10-06T10:00:05.000Z",
                 "message": {"usage": {"input_tokens": 5, "cache_read_input_tokens": 1000,
                                       "cache_creation_input_tokens": 50,
                                       "cache_creation": {"ephemeral_1h_input_tokens": 50, "ephemeral_5m_input_tokens": 0}}}}]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "abc.jsonl"
            p.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in recs))
            s = roost.parse(p)
        self.assertEqual(s["ttl"], 3600)
        self.assertEqual(s["reply_at"] - s["started"], 5)
        self.assertEqual(s["tokens"], 1055)

    def test_parse_reads_a_compaction(self):
        reply = {"type": "assistant", "timestamp": "2026-10-06T10:00:05.000Z",
                 "message": {"usage": {"input_tokens": 5, "cache_read_input_tokens": 167_000}}}
        boundary = {"type": "system", "subtype": "compact_boundary", "timestamp": "2026-10-06T11:00:00.000Z",
                    "compactMetadata": {"trigger": "manual", "preTokens": 167_005, "postTokens": 20_760}}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "abc.jsonl"
            p.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in [reply, boundary]))
            s = roost.parse(p)
            self.assertEqual(s["tokens"], 20_760)  # its context is the summary now, not what it had
            self.assertTrue(s["compacted"])
            p.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in [reply, boundary, dict(reply, timestamp="2026-10-06T11:01:00.000Z")]))
            self.assertFalse(roost.parse(p)["compacted"])  # a reply since: back to its cache as usual

    def test_active_ignores_file_touches(self):
        s = self.sess(last=self.NOW, active=self.NOW - 3 * 86400)
        self.assertTrue(roost.stale(s, self.NOW))  # opened today, but nobody has written to it in 3 days
        self.assertEqual(roost.ago(s, self.NOW), "3d ago")
        self.assertEqual(roost.ago(self.sess(state="busy"), self.NOW), "now")
        self.assertEqual(roost.ago(self.sess(active=self.NOW - 20), self.NOW), "just now")

    def test_span(self):
        self.assertEqual([roost.span(x) for x in (30, 600, 7200, 3 * 86400)], ["1m", "10m", "2h", "3d"])

    def test_demo_has_cache_data(self):
        self.assertTrue(all(roost.cache_left(s) is not None for s in roost.demo_sessions() if s["tokens"]))


class StartingSessions(unittest.TestCase):
    """#49: brief, recording Sessions in sessions:, starting a Session from a project, and linking."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.saved = roost.CONFIG, roost.DONE, roost.RECAPS, roost.PLUGIN, roost.PROJECTS, roost.osa, roost.claude_settings, roost.NOTES
        roost.CONFIG, roost.DONE, roost.RECAPS, roost.NOTES = base / "config.json", base / "_done", base / "sessions", base / "notes"
        roost.claude_settings = lambda: {}  # not the person's real Claude Code settings
        roost.PLUGIN, roost.PROJECTS = base / "plugin", base / "transcripts"
        (roost.PLUGIN / ".claude-plugin").mkdir(parents=True)
        (roost.PLUGIN / ".claude-plugin" / "plugin.json").write_text("{}")
        self.root = base / "projects"
        self.root.mkdir()
        roost.save_config(projects_dir=str(self.root))
        self.scripts = []
        roost.osa = lambda script, *args: self.scripts.append(args) or "ok"

    def tearDown(self):
        roost.CONFIG, roost.DONE, roost.RECAPS, roost.PLUGIN, roost.PROJECTS, roost.osa, roost.claude_settings, roost.NOTES = self.saved
        self.tmp.cleanup()

    def project(self, name, text=README, handoffs=()):
        folder = self.root / name
        (folder / "handoffs").mkdir(parents=True)
        (folder / "README.md").write_text(text)
        for h in handoffs:
            (folder / "handoffs" / h).write_text(f"Handoff {h}")
        return folder

    def ui(self, term="ghostty"):
        ui = Projects.ui(self)
        ui.term, ui.tab, ui.helping = term, 0, False
        ui.sel, ui.query = 0, ""
        ui.refresh = ui.load_projects
        return ui

    # roost project brief

    def test_brief_prints_the_readme_and_the_newest_three_handoffs(self):
        names = [f"2026-10-0{d}-0900-aaaaaaaa.md" for d in range(1, 6)] + ["notes.md"]
        text = roost.brief(self.project("p", handoffs=names))
        self.assertIn("summary: \"Two password managers", text)
        self.assertIn("# Newest Handoffs (3 of 5, newest first)", text)
        shown = [n for n in names if f"## handoffs/{n}" in text]
        self.assertEqual(shown, names[2:5])
        self.assertLess(text.index("2026-10-05"), text.index("2026-10-03"))
        self.assertIn("2 older Handoffs are in", text)

    def test_brief_with_no_handoffs_and_for_a_note(self):
        self.assertIn("# No Handoffs yet", roost.brief(self.project("p")))
        roost.NOTES.mkdir()
        (roost.NOTES / "fix.md").write_text("---\ntags: [recap]\n---\n\nRestarted two Macs.")
        self.assertIn("Restarted two Macs.", roost.brief(roost.find_project("fix")))
        (roost.NOTES / "idea.md").write_text("---\ntags: [devices]\n---\n\nAn idea.")
        with self.assertRaises(ValueError):  # only a recap is found by its name; a note by its path
            roost.find_project("idea")
        with self.assertRaises(ValueError):
            roost.find_project("nope")
        with self.assertRaises(ValueError):
            roost.find_project("../p")

    def test_brief_command_records_the_session_once(self):
        folder = self.project("p")
        argv = ["brief", "p", "--session", "cc3d0000-1111"]
        self.assertEqual(roost.run_command("project", argv), 0)
        self.assertEqual(roost.run_command("project", argv), 0)
        meta = roost.frontmatter((folder / "README.md").read_text())
        self.assertEqual([x["id"] for x in meta["sessions"]], ["aa1b9a71-0000", "bb2c0000-0000", "cc3d0000-1111"])
        self.assertEqual(meta["sessions"][-1]["name"], "cc3d0000")  # no transcript yet: its short id

    def test_list_command(self):
        self.project("p")
        self.assertEqual(roost.run_command("project", ["list"]), 0)

    # sessions: list

    def test_link_session_changes_only_the_sessions_lines(self):
        before = README
        folder = self.project("p")
        self.assertTrue(roost.link_session(folder, "cc3d0000-1111", "Agent Roost"))
        after = (folder / "README.md").read_text()
        self.assertEqual(after.replace("  - name: Agent Roost\n    id: cc3d0000-1111\n", ""), before)
        self.assertFalse(roost.link_session(folder, "cc3d0000-1111", "Agent Roost"))

    def test_link_session_into_an_empty_or_missing_list(self):
        folder = self.project("a", "---\nsummary: s\nsessions: []\nstatus: active\n---\n\n# a\n")
        roost.link_session(folder, "id-1", "Odd: name # here")
        self.assertEqual((folder / "README.md").read_text(),
                         '---\nsummary: s\nsessions:\n  - name: "Odd: name # here"\n    id: id-1\nstatus: active\n---\n\n# a\n')
        self.assertEqual(roost.frontmatter((folder / "README.md").read_text())["sessions"][0]["name"], "Odd: name # here")
        folder = self.project("b", "---\nsummary: s\n---\nbody\n")
        roost.link_session(folder, "id-2", "Two")
        self.assertEqual((folder / "README.md").read_text(), "---\nsummary: s\nsessions:\n  - name: Two\n    id: id-2\n---\nbody\n")

    def test_link_session_refuses_a_flow_list_or_no_frontmatter(self):
        with self.assertRaises(ValueError):
            roost.link_session(self.project("a", "---\nsessions: [{id: x}]\n---\n"), "y", "Y")
        with self.assertRaises(ValueError):
            roost.link_session(self.project("b", "# no frontmatter\n"), "y", "Y")

    def test_unlink_and_assign_move_a_session_between_projects(self):
        a, b = self.project("a"), self.project("b", "---\nsummary: s\nsessions: []\n---\n")
        self.assertEqual(roost.assign_session("bb2c0000-0000", "Old", b), (True, ["a"]))
        self.assertEqual([x["id"] for x in roost.frontmatter((a / "README.md").read_text())["sessions"]], ["aa1b9a71-0000"])
        roost.unlink_session(b, "bb2c0000-0000")
        self.assertIn("sessions: []\n", (b / "README.md").read_text())
        self.assertFalse(roost.unlink_session(b, "bb2c0000-0000"))

    # starting a Session

    def test_start_opens_a_window_with_pickup_then_records_it(self):
        folder = self.project("p")
        msg = roost.start_session("p", str(folder), "ghostty")
        self.assertTrue(msg.startswith("Started a Session for p"))
        cwd, typed, direct = self.scripts[0]
        self.assertEqual(cwd, str(folder))
        self.assertIn("/roost:pickup p", direct)
        self.assertIn(f"--plugin-dir {roost.PLUGIN}", direct)
        self.assertNotIn("--resume", direct)
        sid = re.search(r"--session-id ([0-9a-f-]{36})", direct).group(1)
        meta = roost.frontmatter((folder / "README.md").read_text())
        self.assertEqual(meta["sessions"][-1], {"name": "p", "id": sid})

    def test_a_failed_window_records_nothing(self):
        folder = self.project("p")
        roost.osa = lambda *a: None
        self.assertIn("Couldn't open", roost.start_session("p", str(folder), "ghostty"))
        self.assertEqual(len(roost.frontmatter((folder / "README.md").read_text())["sessions"]), 2)

    def test_without_a_terminal_it_hands_back_what_to_run(self):
        folder = self.project("p")
        run = roost.start_session("p", str(folder), None)
        self.assertEqual((run["cwd"], run["start"][0], run["start"][-1]), (str(folder), "claude", "/roost:pickup p"))
        self.assertEqual(len(roost.frontmatter((folder / "README.md").read_text())["sessions"]), 3)

    # In an IDE's terminal, what would run here is copied instead, to paste in a new tab

    JETBRAINS_NOTE = "Copied the command: press ⌘T and paste it to open it in a new tab"
    VSCODE_NOTE = "Copied the command: open a new terminal (⌃⇧`) and paste it"

    def in_ide(self, ide, clipboard=None):
        """Patches for a test in an IDE's terminal (None: in no IDE), with the clipboard captured in
        self.copied, or clipboard in its place."""
        import unittest.mock
        self.copied = []
        return (unittest.mock.patch.object(roost.terminals, "ide_terminal", lambda env=None: ide),
                unittest.mock.patch.object(roost.UI, "clipboard", staticmethod(clipboard or self.copied.append)))

    def open_here(self, ui):
        """Enter on the Sessions tab's only Session, then enter on Open here, through dispatch."""
        # Unfiled is now view 1; find Sessions by name
        ui.tab, ui.view, ui.top, ui.quit_anyway = [t.name for t in ui.TABS].index("Sessions"), 0, 0, False
        ui.focus = "pane"  # the Sessions view focused, so enter goes to it
        ui.dispatch("enter", ui.sessions)
        opts = ui.choosing["options"]
        ui.choosing["sel"] = next(i for i, (l, _) in enumerate(opts) if l == "Open here")
        return ui.dispatch("enter", None)

    def test_in_jetbrains_open_here_copies_the_attach_line_and_keeps_roost_running(self):
        ui = self.ui(term=None)
        ui.sessions = [dict(session("Live"), attach="abcd1234")]
        ide, clip = self.in_ide("jetbrains")
        with ide, clip:
            self.assertIsNone(self.open_here(ui))
        self.assertEqual((self.copied, ui.msg), (["claude attach abcd1234"], self.JETBRAINS_NOTE))

    def test_in_vscode_start_a_session_copies_the_line_that_starts_it(self):
        self.project("p", handoffs=[time.strftime("%Y-%m-%d-%H%M") + "-aaaaaaaa.md"])
        repo = Path(self.tmp.name) / "my repo"  # a space: the folder comes out quoted
        repo.mkdir()
        ui = self.ui(term=None)
        ui.quit_anyway, ui.psel = False, 1
        ui.sessions = [dict(session("Mine", cwd=str(repo)), id="aa1b9a71-0000", home=str(repo))]
        ui.load_projects()
        ide, clip = self.in_ide("vscode")
        with ide, clip:
            ui.dispatch("enter", ui.project_rows())
            opts = ui.choosing["options"]
            ui.choosing["sel"] = next(i for i, (l, _) in enumerate(opts) if l.startswith("Start a Session"))
            ui.dispatch("enter", None)  # which folder: the project's, then the Session's
            ui.dispatch("down", None)
            self.assertIsNone(ui.dispatch("enter", None))
        self.assertEqual(ui.msg, self.VSCODE_NOTE)
        self.assertEqual(len(self.copied), 1)
        self.assertTrue(self.copied[0].startswith(f"cd '{repo}' && claude --session-id "), self.copied[0])
        self.assertTrue(self.copied[0].endswith(" '/roost:pickup p'"), self.copied[0])

    def test_in_an_ide_ctrl_n_new_session_copies_the_line_that_starts_it(self):
        import unittest.mock
        folder = Path(self.tmp.name) / "a repo"
        folder.mkdir()
        ui = self.ui(term=None)
        ui.quit_anyway = False
        ide, clip = self.in_ide("jetbrains")
        with ide, clip, unittest.mock.patch.object(roost, "plugin_args", lambda: []):
            ui.dispatch("ctrl+n", None)
            ui.dispatch("s", None)
            ui.asking["text"] = str(folder)
            self.assertIsNone(ui.dispatch("enter", None))
        self.assertEqual((self.copied, ui.msg), ([f"cd '{os.path.realpath(folder)}' && claude"], self.JETBRAINS_NOTE))

    def test_in_an_ide_a_failed_copy_says_so_and_keeps_roost_running(self):
        def broken(text):
            raise OSError("pbcopy is missing")
        ui = self.ui(term=None)
        ui.sessions = [dict(session("Live"), attach="abcd1234")]
        ide, clip = self.in_ide("vscode", clipboard=broken)
        with ide, clip:
            self.assertIsNone(self.open_here(ui))
        self.assertTrue(ui.msg.startswith("Couldn't copy"), ui.msg)

    def test_in_no_ide_open_here_hands_back_the_session_to_run_here(self):
        ui = self.ui(term=None)
        ui.sessions = [dict(session("Live"), attach="abcd1234")]
        ide, clip = self.in_ide(None)
        with ide, clip:
            self.assertIs(self.open_here(ui), ui.sessions[0])
        self.assertEqual(self.copied, [])

    def test_no_plugin_means_no_start(self):
        (roost.PLUGIN / ".claude-plugin" / "plugin.json").unlink()
        with self.assertRaises(ValueError):
            roost.start_session("p", str(self.project("p")), "ghostty")
        self.assertEqual(self.scripts, [])

    def test_s_starts_in_the_only_folder_or_asks_which(self):
        folder = self.project("p", handoffs=[time.strftime("%Y-%m-%d-%H%M") + "-aaaaaaaa.md"])
        ui = self.ui()
        ui.psel = 1
        act(ui, ui.keys_projects, ui.project_rows(), "Start a Session")
        self.assertEqual((len(self.scripts), ui.choosing), (1, None))
        repo = Path(self.tmp.name) / "repo"
        repo.mkdir()
        ui.sessions = [dict(session("Mine", cwd=str(repo)), id="aa1b9a71-0000", home=str(repo))]
        ui.load_projects()
        act(ui, ui.keys_projects, ui.project_rows(), "Start a Session")
        self.assertEqual([v for _, v in ui.choosing["options"]], [str(folder), str(repo)])
        ui.dispatch("down", None)
        ui.dispatch("enter", None)
        self.assertEqual((self.scripts[1][0], ui.choosing), (str(repo), None))

    def test_s_on_a_closed_project_starts_nothing(self):
        self.project("p", README.replace("status: active", "status: closed"))
        ui = self.ui()
        ui.folded.discard("Closed")
        rows = ui.project_rows()
        ui.psel = next(i for i, r in enumerate(rows) if r.kind == "project")
        self.assertNotIn("Start a Session", labels(ui, ui.keys_projects, rows))
        ui.start_for(rows[ui.psel].item)
        self.assertEqual((self.scripts, ui.msg), ([], "Title is closed"))

    def test_enter_on_a_closed_session_never_resumes(self):
        self.project("p", handoffs=[time.strftime("%Y-%m-%d-%H%M") + "-aaaaaaaa.md"])
        ui = self.ui()
        opened = []
        saved, roost.open_in_window = roost.open_in_window, lambda s, term: opened.append(s) or "opened"
        try:
            ui.sessions = [dict(session("Old", state="closed", kind=None), id="bb2c0000-0000")]
            ui.load_projects()
            acts = labels(ui, ui.keys_sessions, ui.sessions)  # on the Sessions tab: the Projects tab lists no Sessions
            self.assertEqual(acts[0], "Start a fresh Session for Title")  # by its README's heading
            self.assertFalse([a for a in acts if a.startswith(("Open", "Go to"))])
            # opening it directly is refused
            self.assertIsNone(ui.open_one(ui.sessions[0]))
            self.assertIn("fresh Session", ui.msg)
            self.assertEqual(opened, [])
        finally:
            roost.open_in_window = saved

    def test_a_session_open_in_claude_desktop_says_so_and_enter_goes_there(self):
        """Claude Desktop runs its sessions with its own copy of claude, which `claude agents` lists as
        interactive like a terminal's: roost tags it Desktop and Enter switches to the Desktop app."""
        import subprocess, unittest.mock
        ps = ("47108 /Users/x/Library/Application Support/Claude/claude-code/2.1.295/abc/claude.app/Contents/MacOS/claude --output-format stream-json\n"
              " 5720 claude\n")
        with unittest.mock.patch.object(roost.subprocess, "run",
                                        lambda *a, **kw: subprocess.CompletedProcess(a, 0, ps, "")):
            self.assertEqual(roost.desktop_pids([47108, 5720]), {47108})
        live = {"d-id": {"sessionId": "d-id", "kind": "interactive", "status": "idle", "pid": 47108, "desktop": True}}
        s = dict(session("Budget", state="closed", kind=None), id="d-id", ids=["d-id"])
        roost.attach_live([s], live)
        self.assertTrue(s["desktop"])
        ui = self.ui()
        ui.DIM, ui.status = 0, {}
        ui.sessions = [s]
        self.assertEqual(ui.status_cell(s)[0], "○ open in Desktop")
        self.assertEqual(ui.session_actions(s)[0][0], "Go to Claude Desktop")
        scripts = []
        with unittest.mock.patch.object(roost, "osa", lambda script, *a: scripts.append(script) or ""):
            self.assertEqual(roost.open_in_window(s, "ghostty"), "Switched to Claude Desktop, where Budget is open")
            ui.term = None  # no terminal set: still never resumed here, where Desktop already has it
            self.assertIsNone(ui.open_one(s))
        self.assertEqual(scripts, ['tell application "Claude" to activate'] * 2)

    def test_stop_and_delete_are_actions_that_ask_first(self):
        self.project("p", handoffs=[time.strftime("%Y-%m-%d-%H%M") + "-aaaaaaaa.md"])
        ui = self.ui()
        did = []
        saved = roost.stop_session, roost.delete_session
        roost.stop_session, roost.delete_session = (lambda job: did.append(("stop", job))), (lambda s: did.append(("delete", s["name"])))
        try:
            ui.sessions = [dict(session("Live"), id="aa1b9a71-0000"), dict(session("Old", state="closed", kind=None, mins=60), id="bb2c0000-0000")]
            ui.load_projects()
            rows = ui.project_rows()
            ui.psel = 1
            self.assertFalse([a for a in labels(ui, ui.keys_projects, rows)  # no Session Stop or Delete: the project's own Delete is allowed
                              if a.startswith(("Stop", "Delete")) and a != "Delete"])
            live, old = 0, 1  # on the Sessions tab: the Projects tab lists no Sessions
            ui.sel = live
            act(ui, ui.keys_sessions, ui.sessions, "Stop")
            self.assertTrue(ui.sessions_typing())
            ui.keys_confirm("n")  # any other key cancels
            self.assertEqual((did, ui.msg), ([], "Cancelled"))
            act(ui, ui.keys_sessions, ui.sessions, "Stop")
            ui.keys_confirm("y")
            ui.sel = old
            act(ui, ui.keys_sessions, ui.sessions, "Delete to the Trash")
            ui.keys_confirm("y")
            self.assertEqual(did, [("stop", ui.sessions[0]["attach"]), ("delete", "Old")])
        finally:
            roost.stop_session, roost.delete_session = saved

    def test_add_to_a_project_links_a_session_with_a_filtered_picker(self):
        self.project("alpha", "---\nsummary: s\nsessions: []\n---\n")
        b = self.project("beta", "---\nsummary: s\nsessions: []\n---\n")
        ui = self.ui()
        ui.sessions = [dict(session("Loose"), id="dd4e0000-0000")]
        act(ui, ui.keys_sessions, ui.sessions, "Add to a project")
        for k in names("bet\n"):
            ui.dispatch(k, None)
        self.assertEqual(ui.msg, "Added Loose to beta")
        self.assertEqual(roost.frontmatter((b / "README.md").read_text())["sessions"], [{"name": "Loose", "id": "dd4e0000-0000"}])
        act(ui, ui.keys_sessions, ui.sessions, "Move to another project")
        self.assertEqual([label for label, _ in ui.choosing["options"]], ["alpha"])
        for k in names("alp\n"):
            ui.dispatch(k, None)
        self.assertEqual(ui.msg, "Moved Loose from beta to alpha")

    def test_new_project_from_a_session_lists_it_and_offers_its_handoff(self):
        ui = self.ui()
        offered = []
        ui.ask_summary = offered.append
        ui.sessions = [dict(session("Printer Fix!"), id="dd4e0000-0000")]
        act(ui, ui.keys_sessions, ui.sessions, "New project from this Session")
        self.assertEqual(ui.asking["text"], "Printer Fix!")  # its name starts as the Session's
        typed(ui, "\n")
        self.assertEqual(ui.asking["text"], "printer-fix")  # then its folder, from the name
        typed(ui, "\n")
        self.assertEqual(ui.asking["text"], "Printer Fix!")  # then the summary
        typed(ui, "\x15Fix the printers.\n")
        meta = roost.frontmatter((self.root / "printer-fix" / "README.md").read_text())
        self.assertEqual((meta["description"], meta["sessions"]), ("Fix the printers.", [{"name": "Printer Fix!", "id": "dd4e0000-0000"}]))
        self.assertEqual(offered, ui.sessions)  # its first Handoff, after the cost is shown
        self.assertNotIn("New project from this Session", labels(ui, ui.keys_sessions, ui.sessions))
        # a Session open in a window writes its own Handoff with /wrap-up
        ui.sessions = [dict(session("Other", kind="interactive"), id="ee5f0000-0000")]
        act(ui, ui.keys_sessions, ui.sessions, "New project from this Session")
        typed(ui, "\n\n\n")
        self.assertIn("/wrap-up", ui.msg)
        self.assertEqual(len(offered), 1)

    def test_letters_do_nothing_on_either_tab(self):
        self.project("p", handoffs=[time.strftime("%Y-%m-%d-%H%M") + "-aaaaaaaa.md"])
        ui = self.ui()
        ui.sessions = [dict(session("Live"), id="aa1b9a71-0000"), session("Loose")]
        ui.load_projects()
        ui.psel, ui.view, ui.top = 1, 0, 0
        for k in "apnwyoXtcsrjkghl[]12\x18\x04":
            ui.keys_sessions(k, ui.sessions)
            ui.keys_projects(k, ui.project_rows())
        self.assertEqual((ui.choosing, ui.asking, ui.confirm, ui.msg, self.scripts), (None, None, None, "", []))
        self.assertEqual((ui.view, ui.expanded), (0, set()))

    def test_right_click_opens_the_actions_and_double_click_runs_the_first(self):
        ui = self.ui()
        opened = []
        saved, roost.open_in_window = roost.open_in_window, lambda s, term: opened.append(s["name"]) or "opened"
        try:
            ui.sessions, ui.row_y, ui.tabs, ui.col_x, ui.list_top = [session("Loose")], {6: 0}, [], [], 4
            ui.keys_sessions(("mouse", 10, 6, roost.curses.BUTTON3_PRESSED), ui.sessions)
            self.assertEqual(ui.choosing["title"], "Loose")
            ui.choosing = None
            ui.keys_sessions(("mouse", 10, 6, roost.curses.BUTTON1_DOUBLE_CLICKED), ui.sessions)
            self.assertEqual((opened, ui.choosing), (["Loose"], None))
        finally:
            roost.open_in_window = saved

    def test_the_wrap_up_skill_pipes_its_summary_to_roost_handoff(self):
        skill = (ROOT / "plugin" / "skills" / "wrap-up" / "SKILL.md").read_text()
        meta = roost.frontmatter(skill)
        self.assertEqual(meta["name"], "wrap-up")
        self.assertIn("Use when the person runs /wrap-up", meta["description"])
        self.assertIn("roost handoff --session ${CLAUDE_SESSION_ID} <<'ROOST_SUMMARY'", skill)
        for heading in ["What I did", "What worked", "What failed and why", "Decisions", "Open questions", "Next"]:
            self.assertIn(f"### {heading}", skill)
        self.assertIn("roost project status <id> <value>", skill)
        self.assertIn("roost project new <id>", skill)
        self.assertIn("Safe to /clear or exit.", skill)
        for cmd in ["handoff", "project"]:  # every command the skill names exists
            self.assertIn(cmd, roost.COMMANDS)

    def test_the_pickup_skill_runs_brief_with_this_sessions_id(self):
        skill = (ROOT / "plugin" / "skills" / "pickup" / "SKILL.md").read_text()
        meta = roost.frontmatter(skill)
        self.assertEqual(meta["name"], "pickup")
        self.assertIn("roost project brief", skill)
        self.assertIn("--session ${CLAUDE_SESSION_ID}", skill)
        self.assertIn("roost project list", skill)
        self.assertEqual(json.loads((ROOT / "plugin" / ".claude-plugin" / "plugin.json").read_text())["name"], "roost")


class SubProjectSessions(unittest.TestCase):
    """A Session listed in a sub-project belongs to it: its project, its menu, project: search, and
    Add to a project / Move to another project."""
    setUp, tearDown, project, ui = StartingSessions.setUp, StartingSessions.tearDown, StartingSessions.project, StartingSessions.ui
    sub = SubProjects.sub

    def fixture(self):
        """Project rollout (listing aa1b9a71-0000) with sub-project alpha (listing cc3d0000-1111), done gamma;
        project other. Sessions: the sub-project's, the parent's, and a loose one."""
        self.project("rollout", "---\nsummary: Rollout work.\nstatus: active\nsessions:\n  - name: Parent one\n"
                                "    id: aa1b9a71-0000\n---\n\n# Rollout\n")
        self.project("other", "---\nsummary: Other work.\nstatus: active\nsessions: []\n---\n")
        self.alpha = self.sub("rollout", "alpha", "feature", "active", "Badge printers.", [("Sub one", "cc3d0000-1111")])
        self.sub("rollout", "gamma", "research", "done", "Old survey.")
        ui = self.ui()
        ui.sessions = [dict(session("Sub one"), id="cc3d0000-1111"), dict(session("Parent one"), id="aa1b9a71-0000"),
                       dict(session("Loose"), id="dd4e0000-0000")]
        ui.load_projects()
        return ui

    def test_a_session_listed_in_a_sub_project_belongs_to_it(self):
        ui = self.fixture()
        p = ui.project_of(ui.sessions[0])
        self.assertIsNotNone(p)
        self.assertEqual(p["id"], "rollout/alpha")
        self.assertEqual(ui.project_of(ui.sessions[1])["id"], "rollout")
        ui.sel = 0
        acts = labels(ui, ui.keys_sessions, ui.sessions)
        self.assertTrue([l for l in acts if l.startswith("Write its Handoff from the transcript")], acts)
        self.assertFalse([l for l in acts if l.startswith("Write its Session recap")], acts)

    def test_project_search_matches_a_sub_projects_sessions_by_its_id_or_its_parents(self):
        ui = self.fixture()
        ui.view, ui.sort, ui.summaries, ui.update_note, ui.warning = RUNNING, ("LAST ACTIVE", True), {}, "", ""  # the fixture's are all running
        ui.DIM = ui.GREEN = ui.YELLOW = ui.RED = ui.CYAN = ui.ACCENT = 0
        ui.scr = type("Screen", (), {"getmaxyx": lambda self: (40, 120)})()  # draw_sessions only asks its size
        for q, want in [("project:rollout/alpha", {"cc3d0000-1111"}), ("project:rollout", {"cc3d0000-1111", "aa1b9a71-0000"})]:
            ui.query = q
            ui.draw_sessions()
            self.assertEqual({s["id"] for s in ui.srows}, want, q)

    def test_add_to_a_project_offers_open_sub_projects_and_lists_the_session_in_one(self):
        ui = self.fixture()
        ui.sel = 2  # Loose
        act(ui, ui.keys_sessions, ui.sessions, "Add to a project")
        opts = [l for l, _ in ui.choice_options()]
        for pid in ("rollout", "other", "rollout/alpha"):  # rollout is labeled by its heading, Rollout
            self.assertTrue([l for l in opts if pid in l.lower()], (pid, opts))
        self.assertFalse([l for l in opts if "gamma" in l], opts)  # done: not offered
        ui.choosing["sel"] = next(i for i, l in enumerate(opts) if "rollout/alpha" in l)
        ui.keys_choice("enter")
        listed = [x["id"] for x in roost.frontmatter((self.alpha / "README.md").read_text())["sessions"]]
        self.assertEqual(listed, ["cc3d0000-1111", "dd4e0000-0000"])

    def test_move_to_another_project_offers_open_sub_projects(self):
        ui = self.fixture()
        ui.sel = 1  # Parent one, in rollout
        act(ui, ui.keys_sessions, ui.sessions, "Move to another project")
        opts = [l for l, _ in ui.choice_options()]
        self.assertTrue([l for l in opts if "rollout/alpha" in l], opts)
        ui.choosing["sel"] = next(i for i, l in enumerate(opts) if "rollout/alpha" in l)
        ui.keys_choice("enter")
        listed = [x["id"] for x in roost.frontmatter((self.alpha / "README.md").read_text())["sessions"]]
        self.assertEqual(listed, ["cc3d0000-1111", "aa1b9a71-0000"])
        self.assertEqual(roost.frontmatter((self.root / "rollout" / "README.md").read_text())["sessions"], [])


class Dismissing(unittest.TestCase):
    """Spec 4: Dismiss and Undismiss on a closed Session, from the Sessions tab's actions. Temp
    folders only: config.DISMISSED, the projects, and the transcripts are all in a temp folder."""
    project, ui = StartingSessions.project, StartingSessions.ui

    def setUp(self):
        StartingSessions.setUp(self)
        self.addCleanup(StartingSessions.tearDown, self)
        patcher, self.persisted = dismissed_file(Path(self.tmp.name) / "dismissed.json")
        self.addCleanup(patcher.stop)

    def fixture(self):
        """Project p (its README lists aa1b9a71-0000 and bb2c0000-0000), a transcript for the closed
        one, and Sessions: Live (idle, in p), Asking (waiting), Old (closed, in p), Loose (closed, no project)."""
        self.folder = self.project("p", handoffs=[time.strftime("%Y-%m-%d-%H%M") + "-aaaaaaaa.md"])
        self.transcript = roost.PROJECTS / "-tmp" / "bb2c0000-0000.jsonl"
        self.transcript.parent.mkdir(parents=True)
        self.transcript.write_text('{"type": "user", "message": {"content": "hi"}}\n')
        self.sessions = [dict(session("Live"), id="aa1b9a71-0000"), dict(session("Asking", state="waiting"), id="cc3d0000-0000"),
                         dict(session("Old", state="closed", kind=None, mins=60), id="bb2c0000-0000"),
                         dict(session("Loose", state="closed", kind=None, mins=30), id="dd4e0000-0000")]
        return self.fresh()

    def fresh(self):
        """A new UI reading the same state: the same Sessions, projects, and config.DISMISSED."""
        ui = self.ui()
        ui.DIM, ui.status = 0, {}
        ui.sessions = [dict(s) for s in self.sessions]
        ui.load_projects()
        return ui

    def acts(self, ui, name):
        ui.sel = [s["name"] for s in ui.sessions].index(name)
        return labels(ui, ui.keys_sessions, ui.sessions)

    def pick(self, ui, name, label):
        self.assertIn(label, self.acts(ui, name), name)
        act(ui, ui.keys_sessions, ui.sessions, label)

    def status(self, ui, name):
        return ui.status_cell(next(s for s in ui.sessions if s["name"] == name))[0]

    def test_only_a_closed_session_offers_dismiss(self):
        ui = self.fixture()
        for name in ("Old", "Loose"):  # closed: in a project, and in none
            self.assertIn("Dismiss", self.acts(ui, name), name)
        busy = dict(session("Busy", state="busy"), id="ee5f0000-0000")
        ui.sessions.append(busy)
        for name in ("Live", "Asking", "Busy"):  # idle, needs input, working
            acts = self.acts(ui, name)
            self.assertFalse([l for l in acts if l.startswith(("Dismiss", "Undismiss"))], name)

    def test_dismissing_marks_it_and_persists_without_touching_its_files(self):
        ui = self.fixture()
        readme, transcript = (self.folder / "README.md").read_bytes(), self.transcript.read_bytes()
        stamps = (self.folder / "README.md").stat().st_mtime_ns, self.transcript.stat().st_mtime_ns
        self.assertNotIn("dismissed", self.status(ui, "Old"))
        self.pick(ui, "Old", "Dismiss")
        self.assertIn("dismissed", self.status(ui, "Old"))
        self.assertNotIn("dismissed", self.status(ui, "Loose"))
        self.assertIn("bb2c0000-0000", self.persisted())
        self.assertNotIn("dd4e0000-0000", self.persisted())
        again = self.fresh()  # a new UI reading the same state
        self.assertIn("dismissed", self.status(again, "Old"))
        acts = self.acts(again, "Old")
        self.assertIn("Undismiss", acts)
        self.assertNotIn("Dismiss", acts)
        self.assertEqual((self.folder / "README.md").read_bytes(), readme)  # still in its project's sessions:
        self.assertIn("bb2c0000-0000", roost.read_project(self.folder)["sessions"])
        self.assertEqual(self.transcript.read_bytes(), transcript)
        self.assertEqual(((self.folder / "README.md").stat().st_mtime_ns, self.transcript.stat().st_mtime_ns), stamps)

    def test_undismiss_takes_the_mark_and_the_saved_id_away(self):
        ui = self.fixture()
        self.pick(ui, "Old", "Dismiss")
        self.pick(ui, "Loose", "Dismiss")
        self.pick(ui, "Old", "Undismiss")
        self.assertNotIn("dismissed", self.status(ui, "Old"))
        self.assertIn("dismissed", self.status(ui, "Loose"))
        self.assertNotIn("bb2c0000-0000", self.persisted())
        self.assertIn("dd4e0000-0000", self.persisted())
        again = self.fresh()
        self.assertNotIn("dismissed", self.status(again, "Old"))
        acts = self.acts(again, "Old")
        self.assertIn("Dismiss", acts)
        self.assertNotIn("Undismiss", acts)


class SessionMenuGroups(unittest.TestCase):
    """A Session's actions, in groups split by dividers (| here): work on it, organize it, then stop or delete it."""
    project, ui, fresh, acts = StartingSessions.project, StartingSessions.ui, Dismissing.fresh, Dismissing.acts
    setUp = Dismissing.setUp

    def fixture(self):
        """Dismissing's: project p, Live (idle in the background, in p), and Loose (closed, no project);
        plus project other, so each can go to a project."""
        Dismissing.fixture(self)
        self.project("other", "---\nsummary: Other work.\nstatus: active\nsessions: []\n---\n")
        return self.fresh()

    def test_a_live_background_sessions_actions_in_groups(self):
        ui = self.fixture()
        self.assertEqual(self.acts(ui, "Live"),
                         ["Open in a new window", "Write its Handoff from the transcript", "Ask what to do with it",
                          "Pin: keep it running when idle", DIV,
                          "Move to another project", "Rename", "Copy its id", "Show its folder in Finder", DIV,
                          "Stop"])

    def test_a_closed_sessions_actions_in_groups(self):
        ui = self.fixture()
        self.assertEqual(self.acts(ui, "Loose"),
                         ["Open in a new window", "Write its Session recap from the transcript", "Ask what to do with it",
                          "Restart in the background, pinned", DIV,
                          "New project from this Session", "Add to a project", "Rename", "Dismiss", "Copy its id",
                          "Show its folder in Finder", DIV,
                          "Delete to the Trash"])


    def test_no_action_label_has_an_ellipsis(self):
        """A menu label is just its action's name: none ends in, or holds, "…". Rows: a live background
        Session in a project, and a closed Session in no project."""
        ui = self.fixture()
        for name in ("Live", "Loose"):
            real = [a for a in self.acts(ui, name) if a != DIV]
            self.assertTrue(real, name)
            self.assertEqual([a for a in real if "…" in a], [], name)

class SessionColumns(unittest.TestCase):
    """The Sessions tab's columns scale with the window, read off the drawn header and rows. NAME
    starts at column 5; then STATUS, LAST ACTIVE, CONTEXT, PROJECT."""
    setUp = TextualScreens.setUp
    proj = staticmethod(TextualScreens.proj)
    select = staticmethod(TextualScreens.select)
    run_on = ProjectRows.run_on
    TITLES = [("NAME", "NAME"), ("STATUS", "STAT"), ("LAST ACTIVE", "LAST"), ("CONTEXT", "CONT"), ("PROJECT", "PROJ")]

    def fleet(self, names, label="Printer fleet"):
        """Running Sessions with these names (the first idle, the second waiting, the rest idle), at
        5m, 2h, 3d... ago with 181k, 35k, 9k... of context; the first two are in a project called label."""
        tokens, mins = [181000, 35000, 9000, 120000, 4000], [5, 120, 3 * 1440, 30, 50]
        self.sessions = [session(n, state="waiting" if i == 1 else "idle", mins=mins[i % 5], tokens=tokens[i % 5])
                         for i, n in enumerate(names)]
        return [self.proj("p1", self.sessions[:2], label=label)]

    def drawn(self, projects, w, sort=None, config=None):
        """The Sessions view in a pane w wide (the window w + SIDEBAR_W): its lines from the list's left
        edge, the list's lines (header first), the list's width, and each Session's status text, LAST
        ACTIVE value and PROJECT label as roost works them out."""
        seen = {}

        async def look(pilot):
            ui = pilot.app.ui
            if sort:
                ui.sort = sort
            pilot.app.redraw()
            await pilot.pause()
            seen["list"] = pilot.app.query_one(roost.SessionList).region.width
            seen["x"] = pilot.app.query_one(roost.SessionList).region.x
            seen["cells"] = {s["name"]: (ui.status_cell(s)[0], roost.ago(s), s.get("proj") or "") for s in ui.srows}
        lines = self.run_on(projects, [open_view("Sessions"), look], size=(w + roost.SIDEBAR_W, 30), tab="Sessions", config=config).lines
        lines = [l[seen["x"]:] for l in lines]  # the list's part of each line
        h = next(i for i, l in enumerate(lines) if l.startswith("     NAME"))
        edge = next(i for i in range(h + 1, len(lines)) if lines[i].strip() and set(lines[i].strip()) == {"─"})
        return lines[h:edge], seen["list"], seen["cells"]

    def starts(self, header):
        """Where each drawn column title starts."""
        x, at = {}, 0
        for title, prefix in self.TITLES:
            i = header.find(prefix, at)
            if i >= 0:
                x[title], at = i, i + 1
        return x

    @staticmethod
    def row(listing, name):
        return next(l for l in listing[1:] if name[:12] in l)

    def whole(self, listing, cells, w_titles=("NAME", "STATUS", "LAST ACTIVE", "PROJECT")):
        """True when every Session's name, status, LAST ACTIVE and PROJECT text is drawn whole."""
        for name, (status, when, label) in cells.items():
            row = self.row(listing, name)
            want = {"NAME": name, "STATUS": status, "LAST ACTIVE": when, "PROJECT": label}
            if any(want[t] and want[t] not in row for t in w_titles):
                return False
        return True

    SHORT = ["Printer fix", "Laptop refresh", "Badge audit"]  # the longest is 14

    # 1. NAME is its longest name + 2, whatever name_width says

    def test_name_grows_with_its_longest_name_not_a_fixed_width(self):
        """Same window (200), same everything but the longest name, 12 then 20: NAME is 8 wider less
        its share of the 8 spare columns it took (spread over five columns), so 5 to 8 wider."""
        widths = {}
        for longest in ("Printer fixe", "Printer fix for desk"):  # 12, 20
            listing, _, _ = self.drawn(self.fleet([longest, "Laptop"]), 200)
            widths[len(longest)] = self.starts(listing[0])["STATUS"] - 5
        self.assertGreaterEqual(widths[20] - widths[12], 5, widths)
        self.assertLessEqual(widths[20] - widths[12], 8, widths)

    def test_sessions_name_ignores_the_name_width_setting(self):
        headers = {}
        for setting in (None, 20, 60):
            listing, _, _ = self.drawn(self.fleet(self.SHORT), 160, config={} if setting is None else {"name_width": setting})
            headers[setting] = listing[0]
        self.assertEqual(headers[20], headers[None], headers)
        self.assertEqual(headers[60], headers[None], headers)

    # 2. Whole values on a wide window

    def test_every_value_shows_whole_on_a_wide_window(self):
        long_name = "Quarterly laptop refresh and printer rollout"  # 44
        label = "Office printer fleet consolidation, phase two"  # 45
        listing, _, cells = self.drawn(self.fleet([long_name, "Laptop refresh", "Badge audit"], label=label), 220)
        self.assertTrue(cells[long_name][2], cells)  # it has its project
        for name, (status, when, proj) in cells.items():
            row = self.row(listing, name)
            for text in (name, status, when, proj):
                self.assertIn(text, row, (name, row))
            self.assertNotIn("…", row)

    # 3. Headers never run into each other

    def test_a_header_never_runs_into_the_next_one(self):
        """Every pair of drawn headers has a space between the first's text (its sort arrow included)
        and the second's start, at widths from 100 to 200, sorted on LAST ACTIVE, CONTEXT, STATUS."""
        projects = self.fleet(["Printer fix", "Laptop", "Badge audit for the front lobby doors"], label="Fleet")
        for sort in (("LAST ACTIVE", True), ("CONTEXT", True), ("STATUS", False), ("CONTEXT", False)):
            arrow = "▾" if sort[1] else "▴"
            for w in (100, 115, 130, 160, 200):
                head = self.drawn(projects, w, sort=sort)[0][0]
                x = self.starts(head)
                order = [t for t, _ in self.TITLES if t in x]
                if sort[0] in x and w >= 130:
                    self.assertTrue(head[x[sort[0]]:].startswith(f"{sort[0]} {arrow}"), (sort, w, head))
                for a, b in zip(order, order[1:]):
                    text = head[x[a]:x[b]].rstrip()
                    self.assertLess(x[a] + len(text), x[b], (sort, w, a, b, head))
                    self.assertTrue(text.startswith(a), (sort, w, a, head))

    # 4. Values start under their headers; CONTEXT is right-aligned under its word

    def test_each_value_starts_in_its_column(self):
        projects = self.fleet(self.SHORT + ["Toner order"])
        for w in (120, 200):
            listing, _, cells = self.drawn(projects, w)
            x = self.starts(listing[0])
            context_end = x["CONTEXT"] + len("CONTEXT")
            for name, (status, when, label) in cells.items():
                row = self.row(listing, name)
                self.assertEqual(row.find(status, x["STATUS"] - 2), x["STATUS"], (w, name, row))
                self.assertEqual(row.find(when, x["LAST ACTIVE"] - 2), x["LAST ACTIVE"], (w, name, row))
                s = next(q for q in self.sessions if q["name"] == name)
                ctx = roost.ktok(s["tokens"])
                self.assertEqual(row[context_end - len(ctx):context_end], ctx, (w, name, row))
                self.assertEqual(row[context_end - len(ctx) - 1], " ", (w, name, row))
                if label:
                    self.assertEqual(row.find(label, x["PROJECT"] - 2), x["PROJECT"], (w, name, row))

    def test_a_wrapped_up_sessions_dash_ends_under_context(self):
        """A wrapped-up Session's CONTEXT is "—", right-aligned under the word like a size."""
        old = dict(session("Old printer audit", state="closed", kind=None, mins=120), attach=None)
        self.sessions = self.sessions + [old]
        proj = self.proj("printer-fleet", [old], covered=[old])
        seen = {}

        async def where(pilot):
            seen["x"] = pilot.app.query_one(roost.SessionList).region.x
        lines = self.run_on([proj], [self.select(lambda ui: setattr(ui, "view", CLOSED)), where], size=(160, 30), tab="Sessions").lines
        lines = [l[seen["x"]:] for l in lines]  # the list's part of each line, right of the sidebar
        h = next(i for i, l in enumerate(lines) if l.startswith("     NAME"))
        x = self.starts(lines[h])
        row = next(l for l in lines[h + 1:] if "Old printer audit" in l)
        end = x["CONTEXT"] + len("CONTEXT")
        self.assertEqual(row[end - 1], "—", row)
        self.assertEqual(row[end - 2], " ", row)

    # 5. Spare width: NAME up to its longest + 2, then spread evenly

    def test_a_very_wide_windows_spare_width_is_spread_over_every_column(self):
        """At 240 with short values, each column is wider than its contents by about the same amount:
        no single column (PROJECT, last) takes the spare. A column's contents are its longest value or
        its title (with the sort arrow), whichever is longer, + 2; PROJECT runs to one column short of
        the list's right edge."""
        listing, width, cells = self.drawn(self.fleet(self.SHORT), 240)
        x = self.starts(listing[0])
        self.assertEqual(list(x), [t for t, _ in self.TITLES], listing[0])
        drawn = {"NAME": x["STATUS"] - 5, "STATUS": x["LAST ACTIVE"] - x["STATUS"],
                 "LAST ACTIVE": x["CONTEXT"] - x["LAST ACTIVE"], "CONTEXT": x["PROJECT"] - x["CONTEXT"],
                 "PROJECT": width - 1 - x["PROJECT"]}
        values = {"NAME": [n for n in cells], "STATUS": [c[0] for c in cells.values()],
                  "LAST ACTIVE": [c[1] for c in cells.values()] + ["LAST ACTIVE ▾"],
                  "CONTEXT": [roost.ktok(s["tokens"]) for s in self.sessions], "PROJECT": [c[2] for c in cells.values()]}
        need = {t: max(len(v) for v in values[t] + [t]) + 2 for t in drawn}
        spare = {t: drawn[t] - need[t] for t in drawn}
        for t in drawn:
            self.assertGreaterEqual(spare[t], 10, (t, spare, drawn))
        self.assertLessEqual(max(spare.values()) - min(spare.values()), 3, (spare, drawn))

    # 6. A narrow window: PROJECT gives way first, then CONTEXT, LAST ACTIVE, STATUS; NAME last

    def test_project_gives_way_first(self):
        """Find the narrowest window that shows every value whole; 3 columns narrower, only PROJECT
        is narrower: every other column starts where it did, and PROJECT is still drawn."""
        long_name = "Quarterly laptop refresh and badge audit"  # 40
        label = "Office printer fleet consolidation phase"  # 40
        projects = self.fleet([long_name, "Laptop refresh"], label=label)

        def fits(w):
            listing, _, cells = self.drawn(projects, w)
            return self.whole(listing, cells), listing
        lo, hi = 100, 240
        self.assertTrue(fits(hi)[0], "nothing shows whole even at 240")
        while lo < hi:
            mid = (lo + hi) // 2
            if fits(mid)[0]:
                hi = mid
            else:
                lo = mid + 1
        self.assertGreaterEqual(lo, 103, "the fixture needs longer values")
        full, narrow = fits(lo)[1], fits(lo - 3)[1]
        x, y = self.starts(full[0]), self.starts(narrow[0])
        self.assertIn("PROJECT", y, narrow[0])
        for t in ("NAME", "STATUS", "LAST ACTIVE", "CONTEXT", "PROJECT"):
            self.assertEqual(x[t], y[t], (t, full[0], narrow[0]))
        self.assertNotIn(label, self.row(narrow, long_name))
        self.assertIn(long_name, self.row(narrow, long_name))

    def test_then_context_then_last_active_and_name_keeps_its_width(self):
        """At 100 columns, a longer and longer name: PROJECT goes first, then CONTEXT, then LAST
        ACTIVE, and NAME stays its longest name + 2 while any column after it still has room."""
        for n, gone, kept in ((56, ["PROJECT"], ["STATUS", "LAST ACTIVE"]),
                              (66, ["PROJECT", "CONTEXT"], ["STATUS"]),
                              (80, ["PROJECT", "CONTEXT", "LAST ACTIVE"], [])):
            long_name = ("Quarterly laptop refresh and badge audit for the front lobby doors and the annex")[:n]
            listing, _, cells = self.drawn(self.fleet([long_name, "Laptop refresh"], label="Printer fleet"), 100)
            x = self.starts(listing[0])
            self.assertIn("STATUS", x, (n, listing[0]))
            self.assertGreaterEqual(x["STATUS"] - 5, n + 2, (n, listing[0]))  # NAME whole
            self.assertIn(long_name, self.row(listing, long_name), n)
            for t in gone:
                self.assertNotIn(t, x, (n, listing[0]))
            for t in kept:
                self.assertIn(t, listing[0], (n, listing[0]))
            for name, (status, when, label) in cells.items():
                row = self.row(listing, name)
                self.assertNotIn("Printer fleet", row, n)
                if "STATUS" in kept:
                    self.assertIn(status, row, (n, row))
                if "LAST ACTIVE" in kept:
                    self.assertIn(when, row, (n, row))


if __name__ == "__main__":
    unittest.main()
