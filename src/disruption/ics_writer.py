"""Render DayReports as an RFC 5545 iCalendar (.ics) feed.

The whole feed is regenerated from scratch each run, so there is no state to
reconcile: affected days get an all-day VEVENT, everything else is simply absent.
Each event uses a stable UID (``<iso-date>@<domain>``) so a calendar client that has
already subscribed updates the event in place instead of duplicating it.

Handwritten rather than pulling in a dependency — the format is small and we only emit
all-day events. We still honour the fiddly bits that real clients care about: CRLF line
endings, text escaping, and 75-octet line folding.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from . import config
from .models import DayReport


_ASCII_MAP = {"–": "-", "—": "-", "‘": "'", "’": "'",
              "“": '"', "”": '"', "→": "->", "…": "..."}


def _ascii(text: str) -> str:
    """Reduce text to plain ASCII.

    GitHub Pages serves the feed as ``text/calendar`` with no charset, so any
    non-ASCII byte (e.g. an emoji or en-dash) gets mis-decoded by some clients
    (Google Calendar shows mojibake). Keeping the feed ASCII-only side-steps that
    entirely. Common punctuation is transliterated; anything else is dropped.
    """
    for src, dst in _ASCII_MAP.items():
        text = text.replace(src, dst)
    return text.encode("ascii", "ignore").decode("ascii")


def _escape(text: str) -> str:
    """Escape a TEXT value per RFC 5545 §3.3.11 (ASCII-folded first)."""
    text = _ascii(text)
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _fold(line: str) -> str:
    """Fold a content line to <=75 octets, continuation lines start with a space.

    Folds on character boundaries (never mid multi-byte UTF-8 char), so values
    containing en-dashes, emoji, etc. survive folding intact.
    """
    if len(line.encode("utf-8")) <= 75:
        return line
    segments: list[str] = []
    cur = ""
    cur_bytes = 0
    for ch in line:
        ch_bytes = len(ch.encode("utf-8"))
        # First segment may use 75 octets; continuation segments reserve 1 for the
        # leading space, so 74 octets of content.
        limit = 75 if not segments else 74
        if cur_bytes + ch_bytes > limit:
            segments.append(cur)
            cur, cur_bytes = ch, ch_bytes
        else:
            cur += ch
            cur_bytes += ch_bytes
    segments.append(cur)
    return segments[0] + "".join(f"\r\n {seg}" for seg in segments[1:])


def _group_by_reason(trains) -> list[tuple[str, list[str]]]:
    """Group disrupted trains by reason, preserving first-seen order.

    Returns ``[(reason, ["07:28", "07:58", ...]), ...]``; an unset reason falls
    back to "Disrupted".
    """
    groups: dict[str, list[str]] = {}
    for t in trains:
        reason = t.reason or "Disrupted"
        groups.setdefault(reason, []).append(f"{t.departure:%H:%M}")
    return list(groups.items())


def _window_lines(label: str, disrupted: int, total: int, pct: int, trains) -> list[str]:
    lines = [f"{label} - {disrupted}/{total} disrupted ({pct}%)"]
    for reason, times in _group_by_reason(trains):
        lines.append(f"  {reason}: {', '.join(times)}")
    return lines


def _event_lines(report: DayReport, dtstamp: str, today: date) -> list[str]:
    d = report.date
    summary = f"Bexley trains AM {report.am_pct}% / PM {report.pm_pct}% disrupted"
    desc_parts = [
        *_window_lines("London-bound AM", report.am_disrupted, report.am_total,
                       report.am_pct, report.am_disrupted_trains),
        *_window_lines("Bexley-bound PM", report.pm_disrupted, report.pm_total,
                       report.pm_pct, report.pm_disrupted_trains),
        "",
        "Source: Realtime Trains (actual) + National Rail planner (advance). "
        "Auto-generated.",
    ]
    description = "\n".join(desc_parts)

    lines = [
        "BEGIN:VEVENT",
        f"UID:{d.isoformat()}@{config.ICS_UID_DOMAIN}",
        f"DTSTAMP:{dtstamp}",
        f"DTSTART;VALUE=DATE:{d:%Y%m%d}",
        f"DTEND;VALUE=DATE:{(d + timedelta(days=1)):%Y%m%d}",  # exclusive => one day
        f"SUMMARY:{_escape(summary)}",
        f"DESCRIPTION:{_escape(description)}",
        "TRANSP:TRANSPARENT",
    ]
    # Alert the evening before (20:00): all-day start is midnight, so -PT4H. Pointless
    # for past days, so only future/today events carry it.
    # Note: Google Calendar ignores VALARMs in subscribed feeds; Apple honours them.
    if d >= today:
        lines += [
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            "TRIGGER;RELATED=START:-PT4H",
            f"DESCRIPTION:{_escape(summary)}",
            "END:VALARM",
        ]
    lines.append("END:VEVENT")
    return lines


def _stale_event_lines(d: date, dtstamp: str, today: date) -> list[str]:
    """A warning event for a live-window day the run could NOT refresh.

    Without this, a total source outage would silently serve the last stored value
    (often "clean") and the subscriber would never know the data was untrustworthy.
    """
    summary = "[!] Bexley trains: data unavailable - check live times"
    description = (
        "The automated check could not refresh this day's train data (a data source "
        "was unavailable). Treat the disruption status as UNVERIFIED and check National "
        "Rail or your app for live times. This clears automatically on the next good run."
    )
    lines = [
        "BEGIN:VEVENT",
        f"UID:stale-{d.isoformat()}@{config.ICS_UID_DOMAIN}",
        f"DTSTAMP:{dtstamp}",
        f"DTSTART;VALUE=DATE:{d:%Y%m%d}",
        f"DTEND;VALUE=DATE:{(d + timedelta(days=1)):%Y%m%d}",
        f"SUMMARY:{_escape(summary)}",
        f"DESCRIPTION:{_escape(description)}",
        "TRANSP:TRANSPARENT",
    ]
    if d >= today:
        lines += [
            "BEGIN:VALARM",
            "ACTION:DISPLAY",
            "TRIGGER;RELATED=START:-PT4H",
            f"DESCRIPTION:{_escape(summary)}",
            "END:VALARM",
        ]
    lines.append("END:VEVENT")
    return lines


def build_calendar(
    reports: list[DayReport],
    *,
    now: datetime | None = None,
    today: date | None = None,
    stale_dates: list[date] | None = None,
) -> str:
    """Return the full .ics document for the affected days in ``reports``.

    ``stale_dates`` are live-window days this run could not refresh; each gets a
    warning event so a silent data outage can't masquerade as a clean day.
    """
    now = now or datetime.now(timezone.utc)
    today = today or now.date()
    dtstamp = now.strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{config.ICS_PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"NAME:{_escape(config.CALENDAR_NAME)}",  # RFC 7986; some clients prefer this
        f"X-WR-CALNAME:{config.CALENDAR_NAME}",
        f"X-WR-TIMEZONE:{config.CALENDAR_TIME_ZONE}",
        f"REFRESH-INTERVAL;VALUE=DURATION:{config.ICS_REFRESH}",
        f"X-PUBLISHED-TTL:{config.ICS_REFRESH}",
    ]
    for report in reports:
        if report.affected:
            lines.extend(_event_lines(report, dtstamp, today))
    for d in stale_dates or []:
        lines.extend(_stale_event_lines(d, dtstamp, today))
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(ln) for ln in lines) + "\r\n"


def write_calendar(
    reports: list[DayReport],
    path=None,
    *,
    now: datetime | None = None,
    today: date | None = None,
    stale_dates: list[date] | None = None,
) -> str:
    """Write the feed to ``path`` (default config.OUTPUT_ICS). Returns the text."""
    path = path or config.OUTPUT_ICS
    text = build_calendar(reports, now=now, today=today, stale_dates=stale_dates)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text
