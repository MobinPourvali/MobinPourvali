#!/usr/bin/env python3
"""
Prep a source photo for ASCII conversion.

Pipeline:
  1. rembg cuts the subject out of the background.
  2. OpenCV CLAHE boosts *local* contrast so a flatly-lit face gets real
     highlights and shadows (a global curve does not do this).
  3. The subject is composited onto pure white, so the background maps to
     the blank end of the ASCII ramp and only the subject prints.

Output: source-prepped.png (8-bit grayscale) at the repo root.

Usage:
    python scripts/prep_photo.py source-photo.jpg
    python scripts/prep_photo.py source-photo.jpg --clip-limit 3.5 --gamma 0.9
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "source-prepped.png"

# rembg models: u2net_human_seg is tuned for people; u2net is the generalist.
DEFAULT_MODEL = "u2net_human_seg"


# --------------------------------------------------------------------------- #
# steps
# --------------------------------------------------------------------------- #
def load_image(path: Path, max_width: int) -> Image.Image:
    img = Image.open(path)
    # Respect EXIF orientation before anything else touches the pixels.
    try:
        from PIL import ImageOps

        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    img = img.convert("RGBA")
    if img.width > max_width:
        h = round(img.height * max_width / img.width)
        img = img.resize((max_width, h), Image.LANCZOS)
    return img


def crop_box(img: Image.Image, spec: str) -> Image.Image:
    """
    Pre-crop to x0,y0,x1,y1 before segmentation.

    Values <= 1 are read as fractions of the image, larger values as pixels, so
    both "--crop 0.2,0.05,0.8,1" and "--crop 128,32,512,640" work.
    """
    try:
        nums = [float(v) for v in spec.split(",")]
        if len(nums) != 4:
            raise ValueError
    except ValueError:
        raise SystemExit(f"error: --crop wants four comma-separated numbers, got {spec!r}")

    w, h = img.size
    x0, y0, x1, y1 = nums
    if max(nums) <= 1.0:
        x0, x1 = x0 * w, x1 * w
        y0, y1 = y0 * h, y1 * h

    box = (
        max(0, int(round(x0))),
        max(0, int(round(y0))),
        min(w, int(round(x1))),
        min(h, int(round(y1))),
    )
    if box[2] <= box[0] or box[3] <= box[1]:
        raise SystemExit(f"error: --crop {spec!r} is an empty box")

    print(f"-> cropping to {box} (from {w}x{h})")
    return img.crop(box)


def keep_largest_subject(rgba: np.ndarray, min_frac: float = 0.05) -> np.ndarray:
    """
    Keep only the biggest connected blob in the alpha mask.

    rembg segments *every* person it finds, so a group photo comes back with
    bystanders attached. Dropping all but the largest component leaves the one
    subject who dominates the frame. Components smaller than min_frac of the
    largest are discarded outright.
    """
    alpha = rgba[:, :, 3]
    binary = (alpha > 8).astype(np.uint8)
    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    if n_labels <= 2:  # background + at most one blob
        return rgba

    # Label 0 is the background; find the largest real component.
    areas = stats[1:, cv2.CC_STAT_AREA]
    biggest = int(np.argmax(areas)) + 1
    dropped = sum(1 for a in areas if a >= min_frac * areas.max()) - 1

    out = rgba.copy()
    out[:, :, 3] = np.where(labels == biggest, alpha, 0)
    if dropped > 0:
        print(f"-> dropped {dropped} smaller subject(s) from the mask")
    return out


def cutout(img: Image.Image, model: str) -> Image.Image:
    """Remove the background. Falls back to a fully-opaque alpha if rembg fails."""
    try:
        from rembg import new_session, remove
    except ImportError:
        print("! rembg not installed - skipping background removal", file=sys.stderr)
        return img

    try:
        session = new_session(model)
    except Exception as exc:  # model download failed, unknown name, ...
        print(f"! rembg model {model!r} unavailable ({exc}); using 'u2net'", file=sys.stderr)
        session = new_session("u2net")

    out = remove(img, session=session, post_process_mask=True)
    return out.convert("RGBA")


def crop_to_subject(rgba: np.ndarray, pad_frac: float) -> np.ndarray:
    """Trim to the alpha bounding box plus a margin, so the grid isn't wasted on air."""
    alpha = rgba[:, :, 3]
    ys, xs = np.nonzero(alpha > 8)
    if ys.size == 0:
        return rgba

    y0, y1 = int(ys.min()), int(ys.max()) + 1
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    pad_y = int((y1 - y0) * pad_frac)
    pad_x = int((x1 - x0) * pad_frac)

    h, w = alpha.shape
    y0 = max(0, y0 - pad_y)
    y1 = min(h, y1 + pad_y)
    x0 = max(0, x0 - pad_x)
    x1 = min(w, x1 + pad_x)
    return rgba[y0:y1, x0:x1]


def local_contrast(gray: np.ndarray, mask: np.ndarray, clip: float, tiles: int) -> np.ndarray:
    """
    CLAHE, applied with the cut-out region filled to the subject's mean value.

    Without the fill, the transparent pixels (whose RGB is garbage) would drag
    the histogram of every tile that straddles the silhouette edge.
    """
    filled = gray.copy()
    if mask.any():
        filled[~mask] = int(gray[mask].mean())

    clahe = cv2.createCLAHE(clipLimit=clip, tileGridSize=(tiles, tiles))
    return clahe.apply(filled)


def stretch_levels(gray: np.ndarray, mask: np.ndarray, low_p: float, high_p: float) -> np.ndarray:
    """Map the subject's [low_p, high_p] percentile range onto the full 0-255 range."""
    if not mask.any():
        return gray
    lo, hi = np.percentile(gray[mask], [low_p, high_p])
    if hi - lo < 1e-3:
        return gray
    out = (gray.astype(np.float32) - lo) * (255.0 / (hi - lo))
    return np.clip(out, 0, 255).astype(np.uint8)


def apply_gamma(gray: np.ndarray, gamma: float) -> np.ndarray:
    if abs(gamma - 1.0) < 1e-3:
        return gray
    lut = np.array([((i / 255.0) ** gamma) * 255 for i in range(256)], dtype=np.uint8)
    return cv2.LUT(gray, lut)


def unsharp(gray: np.ndarray, amount: float) -> np.ndarray:
    if amount <= 0:
        return gray
    blur = cv2.GaussianBlur(gray, (0, 0), 2.0)
    out = cv2.addWeighted(gray.astype(np.float32), 1 + amount, blur.astype(np.float32), -amount, 0)
    return np.clip(out, 0, 255).astype(np.uint8)


def composite_on_white(gray: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """gray over pure white, using alpha as the matte. White -> ASCII spaces."""
    a = (alpha.astype(np.float32) / 255.0)
    out = gray.astype(np.float32) * a + 255.0 * (1.0 - a)
    return np.clip(out, 0, 255).astype(np.uint8)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="Prep a photo for ASCII conversion.")
    ap.add_argument("source", type=Path, help="input photo (jpg/png/...)")
    ap.add_argument("-o", "--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--model", default=DEFAULT_MODEL, help="rembg model name")
    ap.add_argument("--no-rembg", action="store_true", help="skip background removal")
    ap.add_argument("--crop", help="pre-crop as x0,y0,x1,y1 (fractions <=1, or pixels)")
    ap.add_argument("--keep-all", action="store_true",
                    help="keep every segmented subject (default: keep only the largest)")
    ap.add_argument("--max-width", type=int, default=1200, help="downscale before processing")
    ap.add_argument("--pad", type=float, default=0.04, help="crop margin as a fraction of subject size")
    ap.add_argument("--clip-limit", type=float, default=3.0, help="CLAHE clip limit")
    ap.add_argument("--tiles", type=int, default=8, help="CLAHE tile grid (NxN)")
    ap.add_argument("--low", type=float, default=1.0, help="black-point percentile")
    ap.add_argument("--high", type=float, default=99.0, help="white-point percentile")
    ap.add_argument("--gamma", type=float, default=0.95, help="<1 brightens, >1 darkens")
    ap.add_argument("--sharpen", type=float, default=0.35, help="unsharp amount (0 disables)")
    args = ap.parse_args()

    if not args.source.exists():
        print(f"error: {args.source} not found", file=sys.stderr)
        return 1

    print(f"-> loading {args.source}")
    img = load_image(args.source, args.max_width)

    if args.crop:
        img = crop_box(img, args.crop)

    if args.no_rembg:
        print("-> skipping background removal")
    else:
        print(f"-> removing background ({args.model})")
        img = cutout(img, args.model)

    rgba = np.array(img, dtype=np.uint8)
    if not args.no_rembg and not args.keep_all:
        rgba = keep_largest_subject(rgba)
    rgba = crop_to_subject(rgba, args.pad)

    rgb = rgba[:, :, :3]
    alpha = rgba[:, :, 3]
    mask = alpha > 8

    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)

    print(f"-> CLAHE (clip={args.clip_limit}, tiles={args.tiles}x{args.tiles})")
    gray = local_contrast(gray, mask, args.clip_limit, args.tiles)
    gray = stretch_levels(gray, mask, args.low, args.high)
    gray = apply_gamma(gray, args.gamma)
    gray = unsharp(gray, args.sharpen)

    print("-> compositing onto white")
    final = composite_on_white(gray, alpha)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(final, mode="L").save(args.out, optimize=True)

    covered = 100.0 * mask.mean()
    print(f"-> wrote {args.out}  ({final.shape[1]}x{final.shape[0]}, subject covers {covered:.1f}%)")
    print("   next: python scripts/make_ascii_svg.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
