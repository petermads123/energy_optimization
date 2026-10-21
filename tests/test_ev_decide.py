"""Tests for energy_optimization.ev.decide (T3, T5-T7, T9, T13-T18, T20): the core."""

import copy
import json
import os
import random
import subprocess
import sys
import time as clock
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import energy_optimization
from energy_optimization.ev import (
    Action,
    ActionKind,
    Charger,
    ChargerDecision,
    ChargerKind,
    ChargerMemory,
    ChargerState,
    Decision,
    EventKind,
    GuardLevel,
    Memory,
    Phase,
    PhaseAmps,
    PhaseCurrents,
    PriceInterval,
    PriceSeries,
    ReasonCode,
    Reduction,
    Session,
    Settings,
    Snapshot,
    Stage,
    Status,
    decide,
    decision_to_dict,
    memory_from_dict,
    memory_to_dict,
    snapshot_from_dict,
    snapshot_to_dict,
)
from energy_optimization.ev.decide import main as showcase
from energy_optimization.ev.planning import energy_per_quarter_kwh

NOW = datetime(2026, 10, 21, 20, 0, tzinfo=UTC)  # 22:00 CEST
TZ = "Europe/Copenhagen"
ZONE = ZoneInfo(TZ)
L1 = frozenset({Phase.L1})
L2 = frozenset({Phase.L2})
L3 = frozenset({Phase.L3})
ALL = frozenset(Phase)
E16 = energy_per_quarter_kwh(16, 1, 230.0)  # 0.92 kWh per quarter on one phase
QUARTER = timedelta(minutes=15)


class Default(Enum):
    """Marks a default for the snapshot builder, apart from `None` meaning missing."""

    FRESH = 1


def adj(
    cid: str = "a",
    phases: frozenset[Phase] = L1,
    low: int = 6,
    high: int = 16,
    fallback: int | None = None,
) -> Charger:
    return Charger(cid, phases, ChargerKind.ADJUSTABLE, low, high, None, fallback)


def pump(
    cid: str = "pump",
    phases: frozenset[Phase] = L1,
    assumed: float = 10.0,
    fallback: int | None = None,
) -> Charger:
    return Charger(cid, phases, ChargerKind.ON_OFF, None, None, assumed, fallback)


def fixed(
    cid: str = "f", phases: frozenset[Phase] = L1, level: float = 10.0
) -> Charger:
    return Charger(cid, phases, ChargerKind.FIXED, assumed_current=level)


def idle(
    cid: str = "a",
    setting: float = 0.0,
    paused: bool = True,
    car: bool = True,
    power: float | None = None,
    available: bool = True,
) -> ChargerState:
    return ChargerState(cid, car, setting, paused, power, available)


def sess(
    cid: str = "a",
    ready: time = time(7, 0),
    need: float | None = 10.0,
    **kwargs: object,
) -> Session:
    return Session(cid, ready, energy_need_kwh=need, **kwargs)  # type: ignore[arg-type]  # test passes through


def now_session(cid: str = "a", **kwargs: object) -> Session:
    return sess(cid, charge_now=True, **kwargs)  # type: ignore[arg-type]  # test passes through


def hourly(
    values: list[float], start: datetime = NOW, updated_at: datetime | None = None
) -> PriceSeries:
    intervals = tuple(
        PriceInterval(start + timedelta(hours=h), start + timedelta(hours=h + 1), price)
        for h, price in enumerate(values)
    )
    return PriceSeries(intervals, start if updated_at is None else updated_at)


def warm(now: datetime) -> Memory:
    """Memory of a steady state: saved a minute ago, green for an hour, no history."""
    return Memory(
        saved_at=now - timedelta(minutes=1), green_since=now - timedelta(hours=1)
    )


def snap(
    chargers: list[Charger],
    states: list[ChargerState] | None = None,
    sessions: list[Session] | None = None,
    prices: PriceSeries | None = None,
    currents: PhaseCurrents | Default | None = Default.FRESH,
    *,
    now: datetime = NOW,
    budget: float = 25.0,
    threshold: float = 32.0,
    fallback: int = 6,
    price_cap: float | None = None,
    default_need: float = 30.0,
) -> Snapshot:
    if states is None:
        states = [idle(c.id) for c in chargers]
    if isinstance(currents, Default):
        currents = PhaseCurrents(now, 0.0, 0.0, 0.0)
    return Snapshot(
        now,
        Settings(
            budget,
            threshold,
            fallback,
            TZ,
            price_cap=price_cap,
            default_energy_need_kwh=default_need,
        ),
        tuple(chargers),
        tuple(states),
        tuple(sessions or []),
        prices if prices is not None else PriceSeries((), now),
        currents,
        warm(now),
    )


def by_id(decision: Decision) -> dict[str, ChargerDecision]:
    return {c.charger_id: c for c in decision.chargers}


def kinds(decision: Decision) -> list[EventKind]:
    return [event.kind for event in decision.events]


def allocations(decision: Decision) -> dict[str, float]:
    return {c.charger_id: c.allocation for c in decision.chargers}


# --- T3: doc scenarios 1-5 -----------------------------------------------------


def three_chargers() -> tuple[list[Charger], list[Session]]:
    chargers = [adj("a"), adj("b"), adj("c")]
    sessions = [
        now_session("a", ready=time(6, 0), need=20.0),
        now_session("b", ready=time(7, 0), need=20.0),
        now_session("c", ready=time(8, 0), need=20.0),
    ]
    return chargers, sessions


@pytest.mark.parametrize(
    ("budget", "count", "expected", "status"),
    [
        (30, 2, (15.0, 15.0, 0.0), Status.LIMITED_BY_BUDGET),
        (14, 3, (7.0, 7.0, 0.0), Status.LIMITED_BY_BUDGET),
        (6, 3, (6.0, 0.0, 0.0), Status.LIMITED_BY_BUDGET),
        (5, 3, (0.0, 0.0, 0.0), Status.WAITING_FOR_CAPACITY),
        (48, 3, (16.0, 16.0, 16.0), Status.NORMAL),
    ],
)
def test_doc_scenarios_2_to_5_split_equally_and_cut_the_latest_ready_time(
    budget: float, count: int, expected: tuple[float, float, float], status: Status
) -> None:
    chargers, sessions = three_chargers()

    result = decide(
        snap(chargers[:count], sessions=sessions[:count], budget=budget, threshold=60)
    )

    assert tuple(allocations(result).get(i, 0.0) for i in "abc") == expected
    assert result.status is status
    for cid, amps in allocations(result).items():
        if amps == 0:
            assert by_id(result)[cid].reason.code is ReasonCode.WAITING_FOR_CAPACITY


def test_doc_scenario_4_with_ordinary_smart_sessions_waits_for_capacity() -> None:
    chargers = [fixed("f", ALL, 27.0), adj("a", ALL), adj("b", ALL)]
    states = [idle("f", 27.0, False), idle("a"), idle("b")]
    sessions = [
        sess("a", ready=time(7, 0), need=10.0),
        sess("b", ready=time(9, 0), need=10.0),
    ]
    prices = hourly([0.20] * 48)

    result = decide(
        snap(chargers, states, sessions, prices, budget=32.0, threshold=60.0)
    )

    assert result.status is Status.WAITING_FOR_CAPACITY
    assert result.available == PhaseAmps(5.0, 5.0, 5.0)
    for cid in "ab":
        decision = by_id(result)[cid]
        assert decision.allocation == 0.0
        assert decision.priority == 4
        assert decision.reason.code is ReasonCode.WAITING_FOR_CAPACITY
        assert "waiting for capacity" in decision.reason.text
    assert result.actions == ()


def test_a_priority_four_charger_with_a_later_plan_is_not_waiting_for_capacity() -> (
    None
):
    prices = hourly([0.50] * 2 + [0.10] * 4 + [0.50] * 42)

    result = decide(snap([adj()], sessions=[sess(need=2.0)], prices=prices))

    decision = by_id(result)["a"]
    assert decision.priority == 4
    assert decision.plan
    assert decision.reason.code is ReasonCode.PLANNED_LATER
    assert result.status is Status.NORMAL


def test_a_planned_charger_held_back_only_by_the_phase_guard_stays_normal() -> None:
    prices = hourly([0.50] * 2 + [0.10] * 4 + [0.50] * 42)
    currents = PhaseCurrents(
        NOW, 30.0, 0.0, 0.0
    )  # guard leaves 2 A, below the 6 A minimum

    result = decide(
        snap([adj()], sessions=[sess(need=2.0)], prices=prices, currents=currents)
    )

    decision = by_id(result)["a"]
    assert decision.priority == 4
    assert decision.plan
    assert decision.reason.code is ReasonCode.PLANNED_LATER
    assert result.status is Status.NORMAL


# --- A2/A6 re-run: a session that can never be planned waits for capacity ---------


def starved_l1_base(
    extra_chargers: list[Charger] | None = None,
    extra_states: list[ChargerState] | None = None,
    sessions: list[Session] | None = None,
    **kwargs: object,
) -> Snapshot:
    """A fixed charger on L1 leaves 3 A there, below the 6 A minimum of `a`."""
    chargers = [fixed("f", L1, 22.0), adj("a", L1), *(extra_chargers or [])]
    states = [idle("f", 22.0, False), idle("a"), *(extra_states or [])]
    if sessions is None:
        sessions = [sess("a", need=10.0)]
    return snap(chargers, states, sessions, hourly([0.2] * 48), **kwargs)  # type: ignore[arg-type]  # test passes through


@pytest.mark.parametrize("served_is_charge_now", [False, True])
def test_a_starved_charger_beside_a_served_one_limits_the_status(
    served_is_charge_now: bool,
) -> None:
    served = now_session("s", need=10.0) if served_is_charge_now else sess("s")
    chargers = [fixed("f", L1, 27.0), adj("t", ALL), adj("s", L2)]
    states = [idle("f", 27.0, False), idle("t"), idle("s")]
    sessions = [sess("t", need=10.0), served]

    result = decide(
        snap(chargers, states, sessions, hourly([0.2] * 48), budget=32.0, threshold=60)
    )

    assert by_id(result)["s"].allocation == 16.0
    assert by_id(result)["s"].priority == (1 if served_is_charge_now else 2)
    starved = by_id(result)["t"]
    assert starved.allocation == 0.0
    assert starved.priority == 4
    assert starved.plan == ()
    assert starved.reason.code is ReasonCode.WAITING_FOR_CAPACITY
    assert result.available == PhaseAmps(5.0, 32.0, 32.0)
    assert result.status is Status.LIMITED_BY_BUDGET


def test_an_empty_plan_left_by_another_sessions_plan_is_waiting_for_capacity() -> None:
    sessions = [sess("a", need=40.0), sess("b", need=5.0)]

    result = decide(
        snap(
            [adj("a"), adj("b")],
            sessions=sessions,
            prices=hourly([0.2] * 12),
            budget=16.0,
            threshold=60.0,
        )
    )

    served, starved = by_id(result)["a"], by_id(result)["b"]
    assert (served.priority, served.allocation) == (2, 16.0)
    assert (starved.priority, starved.allocation) == (4, 0.0)
    assert starved.plan == ()
    assert starved.reason.code is ReasonCode.WAITING_FOR_CAPACITY
    assert starved.reason.text == "Priority 4 — waiting for capacity"
    assert result.status is Status.LIMITED_BY_BUDGET
    assert kinds(result).count(EventKind.TARGET_UNREACHABLE) == 2


@pytest.mark.parametrize(
    ("level", "starved"),
    [(26.0, False), (26.0 + 5e-10, False), (26.5, True), (27.0, True)],
)
def test_planning_capacity_is_starved_only_below_the_adjustable_minimum(
    level: float, starved: bool
) -> None:
    chargers = [fixed("f", ALL, level), adj("a", ALL)]
    states = [idle("f", level, False), idle("a")]

    result = decide(
        snap(
            chargers,
            states,
            [sess("a", need=10.0)],
            hourly([0.2] * 48),
            budget=32.0,
            threshold=60.0,
        )
    )

    decision = by_id(result)["a"]
    if starved:
        assert decision.plan == ()
        assert decision.allocation == 0.0
        assert decision.reason.code is ReasonCode.WAITING_FOR_CAPACITY
        assert result.status is Status.WAITING_FOR_CAPACITY
    else:
        assert decision.plan
        assert decision.allocation == 6.0
        assert decision.reason.code is ReasonCode.IN_PLAN
        assert result.status is Status.LIMITED_BY_BUDGET


@pytest.mark.parametrize(("level", "starved"), [(15.0, False), (15.5, True)])
def test_planning_capacity_is_starved_below_the_assumed_current_of_an_on_off_charger(
    level: float, starved: bool
) -> None:
    chargers = [fixed("f", L1, level), pump("pump", L1, 10.0)]
    states = [idle("f", level, False), idle("pump")]

    result = decide(
        snap(chargers, states, [sess("pump", need=5.0)], hourly([0.2] * 12))
    )

    decision = by_id(result)["pump"]
    if starved:
        assert decision.plan == ()
        assert decision.allocation == 0.0
        assert decision.reason.code is ReasonCode.WAITING_FOR_CAPACITY
        assert result.status is Status.WAITING_FOR_CAPACITY
    else:
        assert decision.allocation == 10.0
        assert decision.reason.code is ReasonCode.IN_PLAN
        assert result.status is Status.NORMAL


def test_an_on_off_charger_starves_while_an_adjustable_on_its_phase_plans() -> None:
    chargers = [fixed("f", L1, 17.0), pump("pump", L1), adj("a", L1)]
    states = [idle("f", 17.0, False), idle("pump"), idle("a")]
    sessions = [sess("pump", need=10.0), sess("a", need=10.0)]

    result = decide(snap(chargers, states, sessions, hourly([0.2] * 12)))

    assert by_id(result)["pump"].reason.code is ReasonCode.WAITING_FOR_CAPACITY
    assert by_id(result)["pump"].allocation == 0.0
    assert by_id(result)["a"].priority == 2
    assert by_id(result)["a"].allocation == 8.0
    assert result.status is Status.LIMITED_BY_BUDGET


@pytest.mark.parametrize(
    ("phases", "starved"), [(ALL, True), (frozenset({Phase.L2, Phase.L3}), False)]
)
def test_one_phase_below_the_minimum_starves_a_multi_phase_charger(
    phases: frozenset[Phase], starved: bool
) -> None:
    chargers = [fixed("f", L1, 22.0), adj("a", phases)]
    states = [idle("f", 22.0, False), idle("a")]

    result = decide(snap(chargers, states, [sess("a", need=10.0)], hourly([0.2] * 48)))

    decision = by_id(result)["a"]
    assert (decision.reason.code is ReasonCode.WAITING_FOR_CAPACITY) is starved
    assert bool(decision.plan) is not starved
    assert (result.status is Status.WAITING_FOR_CAPACITY) is starved


@pytest.mark.parametrize("limit", [10, 11, None])
def test_smart_charging_off_allocations_can_starve_a_smart_session(
    limit: int | None,
) -> None:
    sessions = [
        sess("off", need=20.0, smart_charging=False, user_current_limit=limit),
        sess("p", need=3.0),
    ]

    result = decide(
        snap(
            [adj("off"), adj("p")],
            sessions=sessions,
            prices=hourly([0.2] * 12),
            budget=16.0,
        )
    )

    smart = by_id(result)["p"]
    if limit == 10:
        assert by_id(result)["off"].allocation == 10.0
        assert (smart.priority, smart.allocation) == (2, 6.0)
        assert smart.reason.code is ReasonCode.IN_PLAN
        assert result.status is Status.LIMITED_BY_BUDGET
    else:
        assert by_id(result)["off"].allocation == float(limit or 16)
        assert (smart.priority, smart.allocation) == (4, 0.0)
        assert smart.plan == ()
        assert smart.reason.code is ReasonCode.WAITING_FOR_CAPACITY
        assert result.status is Status.WAITING_FOR_CAPACITY


def test_an_active_tier_at_zero_and_a_starved_charger_wait_for_capacity() -> None:
    chargers = [fixed("f", L1, 27.0), adj("t", ALL), adj("s", L2)]
    states = [idle("f", 27.0, False), idle("t"), idle("s")]
    sessions = [sess("t", need=10.0), sess("s", need=10.0)]
    currents = PhaseCurrents(NOW, 0.0, 55.0, 0.0)  # the phase guard leaves 5 A on L2

    result = decide(
        snap(
            chargers,
            states,
            sessions,
            hourly([0.2] * 48),
            currents,
            budget=32.0,
            threshold=60.0,
        )
    )

    assert by_id(result)["s"].priority == 2
    assert by_id(result)["s"].plan
    assert by_id(result)["s"].allocation == 0.0
    assert by_id(result)["s"].reason.code is ReasonCode.WAITING_FOR_CAPACITY
    assert by_id(result)["t"].reason.code is ReasonCode.WAITING_FOR_CAPACITY
    assert result.status is Status.WAITING_FOR_CAPACITY


def test_a_starved_charger_beside_a_charger_planned_for_later_waits_for_capacity() -> (
    None
):
    chargers = [fixed("f", L1, 27.0), adj("t", ALL), adj("s", L2)]
    states = [idle("f", 27.0, False), idle("t"), idle("s")]
    sessions = [sess("t", need=10.0), sess("s", need=2.0)]
    prices = hourly([0.50] * 2 + [0.10] * 4 + [0.50] * 42)

    result = decide(
        snap(chargers, states, sessions, prices, budget=32.0, threshold=60.0)
    )

    assert by_id(result)["s"].priority == 4
    assert by_id(result)["s"].plan
    assert by_id(result)["s"].reason.code is ReasonCode.PLANNED_LATER
    assert by_id(result)["t"].reason.code is ReasonCode.WAITING_FOR_CAPACITY
    assert result.status is Status.WAITING_FOR_CAPACITY


@pytest.mark.parametrize(
    ("limit", "code", "status"),
    [
        (4, ReasonCode.BELOW_MINIMUM, Status.NORMAL),
        (6, ReasonCode.WAITING_FOR_CAPACITY, Status.WAITING_FOR_CAPACITY),
    ],
)
def test_a_user_limit_below_the_minimum_is_not_reported_as_starved(
    limit: int, code: ReasonCode, status: Status
) -> None:
    session = sess("a", need=10.0, user_current_limit=limit)

    result = decide(starved_l1_base(sessions=[session]))

    decision = by_id(result)["a"]
    assert decision.reason.code is code
    assert decision.priority == 4
    assert decision.allocation == 0.0
    assert result.status is status


@pytest.mark.parametrize(
    ("fallback", "code"),
    [(None, ReasonCode.WAITING_FOR_CAPACITY), (4, ReasonCode.BELOW_MINIMUM)],
)
def test_stale_phase_data_outranks_a_starved_charger_in_the_status(
    fallback: int | None, code: ReasonCode
) -> None:
    chargers = [fixed("f", L1, 22.0), adj("a", L1, fallback=fallback)]
    states = [idle("f", 22.0, False), idle("a")]

    result = decide(
        snap(chargers, states, [sess("a", need=10.0)], hourly([0.2] * 48), None)
    )

    assert by_id(result)["a"].reason.code is code
    assert by_id(result)["a"].allocation == 0.0
    assert result.status is Status.PHASE_DATA_STALE
    assert EventKind.PHASE_DATA_STALE in kinds(result)


def _stale(prices: PriceSeries) -> Snapshot:
    chargers = [fixed("f", L1, 22.0), adj("a", L1)]
    states = [idle("f", 22.0, False), idle("a")]
    return snap(chargers, states, [sess("a", need=10.0)], prices)


def test_stale_prices_never_mark_a_session_starved() -> None:
    stale = PriceSeries((), NOW - timedelta(minutes=61))

    result = decide(_stale(stale))

    decision = by_id(result)["a"]
    assert decision.priority == 3
    assert decision.plan == ()
    assert decision.allocation == 0.0
    assert decision.reason.code is ReasonCode.WAITING_FOR_CAPACITY
    assert decision.reason.text == "Priority 3 — waiting for capacity"
    assert result.status is Status.PRICES_STALE
    assert EventKind.TARGET_UNREACHABLE not in kinds(result)


def test_a_running_starved_charger_is_paused() -> None:
    chargers = [fixed("f", L1, 22.0), adj("a", L1)]
    states = [idle("f", 22.0, False), idle("a", 10.0, paused=False)]

    result = decide(snap(chargers, states, [sess("a", need=10.0)], hourly([0.2] * 48)))

    assert result.actions == (Action("a", ActionKind.PAUSE, 0.0, Stage.DOWN),)
    assert by_id(result)["a"].reason.code is ReasonCode.WAITING_FOR_CAPACITY
    assert result.status is Status.WAITING_FOR_CAPACITY


def test_a_starved_session_reports_what_it_will_reach_and_is_not_on_target() -> None:
    session = sess("a", need=10.0, delivered_kwh=2.5)

    result = decide(starved_l1_base(sessions=[session], price_cap=0.1))

    decision = by_id(result)["a"]
    (event,) = result.events
    assert event.kind is EventKind.TARGET_UNREACHABLE
    assert event.charger_id == "a"
    assert event.params["reachable_kwh"] == 2.5
    assert event.params["target_kwh"] == 10.0
    assert decision.remaining_need_kwh == 7.5
    assert decision.expected_finish is None
    assert decision.reaches_target is False


@pytest.mark.parametrize(
    ("session", "text", "source"),
    [
        (sess("a", need=10.0), "Priority 4 — waiting for capacity", "energy"),
        (
            sess("a", need=None),
            "Priority 4 — waiting for capacity (assumed need 30 kWh)",
            "default",
        ),
        (
            Session(
                "a",
                time(7, 0),
                soc_percent=50.0,
                target_soc_percent=80.0,
                battery_capacity_kwh=40.0,
            ),
            "Priority 4 — waiting for capacity",
            "soc",
        ),
    ],
)
def test_the_starved_reason_has_a_stable_text_and_params(
    session: Session, text: str, source: str
) -> None:
    result = decide(starved_l1_base(sessions=[session]))

    reason = by_id(result)["a"].reason
    assert reason.text == text
    assert reason.params == {"priority": 4, "allocation": 0.0, "need_source": source}


def test_a_starved_snapshot_is_pure_and_repeatable() -> None:
    snapshot = starved_l1_base()
    before = copy.deepcopy(snapshot)

    first = decide(snapshot)

    assert snapshot == before
    assert decide(snapshot) == first


def test_equal_ready_times_rank_the_larger_remaining_need_first() -> None:
    chargers = [adj("small"), adj("big")]
    sessions = [
        now_session("small", need=10.0),
        now_session("big", need=20.0),
    ]

    result = decide(snap(chargers, sessions=sessions, budget=6.0))

    assert allocations(result) == {"small": 0.0, "big": 6.0}


def test_equal_ready_times_and_needs_rank_by_charger_id() -> None:
    chargers = [adj("b"), adj("a")]
    sessions = [now_session("b", need=10.0), now_session("a", need=10.0)]

    result = decide(snap(chargers, sessions=sessions, budget=6.0))

    assert allocations(result) == {"b": 0.0, "a": 6.0}


def test_only_the_highest_occupied_tier_gets_current() -> None:
    chargers = [adj("p1", L1), adj("p3", L2)]
    sessions = [now_session("p1"), sess("p3", need=0.0)]

    result = decide(snap(chargers, sessions=sessions))

    decisions = by_id(result)
    assert decisions["p1"].allocation == 16.0
    assert decisions["p1"].priority == 1
    assert decisions["p3"].allocation == 0.0
    assert decisions["p3"].priority == 3
    assert decisions["p3"].reason.code is ReasonCode.WAITING_FOR_HIGHER_PRIORITY


def test_a_tier_is_occupied_only_by_chargers_that_can_run() -> None:
    chargers = [adj("p1", L1), adj("p3", L2)]
    sessions = [now_session("p1", user_current_limit=4), sess("p3", need=0.0)]

    decisions = by_id(decide(snap(chargers, sessions=sessions)))

    assert decisions["p1"].allocation == 0.0
    assert decisions["p1"].priority == 1
    assert decisions["p1"].reason.code is ReasonCode.BELOW_MINIMUM
    assert decisions["p3"].allocation == 16.0
    assert decisions["p3"].reason.code is ReasonCode.PLAN_COMPLETED


def test_the_most_overdue_charger_ranks_first_in_priority_three() -> None:
    plugged = datetime(2026, 10, 20, 18, 0, tzinfo=UTC)
    chargers = [adj("a"), adj("b")]
    sessions = [
        sess("b", ready=time(7, 0), plugged_in_at=plugged),
        sess("a", ready=time(8, 0), plugged_in_at=plugged),
    ]

    decisions = by_id(decide(snap(chargers, sessions=sessions, budget=6.0)))

    assert decisions["b"].priority == decisions["a"].priority == 3
    assert decisions["b"].allocation == 6.0
    assert decisions["a"].allocation == 0.0


# --- T5: priority assignment ---------------------------------------------------


def test_charge_now_is_priority_one() -> None:
    decision = by_id(decide(snap([adj()], sessions=[now_session()])))["a"]

    assert decision.priority == 1
    assert decision.reason.code is ReasonCode.CHARGE_NOW
    assert decision.reason.text == "Priority 1 — charge now"


def test_charge_now_stays_priority_one_when_its_need_is_covered_and_says_so() -> None:
    session = now_session(need=10.0, delivered_kwh=10.0)

    result = decide(snap([adj()], sessions=[session]))

    decision = by_id(result)["a"]
    assert decision.priority == 1
    assert decision.allocation == 16.0
    assert decision.expected_finish == NOW.astimezone(ZONE)
    covered = [e for e in result.events if e.kind is EventKind.CHARGE_NOW_NEED_COVERED]
    assert [e.charger_id for e in covered] == ["a"]


def test_charge_now_with_a_need_left_raises_no_need_covered_event() -> None:
    result = decide(snap([adj()], sessions=[now_session()]))

    assert EventKind.CHARGE_NOW_NEED_COVERED not in kinds(result)


def test_a_float_residue_of_the_need_counts_as_covered() -> None:
    session = sess(need=30.0, delivered_kwh=29.999999999999996)

    decision = by_id(decide(snap([adj()], sessions=[session])))["a"]

    assert decision.priority == 3
    assert decision.reason.code is ReasonCode.PLAN_COMPLETED
    assert decision.allocation == 16.0
    assert decision.remaining_need_kwh == 0.0


def test_a_float_residue_with_charge_now_raises_the_need_covered_event() -> None:
    session = now_session(need=30.0, delivered_kwh=29.999999999999996)

    result = decide(snap([adj()], sessions=[session]))

    assert by_id(result)["a"].priority == 1
    assert EventKind.CHARGE_NOW_NEED_COVERED in kinds(result)


def test_the_current_quarter_in_the_plan_is_priority_two() -> None:
    prices = hourly([0.10] + [0.30] * 11)
    charger = adj("a", ALL)

    result = decide(snap([charger], sessions=[sess(need=5.0)], prices=prices))

    decision = by_id(result)["a"]
    assert decision.priority == 2
    assert decision.reason.code is ReasonCode.IN_PLAN
    assert decision.allocation == 16.0
    assert [q.start.astimezone(UTC) for q in decision.plan] == [NOW, NOW + QUARTER]
    assert decision.expected_finish == datetime(2026, 10, 21, 22, 30, tzinfo=ZONE)
    assert decision.reaches_target is True
    assert result.actions == (Action("a", ActionKind.RESUME, 16.0, Stage.UP),)


def test_a_plan_for_later_quarters_is_priority_four_and_pauses_the_charger() -> None:
    prices = hourly([0.30] * 8 + [0.10] + [0.30] * 3)
    charger = adj("a", ALL)
    state = idle("a", setting=16.0, paused=False)

    result = decide(snap([charger], [state], [sess(need=5.0)], prices))

    decision = by_id(result)["a"]
    assert decision.priority == 4
    assert decision.reason.code is ReasonCode.PLANNED_LATER
    assert decision.allocation == 0.0
    assert result.actions == (Action("a", ActionKind.PAUSE, 0.0, Stage.DOWN),)
    assert decision.expected_finish == datetime(2026, 10, 22, 6, 30, tzinfo=ZONE)
    assert decision.reaches_target is True


def test_a_passed_ready_time_is_priority_three_and_charges_on_spare_capacity() -> None:
    plugged = datetime(2026, 10, 20, 18, 0, tzinfo=UTC)
    session = sess(need=11.04, plugged_in_at=plugged)

    decision = by_id(decide(snap([adj("a", ALL)], sessions=[session])))["a"]

    assert decision.priority == 3
    assert decision.reason.code is ReasonCode.READY_TIME_PASSED
    assert (
        decision.reason.text
        == "Priority 3 — ready time 07:00 passed, charging on spare capacity"
    )
    assert decision.reason.params["ready_time"] == "07:00"
    assert decision.plan == ()
    assert decision.allocation == 16.0
    assert decision.expected_finish is not None
    assert decision.expected_finish == (NOW + timedelta(hours=1)).astimezone(ZONE)
    assert decision.reaches_target is False


def test_a_deadline_equal_to_now_has_passed() -> None:
    now = datetime(2026, 10, 21, 5, 0, tzinfo=UTC)  # 07:00 CEST
    session = sess(need=5.0, plugged_in_at=datetime(2026, 10, 21, 3, 0, tzinfo=UTC))

    decision = by_id(decide(snap([adj()], sessions=[session], now=now)))["a"]

    assert decision.priority == 3
    assert decision.reason.code is ReasonCode.READY_TIME_PASSED


def test_a_deadline_one_second_ahead_has_not_passed() -> None:
    now = datetime(2026, 10, 21, 4, 59, 59, tzinfo=UTC)
    prices = PriceSeries(
        (
            PriceInterval(
                datetime(2026, 10, 21, 4, 45, tzinfo=UTC),
                datetime(2026, 10, 21, 5, 0, tzinfo=UTC),
                0.1,
            ),
        ),
        now,
    )
    session = sess(need=0.5, plugged_in_at=datetime(2026, 10, 21, 3, 0, tzinfo=UTC))

    decision = by_id(decide(snap([adj()], sessions=[session], prices=prices, now=now)))[
        "a"
    ]

    assert decision.priority == 2
    assert decision.reason.code is ReasonCode.IN_PLAN


def test_plugging_in_exactly_at_the_ready_time_aims_for_the_next_day() -> None:
    now = datetime(2026, 10, 21, 5, 0, tzinfo=UTC)
    session = sess(need=5.0, plugged_in_at=now)

    decision = by_id(decide(snap([adj()], sessions=[session], now=now)))["a"]

    assert decision.priority != 3
    assert decision.deadline == datetime(2026, 10, 22, 7, 0, tzinfo=ZONE)


@pytest.mark.parametrize(
    "session",
    [
        Session(
            "a",
            time(7, 0),
            soc_percent=60,
            target_soc_percent=60,
            battery_capacity_kwh=50,
        ),
        Session(
            "a",
            time(7, 0),
            soc_percent=90,
            target_soc_percent=80,
            battery_capacity_kwh=50,
        ),
        Session("a", time(7, 0), delivered_kwh=30.0),
        Session("a", time(7, 0), energy_need_kwh=0.0),
    ],
    ids=["soc at target", "soc above target", "default need delivered", "zero need"],
)
def test_a_covered_need_below_full_charge_is_plan_completed(session: Session) -> None:
    decision = by_id(decide(snap([adj()], sessions=[session])))["a"]

    assert decision.priority == 3
    assert decision.reason.code is ReasonCode.PLAN_COMPLETED
    assert decision.reason.text.startswith(
        "Priority 3 — plan completed, charging on spare capacity"
    )
    assert decision.allocation == 16.0
    assert decision.remaining_need_kwh == 0.0


@pytest.mark.parametrize("charge_now", [False, True])
def test_a_full_battery_is_target_reached_with_no_priority_and_no_current(
    charge_now: bool,
) -> None:
    session = sess(
        need=None,
        soc_percent=100,
        target_soc_percent=80,
        battery_capacity_kwh=50,
        charge_now=charge_now,
    )

    result = decide(snap([adj()], [idle("a", 16.0, False)], [session]))

    decision = by_id(result)["a"]
    assert decision.priority is None
    assert decision.reason.code is ReasonCode.TARGET_REACHED
    assert decision.allocation == 0.0
    assert decision.remaining_need_kwh == 0.0
    assert EventKind.CHARGE_NOW_NEED_COVERED not in kinds(result)
    assert result.actions == (Action("a", ActionKind.PAUSE, 0.0, Stage.DOWN),)


def test_a_session_on_a_fixed_charger_is_accepted_and_changes_nothing() -> None:
    chargers = [fixed("f"), adj("a")]
    states = [idle("f", 10.0, False), idle("a")]
    without = decide(snap(chargers, states, [now_session("a")]))

    with_session = decide(snap(chargers, states, [now_session("a"), now_session("f")]))

    assert allocations(with_session) == allocations(without)
    assert with_session.actions == without.actions
    assert with_session.available == without.available
    assert by_id(with_session)["f"].reason.code is ReasonCode.FIXED
    assert by_id(with_session)["f"].priority is None


def test_a_car_without_a_session_gets_nothing_and_is_paused() -> None:
    result = decide(snap([adj()], [idle("a", 16.0, False)], []))

    decision = by_id(result)["a"]
    assert decision.reason.code is ReasonCode.NO_SESSION
    assert decision.priority is None
    assert decision.allocation == 0.0
    assert decision.reaches_target is None
    assert result.actions == (Action("a", ActionKind.PAUSE, 0.0, Stage.DOWN),)


def test_a_charger_without_a_car_gets_nothing_and_no_action() -> None:
    result = decide(snap([adj()], [idle("a", 16.0, False, car=False)], [now_session()]))

    decision = by_id(result)["a"]
    assert decision.reason.code is ReasonCode.NO_CAR
    assert decision.allocation == 0.0
    assert result.actions == ()


def test_a_default_need_is_assumed_and_the_reason_says_so() -> None:
    session = sess(need=None)

    decision = by_id(decide(snap([adj()], sessions=[session], default_need=30.0)))["a"]

    assert decision.reason.params["need_source"] == "default"
    assert decision.reason.text.endswith("(assumed need 30 kWh)")
    assert decision.remaining_need_kwh == 30.0


def test_a_soc_need_does_not_add_the_assumed_need_note() -> None:
    session = sess(
        need=None, soc_percent=20, target_soc_percent=80, battery_capacity_kwh=50
    )

    decision = by_id(decide(snap([adj()], sessions=[session])))["a"]

    assert decision.reason.params["need_source"] == "soc"
    assert "assumed" not in decision.reason.text
    assert decision.remaining_need_kwh == pytest.approx(30.0)


def test_stale_prices_make_a_smart_session_priority_three() -> None:
    prices = PriceSeries((), NOW - timedelta(minutes=61))

    result = decide(snap([adj()], sessions=[sess()], prices=prices))

    decision = by_id(result)["a"]
    assert decision.priority == 3
    assert decision.reason.code is ReasonCode.PRICES_STALE
    assert decision.plan == ()
    assert decision.allocation == 16.0
    assert result.status is Status.PRICES_STALE
    assert kinds(result) == [EventKind.PRICES_STALE]


def test_prices_exactly_at_the_maximum_age_are_still_fresh() -> None:
    prices = PriceSeries((), NOW - timedelta(minutes=60))

    result = decide(snap([adj()], sessions=[sess()], prices=prices))

    decision = by_id(result)["a"]
    assert decision.reason.code is not ReasonCode.PRICES_STALE
    assert decision.priority == 4
    assert EventKind.PRICES_STALE not in kinds(result)


def test_stale_prices_do_not_touch_charge_now() -> None:
    prices = PriceSeries((), NOW - timedelta(hours=5))

    decision = by_id(decide(snap([adj()], sessions=[now_session()], prices=prices)))[
        "a"
    ]

    assert decision.priority == 1
    assert decision.reason.code is ReasonCode.CHARGE_NOW


def test_a_price_series_stamped_in_the_future_is_as_stale_as_an_old_one() -> None:
    prices = PriceSeries((), NOW + timedelta(hours=3))

    assert (
        decide(snap([adj()], sessions=[sess()], prices=prices)).status
        is Status.PRICES_STALE
    )


# --- T6: amp limits ------------------------------------------------------------


@pytest.mark.parametrize(
    ("l1", "allocation", "status"),
    [
        (0.0, 16.0, Status.NORMAL),
        (20.0, 12.0, Status.LIMITED_BY_PHASE),
        (26.0, 6.0, Status.LIMITED_BY_PHASE),
        (27.0, 0.0, Status.WAITING_FOR_CAPACITY),
    ],
)
def test_the_phase_guard_binds_before_the_budget(
    l1: float, allocation: float, status: Status
) -> None:
    currents = PhaseCurrents(NOW, l1, 0.0, 0.0)

    result = decide(snap([adj()], sessions=[now_session()], currents=currents))

    decision = by_id(result)["a"]
    assert decision.allocation == allocation
    assert result.status is status
    if status is Status.LIMITED_BY_PHASE:
        assert decision.reason.params["capped_by"] == "phase"


def test_a_threshold_exactly_at_the_load_leaves_room_for_the_minimum_only() -> None:
    currents = PhaseCurrents(NOW, 26.0, 0.0, 0.0)

    assert allocations(
        decide(snap([adj()], sessions=[now_session()], currents=currents))
    ) == {"a": 6.0}


def test_doc_scenario_12_a_shared_phase_is_held_to_the_budget() -> None:
    chargers = [adj("t", ALL), adj("s", L1, 6, 10)]
    sessions = [now_session("t"), now_session("s")]

    result = decide(snap(chargers, sessions=sessions, budget=25.0))

    assert allocations(result) == {"t": 15.0, "s": 10.0}
    assert 15.0 + 10.0 <= 25.0
    assert result.status is Status.LIMITED_BY_BUDGET


def test_a_negative_phase_headroom_is_clipped_and_nothing_is_handed_out() -> None:
    currents = PhaseCurrents(NOW, 40.0, 0.0, 0.0)

    result = decide(snap([adj()], sessions=[now_session()], currents=currents))

    assert result.available == PhaseAmps(0.0, 25.0, 25.0)
    assert allocations(result) == {"a": 0.0}
    assert by_id(result)["a"].reason.code is ReasonCode.WAITING_FOR_CAPACITY
    assert result.actions == ()
    assert result.status is Status.WAITING_FOR_CAPACITY


def test_a_running_charger_is_pulled_down_when_the_phase_is_overloaded() -> None:
    currents = PhaseCurrents(NOW, 40.0, 0.0, 0.0)
    state = idle("a", 16.0, False, power=3680.0)

    result = decide(snap([adj()], [state], [now_session()], currents=currents))

    assert allocations(result) == {"a": 8.0}
    assert result.actions == (Action("a", ActionKind.SET_CURRENT, 8.0, Stage.DOWN),)
    assert result.status is Status.LIMITED_BY_PHASE
    assert result.available.l1 == 8.0


def test_a_fractional_budget_is_floored_to_whole_amps() -> None:
    result = decide(snap([adj()], sessions=[now_session()], budget=10.5))

    decision = by_id(result)["a"]
    assert decision.allocation == 10.0
    assert decision.reason.params["capped_by"] == "budget"
    assert result.status is Status.LIMITED_BY_BUDGET
    assert result.available.l1 == 10.5
    assert result.actions == (Action("a", ActionKind.RESUME, 10.0, Stage.UP),)


def test_doc_scenario_20_an_on_off_charger_is_all_or_nothing() -> None:
    chargers = [pump("pump", assumed=10.0), adj("wb")]
    sessions = [now_session("pump", need=10.0), now_session("wb", need=10.0)]
    states = [idle("pump"), idle("wb")]

    low = decide(snap(chargers, states, sessions, budget=15.0))
    enough = decide(snap(chargers, states, sessions, budget=16.0))
    alone = decide(snap(chargers[:1], states[:1], sessions[:1], budget=9.0))

    assert allocations(low) == {"pump": 10.0, "wb": 0.0}
    assert allocations(enough) == {"pump": 10.0, "wb": 6.0}
    assert allocations(alone) == {"pump": 0.0}
    assert by_id(alone)["pump"].reason.code is ReasonCode.WAITING_FOR_CAPACITY


@pytest.mark.parametrize(("limit", "expected"), [(7, 0.0), (8, 7.5), (None, 7.5)])
def test_an_on_off_charger_with_a_fractional_current_against_an_integer_limit(
    limit: int | None, expected: float
) -> None:
    session = now_session("pump", user_current_limit=limit)

    decision = by_id(decide(snap([pump(assumed=7.5)], sessions=[session])))["pump"]

    assert decision.allocation == expected
    if expected == 0.0:
        assert decision.reason.code is ReasonCode.BELOW_MINIMUM


def test_an_on_off_charger_that_is_on_gets_no_action_while_it_stays_on() -> None:
    result = decide(snap([pump()], [idle("pump", 0.0, False)], [now_session("pump")]))

    assert allocations(result) == {"pump": 10.0}
    assert result.actions == ()


def test_an_on_off_charger_is_paused_when_it_cannot_run() -> None:
    result = decide(
        snap(
            [pump()],
            [idle("pump", 0.0, False)],
            [now_session("pump", user_current_limit=5)],
        )
    )

    assert result.actions == (Action("pump", ActionKind.PAUSE, 0.0, Stage.DOWN),)


def test_the_user_limit_caps_an_adjustable_charger() -> None:
    result = decide(snap([adj()], sessions=[now_session(user_current_limit=10)]))

    decision = by_id(result)["a"]
    assert decision.allocation == 10.0
    assert decision.reason.params["capped_by"] == "user_limit"
    assert result.status is Status.NORMAL


# --- T7: reserved chargers and smart charging off ------------------------------


def test_a_fixed_charger_is_reserved_at_its_level_even_when_flagged_paused() -> None:
    chargers = [fixed("f"), adj("a")]
    states = [idle("f", 10.0, True), idle("a")]

    result = decide(snap(chargers, states, [now_session("a")]))

    f = by_id(result)["f"]
    assert f.allocation == 10.0
    assert f.reason.code is ReasonCode.FIXED
    assert f.reason.text == "Fixed charger, reserved at 10 A"
    assert f.priority is None
    assert "capped_by" not in f.reason.params
    assert by_id(result)["a"].allocation == 15.0
    assert by_id(result)["a"].reason.params["capped_by"] == "budget"
    assert result.available == PhaseAmps(15.0, 25.0, 25.0)
    assert result.actions == (Action("a", ActionKind.RESUME, 15.0, Stage.UP),)


def test_a_fixed_charger_is_reserved_at_the_higher_of_level_and_measured_draw() -> None:
    chargers = [fixed("f", L1, 10.0), adj("a", L2)]
    states = [idle("f", 10.0, False, power=4600.0), idle("a")]

    result = decide(snap(chargers, states, [now_session("a")]))

    assert by_id(result)["f"].allocation == pytest.approx(20.0)
    assert result.available.l1 == pytest.approx(5.0)


def test_a_fixed_charger_that_is_not_drawing_its_level_still_shrinks_the_phase_guard() -> (
    None
):
    chargers = [fixed("f", L1, 10.0), adj("a", L1)]
    states = [idle("f", 10.0, False, power=0.0), idle("a")]
    currents = PhaseCurrents(NOW, 20.0, 0.0, 0.0)

    result = decide(
        snap(chargers, states, [now_session("a")], currents=currents, budget=40.0)
    )

    # 32 A threshold - 20 A of other load - 10 A held for the fixed charger.
    assert result.available.l1 == 2.0
    assert allocations(result)["a"] == 0.0


def test_a_fixed_charger_drawing_its_level_is_held_back_once_not_twice() -> None:
    chargers = [fixed("f", L1, 10.0), adj("a", L1)]
    states = [idle("f", 10.0, False, power=2300.0), idle("a")]
    currents = PhaseCurrents(NOW, 20.0, 0.0, 0.0)

    result = decide(
        snap(chargers, states, [now_session("a")], currents=currents, budget=40.0)
    )

    # The 10 A already inside the 20 A reading are not subtracted a second time.
    assert result.available.l1 == 12.0


def test_an_unavailable_adjustable_charger_is_reserved_at_its_last_setting() -> None:
    state = idle("u", 10.0, False, available=False)

    result = decide(snap([adj("u")], [state], []))

    decision = by_id(result)["u"]
    assert decision.allocation == 10.0
    assert decision.reason.code is ReasonCode.UNAVAILABLE
    assert decision.reason.text == "Charger unavailable, reserved at 10 A"
    assert "capped_by" not in decision.reason.params
    assert result.actions == ()


@pytest.mark.parametrize("currents", [Default.FRESH, None], ids=["fresh", "missing"])
def test_a_reserved_charger_is_never_capped_by_a_limit(
    currents: PhaseCurrents | Default | None,
) -> None:
    state = idle("u", 10.0, False, available=False)

    decision = by_id(decide(snap([adj("u")], [state], [], currents=currents)))["u"]

    assert decision.allocation == 10.0
    assert "capped_by" not in decision.reason.params


def test_an_unavailable_on_off_charger_is_reserved_at_its_assumed_current() -> None:
    state = idle("pump", 0.0, True, available=False)

    result = decide(snap([pump(assumed=10.0)], [state], []))

    assert allocations(result) == {"pump": 10.0}
    assert result.actions == ()


def test_an_unavailable_charger_shrinks_everyone_elses_capacity() -> None:
    chargers = [adj("u"), adj("a")]
    states = [idle("u", 10.0, False, available=False), idle("a")]

    result = decide(snap(chargers, states, [now_session("a")]))

    assert allocations(result)["a"] == 15.0
    assert result.available.l1 == 15.0


def test_smart_charging_off_is_served_before_smart_chargers() -> None:
    chargers = [adj("off"), adj("now")]
    sessions = [sess("off", need=20.0, smart_charging=False), now_session("now")]

    result = decide(snap(chargers, sessions=sessions, budget=20.0))

    off, now_decision = by_id(result)["off"], by_id(result)["now"]
    assert off.allocation == 16.0
    assert off.reason.code is ReasonCode.SMART_CHARGING_OFF
    assert off.reason.text == "Smart charging off, charging at 16 A"
    assert off.priority is None
    assert now_decision.allocation == 0.0
    assert now_decision.reason.code is ReasonCode.WAITING_FOR_CAPACITY
    assert result.status is Status.WAITING_FOR_CAPACITY


def test_smart_charging_off_respects_the_user_limit() -> None:
    chargers = [adj("off"), adj("now")]
    sessions = [
        sess("off", need=20.0, smart_charging=False, user_current_limit=10),
        now_session("now"),
    ]

    result = decide(snap(chargers, sessions=sessions, budget=20.0))

    assert allocations(result) == {"off": 10.0, "now": 10.0}
    assert result.status is Status.LIMITED_BY_BUDGET


def test_smart_charging_off_is_cut_only_when_it_cannot_reach_its_minimum() -> None:
    chargers = [adj("one"), adj("two")]
    sessions = [
        sess("one", need=20.0, smart_charging=False),
        sess("two", need=10.0, smart_charging=False),
    ]

    assert allocations(decide(snap(chargers, sessions=sessions, budget=12.0))) == {
        "one": 6.0,
        "two": 6.0,
    }
    assert allocations(decide(snap(chargers, sessions=sessions, budget=8.0))) == {
        "one": 8.0,
        "two": 0.0,
    }
    assert allocations(decide(snap(chargers, sessions=sessions, budget=5.0))) == {
        "one": 0.0,
        "two": 0.0,
    }


def test_smart_charging_off_ignores_prices_and_the_ready_time() -> None:
    prices = PriceSeries((), NOW - timedelta(hours=9))
    session = sess(need=5.0, smart_charging=False)

    decision = by_id(decide(snap([adj()], sessions=[session], prices=prices)))["a"]

    assert decision.allocation == 16.0
    assert decision.plan == ()
    assert decision.reason.code is ReasonCode.SMART_CHARGING_OFF


def test_smart_charging_off_with_no_capacity_left_says_so() -> None:
    session = sess(need=5.0, smart_charging=False)
    currents = PhaseCurrents(NOW, 30.0, 0.0, 0.0)

    decision = by_id(decide(snap([adj()], sessions=[session], currents=currents)))["a"]

    assert decision.allocation == 0.0
    assert decision.reason.text == "Smart charging off, no capacity left"


# --- T13: fallback under doubt -------------------------------------------------

STALE = None


@pytest.mark.parametrize(
    ("charger", "settings_fallback", "expected", "code", "capped_by"),
    [
        (adj("a", ALL), 6, 6.0, ReasonCode.CHARGE_NOW, "fallback"),
        (adj("a", L1, fallback=10), 6, 10.0, ReasonCode.CHARGE_NOW, "fallback"),
        (adj("a", L1, fallback=0), 6, 0.0, ReasonCode.BELOW_MINIMUM, None),
        (adj("a", L1, low=8), 6, 0.0, ReasonCode.BELOW_MINIMUM, None),
        (adj("a", L1, low=6), 0, 0.0, ReasonCode.BELOW_MINIMUM, None),
        (adj("a", L1, low=6), 6, 6.0, ReasonCode.CHARGE_NOW, "fallback"),
        (pump("a", assumed=6.0), 6, 6.0, ReasonCode.CHARGE_NOW, None),
        (pump("a", assumed=7.0), 6, 0.0, ReasonCode.BELOW_MINIMUM, None),
        (pump("a", assumed=6.5), 6, 0.0, ReasonCode.BELOW_MINIMUM, None),
        (pump("a", assumed=9.0, fallback=9), 6, 9.0, ReasonCode.CHARGE_NOW, None),
    ],
    ids=[
        "global cap on a 3-phase charger",
        "own fallback overrides the global",
        "own fallback of zero",
        "min above the fallback",
        "global fallback of zero",
        "fallback equal to the minimum",
        "on/off at the fallback",
        "on/off above the fallback",
        "on/off a hair above the fallback",
        "on/off with its own fallback",
    ],
)
def test_without_phase_data_every_controllable_charger_is_capped_at_its_fallback(
    charger: Charger,
    settings_fallback: int,
    expected: float,
    code: ReasonCode,
    capped_by: str | None,
) -> None:
    result = decide(
        snap(
            [charger],
            sessions=[now_session()],
            currents=STALE,
            fallback=settings_fallback,
        )
    )

    decision = by_id(result)["a"]
    assert decision.allocation == expected
    assert decision.reason.code is code
    assert decision.priority == 1
    assert decision.reason.params.get("capped_by") == capped_by
    assert result.status is Status.PHASE_DATA_STALE
    assert kinds(result).count(EventKind.PHASE_DATA_STALE) == 1


def test_a_fallback_of_zero_pauses_a_charger_that_is_running() -> None:
    state = idle("a", 16.0, False)

    result = decide(snap([adj()], [state], [now_session()], currents=STALE, fallback=0))

    assert result.actions == (Action("a", ActionKind.PAUSE, 0.0, Stage.DOWN),)


def test_in_fallback_a_running_charger_is_stepped_down_to_the_fallback() -> None:
    state = idle("a", 16.0, False)

    result = decide(snap([adj()], [state], [now_session()], currents=STALE))

    assert result.actions == (Action("a", ActionKind.SET_CURRENT, 6.0, Stage.DOWN),)


def test_in_fallback_increases_up_to_the_fallback_are_allowed() -> None:
    result = decide(snap([adj()], sessions=[now_session()], currents=STALE))

    assert result.actions == (Action("a", ActionKind.RESUME, 6.0, Stage.UP),)


def test_in_fallback_the_budget_and_the_tiers_still_apply() -> None:
    chargers = [adj("a", fallback=10), adj("b", fallback=10), adj("c")]
    sessions = [
        now_session("a", need=20.0),
        now_session("b", need=10.0),
        sess("c", need=0.0),
    ]

    result = decide(snap(chargers, sessions=sessions, currents=STALE, budget=15.0))

    assert allocations(result) == {"a": 8.0, "b": 7.0, "c": 0.0}
    assert by_id(result)["c"].reason.code is ReasonCode.WAITING_FOR_HIGHER_PRIORITY


@pytest.mark.parametrize(
    ("offset", "fresh"),
    [
        (timedelta(0), True),
        (-timedelta(seconds=60), True),
        (timedelta(seconds=60), True),
        (-timedelta(seconds=61), False),
        (timedelta(seconds=61), False),
    ],
)
def test_measurement_age_boundary_decides_between_the_guard_and_the_fallback(
    offset: timedelta, fresh: bool
) -> None:
    currents = PhaseCurrents(NOW + offset, 0.0, 0.0, 0.0)

    result = decide(snap([adj()], sessions=[now_session()], currents=currents))

    assert (result.status is Status.PHASE_DATA_STALE) is (not fresh)
    assert allocations(result) == {"a": 16.0 if fresh else 6.0}


def test_a_missing_value_on_a_phase_a_controllable_charger_uses_is_stale() -> None:
    currents = PhaseCurrents(NOW, 0.0, None, 0.0)

    result = decide(snap([adj("a", ALL)], sessions=[now_session()], currents=currents))

    assert result.status is Status.PHASE_DATA_STALE


def test_a_missing_value_on_a_phase_only_a_fixed_charger_uses_stays_fresh() -> None:
    chargers = [fixed("f", L3, 8.0), adj("a", L1)]
    states = [idle("f", 8.0, False), idle("a")]
    currents = PhaseCurrents(NOW, 0.0, 0.0, None)

    result = decide(snap(chargers, states, [now_session("a")], currents=currents))

    assert result.status is Status.NORMAL
    assert kinds(result) == []
    assert result.available.l3 == 17.0
    assert allocations(result)["a"] == 16.0


def test_a_missing_value_on_a_phase_nobody_uses_stays_fresh_and_gets_the_budget() -> (
    None
):
    currents = PhaseCurrents(NOW, 0.0, None, None)

    result = decide(snap([adj("a", L1)], sessions=[now_session()], currents=currents))

    assert result.status is Status.NORMAL
    assert result.available == PhaseAmps(25.0, 25.0, 25.0)


def test_with_only_fixed_chargers_the_measurements_count_as_fresh() -> None:
    currents = PhaseCurrents(NOW, None, None, None)

    result = decide(snap([fixed("f")], [idle("f", 10.0, False)], [], currents=currents))

    assert result.available == PhaseAmps(15.0, 25.0, 25.0)
    assert result.status is Status.NORMAL
    assert EventKind.PHASE_DATA_STALE not in kinds(result)


def test_an_empty_installation_decides_nothing() -> None:
    currents = PhaseCurrents(NOW, 10.0, 0.0, 0.0)

    result = decide(snap([], [], [], currents=currents))

    assert replace(result, memory=None, guard_level=None, next_check=None) == Decision(
        (), (), PhaseAmps(22.0, 25.0, 25.0), Status.NORMAL, ()
    )
    assert result.memory is not None
    assert result.memory.chargers == ()


def test_stale_phase_data_outranks_stale_prices_in_the_status() -> None:
    prices = PriceSeries((), NOW - timedelta(hours=2))

    result = decide(snap([adj()], sessions=[sess()], prices=prices, currents=STALE))

    assert result.status is Status.PHASE_DATA_STALE
    assert set(kinds(result)) == {EventKind.PHASE_DATA_STALE, EventKind.PRICES_STALE}


# --- T14: contradictory input charges least ------------------------------------


def test_a_paused_charger_with_measured_power_adds_to_the_other_load() -> None:
    currents = PhaseCurrents(NOW, 16.0, 0.0, 0.0)
    other = idle("p", 16.0, True, power=3680.0)

    result = decide(
        snap(
            [adj("p"), adj("a")],
            [other, idle("a")],
            [now_session("a")],
            currents=currents,
        )
    )

    # The paused charger counts as drawing nothing, so all 16 A are somebody else's.
    assert result.available.l1 == 16.0
    assert allocations(result)["a"] == 16.0


def test_measured_draw_above_the_setting_does_not_raise_the_free_current() -> None:
    currents = PhaseCurrents(NOW, 20.0, 0.0, 0.0)
    state = idle("a", 10.0, False, power=3680.0)

    result = decide(snap([adj()], [state], [now_session()], currents=currents))

    assert result.available.l1 == 22.0


def test_a_charger_without_a_car_counts_as_drawing_nothing() -> None:
    currents = PhaseCurrents(NOW, 20.0, 0.0, 0.0)
    carless = idle("c", 16.0, False, car=False)

    result = decide(snap([adj("c")], [carless], [], currents=currents))

    assert result.available.l1 == 12.0
    assert result.actions == ()


def test_missing_charger_power_is_taken_as_drawing_the_setting() -> None:
    currents = PhaseCurrents(NOW, 20.0, 0.0, 0.0)
    running = idle("a", 10.0, False, power=None)

    result = decide(snap([adj()], [running], [now_session()], currents=currents))

    assert result.available.l1 == 22.0


# --- T15: actions --------------------------------------------------------------


def test_every_decrease_and_pause_comes_before_every_increase_and_resume() -> None:
    chargers = [adj("a", L1), adj("b", L2), adj("c", L3), fixed("f", L3, 8.0)]
    states = [
        idle("a", 6.0, False),
        idle("b", 16.0, False),
        idle("c", 16.0, False, car=False),
        idle("f", 8.0, False),
    ]
    currents = PhaseCurrents(NOW, 6.0, 16.0, 8.0)

    result = decide(snap(chargers, states, [now_session("a")], currents=currents))

    assert result.actions == (
        Action("b", ActionKind.PAUSE, 0.0, Stage.DOWN),
        Action("a", ActionKind.SET_CURRENT, 16.0, Stage.UP),
    )


def test_actions_within_a_stage_are_sorted_by_charger_id() -> None:
    chargers = [adj("z", L1), adj("m", L2), adj("b", L3)]
    states = [idle("z", 16.0, False), idle("m", 16.0, False), idle("b", 16.0, False)]

    result = decide(snap(chargers, states, []))

    assert [a.charger_id for a in result.actions] == ["b", "m", "z"]
    assert {a.stage for a in result.actions} == {Stage.DOWN}


@pytest.mark.parametrize(
    ("state", "budget", "expected"),
    [
        (idle("a", 0.0, True), 10.0, Action("a", ActionKind.RESUME, 10.0, Stage.UP)),
        (idle("a", 16.0, True), 10.0, Action("a", ActionKind.RESUME, 10.0, Stage.UP)),
        (
            idle("a", 16.0, False),
            10.0,
            Action("a", ActionKind.SET_CURRENT, 10.0, Stage.DOWN),
        ),
        (
            idle("a", 6.0, False),
            10.0,
            Action("a", ActionKind.SET_CURRENT, 10.0, Stage.UP),
        ),
        (idle("a", 10.0, False), 10.0, None),
        (idle("a", 16.0, False), 16.0, None),
        (idle("a", 0.0, True), 0.0 + 5.0, None),
    ],
    ids=[
        "resume from zero",
        "resume at the target, not the old setting",
        "step down",
        "step up",
        "already at the target",
        "already at the maximum",
        "paused and nothing to give",
    ],
)
def test_the_action_is_chosen_by_comparing_the_target_with_the_present_state(
    state: ChargerState, budget: float, expected: Action | None
) -> None:
    result = decide(snap([adj()], [state], [now_session()], budget=budget))

    assert result.actions == (() if expected is None else (expected,))


def test_a_charger_at_zero_that_is_not_paused_needs_no_pause() -> None:
    result = decide(snap([adj()], [idle("a", 0.0, False)], []))

    assert result.actions == ()


def test_pause_targets_zero_and_every_target_is_absolute() -> None:
    chargers = [adj("a", L1), adj("b", L2)]
    states = [idle("a", 16.0, False), idle("b", 0.0, True)]

    result = decide(snap(chargers, states, [now_session("b")]))

    decisions = by_id(result)
    for action in result.actions:
        expected = decisions[action.charger_id].allocation
        assert action.target_current == expected


def test_executing_the_actions_makes_the_next_decision_a_no_op() -> None:
    chargers = [adj("a", L1), adj("b", L2), pump("p", L3)]
    states = [idle("a", 6.0, False), idle("b", 16.0, False), idle("p", 0.0, True)]
    sessions = [now_session("a"), sess("b", need=0.0), now_session("p")]
    first = decide(snap(chargers, states, sessions))
    settled = []
    for state in states:
        decision = by_id(first)[state.charger_id]
        settled.append(
            replace(
                state,
                current_setting=decision.allocation,
                paused=decision.allocation == 0,
            )
        )

    second = decide(snap(chargers, settled, sessions))

    assert first.actions != ()
    assert second.actions == ()
    assert allocations(second) == allocations(first)


# --- T9/T12/T16: events, plans, finish, reasons --------------------------------


def test_a_target_that_cannot_be_reached_raises_an_event_with_the_reachable_kwh() -> (
    None
):
    session = sess(need=105.0, delivered_kwh=5.0)

    result = decide(
        snap([adj()], sessions=[session], prices=hourly([0.3] * 12), price_cap=0.2)
    )

    decision = by_id(result)["a"]
    events = {e.kind: e for e in result.events}
    unreachable = events[EventKind.TARGET_UNREACHABLE]
    assert unreachable.charger_id == "a"
    assert unreachable.params["reachable_kwh"] == pytest.approx(5.0 + 36 * E16)
    assert unreachable.params["target_kwh"] == 105.0
    assert unreachable.params["deadline"] == "2026-10-22T07:00:00+02:00"
    assert events[EventKind.PRICE_CAP_EXCEEDED].params == {"price_cap": 0.2}
    assert decision.priority == 2
    assert decision.allocation == 16.0
    assert decision.expected_finish is None
    assert decision.reaches_target is False
    assert len(decision.plan) == 36


def test_the_price_cap_is_broken_to_meet_the_ready_time_with_an_event_and_no_unreachable() -> (
    None
):
    result = decide(
        snap(
            [adj()], sessions=[sess(need=5.0)], prices=hourly([0.3] * 12), price_cap=0.2
        )
    )

    decision = by_id(result)["a"]
    assert kinds(result) == [EventKind.PRICE_CAP_EXCEEDED]
    assert decision.reaches_target is True
    assert decision.priority == 2


def test_no_cap_event_when_the_need_fits_under_the_cap() -> None:
    prices = hourly([0.1, 0.1, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5])

    result = decide(
        snap([adj()], sessions=[sess(need=5.0)], prices=prices, price_cap=0.2)
    )

    assert kinds(result) == []


def test_a_user_limit_of_zero_is_unreachable_not_a_crash() -> None:
    session = sess(need=10.0, user_current_limit=0)

    result = decide(snap([adj()], sessions=[session], prices=hourly([0.2] * 12)))

    decision = by_id(result)["a"]
    assert decision.allocation == 0.0
    assert decision.priority == 4
    assert decision.reason.code is ReasonCode.BELOW_MINIMUM
    unreachable = [e for e in result.events if e.kind is EventKind.TARGET_UNREACHABLE]
    assert len(unreachable) == 1
    assert unreachable[0].params["reachable_kwh"] == 0.0
    assert unreachable[0].params["target_kwh"] == 10.0


def test_a_user_limit_of_zero_on_an_on_off_charger_is_unreachable_not_a_crash() -> None:
    session = sess("pump", need=10.0, user_current_limit=0)

    result = decide(snap([pump()], sessions=[session], prices=hourly([0.2] * 12)))

    assert allocations(result) == {"pump": 0.0}
    assert EventKind.TARGET_UNREACHABLE in kinds(result)


def test_a_user_limit_of_zero_with_charge_now_waits_below_minimum() -> None:
    session = now_session(user_current_limit=0)

    decision = by_id(decide(snap([adj()], sessions=[session])))["a"]

    assert decision.priority == 1
    assert decision.allocation == 0.0
    assert decision.reason.code is ReasonCode.BELOW_MINIMUM


def test_a_user_limit_below_the_minimum_is_unreachable_too() -> None:
    session = sess(need=10.0, user_current_limit=4)

    result = decide(snap([adj()], sessions=[session], prices=hourly([0.2] * 12)))

    assert allocations(result) == {"a": 0.0}
    assert EventKind.TARGET_UNREACHABLE in kinds(result)


def test_charge_now_sessions_do_not_consume_planning_capacity() -> None:
    chargers = [adj("n"), adj("p")]
    sessions = [now_session("n"), sess("p", need=1.5)]

    result = decide(
        snap(
            chargers, sessions=sessions, prices=hourly([0.1] + [0.3] * 11), budget=16.0
        )
    )

    p = by_id(result)["p"]
    assert [q.current for q in p.plan] == [16.0, 16.0]
    assert p.priority == 2
    assert p.allocation == 0.0
    assert p.reason.code is ReasonCode.WAITING_FOR_HIGHER_PRIORITY
    assert by_id(result)["n"].allocation == 16.0


def test_smart_off_allocations_shrink_the_capacity_planned_for_smart_sessions() -> None:
    chargers = [adj("off"), adj("p")]
    sessions = [
        sess("off", need=20.0, smart_charging=False, user_current_limit=10),
        sess("p", need=3.0),
    ]

    result = decide(
        snap(chargers, sessions=sessions, prices=hourly([0.1] * 12), budget=20.0)
    )

    assert {q.current for q in by_id(result)["p"].plan} == {10.0}


def test_reserved_chargers_shrink_the_capacity_planned_for_smart_sessions() -> None:
    chargers = [fixed("f"), adj("p")]
    states = [idle("f", 10.0, False), idle("p")]

    result = decide(snap(chargers, states, [sess("p", need=3.0)], hourly([0.1] * 12)))

    assert {q.current for q in by_id(result)["p"].plan} == {15.0}


def test_expected_finish_without_a_plan_follows_the_allocation() -> None:
    plugged = datetime(2026, 10, 20, 18, 0, tzinfo=UTC)
    session = sess(need=11.04, plugged_in_at=plugged)

    decision = by_id(decide(snap([adj("a", ALL)], sessions=[session])))["a"]

    assert decision.expected_finish == NOW.astimezone(ZONE) + timedelta(hours=1)


def test_a_covered_need_finishes_now() -> None:
    decision = by_id(decide(snap([adj()], sessions=[sess(need=0.0)])))["a"]

    assert decision.expected_finish == NOW.astimezone(ZONE)
    assert decision.reaches_target is True


def test_without_a_session_there_is_no_need_deadline_or_verdict() -> None:
    decision = by_id(decide(snap([adj()], sessions=[])))["a"]

    assert decision.remaining_need_kwh is None
    assert decision.deadline is None
    assert decision.expected_finish is None
    assert decision.reaches_target is None
    assert decision.plan == ()


def test_result_datetimes_are_in_the_settings_timezone() -> None:
    result = decide(
        snap([adj("a", ALL)], sessions=[sess(need=5.0)], prices=hourly([0.1] * 12))
    )

    decision = by_id(result)["a"]
    assert decision.deadline is not None
    assert decision.expected_finish is not None
    for moment in (
        decision.deadline,
        decision.expected_finish,
        *(q.start for q in decision.plan),
    ):
        assert moment.tzinfo == ZONE
    assert decision.deadline.isoformat() == "2026-10-22T07:00:00+02:00"


def test_unpriced_quarters_in_the_plan_are_flagged_with_no_price() -> None:
    result = decide(snap([adj()], sessions=[sess(need=1.84)]))

    plan = by_id(result)["a"].plan
    assert len(plan) == 2
    assert [q.price for q in plan] == [None, None]


@pytest.mark.parametrize(
    ("now", "end", "quarters"),
    [
        (
            datetime(2026, 10, 24, 22, 0, tzinfo=UTC),
            datetime(2026, 10, 25, 6, 0, tzinfo=UTC),
            32,
        ),
        (
            datetime(2026, 3, 28, 22, 0, tzinfo=UTC),
            datetime(2026, 3, 29, 5, 0, tzinfo=UTC),
            28,
        ),
    ],
    ids=["25-hour night", "23-hour night"],
)
def test_the_dst_nights_reach_planning_with_the_right_number_of_quarters(
    now: datetime, end: datetime, quarters: int
) -> None:
    prices = PriceSeries((PriceInterval(now, end, 0.1),), now)
    exact = quarters * E16

    fits = decide(snap([adj()], sessions=[sess(need=exact)], prices=prices, now=now))
    over = decide(
        snap([adj()], sessions=[sess(need=exact + 0.1)], prices=prices, now=now)
    )

    assert len(by_id(fits)["a"].plan) == quarters
    assert EventKind.TARGET_UNREACHABLE not in kinds(fits)
    event = [e for e in over.events if e.kind is EventKind.TARGET_UNREACHABLE][0]
    assert event.params["reachable_kwh"] == pytest.approx(exact)


def test_the_current_quarter_is_found_in_utc_inside_the_fall_back_hour() -> None:
    zone_now = datetime(2026, 10, 25, 2, 20, fold=1, tzinfo=ZONE)  # 01:20Z
    prices = PriceSeries(
        (
            PriceInterval(
                datetime(2026, 10, 25, 1, 15, tzinfo=UTC),
                datetime(2026, 10, 25, 1, 45, tzinfo=UTC),
                0.01,
            ),
            PriceInterval(
                datetime(2026, 10, 25, 1, 45, tzinfo=UTC),
                datetime(2026, 10, 26, 6, 0, tzinfo=UTC),
                0.5,
            ),
        ),
        zone_now,
    )

    result = decide(
        snap([adj()], sessions=[sess(need=2 * E16)], prices=prices, now=zone_now)
    )

    assert by_id(result)["a"].priority == 2


def test_reasons_carry_the_priority_allocation_and_need_source() -> None:
    result = decide(snap([adj()], sessions=[now_session()]))

    params = by_id(result)["a"].reason.params

    assert params["priority"] == 1
    assert params["allocation"] == 16.0
    assert params["need_source"] == "energy"
    assert "capped_by" not in params


def test_a_reason_for_a_charger_below_its_minimum_names_the_limit() -> None:
    decision = by_id(
        decide(snap([adj()], sessions=[now_session(user_current_limit=4)]))
    )["a"]

    assert "limit 4 A is below the minimum 6 A" in decision.reason.text
    assert decision.priority == 1


def test_capped_by_is_only_ever_one_of_the_named_limits() -> None:
    cases = [
        decide(snap([adj()], sessions=[now_session(user_current_limit=10)])),
        decide(snap([adj()], sessions=[now_session()], currents=None)),
        decide(snap([adj()], sessions=[now_session()], budget=10.0)),
        decide(
            snap(
                [adj()],
                sessions=[now_session()],
                currents=PhaseCurrents(NOW, 20.0, 0.0, 0.0),
            )
        ),
    ]

    seen = [by_id(case)["a"].reason.params["capped_by"] for case in cases]

    assert seen == ["user_limit", "fallback", "budget", "phase"]


def test_the_available_amps_are_reported_per_phase_after_reservations() -> None:
    chargers = [fixed("f1", L1, 8.0), fixed("f2", L2, 12.0)]
    states = [idle("f1", 8.0, False), idle("f2", 12.0, False)]

    result = decide(snap(chargers, states, []))

    assert result.available == PhaseAmps(17.0, 13.0, 25.0)


def test_limited_by_budget_when_the_phase_guard_is_not_the_cause() -> None:
    result = decide(
        snap([adj()], sessions=[now_session()], budget=12.0, threshold=32.0)
    )

    assert result.status is Status.LIMITED_BY_BUDGET


def test_charger_decisions_come_in_snapshot_order() -> None:
    chargers = [adj("z", L1), adj("a", L2), fixed("m", L3)]

    result = decide(snap(chargers, [idle("z"), idle("a"), idle("m", 10.0, False)], []))

    assert [c.charger_id for c in result.chargers] == ["z", "a", "m"]


# --- T18: determinism, purity, stdlib only -------------------------------------


def mixed_snapshot() -> Snapshot:
    chargers = [adj("t", ALL), adj("s", L1, 6, 10), pump("p", L2), fixed("f", L3, 8.0)]
    states = [idle("t"), idle("s", 6.0, False), idle("p"), idle("f", 8.0, False)]
    sessions = [sess("t", need=20.0), now_session("s"), sess("p", need=5.0)]
    return snap(
        chargers, states, sessions, hourly([0.3, 0.1, 0.2, 0.4] * 6), price_cap=0.25
    )


def test_the_same_snapshot_gives_equal_decisions() -> None:
    snapshot = mixed_snapshot()

    assert decide(snapshot) == decide(snapshot)


def test_decide_leaves_the_snapshot_equal_to_a_deep_copy_taken_before() -> None:
    snapshot = mixed_snapshot()
    before = copy.deepcopy(snapshot)

    decide(snapshot)

    assert snapshot == before


def run_with_hash_seed(seed: str, payload: str) -> str:
    source = Path(energy_optimization.__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(source)}
    code = (
        "import json, sys\n"
        "from energy_optimization.ev import decide, decision_to_dict, snapshot_from_dict\n"
        "data = json.load(sys.stdin)\n"
        "print(json.dumps(decision_to_dict(decide(snapshot_from_dict(data)))))\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
        check=True,
        timeout=60,
    )
    return done.stdout


def test_the_output_does_not_depend_on_the_hash_seed() -> None:
    payload = json.dumps(snapshot_to_dict(mixed_snapshot()))

    outputs = {run_with_hash_seed(seed, payload) for seed in ("0", "1", "12345")}

    assert len(outputs) == 1
    assert json.loads(outputs.pop())["chargers"]


def test_the_package_imports_only_the_standard_library_and_itself() -> None:
    import ast

    package = Path(energy_optimization.__file__).resolve().parent
    allowed = set(sys.stdlib_module_names) | {"energy_optimization"}
    offenders: list[str] = []
    files = sorted(package.rglob("*.py"))
    for path in files:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            offenders += [
                f"{path.name}: {name}" for name in names if name not in allowed
            ]
    assert len(files) >= 8
    assert offenders == []


def test_the_package_reexports_the_public_api() -> None:
    import energy_optimization.ev as ev

    assert set(ev.__all__) >= {
        "Action",
        "Charger",
        "ChargerDecision",
        "ChargerKind",
        "ChargerState",
        "ConfigError",
        "Decision",
        "Event",
        "Phase",
        "PhaseCurrents",
        "PriceInterval",
        "PriceSeries",
        "Reason",
        "Session",
        "Settings",
        "Snapshot",
        "Status",
        "decide",
        "decision_from_dict",
        "decision_to_dict",
        "snapshot_from_dict",
        "snapshot_to_dict",
    }
    assert all(hasattr(ev, name) for name in ev.__all__)


# --- T20: speed ----------------------------------------------------------------


def large_snapshot() -> Snapshot:
    chargers: list[Charger] = []
    states: list[ChargerState] = []
    sessions: list[Session] = []
    phase_sets = [ALL, L1, L2, L3, frozenset({Phase.L1, Phase.L2})]
    for index in range(10):
        cid = f"c{index}"
        chargers.append(adj(cid, phase_sets[index % 5]))
        states.append(idle(cid))
        need = 600.0 if index < 2 else 20.0 + index
        sessions.append(sess(cid, ready=time(5 + index % 3, 0), need=need))
    intervals = tuple(
        PriceInterval(NOW + QUARTER * i, NOW + QUARTER * (i + 1), 0.2 + (i % 7) * 0.05)
        for i in range(48 * 4)
    )
    prices = PriceSeries(intervals, NOW)
    # A cap below every price forces the cap-breaking pass; the first two are unreachable.
    return snap(
        chargers, states, sessions, prices, budget=32.0, threshold=40.0, price_cap=0.0
    )


def test_ten_chargers_and_a_48_hour_series_decide_in_under_100_ms() -> None:
    snapshot = large_snapshot()
    durations = []
    for _ in range(5):
        started = clock.perf_counter()
        result = decide(snapshot)
        durations.append(clock.perf_counter() - started)

    assert len(result.chargers) == 10
    assert EventKind.TARGET_UNREACHABLE in kinds(result)
    assert EventKind.PRICE_CAP_EXCEEDED in kinds(result)
    assert min(durations) < 0.1


# --- T17: invariants for any input ---------------------------------------------

BASES = [
    datetime(2026, 10, 21, 20, 0, tzinfo=UTC),
    datetime(2026, 10, 24, 22, 0, tzinfo=UTC),
    datetime(2026, 3, 28, 22, 0, tzinfo=UTC),
    datetime(2026, 10, 25, 0, 0, tzinfo=UTC),
]


def random_session(rng: random.Random, cid: str, now: datetime) -> Session:
    """Build a session from a random source."""
    plugged = now - timedelta(hours=rng.randint(0, 40)) if rng.random() < 0.2 else None
    return Session(
        cid,
        time(rng.randint(0, 23), rng.choice([0, 7, 15, 30, 45])),
        energy_need_kwh=rng.choice([None, 0.0] + [rng.uniform(1, 60)] * 8),
        soc_percent=rng.choice([None, 100.0] + [rng.uniform(0, 99)] * 8),
        target_soc_percent=rng.choice([None, rng.uniform(1, 100)]),
        battery_capacity_kwh=rng.choice([None, rng.uniform(10, 100)]),
        delivered_kwh=rng.uniform(0, 20),
        charge_now=rng.random() < 0.1,
        smart_charging=rng.random() < 0.9,
        user_current_limit=rng.choice([None] * 3 + [rng.randint(0, 20)]),
        plugged_in_at=plugged,
    )


def random_charger(rng: random.Random, cid: str) -> Charger:
    """Build a charger of a random kind on random phases."""
    phases = frozenset(rng.sample(list(Phase), rng.randint(1, 3)))
    kind = rng.choice(list(ChargerKind))
    own = rng.choice([None, None, rng.randint(0, 12)])
    if kind is ChargerKind.ADJUSTABLE:
        low = rng.randint(1, 10)
        return Charger(cid, phases, kind, low, rng.randint(low, 20), None, own)
    level = rng.choice([5.0, 6.0, 7.5, 10.0, 16.0])
    return Charger(
        cid,
        phases,
        kind,
        None,
        None,
        level,
        own if kind is ChargerKind.ON_OFF else None,
    )


def random_snapshot(rng: random.Random) -> Snapshot:
    """Build a valid snapshot from a random source; hypothesis supplies the source."""
    now = rng.choice(BASES) + timedelta(
        minutes=rng.randint(0, 59), seconds=rng.randint(0, 59)
    )
    prefs = Settings(
        rng.choice([6.0, 10.0, 16.0, 20.5, 25.0, 32.0]),
        rng.choice([16.0, 25.0, 32.0, 40.0]),
        rng.choice([0, 4, 6, 8, 10]),
        TZ,
        min_block_quarters=rng.randint(1, 3),
        price_cap=rng.choice([None, None, rng.uniform(-0.1, 0.6)]),
        default_energy_need_kwh=rng.uniform(0, 40),
    )
    chargers = [random_charger(rng, f"c{i}") for i in range(rng.randint(0, 5))]
    states = [
        ChargerState(
            c.id,
            rng.random() < 0.85,
            rng.choice([0.0, 6.0, 8.0, 10.0, 16.0, 20.0]),
            rng.random() < 0.5,
            rng.choice([None, rng.uniform(0, 14000)]),
            rng.random() < 0.85,
        )
        for c in chargers
    ]
    sessions = [random_session(rng, c.id, now) for c in chargers if rng.random() < 0.8]
    start = now - QUARTER - timedelta(minutes=now.minute % 15, seconds=now.second)
    intervals: list[PriceInterval] = []
    for _ in range(rng.randint(0, 25)):
        start += QUARTER * rng.randint(0, 2)
        length = QUARTER * rng.randint(1, 8)
        intervals.append(PriceInterval(start, start + length, rng.uniform(-0.2, 1.0)))
        start += length
    prices = PriceSeries(
        tuple(intervals),
        now
        + timedelta(
            minutes=rng.choice([rng.randint(-50, 50)] * 5 + [rng.randint(-120, 120)])
        ),
    )
    currents: PhaseCurrents | None = None
    if rng.random() < 0.9:
        currents = PhaseCurrents(
            now
            + timedelta(
                seconds=rng.choice(
                    [rng.randint(-50, 50)] * 4 + [rng.randint(-200, 200)]
                )
            ),
            *(rng.choice([rng.uniform(0, 45)] * 7 + [None]) for _ in Phase),
        )
    return Snapshot(
        now, prefs, tuple(chargers), tuple(states), tuple(sessions), prices, currents
    )


TOLERANCE = 1e-6


def present_current(charger: Charger, state: ChargerState) -> float:
    if state.paused:
        return 0.0
    if charger.kind is ChargerKind.ADJUSTABLE:
        return state.current_setting
    return charger.assumed_current or 0.0


def assert_invariants(snapshot: Snapshot, result: Decision) -> None:
    """Check the doc section 12 invariants without using the module's helpers."""
    cfg = snapshot.settings
    states = {s.charger_id: s for s in snapshot.states}
    sessions = {s.charger_id: s for s in snapshot.sessions}
    decisions = {c.charger_id: c for c in result.chargers}
    assert [c.charger_id for c in result.chargers] == [c.id for c in snapshot.chargers]

    def reserved(c: Charger) -> bool:
        return c.kind is ChargerKind.FIXED or not states[c.id].available

    controllable_phases = {
        p
        for c in snapshot.chargers
        if c.kind is not ChargerKind.FIXED and states[c.id].available
        for p in c.phases
    }
    pc = snapshot.phase_currents
    fresh = (
        pc is not None
        and abs((snapshot.now - pc.measured_at).total_seconds())
        <= cfg.max_measurement_age.total_seconds()
        and all(pc.get(p) is not None for p in controllable_phases)
    )

    for c in snapshot.chargers:
        state, decision = states[c.id], decisions[c.id]
        amps = decision.allocation
        if reserved(c):
            measured = 0.0
            if state.measured_power_w is not None:
                measured = state.measured_power_w / (cfg.voltage * len(c.phases))
            level = (
                c.assumed_current or 0.0
                if c.kind is not ChargerKind.ADJUSTABLE
                else state.current_setting
            )
            assert amps == pytest.approx(max(level, measured))
            continue
        session = sessions.get(c.id)
        limit = (
            float(session.user_current_limit)
            if session and session.user_current_limit is not None
            else None
        )
        cap = float(
            c.fallback_current
            if c.fallback_current is not None
            else cfg.fallback_current
        )
        if c.kind is ChargerKind.ADJUSTABLE:
            assert c.min_current is not None
            assert c.max_current is not None
            top = float(c.max_current)
            if limit is not None:
                top = min(top, limit)
            if not fresh:
                top = min(top, cap)
            assert amps == 0.0 or (c.min_current - TOLERANCE <= amps <= top + TOLERANCE)
            assert amps == int(amps)
        else:
            assumed = c.assumed_current or 0.0
            assert amps == 0.0 or amps == pytest.approx(assumed)
            if amps > 0:
                assert limit is None or assumed <= limit + TOLERANCE
                assert fresh or assumed <= cap + TOLERANCE
        if not state.car_connected or session is None:
            assert amps == 0.0
        if decision.priority == 4:
            assert amps == 0.0

    for phase in Phase:
        on_phase = [c for c in snapshot.chargers if phase in c.phases]
        held = sum(decisions[c.id].allocation for c in on_phase if reserved(c))
        given = sum(decisions[c.id].allocation for c in on_phase if not reserved(c))
        assert given <= max(0.0, cfg.phase_budget - held) + TOLERANCE
        if fresh and pc is not None and pc.get(phase) is not None:
            measured_now = pc.get(phase)
            assert measured_now is not None
            set_to = sum(
                decisions[c.id].allocation
                if reserved(c)
                else (
                    0.0
                    if states[c.id].paused or not states[c.id].car_connected
                    else present_current(c, states[c.id])
                )
                for c in on_phase
            )
            bound = max(0.0, cfg.phase_threshold - (measured_now - set_to) - held)
            assert given <= bound + TOLERANCE
        assert 0.0 <= result.available.get(phase) <= cfg.phase_budget + TOLERANCE

    served = {
        decisions[c.id].priority
        for c in snapshot.chargers
        if not reserved(c)
        and decisions[c.id].allocation > 0
        and decisions[c.id].priority is not None
    }
    assert len(served) <= 1
    assert served <= {1, 2, 3}

    expected: set[tuple[str, ActionKind, float]] = set()
    for c in snapshot.chargers:
        state = states[c.id]
        if reserved(c) or not state.car_connected:
            continue
        target, present = decisions[c.id].allocation, present_current(c, state)
        if target <= 0:
            if present > 0:
                expected.add((c.id, ActionKind.PAUSE, 0.0))
        elif state.paused:
            expected.add((c.id, ActionKind.RESUME, target))
        elif abs(target - present) > 1e-9:
            expected.add((c.id, ActionKind.SET_CURRENT, target))
    assert {
        (a.charger_id, a.kind, a.target_current) for a in result.actions
    } == expected
    assert len(result.actions) == len(expected)
    stages = [a.stage for a in result.actions]
    assert stages == sorted(stages, key=lambda s: s is Stage.UP)
    for action in result.actions:
        present = present_current(
            next(c for c in snapshot.chargers if c.id == action.charger_id),
            states[action.charger_id],
        )
        if action.kind is ActionKind.PAUSE:
            assert action.stage is Stage.DOWN
        elif action.kind is ActionKind.RESUME:
            assert action.stage is Stage.UP
        else:
            assert action.stage is (
                Stage.DOWN if action.target_current < present else Stage.UP
            )


@settings(deadline=None, max_examples=300, derandomize=True)
@given(st.randoms(use_true_random=False))
def test_the_invariants_hold_for_generated_snapshots(rng: random.Random) -> None:
    snapshot = random_snapshot(rng)
    result = decide(snapshot)

    assert_invariants(snapshot, result)
    assert decide(snapshot) == result


@settings(deadline=None, max_examples=60, derandomize=True)
@given(st.randoms(use_true_random=False))
def test_generated_snapshots_survive_the_dict_round_trip_and_decide_the_same(
    rng: random.Random,
) -> None:
    snapshot = random_snapshot(rng)
    data = json.loads(json.dumps(snapshot_to_dict(snapshot), allow_nan=False))

    restored = snapshot_from_dict(data)

    assert decision_to_dict(decide(restored)) == decision_to_dict(decide(snapshot))


# --- Round 2: behaviour over time (T3-T13) ------------------------------------------------
#
# Multi-call scenarios feed each decision's memory into the next snapshot. Charger
# entries come from real calls (a hand-built entry has an empty session key, which
# reads as a changed session and clears the regulation), and the previous call's
# actions are written back into the next call's states by hand.

SECOND = timedelta(seconds=1)
MIN = timedelta(minutes=1)
MICRO = timedelta(microseconds=1)


def with_settings(snapshot: Snapshot, **changes: object) -> Snapshot:
    new = replace(snapshot.settings, **changes)  # type: ignore[arg-type]  # test passes through
    return replace(snapshot, settings=new)


def at(
    snapshot: Snapshot,
    delta: timedelta,
    previous: Decision | None = None,
    *,
    currents: PhaseCurrents | None | Default = Default.FRESH,
    **changes: object,
) -> Snapshot:
    """The next call: `delta` later, re-dated readings, the previous memory fed in."""
    now = snapshot.now + delta
    if isinstance(currents, Default):
        old = snapshot.phase_currents
        currents = None if old is None else PhaseCurrents(now, old.l1, old.l2, old.l3)
    memory = previous.memory if previous is not None else snapshot.memory
    fields: dict[str, object] = {
        "now": now,
        "phase_currents": currents,
        "memory": memory,
    }
    fields.update(changes)
    return replace(snapshot, **fields)  # type: ignore[arg-type]  # test passes through


def readings(
    now: datetime, l1: float = 0.0, l2: float = 0.0, l3: float = 0.0
) -> PhaseCurrents:
    return PhaseCurrents(now, l1, l2, l3)


def run(
    snapshot: Snapshot, delta: timedelta, previous: Decision, **changes: object
) -> tuple[Snapshot, Decision]:
    following = at(snapshot, delta, previous, **changes)  # type: ignore[arg-type]  # test passes through
    return following, decide(following)


def action_tuples(decision: Decision) -> list[tuple[str, ActionKind, float, Stage]]:
    return [(a.charger_id, a.kind, a.target_current, a.stage) for a in decision.actions]


def reason_of(decision: Decision) -> str | None:
    for event in decision.events:
        if event.kind is EventKind.MEMORY_RESET:
            return str(event.params["reason"])
    return None


def entry(decision: Decision, charger_id: str = "a") -> ChargerMemory:
    assert decision.memory is not None
    found = decision.memory.charger(charger_id)
    assert found is not None
    return found


def local(moment: datetime) -> datetime:
    return moment.astimezone(ZONE)


def running(
    cid: str = "a", setting: float = 16.0, amps: float | None = None
) -> ChargerState:
    """A charger that is charging at `setting`, drawing `amps` (default: all of it)."""
    drawn = setting if amps is None else amps
    return idle(cid, setting, paused=False, power=drawn * 230.0)


# --- T3: the conservative start --------------------------------------------------------


def test_a_call_without_memory_increases_nothing_but_still_decreases() -> None:
    chargers = [adj("a", L1), adj("b", L2)]
    states = [idle("a"), running("b", 16.0)]
    sessions = [now_session("a"), now_session("b")]
    first = replace(
        snap(chargers, states, sessions, budget=10.0, currents=readings(NOW, 0, 16, 0)),
        memory=None,
    )

    result = decide(first)

    held = by_id(result)["a"]
    assert held.allocation == 0.0
    assert held.reason.code is ReasonCode.CHARGE_NOW
    assert held.reason.params["held_by"] == "memory_reset"
    assert "capped_by" not in held.reason.params
    assert allocations(result)["b"] == 10.0
    assert action_tuples(result) == [("b", ActionKind.SET_CURRENT, 10.0, Stage.DOWN)]
    resets = [e for e in result.events if e.kind is EventKind.MEMORY_RESET]
    assert len(resets) == 1
    assert resets[0].params == {"reason": "missing"}


def test_the_next_call_with_that_memory_starts_the_charger() -> None:
    chargers = [adj("a", L1), adj("b", L2)]
    states = [idle("a"), running("b", 16.0)]
    sessions = [now_session("a"), now_session("b")]
    first = replace(
        snap(chargers, states, sessions, budget=10.0, currents=readings(NOW, 0, 16, 0)),
        memory=None,
    )
    d1 = decide(first)

    second, d2 = run(
        first,
        5 * MIN,
        d1,
        states=(idle("a"), running("b", 10.0)),
        currents=readings(NOW + 5 * MIN, 0, 10, 0),
    )

    assert reason_of(d2) is None
    assert ("a", ActionKind.RESUME, 10.0, Stage.UP) in action_tuples(d2)
    assert "held_by" not in by_id(d2)["a"].reason.params
    assert second.memory is d1.memory


def test_the_conservative_start_gives_way_to_the_green_hold_not_to_a_second_reset() -> (
    None
):
    first = replace(
        snap([adj("a")], [idle("a")], [now_session("a")], budget=10.0), memory=None
    )
    d1 = decide(first)

    _, d2 = run(first, 30 * SECOND, d1)

    assert reason_of(d2) is None
    held = by_id(d2)["a"]
    assert held.allocation == 0.0
    assert held.reason.params["held_by"] == "phase_guard"
    assert d2.next_check == local(NOW + 60 * SECOND)


@pytest.mark.parametrize(
    ("setting", "allocation", "pause_or_set"),
    [
        (10.6, 10.0, ("a", ActionKind.SET_CURRENT, 10.0, Stage.DOWN)),
        (6.0, 6.0, None),
        (5.6, 0.0, ("a", ActionKind.PAUSE, 0.0, Stage.DOWN)),
        (15.9999999999, 16.0, None),
    ],
)
def test_a_held_charger_is_floored_to_a_whole_legal_current(
    setting: float,
    allocation: float,
    pause_or_set: tuple[str, ActionKind, float, Stage] | None,
) -> None:
    first = replace(
        snap(
            [adj("a")],
            [idle("a", setting, paused=False)],
            [now_session("a")],
            budget=25.0,
        ),
        memory=None,
    )

    result = decide(first)

    assert allocations(result)["a"] == allocation
    assert reason_of(result) == "missing"
    assert action_tuples(result) == ([pause_or_set] if pause_or_set else [])


def memory_row(saved_offset: timedelta, **fields: object) -> Memory:
    saved = NOW + saved_offset
    return Memory(
        saved_at=saved,
        green_since=saved - timedelta(hours=1),
        **fields,  # type: ignore[arg-type]  # test passes through
    )


USABILITY_CASES = [
    ("now", memory_row(timedelta(0)), None),
    ("a minute old", memory_row(-MIN), None),
    ("exactly the age limit", memory_row(-30 * MIN), None),
    ("a microsecond too old", memory_row(-30 * MIN - MICRO), "out_of_date"),
    ("a microsecond in the future", memory_row(MICRO), "future"),
    (
        "known entry acted after it was saved",
        memory_row(
            -MIN,
            chargers=(ChargerMemory("a", last_action_at=NOW - MIN + MICRO),),
        ),
        "future",
    ),
    (
        "known entry switched after it was saved",
        memory_row(
            -MIN,
            chargers=(ChargerMemory("a", switched_at=NOW - MIN + MICRO),),
        ),
        "future",
    ),
    (
        "guard time after it was saved",
        Memory(saved_at=NOW - MIN, green_since=NOW - MIN + MICRO),
        "future",
    ),
    (
        "emergency time after it was saved",
        Memory(saved_at=NOW - MIN, emergency_at=NOW - MIN + MICRO),
        "future",
    ),
    (
        "unknown entry with a bad time",
        memory_row(
            -MIN,
            chargers=(ChargerMemory("ghost", switched_at=NOW + timedelta(hours=1)),),
        ),
        None,
    ),
]


@pytest.mark.parametrize(
    ("memory", "reason"),
    [(m, r) for _, m, r in USABILITY_CASES],
    ids=[name for name, _, _ in USABILITY_CASES],
)
def test_memory_usability_boundaries(memory: Memory, reason: str | None) -> None:
    first = replace(snap([adj("a")], [idle("a")], [now_session("a")]), memory=memory)

    result = decide(first)

    assert reason_of(result) == reason
    assert allocations(result)["a"] == (16.0 if reason is None else 0.0)
    assert result.memory is not None
    assert result.memory.charger("ghost") is None


def test_memory_written_with_another_offset_is_judged_by_its_instant() -> None:
    plus_two = timezone(timedelta(hours=2))
    saved = (NOW - 30 * MIN).astimezone(plus_two)
    memory = Memory(saved_at=saved, green_since=saved - timedelta(hours=1))
    first = replace(snap([adj("a")], [idle("a")], [now_session("a")]), memory=memory)

    result = decide(first)

    assert reason_of(result) is None
    assert allocations(result)["a"] == 16.0


@pytest.mark.parametrize(
    "extreme",
    [
        datetime(1, 1, 1, 0, 30, tzinfo=timezone(timedelta(hours=1))),
        datetime(9999, 12, 31, 23, 30, tzinfo=timezone(timedelta(hours=-1))),
    ],
    ids=["year one", "year 9999"],
)
@pytest.mark.parametrize("where", ["saved_at", "green_since", "emergency_at", "entry"])
def test_a_time_no_utc_date_can_hold_is_an_invalid_restart_not_a_crash(
    extreme: datetime, where: str
) -> None:
    saved = NOW - MIN
    fields: dict[str, Any] = {"saved_at": saved, "green_since": saved - 60 * MIN}
    if where == "entry":
        fields["chargers"] = (ChargerMemory("a", switched_at=extreme),)
    else:
        fields[where] = extreme
    first = replace(
        snap([adj("a")], [idle("a")], [now_session("a")]), memory=Memory(**fields)
    )

    result = decide(first)

    assert reason_of(result) == "invalid"
    assert allocations(result)["a"] == 0.0


def test_an_extreme_time_on_an_unknown_charger_is_ignored() -> None:
    extreme = datetime(1, 1, 1, 0, 30, tzinfo=timezone(timedelta(hours=1)))
    memory = memory_row(-MIN, chargers=(ChargerMemory("ghost", decreased_at=extreme),))
    first = replace(snap([adj("a")], [idle("a")], [now_session("a")]), memory=memory)

    result = decide(first)

    assert reason_of(result) is None
    assert allocations(result)["a"] == 16.0


@pytest.mark.parametrize(
    "name",
    [
        "max_price_age",
        "max_measurement_age",
        "emergency_calm",
        "green_hold",
        "ramp_period",
        "under_use_period",
        "min_pause",
        "hysteresis",
        "resume_probe",
        "min_action_interval",
        "max_memory_age",
    ],
)
def test_a_huge_duration_setting_never_overflows(name: str) -> None:
    huge = timedelta(days=10**6)
    chargers = [adj("a", L1), adj("b", L2), adj("c", L3)]
    states = [running("a", 16.0, 7.0), running("b", 10.0, 9.5), idle("c")]
    sessions = [
        now_session("a"),
        now_session("b", plugged_in_at=NOW - 3 * MIN),
        now_session("c"),
    ]
    first = snap(chargers, states, sessions)
    d0 = decide(first)
    memory = d0.memory
    assert memory is not None
    stamped = {
        "a": replace(
            entry(d0, "a"),
            running=True,
            switched_at=NOW - MIN,
            last_target=16.0,
            last_action_at=NOW - SECOND,
        ),
        "b": replace(
            entry(d0, "b"), regulated_cap=10, full_use_since=NOW - MIN, running=True
        ),
        "c": replace(entry(d0, "c"), regulated_cap=0, under_use_stop_at=NOW - MIN),
    }
    warmed = Memory(
        saved_at=memory.saved_at,
        chargers=tuple(stamped[e.charger_id] for e in memory.chargers),
        green_since=NOW - 10 * MIN,
    )
    second = with_settings(at(first, MIN), **{name: huge})
    second = replace(second, memory=warmed)

    result = decide(second)

    if result.next_check is not None:
        assert result.next_check > second.now
        assert result.next_check <= second.now + second.settings.max_memory_age


def test_a_huge_emergency_calm_never_overflows() -> None:
    first = with_settings(
        snap([adj("a")], [idle("a")], [now_session("a")]),
        emergency_threshold=40.0,
        emergency_calm=timedelta(days=10**6),
    )
    d1 = decide(replace(first, phase_currents=readings(NOW, 41.0)))

    _, d2 = run(first, MIN, d1)

    assert d2.guard_level is GuardLevel.EMERGENCY
    assert d2.next_check is not None


# --- T4/T5: anti-flapping ---------------------------------------------------------------


def paused_for_capacity() -> tuple[Snapshot, Decision]:
    """Call 1: `a` charges at 10 A but only 4 A fit, so it is paused for capacity."""
    first = snap([adj("a")], [running("a", 10.0)], [sess("a", need=0.0)], budget=4.0)
    return first, decide(first)


def roomy(snapshot: Snapshot) -> dict[str, object]:
    return {"settings": replace(snapshot.settings, phase_budget=25.0)}


def test_a_charger_stopped_for_capacity_remembers_why_and_when() -> None:
    first, d1 = paused_for_capacity()

    assert action_tuples(d1) == [("a", ActionKind.PAUSE, 0.0, Stage.DOWN)]
    assert by_id(d1)["a"].reason.code is ReasonCode.WAITING_FOR_CAPACITY
    stored = entry(d1)
    assert stored.stopped_for_capacity
    assert not stored.running
    assert stored.switched_at == NOW
    assert stored.last_target == 0.0
    assert stored.last_action_at == NOW
    assert stored.decreased_at == NOW


def test_the_minimum_pause_holds_a_capacity_stop_until_it_has_passed() -> None:
    first, d1 = paused_for_capacity()

    _, d2 = run(first, 14 * MIN + 59 * SECOND, d1, states=(idle("a"),), **roomy(first))

    held = by_id(d2)["a"]
    assert held.allocation == 0.0
    assert held.reason.code is ReasonCode.MINIMUM_PAUSE
    assert held.reason.params["until"] == "2026-10-21T22:15:00+02:00"
    assert d2.actions == ()
    assert d2.next_check == local(NOW + 15 * MIN)
    assert entry(d2).switched_at == NOW  # holding off is not a new switch


def test_the_minimum_pause_does_not_drift_while_it_is_held() -> None:
    first, d1 = paused_for_capacity()

    _, d2 = run(first, 5 * MIN, d1, states=(idle("a"),), **roomy(first))

    assert by_id(d2)["a"].reason.code is ReasonCode.MINIMUM_PAUSE
    assert entry(d2).switched_at == NOW
    assert entry(d2).stopped_for_capacity


def test_a_capacity_stop_can_restart_once_the_pause_has_passed() -> None:
    first, d1 = paused_for_capacity()

    _, d2 = run(first, 15 * MIN, d1, states=(idle("a"),), **roomy(first))

    assert action_tuples(d2) == [("a", ActionKind.RESUME, 16.0, Stage.UP)]
    assert by_id(d2)["a"].reason.code is ReasonCode.PLAN_COMPLETED


def test_charge_now_skips_the_minimum_pause() -> None:
    first, d1 = paused_for_capacity()

    _, d2 = run(
        first,
        5 * MIN,
        d1,
        states=(idle("a"),),
        sessions=(now_session("a", need=0.0),),
        **roomy(first),
    )

    assert by_id(d2)["a"].reason.code is ReasonCode.CHARGE_NOW
    assert action_tuples(d2) == [("a", ActionKind.RESUME, 16.0, Stage.UP)]


def test_charge_now_skips_the_pause_but_not_the_pacing() -> None:
    first, d1 = paused_for_capacity()
    changes = {
        "states": (idle("a"),),
        "sessions": (now_session("a", need=0.0),),
        **roomy(first),
    }

    _, early = run(first, 10 * SECOND, d1, **changes)
    _, later = run(first, 30 * SECOND, d1, **changes)

    held = by_id(early)["a"]
    assert held.allocation == 0.0
    assert held.reason.code is ReasonCode.CHARGE_NOW
    assert held.reason.params["held_by"] == "pacing"
    assert early.actions == ()
    assert action_tuples(later) == [("a", ActionKind.RESUME, 16.0, Stage.UP)]


def test_a_zero_minimum_pause_restarts_as_soon_as_pacing_allows() -> None:
    first, d1 = paused_for_capacity()
    first = replace(first, settings=replace(first.settings, min_pause=timedelta(0)))
    d1 = decide(first)

    _, d2 = run(first, 31 * SECOND, d1, states=(idle("a"),), **roomy(first))

    assert [a.kind for a in d2.actions] == [ActionKind.RESUME]


def test_an_observed_unplug_and_replug_does_not_skip_the_minimum_pause() -> None:
    first, d1 = paused_for_capacity()
    unplugged, d2 = run(
        first, 2 * MIN, d1, states=(idle("a", car=False),), **roomy(first)
    )

    _, d3 = run(unplugged, 2 * MIN, d2, states=(idle("a"),))

    gone = entry(d2)
    assert not gone.car_connected
    assert gone.stopped_for_capacity
    assert gone.switched_at == NOW
    assert gone.regulated_cap is None
    held = by_id(d3)["a"]
    assert held.allocation == 0.0
    assert held.reason.code is ReasonCode.MINIMUM_PAUSE
    assert held.reason.params["until"] == "2026-10-21T22:15:00+02:00"


def test_a_held_off_charger_still_occupies_its_tier() -> None:
    chargers = [adj("a"), adj("b")]
    prices = hourly([0.2] * 48)
    first = snap(
        chargers,
        [running("a", 10.0), idle("b")],
        [sess("a", need=10.0), sess("b", need=0.0)],
        prices,
        budget=4.0,
    )
    d1 = decide(first)

    _, d2 = run(first, 5 * MIN, d1, states=(idle("a"), idle("b")), **roomy(first))

    assert by_id(d2)["a"].priority == 2
    assert by_id(d2)["a"].reason.code is ReasonCode.MINIMUM_PAUSE
    assert by_id(d2)["b"].reason.code is ReasonCode.WAITING_FOR_HIGHER_PRIORITY
    assert allocations(d2) == {"a": 0.0, "b": 0.0}


def selected_pair(ready_b: time) -> tuple[Snapshot, Decision, Snapshot, Decision]:
    """`a` (ready 08:45) charges at 10 A of 10; `b` joins five minutes later."""
    chargers = [adj("a"), adj("b")]
    first = snap(
        chargers,
        [running("a", 10.0), idle("b", car=False)],
        [sess("a", ready=time(8, 45), need=0.0)],
        budget=10.0,
    )
    d1 = decide(first)
    second, d2 = run(
        first,
        5 * MIN,
        d1,
        states=(running("a", 10.0), idle("b")),
        sessions=(
            sess("a", ready=time(8, 45), need=0.0),
            sess("b", ready=ready_b, need=0.0),
        ),
    )
    return first, d1, second, d2


@pytest.mark.parametrize("ready_b", [time(8, 30), time(8, 15)])
def test_a_selected_charger_keeps_its_place_against_a_contender_within_the_hysteresis(
    ready_b: time,
) -> None:
    _, _, _, d2 = selected_pair(ready_b)

    assert allocations(d2) == {"a": 10.0, "b": 0.0}
    assert by_id(d2)["b"].reason.code is ReasonCode.WAITING_FOR_CAPACITY
    assert d2.actions == ()


def test_a_contender_more_than_the_hysteresis_earlier_takes_the_place() -> None:
    _, _, _, d2 = selected_pair(time(8, 14))

    assert action_tuples(d2) == [
        ("a", ActionKind.PAUSE, 0.0, Stage.DOWN),
        ("b", ActionKind.RESUME, 10.0, Stage.UP),
    ]


def test_a_just_started_charger_is_not_dropped_for_an_equal_tier_one() -> None:
    chargers = [adj("a"), adj("b")]
    first = snap(
        chargers,
        [idle("a"), idle("b", car=False)],
        [sess("a", ready=time(8, 45), need=0.0)],
        budget=10.0,
    )
    d1 = decide(first)
    both = (
        sess("a", ready=time(8, 45), need=0.0),
        sess("b", ready=time(6, 0), need=0.0),
    )
    states = (running("a", 10.0), idle("b"))

    second, d2 = run(first, 5 * MIN, d1, states=states, sessions=both)
    third, d3 = run(second, 10 * MIN, d2, states=states, sessions=both)

    assert action_tuples(d1) == [("a", ActionKind.RESUME, 10.0, Stage.UP)]
    assert allocations(d2) == {"a": 10.0, "b": 0.0}
    assert d2.next_check == local(NOW + 15 * MIN)
    assert action_tuples(d3) == [
        ("a", ActionKind.PAUSE, 0.0, Stage.DOWN),
        ("b", ActionKind.RESUME, 10.0, Stage.UP),
    ]


def test_an_in_flight_resume_still_counts_as_running_for_the_minimum_on_time() -> None:
    chargers = [adj("a"), adj("b")]
    first = snap(
        chargers,
        [idle("a"), idle("b", car=False)],
        [sess("a", ready=time(8, 45), need=0.0)],
        budget=10.0,
    )
    d1 = decide(first)

    second, d2 = run(first, 10 * SECOND, d1)
    _, d3 = run(
        second,
        50 * SECOND,
        d2,
        states=(running("a", 10.0), idle("b")),
        sessions=(
            sess("a", ready=time(8, 45), need=0.0),
            sess("b", ready=time(8, 30), need=0.0),
        ),
    )

    assert action_tuples(d1) == [("a", ActionKind.RESUME, 10.0, Stage.UP)]
    assert d2.actions == ()
    assert allocations(d2)["a"] == 10.0
    assert entry(d2).running
    assert entry(d2).switched_at == NOW
    assert allocations(d3) == {"a": 10.0, "b": 0.0}
    assert d3.actions == ()


def tier_two_pair(ready_b: time) -> Decision:
    prices = hourly([0.10] * 48)
    chargers = [adj("a"), adj("b")]
    first = snap(
        chargers,
        [idle("a"), idle("b", car=False)],
        [sess("a", ready=time(8, 45), need=10.0)],
        prices,
        budget=10.0,
    )
    d1 = decide(first)
    assert by_id(d1)["a"].priority == 2
    assert action_tuples(d1) == [("a", ActionKind.RESUME, 10.0, Stage.UP)]
    _, d2 = run(
        first,
        20 * MIN,
        d1,
        states=(running("a", 10.0), idle("b")),
        sessions=(
            sess("a", ready=time(8, 45), need=10.0),
            sess("b", ready=ready_b, need=10.0),
        ),
    )
    return d2


@pytest.mark.parametrize("ready_b", [time(8, 30), time(8, 15)])
def test_the_hysteresis_holds_in_the_planned_tier_too(ready_b: time) -> None:
    d2 = tier_two_pair(ready_b)

    assert allocations(d2) == {"a": 10.0, "b": 0.0}
    assert by_id(d2)["a"].priority == 2
    assert by_id(d2)["a"].reason.code is ReasonCode.IN_PLAN
    assert by_id(d2)["b"].reason.code is ReasonCode.WAITING_FOR_CAPACITY
    assert d2.actions == ()


def test_a_contender_more_than_the_hysteresis_earlier_displaces_in_the_planned_tier() -> (
    None
):
    d2 = tier_two_pair(time(8, 14))

    assert allocations(d2) == {"a": 0.0, "b": 10.0}
    assert action_tuples(d2) == [
        ("a", ActionKind.PAUSE, 0.0, Stage.DOWN),
        ("b", ActionKind.RESUME, 10.0, Stage.UP),
    ]


def test_a_selected_charger_the_plan_moved_to_later_quarters_stays_moved_without_a_rival() -> (
    None
):
    expensive_now = hourly([0.9, 0.9] + [0.1] * 46)
    first = snap(
        [adj("a")],
        [running("a", 10.0)],
        [sess("a", ready=time(8, 45), need=2.0)],
        expensive_now,
        budget=10.0,
    )
    d1 = decide(first)
    assert d1.memory is not None
    selected = replace(entry(d1), selected=True, running=True)
    carried = replace(d1.memory, chargers=(selected,))

    result = decide(replace(at(first, 5 * MIN), memory=carried))

    assert by_id(result)["a"].reason.code is ReasonCode.PLANNED_LATER
    assert allocations(result)["a"] == 0.0


# --- T7/T8: regulation through decide -------------------------------------------------------


def shared_l1(a_amps: float, **session: object) -> Snapshot:
    """Two charge-now chargers at 11 A each of a 22 A budget; `a` draws `a_amps`."""
    return snap(
        [adj("a"), adj("b")],
        [running("a", 11.0, a_amps), running("b", 11.0)],
        [now_session("a", **session), now_session("b")],
        budget=22.0,
        currents=readings(NOW, 18.0),
    )


def cut_after_a_period(a_amps: float) -> tuple[Snapshot, Decision, Decision]:
    first = shared_l1(a_amps)
    d1 = decide(first)
    second, d2 = run(first, 15 * MIN - SECOND, d1)
    assert d2.actions == ()
    _, d3 = run(second, SECOND, d2)
    return first, d2, d3


def test_nothing_is_cut_before_a_whole_under_use_period() -> None:
    _, d2, _ = cut_after_a_period(7.0)

    assert allocations(d2) == {"a": 11.0, "b": 11.0}
    assert "regulated_cap" not in by_id(d2)["a"].reason.params
    assert entry(d2).under_use_since == NOW


def test_an_under_use_cut_frees_current_to_another_charger_in_the_same_call() -> None:
    _, _, d3 = cut_after_a_period(7.0)

    decision = by_id(d3)["a"]
    assert decision.allocation == 8.0
    assert decision.reason.params["regulated_cap"] == 8
    assert decision.reason.params["capped_by"] == "regulation"
    assert by_id(d3)["b"].allocation == 14.0
    assert action_tuples(d3) == [
        ("a", ActionKind.SET_CURRENT, 8.0, Stage.DOWN),
        ("b", ActionKind.SET_CURRENT, 14.0, Stage.UP),
    ]
    assert entry(d3).regulated_cap == 8


def test_a_cut_below_the_minimum_stops_the_charger_and_remembers_when() -> None:
    _, _, d3 = cut_after_a_period(2.0)

    stopped = by_id(d3)["a"]
    assert stopped.allocation == 0.0
    assert stopped.reason.code is ReasonCode.UNDER_USE
    assert stopped.reason.params["regulated_cap"] == 0
    assert by_id(d3)["b"].allocation == 16.0
    assert action_tuples(d3) == [
        ("a", ActionKind.PAUSE, 0.0, Stage.DOWN),
        ("b", ActionKind.SET_CURRENT, 16.0, Stage.UP),
    ]
    assert entry(d3).regulated_cap == 0
    assert entry(d3).under_use_stop_at == NOW + 15 * MIN


def lone_charger(setting: float = 16.0, amps: float = 7.0) -> Snapshot:
    return snap(
        [adj("a")],
        [running("a", setting, amps)],
        [now_session("a")],
        currents=readings(NOW),
    )


@pytest.mark.parametrize("reduction", list(Reduction))
def test_a_cut_charger_alone_is_cut_to_eight_amps_by_either_method(
    reduction: Reduction,
) -> None:
    first = with_settings(lone_charger(), reduction=reduction)
    d1 = decide(first)

    _, d2 = run(first, 15 * MIN, d1)

    assert action_tuples(d2) == [("a", ActionKind.SET_CURRENT, 8.0, Stage.DOWN)]
    assert by_id(d2)["a"].reason.params["capped_by"] == "regulation"


@pytest.mark.parametrize("reduction", list(Reduction))
def test_a_steady_draw_after_a_cut_is_not_cut_or_ramped_again(
    reduction: Reduction,
) -> None:
    first = with_settings(lone_charger(), reduction=reduction)
    d1 = decide(first)
    snapshot, previous = run(first, 15 * MIN, d1)

    for _ in range(4):
        snapshot, previous = run(
            snapshot, 15 * MIN, previous, states=(running("a", 8.0, 7.0),)
        )

        assert allocations(previous)["a"] == 8.0
        assert previous.actions == ()
        assert entry(previous).regulated_cap == 8
        assert entry(previous).under_use_since is None


def test_whole_ramp_periods_of_full_use_raise_the_cap_back_up_to_the_share() -> None:
    first = lone_charger()
    d1 = decide(first)
    snapshot, previous = run(first, 15 * MIN, d1)
    allocs = [allocations(previous)["a"]]
    caps = [entry(previous).regulated_cap]

    for _ in range(6):
        setting = allocs[-1]
        snapshot, previous = run(
            snapshot, 5 * MIN, previous, states=(running("a", setting, setting - 0.4),)
        )
        allocs.append(allocations(previous)["a"])
        caps.append(entry(previous).regulated_cap)

    assert allocs == [8.0, 8.0, 10.0, 12.0, 14.0, 16.0, 16.0]
    assert caps == [8, 8, 10, 12, 14, None, None]


def never_regulated_cases() -> list[tuple[str, Snapshot]]:
    under = running("a", 11.0, 7.0)
    other = running("b", 11.0)
    pump_a = pump("a", L1, 10.0)
    return [
        (
            "on_off charger",
            snap(
                [pump_a, adj("b")],
                [idle("a", 0.0, paused=False, power=460.0), other],
                [now_session("a"), now_session("b")],
                budget=22.0,
                currents=readings(NOW, 18.0),
            ),
        ),
        (
            "no power reading",
            snap(
                [adj("a"), adj("b")],
                [idle("a", 11.0, paused=False, power=None), other],
                [now_session("a"), now_session("b")],
                budget=22.0,
                currents=readings(NOW, 18.0),
            ),
        ),
        (
            "smart charging off",
            snap(
                [adj("a"), adj("b")],
                [under, other],
                [now_session("a", smart_charging=False), now_session("b")],
                budget=22.0,
                currents=readings(NOW, 18.0),
            ),
        ),
    ]


@pytest.mark.parametrize(
    "case", never_regulated_cases(), ids=[name for name, _ in never_regulated_cases()]
)
def test_chargers_that_cannot_be_regulated_are_never_cut(
    case: tuple[str, Snapshot],
) -> None:
    first = case[1]
    d1 = decide(first)
    second, d2 = run(first, 15 * MIN - SECOND, d1)
    _, d3 = run(second, SECOND, d2)

    assert "regulated_cap" not in by_id(d3)["a"].reason.params
    assert entry(d3).regulated_cap is None
    assert entry(d3).under_use_since is None
    assert by_id(d3)["a"].allocation > 0


def test_a_cut_charger_is_offered_current_again_after_the_probe_time() -> None:
    first = snap(
        [adj("a")],
        [running("a", 10.0, 2.0)],
        [now_session("a")],
        currents=readings(NOW),
    )
    d1 = decide(first)
    second, d2 = run(first, 15 * MIN, d1)
    stop = second.now
    assert by_id(d2)["a"].reason.code is ReasonCode.UNDER_USE
    paused = {"states": (idle("a"),)}
    previous, snapshot = d2, second
    for minutes in (25, 25, 9):
        snapshot, previous = run(snapshot, minutes * MIN, previous, **paused)
        assert by_id(previous)["a"].reason.code is ReasonCode.UNDER_USE
        assert by_id(previous)["a"].allocation == 0.0
        assert previous.actions == ()
    assert snapshot.now == stop + 59 * MIN

    _, probe = run(snapshot, MIN, previous, **paused)

    assert action_tuples(probe) == [("a", ActionKind.RESUME, 16.0, Stage.UP)]
    assert by_id(probe)["a"].reason.code is ReasonCode.CHARGE_NOW
    assert "regulated_cap" not in by_id(probe)["a"].reason.params


def stopped_for_under_use() -> tuple[Snapshot, Decision]:
    first = snap(
        [adj("a")],
        [running("a", 10.0, 2.0)],
        [now_session("a")],
        currents=readings(NOW),
    )
    d1 = decide(first)
    second, d2 = run(first, 15 * MIN, d1, states=(running("a", 10.0, 2.0),))
    assert by_id(d2)["a"].reason.code is ReasonCode.UNDER_USE
    return second, d2


@pytest.mark.parametrize(
    "changes",
    [
        {"sessions": "plugged"},
        {"sessions": "ready"},
        {"sessions": "need"},
        {"sessions": "limit"},
    ],
    ids=["plugged in again", "ready time", "energy need", "current limit"],
)
def test_a_session_change_clears_an_under_use_stop_early(
    changes: dict[str, str],
) -> None:
    second, d2 = stopped_for_under_use()
    variants = {
        "plugged": now_session("a", plugged_in_at=NOW + 19 * MIN),
        "ready": now_session("a", ready=time(8, 0)),
        "need": now_session("a", need=12.0),
        "limit": now_session("a", user_current_limit=12),
    }

    _, result = run(
        second,
        5 * MIN,
        d2,
        states=(idle("a"),),
        sessions=(variants[changes["sessions"]],),
    )

    assert [a.kind for a in result.actions] == [ActionKind.RESUME]
    assert "regulated_cap" not in by_id(result)["a"].reason.params


def test_a_replug_clears_an_under_use_stop_early() -> None:
    second, d2 = stopped_for_under_use()
    unplugged, d3 = run(second, 2 * MIN, d2, states=(idle("a", car=False),))

    _, d4 = run(unplugged, 3 * MIN, d3, states=(idle("a"),))

    assert [a.kind for a in d4.actions] == [ActionKind.RESUME]
    assert entry(d4).regulated_cap is None


def test_the_same_plug_in_time_written_with_another_offset_is_not_a_replug() -> None:
    plugged = datetime(2026, 10, 21, 18, 0, tzinfo=UTC)
    state = running("a", 16.0, 7.0)
    first = snap(
        [adj("a")],
        [state],
        [now_session("a", plugged_in_at=plugged)],
        currents=readings(NOW),
    )
    d1 = decide(first)
    same_instant = plugged.astimezone(timezone(timedelta(hours=2)))

    _, d2 = run(
        first, 15 * MIN, d1, sessions=(now_session("a", plugged_in_at=same_instant),)
    )

    assert action_tuples(d2) == [("a", ActionKind.SET_CURRENT, 8.0, Stage.DOWN)]
    assert by_id(d2)["a"].reason.params["capped_by"] == "regulation"


def test_a_smart_charging_off_period_forgets_the_under_use_timers() -> None:
    under = running("a", 16.0, 7.0)
    full = running("a", 16.0)
    first = snap([adj("a")], [under], [now_session("a")], currents=readings(NOW))
    d1 = decide(first)
    off = now_session("a", smart_charging=False)
    second, d2 = run(first, 5 * MIN, d1, states=(full,), sessions=(off,))
    third, d3 = run(second, 5 * MIN, d2)
    fourth, d4 = run(third, 10 * MIN, d3, states=(under,), sessions=(now_session("a"),))

    assert entry(d2).under_use_since is None
    assert allocations(d4)["a"] == 16.0
    assert "regulated_cap" not in by_id(d4)["a"].reason.params
    assert d4.actions == ()
    assert entry(d4).under_use_since == fourth.now


# --- T9/T10: the phase guard through decide ---------------------------------------------------


def guard_snapshot(**kwargs: object) -> Snapshot:
    return snap(
        [adj("a")],
        [running("a", 6.0)],
        [now_session("a")],
        **kwargs,  # type: ignore[arg-type]  # test passes through
    )


def test_red_cuts_to_fit_and_raises_the_guard_event() -> None:
    first = snap(
        [adj("a")],
        [running("a", 16.0)],
        [now_session("a")],
        currents=readings(NOW, 33.0),
    )

    result = decide(first)

    assert result.guard_level is GuardLevel.RED
    assert action_tuples(result) == [("a", ActionKind.SET_CURRENT, 15.0, Stage.DOWN)]
    events = [e for e in result.events if e.kind is EventKind.PHASE_GUARD_INTERVENED]
    assert [e.params for e in events] == [{"level": "red"}]


def test_yellow_allows_no_increase_and_raises_no_event() -> None:
    result = decide(guard_snapshot(currents=readings(NOW, 30.0)))

    held = by_id(result)["a"]
    assert result.guard_level is GuardLevel.YELLOW
    assert held.allocation == 6.0
    assert held.reason.params["held_by"] == "phase_guard"
    assert "capped_by" not in held.reason.params
    assert result.actions == ()
    assert EventKind.PHASE_GUARD_INTERVENED not in kinds(result)


def test_just_below_yellow_is_green_and_allows_the_increase() -> None:
    result = decide(guard_snapshot(currents=readings(NOW, 29.99)))

    assert result.guard_level is GuardLevel.GREEN
    assert action_tuples(result) == [("a", ActionKind.SET_CURRENT, 8.0, Stage.UP)]


def test_increases_wait_for_the_green_hold_after_a_yellow_call() -> None:
    first = guard_snapshot(currents=readings(NOW, 30.0))
    d1 = decide(first)

    second, d2 = run(first, MIN, d1, currents=readings(NOW + MIN, 10.0))
    third, d3 = run(second, MIN, d2, currents=readings(NOW + 2 * MIN, 10.0))

    assert d1.actions == ()
    assert by_id(d2)["a"].reason.params["held_by"] == "phase_guard"
    assert d2.actions == ()
    assert d2.next_check == local(NOW + 2 * MIN)
    assert action_tuples(d3) == [("a", ActionKind.SET_CURRENT, 16.0, Stage.UP)]
    assert third.now == NOW + 2 * MIN


def test_stale_measurements_leave_increases_to_the_fallback_cap() -> None:
    first = snap([adj("a")], [idle("a")], [now_session("a")], currents=None)

    result = decide(first)

    assert result.guard_level is None
    assert result.status is Status.PHASE_DATA_STALE
    assert action_tuples(result) == [("a", ActionKind.RESUME, 6.0, Stage.UP)]
    assert by_id(result)["a"].reason.params["capped_by"] == "fallback"
    assert "held_by" not in by_id(result)["a"].reason.params


def test_a_stale_call_in_the_middle_restarts_the_green_hold() -> None:
    first = snap([adj("a")], [running("a", 16.0)], [now_session("a")], currents=None)
    d1 = decide(first)

    second, d2 = run(
        first,
        5 * MIN,
        d1,
        states=(running("a", 6.0),),
        currents=readings(NOW + 5 * MIN, 6.0),
    )
    _, d3 = run(second, 5 * MIN, d2, states=(running("a", 6.0),))

    assert d1.guard_level is None
    assert action_tuples(d1) == [("a", ActionKind.SET_CURRENT, 6.0, Stage.DOWN)]
    assert by_id(d2)["a"].reason.params["held_by"] == "phase_guard"
    assert d2.actions == ()
    assert d2.next_check == local(NOW + 6 * MIN)
    assert action_tuples(d3) == [("a", ActionKind.SET_CURRENT, 16.0, Stage.UP)]


def emergency_installation() -> Snapshot:
    chargers = [
        adj("a", L1),
        adj("s", L2),
        pump("p", L3, 10.0),
        fixed("f", L3, 5.0),
    ]
    states = [
        running("a", 16.0),
        running("s", 10.0),
        idle("p", 0.0, paused=False, power=2300.0),
        idle("f", 5.0, paused=False),
    ]
    sessions = [
        now_session("a"),
        sess("s", smart_charging=False),
        sess("p", need=0.0),
    ]
    first = snap(chargers, states, sessions, currents=readings(NOW, 41.0, 10.0, 15.0))
    return with_settings(first, emergency_threshold=40.0)


def all_stopped() -> tuple[ChargerState, ...]:
    return (idle("a"), idle("s"), idle("p"), idle("f", 5.0, paused=False))


def test_an_emergency_pauses_every_controllable_charger_and_reports_it() -> None:
    result = decide(emergency_installation())

    assert result.guard_level is GuardLevel.EMERGENCY
    assert result.status is Status.EMERGENCY
    for cid in "asp":
        assert by_id(result)[cid].allocation == 0.0
        assert by_id(result)[cid].reason.code is ReasonCode.EMERGENCY
    assert by_id(result)["f"].allocation == 5.0
    assert action_tuples(result) == [
        ("a", ActionKind.PAUSE, 0.0, Stage.DOWN),
        ("p", ActionKind.PAUSE, 0.0, Stage.DOWN),
        ("s", ActionKind.PAUSE, 0.0, Stage.DOWN),
    ]
    events = [e for e in result.events if e.kind is EventKind.PHASE_GUARD_INTERVENED]
    assert [e.params for e in events] == [{"level": "emergency"}]


def test_an_emergency_restarts_only_after_the_calm_period() -> None:
    first = emergency_installation()
    d1 = decide(first)
    green = (10.0, 10.0, 10.0)
    snapshot, previous = first, d1
    waits = [MIN, 2 * MIN, 2 * MIN + 59 * SECOND]  # at +1, +3 and +5:59
    for index, wait in enumerate(waits):
        snapshot, previous = run(
            snapshot,
            wait,
            previous,
            states=all_stopped(),
            currents=readings(snapshot.now + wait, *green),
        )

        assert previous.guard_level is GuardLevel.EMERGENCY
        assert previous.status is Status.EMERGENCY
        assert previous.actions == ()
        if index == 0:
            assert previous.next_check == local(NOW + 6 * MIN)

    _, released = run(
        snapshot,
        SECOND,
        previous,
        states=all_stopped(),
        currents=readings(snapshot.now + SECOND, *green),
    )

    assert released.guard_level is GuardLevel.GREEN
    resumed = {a.charger_id for a in released.actions if a.kind is ActionKind.RESUME}
    assert resumed == {"a", "s"}  # the pump is tier 3 and waits behind charge now
    assert by_id(released)["p"].reason.code is ReasonCode.WAITING_FOR_HIGHER_PRIORITY


def test_a_yellow_call_during_the_calm_restarts_it() -> None:
    first = emergency_installation()
    d1 = decide(first)
    snapshot, previous = first, d1
    plan = [(MIN, 10.0), (2 * MIN, 31.0), (MIN, 10.0), (2 * MIN, 10.0)]
    for wait, l1 in plan:
        snapshot, previous = run(
            snapshot,
            wait,
            previous,
            states=all_stopped(),
            currents=readings(snapshot.now + wait, l1, 10.0, 10.0),
        )

        assert previous.guard_level is GuardLevel.EMERGENCY

    _, released = run(
        snapshot,
        3 * MIN,
        previous,
        states=all_stopped(),
        currents=readings(snapshot.now + 3 * MIN, 10.0, 10.0, 10.0),
    )

    assert released.guard_level is GuardLevel.GREEN


def test_stale_measurements_do_not_release_an_emergency() -> None:
    first = emergency_installation()
    d1 = decide(first)

    _, d2 = run(first, MIN, d1, states=all_stopped(), currents=None)

    assert d2.guard_level is GuardLevel.EMERGENCY
    assert d2.status is Status.EMERGENCY
    assert d2.actions == ()
    assert all(allocations(d2)[cid] == 0.0 for cid in "asp")


def test_without_an_emergency_threshold_red_is_the_strongest_reaction() -> None:
    first = replace(
        emergency_installation(), settings=emergency_installation().settings
    )
    first = with_settings(first, emergency_threshold=None)

    result = decide(first)

    assert result.guard_level is GuardLevel.RED
    assert result.status is not Status.EMERGENCY
    assert by_id(result)["f"].allocation == 5.0


def test_an_emergency_countermands_a_resume_still_in_flight() -> None:
    first = with_settings(
        snap([adj("a")], [idle("a")], [now_session("a")]), emergency_threshold=40.0
    )
    d1 = decide(first)

    _, d2 = run(first, 10 * SECOND, d1, currents=readings(NOW + 10 * SECOND, 41.0))

    assert action_tuples(d1) == [("a", ActionKind.RESUME, 16.0, Stage.UP)]
    assert d2.guard_level is GuardLevel.EMERGENCY
    assert allocations(d2)["a"] == 0.0
    assert action_tuples(d2) == [("a", ActionKind.PAUSE, 0.0, Stage.DOWN)]


# --- T11: pacing ------------------------------------------------------------------------------


def cut_to_ten() -> tuple[Snapshot, Decision]:
    first = snap([adj("a")], [running("a", 16.0)], [now_session("a")], budget=10.0)
    d1 = decide(first)
    assert action_tuples(d1) == [("a", ActionKind.SET_CURRENT, 10.0, Stage.DOWN)]
    return first, d1


def test_an_increase_inside_the_action_interval_is_held_and_a_decrease_is_not() -> None:
    first, d1 = cut_to_ten()
    roomy_settings = replace(first.settings, phase_budget=25.0)

    _, held = run(
        first, 10 * SECOND, d1, states=(running("a", 10.0),), settings=roomy_settings
    )
    _, later = run(
        first, 30 * SECOND, d1, states=(running("a", 10.0),), settings=roomy_settings
    )
    _, down = run(
        first,
        10 * SECOND,
        d1,
        states=(running("a", 10.0),),
        settings=replace(first.settings, phase_budget=6.0),
    )

    pacing = by_id(held)["a"]
    assert pacing.allocation == 10.0
    assert pacing.reason.params["held_by"] == "pacing"
    assert "capped_by" not in pacing.reason.params
    assert held.actions == ()
    assert held.next_check == local(NOW + 30 * SECOND)
    assert action_tuples(later) == [("a", ActionKind.SET_CURRENT, 16.0, Stage.UP)]
    assert action_tuples(down) == [("a", ActionKind.SET_CURRENT, 6.0, Stage.DOWN)]


def test_a_command_still_in_flight_is_not_sent_again() -> None:
    first, d1 = cut_to_ten()

    _, d2 = run(first, 10 * SECOND, d1)  # the car still reports 16 A

    assert allocations(d2)["a"] == 10.0
    assert d2.actions == ()


def test_a_decrease_in_flight_is_not_raised_by_a_roomier_share() -> None:
    first, d1 = cut_to_ten()

    _, d2 = run(
        first, 10 * SECOND, d1, settings=replace(first.settings, phase_budget=12.0)
    )

    assert allocations(d2)["a"] == 10.0
    assert by_id(d2)["a"].reason.params["held_by"] == "pacing"
    assert d2.actions == ()


def test_an_increase_in_flight_is_cut_back_when_the_share_shrinks() -> None:
    first = snap(
        [adj("a"), adj("b", high=13)],
        [running("a", 10.0), idle("b", car=False)],
        [now_session("a")],
        currents=readings(NOW, 10.0),
    )
    d1 = decide(first)
    assert action_tuples(d1) == [("a", ActionKind.SET_CURRENT, 16.0, Stage.UP)]

    _, d2 = run(
        first,
        10 * SECOND,
        d1,
        states=(running("a", 10.0), idle("b")),
        sessions=(now_session("a"), sess("b", smart_charging=False)),
    )

    assert allocations(d2) == {"a": 12.0, "b": 13.0}
    assert action_tuples(d2) == [
        ("a", ActionKind.SET_CURRENT, 12.0, Stage.DOWN),
        ("b", ActionKind.RESUME, 13.0, Stage.UP),
    ]


def settling_start() -> tuple[Snapshot, Decision]:
    first = snap(
        [adj("a")],
        [running("a", 16.0)],
        [now_session("a")],
        currents=readings(NOW, 34.0),
    )
    d1 = decide(first)
    assert action_tuples(d1) == [("a", ActionKind.SET_CURRENT, 14.0, Stage.DOWN)]
    assert d1.guard_level is GuardLevel.RED
    return first, d1


@pytest.mark.parametrize("delta", [10 * SECOND, 29 * SECOND])
def test_a_second_decrease_is_not_compounded_while_the_first_settles(
    delta: timedelta,
) -> None:
    first, d1 = settling_start()
    still_drawing = idle("a", 14.0, paused=False, power=16 * 230.0)

    _, d2 = run(first, delta, d1, states=(still_drawing,))

    assert allocations(d2)["a"] == 14.0
    assert d2.actions == ()


def test_a_decrease_that_never_landed_is_not_repeated_either() -> None:
    first, d1 = settling_start()

    _, d2 = run(first, 10 * SECOND, d1)

    assert allocations(d2)["a"] == 14.0
    assert d2.actions == ()


def test_once_the_window_closes_the_remaining_overload_is_cut_again() -> None:
    first, d1 = settling_start()
    still_drawing = idle("a", 14.0, paused=False, power=16 * 230.0)

    _, d2 = run(first, 30 * SECOND, d1, states=(still_drawing,))

    assert action_tuples(d2) == [("a", ActionKind.SET_CURRENT, 12.0, Stage.DOWN)]


# --- T12: when to call next ---------------------------------------------------------------------


def idle_installation(**kwargs: object) -> Snapshot:
    """No car anywhere and stale prices: nothing is pending."""
    return snap(
        [adj("a")],
        [idle("a", car=False)],
        [],
        PriceSeries((), NOW - timedelta(hours=3)),
        **kwargs,  # type: ignore[arg-type]  # test passes through
    )


def test_next_check_is_none_when_nothing_is_pending() -> None:
    assert decide(idle_installation()).next_check is None


def test_next_check_is_the_end_of_the_green_hold() -> None:
    first = replace(idle_installation(), memory=None)

    result = decide(first)

    assert result.next_check == local(NOW + 60 * SECOND)


def test_next_check_is_the_price_series_expiring() -> None:
    first = with_settings(
        snap([adj("a")], [idle("a", car=False)], [], PriceSeries((), NOW)),
        max_memory_age=timedelta(hours=3),
    )

    result = decide(first)

    assert result.next_check == local(NOW + 60 * MIN)


def test_next_check_is_clamped_to_the_memory_expiring() -> None:
    first = snap([adj("a")], [idle("a", car=False)], [], PriceSeries((), NOW))

    result = decide(first)

    assert result.next_check == local(NOW + 30 * MIN)


def test_next_check_is_the_next_quarter_when_a_session_has_a_plan() -> None:
    prices = hourly([0.2] * 48)
    first = snap([adj("a")], [running("a", 16.0)], [sess("a", need=10.0)], prices)

    result = decide(first)

    assert by_id(result)["a"].plan
    assert result.next_check == local(NOW + 15 * MIN)


def test_next_check_is_a_deadline_still_ahead() -> None:
    first = snap(
        [adj("a")],
        [running("a", 16.0)],
        [sess("a", ready=time(22, 10), need=0.0)],
        PriceSeries((), NOW - timedelta(hours=3)),
    )

    result = decide(first)

    assert result.next_check == local(NOW + 10 * MIN)


def test_next_check_is_the_under_use_period_ending() -> None:
    first = replace(lone_charger(), memory=warm(NOW))
    first = replace(first, prices=PriceSeries((), NOW - timedelta(hours=3)))

    result = decide(first)

    assert result.next_check == local(NOW + 15 * MIN)


def test_next_check_is_the_end_of_the_action_window_when_it_comes_first() -> None:
    first = snap([adj("a")], [idle("a")], [now_session("a")])
    d1 = decide(first)

    assert action_tuples(d1) == [("a", ActionKind.RESUME, 16.0, Stage.UP)]
    assert d1.next_check == local(NOW + 30 * SECOND)


def test_next_check_is_the_probe_time_of_a_stopped_charger() -> None:
    second, d2 = stopped_for_under_use()
    long_memory = replace(
        second.settings,
        max_memory_age=timedelta(hours=3),
        max_price_age=timedelta(hours=3),
    )

    _, d3 = run(second, MIN, d2, states=(idle("a"),), settings=long_memory)

    assert d3.next_check == local(second.now + 60 * MIN)  # an hour after the stop


def test_next_check_is_the_end_of_a_ramp_period() -> None:
    first = lone_charger()
    d1 = decide(first)
    snapshot, cut = run(first, 15 * MIN, d1)

    _, d3 = run(snapshot, MIN, cut, states=(running("a", 8.0, 7.6),))

    assert d3.next_check == local(snapshot.now + MIN + 5 * MIN)


def test_next_check_is_the_earliest_of_several_sources() -> None:
    first, d1 = paused_for_capacity()

    _, d2 = run(first, 5 * MIN, d1, states=(idle("a"),), **roomy(first))

    # pause ends at +15 min, memory goes out of date at +35 min, prices at +60 min
    assert d2.next_check == local(NOW + 15 * MIN)


def test_next_check_is_never_later_than_the_memory_expiring() -> None:
    first = snap(
        [adj("a")],
        [running("a", 16.0)],
        [sess("a", need=0.0)],
        PriceSeries((), NOW),
    )
    d1 = decide(first)

    assert d1.next_check is not None
    assert d1.next_check <= NOW + timedelta(minutes=30)
    _, d2 = run(first, d1.next_check - NOW, d1)
    assert reason_of(d2) is None


def test_next_check_is_in_the_installation_timezone_across_the_fall_back_night() -> (
    None
):
    now = datetime(2026, 10, 25, 0, 50, tzinfo=UTC)  # 02:50 CEST
    first = snap(
        [adj("a")],
        [running("a", 16.0)],
        [sess("a", need=10.0)],
        hourly([0.2] * 24, start=now - timedelta(minutes=5)),
        now=now,
    )

    result = decide(first)

    assert result.next_check is not None
    # compared as instants: a fold time never equals a time in another zone
    assert result.next_check.astimezone(UTC) == datetime(2026, 10, 25, 1, 0, tzinfo=UTC)
    assert result.next_check.utcoffset() == timedelta(hours=1)  # 02:00 CET


def test_next_check_adds_durations_in_utc_across_the_fold() -> None:
    now = datetime(2026, 10, 25, 2, 59, 30, tzinfo=ZONE)  # fold=0, 00:59:30 UTC
    first = replace(
        snap(
            [adj("a")],
            [idle("a", car=False)],
            [],
            PriceSeries((), now - timedelta(hours=3)),
            now=now,
        ),
        memory=None,
    )

    result = decide(first)

    assert result.next_check is not None
    assert result.next_check.astimezone(UTC) == datetime(
        2026, 10, 25, 1, 0, 30, tzinfo=UTC
    )


# --- T13: the session start comes from memory ------------------------------------------------------


def session_start_run(minutes: list[int]) -> Decision:
    start = datetime(2026, 10, 21, 4, 0, tzinfo=UTC)  # 06:00 local
    first = with_settings(
        snap(
            [adj("a")],
            [idle("a")],
            [sess("a", ready=time(7, 0), need=10.0)],
            now=start,
            currents=readings(start),
        ),
        max_price_age=timedelta(hours=6),
    )
    snapshot, previous = first, decide(first)
    for minute in minutes:
        snapshot, previous = run(snapshot, timedelta(minutes=minute), previous)
    return previous


def test_the_deadline_is_seen_from_the_session_start_held_in_memory() -> None:
    result = session_start_run([25, 25, 25])  # calls at 06:00, 06:25, 06:50, 07:15

    decision = by_id(result)["a"]
    assert decision.reason.code is ReasonCode.READY_TIME_PASSED
    assert decision.priority == 3
    assert decision.deadline == datetime(2026, 10, 21, 7, 0, tzinfo=ZONE)


def test_without_memory_the_deadline_is_seen_from_now() -> None:
    start = datetime(2026, 10, 21, 5, 15, tzinfo=UTC)  # 07:15 local
    first = replace(
        snap(
            [adj("a")],
            [idle("a")],
            [sess("a", ready=time(7, 0), need=10.0)],
            now=start,
        ),
        memory=None,
    )

    decision = by_id(decide(first))["a"]

    assert decision.deadline == datetime(2026, 10, 22, 7, 0, tzinfo=ZONE)


def test_a_replug_restarts_the_deadline() -> None:
    start = datetime(2026, 10, 21, 4, 50, tzinfo=UTC)  # 06:50 local
    first = snap(
        [adj("a")],
        [idle("a")],
        [sess("a", ready=time(7, 0), need=10.0)],
        now=start,
        currents=readings(start),
    )
    snapshot, previous = first, decide(first)
    snapshot, previous = run(
        snapshot, 25 * MIN, previous, states=(idle("a", car=False),)
    )
    _, last = run(snapshot, 10 * MIN, previous, states=(idle("a"),))

    # plugged in again at 07:25, after the ready time: the next one is tomorrow's
    assert by_id(last)["a"].deadline == datetime(2026, 10, 22, 7, 0, tzinfo=ZONE)


def test_a_given_plugged_in_time_is_the_session_start() -> None:
    start = datetime(2026, 10, 21, 5, 15, tzinfo=UTC)  # 07:15 local
    plugged = datetime(2026, 10, 21, 4, 0, tzinfo=UTC)
    first = snap(
        [adj("a")],
        [idle("a")],
        [sess("a", ready=time(7, 0), need=10.0, plugged_in_at=plugged)],
        now=start,
    )

    decision = by_id(decide(first))["a"]

    assert decision.deadline == datetime(2026, 10, 21, 7, 0, tzinfo=ZONE)


# --- the new memory itself -------------------------------------------------------------------------


def test_the_new_memory_lists_the_snapshot_chargers_in_order_and_drops_unknown_ones() -> (
    None
):
    chargers = [adj("z"), adj("a"), fixed("f")]
    ghost = ChargerMemory("ghost", running=True, switched_at=NOW - 2 * MIN)
    memory = Memory(
        saved_at=NOW - MIN, chargers=(ghost,), green_since=NOW - timedelta(hours=1)
    )
    first = replace(
        snap(chargers, [idle("z"), idle("a"), idle("f", 10.0, paused=False)], []),
        memory=memory,
    )

    result = decide(first)

    assert result.memory is not None
    assert result.memory.saved_at == NOW
    assert [e.charger_id for e in result.memory.chargers] == ["z", "a", "f"]


def test_the_new_memory_records_sessions_actions_and_the_guard() -> None:
    first = snap([adj("a")], [idle("a")], [now_session("a")])

    result = decide(first)

    stored = entry(result)
    assert result.memory is not None
    assert result.memory.level is GuardLevel.GREEN
    assert result.memory.green_since == NOW - timedelta(hours=1)
    assert stored.car_connected
    assert stored.running
    assert stored.selected
    assert stored.session_start == NOW
    assert stored.switched_at == NOW
    assert not stored.stopped_for_capacity
    assert stored.last_target == 16.0
    assert stored.last_action_at == NOW
    assert stored.decreased_at is None


def test_the_memory_round_trips_through_json_and_feeds_the_next_call() -> None:
    first = snap([adj("a")], [idle("a")], [now_session("a")])
    d1 = decide(first)
    assert d1.memory is not None
    data = json.loads(json.dumps(memory_to_dict(d1.memory)))

    restored = memory_from_dict(data)
    _, d2 = run(first, MIN, d1, memory=restored)

    assert restored == d1.memory
    assert reason_of(d2) is None


def test_deciding_twice_from_the_same_memory_gives_the_same_decision() -> None:
    first = snap([adj("a")], [running("a", 16.0, 7.0)], [now_session("a")])
    d1 = decide(first)
    second = at(first, 15 * MIN, d1)

    assert decide(second) == decide(second)
    assert second.memory is d1.memory


# --- T15: invariants over multi-call sequences -------------------------------------------------------


def sequence_settings(rng: random.Random, base: Settings) -> Settings:
    """Round-2 settings drawn from a small set of values, so timers actually fire."""
    emergency = rng.choice(
        [None, None, base.phase_threshold + rng.choice([2.0, 5.0, 12.0])]
    )
    return replace(
        base,
        emergency_threshold=emergency,
        emergency_calm=timedelta(seconds=rng.choice([60, 300, 600])),
        yellow_margin=rng.choice([0.0, 2.0, 4.0]),
        green_hold=timedelta(seconds=rng.choice([20, 60, 120])),
        deadband=rng.choice([0.0, 1.0, 2.0, 4.0]),
        ramp_step=rng.randint(1, 3),
        ramp_period=timedelta(seconds=rng.choice([60, 300])),
        under_use_period=timedelta(seconds=rng.choice([60, 300, 900])),
        reduction=rng.choice(list(Reduction)),
        under_use_margin=rng.choice([0.5, 1.0, 2.0]),
        min_pause=timedelta(seconds=rng.choice([0, 120, 900])),
        hysteresis=timedelta(seconds=rng.choice([0, 1800])),
        resume_probe=timedelta(seconds=rng.choice([300, 3600])),
        min_action_interval=timedelta(seconds=rng.choice([0, 30, 120])),
    )


def next_states(
    rng: random.Random,
    snapshot: Snapshot,
    result: Decision,
    draws: dict[str, float],
) -> tuple[ChargerState, ...]:
    """Let the hardware follow the actions, mostly, and drift a little.

    `draws` is how much of its setting each car keeps drawing, fixed per sequence so
    that under-use lasts long enough to be regulated.
    """
    actions = {a.charger_id: a for a in result.actions}
    chargers = {c.id: c for c in snapshot.chargers}
    states: list[ChargerState] = []
    for old in snapshot.states:
        charger = chargers[old.charger_id]
        setting, paused = old.current_setting, old.paused
        action = actions.get(old.charger_id)
        if action is not None and rng.random() < 0.75:
            if action.kind is ActionKind.PAUSE:
                paused = True
            else:
                paused = False
                setting = action.target_current
        car = old.car_connected if rng.random() > 0.03 else not old.car_connected
        flip = 0.5 if not old.available else 0.01
        available = old.available if rng.random() > flip else not old.available
        nominal = (
            setting
            if charger.kind is ChargerKind.ADJUSTABLE
            else (charger.assumed_current or 0.0)
        )
        power: float | None = None
        if rng.random() < 0.85:
            if paused or not car:
                power = rng.choice([0.0, 0.0, rng.uniform(0, 3000)])
            else:
                factor = draws.setdefault(
                    old.charger_id, rng.choice([0.2, 0.5, 0.95, 1.0, 1.0])
                )
                power = nominal * 230.0 * len(charger.phases) * factor
        states.append(
            ChargerState(old.charger_id, car, setting, paused, power, available)
        )
    return tuple(states)


def next_snapshot(
    rng: random.Random,
    snapshot: Snapshot,
    result: Decision,
    draws: dict[str, float],
) -> Snapshot:
    delta = timedelta(
        seconds=rng.choice(
            [1, 5, 10, 29, 30, 31, 60, 120, 300, 300, 600, 900, 900, 900, 1500, 2400]
        )
    )
    now = snapshot.now + delta
    sessions = list(snapshot.sessions)
    if sessions and rng.random() < 0.12:
        index = rng.randrange(len(sessions))
        sessions[index] = random_session(rng, sessions[index].charger_id, now)
    old_prices = snapshot.prices
    prices = PriceSeries(old_prices.intervals, old_prices.updated_at + delta)
    currents: PhaseCurrents | None = None
    if rng.random() < 0.92:
        currents = PhaseCurrents(
            now + timedelta(seconds=rng.choice([0, 0, 0, rng.randint(-50, 50), 400])),
            *(
                rng.choice(
                    [rng.uniform(0, 28)] * 5
                    + [rng.uniform(28, 34)] * 2
                    + [rng.uniform(34, 48), None]
                )
                for _ in Phase
            ),
        )
    memory = result.memory if rng.random() < 0.92 else None
    return replace(
        snapshot,
        now=now,
        states=next_states(rng, snapshot, result, draws),
        sessions=tuple(sessions),
        prices=prices,
        phase_currents=currents,
        memory=memory,
    )


def utc(moment: datetime) -> datetime:
    return moment.astimezone(UTC)


def previous_memory(snapshot: Snapshot) -> Memory | None:
    """The memory `decide` should accept, worked out without its helpers."""
    memory = snapshot.memory
    if memory is None:
        return None
    age = utc(snapshot.now) - utc(memory.saved_at)
    if age < timedelta(0) or age > snapshot.settings.max_memory_age:
        return None
    return memory


def assert_sequence_invariants(snapshot: Snapshot, result: Decision) -> None:
    """Check one call of a sequence against the promises, independently of `decide`."""
    cfg = snapshot.settings
    now = utc(snapshot.now)
    states = {s.charger_id: s for s in snapshot.states}
    sessions = {s.charger_id: s for s in snapshot.sessions}
    decisions = {c.charger_id: c for c in result.chargers}
    prev = previous_memory(snapshot)
    interval = cfg.min_action_interval
    assert [c.charger_id for c in result.chargers] == [c.id for c in snapshot.chargers]

    def reserved(c: Charger) -> bool:
        return c.kind is ChargerKind.FIXED or not states[c.id].available

    def acts(c: Charger) -> bool:
        return not reserved(c) and states[c.id].car_connected

    def entry_of(c: Charger) -> ChargerMemory | None:
        return prev.charger(c.id) if prev is not None else None

    def within(moment: datetime | None, span: timedelta) -> bool:
        return moment is not None and timedelta(0) <= now - utc(moment) < span

    def present_of(c: Charger) -> float:
        state = states[c.id]
        if state.paused:
            return 0.0
        return (
            state.current_setting
            if c.kind is ChargerKind.ADJUSTABLE
            else (c.assumed_current or 0.0)
        )

    def per_phase(c: Charger) -> float | None:
        power = states[c.id].measured_power_w
        return None if power is None else power / (cfg.voltage * len(c.phases))

    controllable_phases = {
        p for c in snapshot.chargers if not reserved(c) for p in c.phases
    }
    pc = snapshot.phase_currents
    fresh = (
        pc is not None
        and abs((now - utc(pc.measured_at)).total_seconds())
        <= cfg.max_measurement_age.total_seconds()
        and all(pc.get(p) is not None for p in controllable_phases)
    )

    # memory: usable or a restart
    resets = [e for e in result.events if e.kind is EventKind.MEMORY_RESET]
    if prev is None:
        assert len(resets) == 1
        reason = snapshot.memory is None and "missing" or None
        if reason is None:
            assert resets[0].params["reason"] in ("out_of_date", "future")
        else:
            assert resets[0].params["reason"] == reason
    else:
        assert not resets

    # the guard
    level = result.guard_level
    if fresh and pc is not None:
        assert level is not None
        readings_now = [
            v
            for p in Phase
            if p in controllable_phases and (v := pc.get(p)) is not None
        ]
        over_emergency = cfg.emergency_threshold is not None and any(
            v > cfg.emergency_threshold for v in readings_now
        )
        if over_emergency:
            assert level is GuardLevel.EMERGENCY
    else:
        latched = prev is not None and prev.level is GuardLevel.EMERGENCY
        assert level is None or (level is GuardLevel.EMERGENCY and latched)
    if cfg.emergency_threshold is None:
        assert level is not GuardLevel.EMERGENCY
    assert (level is GuardLevel.EMERGENCY) == (result.status is Status.EMERGENCY)
    blocked = bool(resets) or (
        fresh and level in (GuardLevel.YELLOW, GuardLevel.RED, GuardLevel.EMERGENCY)
    )
    green_held = (
        fresh
        and prev is not None
        and (prev.green_since is None or now - utc(prev.green_since) < cfg.green_hold)
    )

    stages: list[Stage] = []
    seen: set[str] = set()
    for sent in result.actions:
        assert sent.charger_id not in seen
        seen.add(sent.charger_id)
        stages.append(sent.stage)
    assert stages == sorted(stages, key=lambda s: s is Stage.UP)
    for stage in Stage:
        ids = [a.charger_id for a in result.actions if a.stage is stage]
        assert ids == sorted(ids)
    by_charger = {a.charger_id: a for a in result.actions}

    for c in snapshot.chargers:
        state, decision = states[c.id], decisions[c.id]
        alloc = decision.allocation
        if reserved(c):
            measured = per_phase(c) or 0.0
            level_c = (
                c.assumed_current or 0.0
                if c.kind is not ChargerKind.ADJUSTABLE
                else state.current_setting
            )
            assert alloc == pytest.approx(max(level_c, measured))
            assert c.id not in by_charger
            continue
        if not state.car_connected or sessions.get(c.id) is None:
            assert alloc == 0.0
        if decision.priority == 4:
            assert alloc == 0.0
        if level is GuardLevel.EMERGENCY:
            assert alloc == 0.0
        if not acts(c):
            assert c.id not in by_charger
            continue
        stored = entry_of(c)
        window = (
            stored is not None
            and stored.last_target is not None
            and within(stored.last_action_at, interval)
        )
        last_sent = stored.last_target if stored is not None else None
        present = present_of(c)
        command = last_sent if window and last_sent is not None else present
        action = by_charger.get(c.id)
        in_flight = window and last_sent is not None and abs(last_sent - alloc) <= 1e-9
        # the command the hardware ends up at is the allocation (nothing is left in flight)
        if action is not None:
            effective = action.target_current
        elif in_flight and last_sent is not None:
            effective = last_sent
        else:
            effective = command
        assert effective == pytest.approx(alloc, abs=1e-9)
        # an action exists exactly when the allocation differs from where it is going
        expected: tuple[ActionKind, float, Stage] | None
        if in_flight:
            expected = None
        elif alloc <= 0:
            expected = (ActionKind.PAUSE, 0.0, Stage.DOWN) if command > 0 else None
        elif window:
            expected = (
                None
                if abs(alloc - command) <= 1e-9
                else (
                    ActionKind.SET_CURRENT,
                    alloc,
                    Stage.DOWN if alloc < command else Stage.UP,
                )
            )
        elif state.paused:
            expected = (ActionKind.RESUME, alloc, Stage.UP)
        elif abs(alloc - present) > 1e-9:
            expected = (
                ActionKind.SET_CURRENT,
                alloc,
                Stage.DOWN if alloc < present else Stage.UP,
            )
        else:
            expected = None
        got = (
            None
            if action is None
            else (action.kind, action.target_current, action.stage)
        )
        assert got == expected
        # increases
        increases = (
            action is not None and action.stage is Stage.UP
        ) or alloc > command + 1e-6
        if blocked:
            assert not increases
        if green_held:
            assert not (action is not None and action.stage is Stage.UP)
        if stored is not None and within(stored.last_action_at, interval):
            assert not (action is not None and action.stage is Stage.UP)
        if window:
            assert alloc <= (last_sent or 0.0) + 1e-9
        # the minimum pause
        if (
            stored is not None
            and stored.stopped_for_capacity
            and not stored.running
            and within(stored.switched_at, cfg.min_pause)
            and decision.priority in (2, 3, 4)
        ):
            assert alloc == 0.0

    # limits of each charger
    for c in snapshot.chargers:
        if reserved(c):
            continue
        alloc = decisions[c.id].allocation
        session = sessions.get(c.id)
        limit = (
            float(session.user_current_limit)
            if session and session.user_current_limit is not None
            else None
        )
        cap = float(
            c.fallback_current
            if c.fallback_current is not None
            else cfg.fallback_current
        )
        if c.kind is ChargerKind.ADJUSTABLE:
            assert c.min_current is not None
            assert c.max_current is not None
            assert alloc == 0.0 or c.min_current <= alloc <= c.max_current
            assert alloc == int(alloc)
            if limit is not None:
                assert alloc <= limit
            if not fresh:
                assert alloc <= cap
            stored = (
                next(e for e in result.memory.chargers if e.charger_id == c.id)
                if result.memory
                else None
            )
            if stored is not None and stored.regulated_cap is not None:
                assert alloc <= stored.regulated_cap
        else:
            assumed = c.assumed_current or 0.0
            assert alloc == 0.0 or alloc == pytest.approx(assumed)
            if alloc > 0:
                assert limit is None or assumed <= limit
                assert fresh or assumed <= cap

    # budget and phase guard
    for phase in Phase:
        on_phase = [c for c in snapshot.chargers if phase in c.phases]
        held = sum(decisions[c.id].allocation for c in on_phase if reserved(c))
        given = sum(decisions[c.id].allocation for c in on_phase if not reserved(c))
        assert given <= max(0.0, cfg.phase_budget - held) + TOLERANCE
        reading = pc.get(phase) if pc is not None else None
        if fresh and reading is not None:
            draw = 0.0
            for c in on_phase:
                state = states[c.id]
                drawn = per_phase(c)
                stored = entry_of(c)
                settling = stored is not None and within(stored.decreased_at, interval)
                if reserved(c):
                    draw += drawn if drawn is not None else decisions[c.id].allocation
                elif settling and state.car_connected and drawn is not None:
                    draw += drawn
                elif state.paused or not state.car_connected:
                    draw += 0.0
                else:
                    nominal = present_of(c)
                    draw += nominal if drawn is None else min(nominal, drawn)
            bound = max(
                0.0,
                min(cfg.phase_budget, cfg.phase_threshold - (reading - draw)) - held,
            )
            assert given <= bound + TOLERANCE
        assert 0.0 <= result.available.get(phase) <= cfg.phase_budget + TOLERANCE

    served = {
        decisions[c.id].priority
        for c in snapshot.chargers
        if not reserved(c)
        and decisions[c.id].allocation > 0
        and decisions[c.id].priority is not None
    }
    assert len(served) <= 1
    assert served <= {1, 2, 3}

    # the memory and the next check
    assert result.memory is not None
    assert result.memory.saved_at == snapshot.now
    assert [e.charger_id for e in result.memory.chargers] == [
        c.id for c in snapshot.chargers
    ]
    assert (
        memory_from_dict(json.loads(json.dumps(memory_to_dict(result.memory))))
        == result.memory
    )
    if result.next_check is not None:
        assert utc(result.next_check) > now
        assert utc(result.next_check) <= now + cfg.max_memory_age
        assert (
            result.next_check.utcoffset()
            == utc(result.next_check).astimezone(ZONE).utcoffset()
        )


@settings(deadline=None, max_examples=150, derandomize=True)
@given(st.randoms(use_true_random=False))
def test_the_promises_hold_across_sequences_of_calls_feeding_memory_forward(
    rng: random.Random,
) -> None:
    snapshot = random_snapshot(rng)
    snapshot = replace(
        snapshot,
        settings=sequence_settings(rng, snapshot.settings),
        memory=None if rng.random() < 0.5 else warm(snapshot.now),
    )
    draws: dict[str, float] = {}
    for _ in range(rng.randint(2, 6)):
        result = decide(snapshot)

        assert_sequence_invariants(snapshot, result)
        assert decide(snapshot) == result
        snapshot = next_snapshot(rng, snapshot, result, draws)


def friendly_snapshot(rng: random.Random) -> Snapshot:
    """A plain installation where every timer can fire: few cars, room to move."""
    now = rng.choice(BASES) + timedelta(minutes=rng.randint(0, 59))
    base = Settings(
        rng.choice([16.0, 25.0, 32.0]),
        40.0,
        rng.choice([6, 8]),
        TZ,
        default_energy_need_kwh=rng.uniform(5, 40),
    )
    chargers: list[Charger] = []
    for index in range(rng.randint(1, 3)):
        phases = frozenset(rng.sample(list(Phase), rng.choice([1, 1, 2])))
        low = rng.choice([6, 6, 8])
        chargers.append(Charger(f"c{index}", phases, ChargerKind.ADJUSTABLE, low, 16))
    states = [
        ChargerState(
            c.id,
            True,
            rng.choice([0.0, 6.0, 10.0, 16.0]),
            rng.random() < 0.4,
            None,
        )
        for c in chargers
    ]
    sessions = [
        Session(
            c.id,
            time(rng.randint(5, 9), 0),
            energy_need_kwh=rng.uniform(2, 50),
            charge_now=rng.random() < 0.5,
            smart_charging=rng.random() < 0.9,
        )
        for c in chargers
    ]
    values = [rng.uniform(0.1, 3.0)] * 8 + [rng.uniform(0.1, 3.0)]
    hour = now.replace(minute=0, second=0, microsecond=0)
    prices = hourly(
        [0.2 + 0.1 * (i % 5) * rng.choice(values) for i in range(60)], hour, now
    )
    currents = PhaseCurrents(now, *(rng.uniform(0, 30) for _ in Phase))
    settings_now = sequence_settings(rng, base)
    return Snapshot(
        now,
        settings_now,
        tuple(chargers),
        tuple(states),
        tuple(sessions),
        prices,
        currents,
        warm(now),
    )


@settings(deadline=None, max_examples=120, derandomize=True)
@given(st.randoms(use_true_random=False))
def test_the_promises_hold_across_plain_sequences_where_every_timer_can_fire(
    rng: random.Random,
) -> None:
    snapshot = friendly_snapshot(rng)
    draws: dict[str, float] = {}
    for _ in range(rng.randint(3, 8)):
        result = decide(snapshot)

        assert_sequence_invariants(snapshot, result)
        assert decide(snapshot) == result
        snapshot = next_snapshot(rng, snapshot, result, draws)


# --- T15: speed with a full memory --------------------------------------------------------------------------


def test_ten_chargers_and_a_48_hour_series_decide_in_under_100_ms_with_a_full_memory() -> (
    None
):
    first = large_snapshot()
    d0 = decide(first)
    assert d0.memory is not None
    stamp = NOW - 2 * MIN
    full = tuple(
        replace(
            e,
            running=True,
            selected=True,
            stopped_for_capacity=False,
            regulated_cap=10,
            under_use_since=stamp,
            full_use_since=stamp,
            switched_at=stamp,
            last_target=10.0,
            last_action_at=stamp,
            decreased_at=stamp,
        )
        for e in d0.memory.chargers
    )
    memory = Memory(
        saved_at=NOW,
        chargers=full,
        level=GuardLevel.GREEN,
        green_since=NOW - timedelta(hours=1),
    )
    later = NOW + MIN
    snapshot = replace(
        first,
        now=later,
        phase_currents=PhaseCurrents(later, 0.0, 0.0, 0.0),
        memory=memory,
    )
    durations = []
    for _ in range(5):
        started = clock.perf_counter()
        result = decide(snapshot)
        durations.append(clock.perf_counter() - started)

    assert len(result.chargers) == 10
    assert reason_of(result) is None
    assert result.memory is not None
    assert any(e.regulated_cap is not None for e in result.memory.chargers)
    assert min(durations) < 0.1


def test_main_showcase_runs_and_prints(capsys: pytest.CaptureFixture[str]) -> None:
    showcase()

    assert "without measurements" in capsys.readouterr().out
