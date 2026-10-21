"""The Agent Roost wren, drawn in the terminal with half blocks: each cell shows two pixels, the top one as
the character's colour and the bottom one as its background. The art is built from brand/source/wren.svg by
brand/build_brand.py --png; don't edit WREN by hand."""
import os

# One string per pixel row, each pixel rrggbb or ...... for transparent. An even number of rows.
WREN = [
    "..........................................................................................a8714aa9724ba9724ba9724ba56f48a56f48..............................",
    "......93603aa06a41............................................................a8714aa8714aa8714aa9724ba9724ba56f48a56f48a56f48a56f48........................",
    "6a3e2493603aa06a4284502f................................................a56e48b9865cb9865ca8714aa9724ba56f48a56f48a56f48a56f48a46d47a46d47..................",
    "84522f93603a93603aa06a4284502f..............................a56e48a56e48a56e48b9865cb9865cb9865cc6966bf6ead6f6ead6f6ead6f6ead6f6ead6a46d47..................",
    "......84522f93603aa06a4284502f........................b07a52a56e48a56e48a56e48b9865cb9865cc6966bc6966bd8ae83d8ae831b1512ffffffedd8b9edd8b9..................",
    "......84522f93603aa06a42a06a4284502f............a26c46b07a52b07a52b07a52b07a52b9865cc6966bc6966bc6966bd8ae83e8cdab1b15121b1512eedabceedabcedd8b9e0a24ae0a24a",
    "............84522f93603aa06a4284502f......a26c46a26c46a26c46b07a52b07a52b7835ab7835ad3a578d3a578d3a578c6966be8cdabe8cdabeedabceedabceedabcefddc1............",
    "........................93603a9a6440a36d47a26c46a26c46a26c46b07a52b7835ab7835aa8714ad3a578d3a578d3a578dcb68de0be98e0be98e8cdabeedabcefddc1efddc1............",
    "..............................9a6440a36d47a36d47a36d47a36d47956039a8714aa8714aa8714ab5805ab5805adcb68ddcb68de0be98e0be98e0be98efdcc1efdcc0efdcc0............",
    "..............................9a64409a6440a36d47956039956039956039a8714aa8714aa8714ab5805ab5805ab5805ab5805ae0be98e0be98efddc1eedbbeefdcc0efdcc0............",
    "..............................9a64409a64409560399560399560398a5636956039a8714aad7650b5805aad7650ad7650ad7650efddc1efddc1efddc1eedbbeeedbbeefdcc0............",
    "..............................663c22663c228a56368a56368a56368a563694603a94603aa06a44ad7650ad7650ad7650ad7650efddc1efddc1efddc1eedbbeeedbbeeedbbe............",
    "..................663c22663c22663c225a341d5a341d8a56368a563694603a94603a94603aa06a44a06a44a06a44ad7650efdbbfefdbbfefdbbfefddc1eedbbef2e2ca..................",
    "........................5a341d5a341d5a341d5a341d50301b94603a94603a94603a94603aa06a44a06a44e6cba8efdbbfefdbbfefdbbfefdbbfefddc2f2e2caf2e2ca..................",
    "....................................50301a50301b50301a6a3f25f0dcc0e0be98e0be98e0be98e0be98e6cba8efdbbfefdbbfefddc2efddc2efddc2f2e2ca........................",
    "....................................6a3f24c18f65ca9b70ca9b70c18f65e0be98e0be98e0be98e0be98efdbbfefddc2efddc2efddc2efddc2efddc2..............................",
    "..........................................c18f65ca9b70ca9b70dfbd96e3c49fe3c49fe3c49fe3c49febd5b4efddc1efddc1efddc1efddc1efddc1..............................",
    "......................................................dfbd96dfbd96e3c49fa86f69e3c49febd5b4ebd5b4ebd5b4efddc1efddc1..........................................",
    "............................................................dfbd96dfbd96a86f69e3c49febd5b4ebd5b4ebd5b4efddc1................................................",
    "........................................................................a86f69............a86f69............................................................",
    "........................................................................a86f69............a86f69............................................................",
    "........................................................................a86f69............a86f69............................................................",
    "........................................................................a86f69a86f69......a86f69a86f69......................................................",
    "............................................................................................................................................................",
]

RESET = "\x1b[0m"


def _colour(px):
    return None if px == "......" else "#" + px


def wren_rows():
    """The art as rows of (char, fg, bg) cells, one row per two pixel rows. ▀ has the top pixel's colour over
    the bottom one's (no background when the bottom is transparent); ▄ draws a bottom pixel alone."""
    out = []
    for top, bottom in zip(WREN[0::2], WREN[1::2]):
        row = []
        for i in range(0, len(top), 6):
            t, b = _colour(top[i:i + 6]), _colour(bottom[i:i + 6])
            row.append(("▀", t, b) if t else ("▄", b, None) if b else (" ", None, None))
        out.append(row)
    return out


def _sgr(colour, ground):
    r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    return f"\x1b[{ground};2;{r};{g};{b}m"


def wren_ansi():
    """The art as terminal lines in 24-bit colour, each ending in a reset."""
    lines = []
    for row in wren_rows():
        line = ""
        for ch, fg, bg in row:
            line += RESET + (_sgr(fg, 38) if fg else "") + (_sgr(bg, 48) if bg else "") + ch
        lines.append(line + RESET)
    return lines


def wren_can_show(stream, env=None):
    """Whether stream is a terminal that draws 24-bit colour, and colour isn't turned off (NO_COLOR)."""
    env = os.environ if env is None else env
    try:
        tty = stream.isatty()
    except (AttributeError, ValueError):
        return False
    return bool(tty) and env.get("COLORTERM") in ("truecolor", "24bit") and "NO_COLOR" not in env


def print_wren(stream=None):
    """Print the wren and a blank line to stream (stdout) when it can show there."""
    import sys
    stream = stream or sys.stdout
    if wren_can_show(stream):
        print("\n".join(wren_ansi()) + "\n", file=stream)
