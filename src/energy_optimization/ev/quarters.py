"""The quarter-hour grid: ready-time deadlines across DST, price splitting, freshness.

All internal arithmetic is in UTC. Two datetimes that share a `ZoneInfo` tzinfo
compare by wall-clock time and ignore `fold`, so every comparison here converts to
UTC first.
"""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from energy_optimization.common import QUARTER as QUARTER
from energy_optimization.common import floor_to_quarter as floor_to_quarter
from energy_optimization.common import prices_fresh as prices_fresh
from energy_optimization.common import quarter_prices as quarter_prices
from energy_optimization.common import quarter_starts as quarter_starts
from energy_optimization.common import to_utc as _utc


def next_occurrence(ready_time: time, after: datetime, timezone: str) -> datetime:
    """Return the first moment after `after` at which the local time is `ready_time`.

    A ready time inside a spring-forward gap is shifted forward by the gap's length.
    One inside a fall-back fold happens twice; the first occurrence is returned
    unless `after` already lies at or past it, in which case the second is. The
    `fold` attribute of `ready_time` is ignored.

    Args:
        ready_time: A local time of day; its `fold` is ignored.
        after: An aware datetime; the result is strictly later.
        timezone: IANA name of the zone `ready_time` is local to.

    Returns:
        The moment, in UTC.

    Raises:
        ValueError: If `after` is naive.
        ZoneInfoNotFoundError: If `timezone` is not a known zone.
    """
    after_utc = _utc(after, "after")
    zone = ZoneInfo(timezone)
    wall = ready_time.replace(tzinfo=None, fold=0)
    day = after_utc.astimezone(zone).date()
    while True:
        naive = datetime.combine(day, wall)
        first = naive.replace(tzinfo=zone)
        candidates = [first.astimezone(UTC)]
        second = first.replace(fold=1).astimezone(UTC)
        # In a fold both readings are real; in a gap the fold=1 reading lands before
        # the gap, where the wall clock shows a different time.
        if (
            second != candidates[0]
            and second.astimezone(zone).replace(tzinfo=None) == naive
        ):
            candidates.append(second)
        for candidate in candidates:
            if candidate > after_utc:
                return candidate
        day += timedelta(days=1)


def main() -> None:
    """Showcase this module's functionality."""
    ready_time = time(7, 0)
    timezone = "Europe/Copenhagen"
    nights = [
        datetime(2026, 3, 28, 7, 0, tzinfo=ZoneInfo(timezone)),  # spring forward
        datetime(2026, 10, 24, 7, 0, tzinfo=ZoneInfo(timezone)),  # fall back
        datetime(2026, 6, 1, 7, 0, tzinfo=ZoneInfo(timezone)),  # an ordinary day
    ]

    for after in nights:
        deadline = next_occurrence(ready_time, after, timezone)
        quarters = quarter_starts(after, deadline)
        print(
            f"after {after.isoformat()}: deadline {deadline.isoformat()}, "
            f"{len(quarters)} quarters"
        )

    # A ready time inside the spring-forward gap moves forward by the gap.
    ready_time = time(2, 30)
    after = datetime(2026, 3, 28, 12, 0, tzinfo=UTC)

    deadline = next_occurrence(ready_time, after, timezone)

    print(f"02:30 on the gap day -> {deadline.isoformat()}")


if __name__ == "__main__":
    main()
