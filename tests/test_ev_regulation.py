"""Tests for energy_optimization.ev.regulation (T6, T8): under-use, ramp and probe."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from energy_optimization.ev import (
    Charger,
    ChargerKind,
    ChargerMemory,
    ChargerState,
    Phase,
    Reduction,
    Settings,
)
from energy_optimization.ev.regulation import Regulation, regulate
from energy_optimization.ev.regulation import main as showcase

NOW = datetime(2026, 10, 21, 20, 0, tzinfo=UTC)
CPH = ZoneInfo("Europe/Copenhagen")
MIN = timedelta(minutes=1)
SECOND = timedelta(seconds=1)
MICRO = timedelta(microseconds=1)
CHARGER = Charger("c", frozenset({Phase.L1}), ChargerKind.ADJUSTABLE, 6, 16)


def cfg(**kwargs: object) -> Settings:
    return Settings(25.0, 32.0, 6, "Europe/Copenhagen", **kwargs)  # type: ignore[arg-type]  # test passes through


def st(
    setting: float,
    amps: float | None,
    *,
    paused: bool = False,
    car: bool = True,
) -> ChargerState:
    power = None if amps is None else amps * 230.0
    return ChargerState("c", car, setting, paused, measured_power_w=power)


def cm(**kwargs: object) -> ChargerMemory:
    return ChargerMemory("c", car_connected=True, **kwargs)  # type: ignore[arg-type]  # test passes through


def step(
    previous: ChargerMemory | None,
    state: ChargerState,
    *,
    now: datetime = NOW,
    reset: bool = False,
    settings: Settings | None = None,
    charger: Charger = CHARGER,
) -> Regulation:
    return regulate(previous, charger, state, reset, now, settings or cfg())


def carry(regulation: Regulation) -> ChargerMemory:
    return cm(
        regulated_cap=regulation.regulated_cap,
        under_use_since=regulation.under_use_since,
        full_use_since=regulation.full_use_since,
        under_use_stop_at=regulation.under_use_stop_at,
    )


# --- the concept's own scenarios (doc scenarios 8-11, 16) ----------------------


def test_regulate_cuts_to_the_draw_plus_the_margin_after_a_whole_period() -> None:
    previous = cm(under_use_since=NOW - 15 * MIN)

    result = step(previous, st(16, 7))

    assert result == Regulation(8, None, None, None, True)


def test_regulate_a_cut_below_the_minimum_stops_the_charger() -> None:
    previous = cm(under_use_since=NOW - 15 * MIN)

    result = step(previous, st(10, 2))

    assert result == Regulation(0, None, None, NOW, True)


def test_regulate_inside_the_deadband_changes_nothing() -> None:
    previous = cm(under_use_since=NOW - 15 * MIN)

    result = step(previous, st(16, 14.5))

    assert result == Regulation(None, None, None, None, False)


def test_regulate_a_whole_ramp_period_of_full_use_raises_the_cap_one_step() -> None:
    previous = cm(regulated_cap=10, full_use_since=NOW - 5 * MIN)

    result = step(previous, st(10, 9.5))

    assert result == Regulation(12, None, NOW, None, False)


def test_regulate_probe_offers_current_again_after_the_probe_time() -> None:
    previous = cm(regulated_cap=0, under_use_stop_at=NOW - 60 * MIN)

    result = step(previous, st(16, 16))

    assert result == Regulation(None, None, NOW, None, False)


def test_regulate_probe_boundary() -> None:
    stop = NOW - 60 * MIN + MICRO

    result = step(cm(regulated_cap=0, under_use_stop_at=stop), st(16, 16))

    assert result == Regulation(0, None, None, stop, False)


def test_regulate_probe_releases_a_paused_charger() -> None:
    previous = cm(regulated_cap=0, under_use_stop_at=NOW - 61 * MIN)

    result = step(previous, st(0, 0.0, paused=True))

    assert result == Regulation(None, None, None, None, False)


@pytest.mark.parametrize(
    "state", [st(16, 16), st(0, 0.0, paused=True), st(6, None), st(16, 16, car=False)]
)
def test_regulate_a_stopped_charger_is_not_re_evaluated_before_the_probe(
    state: ChargerState,
) -> None:
    stop = NOW - 10 * MIN
    previous = cm(
        regulated_cap=0,
        under_use_stop_at=stop,
        under_use_since=NOW - 20 * MIN,
        full_use_since=NOW - 20 * MIN,
    )

    result = step(previous, state)

    assert result == Regulation(0, None, None, stop, False)


# --- boundaries of the periods and the zones -----------------------------------


def test_regulate_under_use_period_boundary() -> None:
    on_time = step(cm(under_use_since=NOW - 15 * MIN), st(16, 7))
    early_since = NOW - 15 * MIN + MICRO

    early = step(cm(under_use_since=early_since), st(16, 7))

    assert on_time.cut and on_time.regulated_cap == 8
    assert early == Regulation(None, early_since, None, None, False)


def test_regulate_ramp_period_boundary() -> None:
    on_time = step(cm(regulated_cap=10, full_use_since=NOW - 5 * MIN), st(10, 10))
    since = NOW - 5 * MIN + SECOND

    early = step(cm(regulated_cap=10, full_use_since=since), st(10, 10))

    assert on_time.regulated_cap == 12
    assert early == Regulation(10, None, since, None, False)


def test_regulate_the_deadband_edge_is_the_zone_between_and_drops_progress() -> None:
    since = NOW - 10 * MIN

    on_edge = step(cm(under_use_since=since), st(16, 14.0))
    just_under = step(cm(under_use_since=since), st(16, 13.99))

    assert on_edge == Regulation(None, None, None, None, False)
    assert just_under == Regulation(None, since, None, None, False)


def test_regulate_full_use_edge_is_the_setting_minus_half_the_margin() -> None:
    on_edge = step(None, st(16, 15.5))
    below = step(None, st(16, 15.49))
    above = step(None, st(16, 18))

    assert on_edge.full_use_since == NOW
    assert below.full_use_since is None
    assert above.full_use_since == NOW


def test_regulate_the_draw_just_under_the_cap_is_between_and_clears_timers() -> None:
    previous = cm(regulated_cap=10, full_use_since=NOW - 4 * MIN)

    result = step(previous, st(10, 9.49))

    assert result == Regulation(10, None, None, None, False)


@pytest.mark.parametrize(
    ("amps", "cap", "stop"),
    [(5.0, 6, None), (4.0, 0, NOW), (0.0, 0, NOW), (4.999, 6, None)],
)
def test_regulate_cut_at_the_minimum(
    amps: float, cap: int, stop: datetime | None
) -> None:
    previous = cm(under_use_since=NOW - 15 * MIN)

    result = step(previous, st(16, amps))

    assert result.regulated_cap == cap
    assert result.under_use_stop_at == stop
    assert result.cut


@pytest.mark.parametrize(("setting", "cap"), [(13, 6), (12.9, 6), (11, 0), (16, 8)])
def test_regulate_halving_cuts_to_half_the_setting(setting: float, cap: int) -> None:
    settings = cfg(reduction=Reduction.HALVE)
    previous = cm(under_use_since=NOW - 15 * MIN)

    result = step(previous, st(setting, 2), settings=settings)

    assert result.regulated_cap == cap
    assert result.cut


def test_regulate_uses_the_phase_count_for_the_draw() -> None:
    three = Charger("c", frozenset(Phase), ChargerKind.ADJUSTABLE, 6, 16)
    state = ChargerState("c", True, 16, False, measured_power_w=3 * 230.0 * 7)

    result = step(cm(under_use_since=NOW - 15 * MIN), state, charger=three)

    assert result.regulated_cap == 8


# --- multi-call behaviour -------------------------------------------------------


def test_regulate_a_steady_draw_after_a_to_use_cut_is_neither_ramped_nor_cut_again() -> (
    None
):
    previous = cm(regulated_cap=8)

    for call in range(1, 5):
        result = step(previous, st(8, 7), now=NOW + 15 * MIN * call)

        assert result == Regulation(8, None, None, None, False)
        previous = carry(result)


def test_regulate_a_steady_draw_after_a_halving_cut_is_neither_ramped_nor_cut_again() -> (
    None
):
    settings = cfg(reduction=Reduction.HALVE)
    previous = cm(under_use_since=NOW)

    cut = step(previous, st(16, 7), now=NOW + 15 * MIN, settings=settings)
    assert cut == Regulation(8, None, None, None, True)
    previous = carry(cut)
    for call in range(2, 6):
        result = step(previous, st(8, 7), now=NOW + 15 * MIN * call, settings=settings)

        assert result == Regulation(8, None, None, None, False)
        previous = carry(result)


def test_regulate_full_use_resets_the_under_use_timer() -> None:
    first = step(cm(under_use_since=NOW - 14 * MIN), st(16, 16))
    later = NOW + MIN

    second = step(carry(first), st(16, 7), now=later)

    assert first == Regulation(None, None, NOW, None, False)
    assert second == Regulation(None, later, None, None, False)


def test_regulate_climbs_back_to_the_maximum_and_then_drops_the_cap() -> None:
    previous = cm(regulated_cap=10, full_use_since=NOW)
    caps = []

    for call in range(1, 5):
        now = NOW + 5 * MIN * call
        current = previous.regulated_cap or 16
        result = step(previous, st(current, current - 0.4), now=now)
        caps.append(result.regulated_cap)
        previous = carry(result)

    assert caps == [12, 14, None, None]


def test_regulate_ramp_clears_the_cap_at_the_maximum() -> None:
    at_top = step(cm(regulated_cap=14, full_use_since=NOW - 5 * MIN), st(14, 14))
    below = step(cm(regulated_cap=13, full_use_since=NOW - 5 * MIN), st(13, 13))
    huge = step(
        cm(regulated_cap=10, full_use_since=NOW - 5 * MIN),
        st(10, 10),
        settings=cfg(ramp_step=1000),
    )

    assert at_top.regulated_cap is None
    assert below == Regulation(15, None, NOW, None, False)
    assert huge.regulated_cap is None


def test_regulate_a_held_setting_does_not_let_the_cap_climb_or_fall() -> None:
    previous = cm(regulated_cap=10, full_use_since=NOW - 5 * MIN)

    result = step(previous, st(6, 6))

    assert result == Regulation(10, None, NOW, None, False)


@pytest.mark.parametrize("setting", [0.0, 1.0, 3.0, 5.5])
def test_regulate_the_ramp_never_leaves_a_cap_between_zero_and_the_minimum(
    setting: float,
) -> None:
    previous = cm(regulated_cap=10, full_use_since=NOW - 5 * MIN)

    result = step(previous, st(setting, setting))

    assert result.regulated_cap == 10


def test_regulate_ramp_needs_a_cap_and_keeps_the_timer_without_one() -> None:
    since = NOW - 60 * MIN

    result = step(cm(full_use_since=since), st(16, 16))

    assert result == Regulation(None, None, since, None, False)


# --- bad or inconsistent memory --------------------------------------------------


def test_regulate_a_cap_of_zero_without_a_stop_time_is_probed_from_now() -> None:
    first = step(cm(regulated_cap=0), st(16, 16))
    later = NOW + 60 * MIN

    second = step(carry(first), st(16, 16), now=later)

    assert first == Regulation(0, None, None, NOW, False)
    assert second == Regulation(None, None, later, None, False)


@pytest.mark.parametrize("cap", [1, 3, 5])
def test_regulate_a_cap_between_zero_and_the_minimum_counts_as_a_stop(cap: int) -> None:
    result = step(cm(regulated_cap=cap), st(16, 16))

    assert result == Regulation(0, None, None, NOW, False)


def test_regulate_a_stop_time_without_a_zero_cap_is_forgotten() -> None:
    result = step(cm(regulated_cap=8, under_use_stop_at=NOW - 5 * MIN), st(8, 7))

    assert result == Regulation(8, None, None, None, False)


def test_regulate_a_cut_never_raises_an_existing_cap() -> None:
    previous = cm(regulated_cap=8, under_use_since=NOW - 15 * MIN)

    result = step(previous, st(16, 10))

    assert result == Regulation(8, None, None, None, False)


def test_regulate_a_cut_with_a_deadband_below_the_margin_does_not_raise_the_cap() -> (
    None
):
    settings = cfg(deadband=0.0, under_use_margin=1.0)
    previous = cm(under_use_since=NOW - 15 * MIN)

    result = step(previous, st(16, 15.5), settings=settings)

    assert result == Regulation(None, None, None, None, False)


def test_regulate_a_cut_that_lowers_only_a_little_still_counts() -> None:
    settings = cfg(deadband=0.0, under_use_margin=1.0)
    previous = cm(under_use_since=NOW - 15 * MIN)

    result = step(previous, st(16, 13.5), settings=settings)

    assert result == Regulation(15, None, None, None, True)


# --- reset, kinds and missing data ------------------------------------------------


def test_regulate_reset_clears_a_stop_and_every_timer() -> None:
    previous = cm(
        regulated_cap=0,
        under_use_stop_at=NOW - MIN,
        under_use_since=NOW - 20 * MIN,
        full_use_since=NOW - 20 * MIN,
    )

    result = step(previous, st(16, 16), reset=True)

    assert result == Regulation(None, None, NOW, None, False)


@pytest.mark.parametrize("kind", [ChargerKind.ON_OFF, ChargerKind.FIXED])
def test_regulate_never_regulates_an_on_off_or_fixed_charger(kind: ChargerKind) -> None:
    charger = Charger("c", frozenset({Phase.L1}), kind, assumed_current=10.0)
    previous = cm(regulated_cap=8, under_use_since=NOW - 20 * MIN)

    result = step(previous, st(10, 1), charger=charger)

    assert result == Regulation(None, None, None, None, False)


@pytest.mark.parametrize(
    "state", [st(16, None), st(0, 0.0, paused=True), st(16, 16, car=False)]
)
def test_regulate_without_power_or_while_paused_keeps_the_cap_and_clears_timers(
    state: ChargerState,
) -> None:
    previous = cm(
        regulated_cap=8, under_use_since=NOW - 20 * MIN, full_use_since=NOW - 20 * MIN
    )

    result = step(previous, state)

    assert result == Regulation(8, None, None, None, False)


def test_regulate_without_any_memory_starts_the_timers_now() -> None:
    under = step(None, st(16, 7))
    full = step(None, st(16, 16))

    assert under == Regulation(None, NOW, None, None, False)
    assert full == Regulation(None, None, NOW, None, False)


# --- time handling ----------------------------------------------------------------


def test_regulate_compares_times_given_in_other_offsets_as_instants() -> None:
    since = (NOW - 15 * MIN).astimezone(timezone(timedelta(hours=5)))

    result = step(cm(under_use_since=since), st(16, 7))

    assert result.cut


def test_regulate_measures_periods_in_utc_across_the_fall_back_fold() -> None:
    since = datetime(2026, 10, 25, 2, 30, tzinfo=CPH)  # 00:30 UTC
    now = since.replace(fold=1)  # 01:30 UTC, an hour later

    result = step(cm(under_use_since=since), st(16, 7), now=now)

    assert result.cut


def test_regulate_is_idempotent_and_leaves_its_arguments_alone() -> None:
    previous = cm(under_use_since=NOW - 15 * MIN)
    before = replace(previous)

    first = step(previous, st(16, 7))
    second = step(previous, st(16, 7))

    assert first == second
    assert previous == before


def test_main_showcase_runs_and_prints(capsys: pytest.CaptureFixture[str]) -> None:
    showcase()

    output = capsys.readouterr().out
    assert "cap 8 A" in output
    assert "cap 12 A" in output
