#!/usr/bin/env python3
"""
Scrape the public contribution calendar - no token, no GraphQL API.

GitHub serves the same HTML fragment the profile page uses at
https://github.com/users/<username>/contributions. We parse the day cells and
write data/contributions.json with the raw days plus derived stats.

    python scripts/fetch_contributions.py
    python scripts/fetch_contributions.py --username someone-else

In GitHub Actions the username defaults to GITHUB_REPOSITORY_OWNER, so the
workflow needs no configuration.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "data" / "contributions.json"

URL = "https://github.com/users/{username}/contributions"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; profile-art/1.0; +https://github.com/{username})",
    "Accept": "text/html, application/xhtml+xml",
    "X-Requested-With": "XMLHttpRequest",
}

# "12 contributions on January 5th." / "No contributions on ..."
COUNT_RE = re.compile(r"^\s*(No|[\d,]+)\s+contribution", re.IGNORECASE)

# Last-resort mapping if GitHub ever drops both data-count and the tooltips.
LEVEL_FALLBACK = {0: 0, 1: 1, 2: 3, 3: 6, 4: 10}


# --------------------------------------------------------------------------- #
# fetch + parse
# --------------------------------------------------------------------------- #
def fetch_html(username: str, retries: int = 4, timeout: int = 30) -> str:
    url = URL.format(username=username)
    headers = {k: v.format(username=username) for k, v in HEADERS.items()}
    last: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            if resp.status_code == 404:
                raise SystemExit(f"error: no such user {username!r} (404)")
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as exc:
            last = exc
            wait = 2 ** attempt
            print(f"! attempt {attempt}/{retries} failed ({exc}); retrying in {wait}s",
                  file=sys.stderr)
            if attempt < retries:
                time.sleep(wait)

    raise SystemExit(f"error: could not fetch contributions: {last}")


def parse_days(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")

    cells = soup.select("td.ContributionCalendar-day[data-date]")
    if not cells:
        # Older markup used <rect>; be forgiving about which tag carries the data.
        cells = soup.select("[data-date][data-level]")
    if not cells:
        raise SystemExit("error: no day cells found - GitHub's markup may have changed")

    # Counts now live in sibling <tool-tip for="cell-id"> elements.
    tips: dict[str, str] = {}
    for tip in soup.find_all("tool-tip"):
        target = tip.get("for")
        if target:
            tips[target] = tip.get_text(" ", strip=True)

    days: list[dict] = []
    guessed = 0

    for cell in cells:
        iso = cell.get("data-date")
        if not iso:
            continue
        try:
            level = int(cell.get("data-level") or 0)
        except ValueError:
            level = 0

        count: int | None = None

        raw = cell.get("data-count")
        if raw is not None:
            try:
                count = int(str(raw).replace(",", ""))
            except ValueError:
                count = None

        if count is None:
            text = tips.get(cell.get("id", "")) or cell.get_text(" ", strip=True)
            m = COUNT_RE.match(text or "")
            if m:
                token = m.group(1)
                count = 0 if token.lower() == "no" else int(token.replace(",", ""))

        if count is None:
            count = LEVEL_FALLBACK.get(level, 0)
            guessed += 1

        days.append({"date": iso, "count": count, "level": level})

    if guessed:
        print(f"! estimated counts for {guessed} day(s) from data-level", file=sys.stderr)

    days.sort(key=lambda d: d["date"])
    # Guard against duplicate cells if the fragment ever repeats a day.
    seen: set[str] = set()
    unique = []
    for d in days:
        if d["date"] not in seen:
            seen.add(d["date"])
            unique.append(d)
    return unique


# --------------------------------------------------------------------------- #
# stats
# --------------------------------------------------------------------------- #
def compute_stats(days: list[dict]) -> dict:
    if not days:
        return {}

    counts = [d["count"] for d in days]
    total = sum(counts)
    active = sum(1 for c in counts if c > 0)

    best = max(days, key=lambda d: d["count"])

    # Current streak: walk backwards. A zero on the most recent day doesn't
    # break it - the day isn't over yet.
    i = len(days) - 1
    if days[i]["count"] == 0:
        i -= 1
    cur_len = 0
    cur_end = cur_start = None
    while i >= 0 and days[i]["count"] > 0:
        if cur_end is None:
            cur_end = days[i]["date"]
        cur_start = days[i]["date"]
        cur_len += 1
        i -= 1

    # Longest streak.
    best_len = run = 0
    best_start = best_end = run_start = None
    for d in days:
        if d["count"] > 0:
            if run == 0:
                run_start = d["date"]
            run += 1
            if run > best_len:
                best_len, best_start, best_end = run, run_start, d["date"]
        else:
            run = 0

    monthly: dict[str, int] = defaultdict(int)
    weekday: dict[str, int] = defaultdict(int)
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for d in days:
        dt = date.fromisoformat(d["date"])
        monthly[dt.strftime("%Y-%m")] += d["count"]
        weekday[names[dt.weekday()]] += d["count"]

    return {
        "total": total,
        "days_tracked": len(days),
        "active_days": active,
        "max_level": max(d["level"] for d in days),
        "best_day": {"date": best["date"], "count": best["count"]},
        "avg_per_day": round(total / len(days), 2),
        "avg_per_active_day": round(total / active, 2) if active else 0.0,
        "current_streak": {"length": cur_len, "start": cur_start, "end": cur_end},
        "longest_streak": {"length": best_len, "start": best_start, "end": best_end},
        "monthly": dict(sorted(monthly.items())),
        "weekday": {n: weekday[n] for n in names},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Scrape the public contribution calendar.")
    ap.add_argument(
        "-u",
        "--username",
        default=os.environ.get("GITHUB_REPOSITORY_OWNER") or "YOUR_USERNAME",
        help="GitHub username (defaults to GITHUB_REPOSITORY_OWNER in Actions)",
    )
    ap.add_argument("-o", "--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    print(f"-> fetching contributions for {args.username}")
    html = fetch_html(args.username)
    days = parse_days(html)
    stats = compute_stats(days)

    payload = {
        "username": args.username,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source": URL.format(username=args.username),
        "range": {"from": days[0]["date"], "to": days[-1]["date"]},
        "stats": stats,
        "days": days,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print(f"-> wrote {args.out}")
    print(f"   {stats['total']:,} contributions over {stats['days_tracked']} days "
          f"({days[0]['date']} -> {days[-1]['date']})")
    print(f"   current streak {stats['current_streak']['length']}d, "
          f"longest {stats['longest_streak']['length']}d, "
          f"best day {stats['best_day']['count']} on {stats['best_day']['date']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
