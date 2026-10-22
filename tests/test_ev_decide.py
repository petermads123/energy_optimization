"""Tests for energy_optimization.ev.decide (T3, T5-T7, T9, T13-T18, T20): the core."""

import copy
import dataclasses
import importlib.util
import inspect
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
    ChargerState,
    Decision,
    EventKind,
    Phase,
    PhaseAmps,
    PhaseCurrents,
    PriceInterval,
    PriceSeries,
    ReasonCode,
    Session,
    Settings,
    Snapshot,
    Stage,
    Status,
    decide,
    decision_from_dict,
    decision_to_dict,
    snapshot_from_dict,
    snapshot_to_dict,
)
from energy_optimization.ev.allocation import phase_capacity
from energy_optimization.ev.decide import main as showcase
from energy_optimization.ev.planning import (
    PlanRequest,
    energy_per_quarter_kwh,
    plan_sessions,
)

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

    assert result == Decision((), (), PhaseAmps(22.0, 25.0, 25.0), Status.NORMAL, ())


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


def assert_plans_fit(snapshot: Snapshot, result: Decision) -> None:
    """No plan books more than a phase can hold, worked out from the decision alone.

    What the fixed, unavailable and smart-charging-off chargers take is out of the room
    the plans share; their final allocation is never above what the planner was told.
    """
    cfg = snapshot.settings
    states = {s.charger_id: s for s in snapshot.states}
    sessions = {s.charger_id: s for s in snapshot.sessions}
    decisions = {c.charger_id: c for c in result.chargers}
    unplanned = {
        c.id
        for c in snapshot.chargers
        if c.kind is ChargerKind.FIXED
        or not states[c.id].available
        or (c.id in sessions and not sessions[c.id].smart_charging)
    }
    booked: dict[tuple[datetime, Phase], float] = {}
    for c in snapshot.chargers:
        planned = decisions[c.id].plan
        if c.id in unplanned:
            assert planned == ()
        for quarter in planned:
            for phase in c.phases:
                # keyed in UTC: the wall times of the fall-back hour repeat
                key = (quarter.start.astimezone(UTC), phase)
                booked[key] = booked.get(key, 0.0) + quarter.current
    for (_, phase), total in booked.items():
        taken = sum(
            decisions[c.id].allocation
            for c in snapshot.chargers
            if phase in c.phases and c.id in unplanned
        )
        assert total <= max(0.0, cfg.phase_budget - taken) + TOLERANCE


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

    assert_plans_fit(snapshot, result)
    for decision in result.chargers:
        assert (decision.reaches_target is None) == (decision.deadline is None)
        if decision.plan and decision.expected_finish is not None:
            # compared as instants: the wall times of the fall-back hour repeat
            first = decision.plan[0].start.astimezone(UTC)
            assert decision.expected_finish.astimezone(UTC) > first

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


# --- T3: the expected finish of an unplanned session is worked out in UTC ---------


def unplanned_snapshot(now: datetime, hours: float, **kwargs: object) -> Snapshot:
    """One 16 A charger, stale prices (so no plan) and a need that takes `hours`."""
    need = 3.68 * hours  # 230 V * 16 A = 3.68 kW
    return snap(
        [adj()],
        sessions=[sess(need=need)],
        prices=PriceSeries((), now - timedelta(hours=2)),
        now=now,
        **kwargs,  # type: ignore[arg-type]  # test passes through
    )


def finish_of(snapshot: Snapshot) -> ChargerDecision:
    """Decide, and check the setup really is allocated, unplanned and unpriced."""
    item = by_id(decide(snapshot))["a"]
    assert item.allocation == 16.0
    assert item.plan == ()
    return item


@pytest.mark.parametrize(
    ("now", "hours", "expected"),
    [
        # the old wall-clock sum gives 03:30+01:00 (an hour late)
        (datetime(2026, 10, 25, 1, 30, tzinfo=ZONE), 2, "2026-10-25T02:30:00+01:00"),
        # 02:30 first pass, fold=0; the old sum gives 02:30+01:00 (an hour late)
        (datetime(2026, 10, 25, 2, 30, tzinfo=ZONE), 1, "2026-10-25T02:30:00+01:00"),
        # second pass, fold=1: wall-clock and UTC agree, so this only proves `fold`
        (
            datetime(2026, 10, 25, 2, 30, fold=1, tzinfo=ZONE),
            1,
            "2026-10-25T03:30:00+01:00",
        ),
        # spring forward: the old sum gives 03:30+02:00 (an hour early)
        (datetime(2026, 3, 29, 1, 30, tzinfo=ZONE), 2, "2026-03-29T04:30:00+02:00"),
        # inside the gap, read with the pre-transition offset (01:30Z)
        (datetime(2026, 3, 29, 2, 30, tzinfo=ZONE), 2, "2026-03-29T05:30:00+02:00"),
    ],
)
def test_an_unplanned_expected_finish_adds_the_hours_in_utc_across_dst(
    now: datetime, hours: int, expected: str
) -> None:
    item = finish_of(unplanned_snapshot(now, hours))

    # `isoformat`, never `==`: equal ZoneInfo datetimes compare by wall clock, `fold`
    # ignored, so a result an hour off on the fall-back night would still be equal.
    assert item.expected_finish is not None
    assert item.expected_finish.isoformat() == expected
    assert item.expected_finish.tzinfo == ZONE


def test_the_unplanned_finish_off_a_wall_clock_now_matches_its_utc_instant() -> None:
    now = datetime(2026, 10, 25, 2, 30, tzinfo=ZONE)  # fold=0, 00:30Z
    assert now.astimezone(UTC) == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)

    item = finish_of(unplanned_snapshot(now, 1))

    assert item.expected_finish is not None
    assert item.expected_finish.astimezone(UTC) == datetime(
        2026, 10, 25, 1, 30, tzinfo=UTC
    )
    assert item.expected_finish.utcoffset() == timedelta(hours=1)


@pytest.mark.parametrize(
    "now",
    [
        datetime(2026, 10, 24, 23, 30, tzinfo=UTC),
        datetime(2026, 10, 25, 1, 30, tzinfo=ZONE),
        datetime(2026, 10, 25, 4, 30, tzinfo=timezone(timedelta(hours=5))),
    ],
)
def test_the_unplanned_finish_is_the_same_instant_whatever_zone_now_is_in(
    now: datetime,
) -> None:
    item = finish_of(unplanned_snapshot(now, 2))

    assert item.expected_finish is not None
    assert item.expected_finish.isoformat() == "2026-10-25T02:30:00+01:00"
    assert item.expected_finish.tzinfo == ZONE


@pytest.mark.parametrize(
    ("ready", "reaches", "deadline"),
    [
        # the first 02:30 (00:30Z) is before the 01:30Z finish
        (time(2, 30), False, "2026-10-25T02:30:00+02:00"),
        # 03:00 is 02:00Z, after the 01:30Z finish (the old sum finishes at 02:30Z)
        (time(3, 0), True, "2026-10-25T03:00:00+01:00"),
    ],
)
def test_reaches_target_compares_instants_across_the_fold(
    ready: time, reaches: bool, deadline: str
) -> None:
    now = datetime(2026, 10, 25, 1, 30, tzinfo=ZONE)  # 23:30Z
    snapshot = snap(
        [adj()],
        sessions=[sess(ready=ready, need=7.36)],
        prices=PriceSeries((), now - timedelta(hours=2)),
        now=now,
    )

    item = finish_of(snapshot)

    assert item.deadline is not None
    assert item.deadline.isoformat() == deadline
    assert item.expected_finish is not None
    assert item.expected_finish.isoformat() == "2026-10-25T02:30:00+01:00"
    assert item.reaches_target is reaches


@pytest.mark.parametrize(
    ("cheap_start", "plan_starts", "finish_utc", "finish_local"),
    [
        # D1: the plan ends with the quarter at 01:00Z; the finish is 01:15Z
        (
            datetime(2026, 10, 25, 0, 45, tzinfo=UTC),
            ["2026-10-25T00:45:00+00:00", "2026-10-25T01:00:00+00:00"],
            "2026-10-25T01:15:00+00:00",
            "2026-10-25T02:15:00+01:00",
        ),
        # D2: the plan ends with the quarter at 00:45Z; the finish is 01:00Z
        (
            datetime(2026, 10, 25, 0, 30, tzinfo=UTC),
            ["2026-10-25T00:30:00+00:00", "2026-10-25T00:45:00+00:00"],
            "2026-10-25T01:00:00+00:00",
            "2026-10-25T02:00:00+01:00",
        ),
    ],
    ids=["d1-ends-in-the-repeated-hour", "d2-ends-at-the-repeated-hour"],
)
def test_a_planned_finish_adds_its_quarter_in_utc_on_the_fall_back_night(
    cheap_start: datetime,
    plan_starts: list[str],
    finish_utc: str,
    finish_local: str,
) -> None:
    now = datetime(2026, 10, 24, 22, 0, tzinfo=UTC)
    intervals = tuple(
        PriceInterval(
            now + QUARTER * i,
            now + QUARTER * (i + 1),
            0.01
            if (now + QUARTER * i) in (cheap_start, cheap_start + QUARTER)
            else 0.30,
        )
        for i in range(32)
    )
    snapshot = snap(
        [adj()],
        sessions=[sess(need=1.84)],
        prices=PriceSeries(intervals, now),
        now=now,
    )

    item = decide(snapshot).chargers[0]

    assert [q.start.astimezone(UTC).isoformat() for q in item.plan] == plan_starts
    assert item.expected_finish is not None
    assert item.expected_finish.astimezone(UTC).isoformat() == finish_utc
    assert item.expected_finish.isoformat() == finish_local
    assert item.expected_finish.astimezone(UTC) > item.plan[0].start.astimezone(UTC)
    assert item.reaches_target is True


TRICKLE_NOW = datetime(2026, 10, 22, 1, 30, tzinfo=ZONE)


def trickle_snapshot(row: str) -> Snapshot:
    """A snapshot whose allocation is so small no calendar holds the finish."""
    stale = PriceSeries((), TRICKLE_NOW - timedelta(hours=2))
    if row == "huge-need":
        return snap(
            [adj()],
            sessions=[sess(need=1e9)],
            prices=stale,
            now=TRICKLE_NOW,
        )
    if row == "need-beyond-timedelta":
        return snap(
            [adj()],
            sessions=[sess(need=1e14)],
            prices=stale,
            now=TRICKLE_NOW,
        )
    if row == "fixed-1e-300":
        return snap(
            [fixed("a", level=1e-300)],
            [idle("a", setting=0.0, paused=False)],
            [sess(need=30.0)],
            stale,
            now=TRICKLE_NOW,
        )
    if row == "fixed-subnormal":
        return snap(
            [fixed("a", level=5e-324)],
            [idle("a", setting=0.0, paused=False)],
            [sess(need=30.0)],
            stale,
            now=TRICKLE_NOW,
        )
    assert row == "unavailable"
    return snap(
        [adj()],
        [idle("a", setting=1e-300, paused=False, available=False)],
        [sess(need=30.0)],
        stale,
        now=TRICKLE_NOW,
    )


TRICKLE_ROWS = [
    "huge-need",
    "need-beyond-timedelta",
    "fixed-1e-300",
    "fixed-subnormal",
    "unavailable",
]


@pytest.mark.parametrize("row", TRICKLE_ROWS)
def test_a_trickle_of_current_gives_no_finish_instead_of_overflowing(row: str) -> None:
    item = by_id(decide(trickle_snapshot(row)))["a"]

    assert item.allocation > 0
    assert item.plan == ()
    assert item.expected_finish is None
    assert item.reaches_target is False


@pytest.mark.parametrize("measured", [1e-6, 1e-9])
def test_a_reserved_charger_with_a_tiny_measured_draw_gives_no_finish(
    measured: float,
) -> None:
    state = ChargerState(
        "a", True, 0.0, False, measured_power_w=measured, available=False
    )
    snapshot = snap(
        [adj()],
        [state],
        [sess(need=10.0)],
        PriceSeries((), TRICKLE_NOW - timedelta(hours=2)),
        now=TRICKLE_NOW,
    )

    item = by_id(decide(snapshot))["a"]

    assert 0 < item.allocation < 1e-3
    assert item.expected_finish is None
    assert item.reaches_target is False


@pytest.mark.parametrize("row", ["fixed-1e-300", "unavailable"])
def test_a_trickle_decision_survives_the_codec_round_trip(row: str) -> None:
    decision = decide(trickle_snapshot(row))

    data = json.loads(json.dumps(decision_to_dict(decision), allow_nan=False))

    assert data["chargers"][0]["expected_finish"] is None
    assert decision_from_dict(data) == decision


# --- A1: no memory between calls -------------------------------------------------


def test_a_decision_does_not_depend_on_the_snapshots_decided_before_it() -> None:
    first_snapshot = snap(
        [adj()], [idle(paused=True)], [sess(need=5.0)], hourly([0.1] * 12)
    )
    other = snap(
        [adj()],
        [idle(setting=16.0, paused=False)],
        [now_session(need=5.0)],
        PriceSeries((), NOW - timedelta(hours=2)),
    )

    first = decide(first_snapshot)
    decide(other)
    decide(other)
    again = decide(first_snapshot)

    assert again == first
    assert decision_to_dict(again) == decision_to_dict(first)


# --- T1: the public surface is round 1's ----------------------------------------

ROUND_1_ALL = {
    "Action",
    "ActionKind",
    "Charger",
    "ChargerDecision",
    "ChargerKind",
    "ChargerState",
    "ConfigError",
    "Decision",
    "Event",
    "EventKind",
    "Phase",
    "PhaseAmps",
    "PhaseCurrents",
    "PlannedQuarter",
    "PriceInterval",
    "PriceSeries",
    "Reason",
    "ReasonCode",
    "Session",
    "Settings",
    "Snapshot",
    "Stage",
    "Status",
    "decide",
    "decision_from_dict",
    "decision_to_dict",
    "snapshot_from_dict",
    "snapshot_to_dict",
}


def test_the_package_exports_exactly_round_ones_names() -> None:
    import energy_optimization.ev as ev

    assert set(ev.__all__) == ROUND_1_ALL
    assert len(ev.__all__) == len(ROUND_1_ALL)


@pytest.mark.parametrize("module", ["guard", "regulation"])
def test_the_round_two_modules_are_gone(module: str) -> None:
    import energy_optimization.ev as ev

    assert importlib.util.find_spec(f"energy_optimization.ev.{module}") is None
    assert not hasattr(ev, module)


@pytest.mark.parametrize(
    ("module", "name"),
    [
        ("model", "Memory"),
        ("model", "ChargerMemory"),
        ("model", "GuardLevel"),
        ("model", "Reduction"),
        ("codec", "memory_to_dict"),
        ("codec", "memory_from_dict"),
        ("result", "GuardLevel"),
        ("allocation", "settling"),
    ],
)
def test_the_round_two_names_are_gone(module: str, name: str) -> None:
    loaded = importlib.import_module(f"energy_optimization.ev.{module}")

    assert not hasattr(loaded, name)


@pytest.mark.parametrize(
    ("cls", "fields"),
    [
        (
            Snapshot,
            (
                "now",
                "settings",
                "chargers",
                "states",
                "sessions",
                "prices",
                "phase_currents",
            ),
        ),
        (
            Settings,
            (
                "phase_budget",
                "phase_threshold",
                "fallback_current",
                "timezone",
                "voltage",
                "min_block_quarters",
                "price_cap",
                "default_energy_need_kwh",
                "max_price_age",
                "max_measurement_age",
            ),
        ),
        (Decision, ("actions", "chargers", "available", "status", "events")),
        (
            PlanRequest,
            (
                "charger_id",
                "phases",
                "min_current",
                "max_current",
                "need_kwh",
                "deadline",
            ),
        ),
    ],
)
def test_the_dataclass_fields_are_round_ones(
    cls: type, fields: tuple[str, ...]
) -> None:
    assert tuple(f.name for f in dataclasses.fields(cls)) == fields


@pytest.mark.parametrize(
    ("function", "names"),
    [
        (
            plan_sessions,
            [
                "requests",
                "now",
                "prices",
                "capacity",
                "voltage",
                "min_block_quarters",
                "price_cap",
                "timezone",
            ],
        ),
        (phase_capacity, ["snapshot", "fresh"]),
    ],
)
def test_the_planner_and_capacity_signatures_are_round_ones(
    function: object, names: list[str]
) -> None:
    parameters = inspect.signature(function).parameters  # type: ignore[arg-type]  # parametrized callables

    assert list(parameters) == names
    assert all(p.default is inspect.Parameter.empty for p in parameters.values())


@pytest.mark.parametrize("enum", [ReasonCode, EventKind, Status])
def test_the_enums_carry_no_round_two_members(enum: type[Enum]) -> None:
    names = {member.name for member in enum}

    assert not names & {
        "MINIMUM_PAUSE",
        "UNDER_USE",
        "EMERGENCY",
        "MEMORY_RESET",
        "PHASE_GUARD_INTERVENED",
    }


def test_main_showcase_runs_and_prints(capsys: pytest.CaptureFixture[str]) -> None:
    showcase()

    assert "without measurements" in capsys.readouterr().out
