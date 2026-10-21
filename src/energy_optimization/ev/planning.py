"""Price planning: how much energy is needed and which quarters should deliver it.

Pure functions over plain values. `plan_sessions` plans every session against shared
per-phase capacity, most urgent first, choosing the cheapest quarters in contiguous
blocks of a minimum length where the price cap, the horizon and the capacity allow.
"""

import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from .model import Phase, Session
from .quarters import QUARTER, quarter_starts
from .result import PlannedQuarter

_EPS = 1e-9
_CAPPED, _OVER_CAP, _UNPRICED = 1, 2, 3


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
class PlanRequest:
    """One session to plan.

    Attributes:
        charger_id: The charger.
        phases: The phases it is counted on.
        min_current: Lowest non-zero current in A.
        max_current: Highest current in A; equal to `min_current` means
            all-or-nothing.
        need_kwh: Energy to deliver.
        deadline: The moment the energy is needed by, aware.
    """

    charger_id: str
    phases: frozenset[Phase]
    min_current: float
    max_current: float
    need_kwh: float
    deadline: datetime


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


@dataclass(frozen=True)
class _Slot:
    """A usable quarter for one request."""

    start: datetime
    current: float
    energy: float
    price: float | None
    category: int


def _usable_current(request: PlanRequest, room: float) -> float:
    """Return the current a request can run at given the room, or 0 if none."""
    if request.min_current == request.max_current:
        return request.max_current if room >= request.max_current - _EPS else 0.0
    current = min(request.max_current, float(math.floor(room + _EPS)))
    return current if current >= request.min_current else 0.0


def _slots(
    request: PlanRequest,
    horizon: Sequence[datetime],
    prices: Mapping[datetime, float],
    remaining: Mapping[datetime, dict[Phase, float]],
    voltage: float,
    price_cap: float | None,
) -> list[_Slot]:
    """List the quarters in the horizon a request can use."""
    slots: list[_Slot] = []
    for start in horizon:
        room = min(remaining[start][phase] for phase in request.phases)
        current = _usable_current(request, room)
        if current <= 0:
            continue
        price = prices.get(start)
        if price is None:
            category = _UNPRICED
        elif price_cap is None or price <= price_cap:
            category = _CAPPED
        else:
            category = _OVER_CAP
        energy = energy_per_quarter_kwh(current, len(request.phases), voltage)
        slots.append(_Slot(start, current, energy, price, category))
    return slots


def _blocks(slots: Sequence[_Slot], length: int) -> list[tuple[int, ...]]:
    """List every run of `length` consecutive usable quarters, as slot indices."""
    blocks: list[tuple[int, ...]] = []
    for first in range(len(slots) - length + 1):
        indices = tuple(range(first, first + length))
        if all(slots[a + 1].start - slots[a].start == QUARTER for a in indices[:-1]):
            blocks.append(indices)
    return blocks


def _select(slots: Sequence[_Slot], need_kwh: float, length: int) -> set[int]:
    """Choose slot indices that cover the need, cheapest first, in blocks."""
    blocks = _blocks(slots, length)
    chosen: set[int] = set()
    got = 0.0
    for tier in (_CAPPED, _OVER_CAP, _UNPRICED):
        tier_blocks = [
            block for block in blocks if max(slots[i].category for i in block) <= tier
        ]
        while got < need_kwh - _EPS:
            picked = _best_block(slots, tier_blocks, chosen, tier, need_kwh - got)
            if picked is None:
                break
            for index in picked:
                if index not in chosen:
                    chosen.add(index)
                    got += slots[index].energy
        while got < need_kwh - _EPS:
            singles = [
                i
                for i, slot in enumerate(slots)
                if i not in chosen and slot.category == tier
            ]
            if not singles:
                break
            index = (
                max(singles, key=lambda i: slots[i].start)
                if tier == _UNPRICED
                else min(singles, key=lambda i: (slots[i].price or 0.0, slots[i].start))
            )
            chosen.add(index)
            got += slots[index].energy
    return chosen


def _best_block(
    slots: Sequence[_Slot],
    blocks: Sequence[tuple[int, ...]],
    chosen: set[int],
    tier: int,
    still_needed: float,
) -> tuple[int, ...] | None:
    """Pick the best eligible block for one pass, or None if there is none."""
    eligible: list[tuple[tuple[int, ...], list[int], float]] = []
    for block in blocks:
        new = [i for i in block if i not in chosen]
        if not new or all(slots[i].category != tier for i in new):
            continue
        eligible.append((block, new, sum(slots[i].energy for i in new)))
    if not eligible:
        return None
    finishing = [
        item
        for item in eligible
        if item[2] >= still_needed - _EPS
        and item[2] - min(slots[i].energy for i in item[1]) < still_needed - _EPS
    ]
    pool = finishing or eligible
    if tier == _UNPRICED:
        return min(pool, key=lambda item: _latest_key(slots, item[1], item[0]))[0]
    return min(pool, key=lambda item: _cheapest_key(slots, item[1], item[0]))[0]


def _cheapest_key(
    slots: Sequence[_Slot], new: Sequence[int], block: Sequence[int]
) -> tuple[float, datetime]:
    """Score a block in a priced pass: mean price of new quarters, earliest start."""
    mean = sum(slots[i].price or 0.0 for i in new) / len(new)
    return mean, slots[block[0]].start


def _latest_key(
    slots: Sequence[_Slot], new: Sequence[int], block: Sequence[int]
) -> tuple[float, float, float]:
    """Score a block in the unpriced pass: latest first, then cheapest priced part."""
    latest = max(slots[i].start for i in new).timestamp()
    priced = [p for i in new if (p := slots[i].price) is not None]
    cheapest = sum(priced) / len(priced) if priced else math.inf
    return -latest, cheapest, -slots[block[0]].start.timestamp()


def _plan_one(
    request: PlanRequest,
    prices: Mapping[datetime, float],
    remaining: dict[datetime, dict[Phase, float]],
    voltage: float,
    min_block_quarters: int,
    price_cap: float | None,
    zone: ZoneInfo,
    horizon: Sequence[datetime],
) -> SessionPlan:
    """Plan one request against the remaining capacity and consume it."""
    slots = _slots(request, horizon, prices, remaining, voltage, price_cap)
    if request.need_kwh <= _EPS:
        indices: set[int] = set()
        reachable = True
    elif sum(slot.energy for slot in slots) < request.need_kwh - _EPS:
        indices = set(range(len(slots)))
        reachable = False
    else:
        indices = _select(slots, request.need_kwh, min_block_quarters)
        reachable = True
    quarters: list[PlannedQuarter] = []
    planned = 0.0
    for index in sorted(indices):
        slot = slots[index]
        quarters.append(
            PlannedQuarter(slot.start.astimezone(zone), slot.price, slot.current)
        )
        planned += slot.energy
        for phase in request.phases:
            remaining[slot.start][phase] -= slot.current
    return SessionPlan(
        charger_id=request.charger_id,
        quarters=tuple(quarters),
        planned_kwh=planned,
        reachable=reachable,
        price_cap_exceeded=any(slots[i].category == _OVER_CAP for i in indices),
        uses_unpriced=any(slots[i].category == _UNPRICED for i in indices),
    )


def plan_sessions(
    requests: Sequence[PlanRequest],
    now: datetime,
    prices: Mapping[datetime, float],
    capacity: Mapping[Phase, float],
    voltage: float,
    min_block_quarters: int,
    price_cap: float | None,
    timezone: str,
    first: Collection[str] = frozenset(),
) -> dict[str, SessionPlan]:
    """Plan every request against shared per-phase capacity, most urgent first.

    Args:
        requests: The sessions to plan.
        now: The time now, aware.
        prices: The price of each known quarter, keyed by UTC quarter start.
        capacity: What the chargers together may use per phase, constant over the
            horizon.
        voltage: Phase voltage in V.
        min_block_quarters: Minimum length of a contiguous run of quarters.
        price_cap: Price above which quarters are avoided, None for off.
        timezone: IANA name; only sets the zone of `PlannedQuarter.start`.
        first: Charger ids whose sessions claim capacity before all the others, each
            group still most urgent first. A session that holds its place (it is
            running and must not be displaced) is planned first, and the rest are
            planned on what is left of every quarter, so the plans together never
            book more than `capacity`.

    Returns:
        Each session's plan, keyed by charger id. A request whose `max_current` is 0
        plans nothing and is unreachable.

    Raises:
        ValueError: If `now` or a deadline is naive.
        ZoneInfoNotFoundError: If `timezone` is not a known zone.
    """
    zone = ZoneInfo(timezone)
    horizons = {
        request.charger_id: quarter_starts(now, request.deadline)
        for request in requests
    }
    remaining: dict[datetime, dict[Phase, float]] = {}
    for horizon in horizons.values():
        for start in horizon:
            if start not in remaining:
                remaining[start] = {phase: capacity.get(phase, 0.0) for phase in Phase}

    def urgency(request: PlanRequest) -> tuple[bool, float, datetime, str]:
        per_quarter = energy_per_quarter_kwh(
            request.max_current, len(request.phases), voltage
        )
        # A request that cannot draw anything (a user limit of 0) can never be
        # served: it is the least slack there is, and plans nothing.
        needed = (
            math.ceil(request.need_kwh / per_quarter - _EPS)
            if per_quarter > 0
            else math.inf
        )
        slack = len(horizons[request.charger_id]) - max(needed, 0)
        return (
            request.charger_id not in first,
            slack,
            request.deadline.astimezone(UTC),
            request.charger_id,
        )

    plans: dict[str, SessionPlan] = {}
    for request in sorted(requests, key=urgency):
        plans[request.charger_id] = _plan_one(
            request,
            prices,
            remaining,
            voltage,
            min_block_quarters,
            price_cap,
            zone,
            horizons[request.charger_id],
        )
    return plans


def main() -> None:
    """Showcase this module's functionality."""
    now = datetime(2026, 10, 21, 22, 0, tzinfo=UTC)
    deadline = now + timedelta(hours=8)
    cheap_hours = {0.30: [0, 1, 2], 0.10: [3, 4], 0.20: [5, 6, 7]}
    prices: dict[datetime, float] = {}
    for price, hours in cheap_hours.items():
        for hour in hours:
            for quarter in range(4):
                start = now + timedelta(hours=hour, minutes=15 * quarter)
                prices[start] = price
    requests = [
        PlanRequest("a", frozenset({Phase.L1}), 6, 16, 12.0, deadline),
        PlanRequest("b", frozenset({Phase.L1}), 6, 16, 12.0, deadline),
    ]
    capacity = {Phase.L1: 16.0, Phase.L2: 16.0, Phase.L3: 16.0}
    voltage = 230.0
    min_block_quarters = 2
    price_cap = None  # None, or a price such as 0.25
    timezone = "Europe/Copenhagen"

    plans = plan_sessions(
        requests,
        now,
        prices,
        capacity,
        voltage,
        min_block_quarters,
        price_cap,
        timezone,
    )

    for charger_id, plan in plans.items():
        times = [q.start.strftime("%H:%M") for q in plan.quarters]
        print(f"{charger_id}: {plan.planned_kwh:.1f} kWh at {times}")

    # A session with no SOC information falls back to the default need.
    session = Session("a", ready_time=time(6, 0))
    default_need_kwh = 30.0

    need, source = remaining_need_kwh(session, default_need_kwh)

    print(f"need {need} kWh from {source}")


if __name__ == "__main__":
    main()
