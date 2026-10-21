"""Tests for energy_optimization.ev.quarters (T11): the quarter grid across DST."""

from datetime import UTC, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest

from energy_optimization.ev.model import PriceInterval, PriceSeries
from energy_optimization.ev.quarters import (
    QUARTER,
    floor_to_quarter,
    next_occurrence,
    prices_fresh,
    quarter_prices,
    quarter_starts,
)

CPH = "Europe/Copenhagen"
ZONE = ZoneInfo(CPH)


def utc(
    year: int,
    month: int,
    day: int,
    hour: int = 0,
    minute: int = 0,
    second: int = 0,
    *,
    microsecond: int = 0,
) -> datetime:
    return datetime(year, month, day, hour, minute, second, microsecond, tzinfo=UTC)


def naive(text: str) -> datetime:
    return datetime.fromisoformat(text)


def test_quarter_is_fifteen_minutes() -> None:
    assert timedelta(minutes=15) == QUARTER


# --- floor_to_quarter ----------------------------------------------------------


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (utc(2026, 10, 21, 14, 45), utc(2026, 10, 21, 14, 45)),
        (utc(2026, 10, 21, 14, 59, 59, microsecond=999999), utc(2026, 10, 21, 14, 45)),
        (utc(2026, 10, 21, 15, 0), utc(2026, 10, 21, 15, 0)),
        (utc(2026, 10, 21, 0, 0, 0, microsecond=1), utc(2026, 10, 21, 0, 0)),
        (datetime(2026, 10, 21, 18, 7, 30, 500, tzinfo=ZONE), utc(2026, 10, 21, 16, 0)),
        (
            datetime(2026, 1, 1, 10, 10, tzinfo=timezone(timedelta(minutes=10))),
            utc(2026, 1, 1, 10, 0),
        ),
        (
            datetime(2026, 1, 1, 10, 0, tzinfo=timezone(timedelta(minutes=10))),
            utc(2026, 1, 1, 9, 45),
        ),
        (
            datetime(
                2026, 1, 1, 5, 50, tzinfo=timezone(timedelta(hours=5, minutes=45))
            ),
            utc(2026, 1, 1, 0, 0),
        ),
    ],
)
def test_floor_to_quarter_returns_the_utc_quarter_start(
    moment: datetime, expected: datetime
) -> None:
    result = floor_to_quarter(moment)

    assert result == expected
    assert result.tzinfo is UTC


@pytest.mark.parametrize(
    ("fold", "expected"),
    [(0, utc(2026, 10, 25, 0, 30)), (1, utc(2026, 10, 25, 1, 30))],
)
def test_floor_to_quarter_respects_fold(fold: int, expected: datetime) -> None:
    moment = datetime(2026, 10, 25, 2, 40, fold=fold, tzinfo=ZONE)

    assert floor_to_quarter(moment) == expected


def test_floor_to_quarter_rejects_naive() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        floor_to_quarter(naive("2026-01-01T12:00:00"))


# --- next_occurrence -----------------------------------------------------------


@pytest.mark.parametrize(
    ("ready", "after", "expected"),
    [
        # An ordinary day: 07:00 CEST is 05:00Z.
        (time(7, 0), utc(2026, 10, 21, 3, 0), utc(2026, 10, 21, 5, 0)),
        # Strictly after: equal rolls over, one microsecond earlier does not.
        (time(7, 0), utc(2026, 10, 21, 5, 0), utc(2026, 10, 22, 5, 0)),
        (
            time(7, 0),
            utc(2026, 10, 21, 4, 59, 59, microsecond=999999),
            utc(2026, 10, 21, 5, 0),
        ),
        # Later the same local evening: next local day.
        (time(7, 0), utc(2026, 10, 21, 21, 30), utc(2026, 10, 22, 5, 0)),
        # T11: 02:30 in the spring-forward gap shifts forward to 03:30 CEST.
        (time(2, 30), utc(2026, 3, 28, 12, 0), utc(2026, 3, 29, 1, 30)),
        # T11: 02:30 in the fall-back fold is the first occurrence, 02:30 CEST.
        (time(2, 30), utc(2026, 10, 24, 12, 0), utc(2026, 10, 25, 0, 30)),
        # After the shifted gap instant exactly: the next day.
        (time(2, 30), utc(2026, 3, 29, 1, 30), utc(2026, 3, 30, 0, 30)),
        # After lies after the wall-clock 02:30 but before the shifted instant.
        (time(2, 30), utc(2026, 3, 29, 1, 15), utc(2026, 3, 29, 1, 30)),
        # The local date, not the UTC date, decides the day.
        (time(0, 15), utc(2026, 10, 21, 22, 30), utc(2026, 10, 22, 22, 15)),
    ],
)
def test_next_occurrence_returns_the_first_matching_moment_after(
    ready: time, after: datetime, expected: datetime
) -> None:
    result = next_occurrence(ready, after, CPH)

    assert result == expected
    assert result.tzinfo is UTC


def test_next_occurrence_uses_the_local_date_west_of_utc() -> None:
    # 02:00Z on 22 Oct is 22:00 on 21 Oct in New York; 23:00 is the same evening.
    result = next_occurrence(time(23, 0), utc(2026, 10, 22, 2, 0), "America/New_York")

    assert result == utc(2026, 10, 22, 3, 0)


@pytest.mark.parametrize(
    ("after", "expected"),
    [
        # Between the two 02:30s the second occurrence is next.
        (utc(2026, 10, 25, 0, 45), utc(2026, 10, 25, 1, 30)),
        # Exactly on the first occurrence: strictly after, so the second.
        (utc(2026, 10, 25, 0, 30), utc(2026, 10, 25, 1, 30)),
        # Just before the first occurrence: the first.
        (utc(2026, 10, 25, 0, 29, 59), utc(2026, 10, 25, 0, 30)),
        # Exactly on the second occurrence: the next day.
        (utc(2026, 10, 25, 1, 30), utc(2026, 10, 26, 1, 30)),
    ],
)
def test_next_occurrence_in_the_fold_goes_to_the_second_occurrence_once_the_first_has_passed(
    after: datetime, expected: datetime
) -> None:
    assert next_occurrence(time(2, 30), after, CPH) == expected


@pytest.mark.parametrize(
    ("after", "expected"),
    [
        (utc(2026, 3, 28, 12, 0), utc(2026, 3, 29, 1, 30)),
        (utc(2026, 10, 24, 12, 0), utc(2026, 10, 25, 0, 30)),
    ],
)
def test_next_occurrence_ignores_the_fold_attribute_of_the_ready_time(
    after: datetime, expected: datetime
) -> None:
    assert next_occurrence(time(2, 30, fold=1), after, CPH) == expected
    assert next_occurrence(time(2, 30, fold=1), after, CPH) == next_occurrence(
        time(2, 30), after, CPH
    )


def test_next_occurrence_is_stable_when_after_is_in_another_offset() -> None:
    plus_five = timezone(timedelta(hours=5))

    result = next_occurrence(
        time(7, 0), datetime(2026, 10, 21, 8, 0, tzinfo=plus_five), CPH
    )

    assert result == utc(2026, 10, 21, 5, 0)


def test_next_occurrence_rejects_a_naive_after() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        next_occurrence(time(7, 0), naive("2026-10-21T07:00:00"), CPH)


def test_next_occurrence_raises_for_an_unknown_zone() -> None:
    with pytest.raises(ZoneInfoNotFoundError):
        next_occurrence(time(7, 0), utc(2026, 10, 21, 0, 0), "Mars/Base")


def test_next_occurrence_equal_to_after_in_the_same_zone_rolls_to_the_next_day() -> (
    None
):
    after = datetime(2026, 10, 21, 7, 0, tzinfo=ZONE)

    assert next_occurrence(time(7, 0), after, CPH) == utc(2026, 10, 22, 5, 0)


@pytest.mark.parametrize(
    ("after", "deadline", "count"),
    [
        (datetime(2026, 3, 28, 7, 0, tzinfo=ZONE), utc(2026, 3, 29, 5, 0), 92),
        (datetime(2026, 10, 24, 7, 0, tzinfo=ZONE), utc(2026, 10, 25, 6, 0), 100),
        (datetime(2026, 6, 1, 7, 0, tzinfo=ZONE), utc(2026, 6, 2, 5, 0), 96),
    ],
)
def test_dst_days_give_92_and_100_quarters_between_two_seven_oclocks(
    after: datetime, deadline: datetime, count: int
) -> None:
    result = next_occurrence(time(7, 0), after, CPH)

    assert result == deadline
    assert len(quarter_starts(after, result)) == count


def test_deadline_across_midnight_keeps_the_local_ready_time() -> None:
    after = datetime(2026, 3, 28, 22, 0, tzinfo=ZONE)

    result = next_occurrence(time(7, 0), after, CPH)

    assert result.astimezone(ZONE).time() == time(7, 0)
    assert result.astimezone(ZONE).date().isoformat() == "2026-03-29"


# --- quarter_starts ------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "end", "count"),
    [
        (utc(2026, 1, 1, 10, 0), utc(2026, 1, 1, 11, 0), 4),
        (utc(2026, 1, 1, 10, 0), utc(2026, 1, 1, 11, 0, 0, microsecond=1), 5),
        (utc(2026, 1, 1, 10, 7), utc(2026, 1, 1, 10, 8), 1),
        (utc(2026, 1, 1, 10, 7), utc(2026, 1, 1, 10, 15), 1),
        (utc(2026, 1, 1, 10, 7), utc(2026, 1, 1, 10, 20), 2),
        (utc(2026, 1, 1, 10, 7), utc(2026, 1, 1, 10, 7), 0),
        (utc(2026, 1, 1, 10, 7), utc(2026, 1, 1, 10, 5), 0),
        (utc(2026, 1, 1, 10, 7), utc(2026, 1, 1, 10, 7, 0, microsecond=0), 0),
    ],
)
def test_quarter_starts_counts_the_quarters_touched(
    start: datetime, end: datetime, count: int
) -> None:
    assert len(quarter_starts(start, end)) == count


def test_quarter_starts_begins_with_the_quarter_containing_start() -> None:
    result = quarter_starts(utc(2026, 1, 1, 10, 7), utc(2026, 1, 1, 10, 50))

    assert result == [
        utc(2026, 1, 1, 10, 0),
        utc(2026, 1, 1, 10, 15),
        utc(2026, 1, 1, 10, 30),
        utc(2026, 1, 1, 10, 45),
    ]
    assert all(item.tzinfo is UTC for item in result)


def test_quarter_starts_compares_in_utc_across_the_fall_back_fold() -> None:
    start = datetime(2026, 10, 25, 2, 0, tzinfo=ZONE)  # 00:00Z
    end = datetime(2026, 10, 25, 2, 0, fold=1, tzinfo=ZONE)  # 01:00Z, same wall clock

    assert quarter_starts(start, end) == [
        utc(2026, 10, 25, 0, 0),
        utc(2026, 10, 25, 0, 15),
        utc(2026, 10, 25, 0, 30),
        utc(2026, 10, 25, 0, 45),
    ]


def test_quarter_starts_orders_by_instant_when_the_end_has_the_earlier_wall_clock() -> (
    None
):
    start = datetime(2026, 10, 25, 2, 45, tzinfo=ZONE)  # 00:45Z
    end = datetime(2026, 10, 25, 2, 15, fold=1, tzinfo=ZONE)  # 01:15Z

    assert quarter_starts(start, end) == [
        utc(2026, 10, 25, 0, 45),
        utc(2026, 10, 25, 1, 0),
    ]


@pytest.mark.parametrize("which", ["start", "end"])
def test_quarter_starts_rejects_a_naive_moment_rather_than_reading_the_host_zone(
    which: str,
) -> None:
    aware = utc(2026, 1, 1, 10, 0)
    loose = naive("2026-01-01T09:00:00")

    with pytest.raises(ValueError, match=which):
        quarter_starts(*((loose, aware) if which == "start" else (aware, loose)))


# --- quarter_prices ------------------------------------------------------------


def series(*intervals: PriceInterval) -> PriceSeries:
    return PriceSeries(intervals, utc(2026, 1, 1, 0, 0))


def test_quarter_prices_splits_an_hourly_interval_into_four_utc_keys() -> None:
    start = datetime.fromisoformat("2026-10-21T20:00:00+02:00")
    end = datetime.fromisoformat("2026-10-21T21:00:00+02:00")

    result = quarter_prices(series(PriceInterval(start, end, -0.3)))

    assert result == {utc(2026, 10, 21, 18, minute): -0.3 for minute in (0, 15, 30, 45)}
    assert all(key.tzinfo is UTC for key in result)


def test_quarter_prices_covers_the_fall_back_hour_in_fixed_offsets() -> None:
    start = datetime.fromisoformat("2026-10-25T02:00:00+02:00")
    end = datetime.fromisoformat("2026-10-25T02:00:00+01:00")

    result = quarter_prices(series(PriceInterval(start, end, 1.0)))

    assert sorted(result) == [
        utc(2026, 10, 25, 0, minute) for minute in (0, 15, 30, 45)
    ]


def test_quarter_prices_gives_the_boundary_quarter_to_the_later_interval() -> None:
    first = PriceInterval(utc(2026, 1, 1, 0, 0), utc(2026, 1, 1, 1, 0), 1.0)
    second = PriceInterval(utc(2026, 1, 1, 1, 0), utc(2026, 1, 1, 1, 15), 2.0)

    result = quarter_prices(series(first, second))

    assert len(result) == 5
    assert result[utc(2026, 1, 1, 0, 45)] == 1.0
    assert result[utc(2026, 1, 1, 1, 0)] == 2.0


def test_quarter_prices_of_an_empty_series_is_empty() -> None:
    assert quarter_prices(series()) == {}


def test_quarter_prices_handles_an_odd_offset_interval() -> None:
    plus = timezone(timedelta(hours=5, minutes=45))
    start = datetime(2026, 1, 1, 5, 45, tzinfo=plus)  # 00:00Z

    result = quarter_prices(
        series(PriceInterval(start, start + timedelta(minutes=30), 4.0))
    )

    assert sorted(result) == [utc(2026, 1, 1, 0, 0), utc(2026, 1, 1, 0, 15)]


def test_quarter_prices_keeps_a_negative_price() -> None:
    result = quarter_prices(
        series(PriceInterval(utc(2026, 1, 1, 0, 0), utc(2026, 1, 1, 0, 15), -2.5))
    )

    assert result == {utc(2026, 1, 1, 0, 0): -2.5}


# --- prices_fresh --------------------------------------------------------------


@pytest.mark.parametrize(
    ("updated_at", "expected"),
    [
        (utc(2026, 10, 21, 11, 0), True),
        (utc(2026, 10, 21, 10, 59, 59, microsecond=999999), False),
        (utc(2026, 10, 21, 13, 0), True),
        (utc(2026, 10, 21, 13, 0, 0, microsecond=1), False),
        (utc(2026, 10, 21, 12, 0), True),
        (datetime.fromisoformat("2026-10-21T13:00:00+01:00"), True),
        (datetime.fromisoformat("2026-10-21T14:00:00+01:00"), True),
        (datetime.fromisoformat("2026-10-21T14:00:01+01:00"), False),
    ],
)
def test_prices_fresh_is_an_inclusive_bound_in_both_directions(
    updated_at: datetime, expected: bool
) -> None:
    prices = PriceSeries((), updated_at)

    assert (
        prices_fresh(prices, utc(2026, 10, 21, 12, 0), timedelta(minutes=60))
        is expected
    )


def test_prices_fresh_compares_the_same_instant_across_offsets() -> None:
    prices = PriceSeries((), datetime.fromisoformat("2026-10-21T20:00:00+02:00"))

    assert prices_fresh(prices, utc(2026, 10, 21, 18, 0), timedelta(seconds=1))


def test_prices_fresh_across_the_fall_back_fold_in_one_zone() -> None:
    updated = datetime(2026, 10, 25, 2, 30, tzinfo=ZONE)  # 00:30Z
    now = datetime(2026, 10, 25, 2, 30, fold=1, tzinfo=ZONE)  # 01:30Z

    assert not prices_fresh(PriceSeries((), updated), now, timedelta(minutes=30))
    assert prices_fresh(PriceSeries((), updated), now, timedelta(minutes=60))


def test_prices_fresh_rejects_a_naive_now() -> None:
    with pytest.raises(ValueError, match="now"):
        prices_fresh(series(), naive("2026-01-01T00:00:00"), timedelta(minutes=60))
