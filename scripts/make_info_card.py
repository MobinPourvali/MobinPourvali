#!/usr/bin/env python3
"""
Hand-author a neofetch-style info card SVG.

Edit CONTENT below - that is the whole point of this file. Everything else is
layout. Each line fades and slides in on a short stagger via CSS keyframes with
animation-fill-mode: forwards, so the panel prints itself once and holds.

    python scripts/make_info_card.py            # writes info-card.svg
    STATIC=1 python scripts/make_info_card.py   # frozen frame, for Quick Look
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "info-card.svg"

USERNAME = "Mowbins"

# --------------------------------------------------------------------------- #
# content - the graph already covers your stats, so this is for the story
# numbers can't tell.
#
# Values run from x=132 to x=596: about 57 monospace characters at 13.5px.
# Anything longer needs splitting across ("kv", ...) then ("cont", ...) lines.
#
# No Prev / Highlights rows: you left those blank. Add them back as
#   ("kv", "Prev|..."), and ("kv", "Highlights|..."), ("bullet", "..."),
# and the layout reflows on its own.
# --------------------------------------------------------------------------- #
CONTENT: list[tuple[str, str]] = [
    ("header", f"{USERNAME}@github"),
    ("rule", ""),
    ("kv", "Now|MSc Molecular Biotechnology & Bioinformatics"),
    ("cont", "University of Milan (UNIMI)"),
    ("cont", "Research Intern, Functional Proteomics @ IFOM"),
    ("blank", ""),
    ("kv", "Stack|R · Python · RNA-seq tooling · bash"),
    ("blank", ""),
    ("kv", "Highlights|Published researcher in Bioinformatics"),
    ("bullet", "Check out my Google Scholar for my latest publications"),
    ("blank", ""),
    ("kv", "Editor|VS Code (RStudio)"),
    ("kv", "Uptime|Coffee-dependent"),
    ("blank", ""),
    ("swatches", ""),
    ("prompt", f"{USERNAME}@github ~ $"),
]

# --------------------------------------------------------------------------- #
# theme + metrics
# --------------------------------------------------------------------------- #
BG = "#0d1117"
BORDER = "#30363d"
CHROME = "#161b22"
FG = "#c9d1d9"
DIM = "#8b949e"
KEY = "#7ee787"
ACCENT = "#58a6ff"
ORANGE = "#ffa657"
PURPLE = "#d2a8ff"

SWATCHES = ["#f85149", "#ffa657", "#e3b341", "#7ee787", "#58a6ff", "#d2a8ff", "#c9d1d9"]

WIDTH = 660          # wide enough that the longest Highlights bullet clears the padding
CHROME_H = 34
PAD_X = 24
BODY_TOP = CHROME_H + 20
LINE_H = 23
FONT_SIZE = 13.5
KEY_COL = 108          # x offset of the value column, relative to PAD_X

STAGGER = 0.075
FIRST_DELAY = 0.15
LINE_DUR = 0.42

BLINK = True           # set False for a completely motionless final frame

FONT_STACK = (
    "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, "
    "'DejaVu Sans Mono', 'Liberation Mono', monospace"
)


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_svg(content: list[tuple[str, str]], static: bool) -> str:
    height = BODY_TOP + len(content) * LINE_H + 18

    body: list[str] = []
    delays: list[float] = []

    for i, (kind, raw) in enumerate(content):
        y = BODY_TOP + i * LINE_H
        delay = FIRST_DELAY + i * STAGGER
        delays.append(delay)
        cls = "" if static else f' class="ln d{i}"'
        x = PAD_X
        inner: list[str] = []

        if kind == "header":
            inner.append(
                f'<text x="{x}" y="{y}" fill="{ACCENT}" font-weight="700">{esc(raw)}</text>'
            )
        elif kind == "rule":
            inner.append(
                f'<text x="{x}" y="{y}" fill="{DIM}">{"-" * 34}</text>'
            )
        elif kind == "kv":
            key, _, val = raw.partition("|")
            inner.append(f'<text x="{x}" y="{y}" fill="{KEY}" font-weight="700">{esc(key)}</text>')
            inner.append(f'<text x="{x + KEY_COL}" y="{y}" fill="{FG}">{esc(val)}</text>')
        elif kind == "cont":
            inner.append(f'<text x="{x + KEY_COL}" y="{y}" fill="{FG}">{esc(raw)}</text>')
        elif kind == "bullet":
            inner.append(f'<text x="{x + KEY_COL}" y="{y}" fill="{DIM}">&#183;</text>')
            inner.append(f'<text x="{x + KEY_COL + 14}" y="{y}" fill="{FG}">{esc(raw)}</text>')
        elif kind == "blank":
            continue
        elif kind == "swatches":
            sw, gap, size = 26, 6, 13
            for j, color in enumerate(SWATCHES):
                inner.append(
                    f'<rect x="{x + j * (sw + gap)}" y="{y - size + 2}" '
                    f'width="{sw}" height="{size}" rx="2" fill="{color}"/>'
                )
        elif kind == "prompt":
            inner.append(f'<text x="{x}" y="{y}" fill="{ORANGE}">{esc(raw)}</text>')
            cur_x = x + len(raw) * 8.1 + 8
            blink = "" if (static or not BLINK) else ' class="cur"'
            inner.append(
                f'<rect{blink} x="{cur_x:.1f}" y="{y - 11}" width="8" height="14" fill="{FG}"/>'
            )

        if inner:
            body.append(f"<g{cls}>" + "".join(inner) + "</g>")

    # One delay class per line, emitted only for the lines that render.
    delay_css = "".join(f".d{i}{{animation-delay:{d:.3f}s}}" for i, d in enumerate(delays))

    if static:
        style = ""
    else:
        style = (
            "<style><![CDATA["
            f".ln{{opacity:0;animation:slidein {LINE_DUR}s cubic-bezier(.22,.61,.36,1) forwards}}"
            "@keyframes slidein{"
            "from{opacity:0;transform:translateX(-12px)}"
            "to{opacity:1;transform:translateX(0)}}"
            + (".cur{animation:blink 1.1s steps(1,end) infinite}"
               "@keyframes blink{0%,49%{opacity:1}50%,100%{opacity:0}}" if BLINK else "")
            + delay_css
            + "@media (prefers-reduced-motion: reduce){"
            ".ln{opacity:1;animation:none}.cur{animation:none}}"
            "]]></style>"
        )

    dots = "".join(
        f'<circle cx="{16 + j * 18}" cy="{CHROME_H / 2:.0f}" r="5.5" fill="{c}"/>'
        for j, c in enumerate(("#f85149", "#e3b341", "#3fb950"))
    )

    return "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" '
            f'viewBox="0 0 {WIDTH} {height}" role="img" aria-labelledby="cardTitle">',
            f'<title id="cardTitle">{esc(USERNAME)} info card</title>',
            style,
            f'<rect x="0.5" y="0.5" width="{WIDTH - 1}" height="{height - 1}" rx="10" '
            f'fill="{BG}" stroke="{BORDER}"/>',
            f'<path d="M0.5 10.5a10 10 0 0 1 10-10h{WIDTH - 21}a10 10 0 0 1 10 10'
            f'V{CHROME_H}H0.5Z" fill="{CHROME}"/>',
            f'<line x1="0.5" y1="{CHROME_H}" x2="{WIDTH - 0.5}" y2="{CHROME_H}" stroke="{BORDER}"/>',
            dots,
            f'<text x="{WIDTH / 2:.0f}" y="{CHROME_H / 2 + 4:.0f}" text-anchor="middle" '
            f'font-family="{FONT_STACK}" font-size="12" fill="{DIM}">neofetch</text>',
            f'<g font-family="{FONT_STACK}" font-size="{FONT_SIZE}">',
            *body,
            "</g>",
            "</svg>",
        ]
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Render the neofetch-style info card.")
    ap.add_argument("-o", "--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--static", action="store_true")
    args = ap.parse_args()

    static = args.static or os.environ.get("STATIC") == "1"
    svg = build_svg(CONTENT, static)
    args.out.write_text(svg, encoding="utf-8")
    print(f"-> wrote {args.out}  ({len(CONTENT)} lines{', static' if static else ''})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
