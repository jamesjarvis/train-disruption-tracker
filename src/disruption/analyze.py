"""Turn scraped train options into a per-day AM/PM disruption summary."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

import httpx

from . import config
from .models import DayReport, TrainOption
from .scraper import fetch_trains


def _dedupe_reasons(*groups: Iterable[TrainOption]) -> list[str]:
    notes: list[str] = []
    for group in groups:
        for opt in group:
            if opt.disrupted and opt.reason and opt.reason not in notes:
                notes.append(opt.reason)
    return notes


def summarise(
    d: date,
    am_trains: list[TrainOption],
    pm_trains: list[TrainOption],
) -> DayReport:
    """Pure summary step (no network) — the unit under test."""
    return DayReport(
        date=d,
        am_total=len(am_trains),
        am_disrupted=sum(1 for t in am_trains if t.disrupted),
        pm_total=len(pm_trains),
        pm_disrupted=sum(1 for t in pm_trains if t.disrupted),
        notes=_dedupe_reasons(am_trains, pm_trains),
    )


def build_day_report(d: date, *, client: httpx.Client | None = None) -> DayReport:
    """Scrape both peak windows for ``d`` and summarise.

    AM: Bexley -> London Bridge, 07:00-10:00 (London-bound).
    PM: London Bridge -> Bexley, 17:00-22:00 (Bexley-bound).
    Raises ScrapeError (via fetch_trains) if a window can't be parsed, so the caller
    can skip the day rather than publish a falsely-clean result.
    """
    am = fetch_trains(
        config.BEXLEY, config.LONDON_BRIDGE, d, config.AM_START, config.AM_END,
        client=client,
    )
    pm = fetch_trains(
        config.LONDON_BRIDGE, config.BEXLEY, d, config.PM_START, config.PM_END,
        client=client,
    )
    return summarise(d, am, pm)
