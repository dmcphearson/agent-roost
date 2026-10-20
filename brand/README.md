# Agent Roost brand

The icon is a low-poly wren, the dreoilín: a small brown bird with its tail cocked up, drawn in flat facets on a cream tile. The logo pairs it with "Agent Roost" in Quicksand SemiBold, one rounded weight for both words, outlined.

| Colour | Hex | Use |
|---|---|---|
| Navy | `#14213D` | Dark backgrounds, logo text on light |
| Cream | `#F6F0E4` | The icon's tile, light backgrounds, logo text on dark |
| Wren brown | `#A8714A` | The bird's back; an accent that matches it |
| Beak amber | `#E0A24A` | The beak; a warm highlight |

- `source/wren.svg`: the master drawing, a 512 px tile edited in Pixelmator Pro. Change the bird here; everything else is built from it.
- `svg/`: the icon (square) and a rounded preview, for press and social posts. Lockups come two ways: `agent-roost-lockup-dark` and `-light` sit on a navy or cream panel, ready to use as they are; `-on-dark` and `-on-light` are transparent, for placing on your own background. `agent-roost-wordmark-on-dark` and `-on-light` are the words alone, for places that already show the icon. Text is outlined, so no font is needed.
- `png/`: the icon at 1024 px, the rounded preview at 512 px, and each lockup at twice its SVG size.

Regenerate the SVGs with `python3 brand/build_brand.py` (needs fontTools; Quicksand is in `fonts/`, SIL OFL), and the PNGs too with `--png` (renders with headless Google Chrome).

These files are © David McPhearson, all rights reserved, and are not covered by the repository's Apache License (see [TRADEMARKS.md](../TRADEMARKS.md)). The Quicksand font keeps its own SIL Open Font License.
