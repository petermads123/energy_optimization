"""Shared foundation for the decision cores.

Everything both `energy_optimization.ev` and `energy_optimization.interruptible`
need lives here: the error types, the validation helpers behind `ConfigError`, the
price types and the quarter-hour grid. All grid arithmetic is in UTC; two datetimes
that share a `ZoneInfo` tzinfo compare by wall-clock time and ignore `fold`, so every
comparison converts to UTC first.
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


class ConfigError(ValueError):
    """Invalid input, naming the offending field.

    Attributes:
        field: The name of the offending field, dotted when nested.
    """

    def __init__(self, field: str, message: str) -> None:
        """Create the error.

        Args:
            field: The name of the offending field.
            message: What is wrong with it, naming the value.
        """
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message


class SolverError(RuntimeError):
    """The solver failed.

    Attributes:
        status: The status `scipy.optimize.milp` reported.
        message: The message it reported.
    """

    def __init__(self, status: int, message: str) -> None:
        """Build the error.

        Args:
            status: The `milp` status code.
            message: The `milp` message.
        """
        super().__init__(f"HiGHS status {status}: {message}")
        self.status = status
        self.message = message


def fail(field: str, message: str, ctx: str = "") -> ConfigError:
    """Build a `ConfigError`, prefixing the message with its context.

    Args:
        field: The name of the offending field.
        message: What is wrong with it.
        ctx: Text put before the message, such as the owning object.

    Returns:
        The error, ready to raise.
    """
    return ConfigError(field, f"{ctx}{message}")


def check_number(field: str, value: object, ctx: str = "") -> float:
    """Return `value` as a finite float; reject bools, non-numbers, NaN and inf.

    Args:
        field: The name of the field being checked.
        value: The value to check.
        ctx: Text put before an error message.

    Returns:
        The value as a float.

    Raises:
        ConfigError: If `value` is not a finite number.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise fail(field, f"must be a number, got {value!r}", ctx)
    try:
        result = float(value)
    except OverflowError:
        raise fail(
            field, "must be finite, got an integer too large for a float", ctx
        ) from None
    if not math.isfinite(result):
        raise fail(field, f"must be finite, got {value!r}", ctx)
    return result


def check_whole(field: str, value: object, ctx: str = "") -> int:
    """Return `value` as an int; accept an integral float, reject everything else.

    Args:
        field: The name of the field being checked.
        value: The value to check.
        ctx: Text put before an error message.

    Returns:
        The value as an int.

    Raises:
        ConfigError: If `value` is not a whole number.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise fail(field, f"must be a whole number, got {value!r}", ctx)
    if isinstance(value, float) and (not math.isfinite(value) or value != int(value)):
        raise fail(field, f"must be a whole number, got {value!r}", ctx)
    return int(value)


def check_aware(field: str, value: object, ctx: str = "") -> datetime:
    """Return `value` if it is a timezone-aware datetime, else raise.

    Args:
        field: The name of the field being checked.
        value: The value to check.
        ctx: Text put before an error message.

    Returns:
        The value.

    Raises:
        ConfigError: If `value` is not an aware datetime.
    """
    if not isinstance(value, datetime):
        raise fail(field, f"must be a datetime, got {value!r}", ctx)
    if value.tzinfo is None or value.utcoffset() is None:
        raise fail(field, f"must be timezone-aware, got naive {value.isoformat()}", ctx)
    return value


def check_bool(field: str, value: object, ctx: str = "") -> bool:
    """Return `value` if it is a bool, else raise.

    Args:
        field: The name of the field being checked.
        value: The value to check.
        ctx: Text put before an error message.

    Returns:
        The value.

    Raises:
        ConfigError: If `value` is not a bool.
    """
    if not isinstance(value, bool):
        raise fail(field, f"must be a bool, got {value!r}", ctx)
    return value


def check_text(field: str, value: object, ctx: str = "") -> str:
    """Return `value` if it is a non-empty string after stripping, else raise.

    Args:
        field: The name of the field being checked.
        value: The value to check.
        ctx: Text put before an error message.

    Returns:
        The value.

    Raises:
        ConfigError: If `value` is not a non-blank string.
    """
    if not isinstance(value, str) or not value.strip():
        raise fail(field, f"must be a non-empty string, got {value!r}", ctx)
    return value


def set_field(obj: object, field: str, value: object) -> None:
    """Set a field on a frozen dataclass.

    Args:
        obj: The dataclass instance.
        field: The attribute to set.
        value: The new value.
    """
    object.__setattr__(obj, field, value)


@dataclass(frozen=True)
class PriceInterval:
    """One priced interval.

    Attributes:
        start: Start, aware, on a quarter boundary in UTC.
        end: End, aware, on a quarter boundary in UTC, after `start`.
        price: Full consumer price per kWh; finite, may be negative.
    """

    start: datetime
    end: datetime
    price: float

    def __post_init__(self) -> None:
        """Validate the interval."""
        start = check_aware("start", self.start)
        end = check_aware("end", self.end)
        if end.astimezone(UTC) <= start.astimezone(UTC):
            raise fail(
                "end", f"must be after start {start.isoformat()}, got {end.isoformat()}"
            )
        for name, moment in (("start", start), ("end", end)):
            utc = moment.astimezone(UTC)
            if utc.minute % 15 or utc.second or utc.microsecond:
                raise fail(
                    name,
                    f"must be on a quarter-hour boundary, got {moment.isoformat()}",
                )
        set_field(self, "price", check_number("price", self.price))


@dataclass(frozen=True)
class PriceSeries:
    """The price series.

    Attributes:
        intervals: Priced intervals, in any order but not overlapping.
        updated_at: When the series was last updated, aware.
    """

    intervals: tuple[PriceInterval, ...]
    updated_at: datetime

    def __post_init__(self) -> None:
        """Validate the series."""
        if isinstance(self.intervals, str) or not isinstance(self.intervals, Iterable):
            raise fail("intervals", f"must be a sequence, got {self.intervals!r}")
        intervals = tuple(self.intervals)
        for interval in intervals:
            if not isinstance(interval, PriceInterval):
                raise fail("intervals", f"must hold PriceInterval, got {interval!r}")
        set_field(self, "intervals", intervals)
        check_aware("updated_at", self.updated_at)
        ordered = sorted(intervals, key=lambda item: item.start.astimezone(UTC))
        for earlier, later in zip(ordered, ordered[1:], strict=False):
            if later.start.astimezone(UTC) < earlier.end.astimezone(UTC):
                raise fail(
                    "intervals",
                    f"overlap: {earlier.start.isoformat()}..{earlier.end.isoformat()} "
                    f"and {later.start.isoformat()}..{later.end.isoformat()}",
                )


QUARTER: timedelta = timedelta(minutes=15)


def to_utc(moment: datetime, name: str) -> datetime:
    """Return an aware moment in UTC; a naive one would be read in the host's zone.

    Args:
        moment: An aware datetime.
        name: The argument's name, used in the error message.

    Returns:
        The same instant in UTC.

    Raises:
        ValueError: If `moment` is naive.
    """
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware, got {moment.isoformat()}")
    return moment.astimezone(UTC)


def floor_to_quarter(moment: datetime) -> datetime:
    """Return the start of the quarter containing a moment.

    Args:
        moment: An aware datetime.

    Returns:
        The start of its quarter-hour, in UTC.

    Raises:
        ValueError: If `moment` is naive.
    """
    utc = to_utc(moment, "moment")
    return utc.replace(minute=utc.minute - utc.minute % 15, second=0, microsecond=0)


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
    start_utc = to_utc(start, "start")
    end_utc = to_utc(end, "end")
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
    return abs(to_utc(now, "now") - series.updated_at.astimezone(UTC)) <= max_age


def main() -> None:
    """Showcase this module's functionality."""
    start = datetime(2026, 10, 21, 18, 0, tzinfo=UTC)
    end = datetime(2026, 10, 21, 19, 0, tzinfo=UTC)
    price = 1.25
    series = PriceSeries(
        intervals=(PriceInterval(start=start, end=end, price=price),),
        updated_at=start,
    )

    quarters = quarter_prices(series)

    print(f"{len(quarters)} quarters, each at {price}:")
    for quarter_start in quarters:
        print(f"  {quarter_start.isoformat()}")

    # A price off the quarter grid is rejected with the field named.
    bad_end = datetime(2026, 10, 21, 19, 10, tzinfo=UTC)

    try:
        PriceInterval(start=start, end=bad_end, price=price)
    except ConfigError as error:
        print(f"rejected: {error} (field={error.field})")


if __name__ == "__main__":
    main()
