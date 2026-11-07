from support import *  # noqa: F401,F403 (the shared namespace, helpers, and fixtures)
from support import _SCRATCH, own_tests  # noqa: F401
import shlex
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
    def test_the_views_are_running_then_closed_then_unfiled(self):
        self.assertEqual(roost.VIEWS, ["Running", "Closed", "Unfiled"])

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

    def test_managed_settings_come_from_this_computers_path_ahead_of_the_users(self):
        """RETENTION_SETTINGS (built when roost starts) is this computer's managed settings,
        system.managed_settings() (on Linux, /etc/claude-code/managed-settings.json), then the user's
        ~/.claude/settings.json. The real platform is asked here, not the tests' macOS default, because
        the list was built on it; test_settings_priority_and_default pins the order's effect."""
        import importlib, sys, unittest.mock
        system = importlib.import_module("roost.core.system")
        with unittest.mock.patch.object(system.sys, "platform", sys.platform):
            here = system.managed_settings()
        self.assertEqual(roost.RETENTION_SETTINGS, [here, Path.home() / ".claude" / "settings.json"])

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
        ui.quit_anyway, ui.psel = False, 0  # the project: no header above it
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
        ui.psel = 0  # the project: no header above it
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
        ui.pquery = "title"  # closed: only a search lists it in the sidebar
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
            self.assertEqual(acts[0], "Write its Handoff from the transcript")  # no Handoff covers it: saving it comes first
            self.assertIn("Start a fresh Session for Title", acts)  # by its README's heading
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
            ui.psel = 0  # the project: no header above it
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

    def test_the_wrap_up_skill_passes_a_name_it_is_given_as_the_handoffs_title(self):
        """/wrap-up <name> names the Handoff: the skill reads its arguments and passes them with --title."""
        skill = (ROOT / "plugin" / "skills" / "wrap-up" / "SKILL.md").read_text()
        self.assertTrue("$ARGUMENTS" in skill, "wrap-up/SKILL.md never reads $ARGUMENTS")
        self.assertTrue([l for l in skill.splitlines() if "roost handoff" in l and "--title" in l],
                        "wrap-up/SKILL.md has no roost handoff command with --title")
        self.assertIn("roost handoff --session ${CLAUDE_SESSION_ID} <<'ROOST_SUMMARY'", skill)  # no name: no --title

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

class ClosedProjectSessionActions(unittest.TestCase):
    """A closed Session an open project lists and no Handoff covers: its actions start with writing its
    Handoff, then resuming it (with what re-reading its context costs), then a fresh Session for the project.
    Resume reopens it the way open_one does; a plain open stays refused. A wrapped-up one gets no Resume."""
    project, ui, fresh, acts = StartingSessions.project, StartingSessions.ui, Dismissing.fresh, Dismissing.acts
    setUp = Dismissing.setUp
    RESUME = "Resume this Session"

    def fixture(self, **old):
        """Project p (Title) listing Wrapped (aa1b9a71-0000, closed two hours ago, a Handoff covers it to its
        last message) and Old (bb2c0000-0000, closed two hours ago, 412,000 tokens, no Handoff covers it);
        old's keys go into Old."""
        from datetime import datetime, timezone
        folder = self.project("p", handoffs=[time.strftime("%Y-%m-%d-%H%M") + "-aaaaaaaa.md"])
        wrapped = dict(session("Wrapped", state="closed", kind=None, mins=120, tokens=412_000), id="aa1b9a71-0000")
        to = datetime.fromtimestamp(wrapped["last"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        (folder / "handoffs" / (time.strftime("%Y-%m-%d-%H%M") + "-aa1b9a71.md")).write_text(
            f"---\nsession: aa1b9a71-0000\nname: Covered\nto: {to}\n---\n\nDone.\n")
        self.sessions = [wrapped, dict(session("Old", state="closed", kind=None, mins=120, tokens=412_000), id="bb2c0000-0000", **old)]
        ui = self.fresh()
        self.assertEqual([ui.wrapped(s) for s in ui.sessions], [True, False])  # the fixture
        return ui

    def real(self, acts):
        return [a for a in acts if a != DIV]

    def assert_resumable(self, ui, name):
        """name's actions offer Resume; leaves it the selected row."""
        acts = self.acts(ui, name)
        self.assertTrue([a for a in acts if a != DIV and a.startswith(self.RESUME)], f"no Resume action: {acts}")

    def test_handoff_first_then_resume_then_a_fresh_session(self):
        acts = self.real(self.acts(self.fixture(), "Old"))
        self.assertEqual(acts[0], "Write its Handoff from the transcript", acts)
        self.assertEqual(acts[1], "Resume this Session · 412k tokens, about 2× to re-read (824k tokens' worth)", acts)
        self.assertIn("Start a fresh Session for Title", acts)
        self.assertGreater(acts.index("Start a fresh Session for Title"), 1, acts)
        self.assertEqual(sum(a.startswith(self.RESUME) for a in acts), 1, acts)

    def test_resume_says_what_re_reading_costs_on_each_cache(self):
        ui = self.fixture()
        base = dict(ui.sessions[1])
        for keys, want in (({}, "about 2× to re-read (824k tokens' worth)"),  # no ttl: the 1-hour cache, cold
                           ({"ttl": 3600}, "about 2× to re-read (824k tokens' worth)"),
                           ({"ttl": 300}, "about 1.25× to re-read (515k tokens' worth)"),
                           ({"reply_at": time.time()}, "about 0.1× to re-read (41k tokens' worth)")):  # still warm
            with self.subTest(keys=keys):
                ui.sessions[1] = dict(base, **keys)
                acts = self.real(self.acts(ui, "Old"))
                self.assertIn(f"{self.RESUME} · 412k tokens, {want}", acts)

    def test_with_no_tokens_resume_is_just_its_name(self):
        acts = self.real(self.acts(self.fixture(tokens=0), "Old"))
        self.assertEqual(acts[1], self.RESUME, acts)
        self.assertEqual([a for a in acts if a.startswith(self.RESUME)], [self.RESUME])

    def test_resume_opens_it_in_a_new_window_and_a_plain_open_is_still_refused(self):
        ui = self.fixture()
        opened = []
        saved, roost.open_in_window = roost.open_in_window, lambda s, term: opened.append((s["id"], term)) or "opened"
        try:
            old = next(s for s in ui.sessions if s["name"] == "Old")
            self.assert_resumable(ui, "Old")
            act(ui, ui.keys_sessions, ui.sessions, self.RESUME)
            self.assertEqual(opened, [("bb2c0000-0000", "ghostty")])
            self.assertNotIn("fresh Session", ui.msg)  # not refused
            ui.msg = ""
            self.assertIsNone(ui.open_one(old))
            self.assertIn("fresh Session", ui.msg)
            self.assertEqual(opened, [("bb2c0000-0000", "ghostty")], "open_one still refuses it")
        finally:
            roost.open_in_window = saved

    def test_with_no_terminal_resume_returns_it_to_run_here(self):
        ui = self.fixture()
        ui.term = None
        opened = []
        saved, roost.open_in_window = roost.open_in_window, lambda s, term: opened.append(s["id"]) or "opened"
        try:
            self.assert_resumable(ui, "Old")
            got = act(ui, ui.keys_sessions, ui.sessions, self.RESUME)
            self.assertEqual((got or {}).get("id"), "bb2c0000-0000", got)
            self.assertEqual(opened, [])
        finally:
            roost.open_in_window = saved

    def test_a_wrapped_up_project_session_gets_no_resume(self):
        acts = self.real(self.acts(self.fixture(), "Wrapped"))
        self.assertEqual(acts[0], "Start a fresh Session for Title", acts)
        self.assertFalse([a for a in acts if a.startswith(self.RESUME)], acts)

    def test_running_and_projectless_sessions_get_no_resume(self):
        ui = self.fixture()
        ui.sessions += [dict(session("Live"), id="cc3d0000-0000"), dict(session("Loose", state="closed", kind=None, mins=30, tokens=9000),
                                                                         id="dd4e0000-0000")]
        for name in ("Live", "Loose"):
            with self.subTest(name=name):
                self.assertFalse([a for a in self.acts(ui, name) if a.startswith(self.RESUME)])


class SessionColumns(unittest.TestCase):
    """The Sessions tab's columns scale with the window, read off the drawn header and rows, inside the
    table's outline: NAME, LAST ACTIVE, CONTEXT, PROJECT."""
    setUp = TextualScreens.setUp
    proj = staticmethod(TextualScreens.proj)
    select = staticmethod(TextualScreens.select)
    run_on = ProjectRows.run_on
    TITLES = [("NAME", "NAME"), ("LAST ACTIVE", "LAST"), ("CONTEXT", "CONT"), ("PROJECT", "PROJ")]

    def fleet(self, names, label="Printer fleet"):
        """Running Sessions with these names (the first idle, the second waiting, the rest idle), at
        5m, 2h, 3d... ago with 181k, 35k, 9k... of context; the first two are in a project called label."""
        tokens, mins = [181000, 35000, 9000, 120000, 4000], [5, 120, 3 * 1440, 30, 50]
        self.sessions = [session(n, state="waiting" if i == 1 else "idle", mins=mins[i % 5], tokens=tokens[i % 5])
                         for i, n in enumerate(names)]
        return [self.proj("p1", self.sessions[:2], label=label)]

    def drawn(self, projects, w, sort=None, config=None):
        """The Sessions tab in a window w wide (it has no sidebar): its lines from the list's left edge, the
        list's lines (header first), the list's width, and each Session's status text, LAST ACTIVE value and
        PROJECT label as roost works them out."""
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
        # 40 rows: each Session takes three lines, and at 30 the table holds only two
        lines = self.run_on(projects, [open_view("Sessions"), look], size=(w, 40), tab="Sessions", config=config).lines
        lines = [l[seen["x"]:] for l in lines]  # the list's part of each line
        h = self.header_y(lines)
        edge = next((i for i in range(h + 1, len(lines)) if lines[i].strip().startswith("╰")), None)  # the outline's bottom
        assert edge is not None, "no outline's bottom edge under the column headers:\n" + "\n".join(lines[h:])
        return lines[h:edge], seen["list"], seen["cells"]

    @staticmethod
    def header_y(lines):
        """The row of the column headers: NAME is its first word, inside the table's outline."""
        h = next((i for i, l in enumerate(lines) if re.match(r"^[│ ]*NAME\b", l)), None)
        assert h is not None, "no column headers:\n" + "\n".join(lines)
        return h

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

    def whole(self, listing, cells, w_titles=("NAME", "LAST ACTIVE", "PROJECT")):
        """True when every Session's name, LAST ACTIVE and PROJECT text is drawn whole."""
        for name, (status, when, label) in cells.items():
            row = self.row(listing, name)
            want = {"NAME": name, "LAST ACTIVE": when, "PROJECT": label}
            if any(want[t] and want[t] not in row for t in w_titles):
                return False
        return True

    SHORT = ["Printer fix", "Laptop refresh", "Badge audit"]  # the longest is 14

    # 1. NAME is its longest name + 2, whatever name_width says

    def test_name_grows_with_its_longest_name_not_a_fixed_width(self):
        """Same window (200), same everything but the longest name, 16 then 24 (both over NAME's floor of
        16): NAME is 8 wider less its share of the 8 spare columns it took (spread over four columns), so
        5 to 8 wider."""
        widths = {}
        for longest in ("Printer fix desk", "Printer fix for desk 12A"):  # 16, 24
            listing, _, _ = self.drawn(self.fleet([longest, "Laptop"]), 200)
            x = self.starts(listing[0])
            widths[len(longest)] = x["LAST ACTIVE"] - x["NAME"]
        self.assertGreaterEqual(widths[24] - widths[16], 5, widths)
        self.assertLessEqual(widths[24] - widths[16], 8, widths)

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
            for text in (name, when, proj):
                self.assertIn(text, row, (name, row))
            self.assertNotIn("…", row)

    # 3. Headers never run into each other

    def test_a_header_never_runs_into_the_next_one(self):
        """Every pair of drawn headers has a space between the first's text (its sort arrow included)
        and the second's start, at widths from 100 to 200, sorted on LAST ACTIVE, CONTEXT, NAME."""
        projects = self.fleet(["Printer fix", "Laptop", "Badge audit for the front lobby doors"], label="Fleet")
        for sort in (("LAST ACTIVE", True), ("CONTEXT", True), ("NAME", False), ("CONTEXT", False)):
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
        lines = [l[seen["x"]:] for l in lines]  # the list's part of each line
        h = self.header_y(lines)
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
        the outline's right edge."""
        listing, width, cells = self.drawn(self.fleet(self.SHORT), 240)
        x = self.starts(listing[0])
        self.assertEqual(list(x), [t for t, _ in self.TITLES], listing[0])
        right = listing[0].rstrip().rfind("│")  # the outline's right edge
        self.assertGreater(right, x["PROJECT"], listing[0])
        drawn = {"NAME": x["LAST ACTIVE"] - x["NAME"],
                 "LAST ACTIVE": x["CONTEXT"] - x["LAST ACTIVE"], "CONTEXT": x["PROJECT"] - x["CONTEXT"],
                 "PROJECT": right - 1 - x["PROJECT"]}
        values = {"NAME": [n for n in cells],
                  "LAST ACTIVE": [c[1] for c in cells.values()] + ["LAST ACTIVE ▾"],
                  "CONTEXT": [roost.ktok(s["tokens"]) for s in self.sessions], "PROJECT": [c[2] for c in cells.values()]}
        need = {t: max(len(v) for v in values[t] + [t]) + 2 for t in drawn}
        spare = {t: drawn[t] - need[t] for t in drawn}
        for t in drawn:
            self.assertGreaterEqual(spare[t], 10, (t, spare, drawn))
        self.assertLessEqual(max(spare.values()) - min(spare.values()), 3, (spare, drawn))

    # 6. A narrow window: PROJECT gives way first, then CONTEXT, LAST ACTIVE; NAME last

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
        for t in ("NAME", "LAST ACTIVE", "CONTEXT", "PROJECT"):
            self.assertEqual(x[t], y[t], (t, full[0], narrow[0]))
        self.assertNotIn(label, self.row(narrow, long_name))
        self.assertIn(long_name, self.row(narrow, long_name))

    def test_then_context_then_last_active_and_name_keeps_its_width(self):
        """At 100 columns, a longer and longer name (50 to 96, by 2): PROJECT goes first, then CONTEXT,
        then LAST ACTIVE, each gone for good once gone, and NAME stays its longest name + 2 while any
        column after it is still drawn."""
        base = "Quarterly laptop refresh and badge audit for the front lobby doors and the annex and its loading dock"
        gone_at = {}
        for n in range(50, 98, 2):
            long_name = base[:n]
            listing, _, cells = self.drawn(self.fleet([long_name, "Laptop refresh"], label="Printer fleet"), 100)
            x = self.starts(listing[0])
            for t in ("PROJECT", "CONTEXT", "LAST ACTIVE"):
                if t in x:
                    self.assertNotIn(t, gone_at, (n, t, "back after it gave way", listing[0]))
                else:
                    gone_at.setdefault(t, n)
            after = [t for t, _ in self.TITLES[1:] if t in x]
            if after:
                self.assertGreaterEqual(x[after[0]] - x["NAME"], n + 2, (n, listing[0]))  # NAME whole
                self.assertIn(long_name, self.row(listing, long_name), n)
            for name, (status, when, label) in cells.items():
                row = self.row(listing, name)
                if "PROJECT" not in x:
                    self.assertNotIn("Printer fleet", row, n)
                if "CONTEXT" in x:  # LAST ACTIVE gives way only after CONTEXT: until then it's whole
                    self.assertIn(when, row, (n, row))
        self.assertEqual(set(gone_at), {"PROJECT", "CONTEXT", "LAST ACTIVE"}, gone_at)
        self.assertLess(gone_at["PROJECT"], gone_at["CONTEXT"], gone_at)
        self.assertLess(gone_at["CONTEXT"], gone_at["LAST ACTIVE"], gone_at)

@unittest.skipUnless(roost.App, "Textual isn't installed here: run the tests with ~/.roost/venv/bin/python")
class SessionRows(unittest.TestCase):
    """The Running and Closed views: the view row as words (the current one bold, underlined, green), the
    table in a rounded outline the tab's width with the view and its count in its top edge, the columns
    NAME, LAST ACTIVE, CONTEXT, PROJECT, and each Session on two lines (its row, then its status mark and
    its last message, dim) and a blank line. The panel under the table no longer shows the last message."""
    setUp = TextualScreens.setUp
    views_fixture = TextualScreens.views_fixture
    closed = staticmethod(TextualScreens.closed)
    look = staticmethod(TextualScreens.look)
    select = staticmethod(TextualScreens.select)
    keys = staticmethod(TextualScreens.keys)
    on_session = TextualScreens.on_session

    TONER = "Swapped the **toner** in `tray 2` and reprinted"
    TONER_PLAIN = "Swapped the toner in tray 2 and reprinted"
    LONG = "Imaged the spare laptops " + " ".join(f"step{i:02d}" for i in range(60))

    def run_app(self, steps, size=(120, 40)):
        """TextualScreens.run_app, 40 rows tall unless asked: at 30 the table holds only two Sessions."""
        return TextualScreens.run_app(self, steps, size=size)

    def rows_fixture(self):
        """Three running Sessions: Printer fix (idle, ○, a last message with ** and backticks), Laptop refresh
        (waiting, ◆, a last message far longer than the window), Badge audit (working, ●, no last message)."""
        self.sessions = [dict(session("Printer fix", mins=5, tokens=41000), summary=self.TONER),
                         dict(session("Laptop refresh", state="waiting", mins=10, tokens=9000), summary=self.LONG),
                         session("Badge audit", state="busy", mins=20, tokens=2000)]

    @staticmethod
    def box(app):
        """The Sessions table's outline: (top, bottom, left, right, lines), the rows of its top and bottom
        edges and the columns of its left and right edges, on the screen as drawn."""
        lines = screen_text(app)
        top = next((y for y, l in enumerate(lines) if y > app.ui.view_row_y and "╭" in l), None)
        assert top is not None, "no outline under the view row:\n" + "\n".join(lines)
        left, right = lines[top].index("╭"), lines[top].find("╮")
        assert right > left, f"the outline's top edge has no ╮: {lines[top]!r}"
        bottom = next((y for y in range(top + 1, len(lines)) if len(lines[y]) > left and lines[y][left] == "╰"), None)
        assert bottom is not None, "the outline has no bottom edge:\n" + "\n".join(lines)
        return top, bottom, left, right, lines

    @classmethod
    def name_y(cls, app, name):
        """The screen row of name's first line, inside the outline."""
        top, bottom, left, right, lines = cls.box(app)
        y = next((y for y in range(top + 1, bottom) if name in lines[y]), None)
        assert y is not None, f"{name} isn't inside the outline:\n" + "\n".join(lines[top:bottom + 1])
        return y

    # 1. The view row: words, the current one bold, underlined and green, counts dim

    def test_the_current_views_word_is_bold_underlined_and_green_and_the_others_and_every_count_dim(self):
        self.views_fixture()
        seen = {}

        def styles(app):
            y = app.ui.view_row_y
            line = screen_text(app)[y]
            out = {}
            for label in roost.VIEWS:
                m = re.search(rf"\b{label} (\d+)\b", line)
                assert m, f"{label} and its count aren't in the view row {line!r}"
                out[label] = ([style_at(app, y, x) for x in range(m.start(), m.start() + len(label))],
                              [style_at(app, y, x) for x in range(m.start(1), m.end(1))])
            return roost.VIEWS[app.ui.view], out, line
        steps = [open_view("Sessions")]
        for i in range(len(roost.VIEWS)):
            steps += [self.look(seen, i, styles), self.keys("right")]
        self.run_app(steps)
        green, muted = roost.GREEN.lower(), roost.MUTED.lower()
        self.assertEqual([seen[i][0] for i in range(len(roost.VIEWS))], list(roost.VIEWS), "the fixture: → went through each view")
        for i in range(len(roost.VIEWS)):
            current, out, line = seen[i]
            for label, (word, count) in out.items():
                with self.subTest(view=current, label=label):
                    if label == current:
                        self.assertTrue(all(color_of(st) == green and st.bold and st.underline for st in word),
                                        f"{label} is bold, underlined and green: {line!r}")
                    else:
                        self.assertEqual({color_of(st) for st in word}, {muted}, f"{label} is dim while {current} shows")
                        self.assertFalse([st for st in word if st.underline], f"{label} isn't underlined while {current} shows")
                    self.assertEqual({color_of(st) for st in count}, {muted}, f"{label}'s count is dim: {line!r}")

    # 2. The outline, its top edge, and what's inside it

    def test_the_table_is_a_rounded_outline_the_tabs_width_with_the_view_and_its_count_in_its_top_edge(self):
        self.views_fixture()  # 2 running, 3 closed
        seen = {}
        steps = [open_view("Sessions"), self.look(seen, "Running", self.box),
                 self.keys("right"), self.look(seen, "Closed", self.box)]
        app = self.run_app(steps)
        width = app.size.width
        for view, count in (("Running", 2), ("Closed", 3)):
            top, bottom, left, right, lines = seen[view]
            with self.subTest(view=view):
                edge = lines[top][left:right + 1]
                self.assertRegex(edge, rf"^╭─ {view} {count} ─+╮$")
                self.assertRegex(lines[bottom][left:right + 1], r"^╰─+╯$")
                self.assertLessEqual(left, 2, f"the outline starts at the tab's left: {lines[top]!r}")
                self.assertGreaterEqual(right, width - 3, f"the outline runs to the tab's right: {lines[top]!r}")
                for y in range(top + 1, bottom):
                    self.assertEqual((lines[y][left:left + 1], lines[y][right:right + 1]), ("│", "│"),
                                     f"row {y} has the outline's sides: {lines[y]!r}")
                self.assertIn("NAME", lines[top + 1], "the column headers are the outline's first line")
                self.assertEqual(lines[top + 2][left + 1:right].strip(), "", "a blank line under the headers")
                names = [s["name"] for s in self.sessions if (s["state"] == "closed") == (view == "Closed")]
                self.assertTrue(any(n in lines[top + 3] for n in names), f"a Session is under the blank line: {lines[top + 3]!r}")

    def test_the_outlines_title_is_bold_its_count_dim_and_its_edge_in_the_line_color(self):
        self.views_fixture()
        seen = {}

        def look(app):
            top, bottom, left, right, lines = self.box(app)
            m = re.search(r"Running (\d+)", lines[top])
            return ([style_at(app, top, x) for x in range(m.start(), m.start() + len("Running"))],
                    [style_at(app, top, x) for x in range(m.start(1), m.end(1))],
                    [style_at(app, top, x) for x in (left, left + 1, right)] + [style_at(app, bottom, left + 3)])
        self.run_app([open_view("Sessions"), self.look(seen, "x", look)])
        title, count, edges = seen["x"]
        self.assertTrue(all(st and st.bold for st in title), "the view's name is bold")
        self.assertEqual({color_of(st) for st in count}, {roost.MUTED.lower()}, "its count is dim")
        line = importlib.import_module("roost.app.widgets").LINE.lower()
        self.assertEqual({color_of(st) for st in edges}, {line}, "the outline is in the line color, as the Projects tab's are")

    def test_the_closed_views_section_headers_are_inside_the_outline(self):
        self.views_fixture()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "dismissed.json"
        spare = next(s for s in self.sessions if s["name"] == "Spare audit")
        path.write_text(json.dumps([spare["id"]]))
        patcher, _ = dismissed_file(path)
        self.addCleanup(patcher.stop)
        seen = {}
        self.run_app([open_view("Sessions"), self.keys("right"), self.look(seen, "x", self.box)])
        top, bottom, left, right, lines = seen["x"]
        for head in ("Closed (2)", "Dismissed (1)"):
            y = next((y for y, l in enumerate(lines) if head in l), None)
            with self.subTest(head=head):
                self.assertIsNotNone(y, "\n".join(lines))
                self.assertTrue(top < y < bottom, f"{head} is inside the outline:\n" + "\n".join(lines[top:bottom + 1]))
                self.assertGreater(lines[y].index(head), left)
                self.assertEqual(lines[y][right:right + 1], "│", lines[y])

    # 3. The columns: NAME, LAST ACTIVE, CONTEXT, PROJECT; no STATUS

    def test_the_columns_are_name_last_active_context_and_project_with_no_status(self):
        self.rows_fixture()
        seen = {}

        def look(app):
            top, bottom, left, right, lines = self.box(app)
            cells = {s["name"]: app.ui.status_cell(s)[0] for s in app.ui.srows}
            return lines[top + 1], {s["name"]: lines[self.name_y(app, s["name"])] for s in app.ui.srows}, cells
        self.run_app([open_view("Sessions"), self.look(seen, "x", look)])
        head, rows, cells = seen["x"]
        self.assertRegex(head, r"NAME.*\bLAST ACTIVE\b.*\bCONTEXT\b.*\bPROJECT\b")
        self.assertNotIn("STATUS", head)
        self.assertIn(roost.ktok(41000), rows["Printer fix"])  # 41k
        for name, row in rows.items():
            with self.subTest(name=name):
                self.assertNotIn(cells[name], row, "its status isn't a column on its row")
                self.assertFalse(set(row) & set("█▉▊▋▌▍▎▏░▒▓■"), f"CONTEXT has no bar: {row!r}")

    # 4. Two lines a Session: its row, then its status mark and its last message; a blank line after

    def lines_of(self, app):
        """For each Session in the view: its row's text, its second line's, and the line after them, with
        the selected row's › column, NAME's column, and each second line's status mark (the first character
        of status_cell's text) and its color as drawn and as its status is colored (status_cell's attr)."""
        top, bottom, left, right, lines = self.box(app)
        ui = app.ui
        out = {}
        for i, s in enumerate(ui.srows):
            y = self.name_y(app, s["name"])
            symbol = ui.status_cell(s)[0][0]
            mark = lines[y + 1].find(symbol)
            out[s["name"]] = {"symbol": symbol, "y": y, "row": lines[y], "second": lines[y + 1], "after": lines[y + 2] if y + 2 < bottom else None,
                              "mark": mark, "drawn": color_of(style_at(app, y + 1, mark)) if mark >= 0 else None,
                              "want": color_of(ui.style(ui.status_cell(s)[1])), "selected": i == ui.sel,
                              "msg": [style_at(app, y + 1, x) for x in range(mark + 1, right)]}
        return out, lines[top + 1].index("NAME"), left, right

    def test_each_session_is_its_row_then_its_status_mark_and_last_message_then_a_blank_line(self):
        self.rows_fixture()
        seen = {}
        self.run_app([open_view("Sessions"), self.on_session("Printer fix"), self.look(seen, "x", self.lines_of)])
        out, name_x, left, right = seen["x"]
        sel = out["Printer fix"]
        self.assertTrue(sel["selected"], "the fixture: Printer fix is selected")
        tick = sel["row"].find("›")
        self.assertTrue(left < tick < sel["row"].index("Printer fix"), f"› before the selected name: {sel['row']!r}")
        last = max(out, key=lambda n: out[n]["y"])
        for name, got in out.items():
            with self.subTest(name=name):
                self.assertEqual(got["second"][tick:tick + 1], got["symbol"],
                                 f"its status mark {got['symbol']} in the column where › sits: {got['second']!r}")
                self.assertEqual(got["mark"], tick, f"{got['symbol']} in the column where › sits: {got['second']!r}")
                self.assertEqual(got["drawn"], got["want"], f"{got['symbol']} is colored as its status is")
                self.assertEqual(got["second"][left + 1:tick].strip(), "", got["second"])
                if name != last:
                    self.assertIsNotNone(got["after"])
                    self.assertEqual(got["after"][left + 1:right].strip(), "", f"a blank line after {name}")
        self.assertNotEqual(out["Printer fix"]["want"], out["Laptop refresh"]["want"], "the fixture: two statuses, two colors")
        self.assertEqual({n: g["symbol"] for n, g in out.items()},
                         {"Printer fix": "○", "Laptop refresh": "◆", "Badge audit": "●"}, "the fixture: three status marks")
        toner = out["Printer fix"]["second"]
        self.assertEqual(toner.find(self.TONER_PLAIN), name_x, f"the last message, plain, starts under NAME: {toner!r}")
        self.assertEqual(toner[right:right + 1], "│")
        self.assertEqual(out["Badge audit"]["second"][left + 1:right].strip(), out["Badge audit"]["symbol"],
                         "no last message: just its status mark")

    def test_the_last_message_is_dim_on_one_line_clipped_to_the_outline(self):
        self.rows_fixture()
        seen = {}
        self.run_app([open_view("Sessions"), self.look(seen, "x", self.lines_of)])
        out, name_x, left, right = seen["x"]
        got = out["Laptop refresh"]
        shown = got["second"][name_x:right].rstrip()
        self.assertGreater(len(shown), 40, f"the message is drawn: {got['second']!r}")
        self.assertTrue(self.LONG.startswith(shown.rstrip("…").rstrip()), f"its start, clipped: {shown!r}")
        self.assertEqual(got["second"][right:right + 1], "│", "clipped inside the outline")
        self.assertEqual(got["after"][left + 1:right].strip(), "", f"not wrapped onto the next line: {got['after']!r}")
        x0 = name_x - got["mark"] - 1  # msg starts the column after the mark
        text = [st for st, ch in zip(got["msg"][x0:], got["second"][name_x:right]) if ch.strip()]
        self.assertTrue(text)
        self.assertEqual({color_of(st) for st in text}, {roost.MUTED.lower()}, "the last message is dim")

    def test_a_session_with_a_recap_shows_the_recaps_summary_plain(self):
        import unittest.mock
        loose = dict(session("Loose ends", mins=3), summary="the raw last message")
        self.sessions = [loose]
        recap = {"id": "loose-recap", "path": "/tmp/loose-recap.md", "summary": "Recap: cleared the **print** queue with `lpstat`",
                 "last": time.time(), "sessions": [loose["id"]], "folder": None, "covered": {}}
        seen = {}
        with unittest.mock.patch.object(roost, "read_recaps", lambda: [recap]):
            self.run_app([open_view("Sessions"), self.look(seen, "x", self.lines_of)])
        out, name_x, left, right = seen["x"]
        second = out["Loose ends"]["second"]
        self.assertEqual(second.find("Recap: cleared the print queue with lpstat"), name_x, second)
        self.assertNotIn("raw last message", second)

    # 5. Either line selects; ↑ ↓ go Session by Session; scrolling keeps both lines in view

    def test_a_click_on_either_of_a_sessions_lines_selects_it(self):
        self.rows_fixture()
        seen = {}

        def click(name, line):
            async def step(pilot):
                pilot.app.redraw()
                await pilot.pause()
                await pilot.click(offset=(30, self.name_y(pilot.app, name) + line))
                await pilot.pause()
                seen[(name, line)] = pilot.app.ui.srows[pilot.app.ui.sel]["name"]
            return step
        self.run_app([open_view("Sessions"), self.on_session("Printer fix"),
                      click("Badge audit", 1), click("Laptop refresh", 1), click("Printer fix", 0), click("Laptop refresh", 0)])
        self.assertEqual(seen, {("Badge audit", 1): "Badge audit", ("Laptop refresh", 1): "Laptop refresh",
                                ("Printer fix", 0): "Printer fix", ("Laptop refresh", 0): "Laptop refresh"})

    def test_up_and_down_move_session_by_session(self):
        self.rows_fixture()
        seen = {}
        name = lambda app: app.ui.srows[app.ui.sel]["name"]
        order = lambda app: [s["name"] for s in app.ui.srows]
        steps = [open_view("Sessions"), self.select(lambda ui: setattr(ui, "sel", 0)), self.look(seen, "order", order),
                 self.look(seen, 0, name)]
        for i, k in enumerate(["down", "down", "up"], 1):
            steps += [self.keys(k), self.look(seen, i, name)]
        self.run_app(steps)
        o = seen["order"]
        self.assertEqual([seen[i] for i in range(4)], [o[0], o[1], o[2], o[1]])

    def test_scrolling_keeps_the_selected_sessions_both_lines_in_view(self):
        self.sessions = [dict(session(f"Desk {i:02d}", mins=i + 1), summary=f"Note for desk {i:02d}") for i in range(14)]
        seen = []

        async def check(pilot):
            pilot.app.redraw()
            await pilot.pause()
            app = pilot.app
            s = app.ui.srows[app.ui.sel]
            top, bottom, left, right, lines = self.box(app)
            y = next((y for y in range(top + 1, bottom) if s["name"] in lines[y]), None)
            seen.append((s["name"], y is not None and top < y + 1 < bottom and s["summary"] in lines[y + 1],
                         "\n".join(lines[top:bottom + 1])))
        steps = [open_view("Sessions"), self.select(lambda ui: setattr(ui, "sel", 0)), check]
        for k in ["down"] * 13 + ["up"] * 13:
            steps += [self.keys(k), check]
        self.run_app(steps, size=(120, 30))
        self.assertEqual(len({n for n, _, _ in seen}), 14, "the fixture: ↓ reached every Session")
        for name, ok, shown in seen:
            self.assertTrue(ok, f"{name}'s row and its last message are both in view:\n{shown}")

    # 6. The panel keeps its facts but no longer shows the last message

    def test_the_panel_has_no_last_message_now_the_list_shows_it(self):
        self.rows_fixture()
        seen = {}

        def look(app):
            box = app.query_one(roost.SessionPanel).region
            lines = screen_text(app)
            return lines[box.y:box.y + box.height], lines[:box.y]
        self.run_app([open_view("Sessions"), self.on_session("Printer fix"), self.look(seen, "x", look)])
        panel, above = seen["x"]
        text = "\n".join(panel)
        self.assertIn("Printer fix", text, "the panel shows the selected Session")
        self.assertRegex(text, r"Project +none")
        self.assertIn("Context", text)
        self.assertTrue(any(self.TONER_PLAIN in l for l in above), "the list shows its last message")
        self.assertNotIn("Swapped the", text, "the panel doesn't")



class OpeningInATab(unittest.TestCase):
    """#325: roost opens a Session in a new tab of the chosen terminal by default; config open_in "window"
    keeps a new window. Terminal.app's tab needs Accessibility, so there a failed tab falls back to a window."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.saved = roost.CONFIG, roost.DONE, roost.RECAPS, roost.PLUGIN, roost.PROJECTS, roost.osa, roost.claude_settings, roost.NOTES
        roost.CONFIG, roost.DONE, roost.RECAPS, roost.NOTES = base / "config.json", base / "_done", base / "sessions", base / "notes"
        roost.claude_settings = lambda: {}
        roost.PLUGIN, roost.PROJECTS = base / "plugin", base / "transcripts"
        (roost.PLUGIN / ".claude-plugin").mkdir(parents=True)
        (roost.PLUGIN / ".claude-plugin" / "plugin.json").write_text("{}")
        self.root = base / "projects"
        self.root.mkdir()
        self.work = base / "work"
        self.work.mkdir()
        roost.save_config(projects_dir=str(self.root))
        self.calls = []  # (script, args) for each osa call
        self.failing = set()  # scripts that fail (osa returns None)
        roost.osa = lambda script, *args: self.calls.append((script, args)) or (None if script in self.failing else "ok")

    def tearDown(self):
        roost.CONFIG, roost.DONE, roost.RECAPS, roost.PLUGIN, roost.PROJECTS, roost.osa, roost.claude_settings, roost.NOTES = self.saved
        self.tmp.cleanup()

    def script(self, term, kind):
        return roost.TERMINALS[term].get(kind)

    def which(self, term):
        """Which of term's scripts each osa call ran: "tab", "new", "focus", or "other"."""
        kinds = {self.script(term, k): k for k in ("focus", "new", "tab") if self.script(term, k)}
        return [kinds.get(script, "other") for script, _ in self.calls]

    def project(self, name):
        folder = self.root / name
        (folder / "handoffs").mkdir(parents=True)
        (folder / "README.md").write_text(README)
        return folder

    def sessions_in(self, folder):
        return len(roost.frontmatter((folder / "README.md").read_text())["sessions"])

    def demo(self, name="Printer fix"):
        return session(name, cwd=str(self.work))

    # the scripts

    def test_every_terminal_has_a_tab_script(self):
        for term in roost.TERMINALS:
            with self.subTest(term=term):
                self.assertIsInstance(self.script(term, "tab"), str)
                self.assertIn("item 1 of argv", self.script(term, "tab") or "")

    def test_ghostty_opens_a_tab_in_the_front_window_or_a_window_when_there_is_none(self):
        tab = self.script("ghostty", "tab") or ""
        self.assertIn("new tab", tab)
        self.assertIn("new window", tab)
        self.assertIn("item 3 of argv", tab)  # Ghostty runs the direct-exec form, as in "new"

    def test_iterm2_creates_a_tab_or_a_window_when_there_is_none(self):
        tab = self.script("iterm2", "tab") or ""
        self.assertIn("create tab with default profile", tab)
        self.assertIn("create window", tab)
        self.assertIn("item 2 of argv", tab)

    def test_terminal_presses_cmd_t_and_runs_the_command_in_the_front_tab(self):
        tab = self.script("terminal", "tab") or ""
        self.assertIn("System Events", tab)
        self.assertIn('keystroke "t" using command down', tab)
        self.assertRegex(tab, r"do script .* in ")
        self.assertIn("item 2 of argv", tab)

    # open_in_window

    def test_open_in_window_uses_a_tab_by_default(self):
        for term in roost.TERMINALS:
            with self.subTest(term=term):
                self.calls.clear()
                s = self.demo()
                self.assertEqual(roost.open_in_window(s, term), "Opened Printer fix in a new tab")
                self.assertEqual(self.which(term), ["tab"])
                self.assertEqual(self.calls[0][1], (str(self.work), *roost.window_command(f"claude attach {s['attach']}", "Printer fix")))

    def test_open_in_tab_and_an_unknown_value_both_mean_a_tab(self):
        for value in ("tab", "sideways", None, ""):
            with self.subTest(open_in=value):
                roost.save_config(open_in=value)
                self.calls.clear()
                self.assertEqual(roost.open_in_window(self.demo(), "ghostty"), "Opened Printer fix in a new tab")
                self.assertEqual(self.which("ghostty"), ["tab"])

    def test_open_in_window_keeps_a_window(self):
        roost.save_config(open_in="window")
        for term in roost.TERMINALS:
            with self.subTest(term=term):
                self.calls.clear()
                self.assertEqual(roost.open_in_window(self.demo(), term), "Opened Printer fix in a new window")
                self.assertEqual(self.which(term), ["new"])

    def test_a_failed_tab_says_tab_and_ghostty_and_iterm2_do_not_fall_back(self):
        for term, label in [("ghostty", "Ghostty"), ("iterm2", "iTerm2")]:
            with self.subTest(term=term):
                self.calls.clear()
                self.failing = {self.script(term, "tab")}
                s = self.demo()
                self.assertEqual(roost.open_in_window(s, term),
                                 f"Couldn't open a {label} tab (check Automation permission, or run roost setup)")
                self.assertEqual(self.which(term), ["tab"])
                self.assertNotIn("opening", s)

    def test_a_failed_window_still_says_window(self):
        roost.save_config(open_in="window")
        self.failing = {self.script("ghostty", "new")}
        self.assertEqual(roost.open_in_window(self.demo(), "ghostty"),
                         "Couldn't open a Ghostty window (check Automation permission, or run roost setup)")

    def test_terminal_app_falls_back_to_a_window_and_says_tabs_need_accessibility(self):
        self.failing = {self.script("terminal", "tab")}
        s = self.demo()
        msg = roost.open_in_window(s, "terminal")
        self.assertIn("in a new window", msg)
        self.assertIn("Accessibility", msg)
        self.assertEqual(self.which("terminal"), ["tab", "new"])
        self.assertEqual(self.calls[0][1], self.calls[1][1])  # the window runs the same command in the same folder

    def test_terminal_app_with_neither_a_tab_nor_a_window_says_it_couldnt_open(self):
        self.failing = {self.script("terminal", "tab"), self.script("terminal", "new")}
        self.assertTrue(self.script("terminal", "tab"))
        self.assertIn("Couldn't open", roost.open_in_window(self.demo(), "terminal"))
        self.assertEqual(self.which("terminal"), ["tab", "new"])

    def test_focusing_an_open_session_is_unchanged(self):
        for value in (None, "window"):
            with self.subTest(open_in=value):
                roost.save_config(open_in=value)
                self.calls.clear()
                s = self.demo()
                s["attached"] = True
                self.assertEqual(roost.open_in_window(s, "ghostty"), "Switched to Printer fix, already open")
                self.assertEqual(self.which("ghostty"), ["focus"])

    # start_session

    def test_start_session_opens_a_tab_and_records_it(self):
        folder = self.project("p")
        msg = roost.start_session("p", str(folder), "ghostty")
        self.assertTrue(msg.startswith("Started a Session for p"), msg)
        self.assertEqual(self.which("ghostty"), ["tab"])
        cwd, typed, direct = self.calls[0][1]
        self.assertEqual(cwd, str(folder))
        self.assertIn("/roost:pickup p", direct)
        self.assertEqual(self.sessions_in(folder), 3)

    def test_start_session_with_open_in_window_opens_a_window(self):
        roost.save_config(open_in="window")
        folder = self.project("p")
        self.assertTrue(roost.start_session("p", str(folder), "ghostty").startswith("Started a Session for p"))
        self.assertEqual(self.which("ghostty"), ["new"])
        self.assertEqual(self.sessions_in(folder), 3)

    def test_start_session_in_a_failed_tab_records_nothing(self):
        folder = self.project("p")
        self.failing = {self.script("iterm2", "tab")}
        self.assertTrue(self.script("iterm2", "tab"))
        self.assertEqual(roost.start_session("p", str(folder), "iterm2"),
                         "Couldn't open a iTerm2 tab (check Automation permission, or run roost setup)")
        self.assertEqual(self.which("iterm2"), ["tab"])
        self.assertEqual(self.sessions_in(folder), 2)

    def test_start_session_in_terminal_app_falls_back_to_a_window_and_records_it(self):
        folder = self.project("p")
        self.failing = {self.script("terminal", "tab")}
        self.assertTrue(self.script("terminal", "tab"))
        msg = roost.start_session("p", str(folder), "terminal")
        self.assertTrue(msg.startswith("Started a Session for p"), msg)
        self.assertIn("Accessibility", msg)
        self.assertEqual(self.which("terminal"), ["tab", "new"])
        self.assertEqual(self.sessions_in(folder), 3)

    def test_start_session_when_terminal_app_opens_nothing_records_nothing(self):
        folder = self.project("p")
        self.failing = {self.script("terminal", "tab"), self.script("terminal", "new")}
        self.assertTrue(self.script("terminal", "tab"))
        self.assertIn("Couldn't open", roost.start_session("p", str(folder), "terminal"))
        self.assertEqual(self.sessions_in(folder), 2)

    # start_plain

    def test_start_plain_opens_a_tab(self):
        msg = roost.start_plain(str(self.work), "ghostty")
        self.assertTrue(msg.startswith("Started a Session in"), msg)
        self.assertEqual(self.which("ghostty"), ["tab"])
        self.assertEqual(self.calls[0][1][0], str(self.work))

    def test_start_plain_with_open_in_window_opens_a_window(self):
        roost.save_config(open_in="window")
        self.assertTrue(roost.start_plain(str(self.work), "ghostty").startswith("Started a Session in"))
        self.assertEqual(self.which("ghostty"), ["new"])

    def test_start_plain_in_a_failed_tab_says_tab(self):
        self.failing = {self.script("ghostty", "tab")}
        self.assertTrue(self.script("ghostty", "tab"))
        self.assertEqual(roost.start_plain(str(self.work), "ghostty"),
                         "Couldn't open a Ghostty tab (check Automation permission, or run roost setup)")
        self.assertEqual(self.which("ghostty"), ["tab"])

    def test_start_plain_in_terminal_app_falls_back_to_a_window(self):
        self.failing = {self.script("terminal", "tab")}
        self.assertTrue(self.script("terminal", "tab"))
        msg = roost.start_plain(str(self.work), "terminal")
        self.assertTrue(msg.startswith("Started a Session in"), msg)
        self.assertIn("Accessibility", msg)
        self.assertEqual(self.which("terminal"), ["tab", "new"])



class OpeningInTmux(unittest.TestCase):
    """Linux support: with terminal "tmux", roost opens and jumps to Sessions by running the tmux command
    (tmux new-window, list-windows, switch-client, select-window), never AppleScript. Nothing real runs:
    subprocess.run is a stand-in that answers as tmux would."""
    setUp_tab, tearDown, project, sessions_in = OpeningInATab.setUp, OpeningInATab.tearDown, OpeningInATab.project, OpeningInATab.sessions_in
    LIST = ["tmux", "list-windows", "-a", "-F", "#{session_name}:#{window_index}\t#{window_name}\t#{pane_title}"]
    SID = "00000000-0000-4000-8000-0000000000aa"

    def setUp(self):
        import subprocess, unittest.mock
        self.setUp_tab()  # osa records into self.calls; tmux must leave it empty
        self.ran = []  # (argv, kwargs) for each subprocess.run
        self.tmux_fails = None  # None, or "exit" (tmux exits 1) or "missing" (FileNotFoundError)
        self.windows = ""  # what tmux list-windows prints

        def run(argv, *a, **kw):
            argv = list(argv)
            self.ran.append((argv, kw))
            if argv[:1] == ["tmux"]:
                if self.tmux_fails == "missing":
                    raise FileNotFoundError(2, "No such file or directory", "tmux")
                if self.tmux_fails == "exit":
                    return subprocess.CompletedProcess(argv, 1, "", "no server running")
                return subprocess.CompletedProcess(argv, 0, self.windows if "list-windows" in argv else "", "")
            return subprocess.CompletedProcess(argv, 0, "", "")  # ps, pgrep: nothing open
        patches = [unittest.mock.patch.object(roost.terminals.subprocess, "run", run),
                   unittest.mock.patch.object(roost.terminals.uuid, "uuid4", lambda: self.SID)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tmux(self):
        """The argv of every tmux command roost ran, in order."""
        return [argv for argv, _ in self.ran if argv[:1] == ["tmux"]]

    def assert_quiet(self):
        """Every tmux command reads nothing from the terminal and keeps its output off the screen; no osa."""
        import subprocess
        for argv, kw in self.ran:
            if argv[:1] == ["tmux"]:
                self.assertEqual(kw.get("stdin"), subprocess.DEVNULL, argv)
                self.assertTrue(kw.get("capture_output") or kw.get("stdout") == subprocess.PIPE, argv)
        self.assertEqual(self.calls, [], "osa ran for tmux")

    def new_window(self, cwd, name, cmd):
        return ["tmux", "new-window", "-c", cwd, "-n", name, roost.window_command(cmd, name)[1]]

    def pickup(self, pid, name):
        return shlex.join(["claude", "--session-id", self.SID, "-n", name, *roost.plugin_args(), f"/roost:pickup {pid}"])

    # start_session

    def test_start_session_opens_a_tmux_window_and_records_it(self):
        folder = self.project("p")
        msg = roost.start_session("p", str(folder), "tmux")
        self.assertEqual(msg, f"Started a Session for p in {roost.short(str(folder))}")
        self.assertEqual(self.tmux(), [self.new_window(str(folder), "p", self.pickup("p", "p"))])
        self.assert_quiet()
        self.assertEqual(self.sessions_in(folder), 3)

    def test_start_session_names_the_tmux_window_after_the_session(self):
        folder = self.project("p")
        roost.start_session("p", str(folder), "tmux", name="Printer fix")
        self.assertEqual(self.tmux(), [self.new_window(str(folder), "Printer fix", self.pickup("p", "Printer fix"))])

    def test_start_session_ignores_open_in(self):
        for value in ("tab", "window"):
            with self.subTest(open_in=value):
                roost.save_config(open_in=value)
                self.ran.clear()
                folder = self.project(f"p-{value}")
                self.assertTrue(roost.start_session(f"p-{value}", str(folder), "tmux").startswith(f"Started a Session for p-{value}"))
                self.assertEqual([a[:2] for a in self.tmux()], [["tmux", "new-window"]])
                self.assert_quiet()

    def test_start_session_when_tmux_fails_records_nothing(self):
        for fails in ("exit", "missing"):
            with self.subTest(tmux=fails):
                self.tmux_fails = fails
                folder = self.project(f"p-{fails}")
                self.assertEqual(roost.start_session(f"p-{fails}", str(folder), "tmux"),
                                 "Couldn't open a tmux window (is tmux running? roost setup chooses another terminal)")
                self.assertEqual(self.sessions_in(folder), 2)
                self.assert_quiet()

    # start_plain

    def test_start_plain_opens_a_tmux_window_named_after_the_folder(self):
        msg = roost.start_plain(str(self.work), "tmux")
        self.assertEqual(msg, f"Started a Session in {roost.short(str(self.work))}")
        cmd = shlex.join(["claude", *roost.plugin_args()])
        self.assertEqual(self.tmux(), [self.new_window(str(self.work), "work", cmd)])
        self.assert_quiet()

    def test_start_plain_when_tmux_fails_says_so(self):
        for fails in ("exit", "missing"):
            with self.subTest(tmux=fails):
                self.tmux_fails = fails
                self.assertEqual(roost.start_plain(str(self.work), "tmux"),
                                 "Couldn't open a tmux window (is tmux running? roost setup chooses another terminal)")
                self.assert_quiet()

    # open_in_window: a Session that isn't open

    def test_open_in_window_opens_a_background_session_in_a_tmux_window(self):
        for value in (None, "tab", "window"):
            with self.subTest(open_in=value):
                roost.save_config(open_in=value)
                self.ran.clear()
                s = session("Printer fix", cwd=str(self.work))
                self.assertEqual(roost.open_in_window(s, "tmux"), "Opened Printer fix in a new tmux window")
                self.assertEqual(self.tmux(), [self.new_window(roost.launch_dir(s), "Printer fix", f"claude attach {s['attach']}")])
                self.assert_quiet()

    def test_open_in_window_resumes_a_closed_session_in_a_tmux_window(self):
        s = session("Printer fix", state="closed", kind=None, cwd=str(self.work))
        self.assertEqual(roost.open_in_window(s, "tmux"), "Opened Printer fix in a new tmux window")
        self.assertEqual(self.tmux(), [self.new_window(roost.launch_dir(s), "Printer fix", f"claude --resume {s['id']}")])
        self.assert_quiet()

    # open_in_window: jumping to an open Session

    def test_jumping_to_an_open_session_switches_to_its_tmux_window(self):
        self.windows = "main:0\tzsh\tdemo-host\nwork:3\t✳ Printer fix\tclaude\n"
        for s in (session("Printer fix", kind="interactive"), session("Printer fix", attached=True)):
            with self.subTest(kind=s["kind"]):
                self.ran.clear()
                self.assertEqual(roost.open_in_window(s, "tmux"), "Switched to Printer fix, already open")
                self.assertEqual(self.tmux(), [self.LIST, ["tmux", "switch-client", "-t", "work:3"],
                                               ["tmux", "select-window", "-t", "work:3"]])
                self.assert_quiet()

    def test_jumping_matches_a_pane_title(self):
        self.windows = "main:0\tzsh\tdemo-host\nmain:2\tclaude\t✳ Printer fix\n"
        self.assertEqual(roost.open_in_window(session("Printer fix", kind="interactive"), "tmux"),
                         "Switched to Printer fix, already open")
        self.assertEqual(self.tmux()[1:], [["tmux", "switch-client", "-t", "main:2"], ["tmux", "select-window", "-t", "main:2"]])

    def test_jumping_matches_the_ai_title(self):
        self.windows = "main:0\tzsh\tdemo-host\nmain:4\tclaude\t✳ Fix the demo printer\n"
        s = session("Printer fix", kind="interactive")
        s["ai_title"] = "Fix the demo printer"
        self.assertEqual(roost.open_in_window(s, "tmux"), "Switched to Printer fix, already open")
        self.assertEqual(self.tmux()[1:], [["tmux", "switch-client", "-t", "main:4"], ["tmux", "select-window", "-t", "main:4"]])

    def test_jumping_takes_the_first_matching_window(self):
        self.windows = "main:1\tPrinter fix\tclaude\nmain:5\tPrinter fix\tclaude\n"
        roost.open_in_window(session("Printer fix", kind="interactive"), "tmux")
        self.assertEqual(self.tmux()[1:], [["tmux", "switch-client", "-t", "main:1"], ["tmux", "select-window", "-t", "main:1"]])

    def test_a_name_only_in_the_middle_of_a_title_is_no_match(self):
        self.windows = "main:1\tPrinter fix notes\tvim\n"
        self.assertEqual(roost.open_in_window(session("Printer fix", kind="interactive"), "tmux"),
                         "Printer fix is already open, but its tmux window wasn't found")
        self.assertEqual(self.tmux(), [self.LIST])
        self.assert_quiet()

    def test_jumping_with_no_matching_window_says_so(self):
        for fails in (None, "exit", "missing"):
            with self.subTest(tmux=fails):
                self.ran.clear()
                self.tmux_fails = fails
                self.windows = "main:0\tzsh\tdemo-host\n"
                self.assertEqual(roost.open_in_window(session("Printer fix", kind="interactive"), "tmux"),
                                 "Printer fix is already open, but its tmux window wasn't found")
                self.assertEqual(self.tmux(), [self.LIST])
                self.assert_quiet()

    # mark_open

    def test_mark_open_marks_a_session_named_in_a_tmux_window_name_or_pane_title(self):
        self.windows = "main:0\tzsh\tdemo-host\nmain:1\t✳ Printer fix\tclaude\nmain:2\tclaude\t✳ Toner check\n"
        rows = [session("Printer fix"), session("Toner check"), session("Badge reader")]
        roost.mark_open(rows, "tmux")
        self.assertEqual([s["attached"] for s in rows], [True, True, False])
        self.assertIn(self.LIST, self.tmux())
        self.assert_quiet()

    def test_mark_open_marks_only_background_sessions(self):
        self.windows = "main:1\tPrinter fix\tclaude\n"
        rows = [session("Printer fix", kind="interactive")]
        roost.mark_open(rows, "tmux")
        self.assertFalse(rows[0]["attached"])

    def test_mark_open_when_tmux_fails_marks_nothing(self):
        for fails in ("exit", "missing"):
            with self.subTest(tmux=fails):
                self.tmux_fails = fails
                self.windows = "main:1\tPrinter fix\tclaude\n"
                rows = [session("Printer fix", attached=True)]
                roost.mark_open(rows, "tmux")
                self.assertFalse(rows[0]["attached"])
                self.assert_quiet()


if __name__ == "__main__":
    unittest.main()
