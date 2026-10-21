"""Tests for energy_optimization.ev.result: the output types."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from energy_optimization.ev.model import Phase
from energy_optimization.ev.result import (
    Action,
    ActionKind,
    ChargerDecision,
    Decision,
    Event,
    EventKind,
    PhaseAmps,
    PlannedQuarter,
    Reason,
    ReasonCode,
    Stage,
    Status,
)

NOW = datetime(2026, 10, 21, 20, 0, tzinfo=UTC)


def reason() -> Reason:
    return Reason(ReasonCode.PLAN_COMPLETED, "Plan completed")


@pytest.mark.parametrize(
    "enum", [Stage, ActionKind, ReasonCode, Status, EventKind], ids=lambda e: e.__name__
)
def test_enum_values_are_the_lower_case_member_names(enum: type) -> None:
    assert all(member.value == member.name.lower() for member in enum)  # type: ignore[attr-defined]  # StrEnum


def test_reason_codes_are_exactly_the_planned_set() -> None:
    assert {code.name for code in ReasonCode} == {
        "CHARGE_NOW",
        "IN_PLAN",
        "READY_TIME_PASSED",
        "PLAN_COMPLETED",
        "PRICES_STALE",
        "PLANNED_LATER",
        "WAITING_FOR_HIGHER_PRIORITY",
        "WAITING_FOR_CAPACITY",
        "TARGET_REACHED",
        "BELOW_MINIMUM",
        "SMART_CHARGING_OFF",
        "FIXED",
        "UNAVAILABLE",
        "NO_CAR",
        "NO_SESSION",
    }


def test_status_and_event_kinds_are_exactly_the_planned_set() -> None:
    assert {s.name for s in Status} == {
        "NORMAL",
        "LIMITED_BY_BUDGET",
        "LIMITED_BY_PHASE",
        "WAITING_FOR_CAPACITY",
        "PHASE_DATA_STALE",
        "PRICES_STALE",
    }
    assert {e.name for e in EventKind} == {
        "TARGET_UNREACHABLE",
        "PRICE_CAP_EXCEEDED",
        "CHARGE_NOW_NEED_COVERED",
        "PHASE_DATA_STALE",
        "PRICES_STALE",
    }


@pytest.mark.parametrize(
    ("phase", "expected"), [(Phase.L1, 1.0), (Phase.L2, 2.0), (Phase.L3, 3.0)]
)
def test_phase_amps_get_returns_the_matching_phase(
    phase: Phase, expected: float
) -> None:
    assert PhaseAmps(1.0, 2.0, 3.0).get(phase) == expected


def test_reason_params_default_to_a_fresh_dict_per_instance() -> None:
    first, second = reason(), reason()

    first.params["x"] = 1

    assert second.params == {}


def test_event_defaults_are_a_global_event_without_params() -> None:
    event = Event(EventKind.PRICES_STALE, "stale")

    assert event.charger_id is None
    assert event.params == {}


def test_charger_decision_normalises_a_plan_list_to_a_tuple() -> None:
    quarter = PlannedQuarter(NOW, None, 10.0)

    decision = ChargerDecision("a", 10.0, 3, reason(), plan=[quarter])  # type: ignore[arg-type]  # a list is accepted

    assert decision.plan == (quarter,)
    assert isinstance(decision.plan, tuple)


def test_charger_decision_defaults_describe_a_charger_without_a_session() -> None:
    decision = ChargerDecision("a", 0.0, None, reason())

    assert decision.plan == ()
    assert decision.remaining_need_kwh is None
    assert decision.deadline is None
    assert decision.expected_finish is None
    assert decision.reaches_target is None


def test_decision_normalises_sequences_to_tuples_and_keeps_order() -> None:
    first = Action("a", ActionKind.PAUSE, 0.0, Stage.DOWN)
    second = Action("b", ActionKind.RESUME, 10.0, Stage.UP)

    decision = Decision(
        actions=[first, second],  # type: ignore[arg-type]  # lists are accepted
        chargers=[ChargerDecision("a", 0.0, None, reason())],  # type: ignore[arg-type]  # lists are accepted
        available=PhaseAmps(1.0, 2.0, 3.0),
        status=Status.NORMAL,
        events=[],  # type: ignore[arg-type]  # lists are accepted
    )

    assert decision.actions == (first, second)
    assert isinstance(decision.chargers, tuple)
    assert decision.events == ()


def test_result_types_are_frozen() -> None:
    action = Action("a", ActionKind.PAUSE, 0.0, Stage.DOWN)

    with pytest.raises(FrozenInstanceError):
        action.target_current = 5.0  # type: ignore[misc]  # proving the dataclass is frozen


def test_equal_decisions_compare_equal() -> None:
    def build() -> Decision:
        return Decision((), (), PhaseAmps(1.0, 2.0, 3.0), Status.NORMAL, ())

    assert build() == build()
