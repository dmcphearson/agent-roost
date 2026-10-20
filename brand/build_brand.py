"""Build Agent Roost brand files: the low-poly wren icon and the logo lockups, wordmark outlined from Quicksand.

The wren is drawn in source/wren.svg (a 512 px tile, edited in Pixelmator); this script takes the bird from it as is.
python3 brand/build_brand.py           writes the SVGs to brand/svg
python3 brand/build_brand.py --png     also renders brand/png and the terminal wren (roost/core/wren.py)
                                       with headless Chrome; the wren needs Pillow
"""
import math
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

HERE = Path(__file__).parent
FONT = HERE / "fonts/Quicksand-Variable.ttf"
WEIGHT = 600  # SemiBold, one weight for both words
SOURCE = HERE / "source/wren.svg"
SVG, PNG = HERE / "svg", HERE / "png"
SVGNS = "http://www.w3.org/2000/svg"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

NAVY, CREAM = "#14213D", "#F6F0E4"
TILE, CORNER = 512, 113  # the source tile and its corner radius
LOCKUP_W = 361  # the lockup without a panel: duoTemp's, 96 px icon and the wordmark
TITLE = "Agent Roost"


def bird():
    """The wren from the source tile (its <g id="Group">, nested groups and all), without the tile's background."""
    ET.register_namespace("", SVGNS)
    group = ET.parse(SOURCE).getroot().find(f".//{{{SVGNS}}}g[@id='Group']")
    if group is None:
        sys.exit(f'[brand] No <g id="Group"> in {SOURCE}')
    return ET.tostring(group, encoding="unicode").replace(f' xmlns="{SVGNS}"', "")


def icon_body(rounded, prefix=""):
    """The icon in TILE units: cream ground and the wren, clipped to rounded corners when asked."""
    body = f'<rect width="{TILE}" height="{TILE}" fill="{CREAM}"/>{bird()}'
    if not rounded:
        return body
    return (f'<defs><clipPath id="{prefix}corner"><rect width="{TILE}" height="{TILE}" rx="{CORNER}"/></clipPath></defs>'
            f'<g clip-path="url(#{prefix}corner)">{body}</g>')


def icon_svg(size, rounded=False):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {TILE} {TILE}">'
            f"<title>{TITLE}</title>{icon_body(rounded)}</svg>\n")


class Face:
    """One weight of the wordmark font, with its pair kerning, for outlining text."""

    def __init__(self, weight):
        self.font = instantiateVariableFont(TTFont(FONT), {"wght": weight})
        self.glyphs = self.font.getGlyphSet()
        self.cmap = self.font.getBestCmap()
        self.upm = self.font["head"].unitsPerEm
        self.hmtx = self.font["hmtx"]
        self.kerning = self._pairs()
        # The ink extent of everything outlined so far, as (left, top, right, bottom) in SVG coordinates.
        self.bounds = None

    def _pairs(self):
        """Pair adjustments from GPOS lookup type 2, flattened to a dict; the first value found wins."""
        pairs = {}
        if "GPOS" not in self.font:
            return pairs
        for lookup in self.font["GPOS"].table.LookupList.Lookup:
            for sub in lookup.SubTable:
                if lookup.LookupType == 9:
                    sub = sub.ExtSubTable
                if getattr(sub, "LookupType", lookup.LookupType) != 2:
                    continue
                first = sub.Coverage.glyphs
                if sub.Format == 1:
                    for left, pairset in zip(first, sub.PairSet):
                        for rec in pairset.PairValueRecord:
                            pairs.setdefault((left, rec.SecondGlyph), getattr(rec.Value1, "XAdvance", 0) or 0)
                elif sub.Format == 2:
                    c1, c2 = sub.ClassDef1.classDefs, sub.ClassDef2.classDefs
                    seconds = {}
                    for glyph, cls in c2.items():
                        seconds.setdefault(cls, []).append(glyph)
                    for left in first:
                        row = sub.Class1Record[c1.get(left, 0)]
                        for cls, rec in enumerate(row.Class2Record):
                            value = getattr(rec.Value1, "XAdvance", 0) or 0
                            if value and cls in seconds:
                                for right in seconds[cls]:
                                    pairs.setdefault((left, right), value)
        return pairs

    def outline(self, text, x, baseline, size, tracking=0.0):
        """Path data for `text` starting at x on the baseline, and the x where it ends."""
        scale = size / self.upm
        parts = []
        names = [self.cmap[ord(ch)] for ch in text]
        for index, name in enumerate(names):
            pen = SVGPathPen(self.glyphs)
            self.glyphs[name].draw(TransformPen(pen, (scale, 0, 0, -scale, x, baseline)))
            parts.append(pen.getCommands())
            box = BoundsPen(self.glyphs)
            self.glyphs[name].draw(TransformPen(box, (scale, 0, 0, -scale, x, baseline)))
            if box.bounds:
                l, t, r, b = box.bounds
                self.bounds = (l, t, r, b) if self.bounds is None else (
                    min(self.bounds[0], l), min(self.bounds[1], t), max(self.bounds[2], r), max(self.bounds[3], b))
            x += self.hmtx[name][0] * scale + tracking * size
            if index + 1 < len(names):
                x += self.kerning.get((name, names[index + 1]), 0) * scale
        return " ".join(p for p in parts if p), x


def words(x, baseline, size=60):
    """'Agent Roost' in one weight, as one path, with the face used (for its bounds)."""
    face = Face(WEIGHT)
    path, end = face.outline("Agent Roost", x, baseline, size, -0.005)
    return path, end, (face,)


def lockup_svg(text_colour, background=None, edge=False):
    """Icon, then 'Agent Roost' with its capitals centred on the icon. Transparent by default; with a
    background it sits centred on a padded panel, the icon lifted by a soft shadow."""
    icon, gap = 96, 24
    # The wordmark is sized so the lockup is LOCKUP_W wide, duoTemp's width, so the two line up side by side;
    # its capitals (about 0.7 of the size) are centred on the icon's middle.
    _, end, _ = words(0, 0)
    size = 60 * (LOCKUP_W - icon - gap) / end
    path, end, _ = words(icon + gap, icon / 2 + size * 0.35, size)
    content_width = LOCKUP_W
    pad_x, pad_y = (64, 40) if background else (0, 0)
    width, height = content_width + pad_x * 2, icon + pad_y * 2
    panel, shadow, lift = "", "", ""
    if background:
        panel = f'<rect width="{width}" height="{height}" fill="{background}"/>'
        shadow = ('<filter id="lift" x="-30%" y="-30%" width="160%" height="170%">'
                  '<feDropShadow dx="0" dy="4" stdDeviation="5" flood-color="#000" flood-opacity="0.3"/></filter>')
        lift = ' filter="url(#lift)"'
    # on light grounds the cream tile needs a hairline to keep its edge
    rim = (f'<rect x="2" y="2" width="{TILE - 4}" height="{TILE - 4}" rx="{CORNER - 2}" fill="none" '
           f'stroke="{NAVY}" stroke-opacity="0.12" stroke-width="4"/>') if edge else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
            f"<title>{TITLE}</title><defs>{shadow}</defs>{panel}"
            f'<g transform="translate({pad_x} {pad_y})">'
            f'<g{lift}><g transform="scale({icon / TILE})">{icon_body(True, "i")}{rim}</g></g>'
            f'<path d="{path}" fill="{text_colour}"/>'
            "</g></svg>\n")


def wordmark_svg(text_colour):
    """The lockup's words without the icon, cropped to the letters, for places that already show the icon."""
    path, _, faces = words(0, 50)
    left, top = min(f.bounds[0] for f in faces), min(f.bounds[1] for f in faces)
    right, bottom = max(f.bounds[2] for f in faces), max(f.bounds[3] for f in faces)
    width, height = math.ceil(right - left), math.ceil(bottom - top)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="{left:.2f} {top:.2f} {width} {height}"><title>{TITLE}</title>'
            f'<path d="{path}" fill="{text_colour}"/></svg>\n')


def render(svg_path, png_path, scale):
    """Screenshot an SVG at its own size times scale, on a transparent page, with headless Chrome."""
    w, h = (int(float(v)) for v in re.search(r'width="([\d.]+)" height="([\d.]+)"', svg_path.read_text()).groups())
    with tempfile.TemporaryDirectory() as profile:
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--user-data-dir={profile}",
                        f"--window-size={w},{h}", f"--force-device-scale-factor={scale}",
                        "--default-background-color=00000000", f"--screenshot={png_path}", svg_path.as_uri()],
                       check=True, capture_output=True)


# The terminal wren: the bird as a WREN_W x WREN_H pixel grid, drawn with half blocks by roost/core/wren.py.
WREN_W, WREN_H = 32, 26
WREN_BOX = (104, 128, 320, 268)  # the bird's box in the source tile: x, y, width, height
WREN_PY = HERE.parent / "roost/core/wren.py"
EYE, EYE_AT, LEG = (0x1B, 0x15, 0x12), (338, 204), (0xA8, 0x6F, 0x69)
LEGS = [(254, 330, 378), (288, 348, 382)]  # x, top and bottom of each leg in the tile


def wren_pixels():
    """Render the bird crisp at 4x, keep each 4x4 block's most common colour, then stamp the eye and legs,
    which are smaller than a pixel at this size. Rows of (r, g, b) or None for transparent."""
    from collections import Counter
    from PIL import Image
    x0, y0, cw, ch = WREN_BOX
    s = 4
    with tempfile.TemporaryDirectory() as tmp:
        svg, png = Path(tmp) / "wren.svg", Path(tmp) / "wren.png"
        svg.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="{WREN_W * s}" height="{WREN_H * s}" '
                       f'viewBox="{x0} {y0} {cw} {ch}" preserveAspectRatio="none" shape-rendering="crispEdges">{bird()}</svg>')
        render(svg, png, 1)
        im = Image.open(png).convert("RGBA")
    grid = []
    for y in range(WREN_H):
        row = []
        for x in range(WREN_W):
            block = [im.getpixel((x * s + i, y * s + j)) for i in range(s) for j in range(s)]
            row.append(Counter(None if a < 128 else (r, g, b) for r, g, b, a in block).most_common(1)[0][0])
        grid.append(row)
    at = lambda ux, uy: (int((ux - x0) / cw * WREN_W), int((uy - y0) / ch * WREN_H))
    ex, ey = at(*EYE_AT)
    face = grid[ey][ex + 2]
    for row in grid:  # the eye's own pixels give way, so it's stamped clean
        for i, c in enumerate(row):
            if c == EYE:
                row[i] = face
    for dx, dy in ((-1, -1), (0, -1), (-1, 0), (0, 0)):
        grid[ey + dy][ex + dx] = EYE
    grid[ey - 1][ex] = (0xFF, 0xFF, 0xFF)  # its highlight
    for lx, top, bottom in LEGS:
        x, ya = at(lx, top)
        yb = min(at(lx, bottom)[1], WREN_H - 1)
        for y in range(ya, yb + 1):
            grid[y][x] = LEG
        grid[yb][min(x + 1, WREN_W - 1)] = LEG  # a toe forward
    # trim empty rows and columns from every side, then pad to an even number of rows for the half blocks
    while grid and not any(grid[0]):
        grid.pop(0)
    while grid and not any(grid[-1]):
        grid.pop()
    used = [x for x in range(WREN_W) if any(row[x] for row in grid)]
    grid = [row[used[0]:used[-1] + 1] for row in grid]
    if len(grid) % 2:
        grid.append([None] * len(grid[0]))
    return grid


def write_wren(grid):
    """Replace the WREN block in roost/core/wren.py with the grid, one string per pixel row."""
    rows = ["".join("......" if c is None else "%02x%02x%02x" % c for c in row) for row in grid]
    block = "WREN = [\n" + "".join(f'    "{r}",\n' for r in rows) + "]\n"
    text = WREN_PY.read_text()
    start, end = text.index("WREN = ["), text.index("]\n", text.index("WREN = [")) + 2
    WREN_PY.write_text(text[:start] + block + text[end:])


def main():
    SVG.mkdir(parents=True, exist_ok=True)
    files = {
        "agent-roost-icon.svg": icon_svg(1024),
        "agent-roost-icon-rounded.svg": icon_svg(512, rounded=True),
        "agent-roost-lockup-on-dark.svg": lockup_svg(CREAM),
        "agent-roost-lockup-on-light.svg": lockup_svg(NAVY, edge=True),
        "agent-roost-lockup-dark.svg": lockup_svg(CREAM, background=NAVY),
        "agent-roost-lockup-light.svg": lockup_svg(NAVY, background=CREAM, edge=True),
        "agent-roost-wordmark-on-dark.svg": wordmark_svg(CREAM),
        "agent-roost-wordmark-on-light.svg": wordmark_svg(NAVY),
    }
    for name, svg in files.items():
        (SVG / name).write_text(svg)
    print(f"[brand] Wrote {len(files)} SVGs to {SVG}")
    if "--png" in sys.argv:
        PNG.mkdir(parents=True, exist_ok=True)
        renders = [("agent-roost-icon.svg", "agent-roost-icon-1024.png", 1),
                   ("agent-roost-icon-rounded.svg", "agent-roost-icon-rounded-512.png", 1)]
        renders += [(f"agent-roost-lockup-{v}.svg", f"agent-roost-lockup-{v}@2x.png", 2)
                    for v in ("dark", "light", "on-dark", "on-light")]
        for svg, png, scale in renders:
            render(SVG / svg, PNG / png, scale)
        print(f"[brand] Rendered {len(renders)} PNGs to {PNG}")
        write_wren(wren_pixels())
        print(f"[brand] Wrote the terminal wren to {WREN_PY}")


if __name__ == "__main__":
    main()
