"""Tests for the iCalendar writer (pure, no network)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from disruption.ics_writer import _escape, _fold, build_calendar
from disruption.models import DayReport

NOW = datetime(2026, 6, 29, 6, 0, 0, tzinfo=timezone.utc)


def _affected() -> DayReport:
    return DayReport(date(2026, 7, 5), am_total=6, am_disrupted=3,
                     pm_total=10, pm_disrupted=0, notes=["Rail replacement bus"])


def _clean() -> DayReport:
    return DayReport(date(2026, 7, 6), am_total=20, am_disrupted=0,
                     pm_total=22, pm_disrupted=0)


def test_only_affected_days_become_events():
    ics = build_calendar([_affected(), _clean()], now=NOW)
    assert ics.count("BEGIN:VEVENT") == 1
    assert "SUMMARY:\U0001f686 Bexley AM 50% / PM 0% disrupted" in ics


def test_all_day_event_uses_date_values_and_stable_uid():
    ics = build_calendar([_affected()], now=NOW)
    assert "DTSTART;VALUE=DATE:20260705" in ics
    assert "DTEND;VALUE=DATE:20260706" in ics  # exclusive end => single day
    assert "UID:2026-07-05@bexside-trains" in ics


def test_wellformed_envelope_and_crlf():
    ics = build_calendar([_affected()], now=NOW)
    assert ics.startswith("BEGIN:VCALENDAR\r\n")
    assert ics.rstrip().endswith("END:VCALENDAR")
    assert "\r\n" in ics and "VERSION:2.0" in ics


def test_empty_when_no_disruption():
    ics = build_calendar([_clean()], now=NOW)
    assert "BEGIN:VEVENT" not in ics
    assert "BEGIN:VCALENDAR" in ics  # still a valid empty calendar


def test_text_escaping_of_commas_and_semicolons():
    # Escaping happens before line folding, so assert on _escape directly.
    assert _escape("Bus; replacement, all day") == "Bus\\; replacement\\, all day"
    assert _escape("a\\b\nc") == "a\\\\b\\nc"


def test_long_line_is_folded_to_75_octets():
    folded = _fold("X" * 200)
    for ln in folded.split("\r\n"):
        assert len(ln.encode("utf-8")) <= 75


def test_folding_never_splits_a_multibyte_char():
    # En-dashes (3 bytes) packed so a naive octet split would land mid-char.
    line = "DESCRIPTION:" + "Dartford–Gravesend " * 8
    folded = _fold(line)
    # Must still be decodable and round-trip to the original once unfolded.
    assert folded.encode("utf-8").decode("utf-8") == folded
    unfolded = folded.replace("\r\n ", "")
    assert unfolded == line
    for ln in folded.split("\r\n"):
        assert len(ln.encode("utf-8")) <= 75


def test_real_feed_with_endash_note_is_valid_utf8():
    rep = DayReport(date(2026, 7, 5), am_total=6, am_disrupted=6, pm_total=10,
                    pm_disrupted=10,
                    notes=["Rail replacement bus between Dartford–Gravesend all day"])
    ics = build_calendar([rep], now=NOW)
    assert ics.encode("utf-8")  # would raise if a surrogate slipped in
