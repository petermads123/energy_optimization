"""Tests for energy_optimization.interruptible.result: the output types."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from energy_optimization.interruptible.model import LoadState
from energy_optimization.interruptible.result import (
    LoadSchedule,
    Reason,
    ReasonCode,
    ScheduledQuarter,
)
from energy_optimization.interruptible.result import main as showcase

START = datetime(2026, 10, 21, 17, 0, tzinfo=UTC)


def test_reason_code_members_and_lower_case_values() -> None:
    assert {code.name: code.value for code in ReasonCode} == {
        "NO_PRICES": "no_prices",
        "FORCED_ON": "forced_on",
        "MAX_OFF_RUN": "max_off_run",
        "WINDOW_BUDGET": "window_budget",
        "MIN_ON_AFTER_OFF": "min_on_after_off",
        "END_OF_HORIZON": "end_of_horizon",
        "MIN_OFF_RUN": "min_off_run",
        "PLANNED_OFF": "planned_off",
        "PLANNED_ON": "planned_on",
        "NO_SAVING": "no_saving",
    }


def test_load_schedule_defaults() -> None:
    schedule = LoadSchedule(LoadState.ON, Reason(ReasonCode.NO_PRICES, ""), (), None)

    assert schedule.plan == ()
    assert schedule.saving_percent is None
    assert schedule.always_on_cost is None
    assert schedule.planned_cost is None
    assert schedule.saving is None
    assert schedule.history_short is False
    assert schedule.proven is True


def test_load_schedule_turns_a_list_plan_into_a_tuple() -> None:
    quarter = ScheduledQuarter(START, 1.0, LoadState.OFF)
    plan = [quarter]

    schedule = LoadSchedule(
        LoadState.OFF,
        Reason(ReasonCode.PLANNED_OFF, "x"),
        plan,  # type: ignore[arg-type]  # normalised to a tuple
        1.0,
    )

    assert schedule.plan == (quarter,)
    assert isinstance(schedule.plan, tuple)
    assert plan == [quarter]


def test_output_types_are_frozen() -> None:
    quarter = ScheduledQuarter(START, 1.0, LoadState.ON)
    reason = Reason(ReasonCode.NO_SAVING, "t")
    schedule = LoadSchedule(LoadState.ON, reason, (quarter,), 0.0)

    with pytest.raises(FrozenInstanceError):
        quarter.price = 2.0  # type: ignore[misc]  # proving frozenness
    with pytest.raises(FrozenInstanceError):
        reason.text = "u"  # type: ignore[misc]  # proving frozenness
    with pytest.raises(FrozenInstanceError):
        schedule.proven = False  # type: ignore[misc]  # proving frozenness


def test_output_types_compare_by_value() -> None:
    assert ScheduledQuarter(START, 1.0, LoadState.ON) == ScheduledQuarter(
        START, 1.0, LoadState.ON
    )
    assert Reason(ReasonCode.NO_SAVING, "a") != Reason(ReasonCode.NO_SAVING, "b")


def test_showcase_runs(capsys: pytest.CaptureFixture[str]) -> None:
    showcase()

    out = capsys.readouterr().out
    assert "plan: ['off', 'on']" in out
    assert "proven=True" in out
