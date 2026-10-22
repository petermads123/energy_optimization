"""The quarter-hour grid: ready-time deadlines across DST, price splitting, freshness.

All internal arithmetic is in UTC. Two datetimes that share a `ZoneInfo` tzinfo
compare by wall-clock time and ignore `fold`, so every comparison here converts to
UTC first.
"""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .model import PriceSeries

QUARTER: timedelta = timedelta(minutes=15)


def floor_to_quarter(moment: datetime) -> datetime:
    """Return the start of the quarter containing a moment.

    Args:
        moment: An aware datetime.

    Returns:
        The start of its quarter-hour, in UTC.

    Raises:
        ValueError: If `moment` is naive.
    """
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"moment must be timezone-aware, got {moment.isoformat()}")
    utc = moment.astimezone(UTC)
    return utc.replace(minute=utc.minute - utc.minute % 15, second=0, microsecond=0)


def _utc(moment: datetime, name: str) -> datetime:
    """Return an aware moment in UTC; a naive one would be read in the host's zone."""
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware, got {moment.isoformat()}")
    return moment.astimezone(UTC)


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


def quarter_starts(start: datetime, end: datetime) -> list[datetime]:
    """List the quarter starts covering `start` up to `end`.

    Args:
        start: An aware datetime.
        end: An aware datetime.

    Returns:
        UTC quarter starts from the quarter containing `start` while before `end`;
        empty when `end <= start`.

    Raises:
        ValueError: If `start` or `end` is naive.
    """
    start_utc = _utc(start, "start")
    end_utc = _utc(end, "end")
    if end_utc <= start_utc:
        return []
    current = floor_to_quarter(start_utc)
    starts: list[datetime] = []
    while current < end_utc:
        starts.append(current)
        current += QUARTER
    return starts


def quarter_prices(series: PriceSeries) -> dict[datetime, float]:
    """Split every price interval into quarters.

    Args:
        series: The price series.

    Returns:
        The price of each quarter, keyed by its UTC start.
    """
    prices: dict[datetime, float] = {}
    for interval in series.intervals:
        current = interval.start.astimezone(UTC)
        end = interval.end.astimezone(UTC)
        while current < end:
            prices[current] = interval.price
            current += QUARTER
    return prices


def prices_fresh(series: PriceSeries, now: datetime, max_age: timedelta) -> bool:
    """Say whether the price series is recent enough to plan with.

    Args:
        series: The price series.
        now: The time now, aware.
        max_age: How old the series may be.

    Returns:
        `abs(now - series.updated_at) <= max_age`; a timestamp far in the future is
        as untrustworthy as one far in the past.

    Raises:
        ValueError: If `now` is naive.
    """
    return abs(_utc(now, "now") - series.updated_at.astimezone(UTC)) <= max_age


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
