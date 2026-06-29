"""Entry point: scrape the horizon, then write the iCalendar feed.

Run daily via launchd. The feed (docs/disruptions.ics) is meant to be published to
GitHub Pages so family can subscribe by URL — no accounts, no OAuth.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

from . import config, ics_writer
from .analyze import build_day_report
from .models import DayReport
from .scraper import ScrapeError


def _log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def collect_reports(horizon_days: int) -> list[DayReport]:
    """Scrape today..today+horizon_days. Skip (don't fail) days that won't parse."""
    today = date.today()
    reports: list[DayReport] = []
    with httpx.Client(
        headers={"User-Agent": config.USER_AGENT},
        timeout=config.REQUEST_TIMEOUT_SECONDS,
        follow_redirects=True,
    ) as client:
        for offset in range(horizon_days + 1):
            d = today + timedelta(days=offset)
            try:
                rep = build_day_report(d, client=client)
            except ScrapeError as exc:
                _log(f"WARN {d}: {exc} — skipping")
                continue
            except httpx.HTTPError as exc:
                _log(f"WARN {d}: HTTP error {exc!r} — skipping")
                continue
            flag = "DISRUPTED" if rep.affected else "clean"
            _log(
                f"{d} {flag}: AM {rep.am_disrupted}/{rep.am_total} ({rep.am_pct}%) "
                f"PM {rep.pm_disrupted}/{rep.pm_total} ({rep.pm_pct}%)"
            )
            reports.append(rep)
    return reports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=config.OUTPUT_ICS,
        help=f"Where to write the .ics feed (default {config.OUTPUT_ICS}).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print the .ics to stdout instead of writing the file.",
    )
    parser.add_argument(
        "--horizon", type=int, default=config.HORIZON_DAYS,
        help=f"Days ahead to scan (default {config.HORIZON_DAYS}).",
    )
    args = parser.parse_args(argv)

    _log(f"Scanning {args.horizon + 1} days (horizon={args.horizon})")
    reports = collect_reports(args.horizon)

    affected = [r for r in reports if r.affected]
    _log(f"Scraped {len(reports)} days; {len(affected)} affected.")

    if not reports:
        _log("No days scraped successfully; leaving existing feed untouched.")
        return 1

    if args.dry_run:
        sys.stdout.write(ics_writer.build_calendar(reports))
        return 0

    ics_writer.write_calendar(reports, args.output)
    _log(f"Wrote feed with {len(affected)} event(s) to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
