"""Tests for energy_optimization.ev.allocation (T7, T13, T14): limits and draw."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from energy_optimization.ev.allocation import main as showcase
from energy_optimization.ev.allocation import (
    measurements_fresh,
    phase_capacity,
    present_draw,
    reserved_current,
)
from energy_optimization.ev.model import (
    Charger,
    ChargerKind,
    ChargerState,
    Phase,
    PhaseCurrents,
    PriceSeries,
    Settings,
    Snapshot,
)

NOW = datetime(2026, 10, 21, 20, 0, tzinfo=UTC)
L1 = frozenset({Phase.L1})
L2 = frozenset({Phase.L2})
ALL = frozenset(Phase)
CAP25 = {Phase.L1: 25.0, Phase.L2: 25.0, Phase.L3: 25.0}


def caps(l1: float = 25.0, l2: float = 25.0, l3: float = 25.0) -> dict[Phase, float]:
    return {Phase.L1: l1, Phase.L2: l2, Phase.L3: l3}


def adjustable(phases: frozenset[Phase] = L1, cid: str = "a") -> Charger:
    return Charger(cid, phases, ChargerKind.ADJUSTABLE, 6, 16)


def on_off(
    phases: frozenset[Phase] = L1, assumed: float = 10.0, cid: str = "o"
) -> Charger:
    return Charger(cid, phases, ChargerKind.ON_OFF, assumed_current=assumed)


def fixed(
    phases: frozenset[Phase] = L1, level: float = 10.0, cid: str = "f"
) -> Charger:
    return Charger(cid, phases, ChargerKind.FIXED, assumed_current=level)


def state(
    cid: str = "a",
    car: bool = True,
    setting: float = 16.0,
    paused: bool = False,
    power: float | None = None,
    available: bool = True,
) -> ChargerState:
    return ChargerState(cid, car, setting, paused, power, available)


def snapshot(
    chargers: list[Charger],
    states: list[ChargerState],
    currents: PhaseCurrents | None,
    budget: float = 25.0,
    threshold: float = 32.0,
) -> Snapshot:
    return Snapshot(
        NOW,
        Settings(budget, threshold, 6, "Europe/Copenhagen"),
        tuple(chargers),
        tuple(states),
        (),
        PriceSeries((), NOW),
        currents,
    )


def currents(
    measured_at: datetime = NOW,
    l1: float | None = 1.0,
    l2: float | None = 2.0,
    l3: float | None = 3.0,
) -> PhaseCurrents:
    return PhaseCurrents(measured_at, l1, l2, l3)


MINUTE = timedelta(seconds=60)


@pytest.mark.parametrize(
    ("offset", "expected"),
    [
        (timedelta(0), True),
        (-MINUTE, True),
        (MINUTE, True),
        (-MINUTE - timedelta(microseconds=1), False),
        (MINUTE + timedelta(microseconds=1), False),
        (timedelta(hours=1), False),
    ],
)
def test_measurements_fresh_age_boundary_in_both_directions(
    offset: timedelta, expected: bool
) -> None:
    result = measurements_fresh(currents(NOW + offset), NOW, MINUTE, ALL)

    assert result is expected


def test_measurements_fresh_is_false_for_a_missing_measurement() -> None:
    assert measurements_fresh(None, NOW, MINUTE, ALL) is False
    assert measurements_fresh(None, NOW, MINUTE, frozenset()) is False


@pytest.mark.parametrize(
    ("phases", "expected"),
    [
        (L2, False),
        (L1, True),
        (frozenset(), True),
        (frozenset({Phase.L1, Phase.L2}), False),
    ],
)
def test_measurements_fresh_only_requires_a_value_on_the_listed_phases(
    phases: frozenset[Phase], expected: bool
) -> None:
    assert measurements_fresh(currents(l2=None), NOW, MINUTE, phases) is expected


def test_measurements_fresh_counts_zero_as_a_value() -> None:
    assert measurements_fresh(currents(l1=0.0), NOW, MINUTE, L1) is True


def test_measurements_fresh_compares_instants_not_wall_clocks_across_the_fold() -> None:
    zone = ZoneInfo("Europe/Copenhagen")
    measured = datetime(2026, 10, 25, 2, 30, tzinfo=zone)  # 00:30Z
    now = datetime(2026, 10, 25, 2, 30, fold=1, tzinfo=zone)  # 01:30Z

    assert measurements_fresh(currents(measured), now, MINUTE, ALL) is False
    assert measurements_fresh(currents(measured), measured, MINUTE, ALL) is True


def test_measurements_fresh_accepts_the_same_instant_in_another_offset() -> None:
    stamp = datetime.fromisoformat("2026-10-21T22:00:00+02:00")

    assert measurements_fresh(currents(stamp), NOW, MINUTE, ALL) is True


# --- reserved_current (T7) -----------------------------------------------------


def test_reserved_current_of_fixed_ignores_paused_and_car() -> None:
    assert (
        reserved_current(
            fixed(), state("f", car=False, setting=0.0, paused=True), 230.0
        )
        == 10.0
    )


@pytest.mark.parametrize(
    ("charger", "power", "voltage", "expected"),
    [
        (fixed(L1, 10), 4600.0, 230.0, 20.0),
        (fixed(ALL, 10), 6900.0, 230.0, 10.0),
        (fixed(ALL, 10), 13800.0, 230.0, 20.0),
        (fixed(L1, 10), 4600.0, 200.0, 23.0),
        (fixed(L1, 10), 0.0, 230.0, 10.0),
        (fixed(L1, 10), None, 230.0, 10.0),
    ],
)
def test_reserved_current_is_the_higher_of_level_and_measured_draw(
    charger: Charger, power: float | None, voltage: float, expected: float
) -> None:
    assert reserved_current(charger, state("f", power=power), voltage) == pytest.approx(
        expected
    )


@pytest.mark.parametrize(
    ("setting", "power", "expected"),
    [(0.0, None, 0.0), (6.0, 2300.0, 10.0), (10.0, 1150.0, 10.0), (10.0, 3680.0, 16.0)],
)
def test_reserved_current_of_an_unavailable_adjustable_uses_its_setting(
    setting: float, power: float | None, expected: float
) -> None:
    charger_state = state(setting=setting, power=power, available=False, paused=True)

    assert reserved_current(adjustable(), charger_state, 230.0) == pytest.approx(
        expected
    )


def test_reserved_current_of_an_unavailable_on_off_uses_the_assumed_current() -> None:
    charger_state = state("o", setting=0.0, available=False)

    assert reserved_current(on_off(assumed=7.5), charger_state, 230.0) == 7.5


# --- present_draw (T7, T14) ----------------------------------------------------


def test_present_draw_of_a_paused_charger_is_zero_even_with_measured_power() -> None:
    assert present_draw(adjustable(), state(paused=True, power=3680.0), 230.0) == 0.0


def test_present_draw_of_a_charger_without_a_car_is_zero() -> None:
    assert present_draw(adjustable(), state(car=False, setting=16.0), 230.0) == 0.0


@pytest.mark.parametrize(
    ("phases", "power", "expected"),
    [
        (L1, 2300.0, 10.0),
        (ALL, 6900.0, 10.0),
        (ALL, 13800.0, 16.0),
        (ALL, None, 16.0),
        (L1, 0.0, 0.0),
        (L1, 3680.0, 16.0),
    ],
)
def test_present_draw_takes_the_smaller_of_setting_and_measured_per_phase(
    phases: frozenset[Phase], power: float | None, expected: float
) -> None:
    result = present_draw(adjustable(phases), state(setting=16.0, power=power), 230.0)

    assert result == pytest.approx(expected)


def test_present_draw_measured_above_the_setting_does_not_raise_it() -> None:
    assert present_draw(adjustable(), state(setting=10.0, power=3680.0), 230.0) == 10.0


@pytest.mark.parametrize(
    ("power", "expected"), [(None, 10.0), (1150.0, 5.0), (9999.0, 10.0)]
)
def test_present_draw_of_on_off_is_the_smaller_of_assumed_and_measured(
    power: float | None, expected: float
) -> None:
    result = present_draw(on_off(), state("o", setting=0.0, power=power), 230.0)

    assert result == pytest.approx(expected)


def test_present_draw_of_a_paused_on_off_is_zero() -> None:
    assert present_draw(on_off(), state("o", setting=0.0, paused=True), 230.0) == 0.0


def test_present_draw_of_a_fixed_charger_without_power_is_its_reservation() -> None:
    result = present_draw(fixed(), state("f", paused=True, setting=0.0), 230.0)

    assert result == 10.0


def test_present_draw_of_a_fixed_charger_is_its_measured_draw_not_its_level() -> None:
    # The level is held back separately; the guard only counts what is in the reading.
    assert present_draw(fixed(), state("f", power=0.0), 230.0) == 0.0
    assert present_draw(fixed(), state("f", power=4600.0), 230.0) == pytest.approx(20.0)


def test_present_draw_of_an_unavailable_charger_ignores_paused() -> None:
    charger_state = state(setting=16.0, paused=True, available=False)

    assert present_draw(adjustable(), charger_state, 230.0) == 16.0


# --- phase_capacity (T6, T13, T14) ---------------------------------------------


def test_phase_capacity_guard_term_adds_back_what_the_chargers_draw() -> None:
    snap = snapshot(
        [adjustable()], [state(setting=10.0)], PhaseCurrents(NOW, 20.0, 0.0, 0.0)
    )

    assert phase_capacity(snap, True) == {
        Phase.L1: 22.0,
        Phase.L2: 25.0,
        Phase.L3: 25.0,
    }


def test_phase_capacity_is_capped_by_the_budget_when_the_measurement_is_low() -> None:
    snap = snapshot(
        [adjustable()], [state(setting=16.0)], PhaseCurrents(NOW, 5.0, 0.0, 0.0)
    )

    assert phase_capacity(snap, True)[Phase.L1] == 25.0


def test_phase_capacity_may_be_negative() -> None:
    snap = snapshot(
        [adjustable()], [state(paused=True)], PhaseCurrents(NOW, 40.0, 0.0, 0.0)
    )

    assert phase_capacity(snap, True)[Phase.L1] == -8.0


def test_phase_capacity_is_bound_by_a_threshold_below_the_budget() -> None:
    snap = snapshot(
        [adjustable()],
        [state(paused=True)],
        PhaseCurrents(NOW, 0.0, 0.0, 0.0),
        threshold=20.0,
    )

    assert phase_capacity(snap, True) == {
        Phase.L1: 20.0,
        Phase.L2: 20.0,
        Phase.L3: 20.0,
    }


def test_phase_capacity_counts_a_three_phase_draw_on_every_phase() -> None:
    snap = snapshot(
        [adjustable(ALL, "t"), adjustable(L1, "s")],
        [state("t", setting=10.0), state("s", setting=6.0)],
        PhaseCurrents(NOW, 20.0, 12.0, 12.0),
        budget=40.0,
    )

    assert phase_capacity(snap, True) == {
        Phase.L1: 28.0,
        Phase.L2: 30.0,
        Phase.L3: 30.0,
    }


def test_phase_capacity_a_three_phase_charger_counted_on_each_phase() -> None:
    snap = snapshot(
        [adjustable(ALL)], [state(setting=16.0)], PhaseCurrents(NOW, 30.0, 30.0, 30.0)
    )

    assert phase_capacity(snap, True) == {p: 18.0 for p in Phase}


@pytest.mark.parametrize(
    ("fresh", "currents_", "expected"),
    [
        (False, PhaseCurrents(NOW, 40.0, 40.0, 40.0), caps()),
        (True, PhaseCurrents(NOW, 0.0, None, 0.0), caps()),
        (True, None, caps()),
        (False, None, caps()),
    ],
)
def test_phase_capacity_is_the_budget_when_not_fresh_or_a_phase_is_missing(
    fresh: bool, currents_: PhaseCurrents | None, expected: dict[Phase, float]
) -> None:
    snap = snapshot([adjustable()], [state()], currents_)

    assert phase_capacity(snap, fresh) == expected


def test_phase_capacity_a_missing_phase_does_not_hide_the_others() -> None:
    snap = snapshot(
        [adjustable()], [state(paused=True)], PhaseCurrents(NOW, 30.0, None, 0.0)
    )

    result = phase_capacity(snap, True)

    assert result[Phase.L1] == 2.0
    assert result[Phase.L2] == 25.0


def test_phase_capacity_counts_a_fixed_charger_at_its_level_when_it_gives_no_power() -> (
    None
):
    snap = snapshot(
        [fixed()],
        [state("f", setting=10.0)],
        PhaseCurrents(NOW, 20.0, 0.0, 0.0),
        budget=40.0,
    )

    assert phase_capacity(snap, True)[Phase.L1] == 22.0


def test_phase_capacity_does_not_credit_a_fixed_charger_that_is_not_drawing_its_level() -> (
    None
):
    snap = snapshot(
        [fixed()],
        [state("f", setting=10.0, power=0.0)],
        PhaseCurrents(NOW, 20.0, 0.0, 0.0),
        budget=40.0,
    )

    assert phase_capacity(snap, True)[Phase.L1] == 12.0


def test_phase_capacity_excludes_a_paused_charger_and_includes_a_fixed_one() -> None:
    snap = snapshot(
        [fixed(L1, 8.0), adjustable(L1, "p")],
        [state("f", setting=8.0), state("p", paused=True, power=2300.0)],
        PhaseCurrents(NOW, 20.0, 0.0, 0.0),
    )

    assert phase_capacity(snap, True)[Phase.L1] == 20.0


def test_phase_capacity_without_chargers_is_threshold_minus_measured() -> None:
    snap = snapshot([], [], PhaseCurrents(NOW, 10.0, 0.0, 0.0))

    assert phase_capacity(snap, True)[Phase.L1] == 22.0


def test_phase_capacity_does_not_mutate_the_snapshot() -> None:
    snap = snapshot([adjustable()], [state()], PhaseCurrents(NOW, 5.0, 5.0, 5.0))
    before = snapshot([adjustable()], [state()], PhaseCurrents(NOW, 5.0, 5.0, 5.0))

    phase_capacity(snap, True)

    assert snap == before


def test_main_showcase_runs_and_prints(capsys: pytest.CaptureFixture[str]) -> None:
    showcase()

    assert "reserved at" in capsys.readouterr().out
