"""Tests for energy_optimization.ev.optimize (T1, T2, T4, T6, T7, T8).

Round 1's `share` tests are kept first, driven through `optimize` with one
`CHARGE_NOW` demand per claim: the quarter-0 tiers must reproduce that rule. After them
come the planning stage (hand-checked plans, minimum blocks, price cap, unpriced
quarters, DST), the tiers, `Demand` validation, the solver-failure and node-budget
paths, a brute-force check of the lexicographic tuple and a limits property.
"""

import itertools
import math
import random
from collections.abc import Callable
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfoNotFoundError

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import energy_optimization.ev as ev
from energy_optimization.ev import optimize as optimize_module
from energy_optimization.ev.model import Phase
from energy_optimization.ev.optimize import (
    NODE_LIMIT,
    Demand,
    Outcome,
    SolverError,
    Tier,
    _current,
    _price_ticks,
    _verify,
    optimize,
)
from energy_optimization.ev.optimize import main as showcase
from energy_optimization.ev.planning import energy_per_quarter_kwh
from energy_optimization.ev.quarters import floor_to_quarter, quarter_starts

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
) -> Demand:
    return Demand(charger_id, phases, Tier.CHARGE_NOW, low, high, high)


def caps(l1: float = 25.0, l2: float = 25.0, l3: float = 25.0) -> dict[Phase, float]:
    return {Phase.L1: l1, Phase.L2: l2, Phase.L3: l3}


def _share(claims: list[Demand], capacity: dict[Phase, float]) -> dict[str, float]:
    outcome = optimize(claims, NOW, {}, capacity, capacity, 230.0, 2, None, "UTC")
    return outcome.allocations


# --- quarter-0 sharing: round 1's share tests (T4) ----------------------------


@pytest.mark.parametrize(
    "reverse", [False, True], ids=["three-phase first", "single first"]
)
def test_share_mixed_phases_is_max_min_fair(reverse: bool) -> None:
    claims = [claim("t", 6, 16, ALL), claim("s", 6, 10, L1)]
    if reverse:
        claims.reverse()

    assert _share(claims, CAP25) == {"t": 15.0, "s": 10.0}


def test_share_unequal_minimums_level_up_from_the_lowest_allocation() -> None:
    claims = [claim("a", 10, 16), claim("b", 6, 16)]

    assert _share(claims, caps(l1=24)) == {"a": 12.0, "b": 12.0}


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

    result = _share(claims, caps(l1=capacity))

    assert (result["a"], result["b"], result["c"]) == expected


def test_share_remainder_follows_ranking_order_not_id_order() -> None:
    claims = [claim("y"), claim("x")]

    assert _share(claims, caps(l1=13)) == {"y": 7.0, "x": 6.0}


def test_share_an_unfit_higher_ranked_claim_does_not_block_a_lower_one() -> None:
    claims = [claim("a", 10, 16), claim("b", 6, 16)]

    assert _share(claims, caps(l1=8)) == {"a": 0.0, "b": 8.0}


def test_share_a_later_claim_on_another_phase_still_fits() -> None:
    claims = [claim("x", phases=L1), claim("y", phases=L2)]

    assert _share(claims, caps(l1=5, l2=10)) == {"x": 0.0, "y": 10.0}


def test_share_a_full_phase_does_not_stop_claims_on_other_phases() -> None:
    claims = [claim("A", phases=ALL), claim("B", phases=L2)]

    assert _share(claims, caps(l1=10, l2=25, l3=25)) == {"A": 10.0, "B": 15.0}


def test_share_caps_every_claim_at_its_max() -> None:
    claims = [claim("a", 6, 8), claim("b", 6, 16)]

    assert _share(claims, caps(l1=30)) == {"a": 8.0, "b": 16.0}


@pytest.mark.parametrize(
    ("capacity", "expected"), [(9, 0.0), (10, 10.0), (15, 10.0), (100, 10.0)]
)
def test_share_all_or_nothing_is_never_partial(
    capacity: float, expected: float
) -> None:
    assert _share([claim("o", 10, 10)], caps(l1=capacity)) == {"o": expected}


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

    assert _share(claims, caps(l1=capacity)) == expected


@pytest.mark.parametrize(
    ("capacity", "expected"),
    [(15.5, 15.0), (15.9999999995, 16.0), (16, 16.0), (100, 16.0), (6.5, 6.0)],
)
def test_share_floors_fractional_capacity_to_whole_amps(
    capacity: float, expected: float
) -> None:
    assert _share([claim("a")], caps(l1=capacity)) == {"a": expected}


@pytest.mark.parametrize(
    ("capacity", "expected"),
    [(6.0, 6.0), (6 - 1e-10, 6.0), (5.9999999995, 6.0), (5.99, 0.0), (0.0, 0.0)],
)
def test_share_min_boundary_with_tolerance(capacity: float, expected: float) -> None:
    assert _share([claim("a")], caps(l1=capacity)) == {"a": expected}


@pytest.mark.parametrize(
    ("l3", "expected"), [(9.0, 9.0), (5.0, 0.0), (6.0, 6.0), (16.0, 16.0)]
)
def test_share_the_tightest_phase_governs_a_multi_phase_claim(
    l3: float, expected: float
) -> None:
    result = _share([claim("t", 6, 16, ALL)], caps(16, 16, l3))

    assert result == {"t": expected}


@pytest.mark.parametrize(
    "capacity",
    [{}, {Phase.L1: 0.0}, {Phase.L1: -3.0}, {Phase.L2: 25.0}],
    ids=["empty", "zero", "negative", "other phase only"],
)
def test_share_gives_nothing_when_the_phase_has_no_room(
    capacity: dict[Phase, float],
) -> None:
    assert _share([claim("a")], capacity) == {"a": 0.0}


def test_share_of_no_claims_is_empty() -> None:
    assert _share([], CAP25) == {}


def test_share_a_claim_with_max_below_min_gets_zero_and_consumes_nothing() -> None:
    claims = [claim("bad", 10, 8), claim("a")]

    assert _share(claims, caps(l1=16)) == {"bad": 0.0, "a": 16.0}


def test_share_returns_every_claim_id_even_when_excluded() -> None:
    claims = [claim("a"), claim("b"), claim("c")]

    assert set(_share(claims, caps(l1=6))) == {"a", "b", "c"}


def test_share_does_not_mutate_its_arguments() -> None:
    capacity = caps(l1=20)
    before = dict(capacity)
    claims = [claim("a"), claim("b")]
    claims_before = list(claims)

    _share(claims, capacity)

    assert capacity == before
    assert claims == claims_before


def test_share_is_idempotent() -> None:
    claims = [claim("a"), claim("b", phases=ALL)]

    assert _share(claims, caps(l1=20)) == _share(claims, caps(l1=20))


# --- planning stage: helpers -----------------------------------------------------

QUARTER = timedelta(minutes=15)
E16 = energy_per_quarter_kwh(16, 1, 230.0)  # 0.92 kWh per quarter on one phase


def q(t: int) -> datetime:
    """Start of the t-th quarter from NOW."""
    return NOW + QUARTER * t


def priced(*values: float | None) -> dict[datetime, float]:
    """Prices for consecutive quarters from NOW; None leaves the quarter unpriced."""
    return {q(t): v for t, v in enumerate(values) if v is not None}


def planned(
    charger_id: str = "a",
    need: float = E16,
    quarters: int = 4,
    low: float = 6,
    high: float = 16,
    phases: frozenset[Phase] = L1,
    now_high: float | None = None,
) -> Demand:
    return Demand(
        charger_id,
        phases,
        Tier.PLANNED,
        low,
        high,
        high if now_high is None else now_high,
        need_kwh=need,
        deadline=q(quarters),
    )


def run(
    demands: list[Demand],
    prices: dict[datetime, float] | None = None,
    capacity: dict[Phase, float] | None = None,
    now_capacity: dict[Phase, float] | None = None,
    block: int = 1,
    cap: float | None = None,
    now: datetime = NOW,
) -> Outcome:
    return optimize(
        demands,
        now,
        {} if prices is None else prices,
        caps(16, 16, 16) if capacity is None else capacity,
        caps(16, 16, 16) if now_capacity is None else now_capacity,
        230.0,
        block,
        cap,
        "Europe/Copenhagen",
    )


def shape(outcome: Outcome, charger_id: str = "a") -> dict[datetime, float]:
    """The plan of a charger as {UTC quarter start: current}."""
    return {
        quarter.start.astimezone(UTC): quarter.current
        for quarter in outcome.plans[charger_id].quarters
    }


# --- bugs the tests found (step 5): each was red before its fix ------------------


@pytest.mark.parametrize("assumed", [7.5, 10.0], ids=["fractional", "whole"])
def test_optimize_plans_an_on_off_demand_at_its_exact_current(assumed: float) -> None:
    need = 2 * energy_per_quarter_kwh(assumed, 1, 230.0)
    demand = planned("p", need=need, low=assumed, high=assumed)

    outcome = run([demand], priced(1, 1, 2, 2))

    assert outcome.plans["p"].reachable is True
    assert shape(outcome, "p") == {q(0): assumed, q(1): assumed}
    assert outcome.allocations["p"] == assumed


def test_optimize_plans_a_fractional_on_off_demand_beside_an_adjustable_one() -> None:
    # An on/off 7.5 A pump and an adjustable car share L1 at 20 A: both run now, so the
    # even-split level has a pinned fractional column to cope with.
    pump = planned(
        "p", need=4 * energy_per_quarter_kwh(7.5, 1, 230.0), low=7.5, high=7.5
    )
    car = planned("c", need=4 * 0.0575 * 12)

    outcome = run(
        [pump, car], priced(1, 1, 1, 1), caps(20, 20, 20), caps(20, 20, 20), block=1
    )

    assert outcome.plans["p"].reachable is True
    assert all(c in (0.0, 7.5) for c in shape(outcome, "p").values())
    assert outcome.allocations["p"] == 7.5
    assert outcome.allocations["c"] == 12.0


def test_optimize_gives_the_better_ranked_car_the_earlier_cheap_quarter() -> None:
    a = planned("a", need=10 * 0.0575, quarters=3, low=6, high=10)
    b = planned("b", need=10 * 0.0575, quarters=3, low=6, high=10)

    outcome = run(
        [a, b], priced(1.0, 0.1, 0.1), caps(10, 10, 10), caps(10, 10, 10), block=1
    )

    assert shape(outcome, "a") == {q(1): 10.0}
    assert shape(outcome, "b") == {q(2): 10.0}


def test_optimize_ranking_follows_the_list_order_not_the_id() -> None:
    first = planned("z", need=10 * 0.0575, quarters=3, low=6, high=10)
    second = planned("a", need=10 * 0.0575, quarters=3, low=6, high=10)

    outcome = run(
        [first, second], priced(1.0, 0.1, 0.1), caps(10, 10, 10), caps(10, 10, 10)
    )

    assert shape(outcome, "z") == {q(1): 10.0}
    assert shape(outcome, "a") == {q(2): 10.0}


def test_optimize_holds_smart_off_to_capacity_in_the_later_quarters() -> None:
    demand = Demand("s", L1, Tier.SMART_OFF, 6, 16, 16)

    outcome = run([demand], capacity=caps(10, 10, 10), now_capacity=caps(16, 16, 16))

    assert outcome.allocations == {"s": 10.0}


def test_demand_rejects_a_tier_that_is_not_a_tier() -> None:
    with pytest.raises(ValueError, match="^tier"):
        Demand("a", L1, 2, 6, 16, 16)  # type: ignore[arg-type]  # untyped callers


# --- planning stage: hand-checked plans (T3) -------------------------------------


@pytest.mark.parametrize(
    ("block", "expected"),
    [
        (1, {0: 16.0, 2: 16.0}),
        (2, {0: 16.0, 1: 6.0, 2: 10.0}),
    ],
    ids=["no-minimum-block", "block-of-two"],
)
def test_optimize_a_minimum_block_trades_price_for_a_contiguous_run(
    block: int, expected: dict[int, float]
) -> None:
    outcome = run([planned(need=2 * E16)], priced(1, 5, 1, 5), block=block)

    assert shape(outcome) == {q(t): c for t, c in expected.items()}
    assert outcome.allocations["a"] == 16.0


@pytest.mark.parametrize(
    ("block", "expected"),
    [(2, [16.0, 16.0]), (8, [16.0, 10.0, 6.0])],
    ids=["block-of-two", "block-longer-than-the-window"],
)
def test_optimize_a_block_longer_than_the_window_is_cut_short_by_the_deadline(
    block: int, expected: list[float]
) -> None:
    outcome = run([planned(need=2 * E16, quarters=3)], priced(1, 1, 1), block=block)

    assert [c for c in shape(outcome).values()] == expected


@pytest.mark.parametrize(
    ("block", "expected"),
    [(1, {0: 6.0}), (2, {3: 6.0})],
    ids=["block-of-one", "block-of-two"],
)
def test_optimize_a_tiny_need_goes_to_the_deadline_quarter_under_a_block(
    block: int, expected: dict[int, float]
) -> None:
    # Pinned, not endorsed (step 8): P4 outranks "earlier first", and the run a deadline
    # cuts short is free, so a need below one minimum-current quarter waits for the last.
    outcome = run([planned(need=0.001)], priced(1, 1, 1, 1), block=block)

    assert shape(outcome) == {q(t): c for t, c in expected.items()}
    assert outcome.allocations["a"] == expected.get(0, 0.0)


def test_optimize_a_partly_unpriced_horizon_uses_the_priced_quarters_first() -> None:
    outcome = run([planned(need=3 * E16)], priced(1, 1, None, None))

    assert shape(outcome) == {q(0): 16.0, q(1): 16.0, q(3): 16.0}
    assert outcome.plans["a"].uses_unpriced is True
    assert outcome.plans["a"].reachable is True


def test_optimize_an_all_unpriced_horizon_charges_only_the_latest_quarter() -> None:
    outcome = run([planned(need=E16)], {})

    assert shape(outcome) == {q(3): 16.0}
    assert outcome.allocations == {"a": 0.0}
    assert outcome.plans["a"].uses_unpriced is True
    assert outcome.plans["a"].quarters[0].price is None


def test_optimize_unpriced_quarters_go_to_the_better_ranked_car_earlier() -> None:
    need = 10 * 0.0575
    a = planned("a", need=need, quarters=3, high=10)
    b = planned("b", need=need, quarters=3, high=10)

    outcome = run([a, b], {}, caps(10, 10, 10), caps(10, 10, 10))

    assert shape(outcome, "a") == {q(1): 10.0}
    assert shape(outcome, "b") == {q(2): 10.0}


def test_optimize_equal_prices_put_the_better_ranked_car_on_now() -> None:
    need = 10 * 0.0575
    first = planned("z", need=need, quarters=3, high=10)
    second = planned("a", need=need, quarters=3, high=10)

    outcome = run([first, second], priced(1, 1, 1), caps(10, 10, 10), caps(10, 10, 10))

    assert outcome.allocations == {"z": 10.0, "a": 0.0}
    assert shape(outcome, "a") == {q(1): 10.0}


def test_optimize_negative_prices_overfill_by_at_most_one_quarter() -> None:
    outcome = run([planned(need=1.0)], priced(-0.1, -0.1, -0.1, -0.1))

    plan = outcome.plans["a"]
    assert [c for c in shape(outcome).values()] == [16.0, 11.0, 6.0]
    assert plan.planned_kwh == pytest.approx(33 * 0.0575)
    assert plan.planned_kwh <= 1.0 + E16
    assert plan.reachable is True


@pytest.mark.parametrize(("price", "exceeded"), [(0.5, False), (0.51, True)])
def test_optimize_a_price_at_the_cap_is_not_above_it(
    price: float, exceeded: bool
) -> None:
    outcome = run([planned(need=2 * E16, quarters=2)], priced(price, 0.1), cap=0.5)

    assert shape(outcome) == {q(0): 16.0, q(1): 16.0}
    assert outcome.plans["a"].price_cap_exceeded is exceeded


def test_optimize_uses_the_cap_only_to_meet_the_need() -> None:
    outcome = run([planned(need=E16, quarters=3)], priced(0.9, 0.2, 0.2), cap=0.5)

    assert outcome.plans["a"].price_cap_exceeded is False
    assert all(q_.price == 0.2 for q_ in outcome.plans["a"].quarters)


def test_optimize_a_shortfall_is_reported_with_the_reachable_energy() -> None:
    outcome = run([planned(need=10.0, quarters=2)], priced(1, 1))

    plan = outcome.plans["a"]
    assert plan.reachable is False
    assert plan.planned_kwh == pytest.approx(2 * E16)


def test_optimize_now_capacity_limits_only_the_first_quarter() -> None:
    outcome = run(
        [planned(need=2 * E16, quarters=3)],
        priced(1, 2, 3),
        caps(16, 16, 16),
        caps(8, 8, 8),
    )

    assert [c for c in shape(outcome).values()] == [8.0, 16.0, 8.0]
    assert outcome.allocations["a"] == 8.0


def test_optimize_the_first_quarter_current_is_the_plans_first_quarter() -> None:
    outcome = run([planned(need=E16)], priced(0.1, 1, 1, 1))

    assert outcome.allocations["a"] == shape(outcome)[q(0)]


@pytest.mark.parametrize(
    "deadline",
    [NOW, NOW - timedelta(hours=1)],
    ids=["deadline-is-now", "deadline-in-the-past"],
)
def test_optimize_plans_nothing_for_a_deadline_at_or_before_now(
    deadline: datetime,
) -> None:
    demand = Demand("a", L1, Tier.PLANNED, 6, 16, 16, need_kwh=E16, deadline=deadline)

    outcome = run([demand], priced(1, 1))

    plan = outcome.plans["a"]
    assert outcome.allocations == {"a": 0.0}
    assert plan.quarters == ()
    assert plan.planned_kwh == 0.0
    assert plan.reachable is False


def test_optimize_a_deadline_inside_the_first_quarter_plans_that_quarter() -> None:
    now = NOW + timedelta(minutes=5)
    demand = Demand(
        "a",
        L1,
        Tier.PLANNED,
        6,
        16,
        16,
        need_kwh=10.0,
        deadline=NOW + 10 * timedelta(minutes=1),
    )

    outcome = run([demand], priced(1), now=now)

    assert shape(outcome) == {NOW: 16.0}
    assert outcome.allocations == {"a": 16.0}
    assert outcome.plans["a"].reachable is False


def test_optimize_a_zero_need_plans_nothing() -> None:
    outcome = run([planned(need=0.0)], priced(1, 1, 1, 1))

    assert outcome.plans["a"].quarters == ()
    assert outcome.plans["a"].reachable is True
    assert outcome.allocations == {"a": 0.0}


def test_optimize_plans_the_repeated_hour_of_the_fall_back_night_in_utc() -> None:
    now = datetime(2026, 10, 25, 0, 0, tzinfo=UTC)
    prices = {now + QUARTER * t: 1.0 for t in range(4)}
    prices |= {now + QUARTER * (4 + t): 0.1 for t in range(4)}
    demand = Demand(
        "a",
        L1,
        Tier.PLANNED,
        6,
        16,
        16,
        need_kwh=4 * E16,
        deadline=now + 2 * timedelta(hours=1),
    )

    outcome = run([demand], prices, now=now)

    plan = outcome.plans["a"]
    assert [q_.start.astimezone(UTC) for q_ in plan.quarters] == [
        now + QUARTER * (4 + t) for t in range(4)
    ]
    assert [q_.start.isoformat() for q_ in plan.quarters] == [
        "2026-10-25T02:00:00+01:00",
        "2026-10-25T02:15:00+01:00",
        "2026-10-25T02:30:00+01:00",
        "2026-10-25T02:45:00+01:00",
    ]
    assert all(q_.price == 0.1 for q_ in plan.quarters)
    assert outcome.allocations == {"a": 0.0}


def test_optimize_a_48_hour_horizon_of_192_quarters_plans_the_cheapest_first() -> None:
    prices = {q(t): 0.01 * t for t in range(192)}
    demand = Demand(
        "a",
        L1,
        Tier.PLANNED,
        6,
        16,
        16,
        need_kwh=4 * E16,
        deadline=NOW + timedelta(hours=48),
    )

    outcome = run([demand], prices, block=2)

    assert shape(outcome) == {q(t): 16.0 for t in range(4)}
    assert outcome.allocations == {"a": 16.0}
    assert outcome.proven is True


def test_optimize_the_overfill_bound_uses_the_highest_current_of_either_quarter() -> (
    None
):
    # Pinned: the bound is need + e * max(first-quarter, later) current, which is the
    # plan's `max_current` through decide; here only the first quarter can run.
    demand = Demand("a", L1, Tier.PLANNED, 6, 4, 16, need_kwh=0.1, deadline=q(1))

    outcome = run([demand], priced(1))

    assert shape(outcome) == {q(0): 6.0}
    assert outcome.plans["a"].reachable is True


# --- the tiers on the leftover (T4) ------------------------------------------------


def tier_demand(charger_id: str, tier: Tier, **kwargs: float) -> Demand:
    low = kwargs.get("low", 6)
    high = kwargs.get("high", 16)
    if tier is Tier.PLANNED:
        return Demand(
            charger_id, L1, tier, low, high, high, need_kwh=E16, deadline=q(4)
        )
    return Demand(charger_id, L1, tier, low, high, high)


QUARTER_ZERO_TIERS = [Tier.SMART_OFF, Tier.CHARGE_NOW, Tier.SPARE]


def tiered(tier: Tier, demands: list[Demand]) -> list[Demand]:
    return [
        Demand(
            d.charger_id,
            d.phases,
            tier,
            d.min_current,
            d.max_current,
            d.now_max_current,
        )
        for d in demands
    ]


@pytest.mark.parametrize("tier", QUARTER_ZERO_TIERS, ids=lambda t: t.name)
@pytest.mark.parametrize(
    ("claims", "capacity", "expected"),
    [
        ([("a", 6, 16, L1), ("b", 6, 16, L1)], caps(16, 16, 16), {"a": 8.0, "b": 8.0}),
        (
            [("p", 10, 10, L1), ("c", 6, 16, L1)],
            caps(16, 16, 16),
            {"p": 10.0, "c": 6.0},
        ),
        (
            [
                ("a", 6, 16, frozenset({Phase.L1, Phase.L2})),
                ("b", 6, 16, L1),
                ("c", 6, 16, L2),
            ],
            caps(16, 16, 16),
            {"a": 8.0, "b": 8.0, "c": 8.0},
        ),
        (
            [("a", 6, 8, L1), ("b", 6, 16, L1), ("c", 6, 16, L1)],
            caps(30, 30, 30),
            {"a": 8.0, "b": 11.0, "c": 11.0},
        ),
    ],
    ids=["even-split", "on-off-first", "mixed-phases", "limited-first"],
)
def test_optimize_every_quarter_zero_tier_reproduces_the_named_share_results(
    tier: Tier,
    claims: list[tuple[str, float, float, frozenset[Phase]]],
    capacity: dict[Phase, float],
    expected: dict[str, float],
) -> None:
    demands = [Demand(i, ph, tier, lo, hi, hi) for i, lo, hi, ph in claims]

    outcome = run(demands, None, capacity, capacity)

    assert outcome.allocations == expected


@pytest.mark.parametrize(
    ("high", "low"),
    [
        (Tier.SMART_OFF, Tier.CHARGE_NOW),
        (Tier.SMART_OFF, Tier.PLANNED),
        (Tier.SMART_OFF, Tier.SPARE),
        (Tier.CHARGE_NOW, Tier.PLANNED),
        (Tier.CHARGE_NOW, Tier.SPARE),
        (Tier.PLANNED, Tier.SPARE),
    ],
    ids=lambda t: t.name,
)
def test_optimize_a_lower_tier_takes_only_what_a_higher_tier_leaves(
    high: Tier, low: Tier
) -> None:
    demands = [
        tier_demand("lo", low),
        tier_demand("hi", high),
    ]  # lower tier listed first
    prices = priced(0.1, 1, 1, 1)

    outcome = run(demands, prices, caps(16, 16, 16), caps(16, 16, 16), block=1)

    assert outcome.allocations == {"lo": 0.0, "hi": 16.0}


def test_optimize_a_lower_tier_gets_the_leftover_on_another_phase() -> None:
    demands = [
        Demand("lo", L2, Tier.SPARE, 6, 16, 16),
        Demand("hi", L1, Tier.CHARGE_NOW, 6, 16, 16),
    ]

    assert run(demands).allocations == {"lo": 16.0, "hi": 16.0}


def test_optimize_smart_off_current_holds_in_the_later_quarters() -> None:
    off = Demand("s", L1, Tier.SMART_OFF, 10, 10, 10)
    car = planned("p", need=3.0, quarters=16)

    outcome = run([off, car], {q(t): 1.0 for t in range(16)})

    assert outcome.allocations == {"s": 10.0, "p": 6.0}
    assert {c for c in shape(outcome, "p").values()} == {6.0}


def test_optimize_two_planned_cars_at_equal_prices_run_now_evenly() -> None:
    cars = [planned("a", need=E16), planned("b", need=E16)]

    outcome = run(cars, priced(1, 1, 1, 1))

    assert outcome.allocations == {"a": 8.0, "b": 8.0}


def test_optimize_the_quarter_zero_stages_ignore_a_deadline_and_a_need() -> None:
    demand = Demand(
        "a", L1, Tier.CHARGE_NOW, 6, 16, 16, need_kwh=0.0001, deadline=NOW - 5 * QUARTER
    )

    assert run([demand]).allocations == {"a": 16.0}


def test_optimize_equal_minimum_and_maximum_is_on_off_even_below_the_first_quarter_limit() -> (
    None
):
    # Pinned: `min == max` makes a demand on/off in every tier; `now_max_current` only
    # lowers the first quarter, it never lifts an on/off demand above its current.
    demand = Demand("a", L1, Tier.CHARGE_NOW, 6, 6, 16)

    assert run([demand]).allocations == {"a": 6.0}


# --- Demand, Outcome, SolverError, NODE_LIMIT ----------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"phases": frozenset()}, "phases"),
        ({"min_current": -1}, "min_current"),
        ({"min_current": True}, "min_current"),
        ({"max_current": float("nan")}, "max_current"),
        ({"max_current": "16"}, "max_current"),
        ({"now_max_current": True}, "now_max_current"),
        ({"now_max_current": -0.5}, "now_max_current"),
        ({"need_kwh": float("inf")}, "need_kwh"),
        ({"need_kwh": -1.0}, "need_kwh"),
        ({"deadline": datetime(2026, 10, 22, 5, 0)}, "deadline"),  # noqa: DTZ001 - deliberately naive
        ({"tier": 2}, "tier"),
        ({"tier": None}, "tier"),
        ({"tier": Tier.PLANNED}, "deadline"),
    ],
)
def test_demand_rejects_each_invalid_field_naming_it(
    kwargs: dict[str, object], field: str
) -> None:
    fields: dict[str, object] = {
        "charger_id": "a",
        "phases": L1,
        "tier": Tier.CHARGE_NOW,
        "min_current": 6,
        "max_current": 16,
        "now_max_current": 16,
    }
    fields.update(kwargs)

    with pytest.raises(ValueError, match=f"^{field}"):
        Demand(**fields)  # type: ignore[arg-type]  # deliberately invalid values


@pytest.mark.parametrize(
    "demand",
    [
        lambda: Demand("a", L1, Tier.SPARE, 0, 0, 0),
        lambda: Demand("a", L1, Tier.PLANNED, 6, 4, 0, deadline=NOW),
        lambda: Demand("a", L1, Tier.CHARGE_NOW, 6, 0, 16),
        lambda: Demand("a", L1, Tier.CHARGE_NOW, 6, 16, 16, deadline=NOW),
        lambda: Demand("a", L1, Tier.CHARGE_NOW, 7.5, 7.5, 16.0),
    ],
    ids=[
        "all-zero",
        "max-below-min",
        "max-zero",
        "deadline-ignored",
        "fractional-on-off",
    ],
)
def test_demand_accepts_zero_and_a_maximum_below_the_minimum(
    demand: Callable[[], Demand],
) -> None:
    assert demand().charger_id == "a"


@pytest.mark.parametrize(
    ("low", "high", "now_high"),
    [
        (6.5, 16.0, 16.0),
        (7.5, 4.0, 16.0),
        (7.5, 16.0, 4.0),
        (0.5, 16.0, 16.0),
        (6.000001, 16.0, 16.0),
    ],
    ids=[
        "above",
        "later-off-now-on",
        "later-on-now-off",
        "below-one",
        "just-fractional",
    ],
)
@pytest.mark.parametrize("tier", list(Tier))
def test_demand_rejects_a_fractional_minimum_on_a_charger_that_can_run_adjustably(
    tier: Tier, low: float, high: float, now_high: float
) -> None:
    # An adjustable charger runs in whole amps, so 7.5 A as a minimum would really mean
    # 8 A while the fit tests count it at 7.5 A and the solve could not be satisfied.
    deadline = NOW if tier is Tier.PLANNED else None

    with pytest.raises(ValueError, match=r"^min_current.*whole"):
        Demand("a", L1, tier, low, high, now_high, deadline=deadline)


@pytest.mark.parametrize(
    ("low", "high", "now_high"),
    [
        (7.5, 7.5, 7.5),
        (7.5, 7.5, 16.0),
        (7.5, 5.0, 5.0),
        (7.5, 0.0, 0.0),
        (6.0, 16.0, 16.0),
    ],
    ids=[
        "on-off",
        "on-off-high-now",
        "user-limited-on-off",
        "limited-to-zero",
        "whole",
    ],
)
def test_demand_accepts_a_fractional_minimum_only_when_it_can_never_run_adjustably(
    low: float, high: float, now_high: float
) -> None:
    # `decide` builds the second and third shapes: an on/off pump at 7.5 A under a user
    # limit of 5 A is off in every quarter, and that is not an error.
    assert Demand("a", L1, Tier.CHARGE_NOW, low, high, now_high).min_current == low


def test_demand_is_frozen() -> None:
    demand = Demand("a", L1, Tier.CHARGE_NOW, 6, 16, 16)

    with pytest.raises(FrozenInstanceError):
        demand.min_current = 8  # type: ignore[misc]  # frozen dataclass


def test_the_tier_members_are_ordered_by_precedence() -> None:
    assert [t.name for t in Tier] == ["SMART_OFF", "CHARGE_NOW", "PLANNED", "SPARE"]
    assert [int(t) for t in Tier] == [0, 1, 2, 3]


def test_solver_error_carries_status_and_message_and_is_reexported() -> None:
    error = SolverError(2, "Infeasible")

    assert str(error) == "HiGHS status 2: Infeasible"
    assert (error.status, error.message) == (2, "Infeasible")
    assert isinstance(error, RuntimeError)
    assert ev.SolverError is SolverError


def test_node_limit_is_a_power_of_two_multiple_of_a_thousand() -> None:
    assert isinstance(NODE_LIMIT, int)
    assert NODE_LIMIT % 1000 == 0
    multiple = NODE_LIMIT // 1000
    assert multiple & (multiple - 1) == 0
    assert NODE_LIMIT == 8000


def test_outcome_is_frozen_and_holds_its_three_fields() -> None:
    outcome = Outcome({"a": 1.0}, {}, True)

    assert (outcome.allocations, outcome.plans, outcome.proven) == (
        {"a": 1.0},
        {},
        True,
    )
    with pytest.raises(FrozenInstanceError):
        outcome.proven = False  # type: ignore[misc]  # frozen dataclass


# --- optimize: arguments, errors, capacities (T7) -------------------------------------


def test_optimize_of_no_demands_returns_before_any_check() -> None:
    naive = datetime(2026, 1, 1)  # noqa: DTZ001 - deliberately naive

    outcome = optimize([], naive, {}, {}, {}, 230.0, 2, None, "Mars/Base")

    assert outcome == Outcome({}, {}, True)


@pytest.mark.parametrize(
    ("now", "timezone", "error"),
    [
        (NOW.replace(tzinfo=None), "UTC", ValueError),
        (NOW, "Mars/Base", ZoneInfoNotFoundError),
    ],
    ids=["naive-now", "unknown-zone"],
)
def test_optimize_rejects_a_naive_now_and_an_unknown_zone(
    now: datetime, timezone: str, error: type[Exception]
) -> None:
    demand = Demand("a", L1, Tier.CHARGE_NOW, 6, 16, 16)

    with pytest.raises(error):
        optimize([demand], now, {}, CAP25, CAP25, 230.0, 2, None, timezone)


@pytest.mark.parametrize(
    "tiers",
    [(Tier.CHARGE_NOW, Tier.CHARGE_NOW), (Tier.CHARGE_NOW, Tier.SPARE)],
    ids=["same-tier", "different-tiers"],
)
def test_optimize_rejects_a_repeated_charger_id(tiers: tuple[Tier, Tier]) -> None:
    demands = [Demand("a", L1, t, 6, 16, 16) for t in tiers]

    with pytest.raises(ValueError, match="unique"):
        run(demands)


@pytest.mark.parametrize(
    "capacity",
    [{Phase.L1: -5.0, Phase.L2: -5.0, Phase.L3: -5.0}, {}],
    ids=["negative", "absent"],
)
def test_optimize_treats_negative_and_absent_capacity_as_zero(
    capacity: dict[Phase, float],
) -> None:
    demands = [
        Demand("c", L1, Tier.CHARGE_NOW, 6, 16, 16),
        planned("p", need=E16),
        Demand("s", L1, Tier.SPARE, 6, 16, 16),
        Demand("o", L1, Tier.SMART_OFF, 6, 16, 16),
    ]

    outcome = run(demands, priced(1, 1, 1, 1), capacity, capacity)

    assert outcome.allocations == {"c": 0.0, "p": 0.0, "s": 0.0, "o": 0.0}
    assert outcome.plans["p"].quarters == ()
    assert outcome.plans["p"].reachable is False
    assert outcome.plans["p"].planned_kwh == 0.0


def test_optimize_does_not_mutate_its_arguments() -> None:
    demands = [planned("a"), Demand("c", L1, Tier.CHARGE_NOW, 6, 16, 16)]
    prices = priced(1, 2, 3, 4)
    capacity = caps(20, 20, 20)
    now_capacity = caps(18, 18, 18)
    before = (list(demands), dict(prices), dict(capacity), dict(now_capacity))

    run(demands, prices, capacity, now_capacity)

    assert (demands, prices, capacity, now_capacity) == before


def test_optimize_is_idempotent_and_returns_every_id() -> None:
    demands = [
        Demand("c", L1, Tier.CHARGE_NOW, 6, 16, 16),
        planned("a"),
        Demand("s", L2, Tier.SPARE, 6, 16, 16),
    ]
    prices = priced(0.3, 0.1, 0.2, 0.1)

    first = run(demands, prices, block=2)

    assert run(demands, prices, block=2) == first
    assert list(first.allocations) == ["c", "a", "s"]
    assert list(first.plans) == ["a"]


def test_optimize_main_showcase_runs_and_prints(
    capsys: pytest.CaptureFixture[str],
) -> None:
    showcase()

    out = capsys.readouterr().out
    assert "proven optimal: True" in out
    assert "no room" in out


# --- rounding checks (C4): a limit broken after rounding raises SolverError ------------


def test_a_current_outside_its_limits_after_rounding_raises_with_status_zero() -> None:
    demand = Demand("a", L1, Tier.CHARGE_NOW, 6, 16, 16)

    with pytest.raises(SolverError) as caught:
        _current(demand, True, 17.0)

    assert caught.value.status == 0
    assert "a solved at 17.0 A" in caught.value.message
    assert _current(demand, True, 0.2) == 0.0
    assert _current(demand, True, 16.0) == 16.0


def test_a_phase_over_its_capacity_after_rounding_raises_with_status_zero() -> None:
    demand = Demand("a", L1, Tier.CHARGE_NOW, 6, 16, 16)
    limits = {phase: 10.0 for phase in Phase}

    with pytest.raises(SolverError) as caught:
        _verify([demand], {"a": 12.0}, {}, NOW, limits, limits)

    assert caught.value.status == 0
    assert "L1" in caught.value.message
    _verify([demand], {"a": 10.0}, {}, NOW, limits, limits)


# --- the solver call (A5): single thread, node budget, no time limit -----------------


def test_optimize_never_passes_a_time_limit_and_runs_single_threaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = optimize_module.milp
    seen: list[dict[str, object]] = []

    def spy(*args: object, **kwargs: object) -> object:
        options = kwargs["options"]
        assert isinstance(options, dict)
        seen.append(dict(options))
        return real(*args, **kwargs)

    monkeypatch.setattr(optimize_module, "milp", spy)

    run([planned(), Demand("c", L1, Tier.CHARGE_NOW, 6, 16, 16)], priced(1, 2, 3, 4))

    assert seen
    for options in seen:
        assert "time_limit" not in options
        assert options["threads"] == 1
        assert options["node_limit"] == NODE_LIMIT
        assert options["mip_rel_gap"] == 0.0
        assert options["presolve"] is True


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (
            4,
            "The HiGHS status code was not recognized. (HiGHS Status 16: Solution limit reached)",
        ),
        (1, "Iteration or time limit reached"),
    ],
    ids=["status-four", "status-one"],
)
def test_optimize_a_node_budget_stop_with_a_point_is_not_proven(
    monkeypatch: pytest.MonkeyPatch, status: int, message: str
) -> None:
    real = optimize_module.milp

    def stopped(*args: object, **kwargs: object) -> SimpleNamespace:
        result = real(*args, **kwargs)
        return SimpleNamespace(
            status=status, message=message, x=result.x, mip_node_count=NODE_LIMIT
        )

    monkeypatch.setattr(optimize_module, "milp", stopped)

    outcome = run([planned()], priced(1, 2, 3, 4))

    assert outcome.proven is False
    assert shape(outcome) == {q(0): 16.0}


def test_optimize_a_status_four_below_the_node_limit_is_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failed(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            status=4,
            message="Solution limit reached",
            x=np.array([1.0]),
            mip_node_count=NODE_LIMIT - 1,
        )

    monkeypatch.setattr(optimize_module, "milp", failed)

    with pytest.raises(SolverError) as caught:
        run([Demand("a", L1, Tier.CHARGE_NOW, 6, 16, 16)])

    assert caught.value.status == 4


# --- T1: no limit is ever broken (property) ---------------------------------------------


def _limit_hi(demand: Demand, first: bool) -> float:
    """The highest current of a demand in a quarter, written out independently."""
    top = demand.now_max_current if first else demand.max_current
    if top < demand.min_current:
        return 0.0
    return demand.min_current if demand.min_current == demand.max_current else top


def _random_demands(rng: random.Random, now: datetime) -> list[Demand]:
    phase_sets = [L1, L2, frozenset({Phase.L1, Phase.L2}), ALL]
    demands: list[Demand] = []
    for index in range(rng.randint(1, 4)):
        tier = rng.choice(list(Tier))
        kind = rng.choice(["adjustable", "adjustable", "on-off", "inverted"])
        # A fractional minimum is only valid as an on/off current.
        low = rng.choice(
            [0, 1, 6, 6, 7.5, 10] if kind == "on-off" else [0, 1, 6, 6, 10]
        )
        high = {
            "adjustable": rng.choice([low, 8, 16, 16]),
            "on-off": low,
            "inverted": max(0, low - 2),
        }[kind]
        first = rng.choice([high, high, rng.choice([0, 4, 8, 16])])
        deadline = None
        if tier is Tier.PLANNED:
            deadline = now + timedelta(minutes=rng.choice([-30, 0, 5, 15, 40, 60, 100]))
        demands.append(
            Demand(
                f"d{index}",
                rng.choice(phase_sets),
                tier,
                float(low),
                float(high),
                float(first),
                need_kwh=rng.choice([0.0, 0.3, 1.0, 3.0]),
                deadline=deadline,
            )
        )
    return demands


def _random_capacity(rng: random.Random) -> dict[Phase, float]:
    return {
        phase: rng.choice([-5.0, 0.0, 6.0, 10.5, 16.0, 25.0, 40.0])
        for phase in Phase
        if rng.random() > 0.15
    }


@settings(deadline=None, max_examples=80, derandomize=True)
@given(st.randoms(use_true_random=False))
def test_optimize_never_breaks_a_limit(rng: random.Random) -> None:
    now = NOW + timedelta(minutes=rng.choice([0, 0, 7, 14]))
    demands = _random_demands(rng, now)
    capacity = _random_capacity(rng)
    now_capacity = _random_capacity(rng)
    prices = {
        q(t): rng.choice([0.1, 0.3, 0.6, -0.1])
        for t in range(12)
        if rng.random() > 0.25
    }
    outcome = optimize(
        demands,
        now,
        prices,
        capacity,
        now_capacity,
        230.0,
        rng.randint(1, 4),
        rng.choice([None, 0.4]),
        "Europe/Copenhagen",
    )
    clipped_later = {p: max(0.0, capacity.get(p, 0.0)) for p in Phase}
    clipped_now = {p: max(0.0, now_capacity.get(p, 0.0)) for p in Phase}

    assert list(outcome.allocations) == [d.charger_id for d in demands]
    assert set(outcome.plans) == {
        d.charger_id for d in demands if d.tier is Tier.PLANNED
    }
    drawn_now = {p: 0.0 for p in Phase}
    drawn_later: dict[datetime, dict[Phase, float]] = {}
    smart_off = {p: 0.0 for p in Phase}
    for d in demands:
        current = outcome.allocations[d.charger_id]
        for phase in d.phases:
            drawn_now[phase] += current
            if d.tier is Tier.SMART_OFF:
                smart_off[phase] += current
        _assert_current(d, current, True)
        if d.tier is not Tier.PLANNED:
            continue
        plan = outcome.plans[d.charger_id]
        first = 0.0
        for quarter in plan.quarters:
            start = quarter.start.astimezone(UTC)
            assert floor_to_quarter(now) <= start < d.deadline  # type: ignore[operator]  # PLANNED has a deadline
            assert (start - NOW) % QUARTER == timedelta(0)
            _assert_current(d, quarter.current, start == floor_to_quarter(now))
            if start == floor_to_quarter(now):
                first = quarter.current
            else:
                slot = drawn_later.setdefault(start, {p: 0.0 for p in Phase})
                for phase in d.phases:
                    slot[phase] += quarter.current
        assert current == first
    for phase in Phase:
        assert drawn_now[phase] <= clipped_now[phase] + 1e-6
        assert smart_off[phase] <= clipped_later[phase] + 1e-6
        for slot in drawn_later.values():
            assert slot[phase] + smart_off[phase] <= clipped_later[phase] + 1e-6


def _assert_current(demand: Demand, current: float, first: bool) -> None:
    top = _limit_hi(demand, first)
    if current == 0:
        return
    assert demand.min_current - 1e-9 <= current <= top + 1e-9
    if demand.min_current == demand.max_current:
        assert current == demand.min_current
    else:
        assert current == int(current)


# --- T2: the lexicographic tuple equals an exhaustive enumeration's -----------------------

LEVELS_ADJUSTABLE = (0.0, 6.0, 7.0)
LEVELS_ON_OFF = (0.0, 10.0)
LEVELS_SIX = (0.0, 6.0)


def _tau(t: int, n: int, count: int, total: int, is_priced: bool) -> int:
    return (t + 1) * (count - n) if is_priced else (total - t) * (n + 1)


def level_tuple(
    demands: list[Demand],
    grid: list[list[float]],
    prices: dict[datetime, float],
    block: int,
    cap: float | None,
    total: int,
) -> tuple[float, ...]:
    """The levels P1 to P7 of one assignment, written out from the plan's model."""
    levels: list[float] = []
    energy = [energy_per_quarter_kwh(1.0, len(d.phases), 230.0) for d in demands]
    delivered = [e * sum(row) for e, row in zip(energy, grid, strict=True)]
    levels.append(
        sum(
            max(0.0, d.need_kwh - got)
            for d, got in zip(demands, delivered, strict=True)
        )
    )
    unpriced = sum(
        e * i
        for e, row in zip(energy, grid, strict=True)
        for t, i in enumerate(row)
        if q(t) not in prices
    )
    levels.append(unpriced)
    if cap is not None:
        levels.append(
            sum(
                e * i
                for e, row in zip(energy, grid, strict=True)
                for t, i in enumerate(row)
                if q(t) in prices and prices[q(t)] > cap
            )
        )
    if block > 1:
        short = 0
        for row in grid:
            on = [i > 0 for i in row]
            for t, is_on in enumerate(on):
                if (
                    is_on
                    and (t == 0 or not on[t - 1])
                    and not all(on[t + 1 : t + block])
                ):
                    short += 1
        levels.append(float(short))
    levels.append(
        sum(
            prices[q(t)] * e * i
            for e, row in zip(energy, grid, strict=True)
            for t, i in enumerate(row)
            if q(t) in prices
        )
    )
    if q(0) not in prices:
        # An unpriced first quarter keeps only the energy the need forces (P5, amended).
        levels.append(
            sum(e * row[0] for e, row in zip(energy, grid, strict=True) if row)
        )
    on_now = [row[0] > 0 for row in grid if row]
    levels.append(-float(sum(on_now)))
    levels.extend(-float(flag) for flag in on_now)
    # P6: the cars on now that can still be raised share the first quarter, lowest
    # first, then the rest goes in ranking order. Cars at their only current (on/off,
    # or limited to their minimum) are not part of the fill.
    live = [
        n
        for n, d in enumerate(demands)
        if d.min_current != d.max_current and d.now_max_current > d.min_current
    ]
    running = [grid[n][0] for n in live if grid[n] and grid[n][0] > 0]
    levels.append(-min(running) if running else 0.0)
    levels.extend(-(grid[n][0] if grid[n] else 0.0) for n in live)
    count = len(demands)
    levels.append(
        float(
            sum(
                _tau(t, n, count, total, q(t) in prices) * i
                for n, row in enumerate(grid)
                for t, i in enumerate(row)
            )
        )
    )
    return tuple(levels)


def _assignments(levels: tuple[float, ...], quarters: int) -> list[tuple[float, ...]]:
    return list(itertools.product(levels, repeat=quarters))


@settings(deadline=None, max_examples=60, derandomize=True)
@given(st.randoms(use_true_random=False))
def test_optimize_matches_a_brute_force_enumeration_level_by_level(
    rng: random.Random,
) -> None:
    count = 2 if rng.random() < 0.75 else 1
    shared = rng.choice([L1, L2, ALL])
    kinds = [rng.choice(["adjustable", "on-off-10", "on-off-6"]) for _ in range(count)]
    sizes = {
        "adjustable": LEVELS_ADJUSTABLE,
        "on-off-10": LEVELS_ON_OFF,
        "on-off-6": LEVELS_SIX,
    }
    horizon = max(
        h
        for h in range(2, 7)
        if h == 2 or math.prod(len(sizes[k]) ** h for k in kinds) <= 30000
    )
    horizon = rng.randint(2, horizon)
    demands: list[Demand] = []
    levels: list[tuple[float, ...]] = []
    for index, kind in enumerate(kinds):
        low, high = {
            "adjustable": (6.0, 7.0),
            "on-off-10": (10.0, 10.0),
            "on-off-6": (6.0, 6.0),
        }[kind]
        demands.append(
            Demand(
                f"d{index}",
                shared if rng.random() < 0.7 else rng.choice([L1, L2, ALL]),
                Tier.PLANNED,
                low,
                high,
                high,
                need_kwh=rng.choice([0.0, 0.3, 0.7, 1.0, 1.4, 2.0]),
                deadline=q(rng.randint(1, horizon)),
            )
        )
        levels.append(sizes[kind])
    prices = {
        q(t): rng.choice([0.1, 0.2, 0.5]) for t in range(horizon) if rng.random() > 0.3
    }
    block = rng.randint(1, 3)
    cap = rng.choice([None, 0.3])
    amps = [0, 6, 7, 10, 12, 13, 14, 16, 20]
    later = {p: float(rng.choice(amps)) for p in Phase}
    first = {p: float(rng.choice(amps)) for p in Phase}

    outcome = optimize(
        demands, NOW, prices, later, first, 230.0, block, cap, "Europe/Copenhagen"
    )

    windows = [len(quarter_starts(NOW, d.deadline)) for d in demands]  # type: ignore[arg-type]  # PLANNED has a deadline
    total = max(windows)
    energy = [energy_per_quarter_kwh(1.0, len(d.phases), 230.0) for d in demands]
    best: tuple[float, ...] | None = None
    options = [_assignments(lv, w) for lv, w in zip(levels, windows, strict=True)]
    for grid in itertools.product(*options):
        if not _feasible(demands, grid, later, first, energy):
            continue
        key = level_tuple(demands, [list(r) for r in grid], prices, block, cap, total)
        if best is None or _lex_less(key, best):
            best = key
    assert best is not None
    if not outcome.proven:
        return
    found = [
        [shape(outcome, d.charger_id).get(q(t), 0.0) for t in range(w)]
        for d, w in zip(demands, windows, strict=True)
    ]
    got = level_tuple(demands, found, prices, block, cap, total)
    assert got == pytest.approx(best, abs=1e-6)


def _feasible(
    demands: list[Demand],
    grid: tuple[tuple[float, ...], ...],
    later: dict[Phase, float],
    first: dict[Phase, float],
    energy: list[float],
) -> bool:
    for t in range(max(len(row) for row in grid)):
        for phase in Phase:
            drawn = sum(
                row[t]
                for d, row in zip(demands, grid, strict=True)
                if phase in d.phases and t < len(row)
            )
            if drawn > (first if t == 0 else later)[phase] + 1e-9:
                return False
    for d, row, e in zip(demands, grid, energy, strict=True):
        if e * sum(row) > d.need_kwh + e * d.max_current + 1e-9:
            return False
    return True


def _lex_less(a: tuple[float, ...], b: tuple[float, ...]) -> bool:
    for x, y in zip(a, b, strict=True):
        if abs(x - y) > 1e-6:
            return x < y
    return False


# --- the planned tier's even first quarter (A3, amended at the step-6 halt) ------------


def test_optimize_three_planned_cars_fill_the_first_quarter_evenly_up_to_each_limit() -> (
    None
):
    # A is limited to 8 A, B and C reach 16 A, 30 A on the phase, equal prices and
    # needs: the first quarter is 8 / 11 / 11, not 8 / 14 / 8 (a single max-min made
    # only the smallest allocation as large as possible).
    cars = [
        planned("a", need=1.84, high=8),
        planned("b", need=1.84),
        planned("c", need=1.84),
    ]

    outcome = run(cars, priced(0.2, 0.2, 0.2, 0.2), caps(30, 30, 30), caps(30, 30, 30))

    assert outcome.allocations == {"a": 8.0, "b": 11.0, "c": 11.0}


def test_optimize_two_planned_cars_forced_into_an_unpriced_first_quarter_share_it() -> (
    None
):
    # No prices at all and a need that only fits if both cars run in every quarter:
    # the forced first quarter is split 8 / 8, not 16 / 0.
    cars = [planned("a", need=1.84), planned("b", need=1.84)]

    outcome = run(cars, {})

    assert outcome.allocations == {"a": 8.0, "b": 8.0}


def test_optimize_an_unpriced_first_quarter_is_not_filled_beyond_what_the_need_forces() -> (
    None
):
    # Latest-first still rules: with room to spare, only the quarters the need forces
    # are used, and none is pulled into the unpriced first quarter.
    outcome = run([planned("a", need=E16)], {})

    assert outcome.allocations == {"a": 0.0}


def test_optimize_planned_cars_that_cannot_all_rise_share_the_remainder_in_ranking_order() -> (
    None
):
    # 20 A for three 6 to 16 A cars: all can sit at 6 A, none can all reach 7 A, and each
    # alone could reach 8 A. The level stops there and the 2 A left goes to the first.
    cars = [planned(n, need=1.84) for n in "abc"]

    outcome = run(cars, priced(0.2, 0.2, 0.2, 0.2), caps(20, 20, 20), caps(20, 20, 20))

    assert outcome.allocations == {"a": 8.0, "b": 6.0, "c": 6.0}


def test_optimize_planned_cars_on_mixed_phases_fill_evenly_by_phase() -> None:
    # A on L1+L2, B on L1, C on L2, 16 A per phase: 8 / 8 / 8, as the quarter-0 tiers.
    cars = [
        planned("a", need=1.84, phases=frozenset({Phase.L1, Phase.L2})),
        planned("b", need=1.84),
        planned("c", need=1.84, phases=L2),
    ]

    outcome = run(cars, priced(0.2, 0.2, 0.2, 0.2))

    assert outcome.allocations == {"a": 8.0, "b": 8.0, "c": 8.0}


# --- the planned tier's even first quarter: step 5 re-run (A3, A2, A5, A6) ----------------

E1 = energy_per_quarter_kwh(1.0, 1, 230.0)  # 0.0575 kWh per amp-quarter on one phase
E3 = energy_per_quarter_kwh(1.0, 3, 230.0)  # the same on three phases


@pytest.mark.parametrize("reverse", [False, True], ids=["a first", "b first"])
def test_optimize_an_unpriced_first_quarter_forced_to_one_car_switches_on_the_better_ranked(
    reverse: bool,
) -> None:
    # The later quarters hold 48 A-quarters against 54 needed, so the first quarter must
    # carry 6 A and only one car fits there: the better-ranked one, whatever its id.
    a = planned("a", need=1.84)
    b = planned("b", need=energy_per_quarter_kwh(22, 1, 230.0))
    cars = [b, a] if reverse else [a, b]

    outcome = run(cars, priced(None, 0.2, 0.2, 0.2))

    first = cars[0].charger_id
    second = cars[1].charger_id
    assert outcome.allocations == {first: 6.0, second: 0.0}


def test_optimize_a_forced_expensive_first_quarter_is_split_evenly() -> None:
    # The capacity of the cheap quarter is exactly the need of one car, so the dear first
    # quarter must carry 16 A; the split costs the same whatever it is, so it is even.
    cars = [planned("a", need=E16, quarters=2), planned("b", need=E16, quarters=2)]

    outcome = run(cars, priced(0.5, 0.1))

    assert outcome.allocations == {"a": 8.0, "b": 8.0}
    assert shape(outcome, "a")[q(1)] == 8.0
    assert shape(outcome, "b")[q(1)] == 8.0


def test_optimize_the_even_split_never_pulls_a_car_into_an_expensive_first_quarter() -> (
    None
):
    # `a` has only the first quarter, so it alone is on now; switching `b` on at 6 A or
    # splitting 8 / 8 would cost more than the cheap later quarters.
    cars = [
        planned("a", need=energy_per_quarter_kwh(10, 1, 230.0), quarters=1),
        planned("b", need=E16),
    ]

    outcome = run(cars, priced(0.5, 0.1, 0.1, 0.1))

    assert outcome.allocations == {"a": 10.0, "b": 0.0}
    assert q(0) not in shape(outcome, "b")


def test_optimize_planned_cars_tied_at_a_cap_leave_the_rest_to_the_uncapped_car() -> (
    None
):
    # Two cars reach their 10 A ceiling in the first round together; the third carries
    # on alone in the second round and stops at what is left.
    cars = [
        planned("a", need=1.84, now_high=10),
        planned("b", need=1.84, now_high=10),
        planned("c", need=1.84),
    ]

    outcome = run(cars, priced(0.2, 0.2, 0.2, 0.2), caps(34, 34, 34), caps(34, 34, 34))

    assert outcome.allocations == {"a": 10.0, "b": 10.0, "c": 14.0}


def test_optimize_a_capped_first_car_passes_the_ranking_remainder_on() -> None:
    # The level stops at 6 A and every car could rise alone, so none saturates: the
    # remainder goes in ranking order, and the first car is capped at 7 A.
    cars = [
        planned("a", need=E16, now_high=7),
        planned("b", need=E16),
        planned("c", need=E16),
    ]

    outcome = run(cars, priced(0.2, 0.2, 0.2, 0.2), caps(20, 20, 20), caps(20, 20, 20))

    assert outcome.allocations == {"a": 7.0, "b": 7.0, "c": 6.0}


def test_optimize_planned_cars_where_one_phase_binds_raise_the_free_phase_car_to_its_top() -> (
    None
):
    # L1 binds at 10 + 10; `c` alone on L2 is free at the first level and then rises to
    # its own top.
    cars = [
        planned("a", need=1.84, phases=frozenset({Phase.L1, Phase.L2})),
        planned("b", need=E16),
        planned("c", need=E16, phases=L2),
    ]

    outcome = run(cars, priced(0.2, 0.2, 0.2, 0.2), caps(20, 30, 16), caps(20, 30, 16))

    assert outcome.allocations == {"a": 10.0, "b": 10.0, "c": 16.0}


def test_optimize_an_on_off_car_ranked_first_keeps_its_slot_and_the_adjustable_takes_the_rest() -> (
    None
):
    # Two of the three fit at their minimum (22 A > 21 A): the on/off car and `a`. The
    # on/off car is not part of the fill but uses room, so `a` rises to 11 A.
    cars = [
        planned("p", need=2 * energy_per_quarter_kwh(10, 1, 230.0), low=10, high=10),
        planned("a", need=1.84),
        planned("b", need=1.84),
    ]

    outcome = run(cars, priced(0.2, 0.2, 0.2, 0.2), caps(30, 30, 30), caps(21, 21, 21))

    assert outcome.allocations == {"p": 10.0, "a": 11.0, "b": 0.0}


def test_optimize_a_fractional_on_off_car_lowers_the_even_level_to_an_exact_fit() -> (
    None
):
    # 29.5 A leaves 22 A for two adjustable cars beside a 7.5 A on/off car: 11 / 11,
    # neither rounding 7.5 up (level 10) nor leaving the level unfloored (12).
    cars = [
        planned("p", need=2 * energy_per_quarter_kwh(7.5, 1, 230.0), low=7.5, high=7.5),
        planned("a", need=1.84),
        planned("b", need=1.84),
    ]

    outcome = run(
        cars, priced(0.2, 0.2, 0.2, 0.2), caps(29.5, 29.5, 29.5), caps(29.5, 29.5, 29.5)
    )

    assert outcome.allocations == {"p": 7.5, "a": 11.0, "b": 11.0}


def test_optimize_an_unpriced_first_quarter_takes_the_smaller_fractional_on_off_car() -> (
    None
):
    # The need forces at least 7.5 A into the first quarter; the other choice would force
    # `a` in at 8 A. The held total is a float, so it is held with the float tolerance.
    cars = [
        planned("a", need=1.84),
        planned("p", need=3 * energy_per_quarter_kwh(7.5, 1, 230.0), low=7.5, high=7.5),
    ]

    outcome = run(cars, priced(None, 0.2, 0.2, 0.2))

    assert outcome.allocations == {"a": 0.0, "p": 7.5}


def test_optimize_planned_cars_all_capped_below_the_room_get_their_caps() -> None:
    cars = [planned(name, need=1.84, now_high=8) for name in "abc"]

    outcome = run(cars, priced(0.2, 0.2, 0.2, 0.2), caps(30, 30, 30), caps(30, 30, 30))

    assert outcome.allocations == {"a": 8.0, "b": 8.0, "c": 8.0}


@pytest.mark.parametrize(
    ("cars", "capacity", "expected"),
    [
        pytest.param(
            [planned("a", need=1.84), planned("b", need=1.84, low=10)],
            18,
            {"a": 8.0, "b": 10.0},
            id="unequal minimums level from the lowest",
        ),
        pytest.param(
            [planned("a", need=E16, now_high=7), planned("b", need=E16)],
            30,
            {"a": 7.0, "b": 16.0},
            id="a car at its first-quarter limit",
        ),
        pytest.param(
            [planned("a", need=1.84, now_high=6), planned("b", need=1.84)],
            30,
            {"a": 6.0, "b": 16.0},
            id="a car limited to its minimum does not hold the level down",
        ),
        pytest.param(
            [planned("a", need=6 * E1 * 4, low=6, high=6), planned("b", need=1.84)],
            30,
            {"a": 6.0, "b": 16.0},
            id="an on/off car does not hold the level down",
        ),
        pytest.param(
            [planned("a", need=1.84, now_high=10.5), planned("b", need=1.84)],
            30,
            {"a": 10.0, "b": 16.0},
            id="a fractional first-quarter limit is filled to its whole amp",
        ),
        pytest.param(
            [planned("a", need=E16, now_high=4), planned("b", need=E16)],
            30,
            {"a": 0.0, "b": 16.0},
            id="a car off now is left out of the fill",
        ),
    ],
)
def test_optimize_planned_cars_fill_to_their_own_limits_at_equal_prices(
    cars: list[Demand], capacity: float, expected: dict[str, float]
) -> None:
    room = caps(capacity, capacity, capacity)

    outcome = run(cars, priced(0.2, 0.2, 0.2, 0.2), room, room)

    assert outcome.allocations == expected


def test_optimize_a_need_limited_car_saturates_below_its_max_and_the_other_takes_the_rest() -> (
    None
):
    # On cost alone 0 / 16 and 6 / 10 tie; cars on now picks 6 / 10, and `a` is stuck at
    # 6 A only because raising it would break the price level (found by a probe).
    cars = [
        planned("a", need=energy_per_quarter_kwh(6, 1, 230.0)),
        planned("b", need=1.84),
    ]

    outcome = run(cars, priced(0.1, 0.5, 0.5, 0.5))

    assert outcome.allocations == {"a": 6.0, "b": 10.0}


def test_optimize_the_fill_never_moves_current_from_a_cheaper_quarter_into_the_first() -> (
    None
):
    # The cheap second quarter is full at 16 A, so the dear first quarter holds 10 A by
    # cost; only one car fits at 6 A or more and ranking picks `a`.
    cars = [
        planned("a", need=E16, quarters=2),
        planned("b", need=10 * E1, quarters=2),
    ]

    outcome = run(cars, priced(0.2, 0.1))

    assert outcome.allocations == {"a": 10.0, "b": 0.0}
    assert shape(outcome, "a") == {q(0): 10.0, q(1): 6.0}
    assert shape(outcome, "b") == {q(1): 10.0}


def test_optimize_an_unpriced_first_quarter_with_an_odd_forced_total_gives_the_extra_amp_by_rank() -> (
    None
):
    # 13 A are forced into the first quarter and both cars go on: each could rise alone
    # but not both, so the extra amp goes to the better-ranked car.
    cars = [planned(name, need=13 * E1, quarters=2, low=6, high=7) for name in "ab"]

    outcome = run(cars, {}, caps(13, 13, 13), caps(13, 13, 13))

    assert outcome.allocations == {"a": 7.0, "b": 6.0}
    assert shape(outcome, "a") == {q(0): 7.0, q(1): 6.0}
    assert shape(outcome, "b") == {q(0): 6.0, q(1): 7.0}


def test_optimize_the_fill_leaves_every_plan_reachable_with_the_same_total_energy() -> (
    None
):
    cars = [
        planned("a", need=1.84, high=8),
        planned("b", need=1.84),
        planned("c", need=1.84),
    ]

    outcome = run(cars, priced(0.2, 0.2, 0.5, 0.5), caps(30, 30, 30), caps(30, 30, 30))

    assert outcome.allocations == {"a": 8.0, "b": 11.0, "c": 11.0}
    for name in "abc":
        assert outcome.plans[name].reachable
        assert outcome.plans[name].planned_kwh == pytest.approx(1.84)
    assert sum(shape(outcome, name).get(q(0), 0.0) for name in "abc") == 30
    assert sum(shape(outcome, name).get(q(1), 0.0) for name in "abc") == 30


def test_optimize_an_unpriced_first_quarter_holds_the_forced_energy_not_the_forced_amps() -> (
    None
):
    # A three-phase car and a single-phase car each need 12 A-quarters and the later
    # quarter holds 16 A on L1. At least 12 A of the two must run now: 6 / 6, 12 / 0 or
    # 0 / 12. Unpriced quarters are used only as far as the need forces them (A2), and
    # that is measured in kWh: 0 / 12 puts 0.69 kWh there, 6 / 6 puts 1.38 kWh.
    cars = [
        planned("c", need=12 * E3, quarters=2, phases=ALL),
        planned("s", need=12 * E1, quarters=2),
    ]

    outcome = run(cars, {})

    assert outcome.allocations == {"c": 0.0, "s": 12.0}
    assert shape(outcome, "c") == {q(1): 12.0}
    assert outcome.plans["s"].planned_kwh == pytest.approx(12 * E1)


def test_optimize_a_first_quarter_dearer_by_one_price_tick_gets_no_extra_amp() -> None:
    # Moving 1 A from the later quarter into the first costs 0.00001 * 0.0575 kWh, which
    # a hold slack of 1e-6 cannot see; a price of EUR 10 per MWh resolution is such a tick.
    # The cheaper quarter must be filled first however small the difference is.
    cars = [planned("a", need=20 * E1, quarters=2)]

    outcome = run(cars, priced(0.20001, 0.2), caps(14, 14, 14), caps(16, 16, 16))

    assert outcome.allocations == {"a": 6.0}
    assert shape(outcome) == {q(0): 6.0, q(1): 14.0}


def test_optimize_equal_prices_still_leave_the_split_free_after_the_tick_fix() -> None:
    # The same instance at equal prices has no cheaper quarter, so the first quarter is
    # free and the earlier-first tie-break pulls current into it.
    cars = [planned("a", need=20 * E1, quarters=2)]

    outcome = run(cars, priced(0.2, 0.2), caps(14, 14, 14), caps(16, 16, 16))

    assert outcome.allocations == {"a": 14.0}


def test_optimize_the_fill_is_robust_to_solver_integrality_noise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # HiGHS returns an integer as 6.9999995 within its 1e-6 integrality tolerance; a
    # car at 7 A must still be seen as able to rise, or the ranking remainder is lost.
    real = optimize_module.milp

    def noisy(*args: object, **kwargs: object) -> SimpleNamespace:
        result = real(*args, **kwargs)
        x = np.where(result.x > 0.5, result.x - 5e-7, result.x)
        return SimpleNamespace(
            status=result.status,
            message=result.message,
            x=x,
            mip_node_count=getattr(result, "mip_node_count", 0),
        )

    cars = [planned(name, need=1.84) for name in "ab"]
    room = caps(15, 15, 15)

    clean = run(cars, priced(0.2, 0.2, 0.2, 0.2), room, room)
    monkeypatch.setattr(optimize_module, "milp", noisy)
    outcome = run(cars, priced(0.2, 0.2, 0.2, 0.2), room, room)

    assert clean.allocations == {"a": 8.0, "b": 7.0}
    assert outcome.allocations == clean.allocations


def _only_probes(
    monkeypatch: pytest.MonkeyPatch,
    stub: Callable[[Callable[..., object]], Callable[..., object]],
) -> list[bool]:
    """Replace `milp` by `stub(real)` for probe solves only; return the `keep` flags seen."""
    seen: list[bool] = []
    real_solve = optimize_module._Problem._solve
    real_milp = optimize_module.milp

    def solve(
        self: optimize_module._Problem, objective: object, keep: bool = True
    ) -> object:
        seen.append(keep)
        if keep:
            return real_solve(self, objective, keep)  # type: ignore[arg-type]  # a vector
        monkeypatch.setattr(optimize_module, "milp", stub(real_milp))
        try:
            return real_solve(self, objective, keep)  # type: ignore[arg-type]  # a vector
        finally:
            monkeypatch.setattr(optimize_module, "milp", real_milp)

    monkeypatch.setattr(optimize_module._Problem, "_solve", solve)
    return seen


def _probe_case() -> Outcome:
    # Three cars that cannot all rise at once on 20 A: the fill must probe each car.
    cars = [planned(name, need=1.84) for name in "abc"]
    room = caps(20, 20, 20)
    return run(cars, priced(0.2, 0.2, 0.2, 0.2), room, room)


def test_optimize_a_probe_failure_raises_solver_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing(_real: Callable[..., object]) -> Callable[..., object]:
        def milp(*_args: object, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(
                status=2, message="infeasible", x=None, mip_node_count=0
            )

        return milp

    seen = _only_probes(monkeypatch, failing)

    with pytest.raises(SolverError) as caught:
        _probe_case()

    assert False in seen  # the failure came from a probe, not a level
    assert caught.value.status == 2


def test_optimize_a_probe_stopped_at_the_node_budget_is_not_proven(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def stopped(real: Callable[..., object]) -> Callable[..., object]:
        def milp(*args: object, **kwargs: object) -> SimpleNamespace:
            result = real(*args, **kwargs)
            return SimpleNamespace(
                status=1,
                message="Iteration or time limit reached",
                x=result.x,  # type: ignore[attr-defined]  # scipy's OptimizeResult
                mip_node_count=NODE_LIMIT,
            )

        return milp

    seen = _only_probes(monkeypatch, stopped)

    outcome = _probe_case()

    assert False in seen
    assert outcome.proven is False
    assert outcome.allocations == {"a": 8.0, "b": 6.0, "c": 6.0}


def test_optimize_the_fill_issues_the_same_solver_calls_every_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = optimize_module.milp
    calls: list[list[tuple[int, tuple[int, ...], tuple[float, ...]]]] = [[]]

    def spy(*args: object, **kwargs: object) -> object:
        c = np.asarray(kwargs["c"])
        used = np.flatnonzero(c)
        calls[-1].append(
            (len(c), tuple(int(i) for i in used), tuple(float(v) for v in c[used]))
        )
        return real(*args, **kwargs)

    monkeypatch.setattr(optimize_module, "milp", spy)
    cars = [planned(name, need=1.84) for name in "abc"]
    wide = [
        planned("a", need=1.84, high=8),
        planned("b", need=1.84),
        planned("c", need=1.84),
    ]

    outcomes = []
    for demands, room in ((cars, 20), (wide, 30)):
        for _ in range(2):
            calls.append([])
            outcomes.append(
                run(
                    demands,
                    priced(0.2, 0.2, 0.2, 0.2),
                    caps(room, room, room),
                    caps(room, room, room),
                )
            )

    assert calls[1] == calls[2] and calls[1]
    assert calls[3] == calls[4] and calls[3]
    assert outcomes[0] == outcomes[1]
    assert outcomes[2] == outcomes[3]


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        ([0.20001, 0.2], [20001, 20000]),
        ([0.1, 0.2, 0.3], [1, 2, 3]),
        ([0.5], [1]),
        ([-0.2, 0.2, 0.0], [-1, 1, 0]),
        ([0.25, 0.5, 0.75], [1, 2, 3]),
        ([0.1 + 0.2, 0.3], [1, 1]),
    ],
    ids=[
        "one tick apart",
        "tenths",
        "single",
        "negative and zero",
        "quarters",
        "float noise",
    ],
)
def test_price_ticks_finds_the_coarse_grid_of_the_prices(
    values: list[float], expected: list[int]
) -> None:
    assert _price_ticks(values) == expected


@pytest.mark.parametrize(
    "values",
    [[], [0.0, 0.0], [0.123456789, 0.2], [1e-8, 2e-8], [1000.0, 0.00001]],
    ids=["none", "all zero", "grid finer than 1e-7", "tiny prices", "too many ticks"],
)
def test_price_ticks_gives_up_when_the_grid_is_too_fine_or_too_wide(
    values: list[float],
) -> None:
    assert _price_ticks(values) is None
