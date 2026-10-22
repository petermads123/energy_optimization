"""Tests for energy_optimization.ev.optimize (T1, T2, T4, T6, T7, T8).

Round 1's `share` tests are kept first, driven through `optimize` with one
`CHARGE_NOW` demand per claim: the quarter-0 tiers must reproduce that rule. After them
come the planning stage (hand-checked plans, minimum blocks, price cap, unpriced
quarters, DST), the tiers, `Demand` validation, the solver-failure and node-budget
paths, a brute-force check of the lexicographic tuple and a limits property.
"""

from datetime import UTC, datetime, timedelta

import pytest

from energy_optimization.ev.model import Phase
from energy_optimization.ev.optimize import Demand, Outcome, Tier, optimize
from energy_optimization.ev.planning import energy_per_quarter_kwh

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
