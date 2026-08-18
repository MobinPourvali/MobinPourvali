#!/usr/bin/env python3
"""
Render an MS/MS peptide fragmentation spectrum as an animated SVG.

The m/z values here are not decorative: b and y ion masses are computed from
monoisotopic residue masses, so for any sequence you pass, the fragment
positions are exactly right. Peak *intensities* are simulated - real ones
depend on the instrument and collision energy - and the panel says so.

Default peptide is LVNELTEFAK, the BSA tryptic peptide used as a QC standard
in most proteomics labs ([M+2H]2+ = 582.3190).

    python scripts/make_msms_spectrum.py
    python scripts/make_msms_spectrum.py --peptide SAMPLERPEPTIDEK
    STATIC=1 python scripts/make_msms_spectrum.py   # frozen frame

Standard library only.
"""

from __future__ import annotations

import argparse
import math
import os
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "msms.svg"

# --------------------------------------------------------------------------- #
# chemistry - monoisotopic residue masses (Da)
# --------------------------------------------------------------------------- #
RESIDUE = {
    "G": 57.02146, "A": 71.03711, "S": 87.03203, "P": 97.05276, "V": 99.06841,
    "T": 101.04768, "C": 103.00919, "L": 113.08406, "I": 113.08406,
    "N": 114.04293, "D": 115.02694, "Q": 128.05858, "K": 128.09496,
    "E": 129.04259, "M": 131.04049, "H": 137.05891, "F": 147.06841,
    "R": 156.10111, "Y": 163.06333, "W": 186.07931,
}
PROTON = 1.007276
WATER = 18.010565
CAM = 57.02146          # carbamidomethyl on Cys, the usual fixed modification

W, H = 900, 440
M_L, M_R, M_T, M_B = 72, 26, 132, 58
PLOT_W = W - M_L - M_R
PLOT_H = H - M_T - M_B

BG = "#0d1117"
BORDER = "#30363d"
GRID = "#1b2027"
AXIS = "#4d5560"
FG = "#c9d1d9"
DIM = "#8b949e"
B_ION = "#00e0ff"       # cyan, N-terminal series
Y_ION = "#ff2d6f"       # magenta, C-terminal series
NOISE = "#39414a"

FONT = ("ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, "
        "'DejaVu Sans Mono', 'Liberation Mono', monospace")


def esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# --------------------------------------------------------------------------- #
# fragmentation
# --------------------------------------------------------------------------- #
def residue_masses(seq: str, cam_cys: bool) -> list[float]:
    out = []
    for aa in seq:
        if aa not in RESIDUE:
            raise SystemExit(f"error: {aa!r} is not a standard amino acid code")
        m = RESIDUE[aa]
        if aa == "C" and cam_cys:
            m += CAM
        out.append(m)
    return out


def fragments(seq: str, cam_cys: bool) -> tuple[list[dict], float, float]:
    """Singly-charged b and y ions, plus [M+H]+ and [M+2H]2+."""
    masses = residue_masses(seq, cam_cys)
    n = len(masses)
    peptide = sum(masses)
    mh = peptide + WATER + PROTON
    mh2 = (peptide + WATER + 2 * PROTON) / 2

    ions: list[dict] = []
    run = 0.0
    for i in range(n - 1):                       # b1..b(n-1)
        run += masses[i]
        ions.append({"series": "b", "idx": i + 1, "mz": run + PROTON})
    run = 0.0
    for i in range(n - 1):                       # y1..y(n-1)
        run += masses[n - 1 - i]
        ions.append({"series": "y", "idx": i + 1, "mz": run + WATER + PROTON})
    return ions, mh, mh2


def simulate_intensities(ions: list[dict], seq: str, seed: int) -> None:
    """
    Illustrative intensities. Loosely follows tryptic behaviour - y ions
    dominate when the C-terminus is K/R, mid-length fragments are strongest -
    but these are not measured values.
    """
    rng = random.Random(seed)
    n = len(seq)
    y_bias = 1.55 if seq[-1] in "KR" else 1.0
    for ion in ions:
        centre = (n - 1) / 2.0
        shape = math.exp(-((ion["idx"] - centre) ** 2) / (2 * (n / 3.2) ** 2))
        base = shape * (y_bias if ion["series"] == "y" else 1.0)
        # Proline effect: cleavage N-terminal to Pro is enhanced.
        pos = ion["idx"] if ion["series"] == "b" else n - ion["idx"]
        if 0 <= pos < n and seq[pos] == "P":
            base *= 1.9
        ion["inten"] = max(0.04, min(1.0, base * rng.uniform(0.45, 1.15)))


def noise_peaks(n: int, lo: float, hi: float, seed: int) -> list[dict]:
    rng = random.Random(seed + 1)
    return [{"series": "noise", "idx": 0, "mz": rng.uniform(lo, hi),
             "inten": rng.uniform(0.01, 0.09)} for _ in range(n)]


# --------------------------------------------------------------------------- #
# render
# --------------------------------------------------------------------------- #
def build_svg(seq: str, ions: list[dict], noise: list[dict], mh: float,
              mh2: float, cam_cys: bool, label_top: int, static: bool) -> str:
    lo, hi = 80.0, mh + 70
    everything = sorted(ions + noise, key=lambda d: d["mz"])

    def sx(mz: float) -> float:
        return M_L + (mz - lo) / (hi - lo) * PLOT_W

    def sy(inten: float) -> float:
        return M_T + PLOT_H - inten * PLOT_H

    # --- grid + m/z axis ----------------------------------------------------
    grid = []
    step = 200
    v = math.ceil(lo / step) * step
    while v <= hi:
        x = sx(v)
        grid.append(f'<line x1="{x:.1f}" y1="{M_T}" x2="{x:.1f}" y2="{M_T + PLOT_H}" stroke="{GRID}"/>')
        grid.append(f'<text x="{x:.1f}" y="{M_T + PLOT_H + 19}" text-anchor="middle" '
                    f'fill="{DIM}" font-size="11">{v}</text>')
        v += step
    for frac in (0.25, 0.5, 0.75, 1.0):
        y = sy(frac)
        grid.append(f'<line x1="{M_L}" y1="{y:.1f}" x2="{M_L + PLOT_W}" y2="{y:.1f}" stroke="{GRID}"/>')
        grid.append(f'<text x="{M_L - 9}" y="{y + 4:.1f}" text-anchor="end" fill="{DIM}" '
                    f'font-size="10.5">{int(frac * 100)}</text>')
    grid.append(f'<line x1="{M_L}" y1="{M_T + PLOT_H}" x2="{M_L + PLOT_W}" y2="{M_T + PLOT_H}" '
                f'stroke="{AXIS}"/>')

    # --- peaks (stick spectrum), left to right like an accumulating scan -----
    peaks = []
    n_waves = 26
    for i, pk in enumerate(everything):
        wave = i * n_waves // max(1, len(everything))
        x, y = sx(pk["mz"]), sy(pk["inten"])
        color = {"b": B_ION, "y": Y_ION}.get(pk["series"], NOISE)
        wcls = "" if static else f' class="pk w{wave}"'
        tip = (f'{pk["series"]}{pk["idx"]}  m/z {pk["mz"]:.4f}'
               if pk["series"] != "noise" else f'm/z {pk["mz"]:.2f}')
        peaks.append(
            f'<rect{wcls} x="{x - 1.1:.1f}" y="{y:.1f}" width="2.2" '
            f'height="{M_T + PLOT_H - y:.1f}" fill="{color}">'
            f'<title>{esc(tip)}</title></rect>'
        )

    # --- ion labels on the strongest assigned peaks -------------------------
    labels = []
    for pk in sorted(ions, key=lambda d: d["inten"], reverse=True)[:label_top]:
        x, y = sx(pk["mz"]), sy(pk["inten"])
        color = B_ION if pk["series"] == "b" else Y_ION
        labels.append(
            f'<text class="lb" x="{x:.1f}" y="{y - 6:.1f}" text-anchor="middle" '
            f'fill="{color}" font-size="10">{pk["series"]}{pk["idx"]}</text>'
        )

    # --- annotated sequence ladder ------------------------------------------
    ladder = []
    n = len(seq)
    cw = 26
    seq_w = n * cw
    x0 = M_L + (PLOT_W - seq_w) / 2
    base_y = 84
    for i, aa in enumerate(seq):
        cx = x0 + i * cw + cw / 2
        ladder.append(f'<text class="sq s{i}" x="{cx:.1f}" y="{base_y}" text-anchor="middle" '
                      f'fill="{FG}" font-size="17" font-weight="700">{aa}</text>')
        if i < n - 1:                                   # b tick: down-right
            bx = x0 + (i + 1) * cw
            ladder.append(f'<path class="sq s{i}" d="M{bx:.1f},{base_y - 16} v-9 h-7" '
                          f'stroke="{B_ION}" stroke-width="1.4" fill="none"/>')
            ladder.append(f'<text class="sq s{i}" x="{bx - 9:.1f}" y="{base_y - 28}" '
                          f'text-anchor="end" fill="{B_ION}" font-size="8.5">b{i + 1}</text>')
            yx = x0 + (i + 1) * cw
            ladder.append(f'<path class="sq s{i}" d="M{yx:.1f},{base_y + 6} v9 h7" '
                          f'stroke="{Y_ION}" stroke-width="1.4" fill="none"/>')
            ladder.append(f'<text class="sq s{i}" x="{yx + 9:.1f}" y="{base_y + 23}" '
                          f'fill="{Y_ION}" font-size="8.5">y{n - 1 - i}</text>')

    # --- chrome -------------------------------------------------------------
    mod = " · Cys+CAM" if (cam_cys and "C" in seq) else ""
    head = [
        f'<text x="{M_L}" y="26" fill="{FG}" font-size="13" font-weight="700">'
        f'msms.py &#8212; MS/MS fragmentation</text>',
        f'<text x="{M_L}" y="42" fill="{DIM}" font-size="11">'
        f'{esc(seq)} &#183; [M+H]&#8314; {mh:.4f} &#183; [M+2H]&#178;&#8314; {mh2:.4f}{esc(mod)}</text>',
    ]
    legend = []
    lx = W - M_R
    for text, color in ((f"y ions", Y_ION), (f"b ions", B_ION)):
        wpx = len(text) * 6.4 + 20
        lx -= wpx
        legend.append(f'<rect x="{lx + 2:.1f}" y="17" width="8" height="8" fill="{color}"/>')
        legend.append(f'<text x="{lx + 15:.1f}" y="25" fill="{DIM}" font-size="11">{text}</text>')

    foot = [
        f'<text x="{M_L + PLOT_W / 2:.0f}" y="{H - 16}" text-anchor="middle" fill="{DIM}" '
        f'font-size="11.5">m/z</text>',
        f'<text transform="translate(19,{M_T + PLOT_H / 2:.0f}) rotate(-90)" text-anchor="middle" '
        f'fill="{DIM}" font-size="11.5">rel. intensity (%)</text>',
        f'<text x="{W - M_R}" y="{H - 16}" text-anchor="end" fill="{DIM}" font-size="10">'
        f'exact monoisotopic m/z &#183; simulated intensities</text>',
    ]

    if static:
        style = ""
    else:
        waves = "".join(f".w{i}{{animation-delay:{1.15 + i * 0.05:.3f}s}}" for i in range(26))
        seqs = "".join(f".s{i}{{animation-delay:{0.25 + i * 0.07:.3f}s}}" for i in range(len(seq)))
        style = (
            "<style><![CDATA["
            "rect{transform-box:fill-box;transform-origin:bottom}"
            ".fr{opacity:0;animation:fade .5s ease-out forwards}"
            ".sq{opacity:0;animation:fade .4s ease-out forwards}"
            ".pk{opacity:0;animation:rise .42s cubic-bezier(.22,.61,.36,1) forwards}"
            ".lb{opacity:0;animation:fade .5s ease-out forwards;animation-delay:2.75s}"
            "@keyframes fade{to{opacity:1}}"
            "@keyframes rise{from{opacity:0;transform:scaleY(0)}to{opacity:1;transform:scaleY(1)}}"
            + waves + seqs +
            "@media (prefers-reduced-motion: reduce){"
            ".fr,.sq,.pk,.lb{opacity:1;animation:none}}"
            "]]></style>"
        )

    fr = "" if static else ' class="fr"'
    return "\n".join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" role="img" aria-labelledby="mt">',
        f'<title id="mt">MS/MS fragmentation spectrum of {esc(seq)}</title>',
        style,
        '<defs><filter id="mglow" x="-60%" y="-60%" width="220%" height="220%">'
        '<feGaussianBlur stdDeviation="2.2" result="b"/>'
        '<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>'
        '</filter></defs>',
        f'<rect x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="10" fill="{BG}" stroke="{BORDER}"/>',
        f'<g font-family="{FONT}">',
        f'<g{fr}>' + "".join(head + legend) + "</g>",
        "".join(ladder),
        f'<g{fr}>' + "".join(grid) + "</g>",
        '<g filter="url(#mglow)">' + "".join(peaks) + "</g>",
        "".join(labels),
        f'<g{fr}>' + "".join(foot) + "</g>",
        "</g></svg>",
    ])


def main() -> int:
    ap = argparse.ArgumentParser(description="Animated MS/MS spectrum SVG.")
    ap.add_argument("--peptide", default="LVNELTEFAK",
                    help="peptide sequence (default: BSA QC standard)")
    ap.add_argument("-o", "--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--no-cam", action="store_true", help="disable Cys carbamidomethyl")
    ap.add_argument("--noise", type=int, default=45, help="unassigned background peaks")
    ap.add_argument("--labels", type=int, default=10)
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--static", action="store_true")
    args = ap.parse_args()

    seq = args.peptide.strip().upper()
    if len(seq) < 3:
        raise SystemExit("error: peptide must be at least 3 residues")

    cam = not args.no_cam
    ions, mh, mh2 = fragments(seq, cam)
    simulate_intensities(ions, seq, args.seed)
    noise = noise_peaks(args.noise, 80.0, mh + 60, args.seed)

    static = args.static or os.environ.get("STATIC") == "1"
    svg = build_svg(seq, ions, noise, mh, mh2, cam, args.labels, static)
    args.out.write_text(svg, encoding="utf-8")

    print(f"-> wrote {args.out}  ({seq}, {len(ions)} b/y ions, "
          f"[M+2H]2+ = {mh2:.4f}, {len(svg) / 1024:.1f} KB{', static' if static else ''})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
