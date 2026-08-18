#!/usr/bin/env python3
"""
Render a differential-expression volcano plot as an animated SVG.

Reads a DESeq2 / edgeR style results table and draws it in the same dark
terminal idiom as the rest of the profile: the frame and axes appear first,
the significance thresholds draw themselves, the point cloud fills in in
waves, and the strongest hits label themselves last.

Standard library only - CI needs no scientific stack.

    python scripts/make_volcano_plot.py --csv results/de_results.csv
    python scripts/make_volcano_plot.py --demo          # simulated data
    STATIC=1 python scripts/make_volcano_plot.py --demo # frozen frame

Column names default to DESeq2's output and are overridable:
    --gene-col gene --lfc-col log2FoldChange --p-col padj
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "volcano.svg"

# --------------------------------------------------------------------------- #
# geometry
# --------------------------------------------------------------------------- #
W, H = 900, 580
M_L, M_R, M_T, M_B = 72, 26, 56, 60
PLOT_W = W - M_L - M_R
PLOT_H = H - M_T - M_B

# --------------------------------------------------------------------------- #
# theme
# --------------------------------------------------------------------------- #
BG = "#0d1117"
BORDER = "#30363d"
GRID = "#1b2027"
AXIS = "#4d5560"
FG = "#c9d1d9"
DIM = "#8b949e"

UP = "#ff2d6f"      # upregulated  - neon magenta/red
DOWN = "#00e0ff"    # downregulated - neon cyan
NS = "#39414a"      # not significant - muted grey

LFC_THR = 1.0       # |log2FC| cutoff
P_THR = 0.05        # adjusted p cutoff

N_WAVES = 30        # point cloud fills in this many staggered waves
FONT = ("ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, "
        "'DejaVu Sans Mono', 'Liberation Mono', monospace")


def esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# --------------------------------------------------------------------------- #
# data
# --------------------------------------------------------------------------- #
def neg_log10(p: float) -> float:
    return -math.log10(max(p, 1e-300))


def load_csv(path: Path, gene_col: str, lfc_col: str, p_col: str) -> list[dict]:
    rows: list[dict] = []
    skipped = 0
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in (lfc_col, p_col) if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(
                f"error: {path} has no column(s) {missing}. "
                f"Found: {reader.fieldnames}. Use --lfc-col / --p-col / --gene-col."
            )
        for i, r in enumerate(reader):
            try:
                lfc = float(r[lfc_col])
                p = float(r[p_col])
            except (TypeError, ValueError):
                skipped += 1          # NA padj is normal in DESeq2 output
                continue
            if math.isnan(lfc) or math.isnan(p):
                skipped += 1
                continue
            name = (r.get(gene_col) or "").strip() or f"g{i}"
            rows.append({"gene": name, "lfc": lfc, "p": p, "nlp": neg_log10(p)})
    if skipped:
        print(f"   skipped {skipped} row(s) with NA/unparseable values")
    if not rows:
        raise SystemExit(f"error: no usable rows in {path}")
    return rows


def demo_data(n: int, seed: int) -> list[dict]:
    """Simulated results. Clearly stamped in the output - never pass this off as real."""
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        lfc = rng.gauss(0, 0.55)
        if rng.random() < 0.055:                       # a minority carry real signal
            lfc += rng.choice((-1, 1)) * rng.uniform(1.1, 4.2)
        z = abs(lfc) / (0.30 + rng.expovariate(7.0))
        p = math.erfc(z / math.sqrt(2)) or 1e-300
        p = min(1.0, p * rng.uniform(1.0, 2.4))        # crude multiple-testing inflation
        rows.append({"gene": f"GENE{i:05d}", "lfc": lfc, "p": p, "nlp": neg_log10(p)})
    return rows


def classify(r: dict, lfc_thr: float, p_thr: float) -> str:
    if r["p"] > p_thr or abs(r["lfc"]) < lfc_thr:
        return "ns"
    return "up" if r["lfc"] > 0 else "down"


# --------------------------------------------------------------------------- #
# axes helpers
# --------------------------------------------------------------------------- #
def nice_step(span: float, target: int = 6) -> float:
    if span <= 0:
        return 1.0
    raw = span / target
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def fmt(v: float) -> str:
    return f"{v:.0f}" if abs(v - round(v)) < 1e-9 else f"{v:g}"


# --------------------------------------------------------------------------- #
# render
# --------------------------------------------------------------------------- #
def build_svg(rows: list[dict], title: str, subtitle: str, lfc_thr: float,
              p_thr: float, n_labels: int, max_points: int, static: bool,
              demo: bool) -> str:
    for r in rows:
        r["cls"] = classify(r, lfc_thr, p_thr)

    n_up = sum(1 for r in rows if r["cls"] == "up")
    n_down = sum(1 for r in rows if r["cls"] == "down")
    n_total = len(rows)

    # Axis ranges, clipped so a couple of extreme genes don't flatten everything.
    xs = sorted(abs(r["lfc"]) for r in rows)
    ys = sorted(r["nlp"] for r in rows)
    xmax = max(xs[int(len(xs) * 0.999) - 1], lfc_thr * 2, 1.0) * 1.08
    ymax = max(ys[int(len(ys) * 0.999) - 1], neg_log10(p_thr) * 2, 1.0) * 1.08

    def sx(lfc: float) -> float:
        return M_L + (max(-xmax, min(xmax, lfc)) + xmax) / (2 * xmax) * PLOT_W

    def sy(nlp: float) -> float:
        return M_T + PLOT_H - (min(nlp, ymax) / ymax) * PLOT_H

    # Thin the non-significant cloud first; significant points are the signal.
    sig = [r for r in rows if r["cls"] != "ns"]
    nonsig = [r for r in rows if r["cls"] == "ns"]
    budget = max(0, max_points - len(sig))
    if len(nonsig) > budget:
        nonsig = random.Random(1).sample(nonsig, budget)
    drawn = sig + nonsig

    # Shuffle so waves scatter across the plot instead of sweeping in row order.
    order = list(range(len(drawn)))
    random.Random(7).shuffle(order)

    parts: list[str] = []

    # --- grid + axes --------------------------------------------------------
    grid: list[str] = []
    xstep = nice_step(2 * xmax)
    v = -math.floor(xmax / xstep) * xstep
    while v <= xmax + 1e-9:
        x = sx(v)
        grid.append(f'<line x1="{x:.1f}" y1="{M_T}" x2="{x:.1f}" y2="{M_T + PLOT_H}" stroke="{GRID}"/>')
        grid.append(f'<text x="{x:.1f}" y="{M_T + PLOT_H + 20}" text-anchor="middle" '
                    f'fill="{DIM}" font-size="11">{fmt(v)}</text>')
        v += xstep

    ystep = nice_step(ymax)
    v = 0.0
    while v <= ymax + 1e-9:
        y = sy(v)
        grid.append(f'<line x1="{M_L}" y1="{y:.1f}" x2="{M_L + PLOT_W}" y2="{y:.1f}" stroke="{GRID}"/>')
        grid.append(f'<text x="{M_L - 10}" y="{y + 4:.1f}" text-anchor="end" '
                    f'fill="{DIM}" font-size="11">{fmt(v)}</text>')
        v += ystep

    # --- threshold lines (self-drawing) -------------------------------------
    # SMIL rather than CSS stroke-dashoffset: only three elements, and it leaves
    # stroke-dasharray free to render these as conventional dashed cutoff lines.
    thr: list[str] = []
    style_thr = f'stroke="{AXIS}" stroke-width="1.2" stroke-dasharray="5 4"'
    yv = sy(neg_log10(p_thr))
    grow = "" if static else (
        f'<animate attributeName="x2" from="{M_L}" to="{M_L + PLOT_W}" '
        f'dur="0.75s" begin="0.45s" fill="freeze"/>')
    x2 = M_L if not static else M_L + PLOT_W
    thr.append(f'<line x1="{M_L}" y1="{yv:.1f}" x2="{x2}" y2="{yv:.1f}" {style_thr}>{grow}</line>')
    for lv in (-lfc_thr, lfc_thr):
        xv = sx(lv)
        y0, y1 = M_T + PLOT_H, M_T
        grow = "" if static else (
            f'<animate attributeName="y2" from="{y0}" to="{y1}" '
            f'dur="0.75s" begin="0.45s" fill="freeze"/>')
        y2 = y0 if not static else y1
        thr.append(f'<line x1="{xv:.1f}" y1="{y0}" x2="{xv:.1f}" y2="{y2}" {style_thr}>{grow}</line>')

    # --- points -------------------------------------------------------------
    pts_ns: list[str] = []
    pts_sig: list[str] = []
    for wave_pos, idx in enumerate(order):
        r = drawn[idx]
        wave = wave_pos * N_WAVES // max(1, len(order))
        x, y = sx(r["lfc"]), sy(r["nlp"])
        if r["cls"] == "ns":
            pts_ns.append(f'<circle class="pt w{wave}" cx="{x:.1f}" cy="{y:.1f}" r="2.1" fill="{NS}"/>')
        else:
            color = UP if r["cls"] == "up" else DOWN
            pts_sig.append(
                f'<circle class="pt w{wave}" cx="{x:.1f}" cy="{y:.1f}" r="3.0" fill="{color}">'
                f'<title>{esc(r["gene"])}  log2FC={r["lfc"]:.2f}  p={r["p"]:.2g}</title></circle>'
            )

    # --- labels for the strongest hits --------------------------------------
    ranked = sorted(sig, key=lambda r: abs(r["lfc"]) * r["nlp"], reverse=True)
    labels: list[str] = []
    placed: list[tuple[float, float]] = []
    for r in ranked:
        if len(labels) >= n_labels:
            break
        x, y = sx(r["lfc"]), sy(r["nlp"])
        if any(abs(x - px) < 82 and abs(y - py) < 15 for px, py in placed):
            continue
        placed.append((x, y))
        left = r["lfc"] > 0
        tx = x - 7 if left else x + 7
        anchor = "end" if left else "start"
        labels.append(
            f'<text class="lb" x="{tx:.1f}" y="{y + 3.5:.1f}" text-anchor="{anchor}" '
            f'fill="{FG}" font-size="10.5">{esc(r["gene"])}</text>'
        )

    # --- chrome -------------------------------------------------------------
    head = [
        f'<text x="{M_L}" y="26" fill="{FG}" font-size="13" font-weight="700">{esc(title)}</text>',
        f'<text x="{M_L}" y="42" fill="{DIM}" font-size="11">{esc(subtitle)}</text>',
    ]
    legend = []
    lx = W - M_R
    for label, color in ((f"{n_down:,} down", DOWN), (f"{n_up:,} up", UP)):
        wpx = len(label) * 6.4 + 20
        lx -= wpx
        legend.append(f'<circle cx="{lx + 5:.1f}" cy="22" r="4.5" fill="{color}"/>')
        legend.append(f'<text x="{lx + 14:.1f}" y="26" fill="{DIM}" font-size="11">{esc(label)}</text>')

    foot = [
        f'<text x="{M_L + PLOT_W / 2:.0f}" y="{H - 16}" text-anchor="middle" fill="{DIM}" '
        f'font-size="11.5">log2 fold change</text>',
        f'<text transform="translate(20,{M_T + PLOT_H / 2:.0f}) rotate(-90)" text-anchor="middle" '
        f'fill="{DIM}" font-size="11.5">-log10 adjusted p</text>',
        f'<text x="{W - M_R}" y="{H - 16}" text-anchor="end" fill="{DIM}" font-size="10.5">'
        f'{n_total:,} genes tested &#183; |log2FC| &#8805; {fmt(lfc_thr)} &#183; padj &#8804; {fmt(p_thr)}</text>',
    ]
    if demo:
        foot.append(
            f'<text x="{M_L}" y="{H - 16}" fill="#d29922" font-size="10.5" font-weight="700">'
            f'SIMULATED DEMO DATA</text>'
        )

    # --- style --------------------------------------------------------------
    if static:
        style = ""
    else:
        waves = "".join(f".w{i}{{animation-delay:{0.95 + i * 0.055:.3f}s}}" for i in range(N_WAVES))
        style = (
            "<style><![CDATA["
            "circle{transform-box:fill-box;transform-origin:center}"
            ".fr{opacity:0;animation:fade .5s ease-out forwards}"
            ".pt{opacity:0;animation:pop .5s cubic-bezier(.22,.61,.36,1) forwards}"
            ".lb{opacity:0;animation:fade .55s ease-out forwards;animation-delay:2.85s}"
            "@keyframes fade{to{opacity:1}}"
            "@keyframes pop{from{opacity:0;transform:scale(.25)}to{opacity:1;transform:scale(1)}}"
            + waves +
            "@media (prefers-reduced-motion: reduce){"
            ".fr,.pt,.lb{opacity:1;animation:none}}"
            "]]></style>"
        )

    fr = "" if static else ' class="fr"'
    parts.append('<?xml version="1.0" encoding="UTF-8"?>')
    parts.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
                 f'viewBox="0 0 {W} {H}" role="img" aria-labelledby="vt">')
    parts.append(f'<title id="vt">{esc(title)}: {n_up} up, {n_down} down of {n_total} genes</title>')
    parts.append(style)
    parts.append('<defs><filter id="glow" x="-60%" y="-60%" width="220%" height="220%">'
                 '<feGaussianBlur stdDeviation="2.6" result="b"/>'
                 '<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>'
                 '</filter></defs>')
    parts.append(f'<rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="10" '
                 f'fill="{BG}" stroke="{BORDER}"/>')
    parts.append(f'<g font-family="{FONT}">')
    parts.append(f'<g{fr}>' + "".join(head + legend) + "</g>")
    parts.append(f'<g{fr}>' + "".join(grid) + "</g>")
    parts.append(f'<g{fr}><rect x="{M_L}" y="{M_T}" width="{PLOT_W}" height="{PLOT_H}" '
                 f'fill="none" stroke="{AXIS}"/></g>')
    parts.append("".join(thr))
    parts.append("<g>" + "".join(pts_ns) + "</g>")
    parts.append('<g filter="url(#glow)">' + "".join(pts_sig) + "</g>")
    parts.append("".join(labels))
    parts.append(f'<g{fr}>' + "".join(foot) + "</g>")
    parts.append("</g></svg>")
    return "\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description="Animated volcano plot SVG.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--csv", type=Path, help="DESeq2/edgeR results table")
    src.add_argument("--demo", action="store_true", help="simulated data (stamped as such)")
    ap.add_argument("-o", "--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--gene-col", default="gene")
    ap.add_argument("--lfc-col", default="log2FoldChange")
    ap.add_argument("--p-col", default="padj")
    ap.add_argument("--title", default="volcano.py -- differential expression")
    ap.add_argument("--subtitle", default="treated vs control")
    ap.add_argument("--lfc-thr", type=float, default=LFC_THR)
    ap.add_argument("--p-thr", type=float, default=P_THR)
    ap.add_argument("--labels", type=int, default=12)
    ap.add_argument("--max-points", type=int, default=2600)
    ap.add_argument("--demo-n", type=int, default=12000)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--static", action="store_true")
    args = ap.parse_args()

    if args.demo:
        rows = demo_data(args.demo_n, args.seed)
        subtitle = args.subtitle + "  (simulated)"
    else:
        rows = load_csv(args.csv, args.gene_col, args.lfc_col, args.p_col)
        subtitle = args.subtitle

    static = args.static or os.environ.get("STATIC") == "1"
    svg = build_svg(rows, args.title, subtitle, args.lfc_thr, args.p_thr,
                    args.labels, args.max_points, static, args.demo)
    args.out.write_text(svg, encoding="utf-8")

    up = sum(1 for r in rows if r["cls"] == "up")
    dn = sum(1 for r in rows if r["cls"] == "down")
    print(f"-> wrote {args.out}  ({len(rows):,} genes, {up} up / {dn} down, "
          f"{len(svg) / 1024:.1f} KB{', static' if static else ''})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
