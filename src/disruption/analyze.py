"""Turn scraped/fetched train options into a per-day AM/PM disruption summary.

Two sources feed the same shape (``TrainOption``):
  - the National Rail journey planner (``scraper``) — planned/advance disruption;
  - Realtime Trains (``rtt``) — actual cancellations + departure delays for recent dates.
"""

from __future__ import annotations

from datetime import date, time

import httpx

from . import config, rtt
from .models import DayReport, TrainOption
from .scraper import fetch_trains


def summarise(
    d: date,
    am_trains: list[TrainOption],
    pm_trains: list[TrainOption],
) -> DayReport:
    """Pure summary step (no network) — the unit under test."""
    am_disrupted = [t for t in am_trains if t.disrupted]
    pm_disrupted = [t for t in pm_trains if t.disrupted]
    return DayReport(
        date=d,
        am_total=len(am_trains),
        am_disrupted=len(am_disrupted),
        pm_total=len(pm_trains),
        pm_disrupted=len(pm_disrupted),
        am_disrupted_trains=am_disrupted,
        pm_disrupted_trains=pm_disrupted,
    )


def merge_trains(
    a: list[TrainOption], b: list[TrainOption]
) -> list[TrainOption]:
    """Union two windows by departure time, keeping the most-disrupted view.

    Used when a single day has both actual (Realtime Trains) and planned (planner)
    coverage — e.g. tomorrow, where RTT sees pre-cancellations and the planner sees
    engineering works.
    """
    seen: dict = {}
    for t in [*a, *b]:
        existing = seen.get(t.departure)
        if existing is None or (t.disrupted and not existing.disrupted):
            seen[t.departure] = t
    return sorted(seen.values(), key=lambda o: o.departure)


def _planner_windows(d: date, client: httpx.Client | None):
    am = fetch_trains(
        config.BEXLEY, config.LONDON_BRIDGE, d, config.AM_START, config.AM_END,
        client=client,
    )
    pm = fetch_trains(
        config.LONDON_BRIDGE, config.BEXLEY, d, config.PM_START, config.PM_END,
        client=client,
    )
    return am, pm


def _actual_windows(d: date, client: httpx.Client | None):
    am = rtt.fetch_trains(
        config.BEXLEY, config.LONDON_BRIDGE, d, config.AM_START, config.AM_END,
        client=client,
    )
    pm = rtt.fetch_trains(
        config.LONDON_BRIDGE, config.BEXLEY, d, config.PM_START, config.PM_END,
        client=client,
    )
    return am, pm


def build_day_report(d: date, *, client: httpx.Client | None = None) -> DayReport:
    """Scrape both peak windows for ``d`` from the planner and summarise.

    AM: Bexley -> London Bridge, 07:00-10:00 (London-bound).
    PM: London Bridge -> Bexley, 17:00-22:00 (Bexley-bound).
    Raises ScrapeError (via fetch_trains) if a window can't be parsed.
    """
    am, pm = _planner_windows(d, client)
    return summarise(d, am, pm)


def build_actual_report(d: date, *, client: httpx.Client | None = None) -> DayReport:
    """Fetch both peak windows for ``d`` from Realtime Trains and summarise.

    Raises RttError (via rtt.fetch_trains) if a window can't be fetched.
    """
    am, pm = _actual_windows(d, client)
    return summarise(d, am, pm)


def build_merged_report(
    d: date,
    *,
    planner_client: httpx.Client | None = None,
    rtt_client: httpx.Client | None = None,
) -> DayReport:
    """Combine actual (Realtime Trains) and planned (planner) coverage for ``d``.

    Used for tomorrow, where both sources contribute. Either source raising propagates
    to the caller, which decides whether the other still provides a usable report.
    """
    a_am, a_pm = _actual_windows(d, rtt_client)
    p_am, p_pm = _planner_windows(d, planner_client)
    return summarise(d, merge_trains(a_am, p_am), merge_trains(a_pm, p_pm))
