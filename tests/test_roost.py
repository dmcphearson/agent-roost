"""Tests for roost's data logic. Demo sessions and temp paths only; real sessions are never read."""
import contextlib
import io
import json
import os
import re
import sys
import tarfile
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import roost as package


class Everything:
    """All of roost's modules as one namespace, so a test reads or replaces a name without knowing
    which module defines it. Replacing one sets it in its defining module, where every caller sees it."""
    def __init__(self):
        object.__setattr__(self, "_mods", package.modules())
        where = {}
        for m in self._mods:
            for name in vars(m):
                where.setdefault(name, m)
        object.__setattr__(self, "_where", where)

    def __getattr__(self, name):
        m = self._where.get(name)
        if m is None or name not in vars(m):
            raise AttributeError(name)
        return getattr(m, name)

    def __setattr__(self, name, value):
        for m in [self._where[name]] if name in self._where else self._mods:  # input(): every module's
            setattr(m, name, value)

    def __delattr__(self, name):
        for m in [self._where[name]] if name in self._where else self._mods:
            if name in vars(m):
                delattr(m, name)


roost = Everything()

RUNNING, ALL = roost.VIEWS.index("Running"), roost.VIEWS.index("All")
_SCRATCH = tempfile.TemporaryDirectory()
roost.NOTES, roost.RECAPS = Path(_SCRATCH.name) / "notes", Path(_SCRATCH.name) / "sessions"  # never the person's own
# nor their settings, pins, or installed files: a test that misses a stand-in writes here instead
for _name, _rel in [("CONFIG", "config.json"), ("PINS", "pins.json"), ("UPDATE_CHECK", "update-check.json"),
                    ("DONE", "_done"), ("ADOPT_PLANS", "adopt"), ("APP", "app"), ("SPEND_LOG", "spend.jsonl"),
                    ("SPEND_CACHE", "spend-cache.json"), ("LOG", "roost.log")]:
    setattr(roost, _name, Path(_SCRATCH.name) / _rel)


def screen_text(app):
    """The screen as Textual draws it, a line of text per row."""
    return ["".join(seg.text for seg in strip).rstrip() for strip in app.screen._compositor.render_strips()]


def session(name, state="idle", kind="background", pinned=False, attached=False, mins=0, tokens=0, cwd="/tmp"):
    sid = f"{abs(hash(name)) % 16**8:08x}-test"
    return {"id": sid, "name": name, "state": state, "kind": kind, "pinned": pinned, "attached": attached,
            "attach": sid[:8] if kind == "background" else None, "last": time.time() - mins * 60,
            "tokens": tokens, "cwd": cwd, "summary": "", "files": []}


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


class Config(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = roost.CONFIG
        roost.CONFIG = Path(self.tmp.name) / "roost" / "config.json"

    def tearDown(self):
        roost.CONFIG = self.saved
        self.tmp.cleanup()

    def test_save_merges_keys(self):
        roost.save_config(terminal="ghostty")
        roost.save_config(name_width=32)
        self.assertEqual(roost.read_config(), {"terminal": "ghostty", "name_width": 32})

    def test_width_alone_does_not_disable_terminal_detection(self):
        roost.save_config(name_width=32)
        saved, roost.detect_terminal = roost.detect_terminal, lambda: "ghostty"
        try:
            self.assertEqual(roost.chosen_terminal(), "ghostty")
        finally:
            roost.detect_terminal = saved


class Formatting(unittest.TestCase):
    def test_clip_adds_ellipsis(self):
        self.assertEqual(roost.clip("abcdef", 4), "abc…")
        self.assertEqual(roost.clip("abc", 4), "abc")

    def test_ktok(self):
        self.assertEqual(roost.ktok(163_713).strip(), "164k")


class Demo(unittest.TestCase):
    def test_demo_sessions_cover_every_section(self):
        groups = {roost.group(s) for s in roost.demo_sessions()}
        self.assertEqual(groups, {0, 1, 2, 3})

    def test_the_suite_logs_to_scratch_not_the_persons_log(self):
        real = Path.home() / ".cache" / "roost.log"
        before = real.stat().st_mtime if real.exists() else None
        roost.log("a line from the test suite")
        self.assertEqual(real.stat().st_mtime if real.exists() else None, before)
        self.assertIn("a line from the test suite", roost.LOG.read_text())

    def test_demo_projects_use_the_demos_own_categories(self):
        names = {c for c, _ in roost.DEMO_CATEGORY_LIST}
        self.assertEqual(set(roost.DEMO_CATEGORIES.values()) - names, set())

    def test_demo_paths_are_fake(self):
        for s in roost.demo_sessions():
            self.assertIn("/projects/", s["cwd"])

    def test_the_demo_starts_a_session_the_way_the_projects_tab_asks(self):
        import subprocess
        with tempfile.TemporaryDirectory() as home:  # use_demo() patches every module, so it runs on its own
            r = subprocess.run([sys.executable, "-c", "from roost.demo import use_demo; use_demo()\n"
                                "from roost.core import terminals\n"
                                "print(terminals.start_session('printer-rollout', '/tmp', 'ghostty', 'Printer fix'))"],
                               cwd=ROOT, env={**os.environ, "HOME": home}, stdin=subprocess.DEVNULL,
                               capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("(demo)", r.stdout)


class Compat(unittest.TestCase):
    def test_a_newer_claude_code_alone_is_only_logged(self):
        import unittest.mock
        newer = f"Claude Code 9.9.9 is newer than roost was tested with ({roost.TESTED_CLAUDE})"
        with unittest.mock.patch.object(roost, "compat_warning", lambda v, a: newer), \
                unittest.mock.patch.object(roost.subprocess, "run", lambda *a, **k: unittest.mock.Mock(stdout="[]")), \
                unittest.mock.patch.object(roost, "log") as log:
            self.assertEqual(roost.check_compat(), "")
        log.assert_called_once()
        changed = "Claude Code 9.9.9 changed its session list (missing x); states may be wrong"
        with unittest.mock.patch.object(roost, "compat_warning", lambda v, a: changed), \
                unittest.mock.patch.object(roost.subprocess, "run", lambda *a, **k: unittest.mock.Mock(stdout="[]")), \
                unittest.mock.patch.object(roost, "log"):
            self.assertEqual(roost.check_compat(), changed)

    AGENT = {k: None for k in roost.AGENT_KEYS}

    def test_tested_version_is_quiet(self):
        self.assertEqual(roost.compat_warning(f"{roost.TESTED_CLAUDE} (Claude Code)", [self.AGENT]), "")

    def test_older_version_is_quiet(self):
        self.assertEqual(roost.compat_warning("2.0.1 (Claude Code)", [self.AGENT]), "")

    def test_newer_version_warns(self):
        self.assertIn("newer", roost.compat_warning("9.0.0 (Claude Code)", [self.AGENT]))

    def test_missing_fields_warn_even_on_tested_version(self):
        agent = dict(self.AGENT); del agent["status"]
        msg = roost.compat_warning(f"{roost.TESTED_CLAUDE} (Claude Code)", [agent])
        self.assertIn("status", msg)

    def test_no_sessions_is_not_a_field_problem(self):
        self.assertEqual(roost.compat_warning(f"{roost.TESTED_CLAUDE} (Claude Code)", []), "")

    def test_unreadable_version(self):
        self.assertIn("Couldn't", roost.compat_warning("", [self.AGENT]))


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


class Log(unittest.TestCase):
    def test_log_appends(self):
        with tempfile.TemporaryDirectory() as d:
            saved, roost.LOG = roost.LOG, Path(d) / "sub" / "roost.log"
            try:
                roost.log("one"); roost.log("two")
                lines = roost.LOG.read_text().splitlines()
            finally:
                roost.LOG = saved
        self.assertEqual([l.split("  ", 1)[1] for l in lines], ["one", "two"])


class Updates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.saved = (roost.UPDATE_CHECK, roost.latest_release, roost.download_tarball, roost.VERSION, roost.update_plugin,
                      roost.update_textual, roost.APP)
        roost.UPDATE_CHECK, roost.APP = self.dir / "check.json", self.dir / "app"
        self.textual_updates = []
        roost.update_textual = lambda me: self.textual_updates.append(me) or 0
        roost.VERSION = "2026.10.03"
        self.plugin_updates = []
        roost.update_plugin = lambda v: self.plugin_updates.append(v) or 0  # the Plugin tests cover it

    def tearDown(self):
        (roost.UPDATE_CHECK, roost.latest_release, roost.download_tarball, roost.VERSION, roost.update_plugin, roost.update_textual,
         roost.APP) = self.saved
        self.tmp.cleanup()

    def test_newer_release_is_reported_and_cached(self):
        calls = []
        roost.latest_release = lambda: calls.append(1) or "2026.11.01"
        self.assertEqual(roost.update_available(), "2026.11.01")
        self.assertEqual(roost.update_available(), "2026.11.01")
        self.assertEqual(len(calls), 1)  # second call used the cache

    def test_same_or_older_release_is_quiet(self):
        roost.latest_release = lambda: "2026.10.03"
        self.assertEqual(roost.update_available(), "")

    def test_offline_is_quiet(self):
        def boom():
            raise RuntimeError("offline")
        roost.latest_release = boom
        self.assertEqual(roost.update_available(), "")

    def test_update_refuses_dev_link(self):
        target = self.dir / "checkout-roost"; target.write_text("x")
        link = self.dir / "roost"; link.symlink_to(target)
        self.assertEqual(roost.self_update(link), 1)
        self.assertEqual(target.read_text(), "x")

    def test_update_installs_the_package_then_the_launcher_and_keeps_the_old_one(self):
        me = self.dir / "roost"; me.write_text("old")
        roost.latest_release = lambda: "2026.11.01"
        launcher = b'#!/usr/bin/env python3\nprint("roost 2026.11.01")\n'
        roost.download_tarball = lambda version: release_tarball("2026.11.01", [("dmcphearson-agent-roost-abc123/bin/roost", launcher),
                                                                   ("dmcphearson-agent-roost-abc123/roost/__init__.py", b"")])
        self.assertEqual(roost.self_update(me), 0)
        self.assertEqual(me.read_bytes(), launcher)
        self.assertEqual((self.dir / "roost.previous").read_text(), "old")
        current = self.dir / "app" / "current"
        self.assertEqual(os.readlink(current), "2026.11.01")  # the program files, one folder per version
        self.assertTrue((current / "roost" / "__init__.py").is_file())
        self.assertFalse((current / "plugin").exists())  # only roost/ and bin/roost come out of the tarball
        self.assertEqual(self.plugin_updates, ["2026.11.01"])  # the plugin follows the new version
        self.assertEqual(self.textual_updates, [me])  # and the new launcher brings Textual to the version it wants

    def test_a_homebrew_roost_leaves_upgrades_to_brew_and_brings_its_plugin_up(self):
        me = self.dir / "roost"; me.write_text("old")
        roost.latest_release = lambda: self.fail("Homebrew's roost doesn't check GitHub")
        saved = roost.BREW
        roost.BREW = True
        try:
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(roost.self_update(me), 0)
                self.assertEqual(roost.setup_textual(), 0)
        finally:
            roost.BREW = saved
        self.assertIn("brew upgrade roost", out.getvalue())
        self.assertEqual(me.read_text(), "old")
        self.assertEqual(self.plugin_updates, ["2026.10.03"])  # the plugin matches what brew installed
        self.assertFalse(roost.APP.exists())

    def test_a_launcher_that_fails_after_the_swap_says_what_is_in_place(self):
        import unittest.mock
        me = self.dir / "roost"; me.write_text("old")
        roost.latest_release = lambda: "2026.11.01"
        roost.download_tarball = lambda version: release_tarball("2026.11.01", [
            ("dmcphearson-agent-roost-abc123/bin/roost", b'#!/usr/bin/env python3\nprint("roost 2026.11.01")\n'),
            ("dmcphearson-agent-roost-abc123/roost/__init__.py", b"")])
        real = roost.shutil.copy2
        def copy2(src, dst, **kw):
            if Path(dst).name == ".roost-update":
                raise OSError("disk full")
            return real(src, dst, **kw)
        with unittest.mock.patch.object(roost.shutil, "copy2", copy2), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(roost.self_update(me), 1)
        self.assertNotIn("nothing changed", out.getvalue())
        self.assertIn("roost runs 2026.11.01", out.getvalue())
        self.assertIn("disk full", out.getvalue())
        self.assertEqual(me.read_text(), "old")
        self.assertEqual(os.readlink(self.dir / "app" / "current"), "2026.11.01")

    def test_a_release_without_the_package_changes_nothing(self):
        me = self.dir / "roost"; me.write_text("old")
        roost.latest_release = lambda: "2026.11.01"
        roost.download_tarball = lambda version: release_tarball("2026.11.01")
        self.assertEqual(roost.self_update(me), 1)
        self.assertEqual(me.read_text(), "old")
        self.assertFalse((self.dir / "app" / "current").exists())

    def test_bad_download_changes_nothing(self):
        me = self.dir / "roost"; me.write_text("old")
        roost.latest_release = lambda: "2026.11.01"
        roost.download_tarball = lambda version: b"not python"
        self.assertEqual(roost.self_update(me), 1)
        self.assertEqual(me.read_text(), "old")
        self.assertFalse(list((self.dir / "app").glob(".*")))  # no half-unpacked download left behind

    def test_textual_goes_in_its_own_venv_and_is_left_alone_when_current(self):
        import unittest.mock
        calls = []

        def run(argv, **kw):
            calls.append(argv[1:3] if argv[1] == "-m" else ["check"])
            if argv[1] == "-m" and argv[2] == "venv":
                (Path(argv[3]) / "bin").mkdir(parents=True)
                (Path(argv[3]) / "bin" / "python").write_text("")
            out = roost.TEXTUAL.split("==")[1] if len(calls) > 3 else "1.0.0"
            return unittest.mock.Mock(returncode=0, stdout=out + "\n", stderr="")
        with unittest.mock.patch.multiple(roost, VENV=self.dir / "venv"), unittest.mock.patch.object(roost.subprocess, "run", run):
            self.assertIn("Installed Textual", roost.install_textual())
            self.assertEqual(calls, [["-m", "venv"], ["check"], ["-m", "pip"]])
            self.assertIn("is in", roost.install_textual())  # the venv is there and current: nothing to do
            self.assertEqual(len(calls), 4)

    def test_already_latest(self):
        me = self.dir / "roost"; me.write_text("old")
        roost.latest_release = lambda: "2026.10.03"
        self.assertEqual(roost.self_update(me), 0)
        self.assertEqual(me.read_text(), "old")


class Osa(unittest.TestCase):
    def test_a_closed_session_opens_where_it_was_started_whatever_the_terminal_says(self):
        """Ghostty runs the direct form in the folder its working-directory setting names, which can be
        home, so that command cds into the Session's folder itself."""
        import subprocess, unittest.mock
        with tempfile.TemporaryDirectory() as start, tempfile.TemporaryDirectory() as later, \
                tempfile.TemporaryDirectory() as elsewhere, unittest.mock.patch.dict(os.environ, {"SHELL": "/bin/sh"}):
            s = dict(session("Printer fix", state="closed", kind=None), id=SID, home=start, cwd=later)
            ran = []
            with unittest.mock.patch.object(roost, "osa", lambda script, *args: ran.append(args) or "ok"):
                roost.open_in_window(s, "ghostty")
            folder, _, direct = ran[0]
            self.assertEqual(folder, start)
            line = direct.replace(f"claude --resume {SID}", "pwd -P").replace("exec /bin/sh -l", "true")
            out = subprocess.run(["/bin/sh", "-c", line], cwd=elsewhere, stdin=subprocess.DEVNULL,
                                 capture_output=True, text=True).stdout
            self.assertEqual(out.split("\x07")[-1].strip(), os.path.realpath(start))  # after the window title

    def test_a_script_that_hangs_is_a_failure_not_an_exception(self):
        import subprocess, unittest.mock
        def hang(*a, **kw):
            raise subprocess.TimeoutExpired("osascript", 10)
        with unittest.mock.patch.object(roost.subprocess, "run", hang), unittest.mock.patch.object(roost, "log"):
            self.assertIsNone(roost.osa("tell application \"Ghostty\" to activate"))


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


if __name__ == "__main__":
    unittest.main()


class Commands(unittest.TestCase):
    def setUp(self):
        self.saved = dict(roost.COMMANDS)

    def tearDown(self):
        roost.COMMANDS.clear(); roost.COMMANDS.update(self.saved)

    def test_help_lists_every_command_on_one_line(self):
        text = roost.usage()
        for name, c in roost.COMMANDS.items():
            self.assertEqual(sum(name in line and c.help in line for line in text.splitlines()), 1)
        self.assertNotIn("{commands}", text)

    def test_command_runs_with_its_flags(self):
        seen = []
        roost.COMMANDS["demo-cmd"] = roost.Command(
            "a test command", lambda p: p.add_argument("--n", type=int), lambda a: seen.append(a.n) or 3)
        with self.assertRaises(SystemExit) as done:
            roost.main(["demo-cmd", "--n", "7"])
        self.assertEqual((done.exception.code, seen), (3, [7]))

    def test_command_help_gives_its_flags(self):
        import contextlib, io
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as done:
            roost.run_command("setup", ["--help"])
        self.assertEqual(done.exception.code, 0)
        self.assertIn("usage: roost setup", out.getvalue())

    def test_setup_and_update_dispatch_as_before(self):
        saved = roost.setup, roost.self_update
        roost.setup, roost.self_update = (lambda only=None: 11), (lambda me=None: 12)
        try:
            for name, code in (("setup", 11), ("update", 12)):
                with self.assertRaises(SystemExit) as done:
                    roost.main([name])
                self.assertEqual(done.exception.code, code)
        finally:
            roost.setup, roost.self_update = saved


def act(ui, keys, rows, label):
    """Open the selected row's actions (enter) and pick the one whose label starts with label."""
    keys("enter", rows)
    opts = ui.choosing["options"]
    ui.choosing["sel"] = next(i for i, (l, _) in enumerate(opts) if l.startswith(label))
    return ui.keys_choice("enter")


def labels(ui, keys, rows):
    """The selected row's actions, as their labels."""
    keys("enter", rows)
    out = [l for l, _ in ui.choosing["options"]]
    ui.choosing = None
    return out


def names(text):
    """Each character of text as the key roost gets: a newline is enter, \\x1b escape, \\t tab."""
    return [roost.UI.CONTROL_KEYS.get(c, c) for c in text]


def typed(ui, text):
    """Type into the prompt at the bottom of the screen; a newline is enter."""
    out = None
    for k in names(text):
        out = ui.keys_ask(k)
    return out


class Tabs(unittest.TestCase):
    """The tab bar, using a UI built without a screen and two stand-in tabs."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = roost.CONFIG
        roost.CONFIG = Path(self.tmp.name) / "config.json"
        self.got = []
        tab = lambda name: roost.Tab(name, lambda ui: None, lambda ui, k, d: self.got.append((name, k)),
                                     [(name, f"{name} keys")], lambda ui: ui.typing)
        self.ui = object.__new__(roost.UI)
        self.ui.TABS = [tab("Sessions"), tab("Projects")]
        self.ui.tab, self.ui.tab_x, self.ui.helping, self.ui.typing, self.ui.msg = 0, [(11, 21, 0), (22, 32, 1)], False, False, ""
        self.ui.choosing, self.ui.confirm, self.ui.summaries, self.ui.quit_anyway = None, None, {}, False
        self.ui.load_projects = lambda: None
        self.ui.asking, self.ui.searching, self.ui.query, self.ui.pquery = None, False, "", ""

    def tearDown(self):
        roost.CONFIG = self.saved
        self.tmp.cleanup()

    def test_projects_is_the_home_tab(self):
        self.assertEqual([t.name for t in roost.UI.TABS], ["Projects", "Sessions", "Notes", "Stats"])

    def test_switching_wraps_and_is_remembered(self):
        self.ui.dispatch("tab", None)
        self.assertEqual((self.ui.tab, roost.read_config()["tab"]), (1, "Projects"))
        self.ui.dispatch("tab", None)
        self.assertEqual(self.ui.tab, 0)
        self.assertEqual(roost.start_tab(["Sessions", "Projects"]), 0)
        self.ui.dispatch("shift+tab", None)
        self.assertEqual(roost.start_tab(["Sessions", "Projects"]), 1)

    def test_tab_key_switches_tabs(self):
        self.ui.dispatch("tab", None)
        self.assertEqual(self.ui.tab, 1)
        self.ui.dispatch("shift+tab", None)
        self.assertEqual((self.ui.tab, self.got), (0, []))

    def test_start_tab_falls_back_when_the_tab_is_gone(self):
        roost.save_config(tab="Tasks")
        self.assertEqual(roost.start_tab(["Sessions"]), 0)

    def test_clicking_a_tab_name_switches(self):
        self.ui.dispatch(("mouse", 25, 0, roost.curses.BUTTON1_CLICKED), None)
        self.assertEqual(self.ui.tab, 1)
        self.assertEqual(self.got, [])

    def test_other_keys_go_to_the_current_tab_only(self):
        self.ui.dispatch("j", None)
        self.ui.switch_tab(1)
        self.ui.dispatch("j", None)
        self.ui.dispatch(("mouse", 5, 6, roost.curses.BUTTON1_CLICKED), None)
        self.assertEqual(self.got, [("Sessions", "j"), ("Projects", "j"), ("Projects", ("mouse", 5, 6, roost.curses.BUTTON1_CLICKED))])

    def test_a_confirm_is_answered_on_whatever_tab_is_open(self):
        self.ui.switch_tab(1)
        self.ui.confirm = ("note", {"name": "x", "path": "/nowhere"})  # as a background run puts one up
        self.ui.dispatch("n", None)
        self.assertEqual((self.ui.confirm, self.ui.msg, self.got), (None, "Cancelled", []))
        self.ui.dispatch("j", None)  # the next key is the tab's again
        self.assertEqual(self.got, [("Projects", "j")])

    def test_a_prompt_gets_the_shared_keys_too(self):
        self.ui.typing = True
        for k in names("\t?q/"):
            self.ui.dispatch(k, None)
        self.assertEqual((self.ui.tab, self.ui.helping, self.ui.searching), (0, False, False))
        self.assertEqual([k for _, k in self.got], names("\t?q/"))

    def test_ctrl_q_quits_and_help_shows_the_current_tab(self):
        self.assertIsNot(self.ui.dispatch("q", None), roost.QUIT)  # a plain q is just a letter, for the tab
        self.assertEqual(self.got.pop(), ("Sessions", "q"))
        self.assertIs(self.ui.dispatch("ctrl+q", None), roost.QUIT)
        self.ui.switch_tab(1)
        self.ui.dispatch("?", None)
        self.assertTrue(self.ui.helping)
        names = [a for a, _ in self.ui.help_rows()]
        self.assertIn("Projects", names)
        self.assertIn("Actions", names)
        self.assertNotIn("Sessions", names)
        self.ui.dispatch("x", None)  # any key closes the keys overlay, and goes nowhere else
        self.assertEqual((self.ui.helping, self.got), (False, []))

    def test_every_tab_shares_one_set_of_keys(self):
        self.assertEqual([a for a, _ in roost.UI.GLOBAL_HELP], ["Move", "Views", "Actions", "Search", "New", "Tabs", "Close"])
        self.assertEqual(roost.BAR_KEYS, "↑↓ move   ←→ views   enter actions   / search   ctrl+n new   tab switch tab   ? help   ctrl+q quit")
        for tab in roost.UI.TABS:  # a tab's own help explains its lists; it adds no keys
            self.assertFalse([b for _, b in tab.help if re.search(r"(^|· )[a-zA-Z] ", b)], tab.name)

    def test_slash_searches_the_current_tab_and_esc_clears_it_without_quitting(self):
        self.ui.switch_tab(1)
        self.ui.dispatch("/", None)
        for k in names("ab\n"):
            self.ui.dispatch(k, None)
        self.assertEqual((self.ui.pquery, self.ui.query, self.ui.searching), ("ab", "", False))
        self.assertIsNone(self.ui.dispatch("escape", None))
        self.assertEqual(self.ui.pquery, "")
        self.assertIsNone(self.ui.dispatch("escape", None))  # esc never quits
        self.assertEqual(self.got, [])

    def test_a_prompt_takes_every_key_until_enter_or_esc(self):
        done = []
        self.ui.ask("Name: ", "x", lambda text: done.append(text))
        for k in names("\tq?y"):
            self.ui.dispatch(k, None)
        self.ui.dispatch("enter", None)
        self.assertEqual((done, self.ui.tab, self.got), (["x\tq?y".replace("\t", "")], 0, []))
        self.ui.ask("Name: ", "x", lambda text: done.append(text))
        self.ui.dispatch("escape", None)
        self.assertEqual((len(done), self.ui.asking, self.ui.msg), (1, None, "Cancelled"))


README = """---
category: vendor-reviews
summary: "Two password managers compared: cost and rollout."
status: active
sessions:
  - name: Password Manager Review
    id: aa1b9a71-0000
  - name: Second look
    id: bb2c0000-0000
---

# Title
"""


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

    def test_a_click_on_a_projects_arrow_opens_and_closes_it(self):
        self.project("b", handoffs=[time.strftime("%Y-%m-%d-%H%M") + "-aaaaaaaa.md"])
        ui = self.ui()
        ui.sessions = [dict(session("Password Manager Review"), id="aa1b9a71-0000")]
        ui.load_projects()
        ui.prow_y = {6: 1}  # the project's row, as draw left it
        click = ("mouse", 3, 6, roost.curses.BUTTON1_CLICKED)
        ui.keys_projects(click, ui.project_rows())
        self.assertEqual(ui.expanded, {"b"})
        ui.keys_projects(click, ui.project_rows())  # a second click on ▾ closes it; it's no double-click
        self.assertEqual((ui.expanded, ui.choosing), (set(), None))
        ui.keys_projects(("mouse", 20, 6, roost.curses.BUTTON1_CLICKED), ui.project_rows())  # the name only selects it
        self.assertEqual(ui.expanded, set())

    def test_wrapped_up_sessions_fold_into_one_line(self):
        """A closed Session whose newest Handoff covers its last message folds into "N wrapped up"."""
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
        self.assertEqual(self.shown(ui), [("head", "Recent"), ("project", "b"), ("linked", "bb2c0000-0000"),
                                          ("wrapped", "b wrapped up"), ("new", "+ New project")])
        ui.psel = 3
        ui.keys_projects("enter", ui.project_rows())
        self.assertIn(("linked", "aa1b9a71-0000"), self.shown(ui))
        ui.keys_projects("left", ui.project_rows())
        self.assertNotIn(("linked", "aa1b9a71-0000"), self.shown(ui))
        ui.keys_projects("left", ui.project_rows())  # ← again closes the project
        self.assertEqual(ui.expanded, set())

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

    def test_enter_expands_a_project_into_its_sessions(self):
        self.project("b", handoffs=[time.strftime("%Y-%m-%d-%H%M") + "-aaaaaaaa.md"])
        ui = self.ui()
        ui.sessions = [dict(session("Mine"), id="aa1b9a71-0000"), dict(session("Old one", state="closed", kind=None, mins=60), id="bb2c0000-0000"),
                       session("Loose", state="waiting")]
        ui.load_projects()
        ui.psel = 1
        act(ui, ui.keys_projects, ui.project_rows(), "Show its Sessions")
        rows = ui.project_rows()
        self.assertEqual([(r.kind, r.depth, r.item.get("name")) for r in rows[1:4]],
                         [("project", 0, None), ("linked", 1, "Mine"), ("linked", 1, "Old one")])
        act(ui, ui.keys_projects, rows, "Hide its Sessions")
        self.assertEqual(ui.expanded, set())

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
            ui.expanded = {"p"}
            rows = ui.project_rows()
            ui.psel = next(i for i, r in enumerate(rows) if r.kind == "linked")
            acts = labels(ui, ui.keys_projects, rows)
            self.assertEqual(acts[0], "Start a fresh Session for Title")  # by its README's heading
            self.assertFalse([a for a in acts if a.startswith(("Open", "Go to"))])
            # the Sessions tab offers the same, and opening it directly is refused
            self.assertEqual(labels(ui, ui.keys_sessions, ui.sessions), acts)
            self.assertIsNone(ui.open_one(ui.sessions[0]))
            self.assertIn("fresh Session", ui.msg)
            self.assertEqual(opened, [])
        finally:
            roost.open_in_window = saved

    def test_stop_and_delete_are_actions_that_ask_first(self):
        self.project("p", handoffs=[time.strftime("%Y-%m-%d-%H%M") + "-aaaaaaaa.md"])
        ui = self.ui()
        did = []
        saved = roost.stop_session, roost.delete_session
        roost.stop_session, roost.delete_session = (lambda job: did.append(("stop", job))), (lambda s: did.append(("delete", s["name"])))
        try:
            ui.sessions = [dict(session("Live"), id="aa1b9a71-0000"), dict(session("Old", state="closed", kind=None, mins=60), id="bb2c0000-0000")]
            ui.load_projects()
            ui.expanded = {"p"}
            rows = ui.project_rows()
            live, old = [i for i, r in enumerate(rows) if r.kind == "linked"]
            ui.psel = 1
            self.assertFalse([a for a in labels(ui, ui.keys_projects, rows) if a.startswith(("Stop", "Delete"))])
            ui.psel = live
            act(ui, ui.keys_projects, rows, "Stop…")
            self.assertTrue(ui.projects_typing())
            ui.keys_confirm("n")  # any other key cancels
            self.assertEqual((did, ui.msg), ([], "Cancelled"))
            act(ui, ui.keys_projects, rows, "Stop…")
            ui.keys_confirm("y")
            ui.psel = old
            act(ui, ui.keys_projects, ui.project_rows(), "Delete to the Trash…")
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


class KeyBar(unittest.TestCase):
    def bar(self, width, keys, note=""):
        ui = object.__new__(roost.UI)
        ui.scr = type("Scr", (), {"getmaxyx": lambda self: (40, width)})()
        return ui.bar_lines(keys, note)

    def test_keys_wrap_between_keys_and_a_long_note_gets_its_own_row(self):
        keys = "↑↓ move   enter open   s start Session   n new project   q quit"
        self.assertEqual(self.bar(200, keys), [[keys, ""]])
        rows = self.bar(40, keys)
        self.assertEqual([k for k, _ in rows], ["↑↓ move   enter open", "s start Session   n new project", "q quit"])
        self.assertEqual(self.bar(40, keys, "Saved")[-1], ["q quit", "Saved"])
        self.assertEqual(self.bar(40, keys, "A message far too long to share a row")[-1], ["", "A message far too long to share a row"])


SID = "11111111-2222-3333-4444-555555555555"
PRICE_PAGE = """# Pricing

## Model pricing

| Model | Base input tokens | 5m cache writes | 1h cache writes | Cache hits and refreshes | Output tokens |
| :---- | :---- | :---- | :---- | :---- | :---- |
| Claude Opus 5.5 | $4 / MTok | $5 / MTok | $8 / MTok | $0.20 / MTok<sup>2</sup> | $20 / MTok |
| Claude Opus 4.1 ([retired](https://x)) | $15 / MTok | $18.75 / MTok | $30 / MTok | $1.50 / MTok | $75 / MTok |
| Claude Haiku 5.5 (for prompts up to 100,000 tokens) | $0.10 / MTok | $0.125 / MTok | $0.20 / MTok | $0.01 / MTok | $0.50 / MTok |
| Claude Haiku 5.5 (for prompts over 100,000 tokens) | $0.50 / MTok | $0.625 / MTok | $1 / MTok | $0.05 / MTok | $2.50 / MTok |

## Feature-specific pricing

### Fast mode pricing

| Model | Input | Output |
| --- | --- | --- |
| Claude Opus 5.5 | $8 / MTok | $40 / MTok |
| Claude Opus 5 / Claude Opus 4.8 | $10 / MTok | $50 / MTok |

### Batch processing

| Model | Batch input | Batch output |
| --- | --- | --- |
| Claude Opus 5.5 | $2 / MTok | $10 / MTok |
"""


def reply(ts, mid, usage, content=(), model="claude-opus-5-5"):
    return {"type": "assistant", "timestamp": ts, "sessionId": SID, "cwd": "/w",
            "message": {"id": mid, "model": model, "usage": usage, "content": list(content)}}


def result(ts, tid, text, error=False, stdout=None):
    rec = {"type": "user", "timestamp": ts, "sessionId": SID, "cwd": "/w",
           "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": text, "is_error": error}]}}
    if stdout is not None:
        rec["toolUseResult"] = {"stdout": stdout, "stderr": ""}
    return rec


def bash(tid, cmd):
    return {"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": cmd}}


USAGE = {"input_tokens": 10, "output_tokens": 1000, "cache_read_input_tokens": 100_000,
         "cache_creation_input_tokens": 2000, "cache_creation": {"ephemeral_1h_input_tokens": 2000},
         "speed": "standard", "inference_geo": "global"}
TRANSCRIPT = [
    {"type": "custom-title", "customTitle": "Printer fix", "sessionId": SID},
    {"type": "user", "timestamp": "2026-10-09T10:00:00.000Z", "sessionId": SID, "cwd": "/w", "message": {"content": "go"}},
    # one reply split over two records: its usage counts once
    reply("2026-10-09T10:01:00.000Z", "m1", USAGE, [{"type": "text", "text": "ok"}]),
    reply("2026-10-09T10:01:01.000Z", "m1", USAGE, [
        {"type": "tool_use", "id": "e1", "name": "Edit", "input": {"file_path": "/w/printers.md"}},
        {"type": "tool_use", "id": "e2", "name": "Write", "input": {"file_path": "/tmp/scratch.txt"}},
        bash("b1", "make test\nsecond line"), bash("b2", "git commit -m 'Fix printers'")]),
    result("2026-10-09T10:02:00.000Z", "b1", "Exit code 2\nFAILED", error=True),
    result("2026-10-09T10:02:01.000Z", "b2", "", stdout="[main abc1234def] Fix printers\n 1 file changed"),
    {"type": "pr-link", "prUrl": "https://github.com/o/r/pull/7", "timestamp": "2026-10-09T10:03:00.000Z"},
    {"type": "pr-link", "prUrl": "https://github.com/o/r/pull/7", "timestamp": "2026-10-09T10:03:01.000Z"},
    reply("2026-10-09T11:00:00.000Z", "m2", dict(USAGE, speed="fast"), [
        {"type": "tool_use", "id": "e3", "name": "Edit", "input": {"file_path": "/w/later.md"}}]),
]


def release_tarball(version, extra=()):
    """A GitHub-style release tarball: everything under one top folder, the plugin in plugin/."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        def add(name, data=b"", kind=tarfile.REGTYPE, link=""):
            info = tarfile.TarInfo(name); info.type = kind; info.linkname = link; info.size = len(data)
            tar.addfile(info, io.BytesIO(data) if kind == tarfile.REGTYPE else None)
        top = "dmcphearson-agent-roost-abc123"
        add(f"{top}/", kind=tarfile.DIRTYPE)
        add(f"{top}/bin/roost", b"#!/usr/bin/env python3\n")
        add(f"{top}/plugin/.claude-plugin/plugin.json", json.dumps({"name": "roost", "version": version}).encode())
        add(f"{top}/plugin/skills/pickup/SKILL.md", b"---\nname: pickup\n---\n")
        for e in extra:
            add(*e)
    return buf.getvalue()


class Plugin(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.saved = roost.PLUGIN, roost.download_tarball, roost.VERSION, roost.claude_plugin, os.environ.get("CLAUDE_CONFIG_DIR")
        roost.PLUGIN = self.home / ".roost" / "plugin"
        roost.PLUGIN.parent.mkdir()
        roost.VERSION = "2026.10.08"
        self.cfg = self.home / "cfg"; self.cfg.mkdir()
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.cfg)
        self.downloads = []
        roost.download_tarball = lambda version: self.downloads.append(version) or release_tarball(version)

    def tearDown(self):
        roost.PLUGIN, roost.download_tarball, roost.VERSION, roost.claude_plugin, cfg = self.saved
        if cfg is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = cfg
        self.tmp.cleanup()

    def enable(self, on=True):
        (self.cfg / "settings.json").write_text(json.dumps(
            {"model": "kept", "enabledPlugins": {"roost@roost": on},
             "extraKnownMarketplaces": {"roost": {"source": {"source": "directory", "path": str(roost.PLUGIN)}}}}))

    def test_install_writes_beside_then_swaps_the_link(self):
        out = roost.install_plugin("2026.10.08")
        self.assertEqual(out, "Installed roost plugin 2026.10.08")
        self.assertTrue(roost.PLUGIN.is_symlink())
        self.assertEqual(roost.plugin_version(), "2026.10.08")
        self.assertTrue((roost.PLUGIN / "skills" / "pickup" / "SKILL.md").is_file())
        self.assertFalse((roost.PLUGIN / "bin").exists())  # only plugin/ comes out of the tarball
        self.assertEqual(roost.install_plugin("2026.10.08"), "roost plugin 2026.10.08 is installed")
        self.assertEqual(len(self.downloads), 1)  # nothing to do the second time
        roost.install_plugin("2026.10.09")
        roost.install_plugin("2026.10.10")
        self.assertEqual(roost.plugin_version(), "2026.10.10")
        kept = sorted(d.name for d in roost.plugin_versions().iterdir() if not d.name.startswith("."))
        self.assertEqual(kept, ["2026.10.09", "2026.10.10"])  # the new one and the one it replaced
        self.assertFalse(any(d.name.endswith(".download") for d in roost.plugin_versions().iterdir()))

    def test_the_plugin_that_came_with_roost_is_copied_not_downloaded(self):
        bundled = self.home / "brew" / "plugin"
        (bundled / ".claude-plugin").mkdir(parents=True)
        (bundled / ".claude-plugin" / "plugin.json").write_text('{"version": "2026.10.08"}')
        (bundled / "skills" / "pickup").mkdir(parents=True)
        (bundled / "skills" / "pickup" / "SKILL.md").write_text("x")
        saved = roost.BUNDLED_PLUGIN
        roost.BUNDLED_PLUGIN = bundled
        try:
            self.assertEqual(roost.install_plugin("2026.10.08"), "Installed roost plugin 2026.10.08")
            self.assertEqual(self.downloads, [])
            self.assertTrue((roost.PLUGIN / "skills" / "pickup" / "SKILL.md").is_file())
            roost.install_plugin("2026.10.09")  # another version than the one it came with is downloaded
            self.assertEqual(self.downloads, ["2026.10.09"])
        finally:
            roost.BUNDLED_PLUGIN = saved

    def test_a_bad_download_leaves_the_old_plugin(self):
        roost.install_plugin("2026.10.08")
        roost.download_tarball = lambda version: release_tarball("2026.10.07")  # says the wrong version
        with self.assertRaisesRegex(RuntimeError, "not 2026.10.09"):
            roost.install_plugin("2026.10.09")
        roost.download_tarball = lambda version: b"not a tarball"
        with self.assertRaises(Exception):
            roost.install_plugin("2026.10.09")
        self.assertEqual(roost.plugin_version(), "2026.10.08")
        self.assertEqual(sorted(d.name for d in roost.plugin_versions().iterdir() if d.name != ".changed"), ["2026.10.08"])

    def test_only_plain_files_inside_plugin_are_unpacked(self):
        top = "dmcphearson-agent-roost-abc123"
        tarball = release_tarball("2026.10.08", extra=[
            (f"{top}/plugin/../../escaped.txt", b"x"),
            (f"{top}/plugin/skills/link", b"", tarfile.SYMTYPE, "/etc/passwd")])
        dest = self.home / "out"
        roost.extract_plugin(tarball, dest)
        self.assertFalse((self.home / "escaped.txt").exists())
        self.assertFalse((dest / "skills" / "link").exists())
        with self.assertRaisesRegex(RuntimeError, "no plugin/ folder"):
            roost.extract_plugin(self.tarball_without_plugin(), self.home / "x")

    def tarball_without_plugin(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            info = tarfile.TarInfo("top/bin/roost"); info.size = 1
            tar.addfile(info, io.BytesIO(b"x"))
        return buf.getvalue()

    def test_update_refuses_to_replace_a_dev_link(self):
        checkout = self.home / "checkout" / "plugin" / ".claude-plugin"
        checkout.mkdir(parents=True)
        (checkout / "plugin.json").write_text('{"version": "2026.10.01"}')
        roost.PLUGIN.symlink_to(checkout.parent)
        with self.assertRaisesRegex(RuntimeError, "development checkout"):
            roost.install_plugin("2026.10.08")
        self.assertEqual(Path(os.path.realpath(roost.PLUGIN)), Path(os.path.realpath(checkout.parent)))
        self.assertEqual(self.downloads, [])
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(roost.update_plugin("2026.10.08"), 1)
        self.assertIn("update it with git", out.getvalue())

    def test_update_says_once_that_running_sessions_need_a_reload(self):
        self.enable()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(roost.update_plugin("2026.10.08"), 0)
        self.assertNotIn("roost setup", out.getvalue())
        self.assertIn("/reload-plugins", roost.taken_plugin_change())
        self.assertEqual(roost.taken_plugin_change(), "")
        self.assertIn('"model": "kept"', (self.cfg / "settings.json").read_text())  # update never touches settings

    def test_update_asks_for_setup_when_claude_code_lacks_it(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(roost.update_plugin("2026.10.08"), 0)
        self.assertIn("run roost setup plugin", out.getvalue())
        self.assertFalse((self.cfg / "settings.json").exists())

    def test_startup_check_says_what_to_run_and_repairs_nothing(self):
        self.assertEqual(roost.plugin_problem(), "roost plugin missing: run roost update")
        roost.install_plugin("2026.10.07")
        self.assertEqual(roost.plugin_problem(), "roost plugin outdated (2026.10.07): run roost update")
        roost.install_plugin("2026.10.08")
        self.assertEqual(roost.plugin_problem(), "roost plugin isn't enabled in Claude Code: run roost setup")
        self.enable(False)
        self.assertIn("run roost setup", roost.plugin_problem())
        self.enable()
        self.assertEqual(roost.plugin_problem(), "")
        self.assertEqual(len(self.downloads), 2)  # the check itself never downloaded anything

    def claude_has(self, version):
        """Claude Code's record of the plugin copy it loads (its cache), as `claude plugin install` writes it."""
        (self.cfg / "plugins").mkdir(exist_ok=True)
        (self.cfg / "plugins" / "installed_plugins.json").write_text(json.dumps(
            {"version": 2, "plugins": {"roost@roost": [{"scope": "user", "version": version}]}}))

    def test_startup_check_notices_claude_codes_copy_is_behind(self):
        roost.install_plugin("2026.10.08")
        self.enable()
        self.claude_has("2026.10.07")
        self.assertEqual(roost.plugin_problem(), "Claude Code has roost plugin 2026.10.07: run roost update")
        self.claude_has("2026.10.08")
        self.assertEqual(roost.plugin_problem(), "")

    def test_update_brings_claude_codes_copy_up_too(self):
        self.enable()
        self.claude_has("2026.10.07")
        calls = []
        def cli(*args):
            calls.append(args)
            if args[0] == "update":
                self.claude_has("2026.10.08")
            return True, "ok"
        roost.claude_plugin = cli
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(roost.update_plugin("2026.10.08"), 0)
        self.assertEqual(calls, [("marketplace", "update", "roost"), ("update", "roost@roost", "--scope", "user")])
        self.assertIn("Claude Code now has roost plugin 2026.10.08", out.getvalue())
        calls.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            roost.update_plugin("2026.10.08")
        self.assertEqual(calls, [])  # already current: no claude calls

    def test_a_failed_claude_update_is_reported_but_does_not_fail_the_update(self):
        self.enable()
        self.claude_has("2026.10.07")
        roost.claude_plugin = lambda *args: (False, "network down")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(roost.update_plugin("2026.10.08"), 0)
        self.assertIn("Claude Code still has roost plugin 2026.10.07", out.getvalue())
        self.assertIn("network down", out.getvalue())

    def test_setup_registers_it_through_the_claude_cli_and_confirms(self):
        calls = []
        def cli(*args):
            calls.append(args)
            if args[0] == "install":
                self.enable()
            return True, "ok"
        roost.claude_plugin = cli
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(roost.setup_plugin(), 0)
        self.assertEqual(calls, [("marketplace", "add", str(roost.PLUGIN)), ("install", "roost@roost", "--scope", "user")])
        self.assertIn("roost@roost is enabled", out.getvalue())

    def test_setup_notices_a_lost_settings_write(self):
        roost.claude_plugin = lambda *args: (True, "ok")  # says ok, but settings don't show it
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(roost.setup_plugin(), 1)
        self.assertIn("don't list roost@roost as enabled", out.getvalue())

    def test_setup_says_blocked_by_org_policy_and_does_not_retry(self):
        calls = []
        roost.claude_plugin = lambda *args: calls.append(args) or (
            False, "Marketplace 'roost' is not allowed by your organization's strictKnownMarketplaces policy")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(roost.setup_plugin(), 1)
        self.assertIn("blocked by org policy", out.getvalue())
        self.assertEqual(len(calls), 1)

    def test_a_session_gets_plugin_dir_until_claude_code_has_it(self):
        with self.assertRaisesRegex(ValueError, "run roost setup"):
            roost.plugin_args()
        roost.install_plugin("2026.10.08")
        self.assertEqual(roost.plugin_args(), ["--plugin-dir", str(roost.PLUGIN)])
        self.enable()
        self.assertEqual(roost.plugin_args(), [])


class PluginRelease(unittest.TestCase):
    root = Path(__file__).resolve().parent.parent

    def test_the_plugin_carries_roost_s_version(self):
        manifest = json.loads((self.root / "plugin" / ".claude-plugin" / "plugin.json").read_text())
        self.assertEqual(manifest["name"], "roost")
        self.assertEqual(manifest["version"], roost.VERSION)  # bump both in the release PR

    def test_the_plugin_folder_is_its_own_marketplace(self):
        mkt = json.loads((self.root / "plugin" / ".claude-plugin" / "marketplace.json").read_text())
        self.assertEqual(mkt["name"], "roost")
        self.assertEqual([(p["name"], p["source"]) for p in mkt["plugins"]], [("roost", "./")])

    def test_headless_runs_load_the_plugin_and_managed_keys_are_left_alone(self):
        src = "".join(p.read_text() for p in sorted((self.root / "roost").rglob("*.py")))
        for line in src.splitlines():
            if re.search(r'"-p"|"--print"', line):
                self.assertIn("--plugin-dir", line)
        for key in ("awsAuthRefresh", "modelOverrides", "CLAUDE_CODE_USE_BEDROCK"):
            self.assertNotIn(key, src)
        managed = [l for l in src.splitlines() if "managed-settings" in l or "RETENTION_SETTINGS" in l]
        self.assertTrue(managed)  # roost reads managed-settings.json for retention, and only reads it
        self.assertFalse([l for l in managed if re.search(r"write|\"w\"|replace|unlink", l)])
        self.assertIsNone(re.search(r'settings\.json"\)\.write|settings\.json"\), "w"', src))


class Handoffs(unittest.TestCase):
    """#50: roost handoff writes facts from the transcript plus a summary; roost ledger prints the facts."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.saved = (roost.CONFIG, roost.DONE, roost.RECAPS, roost.PROJECTS, roost.PRICES,
                      roost.urllib.request.urlopen, roost.LOG, roost.NOTES)
        roost.CONFIG, roost.DONE, roost.RECAPS, roost.NOTES = base / "config.json", base / "_done", base / "sessions", base / "notes"
        roost.PROJECTS, roost.PRICES, roost.LOG = base / "transcripts", base / "prices.json", base / "roost.log"
        self.fetches = []
        roost.urllib.request.urlopen = self.urlopen
        self.page = PRICE_PAGE
        self.root = base / "projects"
        self.root.mkdir()
        roost.save_config(projects_dir=str(self.root))
        self.folder = self.root / "printers"
        (self.folder / "handoffs").mkdir(parents=True)
        (self.folder / "README.md").write_text(f"---\nsummary: Printers\nsessions:\n  - name: Printer fix\n    id: {SID}\n---\n")
        self.transcript(TRANSCRIPT)

    def tearDown(self):
        (roost.CONFIG, roost.DONE, roost.RECAPS, roost.PROJECTS, roost.PRICES,
         roost.urllib.request.urlopen, roost.LOG, roost.NOTES) = self.saved
        self.tmp.cleanup()

    def urlopen(self, req, timeout=None):
        self.fetches.append(req.get_header("User-agent"))
        if self.page is None:
            raise OSError("offline")
        import io
        return io.BytesIO(self.page.encode())

    def transcript(self, records, sid=SID, sub=None):
        d = roost.PROJECTS / "-w"
        path = d / f"{sid}.jsonl" if sub is None else d / sid / "subagents" / f"{sub}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r) + "\n" for r in records))

    def handoff(self, summary="Fixed the printers.", now=None):
        return roost.write_handoff(SID, summary, now=now or time.mktime((2026, 10, 9, 16, 5, 0, 0, 0, -1)))

    # prices

    def test_prices_come_from_the_model_and_fast_mode_tables(self):
        t = roost.parse_prices(PRICE_PAGE)
        self.assertEqual(t["standard"]["claude-opus-5-5"], [[None, [4, 5, 8, 0.2, 20]]])
        self.assertIn("claude-opus-4-1", t["standard"])
        self.assertEqual([tier for tier, _ in t["standard"]["claude-haiku-5-5"]], [100_000, None])
        self.assertEqual(t["fast"], {"claude-opus-5-5": [8, 40], "claude-opus-5": [10, 50], "claude-opus-4-8": [10, 50]})
        self.assertNotIn("claude-opus-5-5", {k for k, v in t["standard"].items() if v[0][1][0] == 2})  # batch table ignored
        with self.assertRaises(ValueError):
            roost.parse_prices("# Pricing\n\nNothing here.")

    def test_reply_cost_at_list_prices(self):
        t = roost.parse_prices(PRICE_PAGE)
        u = {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "cache_read_input_tokens": 1_000_000,
             "cache_creation_input_tokens": 2_000_000,
             "cache_creation": {"ephemeral_5m_input_tokens": 1_000_000, "ephemeral_1h_input_tokens": 1_000_000}}
        self.assertAlmostEqual(roost.reply_cost("claude-opus-5-5", u, t), 4 + 5 + 8 + 0.2 + 20)
        self.assertAlmostEqual(roost.reply_cost("claude-opus-5-5", dict(u, inference_geo="us"), t), 37.2 * 1.1)
        self.assertAlmostEqual(roost.reply_cost("claude-opus-5-5", dict(u, speed="fast"), t), 2 * 37.2)
        small = {"input_tokens": 1000, "output_tokens": 0}
        self.assertAlmostEqual(roost.reply_cost("claude-haiku-5-5-20261001", small, t), 0.1 * 1000 / 1e6)
        self.assertAlmostEqual(roost.reply_cost("claude-haiku-5-5", {"input_tokens": 200_000}, t), 0.5 * 0.2)
        self.assertIsNone(roost.reply_cost("claude-sonnet-9", u, t))
        self.assertIsNone(roost.reply_cost("claude-opus-4-1", dict(u, speed="fast"), t))  # no fast price listed

    def test_prices_are_fetched_once_a_day_and_the_cache_covers_a_failed_fetch(self):
        first = roost.prices(now=1000)
        self.assertEqual((len(self.fetches), first["fetched"]), (1, 1000))
        self.assertTrue(self.fetches[0].startswith("roost/"))  # the site refuses Python's default agent
        roost.prices(now=1000 + 3600)
        self.assertEqual(len(self.fetches), 1)
        self.page = None
        self.assertEqual(roost.prices(now=1000 + 2 * 86400)["fetched"], 1000)
        roost.PRICES.unlink()
        self.assertIsNone(roost.prices(now=1000 + 2 * 86400))

    def test_a_failed_fetch_waits_an_hour_before_the_next(self):
        self.page = None
        self.assertIsNone(roost.prices(now=1000))
        self.assertIsNone(roost.prices(now=1000 + 60))
        self.assertEqual(len(self.fetches), 1)
        self.assertIsNone(roost.prices(now=1000 + 3700))
        self.assertEqual(len(self.fetches), 2)
        self.page = PRICE_PAGE
        roost.PRICES.unlink()
        roost.prices(now=5000)
        self.page = None
        roost.prices(now=5000 + 2 * 86400)
        self.assertEqual(roost.prices(now=5000 + 2 * 86400 + 60)["fetched"], 5000)  # the old copy, no new fetch
        self.assertEqual(len(self.fetches), 4)

    # facts

    def test_facts_come_from_the_transcript(self):
        t = roost.parse_prices(PRICE_PAGE)
        f = roost.facts(SID, table=t)
        self.assertEqual(f["name"], "Printer fix")
        self.assertEqual((f["from"], f["to"]), ("2026-10-09T10:00:00.000Z", "2026-10-09T11:00:00.000Z"))
        self.assertEqual(f["models"], {"claude-opus-5-5": 2})
        self.assertEqual(f["tokens"], {"input": 20, "cache write": 4000, "cache read": 200_000, "output": 2000})
        self.assertEqual(f["files"], ["/w/printers.md", "/w/later.md"])
        self.assertEqual(f["failed"], [("make test", 2)])
        self.assertEqual(f["commits"], [("on main", "abc1234def", "Fix printers")])
        self.assertEqual(f["prs"], ["https://github.com/o/r/pull/7"])
        one = roost.reply_cost("claude-opus-5-5", USAGE, t)
        self.assertAlmostEqual(f["cost"], one * 3)  # the fast reply costs twice as much
        self.assertEqual(roost.facts(SID, table=t), f)

    def test_facts_since_a_time_and_subagents_count(self):
        self.transcript([reply("2026-10-09T10:30:00.000Z", "s1", USAGE)], sub="agent-1")
        f = roost.facts(SID, since="2026-10-09T10:05:00.000Z")
        self.assertEqual(f["from"], "2026-10-09T10:30:00.000Z")
        self.assertEqual(f["models"], {"claude-opus-5-5": 2})
        self.assertEqual((f["files"], f["failed"], f["commits"], f["prs"]), (["/w/later.md"], [], [], []))
        self.assertEqual(f["unpriced"], ["claude-opus-5-5"])  # no price table given

    def test_quiet_commits_are_read_from_git_in_the_commands_repo(self):
        calls = []
        saved = roost.subprocess.run
        roost.subprocess.run = lambda argv, **kw: calls.append(argv) or type(
            "R", (), {"returncode": 0, "stdout": "fed9876\tQuiet commit\n"})()
        try:
            self.transcript([reply("2026-10-09T10:01:00.000Z", "m1", USAGE, [bash("q", "cd ~/repo && git commit -qm x")]),
                             result("2026-10-09T10:01:05.000Z", "q", "", stdout="")])
            f = roost.facts(SID)
        finally:
            roost.subprocess.run = saved
        self.assertEqual(f["commits"], [("in repo", "fed9876", "Quiet commit")])
        self.assertEqual(calls[0][:3], ["git", "-C", str(Path.home() / "repo")])

    # redaction

    def test_secrets_are_redacted_by_kind(self):
        pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----"
        text = (f"pk_12345678_ABCDEFGHIJKLMNOP ghp_{'a' * 36} github_pat_{'b' * 30} AKIAABCDEFGHIJKLMNOP "
                f"sk-ant-api03-{'c' * 30} xoxb-1234-5678-abcdef {pem} password=hunter2 "
                f"access_token=\"s3cr3t\" token=ghp_{'d' * 36} pk_id stays")
        out, kinds = roost.redact(text)
        for kind in ("pk", "github", "aws", "anthropic", "slack", "private-key", "password", "token"):
            self.assertIn(f"[REDACTED:{kind}]", out)
        self.assertNotIn("hunter2", out)
        self.assertNotIn("s3cr3t", out)
        self.assertNotIn("MIIabc", out)
        self.assertIn("token=[REDACTED:github]", out)  # not redacted twice
        self.assertIn("pk_id stays", out)
        self.assertEqual(sorted(kinds), sorted(["pk", "github", "github", "github", "aws", "anthropic", "slack",
                                                "private-key", "password", "token"]))
        okta = "00" + "Ab3_x-" * 6 + "Zz9q"  # an Okta API token: 00 and 40 more
        out, kinds = roost.redact(f"pasted {okta} here, but a sha {'0' * 40} and 00{'a' * 41} stay")
        self.assertEqual((out.count("[REDACTED:okta]"), kinds), (1, ["okta"]))
        self.assertNotIn(okta, out)

    # roost handoff

    def test_handoff_writes_facts_and_summary_and_never_overwrites(self):
        path, kinds = self.handoff(f"Fixed it. The key was ghp_{'a' * 36}.")
        self.assertEqual(path, self.folder / "handoffs" / "2026-10-09-1605-11111111.md")
        text = path.read_text()
        meta = roost.frontmatter(text)
        self.assertEqual((meta["session"], meta["name"], meta["to"]), (SID, "Printer fix", "2026-10-09T11:00:00.000Z"))
        self.assertIn("## Facts", text)
        self.assertIn("- **Failed commands (1):**\n  - `make test` (exit 2)", text)
        self.assertIn("- **Cost:** about $", text)
        self.assertIn("## Summary\n\nFixed it. The key was [REDACTED:github].", text)
        self.assertEqual(kinds, ["github"])
        with self.assertRaisesRegex(ValueError, "never overwrites"):
            self.handoff("Again.")
        self.assertEqual(path.read_text(), text)
        self.assertEqual([p.name for p in (self.folder / "handoffs").iterdir()], [path.name])

    def test_a_later_handoff_covers_only_what_came_after(self):
        self.handoff()
        self.transcript(TRANSCRIPT + [reply("2026-10-09T17:00:00.000Z", "m3", USAGE, [
            {"type": "tool_use", "id": "e9", "name": "Edit", "input": {"file_path": "/w/new.md"}}])])
        path, _ = self.handoff("More.", now=time.mktime((2026, 10, 9, 17, 30, 0, 0, 0, -1)))
        text = path.read_text()
        self.assertIn("- **Files changed (1):**\n  - `/w/new.md`", text)
        self.assertIn("claude-opus-5-5 (1 reply)", text)
        path, _ = self.handoff("Nothing new.", now=time.mktime((2026, 10, 9, 18, 0, 0, 0, 0, -1)))
        self.assertIn("no activity since its last Handoff", path.read_text())

    def test_handoff_refusals(self):
        with self.assertRaisesRegex(ValueError, "no summary"):
            self.handoff("  \n")
        with self.assertRaisesRegex(ValueError, "isn't a Session id"):
            roost.write_handoff("*", "x")
        with self.assertRaisesRegex(ValueError, "no transcript"):
            roost.write_handoff("99999999-2222-3333-4444-555555555555", "x")
        (self.folder / "README.md").write_text("---\nsummary: Printers\nsessions: []\n---\n")
        with self.assertRaisesRegex(ValueError, "no project lists Session 11111111"):
            self.handoff()
        self.assertEqual(list((self.folder / "handoffs").iterdir()), [])

    def test_offline_handoff_says_cost_unknown(self):
        self.page = None
        path, _ = self.handoff()
        self.assertIn("- **Cost:** unknown: roost couldn't fetch Anthropic's prices", path.read_text())

    # roost ledger, and last worked

    def test_ledger_prints_every_handoffs_facts_oldest_first(self):
        self.handoff("Summary one.", now=time.mktime((2026, 10, 9, 16, 5, 0, 0, 0, -1)))
        (self.folder / "handoffs" / "2026-10-01-0900-aaaaaaaa.md").write_text("Written by hand.\n")
        text = roost.ledger(self.folder)
        self.assertTrue(text.startswith("# Ledger of printers: 2 Handoffs, oldest first"))
        self.assertLess(text.index("2026-10-01-0900-aaaaaaaa.md"), text.index("2026-10-09-1605-11111111.md"))
        self.assertIn("(No Facts section", text)
        self.assertIn("- **Session:** Printer fix", text)
        self.assertNotIn("Summary one.", text)
        self.assertIn("No Handoffs yet.", roost.ledger(self.root / "missing"))

    def test_last_worked_uses_a_newer_handoff(self):
        (self.folder / "handoffs" / "2026-10-09-1605-11111111.md").write_text("x")
        p = roost.read_project(self.folder, [{"id": SID, "ids": [SID], "last": time.mktime((2026, 10, 8, 9, 0, 0, 0, 0, -1))}])
        self.assertEqual(p["last"], time.mktime((2026, 10, 9, 16, 5, 0, 0, 0, -1)))
        p = roost.read_project(self.folder, [{"id": SID, "ids": [SID], "last": time.mktime((2026, 10, 10, 9, 0, 0, 0, 0, -1))}])
        self.assertEqual(p["last"], time.mktime((2026, 10, 10, 9, 0, 0, 0, 0, -1)))


DRAFT = """### What I did
- Fixed the printers [3]
### What worked
- Restarting them [4, 5]
### What failed and why
- make test failed: missing driver [6]
### Decisions
- None.
### Open questions
- None.
### Next
- Check the second office [state]"""


class TranscriptSummary(unittest.TestCase):
    """#52: the Handoff and README update from a transcript, without resuming the Session."""
    setUp, tearDown, urlopen, transcript = Handoffs.setUp, Handoffs.tearDown, Handoffs.urlopen, Handoffs.transcript

    def run_summary(self, replies=None, cap=5.0, fail_at=None, during=None):
        """summarize() with a stand-in for the model: each call gets the next reply and costs $1.
        during(n) runs while call n is out, standing in for someone working meanwhile."""
        replies = list(replies or [DRAFT, DRAFT + "\n<!-- review: 0 changes: none -->",
                                   "# Printers\n\n## Current state\n\nFixed.\n\n## Next\n\nCheck the second office.\n"])
        self.calls, self.steps = [], []

        def call(model, system, prompt, budget):
            self.calls.append((model, prompt, budget))
            if fail_at == len(self.calls):
                raise ValueError("the model is down")
            if during:
                during(len(self.calls))
            return replies.pop(0), 1.0
        saved = roost.read_only
        roost.read_only = lambda cmd: "(stub)"
        try:
            return roost.summarize(SID, "claude-opus-5-5", cap, now=time.mktime((2026, 10, 9, 16, 5, 0, 0, 0, -1)), call=call,
                                   progress=self.steps.append)
        finally:
            roost.read_only = saved

    def test_the_conversation_is_numbered_without_reminders_or_thinking(self):
        self.transcript([
            {"type": "user", "timestamp": "2026-10-09T10:00:00.000Z", "message": {"content": "go <system-reminder>noise</system-reminder>"}},
            {"type": "assistant", "timestamp": "2026-10-09T10:00:01.000Z", "message": {"content": [
                {"type": "thinking", "thinking": "secret plan"}, {"type": "text", "text": "ok"}, bash("b1", "ls")]}},
            result("2026-10-09T10:00:02.000Z", "b1", "x" * 5000 + " arn:aws:iam::123456789012:role/Admin"),
            {"type": "attachment", "timestamp": "2026-10-09T10:00:03.000Z", "attachment": {"type": "queued_command", "prompt": "also this", "origin": {"kind": "human"}}},
        ])
        entries, ids, _ = roost.conversation(next(roost.PROJECTS.glob(f"*/{SID}.jsonl")))
        self.assertEqual([e.split(":", 1)[0] for e in entries],
                         ["[1] USER", "[2] CLAUDE", "[3] TOOL CALL Bash", "[4] RESULT Bash", "[5] USER (sent while Claude worked)"])
        self.assertNotIn("noise", entries[0])
        self.assertFalse([e for e in entries if "secret plan" in e])
        self.assertIn("chars cut", entries[3])
        self.assertEqual(ids["arn:aws:iam::123456789012:role/Admin"], 4)  # kept although the output was cut
        later, _, _ = roost.conversation(next(roost.PROJECTS.glob(f"*/{SID}.jsonl")), since="2026-10-09T10:00:01.000Z")
        self.assertEqual(len(later), 2)

    def test_it_writes_the_handoff_and_the_readme_body_and_keeps_the_frontmatter(self):
        readme = self.folder / "README.md"
        readme.write_text(readme.read_text() + "\n# Printers\n\nOld state.\n")
        path, kinds, spent, updated = self.run_summary()
        self.assertTrue(updated)
        self.assertEqual(self.steps, [1, 2, 3])  # draft, review, README: the row shows which
        self.assertEqual(roost.UI.progress({"step": 2, "steps": 3, "started": time.time() - 100}), "2/3 · 1m40s")
        text = path.read_text()
        self.assertIn("## Facts", text)
        self.assertIn("- Fixed the printers\n", text)  # citations come out
        self.assertNotIn("[state]", text)
        self.assertNotIn("<!-- review", text)
        self.assertEqual((kinds, spent), ([], 3.0))
        self.assertTrue(readme.read_text().startswith(f"---\nsummary: Printers\nsessions:\n  - name: Printer fix\n    id: {SID}\n---\n"))
        self.assertIn("## Current state\n\nFixed.", readme.read_text())
        self.assertNotIn("Old state", readme.read_text())
        self.assertEqual([b for _, _, b in self.calls], [5.0, 4.0, 3.0])  # each call gets what's left of the cap
        self.assertIn("Old state", self.calls[2][1])  # the rewrite starts from the README body
        self.assertIn("[1] USER: go", self.calls[0][1])

    def test_a_readme_edited_during_the_rewrite_keeps_the_edit(self):
        readme = self.folder / "README.md"
        readme.write_text(readme.read_text() + "\n# Printers\n\nOld state.\n")

        def edit(n):
            if n == 3:  # the person edits the body while the rewrite is out
                readme.write_text(readme.read_text().replace("Old state.", "Ordered toner."))
        path, _, spent, updated = self.run_summary(during=edit)
        self.assertFalse(updated)
        self.assertTrue(path.exists())  # the Handoff is still written
        self.assertIn("Ordered toner.", readme.read_text())
        self.assertNotIn("## Current state", readme.read_text())
        self.assertEqual(spent, 3.0)

    def test_frontmatter_written_during_the_rewrite_doesnt_stop_it(self):
        readme = self.folder / "README.md"

        def record(n):
            if n == 3:  # roost records another Session meanwhile
                readme.write_text(readme.read_text().replace("sessions:\n", "sessions:\n  - name: Other\n    id: other\n", 1))
        _, _, _, updated = self.run_summary(during=record)
        self.assertTrue(updated)
        self.assertIn("id: other", readme.read_text())
        self.assertIn("## Current state", readme.read_text())

    def test_two_summaries_in_one_project_take_turns_on_the_readme(self):
        import threading
        other = "99999999-2222-3333-4444-555555555555"
        readme = self.folder / "README.md"
        readme.write_text(f"---\nsummary: Printers\nsessions:\n  - name: Printer fix\n    id: {SID}\n"
                          f"  - name: Toner\n    id: {other}\n---\n\n# Printers\n\nOld state.\n")
        self.transcript(TRANSCRIPT, sid=other)
        in_rewrite, prompts = threading.Event(), {}

        def model(name):
            def call(model, system, prompt, budget):
                if "Old state" in prompt or "## Current state" in prompt:  # the README rewrite
                    prompts[name] = prompt
                    if name == "first":
                        in_rewrite.set()
                        time.sleep(0.3)  # the second would read the README now, without a turn
                    return f"# Printers\n\n## Current state\n\n{name} done.\n", 1.0
                return DRAFT, 1.0
            return call
        saved = roost.read_only
        roost.read_only = lambda cmd: "(stub)"
        try:
            a = threading.Thread(target=roost.summarize, args=(SID, "m", 5.0), kwargs={"call": model("first")})
            a.start()
            in_rewrite.wait(5)
            _, _, _, updated = roost.summarize(other, "m", 5.0, call=model("second"))
            a.join(5)
        finally:
            roost.read_only = saved
        self.assertTrue(updated)
        self.assertIn("first done.", prompts["second"])  # the second rewrote what the first wrote
        self.assertIn("second done.", readme.read_text())
        self.assertEqual(len(list((self.folder / "handoffs").iterdir())), 2)

    def test_a_failure_writes_nothing(self):
        readme = (self.folder / "README.md").read_text()
        for kwargs in ({"fail_at": 2}, {"fail_at": 3}, {"cap": 1.5},
                       {"replies": [DRAFT, DRAFT, "# Printers\n\nNo sections."]}, {"replies": [DRAFT, "Just prose."]}):
            with self.assertRaises(ValueError):
                self.run_summary(**kwargs)
            self.assertEqual(list((self.folder / "handoffs").iterdir()), [], kwargs)
            self.assertEqual((self.folder / "README.md").read_text(), readme)

    def test_it_covers_only_what_came_after_the_last_handoff(self):
        self.run_summary()
        with self.assertRaisesRegex(ValueError, "nothing new since its last Handoff"):
            roost.summary_plan(SID)
        self.transcript(TRANSCRIPT + [
            {"type": "user", "timestamp": "2026-10-09T12:00:00.000Z", "message": {"content": "one more thing"}}])
        plan = roost.summary_plan(SID)
        self.assertEqual(plan["since"], "2026-10-09T11:00:00.000Z")
        self.assertEqual(plan["entries"], ["[1] USER: one more thing"])

    def test_with_no_project_it_writes_a_session_note_and_no_readme(self):
        (self.folder / "README.md").write_text("---\nsummary: Printers\n---\n")
        plan = roost.summary_plan(SID)
        self.assertEqual((plan["folder"], plan["project"]), (None, False))
        table = roost.parse_prices(PRICE_PAGE)
        self.assertLess(roost.summary_estimate(plan, "claude-opus-5-5", table),
                        roost.summary_estimate(dict(plan, project=True), "claude-opus-5-5", table))
        path, _, spent, updated = self.run_summary()
        self.assertEqual((path, spent, updated), (roost.NOTES / "printer-fix.md", 2.0, None))  # no README call
        self.assertEqual(len(self.calls), 2)

    def test_the_estimate_comes_from_the_input_size_and_list_prices(self):
        table = roost.parse_prices(PRICE_PAGE)
        plan = roost.summary_plan(SID)
        est = roost.summary_estimate(plan, "claude-opus-5-5", table)
        self.assertGreater(est, 0)
        self.assertLess(est, 1)
        self.assertIsNone(roost.summary_estimate(plan, "claude-sonnet-9", table))
        self.assertEqual(roost.summary_settings(), ("claude-sonnet-5-5", 5.0))
        roost.save_config(summary_model="claude-opus-5-5", summary_budget_usd=2)
        self.assertEqual(roost.summary_settings(), ("claude-opus-5-5", 2.0))

    def test_the_model_call_has_no_tools_and_a_budget(self):
        seen, out = [], {"stdout": json.dumps({"type": "result", "subtype": "success", "result": "hi", "total_cost_usd": 0.5})}
        saved = roost.subprocess.run

        def run(cmd, **kw):
            seen.append((cmd, kw))
            return type("R", (), {"returncode": 0, "stderr": "", **out})()
        roost.subprocess.run = run
        try:
            self.assertEqual(roost.claude_print("claude-sonnet-5-5", "sys", "prompt", 4.5), ("hi", 0.5))
            cmd, kw = seen[0]
            self.assertEqual(cmd[:2], ["claude", "-p"])
            for flag, value in (("--tools", ""), ("--max-budget-usd", "4.50"), ("--model", "claude-sonnet-5-5"),
                                ("--setting-sources", ""), ("--system-prompt", "sys")):
                self.assertEqual(cmd[cmd.index(flag) + 1], value)
            self.assertIn("--no-session-persistence", cmd)
            self.assertEqual(kw["input"], "prompt")
            out["stdout"] = json.dumps({"type": "result", "subtype": "error_max_budget_usd", "is_error": True})
            with self.assertRaisesRegex(ValueError, r"\$4.50"):
                roost.claude_print("claude-sonnet-5-5", "sys", "prompt", 4.5)
            out["stdout"] = "not json"
            with self.assertRaises(ValueError):
                roost.claude_print("claude-sonnet-5-5", "sys", "prompt", 4.5)
        finally:
            roost.subprocess.run = saved

    def test_up_to_three_summaries_run_at_once(self):
        ui = object.__new__(roost.UI)
        ui.msg, ui.confirm, ui.offer, ui.sessions = "", None, None, []
        ui.projects = [{"id": "printers", "sessions": [SID]}]
        job = lambda name, state="running": {"state": state, "name": name, "text": "", "step": 1, "steps": 3, "started": 0}
        mine = dict(session("Printer fix"), id=SID)
        saved = roost.prices
        roost.prices = lambda now=None: None
        try:
            ui.summaries = {"a": job("A"), "b": job("B"), "c": job("C", "done")}
            ui.ask_summary(mine)
            self.assertEqual(ui.confirm, ("summary", mine))  # two running: a third may start
            ui.confirm, ui.summaries["c"] = None, job("C")
            ui.ask_summary(mine)
            self.assertEqual((ui.msg, ui.confirm), ("3 summaries are running: wait for one", None))
            ui.summaries = {SID: job("Printer fix")}
            ui.ask_summary(mine)
            self.assertEqual((ui.msg, ui.confirm), ("Its summary is already running", None))
            ui.summaries = {"adopt:/p": job("Printers")}  # an adopt or a question still runs on its own
            ui.ask_summary(mine)
            self.assertEqual((ui.msg, ui.confirm), ("Wait for Printers's run to finish first", None))
            ui.summaries, ui.quit_anyway, ui.tab = {"a": job("A"), "b": job("B")}, False, 0
            ui.helping = ui.choosing = ui.asking = ui.searching = ui.confirm = None
            ui.TABS = [ui.TABS[0]._replace(typing=lambda ui: False)]
            ui.dispatch("ctrl+q", None)
            self.assertEqual(ui.msg, "2 summaries are running and would be lost: ctrl+q again to quit anyway")
            for m in ("3 summaries are running: wait for one", "Its summary is already running"):
                self.assertTrue(roost.REFUSED.match(m), m)
        finally:
            roost.prices = saved

    def test_the_key_refuses_working_and_window_sessions_and_stops_background_ones(self):
        ui = object.__new__(roost.UI)
        ui.summaries, ui.msg, ui.confirm, ui.offer, ui.sessions = {}, "", None, None, []
        ui.projects = [{"id": "printers", "sessions": [SID]}]
        mine = lambda **kw: dict(session("Printer fix", **kw), id=SID)
        for s, said in ((mine(state="busy"), "working"), (mine(kind="interactive"), "exit it there first"),
                        (mine(attached=True), "exit it there first")):
            ui.ask_summary(s)
            self.assertIn(said, ui.msg)
            self.assertIsNone(ui.confirm)
        stopped, ran = [], []
        saved = roost.stop_session, roost.summarize, roost.prices
        roost.stop_session, roost.prices = stopped.append, lambda now=None: None
        roost.summarize = lambda sid, model, cap, progress: progress(2) or ran.append((sid, model, cap)) or (self.folder / "handoffs" / "h.md", [], 0.4, False)
        ui.refresh = lambda: None
        try:
            s = dict(mine(), attach="job1")
            ui.ask_summary(s)
            self.assertEqual(ui.confirm, ("summary", s))
            self.assertEqual(ui.offer[:2], ("claude-sonnet-5-5", 5.0))
            ui.keys_confirm("y")
            for _ in range(100):
                if ui.summaries[SID]["state"] != "running":
                    break
                time.sleep(0.01)
            self.assertEqual(stopped, ["job1"])  # stopped before the summary ran
            self.assertEqual(ran, [(SID, "claude-sonnet-5-5", 5.0)])
            self.assertEqual(ui.summaries[SID]["state"], "done")
            self.assertIn("left the README alone: it was edited while the summary ran", ui.summaries[SID]["text"])
        finally:
            roost.stop_session, roost.summarize, roost.prices = saved


class SessionRecaps(unittest.TestCase):
    """#53: a Session with no project leaves a Session recap, which a fresh Session can pick up and
    which can grow into a project."""
    setUp, tearDown, urlopen, transcript = Handoffs.setUp, Handoffs.tearDown, Handoffs.urlopen, Handoffs.transcript

    def unlist(self):
        (self.folder / "README.md").write_text("---\nsummary: Printers\nsessions: []\n---\n")

    def at(self, hour, minute=0):
        return time.mktime((2026, 10, 9, hour, minute, 0, 0, 0, -1))

    def note(self, summary=DRAFT, hour=16, **kw):
        return roost.write_handoff(SID, summary, now=self.at(hour), recap=True, **kw)

    def test_a_wrapped_up_session_starts_fresh_from_its_recap(self):
        """After its recap, a closed Session is wrapped up: it never reopens, and a fresh Session picks up the recap."""
        self.unlist()
        self.note()  # covers it to 11:00
        ui = Projects.ui(self)
        ui.DIM, ui.status = 0, {}
        end = roost.stamp("2026-10-09T11:00:00.000Z")
        s = dict(session("Printer fix", state="closed", kind=None), id=SID, active=end)
        ui.sessions = [s]
        ui.load_projects()
        self.assertEqual(ui.status_cell(s)[0], "✓ wrapped up")
        acts = ui.session_actions(s)
        self.assertEqual(acts[0][0], "Start a fresh Session from its recap")
        self.assertFalse([l for l, _ in acts if l.startswith(("Open", "Restart"))])
        started = []
        ui.start = lambda pid, cwd, name=None: started.append((pid, cwd))
        acts[0][1]()
        self.assertEqual(started, [("printer-fix", "/tmp")])  # the recap's folder (/w) is gone, so where the Session worked
        self.assertTrue(ui.resume_refused(s))
        ui.YELLOW = ui.RED = 0
        self.assertTrue(ui.cost_line(dict(s, tokens=166_000, reply_at=end, ttl=3600))[0].startswith("wrapped up"))
        later = dict(s, active=end + 3600)  # it worked after its recap: that work isn't saved anywhere yet
        self.assertNotEqual(ui.status_cell(later)[0], "✓ wrapped up")
        self.assertTrue([l for l, _ in ui.session_actions(later) if l.startswith("Open")])

    def test_advice_reads_back_and_falls_back_to_keep(self):
        text = ("VERDICT: add-to-project\nPROJECT: printers\nFINISHED: no\nOPEN:\n- PR #59 needs review\n- none\n"
                "WARNINGS:\n- an API token was pasted\nWHY:\nIt wrote the project's README.\n")
        a = roost.parse_advice(text, ["printers"])
        self.assertEqual((a["verdict"], a["project"], a["finished"]), ("add-to-project", "printers", False))
        self.assertEqual((a["open"], a["warnings"], a["why"]), (["PR #59 needs review"], ["an API token was pasted"],
                                                                "It wrote the project's README."))
        self.assertEqual(roost.parse_advice(text, ["other"])["verdict"], "keep")  # a project it made up
        self.assertEqual(roost.parse_advice("VERDICT: shred it\nWHY:\nx")["verdict"], "keep")

    def test_ask_what_to_do_then_act_on_it(self):
        """One model call reads the Session with the facts code checked; its advice's key opens that step."""
        self.unlist()
        self.transcript(TRANSCRIPT)
        ui = Projects.ui(self)
        ui.DIM = ui.GREEN = ui.YELLOW = ui.RED = 0
        ui.status, ui.refresh = {}, lambda: None
        s = dict(session("Printer fix", state="closed", kind=None), id=SID, files=[str(self.folder / "README.md")])
        ui.sessions = [s]
        ui.load_projects()
        prompts = []
        sync = type("Sync", (), {"__init__": lambda self, target, daemon: setattr(self, "run", target),
                                 "start": lambda self: self.run()})
        saved = roost.claude_print, roost.threading.Thread
        roost.claude_print = lambda model, system, prompt, budget: prompts.append(prompt) or (
            "VERDICT: add-to-project\nPROJECT: printers\nFINISHED: yes\nOPEN:\n- none\nWARNINGS:\n- none\nWHY:\nIt fixed the printers.\n", 0.05)
        roost.threading.Thread = sync
        self.addCleanup(lambda: (setattr(roost, "claude_print", saved[0]), setattr(roost.threading, "Thread", saved[1])))
        act = dict(ui.session_actions(s))["Ask what to do with it…"]
        act()
        self.assertEqual(ui.confirm[0], "ask")
        self.assertIn("Ask what to do with Printer fix", ui.confirm_text()[0])
        ui.keys_confirm("y")
        self.assertIn("Projects whose folder it edited: printers", prompts[0])
        self.assertIn("Listed by: nothing", prompts[0])
        ui.report_summaries()
        c = ui.choosing  # the advice is a menu: what it found, then each choice spelled out
        self.assertEqual(c["title"], "What to do with Printer fix")
        self.assertTrue(c["body"][0].startswith("Finished. It fixed the printers."))
        self.assertEqual([l for l, _ in c["options"]],
                         ["Add it to printers, then write its Handoff (shows the cost first)  · advised",
                          "Only add it to printers", "Leave it as it is",
                          "Delete it: its history moves to the Trash (asks again)"])
        self.assertIsNone(ui.keys_choice("enter"))  # the advised one is selected
        self.assertEqual(roost.read_project(self.folder)["sessions"], [SID])  # added, and the Handoff asks next
        self.assertEqual(ui.confirm[0], "summary")

    def test_with_nothing_listing_it_a_handoff_needs_note(self):
        self.unlist()
        with self.assertRaisesRegex(ValueError, "--recap"):
            roost.write_handoff(SID, DRAFT)
        path, kinds = self.note()
        self.assertEqual(path, roost.NOTES / "printer-fix.md")
        text = path.read_text()
        meta = roost.frontmatter(text)
        self.assertEqual((meta["title"], roost.tag_list(meta["tags"]), meta["created"]), ("Printer fix", ["recap"], "2026-10-09"))
        [n] = roost.read_notes()  # on the Notes tab, with every note action
        self.assertEqual((n["name"], n["tags"], n["sessions"]), ("Printer fix", ["recap"], [SID]))
        self.assertEqual(meta["summary"], "Fixed the printers [3]")  # its first point, when none is given
        self.assertEqual(meta["sessions"], [{"name": "Printer fix", "id": SID}])
        self.assertEqual(meta["folder"], "/w")
        self.assertIn(f"<!-- roost handoff 2026-10-09-1600-11111111 session: {SID} from: 2026-10-09T10:00:00.000Z to: 2026-10-09T11:00:00.000Z -->", text)
        self.assertIn("## Facts", text)
        [n] = roost.read_recaps()
        self.assertEqual((n["id"], n["sessions"], n["folder"]), ("printer-fix", [SID], "/w"))
        self.assertEqual(roost.owner([SID]), path)

    def test_a_later_handoff_is_added_at_the_end_and_covers_only_what_came_after(self):
        self.unlist()
        path, _ = self.note("### What I did\n- First part\n### Next\n- More", recap_summary="Printer fix")
        first = path.read_text()
        self.transcript(TRANSCRIPT + [{"type": "user", "timestamp": "2026-10-09T12:00:00.000Z", "message": {"content": "again"}}])
        path2, _ = roost.write_handoff(SID, "### What I did\n- Second part", now=self.at(17))
        self.assertEqual(path2, path)
        text = path.read_text()
        self.assertTrue(text.startswith(first.rstrip("\n")))  # nothing above the end changes
        sections = roost.recap_sections(text)
        self.assertEqual([(s[0], s[2]) for s in sections],
                         [("2026-10-09-1600-11111111", "2026-10-09T10:00:00.000Z"), ("2026-10-09-1700-11111111", "2026-10-09T12:00:00.000Z")])
        with self.assertRaisesRegex(ValueError, "never overwrites"):
            roost.write_handoff(SID, "again", now=self.at(17))

    def test_old_recaps_move_into_notes_once(self):
        self.unlist()
        roost.RECAPS.mkdir(parents=True)
        old = roost.RECAPS / "printer-fix.md"
        original = (f"---\nsummary: Fixed it\nfolder: /w\nsessions:\n  - name: Printer fix\n    id: {SID}\n---\n\n"
                    f"# Session recap printer-fix\n\n<!-- roost handoff 2026-10-09-1600-11111111 session: {SID} from:  "
                    f"to: 2026-10-09T11:00:00.000Z -->\n\nDone.\n")
        old.write_text(original)
        (roost.RECAPS / "loose.md").write_text("Restarted two Macs.\n")  # written by hand, no frontmatter
        roost.NOTES.mkdir()
        (roost.NOTES / "printer-fix.md").write_text("my own note\n")
        self.assertEqual(roost.move_recaps(), 2)
        self.assertFalse(roost.RECAPS.exists())
        self.assertEqual((roost.NOTES / "printer-fix.md").read_text(), "my own note\n")  # never replaced
        moved = roost.NOTES / "printer-fix-2.md"
        text = moved.read_text()
        meta = roost.frontmatter(text)
        self.assertEqual((meta["title"], meta["summary"], meta["folder"], roost.tag_list(meta["tags"])),
                         ("Printer fix", "Fixed it", "/w", ["recap"]))
        self.assertIn("Done.", text)
        self.assertEqual(roost.owner([SID]), moved)  # the next wrap-up adds to it
        self.assertEqual(roost.last_covered(moved, [SID]), "2026-10-09T11:00:00.000Z")
        self.assertEqual(sorted(r["id"] for r in roost.read_recaps()), ["loose", "printer-fix-2"])
        self.assertIn("Restarted two Macs.", (roost.NOTES / "loose.md").read_text())
        self.assertEqual(roost.move_recaps(), 0)  # a second run changes nothing
        roost.RECAPS.mkdir()
        old.write_text(original)  # as if the last run stopped after writing the note, before removing this
        self.assertEqual(roost.move_recaps(), 1)
        self.assertFalse(old.exists())
        self.assertFalse((roost.NOTES / "printer-fix-3.md").exists())  # its note was already there

    def test_a_recap_on_the_notes_tab_becomes_a_project_with_its_handoffs(self):
        self.unlist()
        path, _ = self.note()
        path.write_text(path.read_text().replace('folder: "/w"', f'folder: "{self.root}"'))  # a folder that exists
        ui = object.__new__(roost.UI)
        ui.sessions, ui.msg, ui.page, ui.last_click = [], "", 10, (0, -1)
        ui.psel, ui.ptop, ui.prow_y, ui.asking = 0, 0, {}, None
        ui.pquery, ui.searching, ui.term, ui.plugin_ok = "", False, None, True
        ui.folded, ui.expanded, ui.unwrapped = set(), set(), set()
        ui.choosing = ui.confirm = None
        ui.summaries = {}
        ui.load_projects()
        [n] = ui.notes
        started = []
        ui.start = lambda pid, cwd, name=None: started.append(cwd)
        dict(ui.note_actions(n))["Start a Session from it"]()
        self.assertEqual(started, [str(self.root)])  # where its Session worked, not home
        del ui.start
        dict(ui.note_actions(n))["Make it a project…"]()
        typed(ui, "\n")
        typed(ui, "\n")
        self.assertEqual(ui.asking["text"], "Fixed the printers [3]")  # the recap's summary to start from
        typed(ui, "\n")
        self.assertIn("Created", ui.msg)
        self.assertEqual(len(list((self.root / "printer-fix" / "handoffs").iterdir())), 1)
        self.assertEqual(roost.read_recaps(), [])

    def test_notes_never_replace_each_other(self):
        self.unlist()
        roost.NOTES.mkdir(parents=True)
        (roost.NOTES / "printer-fix.md").write_text("someone else's note\n")
        path, _ = self.note()
        self.assertEqual(path.name, "printer-fix-2.md")
        self.assertEqual((roost.NOTES / "printer-fix.md").read_text(), "someone else's note\n")

    def test_pickup_and_starting_from_a_note_list_the_new_session_in_it(self):
        self.unlist()
        path, _ = self.note()
        other = "99999999-2222-3333-4444-555555555555"
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(roost.run_command("project", ["brief", "printer-fix", "--session", other]), 0)
        self.assertIn("Session recap printer-fix", out.getvalue())
        self.assertIn(other, roost.read_recaps()[0]["sessions"])
        saved = roost.plugin_args
        roost.plugin_args = lambda: []
        try:
            started = roost.start_session("printer-fix", "/w", None)
        finally:
            roost.plugin_args = saved
        self.assertEqual(started["start"][-1], "/roost:pickup printer-fix")
        self.assertEqual(len(roost.read_recaps()[0]["sessions"]), 3)
        self.assertEqual(roost.read_recaps()[0]["summary"], "Fixed the printers [3]")  # the rest of the note is untouched

    def test_a_note_becomes_a_project_with_its_handoffs_and_sessions(self):
        self.unlist()
        path, _ = self.note()
        self.transcript(TRANSCRIPT + [{"type": "user", "timestamp": "2026-10-09T12:00:00.000Z", "message": {"content": "again"}}])
        roost.write_handoff(SID, "### What I did\n- Second part", now=self.at(17))
        folder = roost.promote_recap(path, "printer-rollout", "Roll the new driver out to every Mac.")
        self.assertFalse(path.exists())
        handoffs = sorted((folder / "handoffs").iterdir())
        self.assertEqual([h.name for h in handoffs], ["2026-10-09-1600-11111111.md", "2026-10-09-1700-11111111.md"])
        meta = roost.frontmatter(handoffs[1].read_text())
        self.assertEqual((meta["session"], meta["name"], meta["to"]), (SID, "Printer fix", "2026-10-09T12:00:00.000Z"))
        self.assertIn("- Second part", handoffs[1].read_text())
        self.assertEqual(roost.read_project(folder)["sessions"], [SID])
        self.assertEqual(roost.owner([SID]), folder)
        self.assertEqual(roost.last_covered(folder, [SID]), "2026-10-09T12:00:00.000Z")  # the ledger carries on
        self.assertIn("2026-10-09-1700-11111111.md", roost.ledger(folder))
        with self.assertRaisesRegex(ValueError, "already exists"):
            roost.RECAPS.mkdir(exist_ok=True)
            (roost.RECAPS / "x.md").write_text("---\nsummary: x\n---\n\nHand-written.\n")
            roost.promote_recap(roost.RECAPS / "x.md", "printer-rollout", "Again.")
        self.assertTrue((roost.RECAPS / "x.md").exists())  # a failure keeps the note

    def test_a_crafted_marker_writes_nothing_outside_the_project(self):
        roost.RECAPS.mkdir()
        mark = "<!-- roost handoff %s session: s1 from:  to:  -->"
        (roost.RECAPS / "evil.md").write_text("\n".join(["---", "summary: x", "---", "",
                                                         mark % "2026-10-09-1600-ok", "Fine.", "",
                                                         mark % "../../../escaped", "Out.", "", mark % "..", "Dots.", ""]))
        folder = roost.promote_recap(roost.RECAPS / "evil.md", "evil", "Crafted.")
        self.assertEqual([h.name for h in (folder / "handoffs").iterdir()], ["2026-10-09-1600-ok.md"])
        self.assertFalse(list(folder.parent.parent.rglob("escaped.md")))
        self.assertEqual([s[0] for s in roost.recap_sections(mark % "a/b" + "\nx")], [])

    def test_a_hand_written_note_becomes_one_handoff(self):
        roost.RECAPS.mkdir()
        (roost.RECAPS / "vpn.md").write_text("---\nsummary: VPN client\n---\n\nInstalled the VPN client on two Macs.\n")
        folder = roost.promote_recap(roost.RECAPS / "vpn.md", "vpn-rollout", "VPN client everywhere.")
        [h] = (folder / "handoffs").iterdir()
        self.assertTrue(roost.handoff_time(h))
        self.assertIn("Installed the VPN client on two Macs.", h.read_text())

    def test_make_it_a_project_asks_for_an_id_and_summary_then_promotes_it(self):
        self.unlist()
        self.note()
        ui = object.__new__(roost.UI)
        s = dict(session("Printer fix", state="closed", kind=None), id=SID)
        ui.sessions, ui.msg, ui.page, ui.last_click = [s], "", 10, (0, -1)
        ui.psel, ui.ptop, ui.prow_y, ui.asking = 0, 0, {}, None
        ui.pquery, ui.searching, ui.term, ui.plugin_ok = "", False, None, True
        ui.folded, ui.expanded = set(), set()
        ui.unwrapped = set()
        ui.choosing = ui.confirm = None
        ui.summaries = {}
        ui.load_projects()
        self.assertEqual(ui.project_rows()[-1].kind, "new")  # recaps aren't on the Projects tab
        self.assertEqual([(n["id"], n["tags"]) for n in ui.notes], [("printer-fix", ["recap"])])  # it's a note, tagged recap
        self.assertEqual(ui.recap_of(s)["id"], "printer-fix")
        acts = dict(ui.session_actions(s))
        self.assertTrue(any(l.startswith("Read its Session recap") for l in acts))
        self.assertNotIn("New project from this Session…", acts)
        acts["Make it a project… (its recap's Handoffs move in)"]()
        self.assertEqual(ui.asking["text"], "Printer fix")
        typed(ui, "\n")
        self.assertEqual(ui.asking["text"], "printer-fix")
        typed(ui, "\n")
        self.assertEqual(ui.asking["text"], "Fixed the printers [3]")
        typed(ui, "\n")
        self.assertIn("Created", ui.msg)
        self.assertTrue((self.root / "printer-fix" / "handoffs").is_dir())
        self.assertEqual(roost.read_recaps(), [])


PLAN_BODY = """## Summary
Roll the new printer driver out to every office Mac.
## Status
active
## Moves
- `export.csv` -> `data/export.csv`
- `report.pdf` -> `out/report.pdf`
## Archive
- `notes-backup.md`: an older copy of notes.md
## Split out
- `badge-printers/` -> `badge-printers`: a separate rollout with its own finish line
## First Handoff
from: `HANDOFF.md`
### What I did
- Packaged the driver
### What worked
- None.
### What failed and why
- Nothing.
### Decisions
- None.
### Open questions
- None.
### Next
- Deploy to the second office
## README
# Printer rollout

## Current state

Driver packaged; first office done.

## Next

Deploy to the second office.

## Key decisions

## Files

- `notes.md`: working notes
"""


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
        self.assertEqual(meta["summary"], "Roll the new printer driver out to every office Mac.")
        self.assertEqual(meta["sessions"], [{"name": "Printer fix", "id": SID}])  # Sessions stay linked through their ids
        self.assertIn("Driver packaged", text)
        self.assertNotIn("Old notes.", text)
        split = self.root / "badge-printers"
        self.assertTrue((split / "plan.md").exists())
        self.assertIn("separate rollout", roost.frontmatter((split / "README.md").read_text())["summary"])
        # every file is still somewhere: moved, archived, or split out (plus the new README copy and Handoff)
        self.assertEqual(len(self.files(new, split)), before + 3)

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
        ui.nsel, ui.ntop, ui.nrow_y, ui.nquery, ui.quit_anyway = 0, 0, {}, "", False
        ui.nreading, ui.nscroll, ui.npane, ui.nlist_w, ui.TABS = False, 0, (4, 40, 10, 40), 200, roost.UI.TABS
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
        self.assertEqual([r["name"] for r in ui.note_rows()[:-1]], ["Laptop budget"])
        ui.nquery = ""
        ui.nsel = len(ui.note_rows()) - 1
        ui.keys_notes("enter", ui.note_rows())  # enter on + New note
        self.assertEqual(ui.asking["title"], "New note: its title")

    def test_making_a_project_from_a_note(self):
        ui = self.ui()
        roost.create_note("Printer rollout")
        ui.load_projects()
        ui.nsel = 0
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
        ui.nsel = 0
        rows = ui.note_rows()
        ui.keys_notes("right", rows)
        self.assertTrue(ui.nreading)
        ui.keys_notes("down", rows)
        self.assertEqual((ui.nsel, ui.nscroll), (0, 1))  # ↓ scrolls the text, not the list
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
        ui.nsel = 0
        keys = lambda k, rows: ui.keys_notes(k, ui.note_rows())
        self.assertEqual(labels(ui, keys, None)[1:], ["Start a Session from it", "Make it a project…", "Edit its tags…",
                                                      "Show it in Finder", "Delete to the Trash…"])
        self.project("p")
        ui.load_projects()
        act(ui, keys, None, "Move to a project")
        ui.keys_choice("enter")
        self.assertEqual(ui.note_rows()[1:], [])  # moved notes leave the scratchpad
        self.assertTrue((self.root / "p" / "notes" / "idea.md").exists())
        roost.create_note("Another")
        ui.load_projects()
        ui.nsel = 0
        act(ui, keys, None, "Delete to the Trash")
        ui.keys_confirm("y")
        self.assertEqual((len(self.trashed), ui.note_rows()[1:]), (1, []))

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


class ProjectArrows(unittest.TestCase):
    def test_right_opens_a_projects_sessions_and_left_closes_from_a_session(self):
        ui = object.__new__(roost.UI)
        p = {"id": "p", "path": "/p", "sessions": ["s1"], "closed": False}
        rows = [roost.Row("Recent", "head", {"id": "Recent"}, 0), roost.Row("Recent", "project", p, 0)]
        ui.expanded, ui.folded, ui.msg, ui.psel = set(), set(), "", 1
        ui.linked = lambda q: [{"id": "s1"}]
        ui.open_or_close(rows, True)
        self.assertEqual(ui.expanded, {"p"})
        rows.append(roost.Row("Recent", "linked", {"id": "s1"}, 1))
        ui.project_rows, ui.psel = (lambda: rows), 2
        ui.open_or_close(rows, False)
        self.assertEqual((ui.expanded, ui.psel), (set(), 1))

    def test_tags_are_read_from_a_list_or_a_line(self):
        self.assertEqual(roost.tag_list("[ideas, q4]"), ["ideas", "q4"])
        self.assertEqual(roost.tag_list(["a"]), ["a"])


@unittest.skipUnless(roost.App, "Textual isn't installed here: run the tests with ~/.roost/venv/bin/python")
class TextualScreens(unittest.TestCase):
    """The Textual front end, driven by Textual's pilot on made-up Sessions; no terminal or editor."""
    def setUp(self):
        import unittest.mock
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        stubs = dict(read_projects=lambda sessions: [], read_recaps=lambda: [], plugin_problem=lambda: "",
                     check_compat=lambda: "", plugin_registered=lambda s=None: True, taken_plugin_change=lambda: "",
                     retention=lambda: (30, None), update_available=lambda: "", chosen_terminal=lambda: "ghostty",
                     mark_open=lambda ss, term: ss, regather=lambda old: old, read_config=lambda: {},
                     save_config=lambda **kw: None, NOTES=Path(tmp.name) / "notes")
        patcher = unittest.mock.patch.multiple(roost, **stubs)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.sessions = [session("Printer fix", mins=5, tokens=41000), session("Laptop refresh", state="waiting")]

    def run_app(self, steps):
        """Run the app at 120×30, do each step with the pilot, and return the app."""
        import asyncio
        app = roost.RoostApp(self.sessions, "")

        async def go():
            async with app.run_test(size=(120, 30)) as pilot:
                await pilot.pause()
                for step in steps:
                    await step(pilot)
                    await pilot.pause()
                app.lines = screen_text(app)
        asyncio.run(go())
        return app

    @staticmethod
    def keys(*keys):
        return lambda pilot: pilot.press(*keys)

    def screen(self, app):
        return app.lines

    def test_a_wrap_up_shows_under_its_own_heading_at_the_bottom(self):
        done = dict(self.sessions[0], state="closed", kind=None, attach=None)  # stopped for its summary
        self.sessions = [done, self.sessions[1]]

        async def start(pilot):
            pilot.app.ui.summaries = {done["id"]: {"state": "running", "text": "writing", "name": done["name"],
                                                   "step": 1, "steps": 3, "started": time.time()}}
            await pilot.press("tab")
        lines = self.screen(self.run_app([start]))
        heads = [l.strip().split(" (")[0] for l in lines if "(1)" in l]
        self.assertEqual(heads[-1], "Wrapping up")
        self.assertLess(next(i for i, l in enumerate(lines) if "Laptop refresh" in l),
                        next(i for i, l in enumerate(lines) if "Printer fix" in l))

    def test_the_sessions_tab_names_each_sessions_project(self):
        import unittest.mock
        pid = "2026-10-printer-fleet"
        proj = {"id": pid, "path": "/nowhere/" + pid, "sessions": [self.sessions[0]["id"]], "summary": "", "status": "active",
                "category": "", "tags": [], "last": None, "closed": False, "readme": True}
        with unittest.mock.patch.object(roost, "read_projects", lambda sessions: [proj]):
            app = self.run_app([self.keys("tab")])
        lines = self.screen(app)
        self.assertEqual(app.ui.TABS[app.ui.tab].name, "Sessions")
        self.assertIn("PROJECT", next(l for l in lines if "NAME" in l))
        self.assertIn(pid, next(l for l in lines[:-10] if "Printer fix" in l))
        self.assertNotIn(pid, next(l for l in lines[:-10] if "Laptop refresh" in l))
        panel = "\n".join(lines[-10:])
        self.assertRegex(panel, r"Project +(2026-10-printer-fleet|none)")
        self.assertIn("Context", panel)

    def test_it_draws_the_same_frame_and_tab_switches_tabs(self):
        app = self.run_app([self.keys("tab")])
        lines = self.screen(app)
        self.assertTrue(lines[0].startswith(" 🐓 roost   Projects   Sessions   Notes"))
        self.assertEqual(app.ui.TABS[app.ui.tab].name, "Sessions")
        self.assertIn("Printer fix", "\n".join(lines))
        self.assertIn("ctrl+q quit", lines[-1])

    def test_a_confirmation_is_a_box_with_its_keys(self):
        p = {"id": "2026-10-a-project-with-a-long-enough-name-to-crowd-the-row", "path": "/nowhere/x"}

        async def ask(pilot):
            ui = pilot.app.ui
            ui.offer, ui.confirm = ("claude-sonnet-5-5", 5.0, 0.08, None), ("adopt", p)
            pilot.app.redraw()
        app = self.run_app([ask])
        shown = "\n".join(self.screen(app))  # a box in the middle of the screen, keys and all
        for part in ("Plan how to adopt 2026-10-a-project", "about $0.08 with claude-sonnet-5-5", "y to plan · any other key cancels"):
            self.assertIn(part, shown)

    def test_a_failure_is_a_box_until_a_key(self):
        async def fail(pilot):
            pilot.app.ui.msg = "Couldn't move it: [Errno 13] Permission denied: '/x'"
            pilot.app.redraw()

        async def done(pilot):
            pilot.app.ui.msg = "Moved it to the Trash"
            pilot.app.redraw()
        app = self.run_app([fail])
        shown = "\n".join(self.screen(app))
        for part in ("Couldn't move it", "[Errno 13] Permission denied: '/x'", "any key closes"):
            self.assertIn(part, shown)
        self.assertFalse(self.screen(app)[-1].rstrip().endswith("Permission denied: '/x'"))  # not the corner note
        app = self.run_app([fail, self.keys("down")])
        self.assertIsNone(app.ui.alerting)
        self.assertNotIn("any key closes", "\n".join(self.screen(app)))
        app = self.run_app([done])  # a success stays a note in the corner
        self.assertIsNone(app.ui.alerting)
        self.assertIn("Moved it to the Trash", self.screen(app)[-1])

    def test_a_key_handler_that_raises_is_a_box_not_a_crash(self):
        import unittest.mock
        def boom(ui, k, rows):
            raise RuntimeError("boom")
        tabs = [roost.UI.TABS[0]._replace(keys=boom)] + roost.UI.TABS[1:]  # the Projects tab, the first one shown
        with unittest.mock.patch.object(roost.UI, "TABS", tabs), unittest.mock.patch.object(roost, "log"):
            app = self.run_app([self.keys("j")])
        self.assertTrue(app.ui.alerting)
        self.assertIn("boom", "\n".join(self.screen(app)))

    def test_ctrl_q_quits_and_plain_q_is_a_letter(self):
        app = self.run_app([self.keys("q")])
        self.assertIsNone(app.return_value)
        app = self.run_app([self.keys("ctrl+q")])
        self.assertFalse(app.is_running)

    def test_the_window_takes_roosts_color_and_title_and_gets_them_back(self):
        import unittest.mock
        sent = []
        with unittest.mock.patch("textual.drivers.headless_driver.HeadlessDriver.write", lambda self, d: sent.append(d)):
            self.run_app([self.keys("ctrl+q")])
        looks = [d for d in sent if d.startswith("\x1b]1")]
        self.assertEqual(looks, ["\x1b]11;#1b1e24\x07\x1b]2;roost\x07", "\x1b]111\x07"])

    def test_a_click_on_a_tab_name_switches_and_the_wheel_moves(self):
        wheel = type("Wheel", (), {"x": 10, "y": 6, "stop": lambda self: None})()

        async def scroll(pilot):
            pilot.app.query_one(roost.SessionList).on_mouse_scroll_down(wheel)
        app = self.run_app([lambda pilot: pilot.click(offset=(24, 0)), scroll])
        self.assertEqual((app.ui.TABS[app.ui.tab].name, app.ui.sel), ("Sessions", 1))

    def test_a_triple_click_acts_once(self):
        import unittest.mock
        ran = []
        with unittest.mock.patch.object(roost.UI, "session_actions", lambda ui, s: [("Open", lambda: ran.append(s["name"]))]):
            self.run_app([self.keys("tab"), lambda pilot: pilot.click(offset=(10, 6), times=3)])
        self.assertEqual(len(ran), 1)

    def test_a_right_click_opens_a_rows_actions(self):
        app = self.run_app([self.keys("tab"), lambda pilot: pilot.click(offset=(10, 6), button=3)])
        self.assertTrue(app.ui.choosing)
        self.assertIn(app.ui.choosing["title"], [s["name"] for s in self.sessions])

    def test_ctrl_n_asks_for_a_title_then_makes_the_note(self):
        import unittest.mock
        with tempfile.TemporaryDirectory() as tmp:  # a stand-in editor, so the test needs none installed
            stub = Path(tmp) / "stub-editor"
            stub.write_text("#!/bin/sh\nexit 0\n")
            stub.chmod(0o755)
            with unittest.mock.patch.multiple(roost, read_config=lambda: {"editor": str(stub)}):
                app = self.run_app([self.keys("ctrl+n", "n"), self.keys(*"Q4 ideas"), self.keys("enter")])
        self.assertTrue((roost.NOTES / "q4-ideas.md").exists())
        self.assertIn("Couldn't open the editor", app.ui.alerting)  # a test has no terminal to give it; a failure is a box

    def test_the_edge_border_meets_the_fill(self):
        """The eighth blocks sit against the box, on the page's color, so no fill shows past the line."""
        from textual import _border
        from textual.css import constants
        self.assertIn("edge", constants.VALID_BORDER)
        self.assertEqual(_border.BORDER_CHARS["edge"], ((" ", "▁", " "), ("▕", " ", "▏"), (" ", "▔", " ")))
        self.assertEqual(_border.BORDER_LOCATIONS["edge"][1], (1, 0, 1))

    def test_a_line_keeps_wide_characters(self):
        app = self.run_app([])
        line = roost.Line(app.ui, 10)
        line.put(0, "📌 ab")
        self.assertEqual(line.text().plain, "📌 ab     ")
        line.put(1, "x")  # over a wide character's second half: it's blanked, not shifted
        self.assertEqual(line.text().plain, " x ab     ")

    def test_a_new_line_in_the_file_is_a_new_line_in_the_card(self):
        md = roost.line_breaks(roost.MarkdownIt("gfm-like"))
        kinds = [c.type for t in md.parse("first line\nsecond line") for c in t.children or []]
        self.assertEqual(kinds, ["text", "hardbreak", "text"])
        text = roost.note_markdown({"lines": ["- [ ] open", "- [x] done", "```", "- [ ] code", "```"]})
        self.assertEqual(text.splitlines()[:2], ["- ☐ open", "- ☑ done"])
        self.assertIn("- [ ] code", text)  # code is left as it is

    def test_a_warning_sits_in_the_panels_top_edge(self):
        import unittest.mock
        with unittest.mock.patch.object(roost, "days_left", lambda s, rules: 6):
            app = self.run_app([self.keys("tab")])
        lines = self.screen(app)
        edge = next(i for i, l in enumerate(lines) if "⚠ deleted in 6 days" in l)
        self.assertTrue(lines[edge].lstrip().startswith("─"))  # the rule over the panel
        self.assertNotIn("deleted", lines[edge + 1])  # the facts line has its own row

    def test_a_prompt_on_the_bottom_row_is_a_textual_input(self):
        async def rename(pilot):
            pilot.app.ui.rename(self.sessions[0])
            pilot.app.redraw()
        app = self.run_app([self.keys("tab"), rename, self.keys("backspace", "backspace", "!")])
        self.assertEqual(app.ui.asking["text"], "Printer f!")
        app = self.run_app([self.keys("tab"), rename, self.keys("escape")])
        self.assertEqual((app.ui.asking, app.ui.msg), (None, "Cancelled"))

    def test_a_small_window_says_how_big_to_make_it_and_ignores_keys(self):
        import asyncio
        app = roost.RoostApp(self.sessions, "")

        async def go():
            async with app.run_test(size=(90, 24)) as pilot:
                await pilot.pause()
                await pilot.press("tab")
                await pilot.pause()
                app.lines = screen_text(app)
        asyncio.run(go())
        self.assertIn("at least 100 × 28; this one is 90 × 24", "\n".join(self.screen(app)))
        self.assertEqual(app.ui.TABS[app.ui.tab].name, "Projects")

    def test_on_notes_ctrl_n_and_a_rows_actions_show_over_the_card(self):
        roost.create_note("Printer idea", body="first line")
        seen = {}

        async def look(pilot):
            seen["prompt"] = pilot.app.query_one(roost.Prompt).region
        app = self.run_app([self.keys("tab", "tab", "ctrl+n", "N"), look])
        p = seen["prompt"]
        self.assertTrue(p.height and p.bottom <= 30, p)  # the title box is on screen, not under the reader
        app = self.run_app([self.keys("tab", "tab", "enter")])
        y0, x0, bw, bh = app.ui.choosing["box"]
        title = self.screen(app)[y0 + 1][x0 + 3:x0 + 3 + len("Printer idea")]
        self.assertEqual(title, "Printer idea")  # nothing of the card is drawn over the menu

    def test_the_frame_is_the_terminals_own_background(self):
        app = self.run_app([])
        self.assertEqual(app.ui.style(0).bgcolor.name, "default")  # so it's as translucent as the window's edges
        self.assertEqual(app.ui.style(0, card=True).bgcolor.name, roost.CARD)

    def test_notes_list_down_the_left_with_the_note_in_the_card(self):
        roost.create_note("Printer idea", body="first line\nsecond line")
        app = self.run_app([self.keys("tab", "tab")])
        lines = self.screen(app)
        self.assertTrue(any("Printer idea" in l and l.index("Printer idea") < 10 for l in lines))
        self.assertTrue(any("Notes  /  Printer idea" in l for l in lines))
        self.assertTrue(any("Tags" in l and "Created" in l for l in lines))  # the facts, in two columns
        self.assertEqual(app.ui.nrows[app.ui.nsel]["name"], "Printer idea")

    def test_the_stats_tab_shows_spend_and_arrows_change_the_range(self):
        import unittest.mock
        from datetime import date
        today = date.today().isoformat()
        data = {"rows": [(today, "Live work", self.sessions[0]["id"], None, 12.5), (today, "Ask", None, None, 0.25)],
                "tokens": {today: [90, 100]}, "unpriced": [], "priced": True}
        seen = {}

        async def look(pilot):
            for _ in range(20):  # the first read runs in the background
                if pilot.app.ui.stats_view():
                    break
                await pilot.pause(0.05)
            pilot.app.redraw()
            await pilot.pause()
            view = pilot.app.query_one(roost.StatsView)
            seen["shown"], seen["region"] = view.display, view.region
            seen["head"] = str(view.query_one(roost.Head).render())
            seen["table"] = str(view.query_one(roost.KindTable).render())
        with unittest.mock.patch.multiple(roost, gather_spend=lambda table=None, now=None: data, monthly_limit=lambda: None):
            app = self.run_app([self.keys("tab", "tab", "tab"), look, self.keys("right")])
        self.assertEqual(app.ui.TABS[app.ui.tab].name, "Stats")
        self.assertTrue(seen["shown"])
        self.assertTrue(seen["region"].height and seen["region"].bottom <= 28, seen["region"])  # clear of the key bar
        self.assertIn("$12.75 month to date", seen["head"])
        self.assertIn("Total", seen["table"])
        self.assertEqual(app.ui.srange, 3)  # → from Month to date is All time
        self.assertIn("←→ range", self.screen(app)[-1])

        async def hidden(pilot):
            seen["hidden"] = not pilot.app.query_one(roost.StatsPage).display
        self.run_app([hidden])
        self.assertTrue(seen["hidden"])  # only on its own tab


class Stats(unittest.TestCase):
    TABLE = {"fetched": "t1", "standard": {"claude-sonnet-5-5": [(None, (3, 3.75, 6, 0.3, 15))],
                                           "claude-haiku-4-5": [(None, (1, 1.25, 2, 0.1, 5))]}}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        import unittest.mock
        root = Path(self.tmp.name)
        patcher = unittest.mock.patch.multiple(roost, PROJECTS=root / "projects", SPEND_LOG=root / "spend.jsonl",
                                               SPEND_CACHE=root / "cache.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        (root / "projects" / "-x").mkdir(parents=True)

    def reply(self, mid, model="claude-sonnet-5-5", ts="2026-10-10T15:00:00Z", out=1000):
        return {"type": "assistant", "timestamp": ts, "message": {"id": mid, "model": model, "content": [],
                "usage": {"input_tokens": 0, "output_tokens": out, "cache_read_input_tokens": 0}}}

    def user(self, text):
        return {"type": "user", "message": {"role": "user", "content": text}}

    def transcript(self, name, recs):
        path = Path(self.tmp.name) / "projects" / "-x" / f"{name}.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in recs))
        return path

    def test_replies_after_wrap_up_count_as_wrap_ups_until_the_next_prompt(self):
        path = self.transcript("s1", [self.user("fix it"), self.reply("a"),
                                      self.user("<command-name>/roost:wrap-up</command-name>"), self.reply("b"),
                                      self.user("thanks, next thing"), self.reply("c")])
        got = roost.transcript_spend(path, self.TABLE)
        self.assertEqual([(r[0], r[2]) for r in got["replies"]], [("a", 0), ("b", 1), ("c", 0)])
        self.assertAlmostEqual(got["replies"][0][3], 0.015)

    def test_a_bedrock_model_id_is_priced_like_the_anthropic_one(self):
        u = {"input_tokens": 0, "output_tokens": 1000}
        self.assertEqual(roost.reply_cost("us.anthropic.claude-haiku-4-5-20251001-v1:0", u, self.TABLE),
                         roost.reply_cost("claude-haiku-4-5", u, self.TABLE))

    def test_gather_counts_a_repeated_reply_once_and_adds_the_spend_log(self):
        self.transcript("s1", [self.reply("a"), self.reply("b")])
        self.transcript("s2", [self.reply("a")])  # a resumed Session repeats it
        roost.log_spend("Ask", 0.5, session="s1", now=1760108400)
        roost.log_spend("Ask", 0)  # nothing spent, nothing logged
        got = roost.gather_spend(self.TABLE)
        self.assertEqual(sorted(r[1] for r in got["rows"]), ["Ask", "Live work", "Live work"])
        self.assertAlmostEqual(sum(r[4] for r in got["rows"]), 0.53)
        self.assertEqual(roost.gather_spend(self.TABLE)["rows"], got["rows"])  # read again from the cache
        hourly = sum(sum(v) for kinds in got["hours"].values() for v in kinds.values())
        self.assertAlmostEqual(hourly, 0.53)  # the same spend, hour by hour, for the Today chart

    def test_the_view_totals_a_range_and_paces_the_month(self):
        from datetime import date
        rows = [("2026-10-01", "Live work", "s1", None, 10.0), ("2026-10-10", "Wrap-ups", "s1", None, 2.0),
                ("2026-10-10", "Adopt", None, "printer", 1.0), ("2026-09-30", "Live work", "s2", None, 5.0)]
        data = {"rows": rows, "tokens": {"2026-10-10": [80, 100]}, "unpriced": [], "priced": True}
        with unittest.mock.patch.object(roost, "monthly_limit", lambda: None):
            v = roost.spend_view(data, "Month to date", {"s1": "printer"}, {"s1": "Printer fix"}, date(2026, 10, 10))
            week = roost.spend_view(data, "This week", {}, {}, date(2026, 10, 10))
        self.assertEqual(v["total"], 13.0)
        self.assertEqual(v["projects"], [("printer", 13.0)])
        self.assertAlmostEqual(v["pace"], 13.0 / 10 * 31)
        self.assertEqual(v["top_session"], ("Printer fix", 12.0))
        self.assertEqual(v["cached"], 0.8)
        self.assertEqual(week["total"], 3.0)  # Monday Oct 5 on
        self.assertEqual(week["chart_labels"], ["Oct 5", "Oct 6", "Oct 7", "Oct 8", "Oct 9", "Oct 10"])  # its own days
        self.assertEqual(v["chart_labels"][0], "Oct 1")
        self.assertEqual(v["chart"]["Live work"][0], 10.0)
        everything = roost.spend_view(data, "All time", {}, {}, date(2026, 10, 10))
        self.assertEqual(everything["chart_labels"][0], "Sep 30")

    def test_today_is_charted_by_the_hour_up_to_now(self):
        from datetime import date
        data = {"rows": [("2026-10-10", "Live work", "s1", None, 3.0)], "tokens": {}, "unpriced": [], "priced": True,
                "hours": {"2026-10-10": {"Live work": [0.0] * 9 + [1.0, 2.0] + [0.0] * 13}}}
        with unittest.mock.patch.object(roost, "monthly_limit", lambda: None):
            v = roost.spend_view(data, "Today", {}, {}, date(2026, 10, 10), hour=14)
        self.assertEqual(v["chart_labels"], [f"{h:02d}:00" for h in range(15)])
        self.assertEqual(v["chart"]["Live work"][9:11], [1.0, 2.0])
        self.assertEqual(v["chart"]["Wrap-ups"], [0.0] * 15)

    def test_streaks_end_yesterday_while_today_is_young(self):
        from datetime import date
        days = ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-08", "2026-10-09"]
        self.assertEqual(roost.streaks(days, date(2026, 10, 10)), (2, 3))
