from support import *  # noqa: F401,F403 (the shared namespace, helpers, and fixtures)
from support import _SCRATCH, own_tests  # noqa: F401
from test_projects import Projects  # for their helpers; their tests run in test_projects.py

load_tests = own_tests(__name__)


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

    def fresh(self):
        """No log and no record of a logged version, before and after the test."""
        for path in (roost.LOG, roost.config.COMPAT_LOGGED):
            path.unlink(missing_ok=True)

    def launch(self, version):
        """check_compat as one roost launch would run it, with Claude Code reporting this version and
        a session list that has every field roost reads."""
        import unittest.mock
        def run(cmd, *a, **k):
            return unittest.mock.Mock(stdout=f"{version} (Claude Code)\n" if "--version" in cmd else "[]")
        with unittest.mock.patch.object(roost.subprocess, "run", run):
            return roost.check_compat()

    def newer_lines(self, version):
        line = f"compat: Claude Code {version} is newer than roost was tested with ({roost.TESTED_CLAUDE})"
        text = roost.LOG.read_text() if roost.LOG.exists() else ""
        return [l for l in text.splitlines() if l.endswith(line)]

    def test_a_newer_version_is_logged_once_not_every_launch(self):
        self.fresh(); self.addCleanup(self.fresh)
        self.assertEqual(self.launch("9.9.9"), "")
        self.assertEqual(self.launch("9.9.9"), "")
        self.assertEqual(len(self.newer_lines("9.9.9")), 1)

    def test_a_different_newer_version_is_logged_again(self):
        self.fresh(); self.addCleanup(self.fresh)
        self.launch("9.9.9"); self.launch("9.9.9")
        self.assertEqual(self.launch("9.9.10"), "")
        self.assertEqual(len(self.newer_lines("9.9.9")), 1)
        self.assertEqual(len(self.newer_lines("9.9.10")), 1)
        self.assertEqual(len(self.newer_lines("9.9.9")) + len(self.newer_lines("9.9.10")), 2)

    def test_the_logged_version_is_kept_in_the_compat_logged_file(self):
        self.fresh(); self.addCleanup(self.fresh)
        self.launch("9.9.9")
        self.assertTrue(roost.config.COMPAT_LOGGED.exists())
        self.launch("9.9.9")
        self.assertEqual(len(self.newer_lines("9.9.9")), 1)
        roost.config.COMPAT_LOGGED.unlink()  # forget it on disk: the next launch logs the version again
        self.launch("9.9.9")
        self.assertEqual(len(self.newer_lines("9.9.9")), 2)

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
    def test_a_script_that_hangs_is_a_failure_not_an_exception(self):
        import subprocess, unittest.mock
        def hang(*a, **kw):
            raise subprocess.TimeoutExpired("osascript", 10)
        with unittest.mock.patch.object(roost.subprocess, "run", hang), unittest.mock.patch.object(roost, "log"):
            self.assertIsNone(roost.osa("tell application \"Ghostty\" to activate"))


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
        """? explains the keys the bar names: each shared help row's key is an entry in the bar. What each key
        does on a real screen is TextualScreens.test_every_key_the_bar_names_does_what_it_says."""
        entries = re.split(r" {2,}", roost.BAR_KEYS.strip())
        bar = [e.split(" ", 1)[0] for e in entries]
        rows = roost.UI.GLOBAL_HELP
        self.assertEqual(len(bar), len(rows) + 1, entries)  # the Close row explains the bar's last two, ? and ctrl+q
        for (what, text), key in zip(rows[:-1], bar):  # Move ↑↓, Views ←→, Actions enter, Search /, New ctrl+n, Tabs tab
            self.assertEqual(text.split(" ", 1)[0], key, what)
        close = re.split(r"[\s·]+", rows[-1][1])
        for key in bar[-2:]:
            self.assertIn(key, close, rows[-1][0])
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


class ShortWhen(unittest.TestCase):
    """formatting.short_when(ts, now): a time today as "2:17 PM", any other day as two-digit MM/DD/YY or
    DD/MM/YY, in the order formatting.date_order() gives ("MDY" or "DMY")."""
    NOW = datetime(2026, 10, 14, 18, 0).timestamp()

    def at(self, *when):
        return datetime(*when).timestamp()

    def order(self, which):
        import unittest.mock
        return unittest.mock.patch.object(FMT, "date_order", lambda: which)

    def test_a_time_today_is_the_time_only(self):
        for when, shown in [((2026, 10, 14, 14, 17), "2:17 PM"), ((2026, 10, 14, 0, 5), "12:05 AM"),
                            ((2026, 10, 14, 12, 0), "12:00 PM"), ((2026, 10, 14, 9, 5), "9:05 AM"),
                            ((2026, 10, 14, 23, 59), "11:59 PM")]:
            for order in ("MDY", "DMY"):
                with self.order(order):
                    self.assertEqual(roost.short_when(self.at(*when), self.NOW), shown, (when, order))

    def test_any_other_day_is_the_date_only_month_first(self):
        with self.order("MDY"):
            self.assertEqual(roost.short_when(self.at(2026, 10, 13, 14, 17), self.NOW), "10/13/26")  # yesterday
            self.assertEqual(roost.short_when(self.at(2026, 1, 5, 9, 0), self.NOW), "01/05/26")
            self.assertEqual(roost.short_when(self.at(2025, 10, 14, 14, 17), self.NOW), "10/14/25")  # today's date, a year ago

    def test_any_other_day_is_the_date_only_day_first(self):
        with self.order("DMY"):
            self.assertEqual(roost.short_when(self.at(2026, 10, 13, 14, 17), self.NOW), "13/10/26")
            self.assertEqual(roost.short_when(self.at(2026, 1, 5, 9, 0), self.NOW), "05/01/26")
            self.assertEqual(roost.short_when(self.at(2025, 10, 14, 14, 17), self.NOW), "14/10/25")

    def test_now_defaults_to_the_current_time(self):
        ts = time.time()
        self.assertEqual(roost.short_when(ts), clock(ts))
        with self.order("MDY"):
            self.assertEqual(roost.short_when(ts - 3 * 86400), datetime.fromtimestamp(ts - 3 * 86400).strftime("%m/%d/%y"))

    def test_am_and_pm_are_english_whatever_the_locale(self):
        saved = locale.setlocale(locale.LC_TIME)
        tried = 0
        try:
            for name in ("en_GB.UTF-8", "ja_JP.UTF-8", "de_DE.UTF-8"):  # p.m., 午後, and nothing, for %p
                try:
                    locale.setlocale(locale.LC_TIME, name)
                except locale.Error:
                    continue
                tried += 1
                self.assertEqual(roost.short_when(self.at(2026, 10, 14, 14, 17), self.NOW), "2:17 PM", name)
                self.assertEqual(roost.short_when(self.at(2026, 10, 14, 9, 5), self.NOW), "9:05 AM", name)
        finally:
            locale.setlocale(locale.LC_TIME, saved)
        if not tried:
            self.skipTest("none of en_GB, ja_JP, de_DE is installed")

    def test_the_order_comes_from_a_locales_date_format(self):
        for fmt, order in [("%m/%d/%Y", "MDY"), ("%m/%d/%y", "MDY"), ("%d/%m/%Y", "DMY"), ("%d.%m.%Y", "DMY"),
                           ("%Y/%m/%d", "MDY"), ("", "MDY"), ("%x", "MDY")]:
            self.assertEqual(roost.order_from_format(fmt), order, fmt)

    def test_date_order_is_month_or_day_first(self):
        self.assertIn(FMT.date_order(), ("MDY", "DMY"))


class WorkedStaysInFilesAndCommands(unittest.TestCase):
    """Text roost writes to files or prints from roost project list keeps worked()'s "Oct 7, 4:12 pm"."""
    setUp, tearDown, project = Projects.setUp, Projects.tearDown, Projects.project

    def test_roost_project_list_prints_the_long_form(self):
        import argparse
        self.project("2026-09-a", handoffs=["2026-10-07-1612-aa1b9a71.md"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(roost.project_list(argparse.Namespace(all=False)), 0)
        self.assertIn("Oct 7, 4:12 pm", out.getvalue())
        self.assertNotRegex(out.getvalue(), SHORT_DATE)

    def test_a_handoffs_times_keep_the_long_form(self):
        self.assertRegex(roost.iso_time("2026-10-07T16:12:00.000Z"), r"^[A-Z][a-z]{2} \d{1,2}, \d{1,2}:\d\d (am|pm)$")


if __name__ == "__main__":
    unittest.main()
