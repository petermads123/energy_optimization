"""Tests for energy_optimization.ev.model (T1): every invalid input names its field."""

import math
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from energy_optimization.ev.model import (
    Charger,
    ChargerKind,
    ChargerState,
    ConfigError,
    Phase,
    PhaseCurrents,
    PriceInterval,
    PriceSeries,
    Session,
    Settings,
    Snapshot,
)
from energy_optimization.ev.quarters import quarter_prices

CPH = ZoneInfo("Europe/Copenhagen")
NOW = datetime(2026, 10, 21, 18, 0, tzinfo=UTC)
L1 = frozenset({Phase.L1})
ALL = frozenset(Phase)


def naive(text: str) -> datetime:
    """Parse a timestamp without an offset."""
    return datetime.fromisoformat(text)


def adjustable(**kwargs: object) -> Charger:
    values: dict[str, Any] = {
        "id": "carport",
        "phases": L1,
        "kind": ChargerKind.ADJUSTABLE,
        "min_current": 6,
        "max_current": 16,
    }
    values.update(kwargs)
    return Charger(**values)


def on_off(**kwargs: object) -> Charger:
    values: dict[str, Any] = {
        "id": "pump",
        "phases": L1,
        "kind": ChargerKind.ON_OFF,
        "assumed_current": 10.0,
    }
    values.update(kwargs)
    return Charger(**values)


def fixed(**kwargs: object) -> Charger:
    values: dict[str, Any] = {
        "id": "fixed",
        "phases": L1,
        "kind": ChargerKind.FIXED,
        "assumed_current": 10.0,
    }
    values.update(kwargs)
    return Charger(**values)


def state(**kwargs: object) -> ChargerState:
    values: dict[str, Any] = {
        "charger_id": "carport",
        "car_connected": True,
        "current_setting": 0.0,
        "paused": True,
    }
    values.update(kwargs)
    return ChargerState(**values)


def session(**kwargs: object) -> Session:
    values: dict[str, Any] = {
        "charger_id": "carport",
        "ready_time": time(7, 0),
        "energy_need_kwh": 10.0,
    }
    values.update(kwargs)
    return Session(**values)


def settings(**kwargs: object) -> Settings:
    values: dict[str, Any] = {
        "phase_budget": 25.0,
        "phase_threshold": 32.0,
        "fallback_current": 6,
        "timezone": "Europe/Copenhagen",
    }
    values.update(kwargs)
    return Settings(**values)


def interval(start: datetime, minutes: int = 15, price: float = 1.0) -> PriceInterval:
    return PriceInterval(start, start + timedelta(minutes=minutes), price)


def snapshot(**kwargs: object) -> Snapshot:
    values: dict[str, Any] = {
        "now": NOW,
        "settings": settings(),
        "chargers": (adjustable(),),
        "states": (state(),),
        "sessions": (session(),),
        "prices": PriceSeries((), NOW),
    }
    values.update(kwargs)
    return Snapshot(**values)


def field_of(build: Callable[[], object]) -> str:
    """Run a constructor that must fail and return the field the error names."""
    with pytest.raises(ConfigError) as caught:
        build()
    return caught.value.field


# --- ConfigError ---------------------------------------------------------------


def test_config_error_str_is_field_then_message() -> None:
    error = ConfigError("max_current", "charger 'carport': must be ≥ min_current")

    assert str(error) == "max_current: charger 'carport': must be ≥ min_current"
    assert error.field == "max_current"
    assert error.message == "charger 'carport': must be ≥ min_current"
    assert isinstance(error, ValueError)


def test_config_error_for_min_above_max_matches_the_planned_message() -> None:
    with pytest.raises(ConfigError) as caught:
        adjustable(min_current=6, max_current=4)

    assert (
        str(caught.value)
        == "max_current: charger 'carport': must be ≥ min_current (6), got 4"
    )


# --- Charger -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"min_current": 6, "max_current": 4}, "max_current"),
        ({"min_current": 0}, "min_current"),
        ({"min_current": -1}, "min_current"),
        ({"max_current": 0, "min_current": 6}, "max_current"),
        ({"max_current": -1}, "max_current"),
        ({"min_current": None}, "min_current"),
        ({"max_current": None}, "max_current"),
        ({"assumed_current": 10.0}, "assumed_current"),
        ({"min_current": True}, "min_current"),
        ({"min_current": 6.5}, "min_current"),
        ({"max_current": "16"}, "max_current"),
        ({"min_current": math.nan}, "min_current"),
        ({"max_current": math.inf}, "max_current"),
        ({"fallback_current": -1}, "fallback_current"),
        ({"fallback_current": 2.5}, "fallback_current"),
        ({"phases": frozenset()}, "phases"),
        ({"phases": frozenset({"L4"})}, "phases"),
        ({"phases": frozenset({"L1"})}, "phases"),
        ({"phases": "L1"}, "phases"),
        ({"phases": None}, "phases"),
        ({"phases": 1}, "phases"),
        ({"phases": [[Phase.L1]]}, "phases"),
        ({"id": ""}, "id"),
        ({"id": "   "}, "id"),
        ({"id": 5}, "id"),
        ({"kind": "adjustable"}, "kind"),
        ({"kind": None}, "kind"),
    ],
)
def test_adjustable_charger_rejects_invalid_configuration(
    kwargs: dict[str, Any], field: str
) -> None:
    assert field_of(lambda: adjustable(**kwargs)) == field


@pytest.mark.parametrize(
    ("build", "field"),
    [
        (lambda: on_off(min_current=6), "min_current"),
        (lambda: on_off(max_current=16), "max_current"),
        (lambda: on_off(assumed_current=None), "assumed_current"),
        (lambda: on_off(assumed_current=0), "assumed_current"),
        (lambda: on_off(assumed_current=-3.0), "assumed_current"),
        (lambda: on_off(assumed_current=math.nan), "assumed_current"),
        (lambda: on_off(assumed_current=math.inf), "assumed_current"),
        (lambda: on_off(assumed_current=True), "assumed_current"),
        (lambda: on_off(assumed_current="10"), "assumed_current"),
        (lambda: on_off(assumed_current=10**400), "assumed_current"),
        (lambda: on_off(fallback_current=-1), "fallback_current"),
        (lambda: fixed(min_current=6), "min_current"),
        (lambda: fixed(max_current=16), "max_current"),
        (lambda: fixed(assumed_current=None), "assumed_current"),
        (lambda: fixed(assumed_current=0), "assumed_current"),
        (lambda: fixed(fallback_current=0), "fallback_current"),
        (lambda: fixed(fallback_current=6), "fallback_current"),
    ],
)
def test_single_level_charger_rejects_invalid_configuration(
    build: Callable[[], Charger], field: str
) -> None:
    assert field_of(build) == field


def test_charger_rejects_a_duplicate_phase_given_as_a_list() -> None:
    with pytest.raises(ConfigError, match="duplicate phase 'L1'") as caught:
        adjustable(phases=[Phase.L1, Phase.L1])

    assert caught.value.field == "phases"


def test_charger_rejects_a_duplicate_phase_among_others() -> None:
    assert (
        field_of(lambda: adjustable(phases=(Phase.L1, Phase.L2, Phase.L1))) == "phases"
    )


def test_charger_error_names_the_charger_id() -> None:
    with pytest.raises(ConfigError, match="'wallbox'"):
        adjustable(id="wallbox", min_current=0)


def test_charger_accepts_min_equal_to_max() -> None:
    charger = adjustable(min_current=6, max_current=6)

    assert (charger.min_current, charger.max_current) == (6, 6)


def test_charger_stores_whole_valued_floats_as_ints() -> None:
    charger = adjustable(min_current=6.0, max_current=16.0, fallback_current=8.0)

    assert type(charger.min_current) is int
    assert type(charger.max_current) is int
    assert type(charger.fallback_current) is int


def test_charger_accepts_a_zero_fallback_on_adjustable_and_on_off() -> None:
    assert adjustable(fallback_current=0).fallback_current == 0
    assert on_off(fallback_current=0).fallback_current == 0


def test_charger_stores_an_int_assumed_current_as_float() -> None:
    charger = on_off(assumed_current=10)

    assert charger.assumed_current == 10.0
    assert type(charger.assumed_current) is float


def test_charger_phases_from_a_list_equal_phases_from_a_frozenset() -> None:
    from_list = adjustable(phases=[Phase.L1, Phase.L2])
    from_set = adjustable(phases=frozenset({Phase.L2, Phase.L1}))

    assert from_list == from_set
    assert from_list.phases == frozenset({Phase.L1, Phase.L2})
    assert isinstance(from_list.phases, frozenset)


def test_charger_is_frozen() -> None:
    charger = adjustable()

    with pytest.raises(AttributeError):
        charger.id = "other"  # type: ignore[misc]  # proving the dataclass is frozen


def test_charger_accepts_a_non_ascii_id() -> None:
    assert adjustable(id="bilen på carporten").id == "bilen på carporten"


# --- ChargerState --------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"current_setting": -0.1}, "current_setting"),
        ({"current_setting": True}, "current_setting"),
        ({"current_setting": math.nan}, "current_setting"),
        ({"current_setting": math.inf}, "current_setting"),
        ({"current_setting": "6"}, "current_setting"),
        ({"current_setting": None}, "current_setting"),
        ({"measured_power_w": -1.0}, "measured_power_w"),
        ({"measured_power_w": math.nan}, "measured_power_w"),
        ({"measured_power_w": -math.inf}, "measured_power_w"),
        ({"measured_power_w": False}, "measured_power_w"),
        ({"car_connected": 1}, "car_connected"),
        ({"car_connected": None}, "car_connected"),
        ({"paused": None}, "paused"),
        ({"paused": 0}, "paused"),
        ({"available": "yes"}, "available"),
        ({"charger_id": ""}, "charger_id"),
    ],
)
def test_charger_state_rejects_invalid_values(
    kwargs: dict[str, Any], field: str
) -> None:
    assert field_of(lambda: state(**kwargs)) == field


def test_charger_state_accepts_zero_setting_and_missing_power() -> None:
    result = state(current_setting=0, measured_power_w=None)

    assert result.current_setting == 0.0
    assert type(result.current_setting) is float
    assert result.measured_power_w is None


def test_charger_state_accepts_zero_measured_power_as_a_real_reading() -> None:
    assert state(measured_power_w=0).measured_power_w == 0.0


def test_charger_state_defaults_to_available() -> None:
    assert state().available is True


def test_huge_integer_in_a_charger_state_is_a_config_error_not_overflow() -> None:
    assert field_of(lambda: state(current_setting=10**400)) == "current_setting"


# --- Session -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"ready_time": time(7, 0, tzinfo=UTC)}, "ready_time"),
        ({"ready_time": "07:00"}, "ready_time"),
        ({"ready_time": naive("2026-01-01T07:00:00")}, "ready_time"),
        ({"ready_time": None}, "ready_time"),
        ({"soc_percent": -0.1}, "soc_percent"),
        ({"soc_percent": 100.1}, "soc_percent"),
        ({"soc_percent": math.nan}, "soc_percent"),
        ({"target_soc_percent": 0}, "target_soc_percent"),
        ({"target_soc_percent": 100.5}, "target_soc_percent"),
        ({"battery_capacity_kwh": 0}, "battery_capacity_kwh"),
        ({"battery_capacity_kwh": -5}, "battery_capacity_kwh"),
        ({"user_current_limit": -1}, "user_current_limit"),
        ({"user_current_limit": 6.5}, "user_current_limit"),
        ({"user_current_limit": True}, "user_current_limit"),
        ({"delivered_kwh": -1}, "delivered_kwh"),
        ({"delivered_kwh": math.nan}, "delivered_kwh"),
        ({"energy_need_kwh": -0.001}, "energy_need_kwh"),
        ({"energy_need_kwh": math.inf}, "energy_need_kwh"),
        ({"plugged_in_at": naive("2026-10-21T06:00:00")}, "plugged_in_at"),
        ({"plugged_in_at": "2026-10-21T06:00:00Z"}, "plugged_in_at"),
        ({"charge_now": "true"}, "charge_now"),
        ({"charge_now": 0}, "charge_now"),
        ({"smart_charging": None}, "smart_charging"),
        ({"charger_id": "  "}, "charger_id"),
    ],
)
def test_session_rejects_invalid_values(kwargs: dict[str, Any], field: str) -> None:
    assert field_of(lambda: session(**kwargs)) == field


@pytest.mark.parametrize(
    "kwargs",
    [
        {"soc_percent": 0},
        {"soc_percent": 100},
        {"target_soc_percent": 100},
        {"target_soc_percent": 1e-9},
        {"user_current_limit": 0},
        {"energy_need_kwh": 0},
        {"delivered_kwh": 0},
        {"ready_time": time(0, 0)},
        {"ready_time": time(23, 59, 59, 999999)},
    ],
)
def test_session_accepts_documented_boundaries(kwargs: dict[str, Any]) -> None:
    session(**kwargs)


def test_session_accepts_a_ready_time_with_fold_set() -> None:
    assert session(ready_time=time(2, 30, fold=1)).ready_time == time(2, 30)


def test_session_stores_numbers_as_floats_and_the_limit_as_int() -> None:
    result = session(
        energy_need_kwh=10,
        soc_percent=40,
        target_soc_percent=80,
        battery_capacity_kwh=50,
        delivered_kwh=1,
        user_current_limit=10.0,
    )

    assert type(result.energy_need_kwh) is float
    assert type(result.soc_percent) is float
    assert type(result.delivered_kwh) is float
    assert type(result.user_current_limit) is int


def test_session_defaults() -> None:
    result = Session("carport", time(7, 0))

    assert result.energy_need_kwh is None
    assert result.delivered_kwh == 0.0
    assert result.charge_now is False
    assert result.smart_charging is True
    assert result.user_current_limit is None
    assert result.plugged_in_at is None


# --- PriceInterval -------------------------------------------------------------


def test_price_interval_accepts_an_interval_across_the_fall_back_fold() -> None:
    start = datetime(2026, 10, 25, 2, 30, tzinfo=CPH)  # 02:30 CEST = 00:30Z
    end = datetime(2026, 10, 25, 2, 15, fold=1, tzinfo=CPH)  # 02:15 CET = 01:15Z

    result = PriceInterval(start, end, 1.0)

    assert result.end.astimezone(UTC) - result.start.astimezone(UTC) == timedelta(
        minutes=45
    )


def test_price_interval_across_the_whole_fold_hour_yields_four_quarters() -> None:
    start = datetime(2026, 10, 25, 2, 0, tzinfo=CPH)
    end = datetime(2026, 10, 25, 2, 0, fold=1, tzinfo=CPH)

    quarters = quarter_prices(PriceSeries((PriceInterval(start, end, 0.5),), NOW))

    assert sorted(quarters) == [
        datetime(2026, 10, 25, 0, minute, tzinfo=UTC) for minute in (0, 15, 30, 45)
    ]


def test_price_interval_rejects_a_reversed_interval_across_the_fold() -> None:
    start = datetime(2026, 10, 25, 2, 30, fold=1, tzinfo=CPH)  # 01:30Z
    end = datetime(2026, 10, 25, 2, 45, tzinfo=CPH)  # 00:45Z, later wall clock

    assert field_of(lambda: PriceInterval(start, end, 1.0)) == "end"


@pytest.mark.parametrize(
    ("start", "end", "field"),
    [
        (
            datetime(2026, 1, 1, 20, 7, tzinfo=UTC),
            datetime(2026, 1, 1, 20, 15, tzinfo=UTC),
            "start",
        ),
        (
            datetime(2026, 1, 1, 20, 0, 30, tzinfo=UTC),
            datetime(2026, 1, 1, 20, 15, tzinfo=UTC),
            "start",
        ),
        (
            datetime(2026, 1, 1, 20, 0, 0, 1, tzinfo=UTC),
            datetime(2026, 1, 1, 20, 15, tzinfo=UTC),
            "start",
        ),
        (
            datetime(2026, 1, 1, 20, 0, tzinfo=UTC),
            datetime(2026, 1, 1, 20, 22, tzinfo=UTC),
            "end",
        ),
        (
            datetime(2026, 1, 1, 20, 0, tzinfo=UTC),
            datetime(2026, 1, 1, 20, 0, tzinfo=UTC),
            "end",
        ),
        (
            datetime(2026, 1, 1, 20, 15, tzinfo=UTC),
            datetime(2026, 1, 1, 20, 0, tzinfo=UTC),
            "end",
        ),
        (
            naive("2026-01-01T20:00:00"),
            datetime(2026, 1, 1, 20, 15, tzinfo=UTC),
            "start",
        ),
        (datetime(2026, 1, 1, 20, 0, tzinfo=UTC), naive("2026-01-01T20:15:00"), "end"),
    ],
)
def test_price_interval_rejects_misaligned_reversed_empty_and_naive_bounds(
    start: datetime, end: datetime, field: str
) -> None:
    assert field_of(lambda: PriceInterval(start, end, 1.0)) == field


def test_price_interval_alignment_is_judged_in_utc_not_in_local_time() -> None:
    odd = timezone(timedelta(minutes=10))
    aligned_in_utc = datetime(2026, 1, 1, 10, 10, tzinfo=odd)  # 10:00Z
    aligned_locally = datetime(2026, 1, 1, 10, 0, tzinfo=odd)  # 09:50Z

    PriceInterval(aligned_in_utc, aligned_in_utc + timedelta(minutes=15), 1.0)
    assert (
        field_of(
            lambda: PriceInterval(
                aligned_locally, aligned_locally + timedelta(minutes=15), 1.0
            )
        )
        == "start"
    )


def test_price_interval_accepts_a_negative_price_and_stores_an_int_as_float() -> None:
    start = datetime(2026, 1, 1, 20, 0, tzinfo=UTC)

    negative = interval(start, price=-0.5)
    whole = interval(start, price=2)  # type: ignore[arg-type]  # ints are accepted

    assert negative.price == -0.5
    assert type(whole.price) is float


@pytest.mark.parametrize("price", [math.nan, math.inf, -math.inf, True, "1", None])
def test_price_interval_rejects_a_bad_price(price: object) -> None:
    start = datetime(2026, 1, 1, 20, 0, tzinfo=UTC)

    assert field_of(lambda: interval(start, price=price)) == "price"  # type: ignore[arg-type]  # bad on purpose


# --- PriceSeries ---------------------------------------------------------------


def test_price_series_accepts_touching_unordered_intervals() -> None:
    first = interval(datetime(2026, 1, 1, 0, 0, tzinfo=UTC), 60)
    second = interval(datetime(2026, 1, 1, 1, 0, tzinfo=UTC), 60)

    series = PriceSeries([second, first], NOW)  # type: ignore[arg-type]  # a list is accepted

    assert series.intervals == (second, first)
    assert isinstance(series.intervals, tuple)


def test_price_series_accepts_no_intervals() -> None:
    assert PriceSeries((), NOW).intervals == ()


@pytest.mark.parametrize(
    "intervals",
    [
        [  # starts inside the other
            interval(datetime(2026, 1, 1, 0, 0, tzinfo=UTC), 60),
            interval(datetime(2026, 1, 1, 0, 45, tzinfo=UTC), 30),
        ],
        [  # given in reverse order
            interval(datetime(2026, 1, 1, 0, 45, tzinfo=UTC), 30),
            interval(datetime(2026, 1, 1, 0, 0, tzinfo=UTC), 60),
        ],
        [  # identical
            interval(datetime(2026, 1, 1, 0, 0, tzinfo=UTC), 60),
            interval(datetime(2026, 1, 1, 0, 0, tzinfo=UTC), 60),
        ],
        [  # one inside the other, in different offsets
            interval(datetime(2026, 1, 1, 0, 0, tzinfo=UTC), 60),
            interval(
                datetime(2026, 1, 1, 2, 30, tzinfo=timezone(timedelta(hours=2))), 15
            ),
        ],
    ],
)
def test_price_series_rejects_overlapping_intervals(
    intervals: list[PriceInterval],
) -> None:
    assert field_of(lambda: PriceSeries(tuple(intervals), NOW)) == "intervals"


@pytest.mark.parametrize("intervals", [None, 5, "abc", object()])
def test_price_series_rejects_a_non_sequence_of_intervals(intervals: object) -> None:
    assert field_of(lambda: PriceSeries(intervals, NOW)) == "intervals"  # type: ignore[arg-type]  # bad on purpose


def test_price_series_rejects_a_member_that_is_not_an_interval() -> None:
    assert field_of(lambda: PriceSeries((object(),), NOW)) == "intervals"  # type: ignore[arg-type]  # bad on purpose


def test_price_series_rejects_a_naive_update_time() -> None:
    assert (
        field_of(lambda: PriceSeries((), naive("2026-01-01T00:00:00"))) == "updated_at"
    )


# --- PhaseCurrents -------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"measured_at": naive("2026-01-01T00:00:00")}, "measured_at"),
        ({"l1": -0.1}, "l1"),
        ({"l2": True}, "l2"),
        ({"l3": math.nan}, "l3"),
        ({"l1": "5"}, "l1"),
    ],
)
def test_phase_currents_reject_invalid_values(
    kwargs: dict[str, Any], field: str
) -> None:
    values: dict[str, Any] = {"measured_at": NOW, "l1": 1.0, "l2": 2.0, "l3": 3.0}
    values.update(kwargs)

    assert field_of(lambda: PhaseCurrents(**values)) == field


def test_phase_currents_get_returns_none_for_a_missing_phase() -> None:
    currents = PhaseCurrents(NOW, 1.0, None, 3)

    assert currents.get(Phase.L1) == 1.0
    assert currents.get(Phase.L2) is None
    assert currents.get(Phase.L3) == 3.0


def test_phase_currents_accept_zero_as_a_real_reading() -> None:
    assert PhaseCurrents(NOW, 0, 0, 0).get(Phase.L2) == 0.0


# --- Settings ------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["phase_budget", "phase_threshold", "fallback_current", "timezone"]
)
def test_settings_require_budget_threshold_fallback_and_timezone(name: str) -> None:
    assert field_of(lambda: settings(**{name: None})) == name


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"phase_budget": 0}, "phase_budget"),
        ({"phase_budget": -1}, "phase_budget"),
        ({"phase_budget": True}, "phase_budget"),
        ({"phase_budget": math.nan}, "phase_budget"),
        ({"phase_budget": 10**400}, "phase_budget"),
        ({"phase_threshold": 0}, "phase_threshold"),
        ({"phase_threshold": math.inf}, "phase_threshold"),
        ({"voltage": 0}, "voltage"),
        ({"voltage": math.inf}, "voltage"),
        ({"fallback_current": -1}, "fallback_current"),
        ({"fallback_current": 6.5}, "fallback_current"),
        ({"fallback_current": True}, "fallback_current"),
        ({"timezone": "Mars/Olympus"}, "timezone"),
        ({"timezone": ""}, "timezone"),
        ({"timezone": " Europe/Copenhagen"}, "timezone"),
        ({"timezone": "/etc/passwd"}, "timezone"),
        ({"timezone": "../../etc/passwd"}, "timezone"),
        ({"timezone": 5}, "timezone"),
        ({"min_block_quarters": 0}, "min_block_quarters"),
        ({"min_block_quarters": True}, "min_block_quarters"),
        ({"min_block_quarters": 1.5}, "min_block_quarters"),
        ({"price_cap": math.nan}, "price_cap"),
        ({"price_cap": math.inf}, "price_cap"),
        ({"default_energy_need_kwh": -1}, "default_energy_need_kwh"),
        ({"max_price_age": timedelta(0)}, "max_price_age"),
        ({"max_price_age": 60}, "max_price_age"),
        ({"max_measurement_age": timedelta(seconds=-1)}, "max_measurement_age"),
    ],
)
def test_settings_reject_invalid_values(kwargs: dict[str, Any], field: str) -> None:
    assert field_of(lambda: settings(**kwargs)) == field


def test_settings_accept_zero_fallback_single_quarter_blocks_and_a_negative_cap() -> (
    None
):
    result = settings(fallback_current=0, min_block_quarters=1, price_cap=-0.1)

    assert result.fallback_current == 0
    assert result.min_block_quarters == 1
    assert result.price_cap == -0.1


def test_settings_accept_the_smallest_positive_age_and_a_zero_default_need() -> None:
    result = settings(
        max_price_age=timedelta(microseconds=1), default_energy_need_kwh=0
    )

    assert result.max_price_age == timedelta(microseconds=1)
    assert result.default_energy_need_kwh == 0.0


def test_settings_defaults_match_the_concept() -> None:
    result = settings()

    assert result.voltage == 230.0
    assert result.min_block_quarters == 2
    assert result.price_cap is None
    assert result.default_energy_need_kwh == 30.0
    assert result.max_price_age == timedelta(minutes=60)
    assert result.max_measurement_age == timedelta(seconds=60)


def test_settings_unknown_timezone_error_is_a_config_error_not_a_lookup_error() -> None:
    with pytest.raises(ConfigError) as caught:
        settings(timezone="Mars/Olympus")

    assert "Mars/Olympus" in str(caught.value)


# --- Snapshot ------------------------------------------------------------------


def two_chargers() -> dict[str, Any]:
    return {
        "chargers": (adjustable(), on_off()),
        "states": (state(), state(charger_id="pump")),
        "sessions": (),
    }


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"chargers": (adjustable(), adjustable())}, "chargers"),
        ({"states": ()}, "states"),
        ({"states": (state(), state())}, "states"),
        ({"states": (state(), state(charger_id="ghost"))}, "states"),
        ({"sessions": (session(), session())}, "sessions"),
        ({"sessions": (session(charger_id="ghost"),)}, "sessions"),
        ({"now": naive("2026-10-21T18:00:00")}, "now"),
        ({"settings": {}}, "settings"),
        ({"prices": None}, "prices"),
        ({"phase_currents": "x"}, "phase_currents"),
        ({"chargers": adjustable()}, "chargers"),
        ({"chargers": "carport"}, "chargers"),
        ({"chargers": (state(),)}, "chargers"),
        ({"sessions": (adjustable(),)}, "sessions"),
    ],
)
def test_snapshot_rejects_bad_links_and_types(
    kwargs: dict[str, Any], field: str
) -> None:
    assert field_of(lambda: snapshot(**kwargs)) == field


def test_snapshot_error_for_an_unknown_charger_names_it() -> None:
    with pytest.raises(ConfigError, match="'ghost'"):
        snapshot(sessions=(session(charger_id="ghost"),))


def test_snapshot_error_for_a_missing_state_names_the_charger() -> None:
    with pytest.raises(ConfigError, match="'pump'"):
        snapshot(**{**two_chargers(), "states": (state(),)})


def test_snapshot_accepts_a_session_on_a_fixed_charger() -> None:
    result = snapshot(
        chargers=(fixed(),),
        states=(state(charger_id="fixed"),),
        sessions=(session(charger_id="fixed"),),
    )

    assert result.sessions[0].charger_id == "fixed"


def test_snapshot_accepts_an_empty_installation() -> None:
    result = snapshot(chargers=(), states=(), sessions=())

    assert (result.chargers, result.states, result.sessions) == ((), (), ())


def test_snapshot_normalises_lists_and_generators_to_tuples() -> None:
    def generate() -> Iterator[Charger]:
        yield adjustable()

    from_lists = snapshot(
        chargers=[adjustable()], states=[state()], sessions=[session()]
    )
    from_generator = snapshot(chargers=generate())

    assert isinstance(from_lists.chargers, tuple)
    assert isinstance(from_lists.states, tuple)
    assert isinstance(from_lists.sessions, tuple)
    assert from_lists == snapshot()
    assert from_generator.chargers == from_generator.chargers == (adjustable(),)


def test_snapshot_accepts_missing_phase_currents() -> None:
    assert snapshot().phase_currents is None
    assert snapshot(phase_currents=PhaseCurrents(NOW, 1.0, None, None)).phase_currents
