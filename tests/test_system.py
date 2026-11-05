from support import *  # noqa: F401,F403 (the shared namespace, helpers, and fixtures)
from support import _SCRATCH, own_tests  # noqa: F401

load_tests = own_tests(__name__)


def system():
    """roost.core.system, looked up when a test runs (so a missing module fails only these tests)."""
    return importlib.import_module("roost.core.system")


class ThisComputer(unittest.TestCase):
    """What this computer can do, from sys.platform, the environment, and which programs are on PATH.
    Every test sets all three, so none depends on the computer running it."""

    def on(self, platform, env=None, found=()):
        """Patch the platform, the environment (exactly env), and which programs shutil.which finds."""
        import unittest.mock
        sysmod = system()
        for p in (unittest.mock.patch.object(sysmod.sys, "platform", platform),
                  unittest.mock.patch.dict(sysmod.os.environ, env or {}, clear=True),
                  unittest.mock.patch.object(sysmod.shutil, "which",
                                             lambda name, *a, **k: f"/usr/bin/{name}" if name in found else None)):
            p.start()
            self.addCleanup(p.stop)
        return sysmod

    ALL = ("pbcopy", "tmux", "wl-copy", "xclip", "xsel", "xdg-open", "open")

    # copy_command: first match wins

    def test_macos_copies_with_pbcopy_even_inside_tmux(self):
        s = self.on("darwin", {"TMUX": "/tmp/tmux-501/default,1,0", "DISPLAY": ":0"}, found=self.ALL)
        self.assertEqual(s.copy_command(), ["pbcopy"])

    def test_inside_tmux_copies_through_tmux_ahead_of_a_display(self):
        s = self.on("linux", {"TMUX": "/tmp/tmux-1000/default,1,0", "WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"},
                    found=self.ALL)
        self.assertEqual(s.copy_command(), ["tmux", "load-buffer", "-w", "-"])

    def test_tmux_set_but_not_installed_falls_through(self):
        s = self.on("linux", {"TMUX": "x", "DISPLAY": ":0"}, found=("xclip",))
        self.assertEqual(s.copy_command(), ["xclip", "-selection", "clipboard"])

    def test_wayland_copies_with_wl_copy_ahead_of_x(self):
        s = self.on("linux", {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}, found=("wl-copy", "xclip", "xsel"))
        self.assertEqual(s.copy_command(), ["wl-copy"])

    def test_wayland_without_wl_copy_falls_through_to_x(self):
        s = self.on("linux", {"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}, found=("xclip",))
        self.assertEqual(s.copy_command(), ["xclip", "-selection", "clipboard"])

    def test_x_prefers_xclip_then_xsel(self):
        s = self.on("linux", {"DISPLAY": ":0"}, found=("xclip", "xsel"))
        self.assertEqual(s.copy_command(), ["xclip", "-selection", "clipboard"])
        s = self.on("linux", {"DISPLAY": ":0"}, found=("xsel",))
        self.assertEqual(s.copy_command(), ["xsel", "--clipboard", "--input"])

    def test_a_display_with_no_clipboard_program_has_none(self):
        s = self.on("linux", {"DISPLAY": ":0"}, found=("tmux", "wl-copy"))  # no TMUX, no WAYLAND_DISPLAY
        self.assertIsNone(s.copy_command())

    def test_linux_over_ssh_with_no_tmux_or_display_has_none(self):
        s = self.on("linux", {"SSH_CONNECTION": "192.0.2.1 50000 192.0.2.2 22"}, found=self.ALL)
        self.assertIsNone(s.copy_command())

    def test_it_reads_the_environment_at_call_time(self):
        s = self.on("linux", {}, found=self.ALL)
        self.assertIsNone(s.copy_command())
        s.os.environ["DISPLAY"] = ":0"
        self.assertEqual(s.copy_command(), ["xclip", "-selection", "clipboard"])

    # open_command and file_manager

    def test_macos_opens_and_reveals_with_open(self):
        s = self.on("darwin", {}, found=())
        self.assertEqual(s.open_command("/Users/x/notes/idea.md"), ["open", "/Users/x/notes/idea.md"])
        self.assertEqual(s.open_command("/Users/x/notes/idea.md", reveal=True), ["open", "-R", "/Users/x/notes/idea.md"])
        self.assertEqual(s.file_manager(), "Finder")

    def test_linux_with_a_display_opens_with_xdg_open(self):
        for env in ({"DISPLAY": ":0"}, {"WAYLAND_DISPLAY": "wayland-0"}):
            s = self.on("linux", env, found=("xdg-open",))
            self.assertEqual(s.open_command("/home/x/proj"), ["xdg-open", "/home/x/proj"], env)
            self.assertEqual(s.file_manager(), "Files", env)

    def test_linux_reveal_opens_the_folder_that_holds_the_file(self):
        s = self.on("linux", {"DISPLAY": ":0"}, found=("xdg-open",))
        self.assertEqual(s.open_command("/home/x/proj/notes/idea.md", reveal=True), ["xdg-open", "/home/x/proj/notes"])

    def test_linux_with_no_display_cant_show_files(self):
        s = self.on("linux", {"SSH_CONNECTION": "192.0.2.1 50000 192.0.2.2 22"}, found=("xdg-open",))
        self.assertIsNone(s.open_command("/home/x/proj"))
        self.assertIsNone(s.open_command("/home/x/proj/a.md", reveal=True))
        self.assertIsNone(s.file_manager())

    def test_linux_with_a_display_but_no_xdg_open_cant_show_files(self):
        s = self.on("linux", {"DISPLAY": ":0"}, found=("xclip",))
        self.assertIsNone(s.open_command("/home/x/proj"))
        self.assertIsNone(s.file_manager())

    # managed_settings

    def test_managed_settings_path_per_platform(self):
        s = self.on("darwin")
        self.assertEqual(s.managed_settings(), Path("/Library/Application Support/ClaudeCode/managed-settings.json"))
        s = self.on("linux")
        self.assertEqual(s.managed_settings(), Path("/etc/claude-code/managed-settings.json"))

    def test_it_comes_before_every_module_that_imports_it(self):
        """MODULES is lowest first: core.system is listed, ahead of each module that holds it (at least
        sessiondata and the shell); roost.system is that module, and roost.sys is the real sys, never the
        tests' macOS stand-in."""
        mods, s = package.MODULES, system()
        self.assertIn("core.system", mods)
        users = [name for name in mods
                 if any(v is s for v in vars(importlib.import_module(f"roost.{name}")).values())]
        self.assertTrue({"core.sessiondata", "app.shell"} <= set(users), users)
        for name in users:
            self.assertLess(mods.index("core.system"), mods.index(name), name)
        self.assertIs(roost.system, s)  # reachable through the shared namespace
        self.assertIs(roost.sys, sys)


if __name__ == "__main__":
    unittest.main()
