"""Tests for energy_optimization.common (T10, T15): the shared foundation."""

import importlib
import math
from dataclasses import FrozenInstanceError, dataclass
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from decimal import Decimal
from zoneinfo import ZoneInfo

import numpy as np
import pytest

import energy_optimization.ev as ev
import energy_optimization.interruptible as interruptible
from energy_optimization import common
from energy_optimization.common import (
    QUARTER,
    ConfigError,
    PriceInterval,
    PriceSeries,
    SolverError,
    check_aware,
    check_bool,
    check_number,
    check_text,
    check_whole,
    fail,
    floor_to_quarter,
    prices_fresh,
    quarter_prices,
    quarter_starts,
    set_field,
    to_utc,
)
from energy_optimization.common import main as showcase

ev_model = importlib.import_module("energy_optimization.ev.model")
ev_quarters = importlib.import_module("energy_optimization.ev.quarters")
ev_optimize = importlib.import_module("energy_optimization.ev.optimize")

NOW = datetime(2026, 10, 21, 14, 0, tzinfo=UTC)
NAIVE = datetime.fromisoformat("2026-01-01T00:00:00")


class NoOffset(tzinfo):
    """A tzinfo that cannot say its offset."""

    def utcoffset(self, dt: datetime | None) -> timedelta | None:  # noqa: ARG002 - tzinfo protocol
        """Say nothing."""
        return None

    def dst(self, dt: datetime | None) -> timedelta | None:  # noqa: ARG002 - tzinfo protocol
        """Say nothing."""
        return None

    def tzname(self, dt: datetime | None) -> str | None:  # noqa: ARG002 - tzinfo protocol
        """Say nothing."""
        return None


# --- T10: the moved names are the same objects everywhere -----------------------------


@pytest.mark.parametrize(
    ("module", "name"),
    [
        (ev_model, "ConfigError"),
        (ev_model, "PriceInterval"),
        (ev_model, "PriceSeries"),
        (ev_quarters, "QUARTER"),
        (ev_quarters, "floor_to_quarter"),
        (ev_quarters, "quarter_starts"),
        (ev_quarters, "quarter_prices"),
        (ev_quarters, "prices_fresh"),
        (ev_optimize, "SolverError"),
        (ev, "ConfigError"),
        (ev, "PriceInterval"),
        (ev, "PriceSeries"),
        (ev, "SolverError"),
        (interruptible, "ConfigError"),
        (interruptible, "PriceInterval"),
        (interruptible, "PriceSeries"),
        (interruptible, "SolverError"),
    ],
)
def test_reexported_names_are_the_common_objects(module: object, name: str) -> None:
    assert getattr(module, name) is getattr(common, name)


def test_ev_quarters_utc_alias_is_common_to_utc() -> None:
    assert ev_quarters._utc is to_utc


def test_ev_model_private_helpers_are_the_common_helpers() -> None:
    assert ev_model._fail is fail
    assert ev_model._number is check_number
    assert ev_model._whole is check_whole
    assert ev_model._aware is check_aware
    assert ev_model._bool is check_bool
    assert ev_model._text is check_text
    assert ev_model._set is set_field


@pytest.mark.parametrize("package", [ev, interruptible])
def test_every_name_in_all_is_importable(package: object) -> None:
    for name in package.__all__:  # type: ignore[attr-defined]  # a module, checked by name
        assert getattr(package, name) is not None


def test_solver_error_from_ev_is_caught_as_the_common_class() -> None:
    with pytest.raises(common.SolverError):
        raise ev_optimize.SolverError(2, "x")


def test_error_strings_are_unchanged_by_the_move() -> None:
    assert str(SolverError(2, "x")) == "HiGHS status 2: x"
    assert str(ConfigError("f", "m")) == "f: m"


def test_config_error_is_a_value_error_with_field_and_message() -> None:
    error = ConfigError("a.b", "bad")

    assert isinstance(error, ValueError)
    assert (error.field, error.message) == ("a.b", "bad")


def test_solver_error_is_a_runtime_error_with_status_and_message() -> None:
    error = SolverError(4, "node limit")

    assert isinstance(error, RuntimeError)
    assert (error.status, error.message) == (4, "node limit")


# --- helpers ---------------------------------------------------------------------------


def test_fail_returns_without_raising_and_prefixes_the_context() -> None:
    error = fail("x", "bad", "Charger a: ")

    assert isinstance(error, ConfigError)
    assert error.field == "x"
    assert error.message == "Charger a: bad"
    assert str(error) == "x: Charger a: bad"


@pytest.mark.parametrize("value", [0, 1, -3, 2.5, -0.0, 1e300, np.float64(1.5)])
def test_check_number_accepts_finite_numbers(value: float) -> None:
    assert check_number("f", value) == float(value)
    assert isinstance(check_number("f", value), float)


@pytest.mark.parametrize(
    "value",
    [True, False, None, "1", math.nan, math.inf, -math.inf, Decimal("1"), [1], 10**400],
)
def test_check_number_rejects_everything_else(value: object) -> None:
    with pytest.raises(ConfigError) as caught:
        check_number("f", value, "ctx ")
    assert caught.value.field == "f"
    assert caught.value.message.startswith("ctx ")


def test_check_number_names_an_integer_too_large_for_a_float() -> None:
    with pytest.raises(ConfigError, match="too large"):
        check_number("f", 10**400)


def test_check_whole_accepts_integral_floats_and_huge_ints() -> None:
    assert check_whole("f", 4.0) == 4
    assert type(check_whole("f", 4.0)) is int
    assert check_whole("f", -0.0) == 0
    assert type(check_whole("f", 1e16)) is int
    assert check_whole("f", 10**30) == 10**30


@pytest.mark.parametrize(
    "value", [True, None, "2", 2.5, 3.0000000000000004, math.inf, math.nan]
)
def test_check_whole_rejects_non_whole_values(value: object) -> None:
    with pytest.raises(ConfigError) as caught:
        check_whole("f", value)
    assert caught.value.field == "f"


def test_check_aware_returns_the_value_and_rejects_naive_and_non_datetimes() -> None:
    assert check_aware("t", NOW) is NOW
    with pytest.raises(ConfigError, match="timezone-aware"):
        check_aware("t", NAIVE)
    with pytest.raises(ConfigError, match="must be a datetime"):
        check_aware("t", "2026-01-01")


def test_check_aware_rejects_a_tzinfo_without_an_offset() -> None:
    with pytest.raises(ConfigError, match="timezone-aware"):
        check_aware("t", datetime(2026, 1, 1, tzinfo=NoOffset()))


def test_check_bool_accepts_bools_only() -> None:
    assert check_bool("b", True) is True
    for value in (0, 1, None, np.bool_(True)):
        with pytest.raises(ConfigError):
            check_bool("b", value)


def test_check_text_returns_the_value_unstripped() -> None:
    assert check_text("t", " a ") == " a "
    assert check_text("t", "é") == "é"
    for value in ("", "  ", None, 3):
        with pytest.raises(ConfigError):
            check_text("t", value)


def test_set_field_changes_a_frozen_dataclass() -> None:
    @dataclass(frozen=True)
    class Box:
        value: int

    box = Box(1)
    with pytest.raises(FrozenInstanceError):
        box.value = 2  # type: ignore[misc]  # proving the dataclass is frozen
    set_field(box, "value", 2)

    assert box.value == 2


# --- to_utc and the grid ---------------------------------------------------------------


def test_to_utc_names_the_argument_when_naive() -> None:
    with pytest.raises(ValueError, match="^when must be timezone-aware"):
        to_utc(NAIVE, "when")


def test_to_utc_rejects_a_tzinfo_without_an_offset() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        to_utc(datetime(2026, 1, 1, tzinfo=NoOffset()), "when")


def test_to_utc_converts_an_odd_offset_to_the_same_instant() -> None:
    moment = datetime(
        2026, 10, 21, 19, 45, tzinfo=timezone(timedelta(hours=5, minutes=45))
    )

    result = to_utc(moment, "m")

    assert result == moment
    assert result.tzinfo is UTC
    assert result.hour == 14


@pytest.mark.parametrize(("fold", "hour"), [(0, 0), (1, 1)])
def test_to_utc_respects_fold_on_the_fall_back_night(fold: int, hour: int) -> None:
    moment = datetime(
        2026, 10, 25, 2, 30, tzinfo=ZoneInfo("Europe/Copenhagen"), fold=fold
    )

    assert to_utc(moment, "m") == datetime(2026, 10, 25, hour, 30, tzinfo=UTC)


def test_quarter_is_fifteen_minutes() -> None:
    assert timedelta(minutes=15) == QUARTER


def test_floor_to_quarter_works_in_utc_from_any_offset() -> None:
    moment = datetime(
        2026, 10, 21, 19, 52, 30, 5, tzinfo=timezone(timedelta(hours=5, minutes=45))
    )

    assert floor_to_quarter(moment) == datetime(2026, 10, 21, 14, 0, tzinfo=UTC)


def test_floor_to_quarter_rejects_naive() -> None:
    with pytest.raises(ValueError, match="moment"):
        floor_to_quarter(NAIVE)


def test_quarter_starts_covers_start_up_to_end_and_is_empty_backwards() -> None:
    start = datetime(2026, 10, 21, 14, 7, tzinfo=UTC)

    assert quarter_starts(start, start + timedelta(minutes=30)) == [
        datetime(2026, 10, 21, 14, 0, tzinfo=UTC),
        datetime(2026, 10, 21, 14, 15, tzinfo=UTC),
        datetime(2026, 10, 21, 14, 30, tzinfo=UTC),
    ]
    assert quarter_starts(start, start) == []
    assert quarter_starts(start, start - QUARTER) == []
    with pytest.raises(ValueError, match="end"):
        quarter_starts(start, NAIVE)


def test_quarter_prices_splits_intervals_and_keys_by_utc_start() -> None:
    offset = timezone(timedelta(hours=2))
    series = PriceSeries(
        intervals=(
            PriceInterval(
                start=datetime(2026, 10, 21, 16, 0, tzinfo=offset),
                end=datetime(2026, 10, 21, 16, 30, tzinfo=offset),
                price=1.5,
            ),
            PriceInterval(
                start=datetime(2026, 10, 21, 14, 30, tzinfo=UTC),
                end=datetime(2026, 10, 21, 14, 45, tzinfo=UTC),
                price=-2.0,
            ),
        ),
        updated_at=NOW,
    )

    assert quarter_prices(series) == {
        datetime(2026, 10, 21, 14, 0, tzinfo=UTC): 1.5,
        datetime(2026, 10, 21, 14, 15, tzinfo=UTC): 1.5,
        datetime(2026, 10, 21, 14, 30, tzinfo=UTC): -2.0,
    }


def test_prices_fresh_is_symmetric_and_inclusive() -> None:
    series = PriceSeries(intervals=(), updated_at=NOW)
    age = timedelta(minutes=60)

    assert prices_fresh(series, NOW + age, age)
    assert prices_fresh(series, NOW - age, age)
    assert not prices_fresh(series, NOW + age + timedelta(seconds=1), age)
    with pytest.raises(ValueError, match="now"):
        prices_fresh(series, NAIVE, age)


# --- price types -----------------------------------------------------------------------


def test_price_interval_rejects_off_grid_and_backwards_intervals() -> None:
    with pytest.raises(ConfigError) as off_grid:
        PriceInterval(NOW, NOW + timedelta(minutes=10), 1.0)
    assert off_grid.value.field == "end"
    with pytest.raises(ConfigError) as backwards:
        PriceInterval(NOW, NOW, 1.0)
    assert backwards.value.field == "end"
    with pytest.raises(ConfigError) as price:
        PriceInterval(NOW, NOW + QUARTER, math.nan)
    assert price.value.field == "price"


def test_price_interval_accepts_an_odd_offset_that_lands_on_a_utc_quarter() -> None:
    offset = timezone(timedelta(hours=5, minutes=45))

    interval = PriceInterval(
        datetime(2026, 10, 21, 19, 45, tzinfo=offset),
        datetime(2026, 10, 21, 20, 0, tzinfo=offset),
        1,
    )

    assert interval.price == 1.0


def test_price_series_rejects_overlap_and_non_intervals() -> None:
    first = PriceInterval(NOW, NOW + 2 * QUARTER, 1.0)
    second = PriceInterval(NOW + QUARTER, NOW + 3 * QUARTER, 1.0)

    with pytest.raises(ConfigError, match="overlap"):
        PriceSeries((first, second), NOW)
    with pytest.raises(ConfigError, match="PriceInterval"):
        PriceSeries(("x",), NOW)  # type: ignore[arg-type]  # untyped callers
    with pytest.raises(ConfigError, match="sequence"):
        PriceSeries("abc", NOW)  # type: ignore[arg-type]  # untyped callers


def test_showcase_runs(capsys: pytest.CaptureFixture[str]) -> None:
    showcase()

    out = capsys.readouterr().out
    assert "4 quarters" in out
    assert "field=end" in out
