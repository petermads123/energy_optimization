"""Tests for energy_optimization.ev.planning: remaining need and energy per quarter."""

from datetime import time

import pytest

from energy_optimization.ev.model import Session
from energy_optimization.ev.planning import (
    NeedSource,
    energy_per_quarter_kwh,
    remaining_need_kwh,
)
from energy_optimization.ev.planning import main as showcase

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


def test_main_showcase_runs_and_prints(capsys: pytest.CaptureFixture[str]) -> None:
    showcase()

    assert "need " in capsys.readouterr().out
