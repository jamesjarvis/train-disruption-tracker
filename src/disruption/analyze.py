"""Turn scraped/fetched train options into a per-day AM/PM disruption summary.

Two sources feed the same shape (``TrainOption``):
  - the National Rail journey planner (``scraper``) — planned/advance disruption;
  - Realtime Trains (``rtt``) — actual cancellations + departure delays for recent dates.
"""

from __future__ import annotations

from datetime import date, datetime, time

import httpx

from . import config, rtt
from .models import Call, DayReport, Journey, TrainOption
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


def _ends(journey: Journey) -> tuple[Call | None, Call | None]:
    if journey.window == "am":
        return journey.calls.get(config.BEXLEY), journey.calls.get(config.LONDON_BRIDGE)
    return journey.calls.get(config.LONDON_BRIDGE), journey.calls.get(config.BEXLEY)


def journey_outcome(journey: Journey) -> tuple[datetime, TrainOption | None] | None:
    """What happened to one train, judged where it matters: at the destination.

    Returns ``None`` while the outcome is unknown, ``(departure, None)`` for a train
    that ran fine, and ``(departure, option)`` for a disrupted one.
    """
    origin, dest = _ends(journey)
    if origin is None or origin.booked_dep is None:
        return None
    departure = origin.booked_dep
    if journey.cancelled or origin.cancelled:
        return departure, TrainOption(departure, True, journey.reason or "Cancelled", cancelled=True)
    if dest is None:
        return None
    if dest.cancelled:
        reason = "Terminated early" + (f": {journey.reason}" if journey.reason else "")
        return departure, TrainOption(departure, True, reason, cancelled=True)
    if journey.reason == "Rail replacement bus":
        return departure, TrainOption(departure, True, journey.reason)
    if dest.booked_arr is None or dest.actual_arr is None:
        return None
    delay = int((dest.actual_arr - dest.booked_arr).total_seconds() // 60)
    if delay > config.DELAY_THRESHOLD_MINUTES:
        return departure, TrainOption(departure, True, f"Arrived {delay} min late", delay_minutes=delay)
    return departure, None


def _severity(option: TrainOption) -> tuple[bool, int]:
    return option.cancelled, option.delay_minutes or 0


def apply_journeys(report: DayReport) -> None:
    """Replace the departure-based disrupted trains with what journeys show at the destination.

    Trains whose outcome is not known yet keep their departure-based entry.
    """
    outcomes = []
    for journey in report.trains:
        if journey.window not in ("am", "pm"):
            continue
        outcome = journey_outcome(journey)
        if outcome is not None:
            outcomes.append((journey.window, *outcome))
    judged = {departure for _, departure, _ in outcomes}

    def rebuild(window: str, existing: list[TrainOption]) -> list[TrainOption]:
        worst: dict[datetime, TrainOption] = {}
        for w, departure, option in outcomes:
            if w == window and option is not None and (
                departure not in worst or _severity(option) > _severity(worst[departure])
            ):
                worst[departure] = option
        kept = [t for t in existing if t.departure not in judged]
        return sorted([*kept, *worst.values()], key=lambda t: t.departure)

    report.am_disrupted_trains = rebuild("am", report.am_disrupted_trains)
    report.pm_disrupted_trains = rebuild("pm", report.pm_disrupted_trains)
    report.am_disrupted = len(report.am_disrupted_trains)
    report.pm_disrupted = len(report.pm_disrupted_trains)
