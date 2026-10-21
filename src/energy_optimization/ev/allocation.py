"""Phase capacity and current sharing.

Pure helpers: what a phase may carry (budget and phase guard), what chargers draw or
are reserved at, whether phase measurements can be trusted, and the max-min-fair
0-or-minimum sharing of that capacity among claims given in ranking order.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .model import (
    Charger,
    ChargerKind,
    ChargerState,
    Phase,
    PhaseCurrents,
    Settings,
    Snapshot,
)

_EPS = 1e-9


@dataclass(frozen=True)
class Claim:
    """One charger asking for current.

    Attributes:
        charger_id: The charger.
        phases: The phases it is counted on.
        min_current: Lowest non-zero current in A.
        max_current: Highest current in A; equal to `min_current` means
            all-or-nothing.
    """

    charger_id: str
    phases: frozenset[Phase]
    min_current: float
    max_current: float


def share(claims: Sequence[Claim], capacity: Mapping[Phase, float]) -> dict[str, float]:
    """Share capacity among claims given in ranking order.

    First each claim in order is included at its minimum if it fits on all its
    phases, then included adjustable claims rise 1 A at a time, always the one with
    the lowest allocation (ties in ranking order), until none can grow.

    Args:
        claims: The claims, highest ranking first.
        capacity: What may be used per phase; a phase absent from it has none.

    Returns:
        The current of every claim by charger id; claims that did not fit get 0.
    """
    remaining = {phase: capacity.get(phase, 0.0) for phase in Phase}
    allocation: dict[str, float] = {claim.charger_id: 0.0 for claim in claims}
    included: list[Claim] = []
    for claim in claims:
        if claim.max_current < claim.min_current:
            continue
        if all(claim.min_current <= remaining[p] + _EPS for p in claim.phases):
            allocation[claim.charger_id] = claim.min_current
            for phase in claim.phases:
                remaining[phase] -= claim.min_current
            included.append(claim)
    growable = [c for c in included if c.min_current < c.max_current]
    while True:
        candidates = [
            c
            for c in growable
            if allocation[c.charger_id] < c.max_current - _EPS
            and all(remaining[p] >= 1.0 - _EPS for p in c.phases)
        ]
        if not candidates:
            break
        lowest = min(candidates, key=lambda c: allocation[c.charger_id])
        step = min(1.0, lowest.max_current - allocation[lowest.charger_id])
        allocation[lowest.charger_id] += step
        for phase in lowest.phases:
            remaining[phase] -= step
    return allocation


def measurements_fresh(
    currents: PhaseCurrents | None,
    now: datetime,
    max_age: timedelta,
    phases: frozenset[Phase],
) -> bool:
    """Say whether phase measurements can be trusted for the given phases.

    Args:
        currents: The measurement, None when missing.
        now: The time now, aware.
        max_age: How old the measurement may be.
        phases: The phases that must have a value.

    Returns:
        True when there is a measurement no further than `max_age` from `now` in
        either direction, with a value for every phase in `phases`.
    """
    if currents is None:
        return False
    age = abs(now.astimezone(UTC) - currents.measured_at.astimezone(UTC))
    if age > max_age:
        return False
    return all(currents.get(phase) is not None for phase in phases)


def _measured_per_phase(state: ChargerState, phase_count: int, voltage: float) -> float:
    """Return the measured draw per phase, or 0 when the power is missing."""
    if state.measured_power_w is None:
        return 0.0
    return state.measured_power_w / (voltage * phase_count)


def reserved_current(charger: Charger, state: ChargerState, voltage: float) -> float:
    """Return what a fixed or unavailable charger is reserved at on each phase.

    A charger that does not communicate, or has stopped communicating, is assumed to
    go on drawing its last known level, whatever `paused` and `car_connected` say.

    Args:
        charger: The charger.
        state: Its state.
        voltage: Phase voltage in V.

    Returns:
        For a fixed charger the higher of its fixed level and the measured draw;
        for an unavailable one the higher of its last setting (adjustable) or
        assumed current (on/off) and the measured draw.
    """
    measured = _measured_per_phase(state, len(charger.phases), voltage)
    if charger.kind is ChargerKind.ADJUSTABLE:
        level = state.current_setting
    else:
        level = charger.assumed_current or 0.0
    return max(level, measured)


def present_draw(charger: Charger, state: ChargerState, voltage: float) -> float:
    """Return what a charger is taken to draw now on each of its phases.

    Used for the phase-guard calculation. The smaller of setting and measured draw
    is taken, so a car drawing less than it is set to does not inflate the free
    current. A fixed or unavailable charger counts at the draw that is already inside
    the phase measurement (its measured draw, else its reservation); the rest of its
    reservation is left to the caller to hold back, so a reserved charger that is not
    drawing its level yet still shrinks what the others may take.

    Args:
        charger: The charger.
        state: Its state.
        voltage: Phase voltage in V.

    Returns:
        The draw in A: for a fixed or unavailable charger its measured draw, else
        its reserved current; 0 for a paused charger or one without a car, else the
        smaller of its nominal current and its measured draw.
    """
    if charger.kind is ChargerKind.FIXED or not state.available:
        if state.measured_power_w is None:
            return reserved_current(charger, state, voltage)
        return _measured_per_phase(state, len(charger.phases), voltage)
    if state.paused or not state.car_connected:
        return 0.0
    if charger.kind is ChargerKind.ADJUSTABLE:
        nominal = state.current_setting
    else:
        nominal = charger.assumed_current or 0.0
    if state.measured_power_w is None:
        return nominal
    return min(nominal, _measured_per_phase(state, len(charger.phases), voltage))


def _settling_draw(charger: Charger, state: ChargerState, voltage: float) -> float:
    """Return the draw of a charger still ramping down after a decrease.

    Without a car there is nothing ramping down, so a leftover power reading counts
    for nothing, as in `present_draw`.
    """
    controllable = charger.kind is not ChargerKind.FIXED and state.available
    if controllable and state.car_connected and state.measured_power_w is not None:
        return _measured_per_phase(state, len(charger.phases), voltage)
    return present_draw(charger, state, voltage)


def phase_capacity(
    snapshot: Snapshot, fresh: bool, settling: frozenset[str] = frozenset()
) -> dict[Phase, float]:
    """Return what all chargers together may use on each phase.

    Args:
        snapshot: The snapshot.
        fresh: Whether the phase measurements can be trusted.
        settling: Ids of chargers that were sent a decrease a moment ago. Each
            counts at its measured draw (when known) instead of the smaller of
            setting and measured draw, so a car still ramping down is not mistaken
            for other load.

    Returns:
        `min(budget, threshold - (measured - sum of present draws))` per phase when
        `fresh`, else the budget; a settling charger counts at its measured draw. A phase whose measurement is missing gets the
        budget. May be negative.
    """
    settings: Settings = snapshot.settings
    capacity: dict[Phase, float] = {}
    states = {state.charger_id: state for state in snapshot.states}
    for phase in Phase:
        measured = (
            snapshot.phase_currents.get(phase) if snapshot.phase_currents else None
        )
        if not fresh or measured is None:
            capacity[phase] = settings.phase_budget
            continue
        draw = sum(
            (_settling_draw if charger.id in settling else present_draw)(
                charger, states[charger.id], settings.voltage
            )
            for charger in snapshot.chargers
            if phase in charger.phases
        )
        capacity[phase] = min(
            settings.phase_budget, settings.phase_threshold - (measured - draw)
        )
    return capacity


def main() -> None:
    """Showcase this module's functionality."""
    claims = [
        Claim("three_phase", frozenset({Phase.L1, Phase.L2, Phase.L3}), 6, 16),
        Claim("single_l1", frozenset({Phase.L1}), 6, 10),
        Claim("single_l2", frozenset({Phase.L2}), 6, 32),
    ]
    capacity = {Phase.L1: 20.0, Phase.L2: 25.0, Phase.L3: 16.0}

    allocation = share(claims, capacity)

    for charger_id, current in allocation.items():
        print(f"{charger_id}: {current} A")

    # Less than the minimum left for a second claim on the same phase: it gets 0.
    claims = [
        Claim("first", frozenset({Phase.L1}), 6, 16),
        Claim("second", frozenset({Phase.L1}), 6, 16),
    ]
    capacity = {Phase.L1: 8.0, Phase.L2: 0.0, Phase.L3: 0.0}

    allocation = share(claims, capacity)

    print(f"tight phase: {allocation}")


if __name__ == "__main__":
    main()
