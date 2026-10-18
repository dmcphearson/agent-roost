from support import *  # noqa: F401,F403 (the shared namespace, helpers, and fixtures)
from support import _SCRATCH, own_tests  # noqa: F401
from test_projects import Projects  # for their helpers; their tests run in test_projects.py

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
                got = roost.visible(rows, ALL, "", (col, desc))
                self.assertEqual([roost.group(s) for s in got], sorted(roost.group(s) for s in got), (col, desc))


class Sorting(unittest.TestCase):
    def test_default_is_most_recent_first(self):
        rows = roost.visible([session("old", mins=50), session("new", mins=1)], ALL, "")
        self.assertEqual([s["name"] for s in rows], ["new", "old"])

    def test_sort_by_context_descending(self):
        rows = [session("small", tokens=10), session("big", tokens=900)]
        got = roost.visible(rows, ALL, "", ("CONTEXT", True))
        self.assertEqual([s["name"] for s in got], ["big", "small"])

    def test_sort_by_name_ascending_ignores_case(self):
        rows = [session("bravo"), session("Alpha")]
        got = roost.visible(rows, ALL, "", ("NAME", False))
        self.assertEqual([s["name"] for s in got], ["Alpha", "bravo"])


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
        rows = roost.visible([wrapping, bg, closed], ALL, "", keep={"abc"})
        self.assertEqual([s["id"] for s in rows], ["bg", "old", "abc"])
        self.assertEqual([s["id"] for s in roost.visible([wrapping, bg, closed], RUNNING, "", keep={"abc"})], ["bg", "abc"])

    def test_stale_tab(self):
        i = roost.VIEWS.index("Stale")
        old = time.time() - 3 * 86400
        self.assertTrue(roost.in_view(self.sess(last=old), i))
        self.assertFalse(roost.in_view(self.sess(last=time.time()), i))

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
            self.assertFalse([a for a in labels(ui, ui.keys_projects, rows) if a.startswith(("Stop", "Delete"))])
            live, old = 0, 1  # on the Sessions tab: the Projects tab lists no Sessions
            ui.sel = live
            act(ui, ui.keys_sessions, ui.sessions, "Stop…")
            self.assertTrue(ui.sessions_typing())
            ui.keys_confirm("n")  # any other key cancels
            self.assertEqual((did, ui.msg), ([], "Cancelled"))
            act(ui, ui.keys_sessions, ui.sessions, "Stop…")
            ui.keys_confirm("y")
            ui.sel = old
            act(ui, ui.keys_sessions, ui.sessions, "Delete to the Trash…")
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
        self.assertEqual((meta["summary"], meta["sessions"]), ("Fix the printers.", [{"name": "Printer Fix!", "id": "dd4e0000-0000"}]))
        self.assertEqual(offered, ui.sessions)  # its first Handoff, after the cost is shown
        self.assertNotIn("New project from this Session…", labels(ui, ui.keys_sessions, ui.sessions))
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


if __name__ == "__main__":
    unittest.main()
