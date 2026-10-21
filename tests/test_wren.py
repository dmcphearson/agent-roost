from support import *  # noqa: F401,F403 (the shared namespace, helpers, and fixtures)
from support import _SCRATCH, own_tests  # noqa: F401
import importlib
import unittest.mock

load_tests = own_tests(__name__)

PIXEL = re.compile(r"^([0-9a-f]{6}|\.{6})$")
SGR = re.compile(r"\x1b\[([0-9;]*)m")
HALF_BLOCKS = ("▀", "▄")
CLI = sys.modules["roost.cli"]


class Tty(io.StringIO):
    """A stdout that says it's a terminal."""
    def isatty(self):
        return True


def colour(px):
    return None if px == "......" else "#" + px


def pixels(row):
    return [row[i:i + 6] for i in range(0, len(row), 6)]


def expected_cell(top, bottom):
    """One cell from a pixel pair, as the spec states it."""
    top, bottom = colour(top), colour(bottom)
    if top and bottom:
        return ("▀", top, bottom)
    if top:
        return ("▀", top, None)
    if bottom:
        return ("▄", bottom, None)
    return (" ", None, None)


def visible(line):
    return SGR.sub("", line)


def painted(line):
    """Each visible character of an ANSI line with the fg and bg in force when it's drawn."""
    fg = bg = None
    out, pos = [], 0
    for m in SGR.finditer(line):
        out += [(ch, fg, bg) for ch in line[pos:m.start()]]
        pos = m.end()
        params = [int(p) if p else 0 for p in m.group(1).split(";")] if m.group(1) else [0]
        i = 0
        while i < len(params):
            p = params[i]
            if p == 0:
                fg = bg = None
            elif p == 39:
                fg = None
            elif p == 49:
                bg = None
            elif p in (38, 48) and i + 4 < len(params) and params[i + 1] == 2:
                c = "#%02x%02x%02x" % tuple(params[i + 2:i + 5])
                fg, bg = (c, bg) if p == 38 else (fg, c)
                i += 4
            i += 1
    out += [(ch, fg, bg) for ch in line[pos:]]
    return out


def colour_env(**extra):
    """os.environ as a true-colour terminal has it, without NO_COLOR unless asked; restored afterwards."""
    patcher = unittest.mock.patch.dict(os.environ, {"COLORTERM": "truecolor"})
    patcher.start()
    os.environ.pop("NO_COLOR", None)
    os.environ.update(extra)
    return patcher


class WrenArt(unittest.TestCase):
    """The logo itself: core/wren.py's pixel rows and what's made from them."""
    def test_the_wren_module_comes_right_after_config(self):
        self.assertIn("core.wren", package.MODULES)
        self.assertEqual(package.MODULES.index("core.wren"), package.MODULES.index("core.config") + 1)
        self.assertEqual(importlib.import_module("roost.core.wren").WREN, roost.WREN)

    def test_wren_is_even_rows_of_equal_length_hex_or_transparent_pixels(self):
        rows = roost.WREN
        self.assertIsInstance(rows, list)
        self.assertTrue(rows, "the wren has no rows")
        self.assertEqual(len(rows) % 2, 0, f"{len(rows)} pixel rows, not an even number")
        self.assertEqual(len({len(r) for r in rows}), 1, "rows differ in length")
        self.assertEqual(len(rows[0]) % 6, 0)
        for y, row in enumerate(rows):
            self.assertIsInstance(row, str)
            for x, px in enumerate(pixels(row)):
                self.assertRegex(px, PIXEL, f"pixel {x} of row {y}")
        self.assertTrue(any(px != "......" for row in rows for px in pixels(row)), "every pixel is transparent")

    def test_wren_rows_pairs_pixel_rows_into_half_block_cells(self):
        art = roost.WREN
        want = [[expected_cell(t, b) for t, b in zip(pixels(art[y]), pixels(art[y + 1]))]
                for y in range(0, len(art), 2)]
        got = [[tuple(cell) for cell in row] for row in roost.wren_rows()]
        self.assertEqual(len(got), len(art) // 2)
        self.assertEqual(got, want)

    def test_wren_rows_covers_each_pixel_pair_case(self):
        """A small made-up art in place of the wren, so all four pixel-pair cases are seen."""
        wren = importlib.import_module("roost.core.wren")
        art = ["ff0000" "......" "00ff00" "......",
               "0000ff" "abcdef" "......" "......"]
        with unittest.mock.patch.object(wren, "WREN", art):
            got = [[tuple(cell) for cell in row] for row in wren.wren_rows()]
        self.assertEqual(got, [[("▀", "#ff0000", "#0000ff"), ("▄", "#abcdef", None),
                                ("▀", "#00ff00", None), (" ", None, None)]])

    def test_wren_ansi_draws_the_same_cells_with_true_colour_codes(self):
        rows, lines = roost.wren_rows(), roost.wren_ansi()
        self.assertEqual(len(lines), len(rows))
        for y, (row, line) in enumerate(zip(rows, lines)):
            self.assertTrue(line.endswith("\x1b[0m"), f"line {y} doesn't end with a reset: {line!r}")
            self.assertEqual(visible(line), "".join(cell[0] for cell in row), f"line {y}")
            for x, ((ch, fg, bg), (want_ch, want_fg, want_bg)) in enumerate(zip(painted(line), row)):
                self.assertEqual(ch, want_ch)
                if want_ch != " ":
                    self.assertEqual(fg, want_fg, f"foreground of cell {x}, line {y}")
                self.assertEqual(bg, want_bg, f"background of cell {x}, line {y}")

    def test_wren_ansi_uses_24_bit_sgr_codes(self):
        text = "".join(roost.wren_ansi())
        self.assertRegex(text, r"\x1b\[38;2;\d+;\d+;\d+m")
        some = next(cell for row in roost.wren_rows() for cell in row if cell[1])
        r, g, b = (int(some[1][i:i + 2], 16) for i in (1, 3, 5))
        self.assertIn(f"38;2;{r};{g};{b}", text)


class WrenCanShow(unittest.TestCase):
    """wren_can_show: a terminal, true colour, and no NO_COLOR."""
    def test_a_true_colour_terminal_shows_it(self):
        self.assertTrue(roost.wren_can_show(Tty(), {"COLORTERM": "truecolor"}))
        self.assertTrue(roost.wren_can_show(Tty(), {"COLORTERM": "24bit"}))

    def test_not_a_terminal_doesnt(self):
        self.assertFalse(roost.wren_can_show(io.StringIO(), {"COLORTERM": "truecolor"}))

    def test_no_colorterm_or_another_value_doesnt(self):
        self.assertFalse(roost.wren_can_show(Tty(), {}))
        self.assertFalse(roost.wren_can_show(Tty(), {"COLORTERM": "256color"}))
        self.assertFalse(roost.wren_can_show(Tty(), {"COLORTERM": ""}))

    def test_no_color_turns_it_off_whatever_its_value(self):
        self.assertFalse(roost.wren_can_show(Tty(), {"COLORTERM": "truecolor", "NO_COLOR": "1"}))
        self.assertFalse(roost.wren_can_show(Tty(), {"COLORTERM": "24bit", "NO_COLOR": ""}))

    def test_env_defaults_to_os_environ(self):
        patcher = colour_env()
        self.addCleanup(patcher.stop)
        self.assertTrue(roost.wren_can_show(Tty()))
        os.environ["NO_COLOR"] = ""
        self.assertFalse(roost.wren_can_show(Tty()))
        del os.environ["NO_COLOR"]
        del os.environ["COLORTERM"]
        self.assertFalse(roost.wren_can_show(Tty()))


class WrenInTheCommands(unittest.TestCase):
    """roost --version, roost --help, and roost setup print the wren first on a true-colour terminal,
    and exactly what they print today anywhere else."""
    def run_main(self, argv, out, **env):
        patcher = colour_env(**env)
        self.addCleanup(patcher.stop)
        with contextlib.redirect_stdout(out):
            CLI.main(argv)
        return out.getvalue()

    def assert_wren_then(self, text, rest):
        """text is the wren's lines, then rest (blank lines between them allowed)."""
        wren = roost.wren_ansi()
        lines = text.split("\n")
        self.assertEqual(lines[:len(wren)], wren, "the wren isn't printed first")
        self.assertEqual("\n".join(lines[len(wren):]).lstrip("\n"), rest)

    def test_version_on_a_true_colour_terminal_prints_the_wren_then_the_version(self):
        out = self.run_main(["--version"], Tty())
        self.assert_wren_then(out, f"roost {roost.VERSION}\n")

    def test_version_piped_is_exactly_the_version_line(self):
        self.assertEqual(self.run_main(["--version"], io.StringIO()), f"roost {roost.VERSION}\n")

    def test_version_without_colorterm_is_exactly_the_version_line(self):
        patcher = colour_env()
        self.addCleanup(patcher.stop)
        del os.environ["COLORTERM"]
        out = Tty()
        with contextlib.redirect_stdout(out):
            CLI.main(["--version"])
        self.assertEqual(out.getvalue(), f"roost {roost.VERSION}\n")

    def test_version_with_no_color_is_exactly_the_version_line(self):
        self.assertEqual(self.run_main(["--version"], Tty(), NO_COLOR=""), f"roost {roost.VERSION}\n")

    def test_help_on_a_true_colour_terminal_prints_the_wren_above_the_usage(self):
        for flag in ("--help", "-h"):
            with self.subTest(flag=flag):
                self.assert_wren_then(self.run_main([flag], Tty()), CLI.usage() + "\n")

    def test_help_piped_is_exactly_the_usage(self):
        for flag in ("--help", "-h"):
            with self.subTest(flag=flag):
                self.assertEqual(self.run_main([flag], io.StringIO()), CLI.usage() + "\n")

    def test_help_with_no_color_is_exactly_the_usage(self):
        for flag in ("--help", "-h"):
            with self.subTest(flag=flag):
                self.assertEqual(self.run_main([flag], Tty(), NO_COLOR="1"), CLI.usage() + "\n")

    def run_setup(self, out, **env):
        """roost setup up to its first check, with claude missing so it stops there and runs nothing."""
        patcher = colour_env(**env)
        self.addCleanup(patcher.stop)
        install = sys.modules["roost.core.install"]
        never = unittest.mock.Mock(side_effect=AssertionError("setup ran a command"))
        with unittest.mock.patch.object(install.shutil, "which", lambda name: None), \
                unittest.mock.patch.object(install.subprocess, "run", never), contextlib.redirect_stdout(out):
            code = install.setup()
        self.assertEqual(code, 1)
        return out.getvalue()

    def test_setup_on_a_true_colour_terminal_prints_the_wren_above_its_header(self):
        lines = self.run_setup(Tty()).split("\n")
        wren = roost.wren_ansi()
        start = next((i for i in range(len(lines)) if lines[i:i + len(wren)] == wren), None)
        self.assertIsNotNone(start, "the wren's lines aren't in setup's output")
        header = next(i for i, l in enumerate(lines) if "Agent Roost setup" in l)
        self.assertLess(start + len(wren) - 1, header)

    def test_setup_elsewhere_has_no_wren(self):
        for out, env in [(io.StringIO(), {}), (Tty(), {"NO_COLOR": ""})]:
            with self.subTest(tty=out.isatty(), env=env):
                text = self.run_setup(out, **env)
                self.assertIn("Agent Roost setup", text)
                self.assertFalse(any(b in text for b in HALF_BLOCKS), text)
                self.assertNotIn("\x1b[38;2;", text)
                self.assertTrue(text.startswith("\n\033[1mAgent Roost setup\033[0m\n"), repr(text[:80]))
