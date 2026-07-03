"""Entry point: gather actual + planned disruption, persist history, write the feed.

Run every couple of hours via launchd. The feed (docs/disruptions.ics) is published to
GitHub Pages so family can subscribe by URL — no accounts, no OAuth.

Two sources feed one rolling history:
  - Realtime Trains (actual) for yesterday/today/tomorrow — real cancellations + delays;
  - the National Rail planner (advance) for tomorrow .. +horizon — engineering works.
History is kept locally so the feed can show ~2 months of past days alongside the look-
ahead; the published .ics is the durable public record.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx

from . import config, history, ics_writer
from .analyze import build_actual_report, build_day_report, build_merged_report
from .models import DayReport
from .rtt import RttError
from .scraper import ScrapeError


def _log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def _record(store: dict[date, DayReport], rep: DayReport, label: str) -> None:
    history.upsert(store, rep)
    flag = "DISRUPTED" if rep.affected else "clean"
    _log(
        f"{rep.date} [{label}] {flag}: AM {rep.am_disrupted}/{rep.am_total} "
        f"({rep.am_pct}%) PM {rep.pm_disrupted}/{rep.pm_total} ({rep.pm_pct}%)"
    )


def collect_reports(horizon_days: int) -> tuple[list[DayReport], date]:
    """Refresh actual + planned days into the history store, prune, and return it.

    Returns (reports_within_window, today). Each day is fetched independently; a source
    failing for one day skips that refresh (the stored value, if any, is kept) rather
    than failing the run.
    """
    today = date.today()
    store = history.load()

    planner_client = httpx.Client(
        headers={"User-Agent": config.USER_AGENT},
        timeout=config.REQUEST_TIMEOUT_SECONDS,
        follow_redirects=True,
    )
    rtt_client: httpx.Client | None = None
    if config.RTT_USERNAME and config.RTT_PASSWORD:
        rtt_client = httpx.Client(
            auth=(config.RTT_USERNAME, config.RTT_PASSWORD),
            headers={"User-Agent": config.USER_AGENT},
            timeout=config.REQUEST_TIMEOUT_SECONDS,
            follow_redirects=True,
        )
    else:
        _log("WARN RTT_USERNAME/RTT_PASSWORD not set — actual data skipped.")

    try:
        # Actual (Realtime Trains): yesterday only. (Today is merged below; the OJP
        # planner plans forward and won't return a past day reliably.)
        if rtt_client is not None:
            for offset in range(-config.LIVE_BACK_DAYS, 0):
                d = today + timedelta(days=offset)
                try:
                    _record(store, build_actual_report(d, client=rtt_client), "actual")
                except (RttError, httpx.HTTPError) as exc:
                    _log(f"WARN {d} [actual]: {exc} — keeping stored value")

        # Today + tomorrow: merge actual (Realtime Trains) + planner, falling back to
        # whichever source succeeds. Merging today means a dark RTT feed (which silently
        # serves the plan as "all on time") can't hide disruption the planner still sees.
        for offset in range(0, config.LIVE_FWD_DAYS + 1):
            d = today + timedelta(days=offset)
            try:
                if rtt_client is not None:
                    _record(
                        store,
                        build_merged_report(
                            d, planner_client=planner_client, rtt_client=rtt_client
                        ),
                        "merged",
                    )
                else:
                    _record(store, build_day_report(d, client=planner_client), "planner")
            except (RttError, ScrapeError, httpx.HTTPError) as exc:
                _log(f"WARN {d} [merged]: {exc} — trying planner only")
                try:
                    _record(store, build_day_report(d, client=planner_client), "planner")
                except (ScrapeError, httpx.HTTPError) as exc2:
                    _log(f"WARN {d} [planner]: {exc2} — keeping stored value")

        # Advance (planner): the day after the live window .. horizon.
        for offset in range(config.LIVE_FWD_DAYS + 1, horizon_days + 1):
            d = today + timedelta(days=offset)
            try:
                _record(store, build_day_report(d, client=planner_client), "planner")
            except (ScrapeError, httpx.HTTPError) as exc:
                _log(f"WARN {d} [planner]: {exc} — keeping stored value")
    finally:
        planner_client.close()
        if rtt_client is not None:
            rtt_client.close()

    history.prune(store, today, config.HISTORY_KEEP_DAYS)
    history.save(store)

    window_start = today - timedelta(days=config.HISTORY_KEEP_DAYS)
    window_end = today + timedelta(days=horizon_days)
    reports = [
        store[d] for d in sorted(store) if window_start <= d <= window_end
    ]
    return reports, today


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

    _log(f"Scanning {args.horizon + 1} days ahead + up to "
         f"{config.HISTORY_KEEP_DAYS} days of history")
    reports, today = collect_reports(args.horizon)

    affected = [r for r in reports if r.affected]
    _log(f"Feed covers {len(reports)} days; {len(affected)} affected.")

    if not reports:
        _log("No days available; leaving existing feed untouched.")
        return 1

    if args.dry_run:
        sys.stdout.write(ics_writer.build_calendar(reports, today=today))
        return 0

    ics_writer.write_calendar(reports, args.output, today=today)
    _log(f"Wrote feed with {len(affected)} event(s) to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
