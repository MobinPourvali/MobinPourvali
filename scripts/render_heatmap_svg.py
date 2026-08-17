#!/usr/bin/env python3
"""
Render data/contributions.json as an animated 53x7 calendar SVG.

The reveal is a diagonal, line-after-line slide-down: delay is dominated by the
row index with a smaller per-column term. CSS keyframes with
animation-fill-mode: forwards, so it plays once on load and freezes - no
looping glow.

Deliberately stdlib-only (plus json) so the daily workflow needs nothing beyond
requests + beautifulsoup4.

    python scripts/render_heatmap_svg.py   # writes contrib-heatmap.svg
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_IN = ROOT / "data" / "contributions.json"
DEFAULT_OUT = ROOT / "contrib-heatmap.svg"

PALETTE = ["#161b22", "#0e4429", "#006d32", "#26a641", "#39d353", "#69f0a0"]
#          none  ->  brightest (level 5 is a neon top end for your best days)

BG = "#0d1117"
BORDER = "#30363d"
FG = "#c9d1d9"
DIM = "#8b949e"
ACCENT = "#39d353"

WEEKS = 53
CELL = 12
GAP = 3
PITCH = CELL + GAP

PAD = 16
DAY_LABEL_W = 30
GRID_X = PAD + DAY_LABEL_W
MONTH_BASELINE = 24
GRID_Y = 32

ROW_DELAY = 0.10       # line-after-line
COL_DELAY = 0.008      # ...with a diagonal lean
CELL_DUR = 0.38
BASE_DELAY = 0.12

FONT_STACK = (
    "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, "
    "'DejaVu Sans Mono', 'Liberation Mono', monospace"
)
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def percentile(values: list[int], p: float) -> float:
    """Nearest-rank percentile; avoids pulling numpy into the CI job."""
    if not values:
        return 0.0
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, int(round(p / 100.0 * (len(ordered) - 1)))))
    return float(ordered[k])


def build_grid(days: list[dict]) -> tuple[list[list[dict | None]], date]:
    """Bucket days into columns of 7, weeks starting Sunday, keeping the last 53."""
    by_date = {date.fromisoformat(d["date"]): d for d in days}
    first, last = min(by_date), max(by_date)

    # Sunday-align the first column (Python: Monday=0, so Sunday -> 6).
    start = first - timedelta(days=(first.weekday() + 1) % 7)
    n_cols = ((last - start).days // 7) + 1

    if n_cols > WEEKS:
        start += timedelta(weeks=n_cols - WEEKS)
        n_cols = WEEKS

    grid: list[list[dict | None]] = []
    for col in range(n_cols):
        column: list[dict | None] = []
        for row in range(7):
            d = start + timedelta(days=col * 7 + row)
            column.append(by_date.get(d))
        grid.append(column)
    return grid, start


def boost_levels(grid: list[list[dict | None]]) -> None:
    """Promote the top decile of level-4 days to the neon level 5."""
    counts = [d["count"] for col in grid for d in col if d and d["count"] > 0]
    if not counts:
        return
    threshold = max(percentile(counts, 90.0), 1.0)
    for col in grid:
        for d in col:
            if d and d["level"] >= 4 and d["count"] >= threshold:
                d["level"] = 5


def build_svg(payload: dict) -> str:
    days = payload["days"]
    stats = payload.get("stats", {})

    grid, start = build_grid(days)
    boost_levels(grid)
    n_cols = len(grid)

    width = GRID_X + (n_cols * PITCH - GAP) + PAD
    grid_bottom = GRID_Y + 7 * PITCH - GAP
    foot1_y = grid_bottom + 26
    foot2_y = foot1_y + 19
    height = foot2_y + 14

    # --- one CSS class per distinct delay, so the file stays small -----------
    classes: dict[int, str] = {}

    def delay_class(seconds: float) -> str:
        ms = int(round(seconds * 1000))
        if ms not in classes:
            classes[ms] = f"t{len(classes)}"
        return classes[ms]

    cells: list[str] = []
    max_delay = 0.0
    for col_i, column in enumerate(grid):
        x = GRID_X + col_i * PITCH
        for row_i, day in enumerate(column):
            if day is None:
                continue
            y = GRID_Y + row_i * PITCH
            delay = BASE_DELAY + row_i * ROW_DELAY + col_i * COL_DELAY
            max_delay = max(max_delay, delay)
            fill = PALETTE[min(day["level"], len(PALETTE) - 1)]
            label = f'{day["count"]} on {day["date"]}'
            cells.append(
                f'<rect class="c {delay_class(delay)}" x="{x}" y="{y}" '
                f'width="{CELL}" height="{CELL}" rx="2.5" fill="{fill}">'
                f"<title>{esc(label)}</title></rect>"
            )

    tail_delay = max_delay + CELL_DUR + 0.05
    tail_class = delay_class(tail_delay)

    # --- month labels along the top -----------------------------------------
    month_labels: list[str] = []
    last_month = -1
    last_x = -999
    for col_i in range(n_cols):
        col_date = start + timedelta(days=col_i * 7)
        if col_date.month != last_month:
            x = GRID_X + col_i * PITCH
            # Skip a label that would collide with the previous one or overrun.
            if x - last_x >= 34 and x + 26 <= width - PAD:
                month_labels.append(
                    f'<text class="c {delay_class(BASE_DELAY + col_i * COL_DELAY)}" '
                    f'x="{x}" y="{MONTH_BASELINE}" fill="{DIM}" font-size="11">'
                    f"{MONTHS[col_date.month - 1]}</text>"
                )
                last_x = x
            last_month = col_date.month

    # --- weekday labels down the left ---------------------------------------
    day_labels = []
    for row_i, name in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        y = GRID_Y + row_i * PITCH + CELL - 2
        day_labels.append(
            f'<text class="c {delay_class(BASE_DELAY + row_i * ROW_DELAY)}" '
            f'x="{GRID_X - 8}" y="{y}" text-anchor="end" fill="{DIM}" font-size="11">'
            f"{name}</text>"
        )

    # --- legend, right-aligned on the first footer line ----------------------
    sw, sgap = 11, 3
    legend_boxes_w = len(PALETTE) * (sw + sgap) - sgap
    legend_w = 30 + 6 + legend_boxes_w + 6 + 34
    lx = width - PAD - legend_w
    legend = [
        f'<text x="{lx}" y="{foot1_y}" fill="{DIM}" font-size="11">Less</text>'
    ]
    for j, color in enumerate(PALETTE):
        bx = lx + 36 + j * (sw + sgap)
        legend.append(
            f'<rect x="{bx}" y="{foot1_y - sw + 1}" width="{sw}" height="{sw}" '
            f'rx="2.5" fill="{color}"/>'
        )
    legend.append(
        f'<text x="{lx + legend_w}" y="{foot1_y}" text-anchor="end" fill="{DIM}" '
        f'font-size="11">More</text>'
    )

    # --- footer text ---------------------------------------------------------
    total = stats.get("total", sum(d["count"] for d in days))
    cur = stats.get("current_streak", {}).get("length", 0)
    longest = stats.get("longest_streak", {}).get("length", 0)
    best = stats.get("best_day", {"count": 0, "date": days[-1]["date"]})

    foot1 = (
        f'<text x="{PAD}" y="{foot1_y}" fill="{FG}" font-size="12.5">'
        f'<tspan fill="{ACCENT}" font-weight="700">{total:,}</tspan>'
        f" contributions in the last year</text>"
    )
    foot2 = (
        f'<text x="{PAD}" y="{foot2_y}" fill="{DIM}" font-size="11">'
        f"Current streak {cur}d &#183; Longest {longest}d &#183; "
        f'Best day {best["count"]} on {esc(best["date"])}</text>'
    )

    delay_css = "".join(f".{name}{{animation-delay:{ms}ms}}" for ms, name in classes.items())
    style = (
        "<style><![CDATA["
        f".c{{opacity:0;animation:drop {CELL_DUR}s cubic-bezier(.22,.61,.36,1) forwards}}"
        "@keyframes drop{"
        "from{opacity:0;transform:translateY(-7px)}"
        "to{opacity:1;transform:translateY(0)}}"
        + delay_css
        + "@media (prefers-reduced-motion: reduce){.c{opacity:1;animation:none}}"
        "]]></style>"
    )

    return "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="hmTitle">',
            f'<title id="hmTitle">{total:,} contributions in the last year</title>',
            style,
            f'<rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="10" '
            f'fill="{BG}" stroke="{BORDER}"/>',
            f'<g font-family="{FONT_STACK}">',
            *month_labels,
            *day_labels,
            *cells,
            f'<g class="c {tail_class}">',
            foot1,
            foot2,
            *legend,
            "</g>",
            "</g>",
            "</svg>",
        ]
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Render the contribution heatmap SVG.")
    ap.add_argument("-i", "--json", type=Path, default=DEFAULT_IN)
    ap.add_argument("-o", "--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    if not args.json.exists():
        print(f"error: {args.json} not found - run fetch_contributions.py first")
        return 1

    payload = json.loads(args.json.read_text(encoding="utf-8"))
    if not payload.get("days"):
        print("error: no days in the JSON payload")
        return 1

    svg = build_svg(payload)
    args.out.write_text(svg, encoding="utf-8")

    # Keep the README's width math honest: heatmap width should equal 370 + 490.
    print(f"-> wrote {args.out}  ({len(payload['days'])} days, {len(svg) / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
