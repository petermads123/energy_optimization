"""Tests for energy_optimization.interruptible.model (T7, T8): validation of the inputs."""

import math
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone

import pytest

from energy_optimization.common import ConfigError, PriceInterval, PriceSeries
from energy_optimization.interruptible.model import (
    Limits,
    Load,
    LoadSnapshot,
    LoadState,
)
from energy_optimization.interruptible.model import main as showcase

NOW = datetime(2026, 10, 21, 17, 0, tzinfo=UTC)
NAIVE = datetime.fromisoformat("2026-10-21T17:00:00")
PRICES = PriceSeries((PriceInterval(NOW, NOW + timedelta(hours=1), 1.0),), NOW)
ON = LoadState.ON
OFF = LoadState.OFF


def lim(x: int, y: int, z: int, n: int = 0, m: int = 1) -> Limits:
    return Limits(
        max_off_run=x,
        window_off_budget=y,
        window_length=z,
        min_on_after_off=n,
        min_off_run=m,
    )


def field_of(error: pytest.ExceptionInfo[ConfigError]) -> str:
    return error.value.field


# --- LoadState -------------------------------------------------------------------------


def test_load_state_values_are_on_and_off() -> None:
    assert {state.name: state.value for state in LoadState} == {
        "ON": "on",
        "OFF": "off",
    }


# --- Limits ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "field"),
    [
        ((0, 1, 1, 0, 1), "max_off_run"),
        ((1, 0, 1, 0, 1), "window_off_budget"),
        ((1, 1, 0, 0, 1), "window_length"),
        ((1, 1, 1, -1, 1), "min_on_after_off"),
        ((1, 1, 1, 0, 0), "min_off_run"),
        ((-5, 1, 1, 0, 1), "max_off_run"),
    ],
)
def test_limits_rejects_each_value_below_its_range(
    args: tuple[int, int, int, int, int], field: str
) -> None:
    with pytest.raises(ConfigError) as error:
        lim(*args)
    assert field_of(error) == field


def test_limits_range_checks_come_before_cross_checks() -> None:
    # Y=0 would also break M <= Y, but the range error on Y itself is reported.
    with pytest.raises(ConfigError) as error:
        Limits(1, 0, 1)
    assert field_of(error) == "window_off_budget"
    # Z=0 would also break Y <= Z.
    with pytest.raises(ConfigError) as error:
        Limits(1, 1, 0)
    assert field_of(error) == "window_length"


def test_limits_budget_above_window_names_the_budget() -> None:
    with pytest.raises(ConfigError) as error:
        lim(1, 5, 4, 0, 2)  # also M > X: Y > Z is the one reported
    assert field_of(error) == "window_off_budget"
    assert "window_length" in str(error.value)


def test_limits_min_off_above_max_off_names_min_off_run() -> None:
    with pytest.raises(ConfigError, match="max_off_run") as error:
        lim(2, 4, 4, 0, 3)
    assert field_of(error) == "min_off_run"


def test_limits_min_off_above_budget_names_min_off_run() -> None:
    with pytest.raises(ConfigError, match="window_off_budget") as error:
        lim(4, 2, 4, 0, 3)
    assert field_of(error) == "min_off_run"


@pytest.mark.parametrize("args", [(1, 1, 1, 0, 1), (3, 3, 3, 0, 3), (4, 8, 16, 2, 2)])
def test_limits_accepts_the_tightest_valid_values(args: tuple[int, ...]) -> None:
    limits = lim(*args)

    assert (
        limits.max_off_run,
        limits.window_off_budget,
        limits.window_length,
        limits.min_on_after_off,
        limits.min_off_run,
    ) == args


def test_limits_integral_floats_become_ints() -> None:
    limits = Limits(4.0, 8.0, 16.0, 2.0, 2.0)  # type: ignore[arg-type]  # floats accepted as in ev

    for value in (
        limits.max_off_run,
        limits.window_off_budget,
        limits.window_length,
        limits.min_on_after_off,
        limits.min_off_run,
    ):
        assert type(value) is int


@pytest.mark.parametrize("bad", [True, 2.5, math.nan, math.inf, "3", None])
@pytest.mark.parametrize(
    "field",
    [
        "max_off_run",
        "window_off_budget",
        "window_length",
        "min_on_after_off",
        "min_off_run",
    ],
)
def test_limits_rejects_bools_fractions_and_non_numbers(
    field: str, bad: object
) -> None:
    args: dict[str, object] = {
        "max_off_run": 4,
        "window_off_budget": 8,
        "window_length": 16,
        "min_on_after_off": 2,
        "min_off_run": 2,
    }
    args[field] = bad

    with pytest.raises(ConfigError) as error:
        Limits(**args)  # type: ignore[arg-type]  # deliberately malformed
    assert field_of(error) == field


@pytest.mark.parametrize(
    ("args", "needed"),
    [
        ((4, 8, 16, 2, 2), 15),
        ((10, 4, 4, 0, 1), 10),
        ((1, 1, 1, 5, 1), 5),
        ((1, 1, 1, 0, 1), 1),
        ((2, 1, 10, 0, 1), 9),
        ((8, 1, 4, 0, 1), 8),
        ((2, 2, 3, 5, 1), 5),
    ],
)
def test_limits_history_needed_takes_the_largest_of_three(
    args: tuple[int, int, int, int, int], needed: int
) -> None:
    assert lim(*args).history_needed == needed


def test_limits_is_frozen() -> None:
    limits = lim(1, 1, 1)
    with pytest.raises(AttributeError):
        limits.max_off_run = 2  # type: ignore[misc]  # proving frozenness


# --- Load ------------------------------------------------------------------------------


@pytest.mark.parametrize("rebound", [0, 0.0, 0.5, 1, 1.0])
def test_load_accepts_rebound_boundaries_as_floats(rebound: float) -> None:
    load = Load(lim(1, 1, 1), rebound)

    assert load.rebound == rebound
    assert type(load.rebound) is float


@pytest.mark.parametrize(
    "rebound", [-1e-12, 1.0000001, -1, 2, math.nan, math.inf, True, None, "0.5"]
)
def test_load_rejects_rebound_outside_zero_to_one(rebound: object) -> None:
    with pytest.raises(ConfigError) as error:
        Load(lim(1, 1, 1), rebound)  # type: ignore[arg-type]  # deliberately malformed
    assert field_of(error) == "rebound"


@pytest.mark.parametrize("percent", [0, 0.0, 12.5, 1e6])
def test_load_accepts_minimum_saving_percent(percent: float) -> None:
    assert (
        Load(lim(1, 1, 1), 0.5, min_saving_percent=percent).min_saving_percent
        == percent
    )


@pytest.mark.parametrize(
    "percent", [-0.001, -1e-9, math.inf, -math.inf, math.nan, True, "5"]
)
def test_load_rejects_bad_minimum_saving_percent(percent: object) -> None:
    with pytest.raises(ConfigError) as error:
        Load(lim(1, 1, 1), 0.5, min_saving_percent=percent)  # type: ignore[arg-type]  # malformed
    assert field_of(error) == "min_saving_percent"


def test_load_power_kw_defaults_to_none_and_accepts_small_and_int_values() -> None:
    assert Load(lim(1, 1, 1), 0.5).power_kw is None
    assert Load(lim(1, 1, 1), 0.5, power_kw=1e-9).power_kw == 1e-9
    power = Load(lim(1, 1, 1), 0.5, power_kw=2).power_kw
    assert power == 2.0
    assert type(power) is float


@pytest.mark.parametrize("power", [0, 0.0, -1.0, math.nan, math.inf, True, "1"])
def test_load_rejects_bad_power(power: object) -> None:
    with pytest.raises(ConfigError) as error:
        Load(lim(1, 1, 1), 0.5, power_kw=power)  # type: ignore[arg-type]  # deliberately malformed
    assert field_of(error) == "power_kw"


def test_load_rejects_limits_that_are_not_limits() -> None:
    with pytest.raises(ConfigError) as error:
        Load(None, 0.5)  # type: ignore[arg-type]  # deliberately malformed
    assert field_of(error) == "limits"


# --- LoadSnapshot ----------------------------------------------------------------------


def snapshot(**overrides: object) -> LoadSnapshot:
    args: dict[str, object] = {
        "now": NOW,
        "load": Load(lim(2, 2, 4), 0.5),
        "prices": PRICES,
    }
    args.update(overrides)
    return LoadSnapshot(**args)  # type: ignore[arg-type]  # the tests pass malformed values


def test_snapshot_defaults_are_empty_history_and_no_override() -> None:
    result = snapshot()

    assert result.history == ()
    assert result.force_on is False


def test_snapshot_normalises_list_and_generator_history_to_a_tuple() -> None:
    source = [ON, OFF]

    from_list = snapshot(history=source)
    from_generator = snapshot(history=(state for state in source))

    assert from_list.history == (ON, OFF)
    assert from_generator.history == (ON, OFF)
    assert isinstance(from_list.history, tuple)
    assert source == [ON, OFF]


def test_snapshot_rejects_plain_strings_as_history_entries_naming_the_index() -> None:
    with pytest.raises(ConfigError, match="entry 0") as first:
        snapshot(history=("on",))
    with pytest.raises(ConfigError, match="entry 1") as second:
        snapshot(history=(ON, "off"))
    assert field_of(first) == field_of(second) == "history"


def test_snapshot_rejects_a_string_as_history() -> None:
    with pytest.raises(ConfigError) as error:
        snapshot(history="off")
    assert field_of(error) == "history"


@pytest.mark.parametrize("history", [{OFF}, frozenset({ON, OFF}), {0: OFF}, 5, None])
def test_snapshot_rejects_unordered_or_non_iterable_history(history: object) -> None:
    with pytest.raises(ConfigError) as error:
        snapshot(history=history)
    assert field_of(error) == "history"


def test_snapshot_accepts_an_iterator_history() -> None:
    iterator: Iterator[LoadState] = iter([OFF, OFF])

    assert snapshot(history=iterator).history == (OFF, OFF)


@pytest.mark.parametrize("force_on", [1, 0, None, "yes"])
def test_snapshot_force_on_must_be_a_bool(force_on: object) -> None:
    with pytest.raises(ConfigError) as error:
        snapshot(force_on=force_on)
    assert field_of(error) == "force_on"


def test_snapshot_rejects_a_naive_now() -> None:
    with pytest.raises(ConfigError) as error:
        snapshot(now=NAIVE)
    assert field_of(error) == "now"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("load", None),
        ("prices", None),
        ("prices", []),
        ("prices", PRICES.intervals),
        ("load", lim(1, 1, 1)),
    ],
)
def test_snapshot_rejects_wrong_types_for_load_and_prices(
    field: str, value: object
) -> None:
    with pytest.raises(ConfigError) as error:
        snapshot(**{field: value})
    assert field_of(error) == field


def test_snapshot_accepts_an_odd_offset_now() -> None:
    offset = timezone(timedelta(hours=5, minutes=45))

    assert snapshot(
        now=datetime(2026, 10, 21, 22, 52, tzinfo=offset)
    ).now.utcoffset() == timedelta(hours=5, minutes=45)


def test_prices_off_the_quarter_grid_are_refused_where_they_are_built() -> None:
    with pytest.raises(ConfigError) as error:
        PriceInterval(NOW, NOW + timedelta(minutes=10), 1.0)
    assert field_of(error) == "end"


def test_showcase_runs(capsys: pytest.CaptureFixture[str]) -> None:
    showcase()

    out = capsys.readouterr().out
    assert "look-back: 15 quarters" in out
    assert "field=min_off_run" in out
