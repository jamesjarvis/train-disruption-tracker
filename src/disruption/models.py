"""Core data structures shared across the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True)
class TrainOption:
    """One train option returned by the journey planner for a window."""

    departure: datetime  # scheduled departure from the origin station
    disrupted: bool  # itinerary has a replacement-bus leg OR is cancelled
    reason: str | None = None  # short human description, e.g. "Bus service"

    def __hash__(self) -> int:  # dedupe by departure time across stepped requests
        return hash(self.departure)


@dataclass
class DayReport:
    """AM/PM disruption summary for a single date."""

    date: date
    am_total: int = 0
    am_disrupted: int = 0
    pm_total: int = 0
    pm_disrupted: int = 0
    # The actual disrupted trains, so the calendar can list times + reasons.
    am_disrupted_trains: list[TrainOption] = field(default_factory=list)
    pm_disrupted_trains: list[TrainOption] = field(default_factory=list)

    @property
    def affected(self) -> bool:
        return self.am_disrupted > 0 or self.pm_disrupted > 0

    @staticmethod
    def _pct(disrupted: int, total: int) -> int:
        return round(100 * disrupted / total) if total else 0

    @property
    def am_pct(self) -> int:
        return self._pct(self.am_disrupted, self.am_total)

    @property
    def pm_pct(self) -> int:
        return self._pct(self.pm_disrupted, self.pm_total)
