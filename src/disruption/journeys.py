"""Every train in the am/pm/late windows, with its calls at the stations we care about."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import httpx

from . import config, rtt
from .models import Journey

ARRIVAL_GRACE = timedelta(minutes=60)

WINDOWS = (
    ("am", config.BEXLEY, config.LONDON_BRIDGE, config.AM_START, config.AM_END),
    ("pm", config.LONDON_BRIDGE, config.BEXLEY, config.PM_START, config.PM_END),
    ("late", config.LONDON_BRIDGE, config.BEXLEY, config.LATE_START, config.LATE_END),
)


def collect(d: date, previous: list[Journey], *, client: httpx.Client | None,
            now: datetime, limit: int) -> tuple[list[Journey], int]:
    known = {j.uid: j for j in previous}
    result: list[Journey] = []
    used = 0
    for window, origin, dest, start, end in WINDOWS:
        for uid, run_date, booked in rtt.search_services(origin, dest, d, start, end,
                                                         client=client):
            old = known.get(uid)
            if (old and old.final) or now < booked + ARRIVAL_GRACE or used >= limit:
                if old:
                    result.append(old)
                continue
            before = rtt.requests_made
            try:
                cancelled, reason, calls = rtt.fetch_service(uid, origin, client=client)
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                print(f"WARN {d} [journeys]: {uid}: {exc}", flush=True)
                if old:
                    result.append(old)
                continue
            finally:
                used += max(1, rtt.requests_made - before)
            result.append(Journey(uid, run_date, window, cancelled, reason, calls))
    seen = {j.uid for j in result}
    result += [j for j in previous if j.uid not in seen]
    return result, used
