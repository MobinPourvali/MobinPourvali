#!/usr/bin/env python3
"""
Render a BLAST-style local alignment as an animated SVG.

The alignment is genuinely computed - Smith-Waterman with affine gaps over
BLOSUM62 - against real sequences pulled from UniProt. Nothing here is a
mock-up: the identities, positives, gaps and score are what the algorithm
returns, and a reviewer can reproduce them.

The animation is a decoder sweep: a neon scan bar travels left to right,
scrambled residues ahead of it resolve into the true alignment behind it,
then it sweeps back and repeats.

    python scripts/make_alignment_svg.py
    python scripts/make_alignment_svg.py --query P04637 --subject P02340
    STATIC=1 python scripts/make_alignment_svg.py   # frozen frame

Standard library only. Sequences are cached under data/ after first fetch.
"""

from __future__ import annotations

import argparse
import os
import random
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "sequences"
DEFAULT_OUT = ROOT / "alignment.svg"

UNIPROT = "https://rest.uniprot.org/uniprotkb/{acc}.fasta"

# --------------------------------------------------------------------------- #
# BLOSUM62 (NCBI standard)
# --------------------------------------------------------------------------- #
_AA = "ARNDCQEGHILKMFPSTWYV"
_B62_ROWS = """
 4 -1 -2 -2  0 -1 -1  0 -2 -1 -1 -1 -1 -2 -1  1  0 -3 -2  0
-1  5  0 -2 -3  1  0 -2  0 -3 -2  2 -1 -3 -2 -1 -1 -3 -2 -3
-2  0  6  1 -3  0  0  0  1 -3 -3  0 -2 -3 -2  1  0 -4 -2 -3
-2 -2  1  6 -3  0  2 -1 -1 -3 -4 -1 -3 -3 -1  0 -1 -4 -3 -3
 0 -3 -3 -3  9 -3 -4 -3 -3 -1 -1 -3 -1 -2 -3 -1 -1 -2 -2 -1
-1  1  0  0 -3  5  2 -2  0 -3 -2  1  0 -3 -1  0 -1 -2 -1 -2
-1  0  0  2 -4  2  5 -2  0 -3 -3  1 -2 -3 -1  0 -1 -3 -2 -2
 0 -2  0 -1 -3 -2 -2  6 -2 -4 -4 -2 -3 -3 -2  0 -2 -2 -3 -3
-2  0  1 -1 -3  0  0 -2  8 -3 -3 -1 -2 -1 -2 -1 -2 -2  2 -3
-1 -3 -3 -3 -1 -3 -3 -4 -3  4  2 -3  1  0 -3 -2 -1 -3 -1  3
-1 -2 -3 -4 -1 -2 -3 -4 -3  2  4 -2  2  0 -3 -2 -1 -2 -1  1
-1  2  0 -1 -3  1  1 -2 -1 -3 -2  5 -1 -3 -1  0 -1 -3 -2 -2
-1 -1 -2 -3 -1  0 -2 -3 -2  1  2 -1  5  0 -2 -1 -1 -1 -1  1
-2 -3 -3 -3 -2 -3 -3 -3 -1  0  0 -3  0  6 -4 -2 -2  1  3 -1
-1 -2 -2 -1 -3 -1 -1 -2 -2 -3 -3 -1 -2 -4  7 -1 -1 -4 -3 -2
 1 -1  1  0 -1  0  0  0 -1 -2 -2  0 -1 -2 -1  4  1 -3 -2 -2
 0 -1  0 -1 -1 -1 -1 -2 -2 -1 -1 -1 -1 -2 -1  1  5 -2 -2  0
-3 -3 -4 -4 -2 -2 -3 -2 -2 -3 -2 -3 -1  1 -4 -3 -2 11  2 -3
-2 -2 -2 -3 -2 -1 -2 -3  2 -1 -1 -2 -1  3 -3 -2 -2  2  7 -1
 0 -3 -3 -3 -1 -2 -2 -3 -3  3  1 -2  1 -1 -2 -2  0 -3 -1  4
"""

def _load_b62() -> dict[tuple[str, str], int]:
    rows = [r.split() for r in _B62_ROWS.strip().split("\n")]
    assert len(rows) == 20 and all(len(r) == 20 for r in rows), "BLOSUM62 shape"
    m = {}
    for i, a in enumerate(_AA):
        for j, b in enumerate(_AA):
            m[(a, b)] = int(rows[i][j])
    # Symmetry is a cheap guard against a transcription slip.
    for (a, b), v in m.items():
        assert m[(b, a)] == v, f"BLOSUM62 asymmetric at {a}{b}"
    return m

B62 = _load_b62()
GAP_OPEN, GAP_EXTEND = 11, 1


def score(a: str, b: str) -> int:
    return B62.get((a, b), -4)


# --------------------------------------------------------------------------- #
# sequences
# --------------------------------------------------------------------------- #
def fetch_fasta(acc: str) -> tuple[str, str]:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{acc}.fasta"
    if not path.exists():
        req = urllib.request.Request(
            UNIPROT.format(acc=acc),
            headers={"User-Agent": "profile-art/1.0 (github profile SVG)"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            path.write_bytes(resp.read())
        print(f"   fetched {acc} from UniProt")
    text = path.read_text()
    lines = text.strip().split("\n")
    header = lines[0].lstrip(">")
    seq = "".join(l.strip() for l in lines[1:] if not l.startswith(">"))
    return header, seq


def short_name(header: str) -> str:
    parts = header.split("|")
    return parts[2].split()[0] if len(parts) >= 3 else header.split()[0]


# --------------------------------------------------------------------------- #
# Smith-Waterman, affine gaps
# --------------------------------------------------------------------------- #
def smith_waterman(a: str, b: str) -> dict:
    n, m = len(a), len(b)
    NEG = -10**9
    M = [[0] * (m + 1) for _ in range(n + 1)]
    Ix = [[NEG] * (m + 1) for _ in range(n + 1)]
    Iy = [[NEG] * (m + 1) for _ in range(n + 1)]
    ptr = [[0] * (m + 1) for _ in range(n + 1)]     # 0 stop, 1 diag, 2 up, 3 left

    best, bi, bj = 0, 0, 0
    for i in range(1, n + 1):
        ai = a[i - 1]
        Mi, Mi1 = M[i], M[i - 1]
        Ixi, Ixi1 = Ix[i], Ix[i - 1]
        Iyi = Iy[i]
        ptri = ptr[i]
        for j in range(1, m + 1):
            bj_ = b[j - 1]
            Ixi[j] = max(Mi1[j] - GAP_OPEN, Ixi1[j] - GAP_EXTEND)
            Iyi[j] = max(Mi[j - 1] - GAP_OPEN, Iyi[j - 1] - GAP_EXTEND)
            diag = max(Mi1[j - 1], Ixi1[j - 1], Iy[i - 1][j - 1])
            cand = max(0, diag) + score(ai, bj_)
            best_cell = max(cand, Ixi[j], Iyi[j], 0)
            Mi[j] = best_cell
            if best_cell == 0:
                ptri[j] = 0
            elif best_cell == cand:
                ptri[j] = 1
            elif best_cell == Ixi[j]:
                ptri[j] = 2
            else:
                ptri[j] = 3
            if best_cell > best:
                best, bi, bj = best_cell, i, j

    # traceback
    qa, sa = [], []
    i, j = bi, bj
    while i > 0 and j > 0 and M[i][j] > 0:
        d = ptr[i][j]
        if d == 1:
            qa.append(a[i - 1]); sa.append(b[j - 1]); i -= 1; j -= 1
        elif d == 2:
            qa.append(a[i - 1]); sa.append("-"); i -= 1
        elif d == 3:
            qa.append("-"); sa.append(b[j - 1]); j -= 1
        else:
            break
    qa.reverse(); sa.reverse()
    return {"score": best, "qseq": "".join(qa), "sseq": "".join(sa),
            "qstart": i + 1, "sstart": j + 1}


def annotate(qseq: str, sseq: str) -> tuple[str, int, int, int]:
    mid, ident, pos, gaps = [], 0, 0, 0
    for x, y in zip(qseq, sseq):
        if x == "-" or y == "-":
            mid.append(" "); gaps += 1
        elif x == y:
            mid.append(x); ident += 1; pos += 1
        elif score(x, y) > 0:
            mid.append("+"); pos += 1
        else:
            mid.append(" ")
    return "".join(mid), ident, pos, gaps


def best_window(qseq: str, sseq: str, width: int) -> int:
    """Offset of the most identity-dense window - the part worth showing."""
    if len(qseq) <= width:
        return 0
    hits = [1 if (x == y and x != "-") else 0 for x, y in zip(qseq, sseq)]
    run = sum(hits[:width]); best, at = run, 0
    for i in range(1, len(hits) - width + 1):
        run += hits[i + width - 1] - hits[i - 1]
        if run > best:
            best, at = run, i
    return at


# --------------------------------------------------------------------------- #
# render
# --------------------------------------------------------------------------- #
W = 860
M_L, M_R = 34, 34
FS = 15.0
CW = 9.0
COLS = 66
LINE_H = 22
LABEL_W = 108
SEQ_X = M_L + LABEL_W
BLOCK_TOP = 104
BLOCK_GAP = 92

BG = "#0d1117"
BORDER = "#30363d"
FG = "#c9d1d9"
DIM = "#8b949e"
IDENT = "#39ff14"     # neon green - exact identity
SIMIL = "#ffa657"     # amber - positive BLOSUM62 score
MISS = "#ff2d6f"      # magenta - mismatch
GAPC = "#495159"      # dim - gap
SCRAM = "#2f3742"     # unresolved, ahead of the scan bar

FONT = ("ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, "
        "'DejaVu Sans Mono', 'Liberation Mono', monospace")
CYCLE = 9.0


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def colour_of(x: str, y: str) -> str:
    if x == "-" or y == "-":
        return GAPC
    if x == y:
        return IDENT
    return SIMIL if score(x, y) > 0 else MISS


def text_runs(chars: list[str], colours: list[str], y: float) -> list[str]:
    """One <text> per colour class, each with an explicit per-glyph x list."""
    groups: dict[str, list[tuple[float, str]]] = {}
    for k, (ch, col) in enumerate(zip(chars, colours)):
        if ch == " ":
            continue
        groups.setdefault(col, []).append((SEQ_X + k * CW, ch))
    out = []
    for col, items in groups.items():
        xs = " ".join(f"{x:.1f}" for x, _ in items)
        txt = esc("".join(c for _, c in items))
        out.append(f'<text x="{xs}" y="{y:.1f}" fill="{col}">{txt}</text>')
    return out


def build_svg(qname: str, sname: str, aln: dict, blocks: int, static: bool,
              seed: int) -> str:
    qseq, sseq = aln["qseq"], aln["sseq"]
    mid, ident, pos, gaps = annotate(qseq, sseq)
    total = len(qseq)

    start = best_window(qseq, sseq, COLS * blocks)
    rng = random.Random(seed)

    height = BLOCK_TOP + blocks * BLOCK_GAP + 58
    aln_w = COLS * CW

    body: list[str] = []
    scrambled: list[str] = []

    # running residue coordinates up to the window start
    qpos = aln["qstart"] + sum(1 for c in qseq[:start] if c != "-")
    spos = aln["sstart"] + sum(1 for c in sseq[:start] if c != "-")

    for b in range(blocks):
        lo = start + b * COLS
        hi = min(lo + COLS, total)
        if lo >= total:
            break
        qs, ss, ms = qseq[lo:hi], sseq[lo:hi], mid[lo:hi]
        top = BLOCK_TOP + b * BLOCK_GAP

        q_end = qpos + sum(1 for c in qs if c != "-") - 1
        s_end = spos + sum(1 for c in ss if c != "-") - 1

        cols = [colour_of(x, y) for x, y in zip(qs, ss)]
        mcols = [IDENT if c not in " +" else (SIMIL if c == "+" else DIM) for c in ms]

        body.append(f'<text x="{M_L}" y="{top:.1f}" fill="{DIM}">Query</text>')
        body.append(f'<text x="{SEQ_X - 12}" y="{top:.1f}" text-anchor="end" fill="{DIM}">{qpos}</text>')
        body += text_runs(list(qs), cols, top)
        body.append(f'<text x="{SEQ_X + aln_w + 12}" y="{top:.1f}" fill="{DIM}">{q_end}</text>')

        body += text_runs(list(ms), mcols, top + LINE_H)

        body.append(f'<text x="{M_L}" y="{top + 2 * LINE_H:.1f}" fill="{DIM}">Sbjct</text>')
        body.append(f'<text x="{SEQ_X - 12}" y="{top + 2 * LINE_H:.1f}" text-anchor="end" fill="{DIM}">{spos}</text>')
        body += text_runs(list(ss), cols, top + 2 * LINE_H)
        body.append(f'<text x="{SEQ_X + aln_w + 12}" y="{top + 2 * LINE_H:.1f}" fill="{DIM}">{s_end}</text>')

        if not static:
            for row, n in ((0, len(qs)), (1, len(ms)), (2, len(ss))):
                junk = "".join(rng.choice(_AA) for _ in range(n))
                xs = " ".join(f"{SEQ_X + k * CW:.1f}" for k in range(n))
                scrambled.append(
                    f'<text x="{xs}" y="{top + row * LINE_H:.1f}" fill="{SCRAM}">{junk}</text>'
                )

        qpos = q_end + 1
        spos = s_end + 1

    pct = lambda v: f"{100.0 * v / total:.0f}%"
    stats_y = BLOCK_TOP + blocks * BLOCK_GAP - 4
    stats = [
        f'<text x="{M_L}" y="{stats_y:.1f}" fill="{FG}" font-size="12.5">'
        f'Score = <tspan fill="{IDENT}" font-weight="700">{aln["score"]}</tspan>'
        f' &#183; Identities = {ident}/{total} ({pct(ident)})'
        f' &#183; Positives = {pos}/{total} ({pct(pos)})'
        f' &#183; Gaps = {gaps}/{total} ({pct(gaps)})</text>',
        f'<text x="{M_L}" y="{stats_y + 19:.1f}" fill="{DIM}" font-size="11">'
        f'Smith-Waterman local alignment &#183; BLOSUM62 &#183; gap open {GAP_OPEN} / extend {GAP_EXTEND}'
        f' &#183; sequences from UniProt</text>',
    ]

    head = [
        f'<text x="{M_L}" y="32" fill="{FG}" font-size="14" font-weight="700">'
        f'align.py &#8212; local sequence alignment</text>',
        f'<text x="{M_L}" y="54" fill="{DIM}" font-size="11.5">'
        f'{esc(qname)} &#215; {esc(sname)}</text>',
    ]
    legend = []
    lx = W - M_R
    for label, col in (("gap", GAPC), ("mismatch", MISS), ("similar", SIMIL), ("identity", IDENT)):
        wpx = len(label) * 6.2 + 20
        lx -= wpx
        legend.append(f'<rect x="{lx:.1f}" y="24" width="9" height="9" fill="{col}"/>')
        legend.append(f'<text x="{lx + 13:.1f}" y="32" fill="{DIM}" font-size="10.5">{label}</text>')

    if static:
        defs = anim = ""
        scan = ""
    else:
        # Resolved layer grows left-to-right; the scrambled layer is its exact
        # complement, so the two always tile the alignment with no seam.
        defs = (
            "<defs>"
            f'<clipPath id="resolved"><rect x="{SEQ_X}" y="0" width="0" height="{height}">'
            f'<animate attributeName="width" values="0;{aln_w};{aln_w};0" '
            f'keyTimes="0;0.42;0.88;1" dur="{CYCLE}s" repeatCount="indefinite"/></rect></clipPath>'
            f'<clipPath id="pending"><rect x="{SEQ_X}" y="0" width="{aln_w}" height="{height}">'
            f'<animate attributeName="x" values="{SEQ_X};{SEQ_X + aln_w};{SEQ_X + aln_w};{SEQ_X}" '
            f'keyTimes="0;0.42;0.88;1" dur="{CYCLE}s" repeatCount="indefinite"/>'
            f'<animate attributeName="width" values="{aln_w};0;0;{aln_w}" '
            f'keyTimes="0;0.42;0.88;1" dur="{CYCLE}s" repeatCount="indefinite"/></rect></clipPath>'
            "</defs>"
        )
        bar_top = BLOCK_TOP - 16
        bar_h = blocks * BLOCK_GAP - 34
        scan = (
            f'<rect x="{SEQ_X}" y="{bar_top}" width="2" height="{bar_h}" fill="{IDENT}" opacity="0.9">'
            f'<animate attributeName="x" values="{SEQ_X};{SEQ_X + aln_w};{SEQ_X + aln_w};{SEQ_X}" '
            f'keyTimes="0;0.42;0.88;1" dur="{CYCLE}s" repeatCount="indefinite"/>'
            f'<animate attributeName="opacity" values="0.9;0.9;0;0;0.9" '
            f'keyTimes="0;0.42;0.5;0.88;1" dur="{CYCLE}s" repeatCount="indefinite"/></rect>'
        )
        anim = ""

    return "\n".join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{height}" '
        f'viewBox="0 0 {W} {height}" role="img" aria-labelledby="at">',
        f'<title id="at">{esc(qname)} vs {esc(sname)}: {ident}/{total} identities</title>',
        defs,
        '<defs><filter id="aglow" x="-30%" y="-30%" width="160%" height="160%">'
        '<feGaussianBlur stdDeviation="1.5" result="b"/>'
        '<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>'
        '</filter></defs>',
        f'<rect x="0.5" y="0.5" width="{W - 1}" height="{height - 1}" rx="10" '
        f'fill="{BG}" stroke="{BORDER}"/>',
        f'<g font-family="{FONT}" font-size="{FS}">',
        "".join(head + legend),
        (f'<g clip-path="url(#pending)">' + "".join(scrambled) + "</g>") if scrambled else "",
        (f'<g clip-path="url(#resolved)" filter="url(#aglow)">' if not static
         else '<g filter="url(#aglow)">') + "".join(body) + "</g>",
        scan,
        "".join(stats),
        "</g></svg>",
    ])


def main() -> int:
    ap = argparse.ArgumentParser(description="Animated BLAST-style alignment SVG.")
    ap.add_argument("--query", default="P04637", help="UniProt accession (default human p53)")
    ap.add_argument("--subject", default="P02340", help="UniProt accession (default mouse p53)")
    ap.add_argument("-o", "--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--blocks", type=int, default=2, help="alignment blocks of 66 columns")
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--static", action="store_true")
    args = ap.parse_args()

    qh, qs = fetch_fasta(args.query)
    sh, ss = fetch_fasta(args.subject)
    print(f"-> aligning {short_name(qh)} ({len(qs)} aa) x {short_name(sh)} ({len(ss)} aa)")

    aln = smith_waterman(qs, ss)
    mid, ident, pos, gaps = annotate(aln["qseq"], aln["sseq"])
    total = len(aln["qseq"])

    static = args.static or os.environ.get("STATIC") == "1"
    svg = build_svg(short_name(qh), short_name(sh), aln, args.blocks, static, args.seed)
    args.out.write_text(svg, encoding="utf-8")

    print(f"   score={aln['score']}  length={total}  "
          f"identities={ident} ({100*ident/total:.1f}%)  "
          f"positives={pos} ({100*pos/total:.1f}%)  gaps={gaps}")
    print(f"-> wrote {args.out}  ({len(svg)/1024:.1f} KB{', static' if static else ''})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
