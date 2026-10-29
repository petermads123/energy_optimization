"""Tests for energy_optimization.interruptible.scheduler (T1-T9, T11-T14, T16).

Notation: a history or plan is written as a string, ``+`` for an on quarter and ``-`` for
an off one, oldest (or first) on the left. ``lim(X, Y, Z, N, M)`` builds the limits.
The checks of T1 and T2 use an oracle written here from the concept's rules, not the
module's helpers.
"""

import copy
import importlib
import itertools
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from types import ModuleType
from typing import cast

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from scipy.optimize import OptimizeResult
from scipy.optimize import milp as real_milp

from energy_optimization.common import PriceInterval, PriceSeries, SolverError
from energy_optimization.interruptible import (
    Limits,
    Load,
    LoadSchedule,
    LoadSnapshot,
    LoadState,
    ReasonCode,
    schedule,
)

# `energy_optimization.interruptible.schedule` is the re-exported function, so the module
# is reached by name; the milp spies patch the module, not the package.
scheduler = importlib.import_module("energy_optimization.interruptible.scheduler")
showcase = scheduler.main

T0 = datetime(2026, 10, 21, 17, 0, tzinfo=UTC)
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


def hist(text: str) -> tuple[LoadState, ...]:
    return tuple(ON if char == "+" else OFF for char in text)


def series(prices: Sequence[float], start: datetime = T0) -> PriceSeries:
    return PriceSeries(
        intervals=tuple(
            PriceInterval(
                start=start + timedelta(minutes=15 * i),
                end=start + timedelta(minutes=15 * (i + 1)),
                price=price,
            )
            for i, price in enumerate(prices)
        ),
        updated_at=start,
    )


def snap(
    prices: Sequence[float],
    limits: Limits,
    rebound: float,
    history: str | Sequence[LoadState] = "",
    percent: float = 0.0,
    power: float | None = None,
    force_on: bool = False,
    now: datetime = T0,
) -> LoadSnapshot:
    states = hist(history) if isinstance(history, str) else tuple(history)
    return LoadSnapshot(
        now=now,
        load=Load(limits, rebound, percent, power),
        prices=series(prices),
        history=states,
        force_on=force_on,
    )


def run(*args: object, **kwargs: object) -> LoadSchedule:
    return schedule(snap(*args, **kwargs))  # type: ignore[arg-type]  # thin test helper


def shape(result: LoadSchedule) -> str:
    return "".join("+" if q.state is ON else "-" for q in result.plan)


# --- the oracle: the concept's rules, enumerated -------------------------------------


def _periods(combined: list[bool]) -> list[tuple[int, int]]:
    """Maximal off runs `(start, end)` of a sequence where True is off."""
    found: list[tuple[int, int]] = []
    index = 0
    while index < len(combined):
        if combined[index]:
            end = index
            while end < len(combined) and combined[end]:
                end += 1
            found.append((index, end))
            index = end
        else:
            index += 1
    return found


def look_back(history: Sequence[LoadState], limits: Limits) -> list[bool]:
    """The history as the core reads it: its look-back, padded with on."""
    needed = limits.history_needed
    seen = [state is OFF for state in history[-needed:]]
    return [False] * (needed - len(seen)) + seen


def trailing_off(past: list[bool]) -> int:
    count = 0
    while count < len(past) and past[-1 - count]:
        count += 1
    return count


def tolerance(prices: Sequence[float]) -> float:
    return 1e-9 * max(1.0, sum(abs(p) for p in prices))


def keeps_limits(
    plan: tuple[bool, ...],
    past: list[bool],
    prices: Sequence[float],
    limits: Limits,
    rebound: float,
    percent: float,
    force_on: bool,
    *,
    strict: bool,
) -> bool:
    """Whether a plan (True is off) breaks nothing; `strict` also demands M for a run."""
    horizon = len(plan)
    past_len = len(past)
    combined = past + list(plan)
    tail = max(limits.min_on_after_off, 1)
    run_now = trailing_off(past)
    if force_on and plan[0]:
        return False
    if strict and 0 < run_now < limits.min_off_run:
        forced = min(limits.min_off_run - run_now, horizon)
        if not all(plan[:forced]):
            return False
    periods = _periods(combined)
    for start, end in periods:
        if end <= past_len:
            continue
        if end - start > limits.max_off_run:
            return False
        in_progress = start < past_len
        end_in_plan = end - past_len
        shortest = max(0, limits.min_off_run - run_now)
        exempt = in_progress and end_in_plan == min(shortest, horizon)
        if end_in_plan + tail > horizon and not exempt:
            return False
        if not in_progress:
            if end - start < limits.min_off_run:
                return False
            a, b = start - past_len, end_in_plan
            span = sum(prices[a : b + tail])
            cost = sum(prices[b : b + tail]) * (1 + rebound / tail * (b - a))
            saving = span - cost
            if not (saving > tolerance(prices) and saving >= percent / 100 * abs(span)):
                return False
    for index in range(len(combined)):
        window = combined[index : index + limits.window_length]
        touches_plan = index + limits.window_length > past_len and any(
            combined[past_len : index + limits.window_length]
        )
        if touches_plan and sum(window) > limits.window_off_budget:
            return False
    for q in range(past_len, len(combined)):
        if combined[q] and not combined[q - 1]:
            quiet = 0
            while q - 1 - quiet >= 0 and not combined[q - 1 - quiet]:
                quiet += 1
            if q - quiet > 0 and quiet < limits.min_on_after_off:
                return False
    return True


def plan_cost(
    plan: tuple[bool, ...],
    past: list[bool],
    prices: Sequence[float],
    limits: Limits,
    rebound: float,
) -> float:
    """Cost of a plan in quarter units: steady energy plus rebound inside the horizon."""
    horizon = len(plan)
    tail = max(limits.min_on_after_off, 1)
    extra = [0.0] * horizon
    for start, end in _periods(past + list(plan)):
        if end < len(past):  # an earlier run's rebound is the same for every plan
            continue
        for q in range(end - len(past), end - len(past) + tail):
            if q < horizon:
                extra[q] += rebound * (end - start) / tail
    return sum(
        prices[t] * ((0.0 if plan[t] else 1.0) + extra[t]) for t in range(horizon)
    )


def best_plan(
    prices: Sequence[float],
    limits: Limits,
    rebound: float,
    history: Sequence[LoadState],
    percent: float,
    force_on: bool,
) -> tuple[tuple[bool, ...], float]:
    """Enumerate every plan; return the cheapest, then fewest off, then earliest off."""
    past = look_back(history, limits)
    horizon = len(prices)
    options = list(itertools.product([False, True], repeat=horizon))
    for strict in (True, False):
        valid = [
            plan
            for plan in options
            if keeps_limits(
                plan, past, prices, limits, rebound, percent, force_on, strict=strict
            )
            and (strict or not plan[0] or not trailing_off(past))
        ]
        if valid:
            break
    costs = {plan: plan_cost(plan, past, prices, limits, rebound) for plan in valid}
    lowest = min(costs.values())
    near = [plan for plan in valid if costs[plan] <= lowest + tolerance(prices)]
    chosen = min(
        near,
        key=lambda plan: (sum(plan), sum(t + 1 for t, off in enumerate(plan) if off)),
    )
    return chosen, lowest


@dataclass(frozen=True)
class Case:
    """One generated input."""

    prices: tuple[float, ...]
    limits: Limits
    rebound: float
    history: tuple[LoadState, ...]
    percent: float
    force_on: bool


@st.composite
def cases(draw: st.DrawFn, max_horizon: int, max_history: int) -> Case:
    x = draw(st.integers(1, 5))
    z = draw(st.integers(1, 9))
    y = draw(st.integers(1, z))
    m = draw(st.integers(1, min(x, y)))
    n = draw(st.integers(0, 4))
    horizon = draw(st.integers(1, max_horizon))
    prices = draw(st.lists(st.integers(-3, 9), min_size=horizon, max_size=horizon))
    states = draw(st.lists(st.sampled_from([ON, OFF]), max_size=max_history))
    return Case(
        prices=tuple(float(p) for p in prices),
        limits=lim(x, y, z, n, m),
        rebound=draw(st.sampled_from([0.0, 0.25, 0.5, 1.0])),
        history=tuple(states),
        percent=draw(st.sampled_from([0.0, 0.0, 10.0, 50.0])),
        force_on=draw(st.booleans()),
    )


def solve_case(case: Case) -> LoadSchedule:
    return run(
        case.prices,
        case.limits,
        case.rebound,
        case.history,
        case.percent,
        None,
        case.force_on,
    )


# --- T1: no limit is ever broken -------------------------------------------------------


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(cases(max_horizon=14, max_history=14))
def test_t1_every_plan_keeps_every_limit_counting_history(case: Case) -> None:
    result = solve_case(case)
    plan = tuple(q.state is OFF for q in result.plan)
    past = look_back(case.history, case.limits)

    assert len(plan) == len(case.prices)
    assert keeps_limits(
        plan,
        past,
        case.prices,
        case.limits,
        case.rebound,
        case.percent,
        case.force_on,
        strict=False,
    )
    # A run in progress below M is abandoned only by turning on right now.
    assert (
        keeps_limits(
            plan,
            past,
            case.prices,
            case.limits,
            case.rebound,
            case.percent,
            case.force_on,
            strict=True,
        )
        or result.state is ON
    )
    if result.reason.code in {
        ReasonCode.FORCED_ON,
        ReasonCode.MAX_OFF_RUN,
        ReasonCode.WINDOW_BUDGET,
        ReasonCode.MIN_ON_AFTER_OFF,
        ReasonCode.END_OF_HORIZON,
    }:
        assert result.state is ON
    assert result.state is result.plan[0].state


# --- T2: the cost is the brute-force minimum -------------------------------------------


@settings(max_examples=120, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(cases(max_horizon=9, max_history=10))
def test_t2_plan_is_the_cheapest_then_fewest_off_then_earliest(case: Case) -> None:
    result = solve_case(case)
    plan = tuple(q.state is OFF for q in result.plan)

    expected, lowest = best_plan(
        case.prices,
        case.limits,
        case.rebound,
        case.history,
        case.percent,
        case.force_on,
    )

    past = look_back(case.history, case.limits)
    assert plan_cost(plan, past, case.prices, case.limits, case.rebound) == (
        pytest.approx(lowest, abs=1e-6)
    )
    assert plan == expected


@pytest.mark.parametrize(
    ("prices", "limits", "rebound", "history", "expected"),
    [
        # off in the dearest quarter, then the rebound lands on cheap ones
        ([1, 9, 1, 1, 1], lim(2, 2, 5, 1, 1), 0.5, "", "--+++"),
        # a flat price at r < 1 does plan off quarters (T3)
        ([2, 2, 2, 2], lim(1, 1, 1, 0, 1), 0.5, "", "-+-+"),
        # the earlier of two equal-cost plans
        ([5, 5, 1, 1], lim(1, 1, 4, 0, 1), 0.0, "", "-+++"),
    ],
)
def test_schedule_hand_checked_plans(
    prices: list[float], limits: Limits, rebound: float, history: str, expected: str
) -> None:
    assert shape(run(prices, limits, rebound, history)) == expected


# --- T3: A3 as read in the plan --------------------------------------------------------


@pytest.mark.parametrize("history", ["", "++++++", "+++++-", "+++++--", "---+++"])
@pytest.mark.parametrize(
    "limits", [lim(4, 8, 8, 1, 1), lim(3, 3, 6, 2, 2), lim(5, 2, 4, 0, 2)]
)
def test_t3_flat_price_with_full_rebound_starts_no_off_period(
    limits: Limits, history: str
) -> None:
    result = run([2.0] * 10, limits, 1.0, history)

    # At most the run in progress continues to M; nothing else is ever off.
    states = shape(result)
    leading = len(states) - len(states.lstrip("-"))
    assert states[leading:] == "+" * (len(states) - leading)
    run_now = len(history) - len(history.rstrip("-"))
    expected_leading = max(0, limits.min_off_run - run_now) if run_now else 0
    assert leading <= expected_leading


def test_t3_flat_price_full_rebound_no_history_is_always_on() -> None:
    result = run([2.0] * 8, lim(4, 8, 8, 1, 1), 1.0)

    assert shape(result) == "+" * 8
    assert result.reason.code is ReasonCode.NO_SAVING
    assert result.saving_percent == pytest.approx(0.0)


def test_t3_run_in_progress_at_m_stops_on_the_tie_break() -> None:
    result = run([2.0] * 8, lim(4, 8, 8, 1, 1), 1.0, "+++++--")

    assert shape(result) == "+" * 8


def test_t3_run_in_progress_below_m_continues_exactly_to_m() -> None:
    result = run([2.0] * 8, lim(4, 8, 8, 1, 3), 1.0, "++++++-")

    assert shape(result) == "--" + "+" * 6
    assert result.reason.code is ReasonCode.MIN_OFF_RUN


def test_t3_flat_negative_price_with_full_rebound_stays_on() -> None:
    result = run([-2.0] * 8, lim(4, 8, 8, 1, 1), 1.0)

    assert shape(result) == "+" * 8


def test_t3_negative_flat_prices_stay_on_with_a_defined_percentage() -> None:
    result = run([-1.0] * 4, lim(1, 1, 1, 0, 1), 0.0)

    assert shape(result) == "++++"
    assert result.reason.code is ReasonCode.NO_SAVING
    assert result.saving_percent == 0.0


# --- T4: histories that bind -----------------------------------------------------------


@pytest.mark.parametrize(
    ("history", "state", "code"),
    [
        ("++-", ON, None),  # h = X - 1: still free to go off
        ("+--", ON, ReasonCode.MAX_OFF_RUN),  # h = X
        ("---", ON, ReasonCode.MAX_OFF_RUN),  # h > X: a plug switched by hand
        ("------", ON, ReasonCode.MAX_OFF_RUN),  # far above X: no exception
    ],
)
def test_t4_max_off_run_boundary(
    history: str, state: LoadState, code: ReasonCode | None
) -> None:
    result = run([10, 1, 1, 1], lim(2, 4, 4, 0, 1), 0.0, history)

    if code is None:
        assert result.state is OFF
        assert result.reason.code is ReasonCode.PLANNED_OFF
    else:
        assert result.state is state
        assert result.reason.code is code


def test_t4_max_off_run_on_the_plan_after_a_hand_switched_long_run() -> None:
    result = run([10, 1, 1, 1], lim(2, 4, 4, 0, 1), 0.0, "---")

    assert result.plan[0].state is ON
    assert "2" in result.reason.text


@pytest.mark.parametrize(
    ("history", "state", "code"),
    [
        ("-++", OFF, ReasonCode.PLANNED_OFF),
        ("--+", ON, ReasonCode.WINDOW_BUDGET),
    ],
)
def test_t4_window_budget_boundary(
    history: str, state: LoadState, code: ReasonCode
) -> None:
    result = run([10, 1, 1, 1, 1], lim(2, 2, 4, 0, 1), 0.0, history)

    assert (result.state, result.reason.code) == (state, code)
    if code is ReasonCode.WINDOW_BUDGET:
        assert "2" in result.reason.text
        assert "4" in result.reason.text


def test_t4_window_budget_stops_a_run_below_min_off() -> None:
    result = run([5.0] * 6, lim(2, 2, 4, 0, 2), 0.0, "-+-")

    assert shape(result)[0] == "+"
    assert result.reason.code is ReasonCode.WINDOW_BUDGET
    assert result.state is ON


def test_t4_history_that_breaks_a_limit_is_accepted_and_constrained_quarters_are_on() -> (
    None
):
    # The window of 4 already holds 3 off quarters against a budget of 2.
    result = run([10, 10, 1, 1, 1, 1], lim(3, 2, 4, 0, 1), 0.0, "-+--")

    assert result.state is ON
    assert result.plan[0].state is ON
    assert result.reason.code is ReasonCode.WINDOW_BUDGET


def test_t4_history_that_breaks_min_on_is_accepted() -> None:
    # An on run of 1 after an off period, against N = 3; the next quarter may not be off.
    result = run([9, 1, 1, 1, 1, 1], lim(2, 4, 4, 3, 1), 0.0, "-+")

    assert result.state is ON
    assert result.reason.code is ReasonCode.MIN_ON_AFTER_OFF
    assert shape(result)[1] == "+"


# --- T5: the override ------------------------------------------------------------------


def test_t5_force_on_gives_on_and_forced_on() -> None:
    result = run([10, 1, 1, 1, 1], lim(2, 2, 4, 0, 1), 0.0, force_on=True)

    assert result.state is ON
    assert result.reason.code is ReasonCode.FORCED_ON


@pytest.mark.parametrize("history", ["++-", "---"])
def test_t5_force_on_wins_over_a_run_below_m_and_over_max_off_run(history: str) -> None:
    result = run([1.0] * 6, lim(3, 3, 3, 0, 2), 1.0, history, force_on=True)

    assert result.state is ON
    assert result.reason.code is ReasonCode.FORCED_ON


def test_t5_rest_of_the_plan_still_keeps_min_on_after_the_forced_end_of_a_run() -> None:
    # The run ended now, so N = 2 forbids the cheap off arc over [1, 4).
    result = run([1, 10, 10, 10, 1, 1], lim(3, 3, 3, 2, 3), 0.0, "++-", force_on=True)

    assert result.reason.code is ReasonCode.FORCED_ON
    assert shape(result) == "++++++"


def test_t5_rest_of_the_plan_can_still_use_off_quarters_later() -> None:
    result = run([1, 1, 9, 9, 1, 1], lim(2, 2, 6, 1, 2), 0.0, force_on=True)

    assert shape(result) == "++--++"


# --- T6: the figures -------------------------------------------------------------------


def test_t6_clipped_continuation_longer_than_the_horizon_reports_zero_at_flat_r1() -> (
    None
):
    # Regression: the forced run (3 more quarters) is longer than the 2 priced ones.
    result = run([1.0, 1.0], lim(4, 4, 4, 0, 4), 1.0, "-")

    assert shape(result) == "--"
    assert result.reason.code is ReasonCode.MIN_OFF_RUN
    assert result.saving_percent == pytest.approx(0.0)


@pytest.mark.parametrize("prices", [[1.0, 1.0], [1.0, 1.0, 1.0], [1.0] * 4, [1.0] * 6])
def test_t6_zero_saving_at_flat_r1_for_every_horizon_length_against_a_forced_run(
    prices: list[float],
) -> None:
    result = run(prices, lim(4, 4, 4, 0, 4), 1.0, "-")

    assert result.saving_percent == pytest.approx(0.0)


def test_t6_clipped_continuation_charges_rebound_past_the_horizon() -> None:
    result = run([2.0, 2.0], lim(3, 3, 3, 4, 3), 1.0, "+++-", power=1.0)

    assert shape(result) == "--"
    assert result.saving_percent == pytest.approx(0.0)
    assert result.always_on_cost == pytest.approx(1.5)
    assert result.planned_cost == pytest.approx(1.5)
    assert result.saving == pytest.approx(0.0)


def test_t6_clipped_continuation_with_half_rebound() -> None:
    result = run([2.0, 2.0], lim(3, 3, 3, 4, 3), 0.5, "+++-", power=1.0)

    assert result.saving_percent == pytest.approx(40.0)


def test_t6_saving_percent_matches_a_hand_calculation() -> None:
    # off in 3.0, rebound 1.0 * (1 + 0): 3 + 1 against 3 + 1 -> 4 vs 4 at r = 1 ...
    result = run([3.0, 1.0], lim(1, 1, 1, 0, 1), 0.0)

    assert shape(result) == "-+"
    assert result.saving_percent == pytest.approx(75.0)
    result = run([3.0, 1.0], lim(1, 1, 1, 0, 1), 1.0)
    assert result.saving_percent == pytest.approx(50.0)


def test_t6_money_fields_appear_only_with_power_and_scale_with_it() -> None:
    base = run([5.0, 5.0, 1.0, 1.0], lim(1, 1, 4, 0, 1), 0.0)
    assert (base.always_on_cost, base.planned_cost, base.saving) == (None, None, None)

    one = run([5.0, 5.0, 1.0, 1.0], lim(1, 1, 4, 0, 1), 0.0, power=1.0)
    two = run([5.0, 5.0, 1.0, 1.0], lim(1, 1, 4, 0, 1), 0.0, power=2.0)

    assert (one.always_on_cost, one.planned_cost) == (
        pytest.approx(3.0),
        pytest.approx(1.75),
    )
    assert (two.always_on_cost, two.planned_cost, two.saving) == (
        pytest.approx(6.0),
        pytest.approx(3.5),
        pytest.approx(2.5),
    )
    for other in (one, two):
        assert other.plan == base.plan
        assert other.reason == base.reason
        assert other.saving_percent == base.saving_percent
    assert two.saving_percent == pytest.approx(100 * 2.5 / 6.0)


def test_t6_zero_always_on_cost_gives_none_but_keeps_money() -> None:
    result = run([1.0, -1.0], lim(1, 1, 1, 0, 1), 0.0, power=4.0)

    assert shape(result) == "-+"
    assert result.saving_percent is None
    assert result.always_on_cost == 0.0
    assert result.planned_cost == -1.0
    assert result.saving == 1.0


def test_t6_negative_always_on_cost_gives_a_positive_percentage() -> None:
    result = run([2.0, -5.0], lim(1, 1, 1, 0, 1), 0.0, power=4.0)

    assert shape(result) == "-+"
    assert result.saving_percent == pytest.approx(200 / 3)


def test_t6_flat_price_full_rebound_reports_exactly_zero_percent() -> None:
    result = run([2.0] * 12, lim(4, 8, 16, 2, 2), 1.0)

    assert shape(result) == "+" * 12
    assert result.saving_percent == 0.0


# --- T7: ConfigError at the snapshot ---------------------------------------------------


def test_t7_naive_now_is_a_config_error_naming_now() -> None:
    from energy_optimization.common import ConfigError

    with pytest.raises(ConfigError) as error:
        LoadSnapshot(
            now=datetime.fromisoformat("2026-10-21T17:00:00"),
            load=Load(lim(1, 1, 1), 0.5),
            prices=series([1.0]),
        )
    assert error.value.field == "now"


# --- T8: history length ----------------------------------------------------------------


def test_t8_history_short_flag_flips_at_the_look_back() -> None:
    limits = lim(3, 3, 3, 0, 2)  # look-back 3
    assert limits.history_needed == 3

    assert run([3, 1, 1, 1], limits, 0.0, "++-").history_short is False
    assert run([3, 1, 1, 1], limits, 0.0, "+-").history_short is True
    assert run([3, 1, 1, 1], limits, 0.0, "").history_short is True
    assert run([3, 1, 1, 1], limits, 0.0, "-++-").history_short is False


def test_t8_short_history_is_padded_with_on() -> None:
    limits = lim(3, 3, 3, 0, 2)

    assert run([3, 1, 1, 1], limits, 0.0, "+-") == run(
        [3, 1, 1, 1], limits, 0.0, "++-"
    ) or (
        shape(run([3, 1, 1, 1], limits, 0.0, "+-"))
        == shape(run([3, 1, 1, 1], limits, 0.0, "++-"))
    )
    assert run([3, 1, 1, 1], limits, 0.0, "") == run(
        [3, 1, 1, 1], limits, 0.0, "+++"
    ) or shape(run([3, 1, 1, 1], limits, 0.0, "")) == shape(
        run([3, 1, 1, 1], limits, 0.0, "+++")
    )


@pytest.mark.parametrize("older", ["-" * 10, "+" * 7, "-+-+-+-"])
def test_t8_older_history_than_the_look_back_changes_nothing(older: str) -> None:
    limits = lim(2, 4, 4, 0, 1)  # look-back 3
    tail = "--+"
    prices = [10, 1, 1, 1]

    plain = run(prices, limits, 0.5, tail, power=1.0)
    longer = run(prices, limits, 0.5, older + tail, power=1.0)

    assert plain.history_short is False
    assert longer == plain


def test_t8_older_off_run_does_not_leak_into_the_money_figures() -> None:
    limits = lim(2, 4, 4, 0, 1)

    short = run([10, 1, 1, 1], limits, 0.5, "---", power=1.0)
    long = run([10, 1, 1, 1], limits, 0.5, "-" * 10, power=1.0)

    assert long == short


def test_t8_empty_history_is_short_even_for_the_tightest_limits() -> None:
    assert run([1.0], lim(1, 1, 1), 0.0).history_short is True


def test_t8_huge_window_length_with_short_history_is_fast_and_ties_go_earliest() -> (
    None
):
    began = time.perf_counter()
    result = run([3.0, 1.0, 3.0, 1.0], lim(1, 1, 10**9, 0, 1), 0.0)
    elapsed = time.perf_counter() - began

    assert shape(result) == "-+++"
    assert result.reason.code is ReasonCode.PLANNED_OFF
    assert result.history_short is True
    assert elapsed < 1.0


# --- T9: purity, determinism, the solver call ------------------------------------------


def test_t9_repeated_calls_are_equal_and_the_input_is_unchanged() -> None:
    history = list(hist("+-+"))
    snapshot = LoadSnapshot(
        now=T0,
        load=Load(lim(3, 4, 8, 1, 1), 0.7, 5.0, 1.5),
        prices=series([1, 4, 5, 2, 1, 1, 3, 1, 1, 1]),
        history=history,  # type: ignore[arg-type]  # normalised to a tuple
    )
    before = copy.deepcopy(snapshot)

    first = schedule(snapshot)
    second = schedule(snapshot)

    assert first == second
    assert snapshot == before
    assert history == list(hist("+-+"))


def test_t9_now_inside_a_quarter_in_an_odd_offset_gives_utc_starts() -> None:
    offset = timezone(timedelta(hours=5, minutes=45))
    now = datetime(2026, 10, 21, 22, 52, tzinfo=offset)  # 17:07 UTC
    prices = PriceSeries(
        (
            PriceInterval(
                datetime(2026, 10, 21, 22, 45, tzinfo=offset),
                datetime(2026, 10, 21, 23, 45, tzinfo=offset),
                2.0,
            ),
        ),
        T0,
    )

    result = schedule(
        LoadSnapshot(now=now, load=Load(lim(1, 1, 1), 0.5), prices=prices)
    )

    assert result.plan[0].start == T0
    assert result.plan[0].start.tzinfo is UTC
    assert len(result.plan) == 4


@dataclass
class Spy:
    """A stand-in for `milp` that records its calls and may alter the result."""

    calls: list[dict[str, object]]
    script: list[Callable[[OptimizeResult], OptimizeResult] | None]

    def __call__(self, **kwargs: object) -> OptimizeResult:
        """Call the real solver, then apply the scripted change for this call."""
        index = len(self.calls)
        # `milp` pops the options it understands, so record a copy first.
        self.calls.append({**kwargs, "options": dict(kwargs["options"])})  # type: ignore[call-overload]  # a dict
        real = real_milp(**kwargs)  # type: ignore[arg-type]  # forwarded as given
        step = self.script[index] if index < len(self.script) else None
        return real if step is None else step(real)


def spied(
    monkeypatch: pytest.MonkeyPatch,
    *script: Callable[[OptimizeResult], OptimizeResult] | None,
) -> Spy:
    spy = Spy(calls=[], script=list(script))
    monkeypatch.setattr(scheduler, "milp", spy)
    return spy


def without_point(
    status: int, message: str = "no point"
) -> Callable[[OptimizeResult], OptimizeResult]:
    def change(real: OptimizeResult) -> OptimizeResult:
        real["x"] = None
        real["status"] = status
        real["message"] = message
        return real

    return change


def with_status(
    status: int, nodes: int = 0, message: str = "stopped"
) -> Callable[[OptimizeResult], OptimizeResult]:
    def change(real: OptimizeResult) -> OptimizeResult:
        real["status"] = status
        real["mip_node_count"] = nodes
        real["message"] = message
        return real

    return change


def options(spy: Spy, index: int) -> dict[str, object]:
    return cast(dict[str, object], spy.calls[index]["options"])


SMALL = ([3.0, 1.0, 3.0, 1.0, 1.0], lim(1, 2, 5, 1, 1), 0.5)


def test_t9_solver_options_are_one_thread_node_budget_no_time_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = spied(monkeypatch)

    result = run(*SMALL)

    assert result.proven is True
    assert len(spy.calls) == 2  # the cost level, then the off-count level
    for call in spy.calls:
        assert call["options"] == {
            "node_limit": 8000,
            "mip_rel_gap": 0.0,
            "presolve": True,
            "threads": 1,
        }
        assert "time_limit" not in call
    assert scheduler.NODE_LIMIT == 8000


# --- T16: solver failures --------------------------------------------------------------


def test_t16_no_point_is_retried_once_with_presolve_off_and_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = spied(monkeypatch, without_point(1))

    result = run(*SMALL)

    assert result.proven is True
    assert len(spy.calls) == 3
    assert [options(spy, n)["presolve"] for n in range(3)] == [True, False, True]
    kept = [
        {k: v for k, v in options(spy, n).items() if k != "presolve"} for n in (0, 1)
    ]
    assert kept[0] == kept[1]


def test_t16_status_two_with_a_point_is_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = spied(monkeypatch, with_status(2))

    result = run(*SMALL)

    assert result.proven is True
    assert len(spy.calls) == 3


def test_t16_second_infeasible_raises_with_status_two(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spied(monkeypatch, with_status(2), with_status(2, message="still"))

    with pytest.raises(SolverError) as error:
        run(*SMALL)
    assert error.value.status == 2


def test_t16_second_failure_without_a_point_reports_the_retrys_status_and_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = spied(monkeypatch, without_point(1, "first"), without_point(3, "second"))

    with pytest.raises(SolverError, match="second") as error:
        run(*SMALL)
    assert error.value.status == 3
    assert error.value.message == "second"
    assert len(spy.calls) == 2


def test_t16_status_one_with_a_point_is_kept_as_not_proven(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = spied(monkeypatch, with_status(1))

    result = run(*SMALL)

    assert result.proven is False
    assert len(spy.calls) == 2


def test_t16_status_four_at_the_node_limit_is_not_proven(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spied(monkeypatch, with_status(4, nodes=scheduler.NODE_LIMIT))

    assert run(*SMALL).proven is False


@pytest.mark.parametrize(
    ("step", "status"),
    [
        (with_status(4, nodes=scheduler.NODE_LIMIT - 1), 4),
        (with_status(3), 3),
        (with_status(4, nodes=0), 4),
    ],
)
def test_t16_other_statuses_with_a_point_raise_at_once(
    monkeypatch: pytest.MonkeyPatch,
    step: Callable[[OptimizeResult], OptimizeResult],
    status: int,
) -> None:
    spy = spied(monkeypatch, step)

    with pytest.raises(SolverError) as error:
        run(*SMALL)
    assert error.value.status == status
    assert len(spy.calls) == 1


def test_t16_a_failure_in_the_second_level_is_retried_and_can_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = spied(monkeypatch, None, without_point(3, "late"), without_point(3, "later"))

    with pytest.raises(SolverError, match="later"):
        run(*SMALL)
    assert len(spy.calls) == 3
    assert options(spy, 2)["presolve"] is False


def test_t16_a_not_proven_second_level_marks_the_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spied(monkeypatch, None, with_status(1))

    assert run(*SMALL).proven is False


# --- T11: the minimum saving -----------------------------------------------------------


def test_t11_threshold_is_inclusive_at_its_boundary() -> None:
    kwargs = ([3.0, 1.0], lim(1, 1, 1, 0, 1), 0.0)

    assert shape(run(*kwargs, percent=75.0)) == "-+"
    just_over = run(*kwargs, percent=75.0001)
    assert shape(just_over) == "++"
    assert just_over.reason.code is ReasonCode.NO_SAVING
    assert just_over.saving_percent == 0.0


def test_t11_rebound_is_spread_over_the_first_n_on_quarters() -> None:
    # Off in 10; tail (2, 4) with rebound 0.5 x 1 quarter / 2 -> 6 * 1.25 = 7.5.
    # The arc saves 16 - 7.5 = 8.5 of the 16 affected: 53.125 %.
    prices = [10.0, 2.0, 4.0, 1.0]
    limits = lim(1, 1, 1, 2, 1)

    assert shape(run(prices, limits, 0.5)) == "-+++"
    assert shape(run(prices, limits, 0.5, percent=53.125)) == "-+++"
    over = run(prices, limits, 0.5, percent=53.13)
    assert shape(over) == "++++"
    assert over.reason.code is ReasonCode.NO_SAVING


def test_t11_zero_span_passes_any_threshold() -> None:
    # The affected quarters cost 0 always on, so P % of it is 0: any saving is enough.
    # (Decided: the concept measures against the absolute value of the cost.)
    for percent in (0.0, 1e6):
        result = run([1.0, -1.0], lim(1, 1, 1, 0, 1), 0.0, percent=percent)
        assert shape(result) == "-+"


def test_t11_percent_above_one_hundred_can_still_allow_a_negative_price_tail() -> None:
    # The tail has a negative price: the saving exceeds the absolute cost.
    result = run([4.0, -2.0, 1.0], lim(1, 1, 1, 0, 1), 0.0, percent=100.0)

    assert result.plan[0].state is OFF


def test_t11_no_period_reaches_the_threshold_gives_always_on() -> None:
    result = run([2.0, 3.0, 2.0, 3.0, 2.0, 2.0], lim(2, 3, 6, 1, 1), 0.2, percent=99.0)

    assert shape(result) == "++++++"
    assert result.reason.code is ReasonCode.NO_SAVING


def test_t11_plan_is_identical_with_and_without_power() -> None:
    prices = [4, 1, 5, 5, 1, 1, 6, 1]
    without = run(prices, lim(2, 4, 8, 1, 1), 0.3, percent=20.0)
    with_power = run(prices, lim(2, 4, 8, 1, 1), 0.3, percent=20.0, power=3.0)

    assert with_power.plan == without.plan
    assert with_power.reason == without.reason
    assert with_power.saving_percent == without.saving_percent


@settings(max_examples=80, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(cases(max_horizon=12, max_history=8), st.sampled_from([5.0, 25.0, 60.0]))
def test_t11_every_off_period_saves_at_least_the_percentage(
    case: Case, percent: float
) -> None:
    result = run(
        case.prices,
        case.limits,
        case.rebound,
        case.history,
        percent,
        None,
        case.force_on,
    )
    plan = tuple(q.state is OFF for q in result.plan)
    past = look_back(case.history, case.limits)

    assert keeps_limits(
        plan,
        past,
        case.prices,
        case.limits,
        case.rebound,
        percent,
        case.force_on,
        strict=False,
    )


# --- T12: the end of the horizon -------------------------------------------------------


def test_t12_horizon_too_short_for_an_off_period_gives_end_of_horizon() -> None:
    result = run([10.0, 1.0], lim(2, 2, 2, 1, 2), 0.0)

    assert shape(result) == "++"
    assert result.reason.code is ReasonCode.END_OF_HORIZON
    assert "2" in result.reason.text


def test_t12_horizon_just_long_enough_plans_the_off_period() -> None:
    result = run([10.0, 10.0, 1.0], lim(2, 2, 2, 1, 2), 0.0)

    assert shape(result) == "--+"
    assert result.reason.code is ReasonCode.PLANNED_OFF


def test_t12_last_n_quarters_stay_on() -> None:
    result = run([1, 1, 1, 1, 1, 10], lim(2, 2, 2, 2, 1), 0.0)

    assert shape(result)[-2:] == "++"


@pytest.mark.parametrize(
    ("prices", "limits", "history", "plan", "code"),
    [
        ([3.0, 1.0], lim(1, 1, 1, 0, 1), "", "-+", ReasonCode.PLANNED_OFF),
        ([3.0], lim(1, 1, 1, 0, 1), "", "+", ReasonCode.END_OF_HORIZON),
        ([3.0], lim(2, 2, 2, 0, 1), "+-", "+", ReasonCode.END_OF_HORIZON),
        ([3.0], lim(3, 3, 3, 0, 3), "-", "-", ReasonCode.MIN_OFF_RUN),
    ],
)
def test_t12_horizon_length_boundaries(
    prices: list[float], limits: Limits, history: str, plan: str, code: ReasonCode
) -> None:
    result = run(prices, limits, 0.0, history)

    assert shape(result) == plan
    assert result.reason.code is code


def test_t12_run_below_m_continues_even_when_its_tail_falls_past_the_horizon() -> None:
    result = run([2.0, 2.0], lim(3, 3, 3, 4, 3), 1.0, "+++-")

    assert shape(result) == "--"
    assert result.reason.code is ReasonCode.MIN_OFF_RUN


@pytest.mark.parametrize(
    ("history", "plan", "code"),
    [
        ("+-", "-++++++", ReasonCode.MIN_OFF_RUN),  # h=1, M=2: one more quarter
        ("+--", "+" * 7, ReasonCode.NO_SAVING),  # h=M: ends now, flat price
        ("---", "+" * 7, ReasonCode.MAX_OFF_RUN),  # h=X
    ],
)
def test_t3_run_in_progress_at_each_boundary(
    history: str, plan: str, code: ReasonCode
) -> None:
    result = run([1.0] * 7, lim(3, 3, 3, 0, 2), 1.0, history)

    assert shape(result) == plan
    assert result.reason.code is code
    assert result.saving_percent == pytest.approx(0.0)


def test_t3_run_in_progress_extra_one_and_two_cost_the_same_so_the_shorter_wins() -> (
    None
):
    result = run([1.0] * 6, lim(3, 3, 3, 0, 2), 1.0, "++-")

    assert shape(result) == "-+++++"


# --- T14: the reason table -------------------------------------------------------------


def test_t14_no_price_for_the_current_quarter() -> None:
    later = T0 + timedelta(hours=1)
    snapshot = LoadSnapshot(
        now=T0,
        load=Load(lim(1, 1, 1), 0.5, power_kw=1.0),
        prices=series([1.0, 2.0], start=later),
        force_on=True,
    )

    result = schedule(snapshot)

    assert result.state is ON
    assert result.reason.code is ReasonCode.NO_PRICES
    assert result.plan == ()
    assert result.saving_percent is None
    assert (result.always_on_cost, result.planned_cost, result.saving) == (
        None,
        None,
        None,
    )
    assert result.history_short is True
    assert result.proven is True


def test_t14_no_price_keeps_the_history_short_flag_off_when_history_is_long() -> None:
    snapshot = LoadSnapshot(
        now=T0,
        load=Load(lim(1, 1, 1), 0.5),
        prices=series([1.0], start=T0 + timedelta(hours=1)),
        history=hist("++"),
    )

    assert schedule(snapshot).history_short is False


def test_t14_an_unpriced_gap_ends_the_horizon() -> None:
    prices = PriceSeries(
        (
            PriceInterval(T0, T0 + timedelta(minutes=30), 1.0),
            PriceInterval(
                T0 + timedelta(minutes=45), T0 + timedelta(hours=1, minutes=45), 1.0
            ),
        ),
        T0,
    )

    result = schedule(LoadSnapshot(now=T0, load=Load(lim(1, 1, 1), 0.5), prices=prices))

    assert len(result.plan) == 2


def test_t14_prices_starting_after_the_current_quarter_give_no_prices() -> None:
    prices = series([1.0, 1.0], start=T0 + timedelta(minutes=15))

    result = schedule(
        LoadSnapshot(
            now=T0 + timedelta(minutes=7), load=Load(lim(1, 1, 1), 0.5), prices=prices
        )
    )

    assert result.reason.code is ReasonCode.NO_PRICES
    assert result.plan == ()


def test_t14_prices_ending_before_now_give_no_prices() -> None:
    result = schedule(
        LoadSnapshot(
            now=T0 + timedelta(hours=2),
            load=Load(lim(1, 1, 1), 0.5),
            prices=series([1.0, 1.0]),
        )
    )

    assert result.reason.code is ReasonCode.NO_PRICES


@pytest.mark.parametrize(
    ("prices", "limits", "history", "force_on", "code", "state"),
    [
        # force_on beats a run past X, which beats the window, which beats N
        ([10, 1, 1, 1], lim(2, 2, 4, 1, 1), "---", True, ReasonCode.FORCED_ON, ON),
        ([10, 1, 1, 1], lim(2, 2, 4, 1, 1), "---", False, ReasonCode.MAX_OFF_RUN, ON),
        ([10, 1, 1, 1], lim(2, 2, 4, 2, 1), "--+", False, ReasonCode.WINDOW_BUDGET, ON),
        ([10, 1], lim(2, 2, 4, 2, 1), "+-+", False, ReasonCode.MIN_ON_AFTER_OFF, ON),
        # X and the window before M: a run below M that cannot continue is cut
        ([10, 1, 1, 1, 1], lim(3, 3, 4, 1, 3), "++-", True, ReasonCode.FORCED_ON, ON),
        (
            [10, 1, 1, 1, 1, 1, 1],
            lim(3, 4, 8, 1, 3),
            "---+++-",
            False,
            ReasonCode.WINDOW_BUDGET,
            ON,
        ),
        # M and N give way to the end-of-horizon rule only where nothing else decides
        ([10, 1], lim(2, 2, 4, 2, 1), "", False, ReasonCode.END_OF_HORIZON, ON),
        ([10, 10, 1, 1], lim(2, 2, 4, 1, 1), "", False, ReasonCode.PLANNED_OFF, OFF),
    ],
)
def test_t14_reason_precedence(
    prices: list[float],
    limits: Limits,
    history: str,
    force_on: bool,
    code: ReasonCode,
    state: LoadState,
) -> None:
    result = run(prices, limits, 0.0, history, force_on=force_on)

    assert (result.reason.code, result.state) == (code, state)
    assert result.plan[0].state is state


def test_t14_min_on_after_off_boundary() -> None:
    kwargs = ([10.0, 1.0, 1.0, 1.0, 1.0, 1.0], lim(2, 4, 4, 3, 1), 0.0)

    below = run(*kwargs, history="-++")
    at = run(*kwargs, history="-+++")
    no_off = run(*kwargs, history="+")

    assert (below.state, below.reason.code) == (ON, ReasonCode.MIN_ON_AFTER_OFF)
    assert shape(below)[0] == "+"
    assert (at.state, at.reason.code) == (OFF, ReasonCode.PLANNED_OFF)
    assert no_off.reason.code is not ReasonCode.MIN_ON_AFTER_OFF


def test_t14_no_off_arc_before_n_minus_j() -> None:
    result = run(
        [5.0, 5.0, 9.0, 1.0, 1.0, 1.0, 1.0, 1.0], lim(2, 4, 4, 3, 1), 0.0, "-+"
    )

    assert shape(result)[:2] == "++"


def test_t14_min_off_run_while_the_run_is_below_m() -> None:
    result = run([1.0] * 5, lim(3, 3, 3, 0, 3), 0.0, "+-")

    assert result.state is OFF
    assert result.reason.code is ReasonCode.MIN_OFF_RUN
    assert "3" in result.reason.text


def test_t14_planned_on_when_the_plan_goes_off_later() -> None:
    result = run([1, 1, 9, 9, 1, 1], lim(2, 2, 6, 1, 2), 0.0)

    assert result.state is ON
    assert result.reason.code is ReasonCode.PLANNED_ON


def test_t14_planned_off_names_the_number_of_off_quarters() -> None:
    result = run([9, 9, 1, 1], lim(2, 2, 4, 1, 1), 0.0)

    assert result.reason.code is ReasonCode.PLANNED_OFF
    assert "2" in result.reason.text


def test_t14_run_ending_now_without_later_off_reports_no_saving() -> None:
    # Pinned, not endorsed: the sentence says "no off period saves enough" about a run
    # that was stopped, because the reason follows the plan, which has no off quarter.
    result = run([2.0] * 6, lim(4, 8, 8, 1, 1), 1.0, "++--")

    assert result.reason.code is ReasonCode.NO_SAVING


# --- N = 0 behaves as N = 1 ------------------------------------------------------------


@pytest.mark.parametrize(
    "prices",
    [[5, 1, 5, 1, 5, 1, 1], [3, 3, 8, 1, 1, 9, 1, 1], [-2, 4, 4, 4, 1, 1]],
)
def test_n_zero_behaves_as_n_one(prices: list[float]) -> None:
    zero = run(prices, lim(2, 3, 4, 0, 1), 0.5, "-+")
    one = run(prices, lim(2, 3, 4, 1, 1), 0.5, "-+")

    assert zero.plan == one.plan
    assert zero.saving_percent == one.saving_percent


# --- the other cases the designers asked for --------------------------------------------


def test_schedule_r0_flat_alternates_and_keeps_the_last_quarter_on() -> None:
    result = run([1.0] * 4, lim(1, 1, 1, 0, 1), 0.0)

    assert shape(result) == "-+-+"
    assert result.saving_percent == pytest.approx(50.0)


def test_schedule_r1_does_not_plan_off_before_a_dearer_quarter() -> None:
    cheap_after = run([3.0, 1.0], lim(1, 1, 1, 0, 1), 1.0)
    dear_after = run([3.0, 4.0], lim(1, 1, 1, 0, 1), 1.0)

    assert shape(cheap_after) == "-+"
    assert cheap_after.saving_percent == pytest.approx(50.0)
    assert shape(dear_after) == "++"
    assert dear_after.reason.code is ReasonCode.NO_SAVING


def test_schedule_window_budget_binds_across_the_horizon() -> None:
    # Y = 1 in every window of 4: at most one off quarter in any four.
    result = run([9, 1, 9, 1, 9, 1, 9, 1], lim(1, 1, 4, 0, 1), 0.0)

    states = shape(result)
    assert all(states[i : i + 4].count("-") <= 1 for i in range(len(states)))
    assert states.count("-") >= 2


def test_schedule_sliding_window_not_fixed_blocks() -> None:
    # Offs at 3 and 4 would fit two fixed blocks of 4 but not the sliding window [1, 5).
    result = run([1, 1, 1, 9, 9, 1, 1, 1], lim(1, 1, 4, 0, 1), 0.0)

    states = shape(result)
    assert all(states[i : i + 4].count("-") <= 1 for i in range(len(states)))


def test_schedule_horizon_runs_from_the_quarter_holding_now() -> None:
    offset = timezone(timedelta(hours=5, minutes=45))
    now = datetime(2026, 10, 21, 22, 52, tzinfo=offset)  # 17:07 UTC

    result = schedule(
        LoadSnapshot(
            now=now,
            load=Load(lim(1, 1, 1), 0.5),
            prices=series([1.0] * 4),
        )
    )

    assert [q.start for q in result.plan] == [
        T0 + timedelta(minutes=15 * i) for i in range(4)
    ]


def test_schedule_plan_carries_the_price_of_each_quarter() -> None:
    result = run([1.5, -2.0, 3.0], lim(1, 1, 1), 0.0)

    assert [q.price for q in result.plan] == [1.5, -2.0, 3.0]


def test_schedule_unsorted_price_intervals_are_read_in_time_order() -> None:
    forward = series([3.0, 1.0, 3.0, 1.0])
    shuffled = PriceSeries(tuple(reversed(forward.intervals)), T0)

    one = schedule(LoadSnapshot(T0, Load(lim(1, 1, 1), 0.0), forward))
    other = schedule(LoadSnapshot(T0, Load(lim(1, 1, 1), 0.0), shuffled))

    assert one == other


def test_schedule_prices_beyond_a_gap_are_ignored() -> None:
    gapped = PriceSeries(
        (
            PriceInterval(T0, T0 + timedelta(minutes=30), 5.0),
            PriceInterval(T0 + timedelta(hours=1), T0 + timedelta(hours=2), 0.1),
        ),
        T0,
    )

    result = schedule(LoadSnapshot(T0, Load(lim(1, 1, 1), 0.0), gapped))

    assert len(result.plan) == 2


# --- T13: speed ------------------------------------------------------------------------


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_t13_a_192_quarter_horizon_with_a_day_long_window_solves_within_ten_seconds(
    seed: int,
) -> None:
    import random

    rng = random.Random(seed)
    day = [4.0 + 3.0 * ((t % 96) / 96) + rng.uniform(-1.5, 1.5) for t in range(192)]
    snapshot = snap(day, lim(8, 32, 96, 4, 2), 0.8)

    began = time.perf_counter()
    result = schedule(snapshot)
    elapsed = time.perf_counter() - began

    assert elapsed < 10.0
    assert len(result.plan) == 192
    assert result.plan[-4:] == tuple(q for q in result.plan[-4:] if q.state is ON)
    states = shape(result)
    assert all(states[i : i + 96].count("-") <= 32 for i in range(len(states)))


# --- the showcase ----------------------------------------------------------------------


def test_showcase_runs(capsys: pytest.CaptureFixture[str]) -> None:
    module: ModuleType = scheduler

    module.main()

    out = capsys.readouterr().out
    assert "plan (# on, . off)" in out
    assert "forced: on (forced_on)" in out
