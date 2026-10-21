"""The phase guard over time: a level from one reading, and the latch between calls.

`instant_level` classifies one phase reading as green, yellow, red or emergency.
`advance_guard` carries that classification across calls using the memory of the
previous decision: increases wait for the phases to have been green for the green
hold, and an emergency releases only after a calm period.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .model import GuardLevel, Memory, Phase, PhaseCurrents, Settings


def instant_level(
    currents: PhaseCurrents, phases: frozenset[Phase], settings: Settings
) -> GuardLevel:
    """Classify one phase reading.

    Args:
        currents: The measured phase currents.
        phases: The phases to look at; a phase with no reading is skipped.
        settings: The installation settings.

    Returns:
        `EMERGENCY` if any reading exceeds the emergency threshold (when set), else
        `RED` if any exceeds the phase threshold, else `YELLOW` if any is at or above
        the threshold minus the yellow margin, else `GREEN`.
    """
    readings = [
        value
        for phase in Phase
        if phase in phases and (value := currents.get(phase)) is not None
    ]
    emergency = settings.emergency_threshold
    if emergency is not None and any(value > emergency for value in readings):
        return GuardLevel.EMERGENCY
    if any(value > settings.phase_threshold for value in readings):
        return GuardLevel.RED
    yellow_from = settings.phase_threshold - settings.yellow_margin
    if any(value >= yellow_from for value in readings):
        return GuardLevel.YELLOW
    return GuardLevel.GREEN


@dataclass(frozen=True)
class GuardState:
    """The guard after one call.

    Attributes:
        level: The level, None when the measurements are stale and no emergency is
            latched.
        green_since: Since when every call has seen green, None otherwise.
        emergency_at: When the emergency level was last entered, None when none is
            latched.
        increases_allowed: Whether the guard permits any increase now.
        intervened: Whether the level is red or emergency.
    """

    level: GuardLevel | None
    green_since: datetime | None
    emergency_at: datetime | None
    increases_allowed: bool
    intervened: bool


def _elapsed(now: datetime, since: datetime) -> timedelta:
    """Return `now - since`, compared in UTC."""
    return now.astimezone(UTC) - since.astimezone(UTC)


def advance_guard(
    memory: Memory | None,
    instant: GuardLevel | None,
    now: datetime,
    settings: Settings,
) -> GuardState:
    """Advance the guard by one call.

    Args:
        memory: The previous decision's memory, None when unusable.
        instant: The level of this call's reading, None when the measurements are
            stale or missing.
        now: The time now, aware.
        settings: The installation settings.

    Returns:
        The guard's state after this call. With stale data increases stay allowed
        (round 1's fallback cap governs them) unless an emergency is latched; with a
        fresh reading they need the level green for the green hold.
    """
    latched = memory is not None and memory.level is GuardLevel.EMERGENCY
    previous_green = memory.green_since if memory is not None else None
    previous_emergency = memory.emergency_at if memory is not None else None
    if instant is None:
        if latched:
            return GuardState(GuardLevel.EMERGENCY, None, previous_emergency, False, True)
        return GuardState(None, None, previous_emergency, True, False)
    if instant is GuardLevel.EMERGENCY:
        return GuardState(GuardLevel.EMERGENCY, None, now, False, True)
    level = instant
    emergency_at = None
    if latched:
        emergency_at = previous_emergency
        if instant is GuardLevel.GREEN:
            green_since = previous_green or now
            if _elapsed(now, green_since) >= settings.emergency_calm:
                emergency_at = None
            else:
                level = GuardLevel.EMERGENCY
        else:
            level = GuardLevel.EMERGENCY
            green_since = None
        if level is GuardLevel.EMERGENCY:
            return GuardState(
                level,
                green_since if instant is GuardLevel.GREEN else None,
                emergency_at,
                False,
                True,
            )
    else:
        green_since = (previous_green or now) if instant is GuardLevel.GREEN else None
    allowed = (
        level is GuardLevel.GREEN
        and green_since is not None
        and _elapsed(now, green_since) >= settings.green_hold
    )
    return GuardState(
        level,
        green_since,
        emergency_at,
        allowed,
        level in (GuardLevel.RED, GuardLevel.EMERGENCY),
    )


def main() -> None:
    """Showcase this module's functionality."""
    start = datetime(2026, 10, 21, 20, 0, tzinfo=UTC)
    settings = Settings(
        phase_budget=25.0,
        phase_threshold=32.0,
        fallback_current=6,
        timezone="Europe/Copenhagen",
        emergency_threshold=40.0,
    )
    phases = frozenset(Phase)  # L1, L2, L3
    readings = [33.0, 20.0, 20.0, 20.0]  # amps on L1; the first call is over the limit
    memory = None

    for step, amps in enumerate(readings):
        now = start + timedelta(seconds=30 * step)
        currents = PhaseCurrents(now, l1=amps, l2=10.0, l3=5.0)
        instant = instant_level(currents, phases, settings)

        state = advance_guard(memory, instant, now, settings)

        print(
            f"+{30 * step:>3} s: {amps} A -> {state.level}, "
            f"increases allowed: {state.increases_allowed}"
        )
        memory = Memory(
            saved_at=now,
            level=state.level,
            green_since=state.green_since,
            emergency_at=state.emergency_at,
        )

    # No usable reading: round 1's fallback cap governs, the guard does not block.
    instant = None

    state = advance_guard(memory, instant, start + timedelta(minutes=5), settings)

    print(f"stale: level {state.level}, increases allowed: {state.increases_allowed}")


if __name__ == "__main__":
    main()
