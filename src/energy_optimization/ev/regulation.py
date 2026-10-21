"""Regulation of one adjustable charger to what its car actually draws.

A car that draws much less than its setting wastes allocation other chargers could
use. `regulate` cuts the charger to its measured draw after a whole under-use period,
ramps it back one step per whole ramp period of full use, and offers a charger that
was cut to 0 current again once the resume probe has passed or the session changed.
"""

import math
from dataclasses import dataclass
from datetime import UTC, datetime

from .model import (
    Charger,
    ChargerKind,
    ChargerMemory,
    ChargerState,
    Phase,
    Reduction,
    Settings,
)


@dataclass(frozen=True)
class Regulation:
    """One adjustable charger's regulation after one call.

    Attributes:
        regulated_cap: Cap in A the charger may not exceed, None for none; 0 means
            stopped for under-use.
        under_use_since: Since when the draw has been under the setting.
        full_use_since: Since when the draw has matched the setting.
        under_use_stop_at: When the charger was cut to 0, None otherwise.
        cut: Whether this call cut the charger.
    """

    regulated_cap: int | None
    under_use_since: datetime | None
    full_use_since: datetime | None
    under_use_stop_at: datetime | None
    cut: bool


def _elapsed_seconds(now: datetime, since: datetime) -> float:
    """Return seconds from `since` to `now`, compared in UTC."""
    return (now.astimezone(UTC) - since.astimezone(UTC)).total_seconds()


def regulate(
    previous: ChargerMemory | None,
    charger: Charger,
    state: ChargerState,
    reset: bool,
    now: datetime,
    settings: Settings,
) -> Regulation:
    """Take one regulation step for one charger.

    Args:
        previous: The charger's memory from the last call, None when there is none.
        charger: The charger; only an adjustable one is regulated.
        state: Its state now.
        reset: Whether the session was re-plugged or changed; clears the regulation.
        now: The time now, aware.
        settings: The installation settings.

    Returns:
        The regulation after this call. An on/off charger, and a charger without a
        power reading, is never regulated.
    """
    if charger.kind is not ChargerKind.ADJUSTABLE:
        return Regulation(None, None, None, None, False)
    low = charger.min_current or 0
    high = charger.max_current or 0
    cap = previous.regulated_cap if previous and not reset else None
    under_since = previous.under_use_since if previous and not reset else None
    full_since = previous.full_use_since if previous and not reset else None
    stop_at = previous.under_use_stop_at if previous and not reset else None
    if cap is not None and 0 < cap < low:
        cap = 0  # a cap the charger cannot run at is a stop, whoever wrote it
    if cap == 0 and stop_at is None:
        stop_at = now  # a stop with no start time is probed from the first sight
    if cap != 0:
        stop_at = None
    if (
        stop_at is not None
        and _elapsed_seconds(now, stop_at) >= settings.resume_probe.total_seconds()
    ):
        cap = None
        stop_at = None
    if (
        state.paused
        or not state.car_connected
        or state.measured_power_w is None
        or cap == 0
    ):
        return Regulation(cap, None, None, stop_at, False)

    measured = state.measured_power_w / (settings.voltage * len(charger.phases))
    setting = state.current_setting
    if measured < setting - settings.deadband:
        under_since = under_since or now
        if (
            _elapsed_seconds(now, under_since)
            < settings.under_use_period.total_seconds()
        ):
            return Regulation(cap, under_since, None, stop_at, False)
        if settings.reduction is Reduction.HALVE:
            reduced = math.floor(setting / 2)
        else:
            reduced = math.ceil(measured + settings.under_use_margin)
        # A cut only ever lowers: never above the setting, the existing cap or the
        # charger's maximum, which a deadband smaller than the margin could allow.
        ceiling = min(high, high if cap is None else cap, math.ceil(setting))
        if reduced >= ceiling:
            return Regulation(cap, None, None, stop_at, False)
        if reduced < low:
            return Regulation(0, None, None, now, True)
        return Regulation(reduced, None, None, stop_at, True)
    if measured >= setting - settings.under_use_margin / 2:
        full_since = full_since or now
        if (
            cap is not None
            and cap > 0
            and _elapsed_seconds(now, full_since)
            >= settings.ramp_period.total_seconds()
        ):
            # Climb one step from the lower of the cap and the setting, but never
            # below the cap: a setting held down by the guard or by pacing neither
            # lets the cap climb nor drags it down below the minimum.
            cap = max(cap, min(cap, math.floor(setting)) + settings.ramp_step)
            full_since = now
            if cap >= high:
                cap = None
        return Regulation(cap, None, full_since, stop_at, False)
    return Regulation(cap, None, None, stop_at, False)


def main() -> None:
    """Showcase this module's functionality."""
    now = datetime(2026, 10, 21, 20, 0, tzinfo=UTC)
    settings = Settings(
        phase_budget=25.0,
        phase_threshold=32.0,
        fallback_current=6,
        timezone="Europe/Copenhagen",
        reduction=Reduction.TO_USE,  # TO_USE, HALVE
    )
    charger = Charger("carport", frozenset({Phase.L1}), ChargerKind.ADJUSTABLE, 6, 16)
    long_ago = now - settings.under_use_period
    reset = False  # True clears the regulation, e.g. after a re-plug

    # 16 A set, about 7 A drawn for a whole under-use period: cut to 8 A.
    state = ChargerState("carport", True, 16, False, measured_power_w=7 * 230.0)
    previous = ChargerMemory("carport", car_connected=True, under_use_since=long_ago)

    regulation = regulate(previous, charger, state, reset, now, settings)

    print(f"16 A set, 7 A drawn for 15 min: cap {regulation.regulated_cap} A")

    # 10 A set, 2 A drawn: the cut falls below the 6 A minimum, so the charger stops.
    state = ChargerState("carport", True, 10, False, measured_power_w=2 * 230.0)

    regulation = regulate(previous, charger, state, reset, now, settings)

    print(f"10 A set, 2 A drawn: cap {regulation.regulated_cap} A (0 means stopped)")

    # 16 A set, 14.5 A drawn: inside the 2 A deadband, nothing changes.
    state = ChargerState("carport", True, 16, False, measured_power_w=14.5 * 230.0)

    regulation = regulate(previous, charger, state, reset, now, settings)

    print(f"16 A set, 14.5 A drawn: cap {regulation.regulated_cap}")

    # After a cut to 10 A, a whole ramp period of full use raises the cap one step.
    state = ChargerState("carport", True, 10, False, measured_power_w=9.5 * 230.0)
    previous = ChargerMemory(
        "carport",
        car_connected=True,
        regulated_cap=10,
        full_use_since=now - settings.ramp_period,
    )

    regulation = regulate(previous, charger, state, reset, now, settings)

    print(f"cap 10 A, 9.5 A drawn for 5 min: cap {regulation.regulated_cap} A")


if __name__ == "__main__":
    main()
