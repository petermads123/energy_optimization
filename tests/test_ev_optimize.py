"""Tests for energy_optimization.ev.optimize: quarter-0 sharing (T4), limits and errors.

The quarter-0 tiers must reproduce round 1's `share` rule, so round 1's `share` tests are
kept here, driven through `optimize` with one `CHARGE_NOW` demand per claim.
"""

from datetime import UTC, datetime

import pytest

from energy_optimization.ev.model import Phase
from energy_optimization.ev.optimize import Demand, Tier, optimize

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
