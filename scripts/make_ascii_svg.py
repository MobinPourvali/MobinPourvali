#!/usr/bin/env python3
"""
Turn source-prepped.png into a self-typing monochrome ASCII portrait SVG.

Each row of characters lives behind its own horizontal clip rect. The rects
animate their width 0 -> full (SMIL <animate>, fill="freeze"), staggered top to
bottom, so the portrait prints itself in one pass and then holds. A small block
cursor rides each wipe edge and switches off when the row lands.

Every row is emitted at the full column width with xml:space="preserve" and an
explicit textLength. That is what keeps columns aligned across rows even when a
renderer falls back to a different monospace font.

    python scripts/make_ascii_svg.py            # writes avi-ascii.svg
    STATIC=1 python scripts/make_ascii_svg.py   # frozen frame, for Quick Look
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SRC = ROOT / "source-prepped.png"
DEFAULT_OUT = ROOT / "avi-ascii.svg"

# bright (sparse) -> dark (dense). The leading space clears the background.
RAMP = " .`:-=+*cs#%@"

COLS = 100
ROWS = 53

FONT_SIZE = 14.0
CHAR_W = 8.4          # advance width per column
CHAR_H = 15.4         # line height
PAD_X = 14.0
PAD_Y = 14.0

FG = "#c9d1d9"        # one light-gray fill: per-char color is what makes ASCII look like static
BG = "#0d1117"

ROW_DUR = 0.42        # seconds for one row to wipe in
STAGGER = 0.055       # delay between consecutive rows
START_DELAY = 0.25

FONT_STACK = (
    "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, "
    "'DejaVu Sans Mono', 'Liberation Mono', monospace"
)


def load_grid(src: Path, cols: int, rows: int) -> np.ndarray:
    """Fit the image into a cols x rows character grid, letterboxed with white."""
    img = Image.open(src).convert("L")

    # Preserve the photo's aspect ratio in *rendered* space, not cell space.
    cell_aspect = CHAR_W / CHAR_H
    scale = min(cols / img.width, rows / (img.height * cell_aspect))
    cell_w = max(1, min(cols, round(img.width * scale)))
    cell_h = max(1, min(rows, round(img.height * scale * cell_aspect)))

    small = img.resize((cell_w, cell_h), Image.LANCZOS)

    canvas = np.full((rows, cols), 255, dtype=np.uint8)
    y0 = (rows - cell_h) // 2
    x0 = (cols - cell_w) // 2
    canvas[y0 : y0 + cell_h, x0 : x0 + cell_w] = np.array(small, dtype=np.uint8)
    return canvas


def to_rows(grid: np.ndarray, ramp: str) -> list[str]:
    n = len(ramp) - 1
    # 255 (white) -> index 0 (space); 0 (black) -> last, densest glyph.
    idx = np.rint((255.0 - grid.astype(np.float32)) / 255.0 * n).astype(int)
    idx = np.clip(idx, 0, n)
    return ["".join(ramp[i] for i in row) for row in idx]


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_svg(rows: list[str], static: bool) -> str:
    cols = len(rows[0]) if rows else COLS
    text_w = cols * CHAR_W
    width = round(text_w + PAD_X * 2)
    height = round(len(rows) * CHAR_H + PAD_Y * 2)

    total = START_DELAY + max(0, len(rows) - 1) * STAGGER + ROW_DUR

    defs: list[str] = []
    texts: list[str] = []
    cursors: list[str] = []

    for i, row in enumerate(rows):
        y_top = PAD_Y + i * CHAR_H
        baseline = y_top + CHAR_H * 0.78
        begin = START_DELAY + i * STAGGER

        if static:
            clip_attr = ""
        else:
            cid = f"w{i}"
            defs.append(
                f'<clipPath id="{cid}">'
                f'<rect x="{PAD_X:.1f}" y="{y_top:.1f}" width="0" height="{CHAR_H:.1f}">'
                f'<animate attributeName="width" from="0" to="{text_w:.1f}" '
                f'dur="{ROW_DUR}s" begin="{begin:.3f}s" calcMode="linear" fill="freeze"/>'
                f"</rect></clipPath>"
            )
            clip_attr = f' clip-path="url(#{cid})"'

        texts.append(
            f'<text x="{PAD_X:.1f}" y="{baseline:.1f}" textLength="{text_w:.1f}" '
            f'lengthAdjust="spacing"{clip_attr}>{esc(row)}</text>'
        )

        if not static and row.strip():
            cw = CHAR_W * 0.85
            ch = CHAR_H * 0.72
            cy = y_top + (CHAR_H - ch) / 2
            cursors.append(
                f'<rect x="{PAD_X:.1f}" y="{cy:.1f}" width="{cw:.1f}" height="{ch:.1f}" '
                f'fill="{FG}" opacity="0">'
                f'<set attributeName="opacity" to="0.8" begin="{begin:.3f}s"/>'
                f'<animate attributeName="x" from="{PAD_X:.1f}" to="{PAD_X + text_w - cw:.1f}" '
                f'dur="{ROW_DUR}s" begin="{begin:.3f}s" calcMode="linear" fill="freeze"/>'
                f'<set attributeName="opacity" to="0" begin="{begin + ROW_DUR:.3f}s"/>'
                f"</rect>"
            )

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="asciiTitle">',
        "<title id=\"asciiTitle\">ASCII portrait</title>",
        f'<rect width="{width}" height="{height}" rx="8" fill="{BG}"/>',
    ]
    if defs:
        parts.append("<defs>" + "".join(defs) + "</defs>")
    parts.append(
        f'<g font-family="{FONT_STACK}" font-size="{FONT_SIZE}" fill="{FG}" '
        f'xml:space="preserve" shape-rendering="crispEdges">'
    )
    parts.extend(texts)
    parts.append("</g>")
    parts.extend(cursors)
    parts.append("</svg>")

    if not static:
        parts.insert(3, f"<!-- prints once in ~{total:.1f}s, then freezes -->")
    return "\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description="Render an ASCII portrait SVG.")
    ap.add_argument("-i", "--src", type=Path, default=DEFAULT_SRC)
    ap.add_argument("-o", "--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--cols", type=int, default=COLS)
    ap.add_argument("--rows", type=int, default=ROWS)
    ap.add_argument("--ramp", default=RAMP)
    ap.add_argument("--static", action="store_true", help="emit a frozen frame (no animation)")
    args = ap.parse_args()

    if not args.src.exists():
        print(f"error: {args.src} not found - run prep_photo.py first")
        return 1

    static = args.static or os.environ.get("STATIC") == "1"

    grid = load_grid(args.src, args.cols, args.rows)
    rows = to_rows(grid, args.ramp)
    svg = build_svg(rows, static)

    args.out.write_text(svg, encoding="utf-8")
    ink = sum(1 for r in rows for c in r if c != " ")
    print(f"-> wrote {args.out}  ({args.cols}x{args.rows} grid, {ink} glyphs, "
          f"{len(svg) / 1024:.1f} KB{', static' if static else ''})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
