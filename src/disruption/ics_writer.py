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


def _escape(text: str) -> str:
    """Escape a TEXT value per RFC 5545 §3.3.11."""
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _fold(line: str) -> str:
    """Fold a content line to <=75 octets, continuation lines start with a space."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    chunks: list[bytes] = []
    # First line up to 75 octets; subsequent up to 74 (leading space counts).
    chunks.append(raw[:75])
    rest = raw[75:]
    while rest:
        chunks.append(b" " + rest[:74])
        rest = rest[74:]
    return b"\r\n".join(chunks).decode("utf-8")


def _event_lines(report: DayReport, dtstamp: str) -> list[str]:
    d = report.date
    summary = (
        f"\U0001f686 Bexley AM {report.am_pct}% / PM {report.pm_pct}% disrupted"
    )
    desc_parts = [
        f"London-bound peak 07:00-10:00: {report.am_disrupted}/{report.am_total} "
        f"trains disrupted ({report.am_pct}%).",
        f"Bexley-bound peak 17:00-22:00: {report.pm_disrupted}/{report.pm_total} "
        f"trains disrupted ({report.pm_pct}%).",
    ]
    if report.notes:
        desc_parts.append("")
        desc_parts.extend(f"- {n}" for n in report.notes)
    desc_parts += ["", "Source: National Rail journey planner. Auto-generated."]
    description = "\n".join(desc_parts)

    return [
        "BEGIN:VEVENT",
        f"UID:{d.isoformat()}@{config.ICS_UID_DOMAIN}",
        f"DTSTAMP:{dtstamp}",
        f"DTSTART;VALUE=DATE:{d:%Y%m%d}",
        f"DTEND;VALUE=DATE:{(d + timedelta(days=1)):%Y%m%d}",  # exclusive => one day
        f"SUMMARY:{_escape(summary)}",
        f"DESCRIPTION:{_escape(description)}",
        "TRANSP:TRANSPARENT",
        "END:VEVENT",
    ]


def build_calendar(reports: list[DayReport], *, now: datetime | None = None) -> str:
    """Return the full .ics document for the affected days in ``reports``."""
    dtstamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{config.ICS_PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{config.CALENDAR_NAME}",
        f"X-WR-TIMEZONE:{config.CALENDAR_TIME_ZONE}",
        f"REFRESH-INTERVAL;VALUE=DURATION:{config.ICS_REFRESH}",
        f"X-PUBLISHED-TTL:{config.ICS_REFRESH}",
    ]
    for report in reports:
        if report.affected:
            lines.extend(_event_lines(report, dtstamp))
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(ln) for ln in lines) + "\r\n"


def write_calendar(
    reports: list[DayReport], path=None, *, now: datetime | None = None
) -> str:
    """Write the feed to ``path`` (default config.OUTPUT_ICS). Returns the text."""
    path = path or config.OUTPUT_ICS
    text = build_calendar(reports, now=now)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text
