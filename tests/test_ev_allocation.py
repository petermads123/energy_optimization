"""Tests for energy_optimization.ev.allocation (T4, T7, T13, T14): sharing and limits."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from energy_optimization.ev.allocation import (
    Claim,
    measurements_fresh,
    phase_capacity,
    present_draw,
    reserved_current,
    share,
)
from energy_optimization.ev.allocation import main as showcase
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


def claim(
    charger_id: str,
    low: float = 6,
    high: float = 16,
    phases: frozenset[Phase] = L1,
) -> Claim:
    return Claim(charger_id, phases, low, high)


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


# --- share (T4) ----------------------------------------------------------------


@pytest.mark.parametrize(
    "reverse", [False, True], ids=["three-phase first", "single first"]
)
def test_share_mixed_phases_is_max_min_fair(reverse: bool) -> None:
    claims = [claim("t", 6, 16, ALL), claim("s", 6, 10, L1)]
    if reverse:
        claims.reverse()

    assert share(claims, CAP25) == {"t": 15.0, "s": 10.0}


def test_share_unequal_minimums_level_up_from_the_lowest_allocation() -> None:
    claims = [claim("a", 10, 16), claim("b", 6, 16)]

    assert share(claims, caps(l1=24)) == {"a": 12.0, "b": 12.0}


@pytest.mark.parametrize(
    ("capacity", "expected"),
    [
        (14, (7.0, 7.0, 0.0)),
        (13, (7.0, 6.0, 0.0)),
        (13.5, (7.0, 6.0, 0.0)),
        (18, (6.0, 6.0, 6.0)),
    ],
)
def test_share_gives_zero_or_min_in_ranking_order(
    capacity: float, expected: tuple[float, float, float]
) -> None:
    claims = [claim("a"), claim("b"), claim("c")]

    result = share(claims, caps(l1=capacity))

    assert (result["a"], result["b"], result["c"]) == expected


def test_share_remainder_follows_ranking_order_not_id_order() -> None:
    claims = [claim("y"), claim("x")]

    assert share(claims, caps(l1=13)) == {"y": 7.0, "x": 6.0}


def test_share_an_unfit_higher_ranked_claim_does_not_block_a_lower_one() -> None:
    claims = [claim("a", 10, 16), claim("b", 6, 16)]

    assert share(claims, caps(l1=8)) == {"a": 0.0, "b": 8.0}


def test_share_a_later_claim_on_another_phase_still_fits() -> None:
    claims = [claim("x", phases=L1), claim("y", phases=L2)]

    assert share(claims, caps(l1=5, l2=10)) == {"x": 0.0, "y": 10.0}


def test_share_a_full_phase_does_not_stop_claims_on_other_phases() -> None:
    claims = [claim("A", phases=ALL), claim("B", phases=L2)]

    assert share(claims, caps(l1=10, l2=25, l3=25)) == {"A": 10.0, "B": 15.0}


def test_share_caps_every_claim_at_its_max() -> None:
    claims = [claim("a", 6, 8), claim("b", 6, 16)]

    assert share(claims, caps(l1=30)) == {"a": 8.0, "b": 16.0}


@pytest.mark.parametrize(
    ("capacity", "expected"), [(9, 0.0), (10, 10.0), (15, 10.0), (100, 10.0)]
)
def test_share_all_or_nothing_is_never_partial(
    capacity: float, expected: float
) -> None:
    assert share([claim("o", 10, 10)], caps(l1=capacity)) == {"o": expected}


@pytest.mark.parametrize(
    ("capacity", "expected"),
    [
        (20, {"o": 7.5, "a": 12.0}),
        (13, {"o": 7.5, "a": 0.0}),
        (7.4, {"o": 0.0, "a": 7.0}),
    ],
)
def test_share_all_or_nothing_never_grows_beside_an_adjustable_claim(
    capacity: float, expected: dict[str, float]
) -> None:
    claims = [claim("o", 7.5, 7.5), claim("a", 6, 16)]

    assert share(claims, caps(l1=capacity)) == expected


@pytest.mark.parametrize(
    ("capacity", "expected"),
    [(15.5, 15.0), (15.9999999995, 16.0), (16, 16.0), (100, 16.0), (6.5, 6.0)],
)
def test_share_floors_fractional_capacity_to_whole_amps(
    capacity: float, expected: float
) -> None:
    assert share([claim("a")], caps(l1=capacity)) == {"a": expected}


@pytest.mark.parametrize(
    ("capacity", "expected"),
    [(6.0, 6.0), (6 - 1e-10, 6.0), (5.9999999995, 6.0), (5.99, 0.0), (0.0, 0.0)],
)
def test_share_min_boundary_with_tolerance(capacity: float, expected: float) -> None:
    assert share([claim("a")], caps(l1=capacity)) == {"a": expected}


@pytest.mark.parametrize(
    ("l3", "expected"), [(9.0, 9.0), (5.0, 0.0), (6.0, 6.0), (16.0, 16.0)]
)
def test_share_the_tightest_phase_governs_a_multi_phase_claim(
    l3: float, expected: float
) -> None:
    result = share([claim("t", 6, 16, ALL)], caps(16, 16, l3))

    assert result == {"t": expected}


@pytest.mark.parametrize(
    "capacity",
    [{}, {Phase.L1: 0.0}, {Phase.L1: -3.0}, {Phase.L2: 25.0}],
    ids=["empty", "zero", "negative", "other phase only"],
)
def test_share_gives_nothing_when_the_phase_has_no_room(
    capacity: dict[Phase, float],
) -> None:
    assert share([claim("a")], capacity) == {"a": 0.0}


def test_share_of_no_claims_is_empty() -> None:
    assert share([], CAP25) == {}


def test_share_a_claim_with_max_below_min_gets_zero_and_consumes_nothing() -> None:
    claims = [claim("bad", 10, 8), claim("a")]

    assert share(claims, caps(l1=16)) == {"bad": 0.0, "a": 16.0}


def test_share_returns_every_claim_id_even_when_excluded() -> None:
    claims = [claim("a"), claim("b"), claim("c")]

    assert set(share(claims, caps(l1=6))) == {"a", "b", "c"}


def test_share_does_not_mutate_its_arguments() -> None:
    capacity = caps(l1=20)
    before = dict(capacity)
    claims = [claim("a"), claim("b")]
    claims_before = list(claims)

    share(claims, capacity)

    assert capacity == before
    assert claims == claims_before


def test_share_is_idempotent() -> None:
    claims = [claim("a"), claim("b", phases=ALL)]

    assert share(claims, caps(l1=20)) == share(claims, caps(l1=20))


# --- measurements_fresh (T13) --------------------------------------------------


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


# --- phase_capacity with settling (T11, amended A8) -----------------------------


def settling_snapshot(
    charger: Charger, charger_state: ChargerState, l1: float | None = 20.0
) -> Snapshot:
    currents = PhaseCurrents(NOW, l1, 20.0 if l1 is not None else 0.0, 20.0)
    return snapshot([charger], [charger_state], currents, budget=25.0, threshold=25.0)


def drawing(
    setting: float = 6.0, amps: float | None = 16.0, **kwargs: bool
) -> ChargerState:
    power = None if amps is None else amps * 230.0
    return state("a", setting=setting, power=power, **kwargs)


def test_phase_capacity_settling_counts_the_measured_draw_not_the_lower_setting() -> (
    None
):
    snap = settling_snapshot(adjustable(), drawing())

    assert phase_capacity(snap, True)[Phase.L1] == 11.0
    assert phase_capacity(snap, True, frozenset({"a"}))[Phase.L1] == 21.0


def test_phase_capacity_settling_default_is_no_settling() -> None:
    snap = settling_snapshot(adjustable(), drawing())

    assert phase_capacity(snap, True) == phase_capacity(snap, True, frozenset())


def test_phase_capacity_settling_counts_a_paused_car_that_still_draws() -> None:
    snap = settling_snapshot(adjustable(), drawing(setting=6.0, paused=True))

    assert phase_capacity(snap, True)[Phase.L1] == 5.0
    assert phase_capacity(snap, True, frozenset({"a"}))[Phase.L1] == 21.0


def test_phase_capacity_settling_ignores_a_leftover_reading_without_a_car() -> None:
    snap = settling_snapshot(adjustable(), drawing(car=False))

    assert phase_capacity(snap, True, frozenset({"a"})) == phase_capacity(snap, True)
    assert phase_capacity(snap, True, frozenset({"a"}))[Phase.L1] == 5.0


def test_phase_capacity_settling_without_a_power_reading_uses_the_present_draw() -> (
    None
):
    snap = settling_snapshot(adjustable(), drawing(amps=None))

    assert phase_capacity(snap, True, frozenset({"a"})) == phase_capacity(snap, True)
    assert phase_capacity(snap, True)[Phase.L1] == 11.0


def test_phase_capacity_settling_a_draw_below_the_setting_changes_nothing() -> None:
    snap = settling_snapshot(adjustable(), drawing(setting=16.0, amps=4.0))

    assert phase_capacity(snap, True, frozenset({"a"})) == phase_capacity(snap, True)
    assert phase_capacity(snap, True)[Phase.L1] == 9.0


def test_phase_capacity_settling_counts_the_draw_on_every_phase_of_a_charger() -> None:
    # 3 phases at 230 V: 16 A on each needs 3 x 230 x 16 W.
    snap = settling_snapshot(
        adjustable(ALL), state("a", setting=6.0, power=3 * 230.0 * 16.0)
    )

    plain = phase_capacity(snap, True)
    settled = phase_capacity(snap, True, frozenset({"a"}))

    assert all(plain[p] == 11.0 for p in Phase)
    assert all(settled[p] == 21.0 for p in Phase)


def test_phase_capacity_settling_only_touches_the_chargers_named() -> None:
    chargers = [adjustable(cid="a"), adjustable(cid="b")]
    states = [drawing(), state("b", setting=6.0, power=16.0 * 230.0)]
    currents = PhaseCurrents(NOW, 30.0, 0.0, 0.0)
    snap = snapshot(chargers, states, currents, budget=100.0, threshold=40.0)

    only_a = phase_capacity(snap, True, frozenset({"a"}))[Phase.L1]
    both = phase_capacity(snap, True, frozenset({"a", "b"}))[Phase.L1]

    assert only_a == 40.0 - (30.0 - (16.0 + 6.0))  # a at 16, b at its 6 A setting
    assert both == 40.0 - (30.0 - 32.0)  # both at 16


@pytest.mark.parametrize("settling", [frozenset({"zzz"}), frozenset({""})])
def test_phase_capacity_settling_ignores_unknown_ids(settling: frozenset[str]) -> None:
    snap = settling_snapshot(adjustable(), drawing())

    assert phase_capacity(snap, True, settling) == phase_capacity(snap, True)


def test_phase_capacity_settling_is_ignored_for_stale_measurements() -> None:
    snap = settling_snapshot(adjustable(), drawing())

    assert phase_capacity(snap, False, frozenset({"a"})) == caps()


def test_phase_capacity_settling_is_ignored_for_a_fixed_charger() -> None:
    snap = settling_snapshot(fixed(level=6.0, cid="a"), drawing(setting=6.0, amps=10.0))

    assert phase_capacity(snap, True, frozenset({"a"})) == phase_capacity(snap, True)


def test_phase_capacity_settling_is_ignored_for_an_unavailable_charger() -> None:
    snap = settling_snapshot(adjustable(), drawing(available=False))

    assert phase_capacity(snap, True, frozenset({"a"})) == phase_capacity(snap, True)


def test_phase_capacity_settling_leaves_a_phase_without_a_reading_at_the_budget() -> (
    None
):
    snap = settling_snapshot(adjustable(ALL), drawing(), l1=None)

    result = phase_capacity(snap, True, frozenset({"a"}))

    assert result[Phase.L1] == 25.0


def test_phase_capacity_settling_does_not_change_its_argument() -> None:
    settling = frozenset({"a"})
    snap = settling_snapshot(adjustable(), drawing())

    first = phase_capacity(snap, True, settling)
    second = phase_capacity(snap, True, settling)

    assert first == second
    assert settling == frozenset({"a"})


def test_main_showcase_runs_and_prints(capsys: pytest.CaptureFixture[str]) -> None:
    showcase()

    assert "tight phase" in capsys.readouterr().out
