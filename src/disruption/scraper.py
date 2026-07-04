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


# Strong, human-readable signals that a page describes a rail-replacement bus.
# National Rail moved the per-row bus markers out of the summary row over time, but
# the page as a whole always carries at least one of these when a bus is involved.
# Used both to flag a journey and as a page-level backstop (see ``_parse_page``).
_BUS_SIGNALS = (
    "sprite-bus",
    "replacement bus",
    "made by bus",
    "rail replacement",
)


def _page_signals_bus(html: str) -> bool:
    """True if the raw page clearly describes a replacement bus anywhere."""
    low = html.lower()
    return any(sig in low for sig in _BUS_SIGNALS)


def _journey_groups(tree):
    """Yield one node list per journey: the ``tr.mtx`` summary row plus the detail
    rows that follow it (``tr.changes``, ``tr.status``, …) up to the next ``tr.mtx``.

    National Rail renders the bus / disruption markers in those trailing detail rows,
    not in the summary row, so a journey must be judged from the whole group.
    """
    for mtx in tree.css("tr.mtx"):
        group = [mtx]
        sib = mtx.next
        while sib is not None:
            if sib.tag == "tr":
                if "mtx" in (sib.attributes.get("class", "") or "").split():
                    break  # next journey starts here
                group.append(sib)  # a detail row (changes / status / …)
            sib = sib.next  # skip whitespace text nodes and anything non-tr
        yield group


def _parse_row(group, d: date) -> TrainOptionParse | None:
    """Parse one journey group into a (departure, disrupted, reason) tuple.

    ``group`` is the node list from :func:`_journey_groups`: the summary row first,
    then its detail rows. Disruption is judged across the whole group.
    """
    mtx = group[0]
    dep_node = mtx.css_first(".dep")
    if dep_node is None:
        return None
    dep_text = dep_node.text(strip=True)  # e.g. "07:28"
    try:
        hh, mm = (int(x) for x in dep_text.split(":"))
    except ValueError:
        return None
    departure = datetime.combine(d, time(hh, mm))

    group_html = "".join(n.html or "" for n in group)
    bus = _page_signals_bus(group_html) or "Platform BUS" in group_html
    flagged = any(n.css_first(".journey-status-disrupted") is not None for n in group)
    disrupted = bus or flagged

    reason: str | None = None
    if bus:
        # A clean fixed label reads better in the calendar than the planner's verbose
        # (and un-spaced) "Bus serviceAll or part of this journey…" run-on text.
        reason = "Rail replacement bus"
    elif disrupted:
        desc = next(
            (n.css_first(".disruptiondesc") for n in group
             if n.css_first(".disruptiondesc") is not None),
            None,
        )
        reason = desc.text(strip=True) if desc is not None and desc.text(strip=True) else "Disrupted"
    return (departure, disrupted, reason)


# A parsed row is a (departure, disrupted, reason) tuple before window filtering.
TrainOptionParse = tuple


def _parse_page(html: str, d: date, *, _row_parser=_parse_row) -> list[TrainOptionParse]:
    tree = HTMLParser(html)
    groups = list(_journey_groups(tree))
    parsed = [p for p in (_row_parser(g, d) for g in groups) if p is not None]
    # Backstop: if the page has journeys AND clearly describes a replacement bus, yet
    # none were flagged disrupted, our per-row detection has drifted from the markup.
    # Refuse to report a (false) clean day — raise so the caller keeps the last stored
    # value and logs it, instead of silently publishing "clean". This is the exact
    # failure that shipped: a bus day scored 0% disrupted.
    if (
        groups
        and _page_signals_bus(html)
        and not any(dis for _dep, dis, _r in parsed)
    ):
        raise ScrapeError(
            f"Page for {d} describes a replacement bus but no journey parsed as "
            "disrupted; planner markup may have changed."
        )
    return parsed


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
