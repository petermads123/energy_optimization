"""Phase capacity, draw and reservations.

Pure helpers: what a phase may carry (budget and phase guard), what chargers draw or
are reserved at, and whether phase measurements can be trusted. Sharing the capacity
among chargers is the optimiser's job, in `optimize`.
"""

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


def phase_capacity(snapshot: Snapshot, fresh: bool) -> dict[Phase, float]:
    """Return what all chargers together may use on each phase.

    Args:
        snapshot: The snapshot.
        fresh: Whether the phase measurements can be trusted.

    Returns:
        `min(budget, threshold - (measured - sum of present draws))` per phase when
        `fresh`, else the budget. A phase whose measurement is missing gets the
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
            present_draw(charger, states[charger.id], settings.voltage)
            for charger in snapshot.chargers
            if phase in charger.phases
        )
        capacity[phase] = min(
            settings.phase_budget, settings.phase_threshold - (measured - draw)
        )
    return capacity


def main() -> None:
    """Showcase this module's functionality."""
    now = datetime(2026, 10, 21, 20, 0, tzinfo=UTC)
    max_age = timedelta(seconds=60)
    phases = frozenset({Phase.L1, Phase.L2})
    currents = PhaseCurrents(now - timedelta(seconds=30), l1=12.0, l2=10.0, l3=None)

    fresh = measurements_fresh(currents, now, max_age, phases)

    print(f"measurements fresh on L1 and L2: {fresh}")

    # L3 has no value, so a charger on all three phases cannot trust them.
    phases = frozenset(Phase)

    fresh = measurements_fresh(currents, now, max_age, phases)

    print(f"measurements fresh on all phases: {fresh}")

    charger = Charger(
        "fixed", frozenset({Phase.L1}), ChargerKind.FIXED, None, None, 8.0
    )
    state = ChargerState("fixed", True, 8, False, measured_power_w=1150.0)
    voltage = 230.0

    reserved = reserved_current(charger, state, voltage)

    print(f"reserved at {reserved} A")


if __name__ == "__main__":
    main()
