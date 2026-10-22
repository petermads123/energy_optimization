"""Planning vocabulary: how much energy a session needs and what a plan looks like.

Pure functions and types over plain values. The plan itself is made by the optimiser
in `optimize`; this module holds the remaining need, the energy of a quarter at a
current, and the `SessionPlan` that the optimiser returns for each planned session.
"""

from dataclasses import dataclass
from datetime import time
from enum import StrEnum

from .model import Session
from .result import PlannedQuarter


class NeedSource(StrEnum):
    """Where a remaining need came from."""

    ENERGY = "energy"
    SOC = "soc"
    DEFAULT = "default"


def remaining_need_kwh(
    session: Session, default_need_kwh: float
) -> tuple[float, NeedSource]:
    """Work out how much energy a session still needs.

    Args:
        session: The session.
        default_need_kwh: The need to assume when the session gives no need and no
            complete SOC information.

    Returns:
        The remaining need in kWh, clipped at 0, and where it came from. The need is
        `energy_need_kwh - delivered` when given, else `(target - soc) / 100 *
        capacity` when all three SOC fields are given, else `default - delivered`.
    """
    if session.energy_need_kwh is not None:
        return (
            max(0.0, session.energy_need_kwh - session.delivered_kwh),
            NeedSource.ENERGY,
        )
    if (
        session.soc_percent is not None
        and session.target_soc_percent is not None
        and session.battery_capacity_kwh is not None
    ):
        gap = session.target_soc_percent - session.soc_percent
        return max(0.0, gap / 100 * session.battery_capacity_kwh), NeedSource.SOC
    return (
        max(0.0, default_need_kwh - session.delivered_kwh),
        NeedSource.DEFAULT,
    )


def energy_per_quarter_kwh(current: float, phase_count: int, voltage: float) -> float:
    """Return the energy delivered in one quarter at a current.

    Args:
        current: Current per phase in A.
        phase_count: Number of phases it flows on.
        voltage: Phase voltage in V.

    Returns:
        `voltage * current * phase_count / 4 / 1000` in kWh.
    """
    return voltage * current * phase_count / 4 / 1000


@dataclass(frozen=True)
class SessionPlan:
    """One session's plan.

    Attributes:
        charger_id: The charger.
        quarters: The planned quarters in time order.
        planned_kwh: The energy the plan delivers.
        reachable: Whether the plan covers the need.
        price_cap_exceeded: Whether a planned quarter costs more than the cap.
        uses_unpriced: Whether a planned quarter has no price.
    """

    charger_id: str
    quarters: tuple[PlannedQuarter, ...]
    planned_kwh: float
    reachable: bool
    price_cap_exceeded: bool
    uses_unpriced: bool


def main() -> None:
    """Showcase this module's functionality."""
    session = Session(
        "a", ready_time=time(6, 0), energy_need_kwh=20.0, delivered_kwh=5.0
    )
    default_need_kwh = 30.0

    need, source = remaining_need_kwh(session, default_need_kwh)

    print(f"need {need} kWh from {source}")

    # A session with no SOC information falls back to the default need.
    session = Session("a", ready_time=time(6, 0))

    need, source = remaining_need_kwh(session, default_need_kwh)

    print(f"need {need} kWh from {source}")

    current = 16.0
    phase_count = 3
    voltage = 230.0

    energy = energy_per_quarter_kwh(current, phase_count, voltage)

    print(f"{current} A on {phase_count} phases delivers {energy:.2f} kWh per quarter")


if __name__ == "__main__":
    main()
