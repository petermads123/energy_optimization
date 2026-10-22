"""Tests for energy_optimization.ev.planning (T8, T9, T10, T12): price planning."""

import itertools
import math
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from energy_optimization.ev.model import Phase, PriceInterval, PriceSeries, Session
from energy_optimization.ev.planning import (
    NeedSource,
    PlanRequest,
    SessionPlan,
    energy_per_quarter_kwh,
    plan_sessions,
    remaining_need_kwh,
)
from energy_optimization.ev.planning import main as showcase
from energy_optimization.ev.quarters import (
    QUARTER,
    next_occurrence,
    quarter_prices,
)

NOW = datetime(2026, 10, 21, 20, 0, tzinfo=UTC)
TZ = "Europe/Copenhagen"
L1 = frozenset({Phase.L1})
L2 = frozenset({Phase.L2})
ALL = frozenset(Phase)
CAP16 = {Phase.L1: 16.0, Phase.L2: 16.0, Phase.L3: 16.0}
E16 = energy_per_quarter_kwh(16, 1, 230.0)  # 0.92 kWh per quarter on one phase


def utc(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=UTC)  # type: ignore[arg-type,misc]  # positional date parts


def prices_from(values: list[float], start: datetime = NOW) -> dict[datetime, float]:
    return {start + QUARTER * index: value for index, value in enumerate(values)}


def request(
    cid: str = "a",
    phases: frozenset[Phase] = L1,
    low: float = 6,
    high: float = 16,
    need: float = 1.84,
    deadline: datetime = NOW + timedelta(hours=2),
) -> PlanRequest:
    return PlanRequest(cid, phases, low, high, need, deadline)


def plan(
    requests: list[PlanRequest],
    prices: dict[datetime, float] | None = None,
    capacity: dict[Phase, float] | None = None,
    block: int = 2,
    cap: float | None = None,
    now: datetime = NOW,
) -> dict[str, SessionPlan]:
    return plan_sessions(
        requests,
        now,
        prices or {},
        capacity or CAP16,
        230.0,
        block,
        cap,
        TZ,
    )


def slots(result: SessionPlan) -> list[int]:
    """The planned quarters as quarter numbers counted from NOW."""
    return [int((q.start.astimezone(UTC) - NOW) / QUARTER) for q in result.quarters]


# --- remaining_need_kwh --------------------------------------------------------


def need_of(**kwargs: object) -> tuple[float, NeedSource]:
    values: dict[str, object] = {"charger_id": "a", "ready_time": time(7, 0)}
    values.update(kwargs)
    return remaining_need_kwh(Session(**values), 30.0)  # type: ignore[arg-type]  # test builds kwargs


def test_remaining_need_subtracts_delivered_from_a_given_energy_need() -> None:
    assert need_of(energy_need_kwh=10.0, delivered_kwh=4.0) == (6.0, NeedSource.ENERGY)


def test_remaining_need_a_given_energy_need_beats_soc_fields() -> None:
    result = need_of(
        energy_need_kwh=10.0,
        delivered_kwh=4.0,
        soc_percent=20,
        target_soc_percent=80,
        battery_capacity_kwh=50,
    )

    assert result == (6.0, NeedSource.ENERGY)


def test_remaining_need_zero_energy_need_is_a_need_not_a_missing_value() -> None:
    result = need_of(
        energy_need_kwh=0.0,
        soc_percent=20,
        target_soc_percent=80,
        battery_capacity_kwh=50,
    )

    assert result == (0.0, NeedSource.ENERGY)


def test_remaining_need_from_soc_ignores_delivered_energy() -> None:
    need, source = need_of(
        soc_percent=50, target_soc_percent=80, battery_capacity_kwh=40, delivered_kwh=5
    )

    assert need == pytest.approx(12.0)
    assert source is NeedSource.SOC


def test_remaining_need_zero_soc_is_a_value_not_a_missing_one() -> None:
    result = need_of(soc_percent=0.0, target_soc_percent=80, battery_capacity_kwh=50)

    assert result == (40.0, NeedSource.SOC)


@pytest.mark.parametrize(
    "present",
    [
        {"target_soc_percent": 80, "battery_capacity_kwh": 50},
        {"soc_percent": 20, "battery_capacity_kwh": 50},
        {"soc_percent": 20, "target_soc_percent": 80},
        {"soc_percent": 20},
        {},
    ],
)
def test_remaining_need_incomplete_soc_information_uses_the_default(
    present: dict[str, float],
) -> None:
    assert need_of(delivered_kwh=4.0, **present) == (26.0, NeedSource.DEFAULT)


@pytest.mark.parametrize(
    ("kwargs", "source"),
    [
        ({"energy_need_kwh": 10.0, "delivered_kwh": 12.0}, NeedSource.ENERGY),
        (
            {"soc_percent": 90, "target_soc_percent": 80, "battery_capacity_kwh": 50},
            NeedSource.SOC,
        ),
        ({"delivered_kwh": 31.0}, NeedSource.DEFAULT),
    ],
)
def test_remaining_need_is_clipped_at_zero(
    kwargs: dict[str, float], source: NeedSource
) -> None:
    assert need_of(**kwargs) == (0.0, source)


def test_remaining_need_with_a_zero_default_is_zero() -> None:
    session = Session("a", time(7, 0))

    assert remaining_need_kwh(session, 0.0) == (0.0, NeedSource.DEFAULT)


def test_remaining_need_does_not_mutate_the_session() -> None:
    session = Session("a", time(7, 0), energy_need_kwh=5.0)
    before = Session("a", time(7, 0), energy_need_kwh=5.0)

    remaining_need_kwh(session, 30.0)

    assert session == before


# --- energy_per_quarter_kwh ----------------------------------------------------


@pytest.mark.parametrize(
    ("current", "phases", "voltage", "expected"),
    [
        (16, 3, 230, 2.76),
        (16, 1, 230, 0.92),
        (6, 1, 230, 0.345),
        (7.5, 1, 230, 0.43125),
        (0, 3, 230, 0.0),
        (16, 1, 200, 0.8),
    ],
)
def test_energy_per_quarter_pins_the_formula_and_its_units(
    current: float, phases: int, voltage: float, expected: float
) -> None:
    assert energy_per_quarter_kwh(current, phases, voltage) == pytest.approx(expected)


# --- plan_sessions: basics -----------------------------------------------------


def test_plan_of_no_requests_is_empty() -> None:
    assert plan([]) == {}


def test_plan_is_keyed_by_charger_id() -> None:
    result = plan([request("a"), request("b", phases=L2)])

    assert set(result) == {"a", "b"}
    assert result["a"].charger_id == "a"


def test_plan_with_zero_need_plans_nothing_and_is_reachable() -> None:
    result = plan([request(need=0.0)])["a"]

    assert result == SessionPlan("a", (), 0.0, True, False, False)


def test_plan_with_the_deadline_at_now_plans_nothing_and_is_unreachable() -> None:
    result = plan([request(need=5.0, deadline=NOW)])["a"]

    assert result == SessionPlan("a", (), 0.0, False, False, False)


def test_plan_with_zero_max_current_is_unreachable_not_a_division_by_zero() -> None:
    result = plan(
        [request(low=6, high=0.0, need=5.0, deadline=NOW + timedelta(hours=8))]
    )["a"]

    assert result == SessionPlan("a", (), 0.0, False, False, False)


def test_plan_with_zero_max_current_does_not_disturb_the_other_requests() -> None:
    prices = prices_from([0.1] * 8)
    stopped = request("a", high=0.0, need=5.0)
    normal = request("b", need=1.84)

    result = plan([stopped, normal], prices)

    assert result["a"].quarters == ()
    assert result["b"].reachable is True


def test_plan_stamps_the_quarters_in_the_settings_timezone() -> None:
    result = plan([request()], prices_from([0.1] * 8))["a"]

    assert all(q.start.tzinfo == ZoneInfo(TZ) for q in result.quarters)


def test_plan_quarters_are_in_time_order_and_unique() -> None:
    prices = prices_from([0.5, 0.1, 0.5, 0.1, 0.5, 0.1, 0.5, 0.1])

    result = plan([request(need=4 * E16)], prices)["a"]

    assert slots(result) == sorted(set(slots(result)))


def test_plan_reports_the_energy_of_the_planned_quarters() -> None:
    result = plan(
        [request(need=3 * E16, deadline=NOW + timedelta(hours=4))],
        prices_from([0.1] * 16),
    )["a"]

    assert result.planned_kwh == pytest.approx(len(result.quarters) * E16)
    assert result.planned_kwh >= 3 * E16 - 1e-9


def test_plan_does_not_mutate_its_inputs() -> None:
    prices = prices_from([0.3, 0.1, 0.1, 0.3, 0.2, 0.2, 0.3, 0.3])
    capacity = dict(CAP16)
    requests = [request("a"), request("b")]
    before = (dict(prices), dict(capacity), list(requests))

    plan(requests, prices, capacity)

    assert (prices, capacity, requests) == before


def test_plan_is_idempotent() -> None:
    prices = prices_from([0.3, 0.1, 0.1, 0.3, 0.2, 0.2, 0.3, 0.3])

    assert plan([request("a"), request("b")], prices) == plan(
        [request("a"), request("b")], prices
    )


def test_plan_rejects_a_naive_now() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        plan([request()], now=datetime.fromisoformat("2026-10-21T20:00:00"))


# --- plan_sessions: choosing quarters (T8) -------------------------------------


def test_plan_picks_the_cheapest_contiguous_block() -> None:
    prices = prices_from([0.30, 0.10, 0.10, 0.30, 0.30, 0.30, 0.30, 0.30])

    result = plan([request(need=1.84)], prices)["a"]

    assert slots(result) == [1, 2]
    assert result.reachable is True


def test_plan_scenario_14_twenty_kwh_at_eleven_kilowatts_in_eight_hours() -> None:
    hourly = [0.3, 0.3, 0.3, 0.1, 0.1, 0.2, 0.2, 0.2]
    series = PriceSeries(
        tuple(
            PriceInterval(NOW + timedelta(hours=h), NOW + timedelta(hours=h + 1), price)
            for h, price in enumerate(hourly)
        ),
        NOW,
    )
    capacity = {Phase.L1: 25.0, Phase.L2: 25.0, Phase.L3: 25.0}

    result = plan(
        [request("a", ALL, 6, 16, 20.0, NOW + timedelta(hours=8))],
        quarter_prices(series),
        capacity,
    )["a"]

    assert slots(result) == list(range(12, 20))
    assert [q.start.astimezone(UTC).strftime("%H:%M") for q in result.quarters][
        0
    ] == "23:00"
    assert result.quarters[-1].start.astimezone(UTC).strftime("%H:%M") == "00:45"
    assert all(q.current == 16.0 for q in result.quarters)
    assert result.planned_kwh == pytest.approx(22.08)
    assert (result.reachable, result.price_cap_exceeded, result.uses_unpriced) == (
        True,
        False,
        False,
    )


def runs(indices: list[int]) -> list[int]:
    """Lengths of the runs of consecutive numbers in a sorted list."""
    lengths: list[int] = []
    for _, group in itertools.groupby(enumerate(indices), key=lambda p: p[1] - p[0]):
        lengths.append(len(list(group)))
    return lengths


def cheapest_cost_with_runs(values: list[float], count: int, minimum: int) -> float:
    """Brute force: the cheapest `count` quarters whose runs are all `minimum` or longer."""
    best = math.inf
    for chosen in itertools.combinations(range(len(values)), count):
        if all(length >= minimum for length in runs(list(chosen))):
            best = min(best, sum(values[i] for i in chosen))
    return best


@pytest.mark.parametrize(
    ("values", "quarters"),
    [
        ([0.3, 0.3, 0.3, 0.1, 0.1, 0.2, 0.2, 0.2], 4),
        ([0.5, 0.1, 0.1, 0.5, 0.5, 0.2, 0.2, 0.5, 0.9, 0.9], 4),
        ([0.9, 0.2, 0.2, 0.2, 0.9, 0.9, 0.1, 0.1, 0.9, 0.9], 5),
        ([0.4, 0.1, 0.1, 0.1, 0.4, 0.4, 0.9, 0.9], 3),
    ],
)
def test_plan_matches_the_hand_checked_cheapest_selection_with_runs_of_two(
    values: list[float], quarters: int
) -> None:
    result = plan(
        [request(need=quarters * E16, deadline=NOW + QUARTER * len(values))],
        prices_from(values),
    )["a"]

    chosen = slots(result)
    assert len(chosen) == quarters
    assert min(runs(chosen)) >= 2
    assert sum(values[i] for i in chosen) == pytest.approx(
        cheapest_cost_with_runs(values, quarters, 2)
    )


def test_plan_without_a_cap_never_leaves_a_run_shorter_than_the_block_length() -> None:
    values = [0.3, 0.1, 0.3, 0.1, 0.3, 0.1, 0.1, 0.3, 0.2, 0.2, 0.9, 0.9]

    result = plan(
        [request(need=6 * E16, deadline=NOW + QUARTER * len(values))],
        prices_from(values),
    )["a"]

    assert min(runs(slots(result))) >= 2


def test_plan_an_odd_need_extends_a_block_rather_than_adding_a_second_pair() -> None:
    prices = prices_from([0.30, 0.10, 0.10, 0.20, 0.90, 0.11, 0.11])

    result = plan([request(need=3 * E16, deadline=NOW + QUARTER * 7)], prices)["a"]

    assert slots(result) == [1, 2, 3]


def test_plan_with_a_block_length_of_one_is_the_plain_cheapest_selection() -> None:
    prices = prices_from([0.3, 0.1, 0.3, 0.1, 0.3])

    result = plan([request(need=1.84, deadline=NOW + QUARTER * 5)], prices, block=1)[
        "a"
    ]

    assert slots(result) == [1, 3]


@pytest.mark.parametrize(
    ("horizon", "block"),
    [(1, 2), (3, 4)],
    ids=["one quarter, L=2", "three quarters, L=4"],
)
def test_plan_falls_back_to_single_quarters_when_no_block_fits(
    horizon: int, block: int
) -> None:
    prices = prices_from([0.3, 0.1, 0.2][:horizon])

    result = plan(
        [request(need=0.5, deadline=NOW + QUARTER * horizon)], prices, block=block
    )["a"]

    cheapest = min(range(horizon), key=lambda i: [0.3, 0.1, 0.2][i])
    assert slots(result) == [cheapest]
    assert result.quarters[0].current == 16.0
    assert result.reachable is True


def test_plan_ties_go_to_the_earliest_quarters() -> None:
    result = plan([request(need=1.84)], prices_from([0.2] * 8))["a"]

    assert slots(result) == [0, 1]


def test_plan_uses_negative_prices_first() -> None:
    prices = prices_from([0.2, 0.2, -0.5, -0.5, 0.2, 0.2, 0.2, 0.2])

    assert slots(plan([request(need=1.84)], prices)["a"]) == [2, 3]


def test_plan_hourly_intervals_split_into_four_equal_quarters_and_plan_as_a_run() -> (
    None
):
    series = PriceSeries(
        (
            PriceInterval(NOW, NOW + timedelta(hours=1), 0.5),
            PriceInterval(NOW + timedelta(hours=1), NOW + timedelta(hours=2), 0.1),
        ),
        NOW,
    )

    result = plan([request(need=4 * E16)], quarter_prices(series))["a"]

    assert slots(result) == [4, 5, 6, 7]
    assert {q.price for q in result.quarters} == {0.1}


def test_plan_a_partial_current_quarter_is_planned_like_any_other() -> None:
    now = NOW + timedelta(minutes=7)
    prices = prices_from([0.1] * 8)

    result = plan(
        [request(need=1.84, deadline=NOW + timedelta(hours=2))], prices, now=now
    )["a"]

    assert slots(result) == [0, 1]


# --- plan_sessions: price cap (T12) --------------------------------------------


def test_plan_price_equal_to_the_cap_counts_as_under_it() -> None:
    prices = prices_from([0.25, 0.25, 0.40, 0.40, 0.40, 0.40, 0.40, 0.40])

    at_cap = plan([request()], prices, cap=0.25)["a"]
    just_under = plan([request()], prices, cap=0.2499)["a"]

    assert at_cap.price_cap_exceeded is False
    assert slots(at_cap) == [0, 1]
    assert just_under.price_cap_exceeded is True
    assert just_under.reachable is True


def test_plan_cap_is_not_broken_when_the_need_fits_under_it() -> None:
    prices = prices_from([0.5, 0.5, 0.1, 0.1, 0.1, 0.1, 0.5, 0.5])

    result = plan([request(need=4 * E16)], prices, cap=0.2)["a"]

    assert slots(result) == [2, 3, 4, 5]
    assert result.price_cap_exceeded is False


def test_plan_cap_is_broken_only_for_the_missing_energy() -> None:
    prices = prices_from([0.5, 0.5, 0.1, 0.1, 0.5, 0.5, 0.5, 0.5])

    result = plan([request(need=3 * E16)], prices, cap=0.2)["a"]

    assert slots(result) == [1, 2, 3]
    assert result.price_cap_exceeded is True
    assert result.reachable is True


def test_plan_isolated_quarters_under_the_cap_beat_a_block_over_it() -> None:
    prices = prices_from([0.1, 0.5] * 4)

    capped = plan([request(need=1.5)], prices, cap=0.3)["a"]
    uncapped = plan([request(need=1.5)], prices)["a"]

    assert slots(capped) == [0, 2]
    assert capped.price_cap_exceeded is False
    assert slots(uncapped) == [0, 1]


def test_plan_a_cap_of_zero_with_only_positive_prices_breaks_the_cap_to_meet_the_need() -> (
    None
):
    result = plan([request()], prices_from([0.1] * 8), cap=0.0)["a"]

    assert result.price_cap_exceeded is True
    assert result.reachable is True


def test_plan_unreachable_plans_every_usable_quarter() -> None:
    hourly = [0.3, 0.3, 0.3, 0.1, 0.1, 0.2, 0.2, 0.2]
    prices = {}
    for hour, price in enumerate(hourly):
        for quarter in range(4):
            prices[NOW + timedelta(hours=hour, minutes=15 * quarter)] = price
    capacity = {Phase.L1: 25.0, Phase.L2: 25.0, Phase.L3: 25.0}
    req = request("a", ALL, 6, 16, 1000.0, NOW + timedelta(hours=8))

    result = plan([req], prices, capacity)["a"]
    capped = plan([req], prices, capacity, cap=0.15)["a"]

    assert len(result.quarters) == 32
    assert result.reachable is False
    assert result.planned_kwh == pytest.approx(88.32)
    assert (result.price_cap_exceeded, result.uses_unpriced) == (False, False)
    assert capped.price_cap_exceeded is True


def test_plan_unreachable_with_unpriced_quarters_is_flagged() -> None:
    prices = prices_from([0.2] * 16)
    req = request(need=1000.0, deadline=NOW + timedelta(hours=8))

    result = plan([req], prices)["a"]

    assert len(result.quarters) == 32
    assert result.uses_unpriced is True
    assert [q.price for q in result.quarters].count(None) == 16


# --- plan_sessions: unpriced quarters (T10) ------------------------------------


def test_plan_uses_unpriced_quarters_last_and_latest_first() -> None:
    prices = prices_from([0.2] * 8)
    req = request(need=9.0, deadline=NOW + timedelta(hours=9))

    result = plan([req], prices)["a"]

    assert slots(result) == list(range(8)) + [34, 35]
    assert [q.price for q in result.quarters][-2:] == [None, None]
    assert result.uses_unpriced is True
    assert result.reachable is True


def test_plan_without_unpriced_quarters_when_the_prices_cover_the_horizon() -> None:
    prices = prices_from([0.2] * 36)
    req = request(need=9.0, deadline=NOW + timedelta(hours=9))

    result = plan([req], prices)["a"]

    assert slots(result) == list(range(10))
    assert result.uses_unpriced is False


def test_plan_supplying_the_missing_prices_changes_the_plan() -> None:
    req = request(need=9.0, deadline=NOW + timedelta(hours=9))
    known = prices_from([0.2] * 8)
    complete = {**known, **prices_from([0.05] * 28, NOW + QUARTER * 8)}

    before = plan([req], known)["a"]
    after = plan([req], complete)["a"]

    assert before.uses_unpriced is True
    assert after.uses_unpriced is False
    assert slots(after) != slots(before)


def test_plan_a_priced_single_quarter_is_taken_before_an_unpriced_block() -> None:
    prices = {NOW + QUARTER * 3: 0.9}
    req = request(need=1.84, deadline=NOW + QUARTER * 8)

    result = plan([req], prices)["a"]

    assert 3 in slots(result)
    assert len(result.quarters) == 2


# --- plan_sessions: shared capacity (T12) --------------------------------------


def test_plan_the_least_slack_session_claims_the_cheap_quarters_first() -> None:
    prices = {NOW + QUARTER * i: (0.1 if i < 8 else 0.5) for i in range(32)}
    relaxed = request("a", need=3.0, deadline=NOW + timedelta(hours=8))
    urgent = request("b", need=7.0, deadline=NOW + timedelta(hours=2))

    result = plan([relaxed, urgent], prices)

    assert slots(result["b"]) == list(range(8))
    assert result["b"].reachable is True
    assert slots(result["a"]) == [8, 9, 10, 11]
    assert result["a"].reachable is True


def test_plan_urgency_does_not_depend_on_the_order_of_the_requests() -> None:
    prices = prices_from([0.05, 0.05] + [0.30] * 6)
    late = request("late", need=1.84, deadline=NOW + timedelta(hours=2))
    urgent = request("urgent", need=1.84, deadline=NOW + QUARTER * 2)

    forward = plan([late, urgent], prices)
    backward = plan([urgent, late], prices)

    assert forward == backward
    assert slots(forward["urgent"]) == [0, 1]
    assert forward["urgent"].reachable is True
    assert forward["late"].reachable is True


def test_plan_equal_urgency_goes_by_deadline_then_id() -> None:
    prices = prices_from([0.1] * 4)
    first = request("b", need=1.84, deadline=NOW + QUARTER * 2)
    second = request("a", need=1.84, deadline=NOW + QUARTER * 2)

    result = plan([first, second], prices)

    assert result["a"].reachable is True
    assert result["b"].reachable is False


def test_plan_never_plans_a_phase_beyond_its_capacity() -> None:
    prices = prices_from([0.1] * 8)
    requests = [
        request("a", need=3.68),
        request("b", need=3.68),
        request("c", need=3.68),
    ]

    result = plan(requests, prices)

    for index in range(8):
        total = sum(
            q.current
            for item in result.values()
            for q in item.quarters
            if int((q.start.astimezone(UTC) - NOW) / QUARTER) == index
        )
        assert total <= 16.0 + 1e-9


def test_plan_requests_on_different_phases_do_not_compete() -> None:
    prices = prices_from([0.3, 0.1, 0.1, 0.3, 0.3, 0.3, 0.3, 0.3])

    result = plan([request("a", L1), request("b", L2)], prices)

    assert slots(result["a"]) == slots(result["b"]) == [1, 2]


def test_plan_a_three_phase_request_blocks_every_phase_it_uses() -> None:
    prices = prices_from([0.1] * 4)
    three = request(
        "a",
        ALL,
        need=2 * energy_per_quarter_kwh(16, 3, 230.0),
        deadline=NOW + QUARTER * 2,
    )
    single = request("b", L2, need=1.84, deadline=NOW + QUARTER * 2)

    result = plan([three, single], prices)

    assert result["a"].reachable is True
    assert result["b"].quarters == ()
    assert result["b"].reachable is False


@pytest.mark.parametrize(
    ("capacity", "second_currents", "second_reachable"),
    [(21.5, [], False), (21.9999999999, [6.0, 6.0], False), (22.0, [6.0, 6.0], False)],
)
def test_plan_a_fractional_leftover_is_floored_to_whole_amps(
    capacity: float, second_currents: list[float], second_reachable: bool
) -> None:
    prices = prices_from([0.1, 0.1])
    first = request("a", need=1.84, deadline=NOW + QUARTER * 2)
    second = request("b", need=1.84, deadline=NOW + QUARTER * 2)

    result = plan(
        [first, second], prices, {Phase.L1: capacity, Phase.L2: 0.0, Phase.L3: 0.0}
    )

    assert [q.current for q in result["a"].quarters] == [16.0, 16.0]
    assert [q.current for q in result["b"].quarters] == second_currents
    assert result["b"].reachable is second_reachable


def test_plan_the_second_request_runs_at_the_leftover_current() -> None:
    all_or_nothing = request("a", low=10, high=10, need=0.1, deadline=NOW + QUARTER)
    adjustable = request("b", need=0.1, deadline=NOW + QUARTER)

    result = plan([all_or_nothing, adjustable], capacity={Phase.L1: 16.0})

    assert result["a"].quarters[0].current == 10.0
    assert result["b"].quarters[0].current == 6.0


def test_plan_an_all_or_nothing_request_does_not_run_at_a_smaller_leftover() -> None:
    first = request("a", low=10, high=10, need=0.1, deadline=NOW + QUARTER)
    second = request("b", low=10, high=10, need=0.1, deadline=NOW + QUARTER)

    result = plan([first, second], capacity={Phase.L1: 16.0})

    assert result["b"].quarters == ()
    assert result["b"].reachable is False


@pytest.mark.parametrize(
    ("capacity", "planned"), [(7.4999999999, 7.5), (7.5, 7.5), (7.4, None)]
)
def test_plan_all_or_nothing_room_tolerance(
    capacity: float, planned: float | None
) -> None:
    req = request("o", low=7.5, high=7.5, need=0.43, deadline=NOW + QUARTER)

    result = plan([req], capacity={Phase.L1: capacity})["o"]

    assert [q.current for q in result.quarters] == (
        [] if planned is None else [planned]
    )


@pytest.mark.parametrize(
    "capacity",
    [
        {Phase.L1: 16.0},
        {Phase.L1: 16.0, Phase.L2: 0.0},
        {Phase.L1: 16.0, Phase.L2: -5.0},
    ],
    ids=["absent", "zero", "negative"],
)
def test_plan_nothing_is_planned_on_a_phase_without_room(
    capacity: dict[Phase, float],
) -> None:
    result = plan([request("a", L2)], prices_from([0.1] * 8), capacity)["a"]

    assert result.quarters == ()
    assert result.planned_kwh == 0.0
    assert result.reachable is False


def test_plan_an_adjustable_request_below_its_minimum_room_plans_nothing() -> None:
    result = plan([request()], prices_from([0.1] * 8), {Phase.L1: 5.0})["a"]

    assert result.quarters == ()
    assert result.reachable is False


# --- plan_sessions: DST --------------------------------------------------------


@pytest.mark.parametrize(
    ("now", "quarters"),
    [(utc(2026, 10, 24, 5, 0), 100), (utc(2026, 3, 28, 6, 0), 92)],
    ids=["25-hour day", "23-hour day"],
)
def test_plan_dst_horizons_have_92_and_100_quarters(
    now: datetime, quarters: int
) -> None:
    deadline = next_occurrence(time(7, 0), now, TZ)
    req = request(need=10_000.0, deadline=deadline)

    result = plan([req], now=now)["a"]

    assert len(result.quarters) == quarters
    assert result.reachable is False


def test_plan_contiguity_across_the_fall_back_hour_is_judged_in_utc() -> None:
    now = utc(2026, 10, 24, 22, 0)
    start = now
    prices = {start + QUARTER * i: 0.30 for i in range(32)}
    cheap = utc(2026, 10, 25, 0, 45)
    prices[cheap] = 0.01
    prices[cheap + QUARTER] = 0.01
    req = request(need=1.84, deadline=utc(2026, 10, 25, 6, 0))

    result = plan([req], prices, now=now)["a"]

    assert [q.start.astimezone(UTC) for q in result.quarters] == [
        cheap,
        cheap + QUARTER,
    ]
    assert [q.start.isoformat() for q in result.quarters] == [
        "2026-10-25T02:45:00+02:00",
        "2026-10-25T02:00:00+01:00",
    ]


# --- plan_sessions: property ---------------------------------------------------


@settings(deadline=None, max_examples=100, derandomize=True)
@given(
    prices=st.lists(st.floats(-1, 2, allow_nan=False), min_size=0, max_size=24),
    needs=st.lists(st.floats(0, 20), min_size=1, max_size=3),
    capacity=st.floats(0, 40),
    block=st.integers(1, 4),
    cap=st.one_of(st.none(), st.floats(-0.5, 1.5)),
)
def test_plan_invariants_hold_for_generated_inputs(
    prices: list[float],
    needs: list[float],
    capacity: float,
    block: int,
    cap: float | None,
) -> None:
    deadline = NOW + QUARTER * 20
    requests = [
        request(f"c{i}", L1, 6, 16, need, deadline) for i, need in enumerate(needs)
    ]

    result = plan(requests, prices_from(prices), {Phase.L1: capacity}, block, cap)

    per_quarter: dict[int, float] = {}
    for item in result.values():
        assert slots(item) == sorted(set(slots(item)))
        for q in item.quarters:
            index = int((q.start.astimezone(UTC) - NOW) / QUARTER)
            assert 0 <= index < 20
            assert 6 <= q.current <= 16
            per_quarter[index] = per_quarter.get(index, 0.0) + q.current
        assert item.planned_kwh == pytest.approx(
            sum(energy_per_quarter_kwh(q.current, 1, 230.0) for q in item.quarters)
        )
    assert all(total <= capacity + 1e-6 for total in per_quarter.values())


# --- plan_sessions: shared capacity on mixed phases (T5) -----------------------

MIXED_PHASES = [L1, L2, ALL]


@settings(deadline=None, max_examples=100, derandomize=True)
@given(
    prices=st.lists(st.floats(-1, 2, allow_nan=False), min_size=0, max_size=24),
    shapes=st.lists(
        st.tuples(
            st.sampled_from(MIXED_PHASES),
            st.floats(0, 12),
            st.integers(1, 20),
            st.booleans(),
        ),
        min_size=1,
        max_size=4,
    ),
    capacity=st.tuples(st.floats(0, 40), st.floats(0, 40), st.floats(0, 40)),
    block=st.integers(1, 4),
)
def test_plan_never_books_more_than_a_phase_holds_on_mixed_phases(
    prices: list[float],
    shapes: list[tuple[frozenset[Phase], float, int, bool]],
    capacity: tuple[float, float, float],
    block: int,
) -> None:
    caps = dict(zip(Phase, capacity, strict=True))
    requests = [
        request(
            f"c{i}",
            phases,
            16 if all_or_nothing else 6,
            16,
            need,
            NOW + QUARTER * quarters,
        )
        for i, (phases, need, quarters, all_or_nothing) in enumerate(shapes)
    ]

    result = plan(requests, prices_from(prices), caps, block)

    booked: dict[tuple[datetime, Phase], float] = {}
    for req in requests:
        for q in result[req.charger_id].quarters:
            assert 6 <= q.current <= 16
            for phase in req.phases:
                key = (q.start.astimezone(UTC), phase)
                booked[key] = booked.get(key, 0.0) + q.current
    for (_, phase), total in booked.items():
        assert total <= caps[phase] + 1e-6


def test_plan_rejects_a_naive_deadline() -> None:
    naive = datetime.fromisoformat("2026-10-21T22:00:00")

    with pytest.raises(ValueError):
        plan([request(deadline=naive)])


def test_plan_rejects_an_unknown_timezone() -> None:
    with pytest.raises(ZoneInfoNotFoundError):
        plan_sessions([request()], NOW, {}, CAP16, 230.0, 2, None, "Mars/Olympus")


def test_plan_with_no_requests_never_looks_at_a_naive_now() -> None:
    # Pins current behaviour: the docstring says a naive `now` raises, but with nothing
    # to plan the grid is never built (see section 5 of the plan file, for step 8).
    naive_now = datetime.fromisoformat("2026-10-21T20:00:00")

    assert plan_sessions([], naive_now, {}, CAP16, 230.0, 2, None, TZ) == {}


def test_main_showcase_runs_and_prints(capsys: pytest.CaptureFixture[str]) -> None:
    showcase()

    assert "need " in capsys.readouterr().out
