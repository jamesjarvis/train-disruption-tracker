"""Fetch *actual* running data from the Realtime Trains API (api.rtt.io).

The journey planner (``scraper.py``) is future-dated and schedule-only, so it can't see
a train that actually got cancelled on the day or ran late. Realtime Trains exposes a
free JSON departures board with booked vs realtime times and cancellation status, which
gives us that signal for recent dates.

This module mirrors ``scraper.fetch_trains`` (same signature, returns ``TrainOption``s)
so the rest of the pipeline treats planner and actual data identically. All
Realtime-Trains-specific parsing lives here.

A train is flagged ``disrupted`` if it is cancelled, or departs origin more than
``config.DELAY_THRESHOLD_MINUTES`` late.
"""

from __future__ import annotations

import time as _time
from datetime import date, datetime, time, timedelta

import httpx

from . import config
from .scraper import _get, _step_times  # shared retry + window-stepping


class RttError(RuntimeError):
    """Raised when Realtime Trains returns something we can't parse into trains."""


def _url(origin: str, dest: str, d: date, t: time) -> str:
    return f"{config.RTT_BASE}/search/{origin}/to/{dest}/{d:%Y/%m/%d}/{t:%H%M}"


def _hhmm_to_minutes(hhmm: str) -> int | None:
    """'0735' -> 455 minutes-since-midnight; None if unparseable."""
    if not hhmm or len(hhmm) != 4 or not hhmm.isdigit():
        return None
    return int(hhmm[:2]) * 60 + int(hhmm[2:])


def _parse_service(service: dict, d: date):
    """Return a (departure, disrupted, reason, cancelled, delay) tuple, or None."""
    loc = service.get("locationDetail") or {}
    booked = _hhmm_to_minutes(loc.get("gbttBookedDeparture", ""))
    if booked is None:
        return None  # no public booked departure here (e.g. arrival-only call)
    departure = datetime.combine(d, time(booked // 60, booked % 60))

    display = (loc.get("displayAs") or "").upper()
    cancelled = "CANCEL" in display or bool(loc.get("cancelReasonCode"))

    realtime = _hhmm_to_minutes(loc.get("realtimeDeparture", ""))
    delay = realtime - booked if realtime is not None else None

    disrupted = cancelled or (delay is not None and delay > config.DELAY_THRESHOLD_MINUTES)

    reason: str | None = None
    if cancelled:
        reason = loc.get("cancelReasonShortText") or "Cancelled"
    elif disrupted:
        reason = f"Delayed {delay} min"
    return (departure, disrupted, reason, cancelled, delay)


def _parse_page(payload: dict, d: date):
    services = payload.get("services")
    if not services:
        return []
    return [p for p in (_parse_service(s, d) for s in services) if p is not None]


def fetch_trains(
    origin: str,
    dest: str,
    d: date,
    start: time,
    end: time,
    *,
    client: httpx.Client | None = None,
):
    """Return de-duplicated *actual* train options departing in [start, end) on ``d``.

    Mirrors ``scraper.fetch_trains``. Raises ``RttError`` if no window parsed, so the
    caller can skip rather than publish a falsely-clean result.
    """
    from .models import TrainOption

    if not (config.RTT_USERNAME and config.RTT_PASSWORD):
        raise RttError("RTT_USERNAME/RTT_PASSWORD not set; cannot fetch actual data.")

    owns_client = client is None
    if client is None:
        client = httpx.Client(
            auth=(config.RTT_USERNAME, config.RTT_PASSWORD),
            headers={"User-Agent": config.USER_AGENT},
            timeout=config.REQUEST_TIMEOUT_SECONDS,
            follow_redirects=True,
        )

    seen: dict[datetime, TrainOption] = {}
    pages_ok = 0
    try:
        for i, t in enumerate(_step_times(start, end, config.WINDOW_STEP_MINUTES)):
            if i:
                _time.sleep(config.REQUEST_DELAY_SECONDS)
            resp = _get(client, _url(origin, dest, d, t))
            parsed = _parse_page(resp.json(), d)
            if parsed:
                pages_ok += 1
            for departure, disrupted, reason, cancelled, delay in parsed:
                if not (start <= departure.time() < end):
                    continue
                existing = seen.get(departure)
                if existing is None or (disrupted and not existing.disrupted):
                    seen[departure] = TrainOption(
                        departure, disrupted, reason, cancelled, delay
                    )
    finally:
        if owns_client:
            client.close()

    if pages_ok == 0:
        raise RttError(
            f"No Realtime Trains services for {origin}->{dest} on {d} "
            f"[{start:%H:%M}-{end:%H:%M}]."
        )
    return sorted(seen.values(), key=lambda o: o.departure)
