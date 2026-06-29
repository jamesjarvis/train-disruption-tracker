"""Scrape the National Rail OJP 'times and fares' pages.

The legacy endpoint at ojp.nationalrail.co.uk is server-rendered HTML (no JavaScript),
future-dated, and free with no signup. URL shape:

    https://ojp.nationalrail.co.uk/service/timesandfares/{FROM}/{TO}/{ddmmyy}/{HHMM}/dep

Each request returns roughly six train options starting at the requested time, so to
cover a multi-hour window we step the start time in 30-minute hops and dedupe by
departure time.

All National-Rail-specific parsing lives in this module. If the site changes, this is
the only file that should need to change.
"""

from __future__ import annotations

import time as _time
from datetime import date, datetime, time, timedelta

import httpx
from selectolax.parser import HTMLParser

from . import config


class ScrapeError(RuntimeError):
    """Raised when the planner returns something we can't parse into trains."""


def _format_date(d: date) -> str:
    """National Rail expects ddmmyy, e.g. 2026-06-29 -> '290626'."""
    return d.strftime("%d%m%y")


def _url(origin: str, dest: str, d: date, t: time) -> str:
    return f"{config.OJP_BASE}/{origin}/{dest}/{_format_date(d)}/{t:%H%M}/dep"


def _step_times(start: time, end: time, step_minutes: int) -> list[time]:
    """Start times to query so the whole [start, end) window is covered."""
    out: list[time] = []
    cur = datetime.combine(date.min, start)
    stop = datetime.combine(date.min, end)
    while cur < stop:
        out.append(cur.time())
        cur += timedelta(minutes=step_minutes)
    return out


def _parse_row(row, d: date) -> TrainOptionParse | None:
    dep_node = row.css_first(".dep")
    if dep_node is None:
        return None
    dep_text = dep_node.text(strip=True)  # e.g. "07:28"
    try:
        hh, mm = (int(x) for x in dep_text.split(":"))
    except ValueError:
        return None
    departure = datetime.combine(d, time(hh, mm))

    row_html = row.html or ""
    bus = "Platform BUS" in row_html or row.css_first(".sprite-bus") is not None
    flagged = row.css_first(".journey-status-disrupted") is not None
    disrupted = bus or flagged

    reason: str | None = None
    if disrupted:
        desc = row.css_first(".disruptiondesc")
        if desc is not None and desc.text(strip=True):
            reason = desc.text(strip=True)
        elif bus:
            reason = "Rail replacement bus"
        else:
            reason = "Disrupted"
    return (departure, disrupted, reason)


# A parsed row is a (departure, disrupted, reason) tuple before window filtering.
TrainOptionParse = tuple


def _parse_page(html: str, d: date) -> list[TrainOptionParse]:
    tree = HTMLParser(html)
    rows = tree.css("tr.mtx")
    return [p for p in (_parse_row(r, d) for r in rows) if p is not None]


def _get(client: httpx.Client, url: str) -> httpx.Response:
    """GET with retries + linear backoff, to ride out transient network blips."""
    last_exc: Exception | None = None
    for attempt in range(1, config.REQUEST_RETRIES + 1):
        try:
            resp = client.get(url)
            resp.raise_for_status()
            return resp
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt < config.REQUEST_RETRIES:
                _time.sleep(config.RETRY_BACKOFF_SECONDS * attempt)
    assert last_exc is not None
    raise last_exc


def fetch_trains(
    origin: str,
    dest: str,
    d: date,
    start: time,
    end: time,
    *,
    client: httpx.Client | None = None,
):
    """Return de-duplicated train options departing in [start, end) on date ``d``.

    Imported lazily to avoid a circular import with models.
    """
    from .models import TrainOption

    owns_client = client is None
    if client is None:
        client = httpx.Client(
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
            parsed = _parse_page(resp.text, d)
            if parsed:
                pages_ok += 1
            for departure, disrupted, reason in parsed:
                if not (start <= departure.time() < end):
                    continue
                # Keep the disrupted view if any stepped request flags this train.
                existing = seen.get(departure)
                if existing is None or (disrupted and not existing.disrupted):
                    seen[departure] = TrainOption(departure, disrupted, reason)
    finally:
        if owns_client:
            client.close()

    if pages_ok == 0:
        raise ScrapeError(
            f"No parseable trains for {origin}->{dest} on {d} "
            f"[{start:%H:%M}-{end:%H:%M}]; planner layout may have changed."
        )
    return sorted(seen.values(), key=lambda o: o.departure)
