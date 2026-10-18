from support import *  # noqa: F401,F403 (the shared namespace, helpers, and fixtures)
from support import _SCRATCH, own_tests  # noqa: F401

load_tests = own_tests(__name__)


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


if __name__ == "__main__":
    unittest.main()
