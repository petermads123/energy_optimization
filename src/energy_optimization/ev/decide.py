"""The EV charging decision core: one snapshot in, one decision out.

`decide` recomputes everything on every call. It classifies each charger, assigns
priority tiers, hands the demands to the optimiser (`optimize`), which plans the
cheapest quarters and shares the capacity of the shared grid connection tier by tier on
what the tiers above leave, and returns the actions that get there in a safe order,
with a plain-language reason for each charger. The optimiser raises `SolverError` when
HiGHS fails; `decide` lets it through.
"""

from dataclasses import dataclass, replace
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from .allocation import measurements_fresh, phase_capacity, reserved_current
from .model import (
    Charger,
    ChargerKind,
    ChargerState,
    Phase,
    PhaseCurrents,
    PriceInterval,
    PriceSeries,
    Session,
    Settings,
    Snapshot,
)
from .optimize import Demand, Outcome, Tier, optimize
from .planning import (
    NeedSource,
    SessionPlan,
    energy_per_quarter_kwh,
    remaining_need_kwh,
)
from .quarters import (
    QUARTER,
    floor_to_quarter,
    next_occurrence,
    prices_fresh,
    quarter_prices,
)
from .result import (
    Action,
    ActionKind,
    ChargerDecision,
    Decision,
    Event,
    EventKind,
    Params,
    PhaseAmps,
    Reason,
    ReasonCode,
    Stage,
    Status,
)

_EPS = 1e-9


class _Group(StrEnum):
    """How a charger is treated."""

    RESERVED = "reserved"
    IDLE = "idle"
    SMART_OFF = "smart_off"
    SMART = "smart"


@dataclass
class _Item:
    """Working record for one charger during a call."""

    charger: Charger
    state: ChargerState
    session: Session | None
    group: _Group
    code: ReasonCode
    acts: bool = True
    priority: int | None = None
    need: float | None = None
    source: NeedSource | None = None
    deadline: datetime | None = None
    eff_max: float = 0.0
    can_run: bool = False
    allocation: float = 0.0
    plan: SessionPlan | None = None
    final_code: ReasonCode | None = None
    covered_event: bool = False
    starved: bool = False


@dataclass(frozen=True)
class _Context:
    """Per-call values shared by the steps."""

    now: datetime
    zone: ZoneInfo
    settings: Settings
    prices_ok: bool
    fallback: bool


def _top_current(charger: Charger) -> float:
    """Return a controllable charger's highest current."""
    if charger.kind is ChargerKind.ADJUSTABLE:
        return float(charger.max_current or 0)
    return float(charger.assumed_current or 0.0)


def _low_current(charger: Charger) -> float:
    """Return a controllable charger's lowest non-zero current."""
    if charger.kind is ChargerKind.ADJUSTABLE:
        return float(charger.min_current or 0)
    return float(charger.assumed_current or 0.0)


def _classify(
    charger: Charger, state: ChargerState, session: Session | None, ctx: _Context
) -> _Item:
    """Classify one charger by the first rule that applies."""
    if not state.available:
        item = _Item(charger, state, session, _Group.RESERVED, ReasonCode.UNAVAILABLE)
        item.acts = False
    elif charger.kind is ChargerKind.FIXED:
        item = _Item(charger, state, session, _Group.RESERVED, ReasonCode.FIXED)
        item.acts = False
    elif not state.car_connected:
        item = _Item(charger, state, session, _Group.IDLE, ReasonCode.NO_CAR)
        item.acts = False
        return item
    elif session is None:
        return _Item(charger, state, session, _Group.IDLE, ReasonCode.NO_SESSION)
    else:
        item = _Item(charger, state, session, _Group.SMART, ReasonCode.IN_PLAN)
    if session is None:
        return item
    item.need, item.source = remaining_need_kwh(
        session, ctx.settings.default_energy_need_kwh
    )
    item.deadline = next_occurrence(
        session.ready_time, session.plugged_in_at or ctx.now, ctx.settings.timezone
    )
    if item.need <= _EPS:
        item.need = 0.0  # float residue from summed deliveries is no need at all
    if item.group is _Group.RESERVED:
        return item
    if not session.smart_charging:
        item.group = _Group.SMART_OFF
        item.code = ReasonCode.SMART_CHARGING_OFF
    elif session.soc_percent is not None and session.soc_percent >= 100:
        item.group = _Group.IDLE
        item.code = ReasonCode.TARGET_REACHED
        item.need = 0.0
    elif session.charge_now:
        item.priority = 1
        item.code = ReasonCode.CHARGE_NOW
        item.covered_event = item.need == 0
    elif item.need == 0:
        item.priority = 3
        item.code = ReasonCode.PLAN_COMPLETED
    elif not ctx.prices_ok:
        item.priority = 3
        item.code = ReasonCode.PRICES_STALE
    elif item.deadline <= ctx.now.astimezone(UTC):
        item.priority = 3
        item.code = ReasonCode.READY_TIME_PASSED
    return item


def _set_limits(item: _Item, ctx: _Context) -> None:
    """Work out a controllable charger's effective maximum and whether it can run."""
    charger = item.charger
    top = _top_current(charger)
    eff = top
    if item.session is not None and item.session.user_current_limit is not None:
        eff = min(eff, float(item.session.user_current_limit))
    if ctx.fallback:
        cap = charger.fallback_current
        eff = min(eff, float(ctx.settings.fallback_current if cap is None else cap))
    item.eff_max = eff
    if charger.kind is ChargerKind.ADJUSTABLE:
        item.can_run = eff >= _low_current(charger)
    else:
        item.can_run = eff >= top


def _rank_key(item: _Item) -> tuple[datetime, float, str]:
    """Rank earliest deadline first, then largest remaining need, then id."""
    deadline = item.deadline or datetime.max.replace(tzinfo=UTC)
    return deadline, -(item.need or 0.0), item.charger.id


def _subtract(cap: dict[Phase, float], phases: frozenset[Phase], amount: float) -> None:
    """Take current off the capacity of every phase in `phases`."""
    for phase in phases:
        cap[phase] -= amount


def _clip(cap: dict[Phase, float]) -> dict[Phase, float]:
    """Return the capacity with negative values raised to 0."""
    return {phase: max(0.0, value) for phase, value in cap.items()}


def _demand(item: _Item, tier: Tier) -> Demand:
    """Build the optimiser's demand for a charger."""
    top = _top_current(item.charger)
    limit = item.session.user_current_limit if item.session else None
    if limit is not None:
        top = min(top, float(limit))
    return Demand(
        item.charger.id,
        item.charger.phases,
        tier,
        _low_current(item.charger),
        top,
        item.eff_max,
        item.need or 0.0 if tier is Tier.PLANNED else 0.0,
        item.deadline if tier is Tier.PLANNED else None,
    )


def _demands(items: list[_Item]) -> list[Demand]:
    """Build every demand in tier order, each tier in ranking order."""
    tiers = (
        (Tier.SMART_OFF, lambda i: i.group is _Group.SMART_OFF and i.can_run),
        (
            Tier.CHARGE_NOW,
            lambda i: i.group is _Group.SMART and i.priority == 1 and i.can_run,
        ),
        (Tier.PLANNED, lambda i: i.group is _Group.SMART and i.priority is None),
        (
            Tier.SPARE,
            lambda i: i.group is _Group.SMART and i.priority == 3 and i.can_run,
        ),
    )
    demands: list[Demand] = []
    for tier, belongs in tiers:
        demands.extend(
            _demand(i, tier) for i in sorted(filter(belongs, items), key=_rank_key)
        )
    return demands


def _room(items: list[_Item], cap: dict[Phase, float]) -> dict[Phase, float]:
    """Return the first-quarter room left on each phase after every allocation."""
    room = dict(cap)
    for item in items:
        if item.group in (_Group.SMART_OFF, _Group.SMART):
            _subtract(room, item.charger.phases, item.allocation)
    return room


def _outranked(item: _Item, items: list[_Item]) -> bool:
    """Say whether a higher-precedence charger on one of its phases got current."""
    for other in items:
        if other is item or other.allocation <= 0:
            continue
        if not item.charger.phases & other.charger.phases:
            continue
        if other.group is _Group.SMART_OFF:
            return True
        if (
            other.group is _Group.SMART
            and other.priority is not None
            and item.priority is not None
            and other.priority < item.priority
        ):
            return True
    return False


def _short(item: _Item, room: dict[Phase, float]) -> bool:
    """Say whether a charger is below its maximum for want of room on a phase."""
    if item.allocation >= item.eff_max - _EPS:
        return False
    need = max(1.0, _low_current(item.charger))
    return any(room[phase] < need - _EPS for phase in item.charger.phases)


def _present(item: _Item) -> float:
    """Return the current a charger is at now, 0 when paused."""
    if item.state.paused:
        return 0.0
    if item.charger.kind is ChargerKind.ADJUSTABLE:
        return item.state.current_setting
    return float(item.charger.assumed_current or 0.0)


def _actions(items: list[_Item]) -> tuple[Action, ...]:
    """Build the ordered actions: every decrease and pause before every increase."""
    down: list[Action] = []
    up: list[Action] = []
    for item in items:
        if not item.acts:
            continue
        target = item.allocation
        present = _present(item)
        charger_id = item.charger.id
        if target <= 0:
            if present > 0:
                down.append(Action(charger_id, ActionKind.PAUSE, 0.0, Stage.DOWN))
        elif item.state.paused:
            up.append(Action(charger_id, ActionKind.RESUME, target, Stage.UP))
        elif abs(target - present) > _EPS:
            kind = ActionKind.SET_CURRENT
            if target < present:
                down.append(Action(charger_id, kind, target, Stage.DOWN))
            else:
                up.append(Action(charger_id, kind, target, Stage.UP))
    down.sort(key=lambda action: action.charger_id)
    up.sort(key=lambda action: action.charger_id)
    return (*down, *up)


def _expected_finish(item: _Item, ctx: _Context) -> datetime | None:
    """Estimate when a session's remaining need will be met."""
    need = item.need
    if need is None:
        return None
    if need <= _EPS:
        return ctx.now.astimezone(ctx.zone)
    voltage = ctx.settings.voltage
    phase_count = len(item.charger.phases)
    if item.plan is not None:
        total = 0.0
        for quarter in item.plan.quarters:
            total += energy_per_quarter_kwh(quarter.current, phase_count, voltage)
            if total >= need - _EPS:
                # the quarter's end is added on the UTC instant, never on a zone-aware start
                return (quarter.start.astimezone(UTC) + QUARTER).astimezone(ctx.zone)
        return None
    if item.allocation > 0:
        hours = need * 1000 / (voltage * item.allocation * phase_count)
        try:
            return (ctx.now.astimezone(UTC) + timedelta(hours=hours)).astimezone(
                ctx.zone
            )
        except OverflowError:
            return None  # a trickle of current: no finish within any calendar
    return None


def _capped_by(
    item: _Item, ctx: _Context, raw: dict[Phase, float], room: dict[Phase, float]
) -> str | None:
    """Say what holds an allocated charger below its own maximum, if anything."""
    top = _top_current(item.charger)
    if item.group is _Group.RESERVED or item.allocation <= 0:
        return None
    if item.allocation >= top - _EPS:
        return None
    if item.allocation >= item.eff_max - _EPS:
        limit = item.session.user_current_limit if item.session else None
        if ctx.fallback and (limit is None or limit >= item.eff_max):
            return "fallback"
        return "user_limit"
    if not _short(item, room):
        return None  # the plan runs it below its maximum on purpose
    budget = ctx.settings.phase_budget
    if any(raw[phase] < budget - _EPS for phase in item.charger.phases):
        return "phase"
    return "budget"


def _sentence(item: _Item, code: ReasonCode, ctx: _Context) -> str:
    """Write the English sentence for a charger's reason."""
    prefix = f"Priority {item.priority} — " if item.priority else ""
    ready = item.session.ready_time.strftime("%H:%M") if item.session else ""
    texts = {
        ReasonCode.CHARGE_NOW: "charge now",
        ReasonCode.IN_PLAN: "the current quarter is in the plan",
        ReasonCode.READY_TIME_PASSED: (
            f"ready time {ready} passed, charging on spare capacity"
        ),
        ReasonCode.PLAN_COMPLETED: "plan completed, charging on spare capacity",
        ReasonCode.PRICES_STALE: "prices are stale, charging on spare capacity",
        ReasonCode.PLANNED_LATER: "charging is planned for later quarters",
        ReasonCode.WAITING_FOR_HIGHER_PRIORITY: (
            "waiting for higher-priority chargers"
        ),
        ReasonCode.WAITING_FOR_CAPACITY: "waiting for capacity",
        ReasonCode.BELOW_MINIMUM: (
            f"cannot run: limit {item.eff_max:g} A is below the minimum "
            f"{_low_current(item.charger):g} A"
        ),
        ReasonCode.SMART_CHARGING_OFF: (
            f"smart charging off, charging at {item.allocation:g} A"
            if item.allocation > 0
            else "smart charging off, no capacity left"
        ),
        ReasonCode.FIXED: f"fixed charger, reserved at {item.allocation:g} A",
        ReasonCode.UNAVAILABLE: f"charger unavailable, reserved at {item.allocation:g} A",
        ReasonCode.TARGET_REACHED: "target reached, the car cannot take more",
        ReasonCode.NO_CAR: "no car connected",
        ReasonCode.NO_SESSION: "car connected but no charging session",
    }
    body = texts[code]
    text = prefix + body if prefix else body[0].upper() + body[1:]
    if item.group is _Group.SMART and item.source is NeedSource.DEFAULT:
        text += f" (assumed need {ctx.settings.default_energy_need_kwh:g} kWh)"
    return text


def _reason(
    item: _Item, ctx: _Context, raw: dict[Phase, float], room: dict[Phase, float]
) -> Reason:
    """Build a charger's reason: code, parameters and sentence."""
    code = item.final_code or item.code
    params: Params = {}
    if item.priority is not None:
        params["priority"] = item.priority
    params["allocation"] = item.allocation
    if item.source is not None:
        params["need_source"] = item.source.value
    if code is ReasonCode.READY_TIME_PASSED and item.session is not None:
        params["ready_time"] = item.session.ready_time.strftime("%H:%M")
    capped = _capped_by(item, ctx, raw, room)
    if capped is not None:
        params["capped_by"] = capped
    return Reason(code, _sentence(item, code, ctx), params)


def _events(items: list[_Item], ctx: _Context, outcome: Outcome) -> tuple[Event, ...]:
    """Collect the notification-worthy events of one call."""
    events: list[Event] = []
    cap = ctx.settings.price_cap
    for item in items:
        plan = item.plan
        charger_id = item.charger.id
        if plan is not None and not plan.reachable:
            delivered = item.session.delivered_kwh if item.session else 0.0
            reachable = delivered + plan.planned_kwh
            target = delivered + (item.need or 0.0)
            deadline = (item.deadline or ctx.now).astimezone(ctx.zone).isoformat()
            events.append(
                Event(
                    EventKind.TARGET_UNREACHABLE,
                    f"Charger {charger_id} will not reach its target by {deadline}: "
                    f"{reachable:.1f} of {target:.1f} kWh",
                    charger_id,
                    {
                        "reachable_kwh": reachable,
                        "target_kwh": target,
                        "deadline": deadline,
                    },
                )
            )
        if plan is not None and plan.price_cap_exceeded:
            events.append(
                Event(
                    EventKind.PRICE_CAP_EXCEEDED,
                    f"Charger {charger_id} charges above the price cap to meet "
                    "its ready time",
                    charger_id,
                    {"price_cap": cap if cap is not None else 0.0},
                )
            )
        if item.covered_event:
            events.append(
                Event(
                    EventKind.CHARGE_NOW_NEED_COVERED,
                    f"Charger {charger_id}: the need is covered, reset charge now",
                    charger_id,
                )
            )
    if ctx.fallback:
        events.append(
            Event(
                EventKind.PHASE_DATA_STALE,
                "Phase measurements are stale or missing, "
                "chargers are capped at their fallback current",
            )
        )
    if not ctx.prices_ok:
        events.append(
            Event(
                EventKind.PRICES_STALE, "The price series is stale, nothing is planned"
            )
        )
    if not outcome.proven:
        events.append(
            Event(
                EventKind.PLAN_NOT_PROVEN_OPTIMAL,
                "The solver stopped at its node budget: the plan is the best found, "
                "not proven optimal",
            )
        )
    return tuple(events)


def _status(
    ctx: _Context,
    items: list[_Item],
    active: list[_Item],
    raw: dict[Phase, float],
    room: dict[Phase, float],
) -> Status:
    """Pick the overall status: the first that applies."""
    if ctx.fallback:
        return Status.PHASE_DATA_STALE
    if not ctx.prices_ok:
        return Status.PRICES_STALE
    waiting = active + [item for item in items if item.starved]
    if waiting and all(item.allocation <= 0 for item in waiting):
        return Status.WAITING_FOR_CAPACITY
    served = (
        active
        + [item for item in items if item.group is _Group.SMART_OFF and item.can_run]
        + [item for item in items if item.starved]
    )
    short = [item for item in served if _short(item, room)]
    if not short:
        return Status.NORMAL
    budget = ctx.settings.phase_budget
    for item in short:
        if any(raw[phase] < budget - _EPS for phase in item.charger.phases):
            return Status.LIMITED_BY_PHASE
    return Status.LIMITED_BY_BUDGET


def decide(snapshot: Snapshot) -> Decision:
    """Decide what every charger should be set to now.

    Args:
        snapshot: One complete, validated snapshot of the installation.

    Returns:
        The ordered actions (every decrease and pause before every increase), a
        decision with reason per charger in snapshot order, the amps still
        available per phase, the overall status and the events.
    """
    settings = snapshot.settings
    now = snapshot.now
    states = {state.charger_id: state for state in snapshot.states}
    sessions = {session.charger_id: session for session in snapshot.sessions}
    controllable = frozenset(
        phase
        for charger in snapshot.chargers
        if charger.kind is not ChargerKind.FIXED and states[charger.id].available
        for phase in charger.phases
    )
    fresh = measurements_fresh(
        snapshot.phase_currents, now, settings.max_measurement_age, controllable
    )
    ctx = _Context(
        now=now,
        zone=ZoneInfo(settings.timezone),
        settings=settings,
        prices_ok=prices_fresh(snapshot.prices, now, settings.max_price_age),
        fallback=not fresh,
    )
    items = [
        _classify(c, states[c.id], sessions.get(c.id), ctx) for c in snapshot.chargers
    ]
    for item in items:
        if item.group in (_Group.SMART, _Group.SMART_OFF):
            _set_limits(item, ctx)

    raw = phase_capacity(snapshot, fresh)
    cap = dict(raw)
    plan_cap = {phase: settings.phase_budget for phase in Phase}
    for item in items:
        if item.group is _Group.RESERVED:
            item.allocation = reserved_current(
                item.charger, item.state, settings.voltage
            )
            _subtract(cap, item.charger.phases, item.allocation)
            _subtract(plan_cap, item.charger.phases, item.allocation)
    cap = _clip(cap)
    available = PhaseAmps(cap[Phase.L1], cap[Phase.L2], cap[Phase.L3])

    plan_cap = _clip(plan_cap)
    outcome = optimize(
        _demands(items),
        now,
        quarter_prices(snapshot.prices) if ctx.prices_ok else {},
        plan_cap,
        cap,
        settings.voltage,
        settings.min_block_quarters,
        settings.price_cap,
        settings.timezone,
    )
    for item in items:
        if item.charger.id in outcome.allocations:
            item.allocation = outcome.allocations[item.charger.id]
    this_quarter = floor_to_quarter(now)
    for item in items:
        if item.group is _Group.SMART and item.priority is None:
            item.plan = outcome.plans[item.charger.id]
            in_plan = any(
                q.start.astimezone(UTC) == this_quarter for q in item.plan.quarters
            )
            item.priority = 2 if in_plan else 4
            item.code = ReasonCode.IN_PLAN if in_plan else ReasonCode.PLANNED_LATER
            # A session that can run but got no quarter at all is waiting for
            # capacity, whatever consumed it: the reservations, the smart-charging-off
            # allocations or another session's plan. Calling it "planned for later"
            # would be false, since nothing is planned.
            item.starved = item.can_run and not item.plan.quarters

    smart = [i for i in items if i.group is _Group.SMART]
    active = sorted(
        (i for i in smart if i.priority in (1, 2, 3) and i.can_run), key=_rank_key
    )
    for item in smart:
        if not item.can_run:
            item.final_code = ReasonCode.BELOW_MINIMUM
        elif item.starved:
            item.final_code = ReasonCode.WAITING_FOR_CAPACITY
        elif item.priority == 4:
            item.final_code = ReasonCode.PLANNED_LATER
        elif item.allocation <= 0:
            item.final_code = (
                ReasonCode.WAITING_FOR_HIGHER_PRIORITY
                if _outranked(item, items)
                else ReasonCode.WAITING_FOR_CAPACITY
            )
    for item in items:
        if item.group is _Group.SMART_OFF and not item.can_run:
            item.final_code = ReasonCode.BELOW_MINIMUM
    room = _room(items, cap)

    decisions = tuple(
        ChargerDecision(
            charger_id=item.charger.id,
            allocation=item.allocation,
            priority=item.priority,
            reason=_reason(item, ctx, raw, room),
            plan=item.plan.quarters if item.plan else (),
            remaining_need_kwh=item.need,
            deadline=(item.deadline.astimezone(ctx.zone) if item.deadline else None),
            expected_finish=_expected_finish(item, ctx),
            reaches_target=_reaches(item, ctx),
        )
        for item in items
    )
    return Decision(
        actions=_actions(items),
        chargers=decisions,
        available=available,
        status=_status(ctx, items, active, raw, room),
        events=_events(items, ctx, outcome),
    )


def _reaches(item: _Item, ctx: _Context) -> bool | None:
    """Say whether the need is met by the deadline; None without a session."""
    if item.session is None or item.deadline is None:
        return None
    finish = _expected_finish(item, ctx)
    now = ctx.now.astimezone(UTC)
    return (
        finish is not None
        and item.deadline > now
        and finish.astimezone(UTC) <= item.deadline
    )


def main() -> None:
    """Showcase this module's functionality."""
    now = datetime(2026, 10, 21, 20, 0, tzinfo=UTC)
    settings = Settings(
        phase_budget=25.0,
        phase_threshold=32.0,
        fallback_current=6,
        timezone="Europe/Copenhagen",
    )
    chargers = (
        Charger("carport", frozenset(Phase), ChargerKind.ADJUSTABLE, 6, 16),
        Charger("garage", frozenset({Phase.L1}), ChargerKind.ADJUSTABLE, 6, 16),
        Charger("wallbox", frozenset({Phase.L2}), ChargerKind.FIXED, None, None, 8.0),
    )
    states = (
        ChargerState("carport", True, 0, True),
        ChargerState("garage", True, 0, True),
        ChargerState("wallbox", True, 8, False),
    )
    sessions = (
        Session("carport", time(7, 0), energy_need_kwh=15.0),
        Session(
            "garage",
            time(7, 0),
            soc_percent=50,
            target_soc_percent=80,
            battery_capacity_kwh=40.0,
            charge_now=True,
        ),
    )
    hourly_prices = [0.10, 0.11, 0.12, 0.25, 0.30, 0.35, 0.40, 0.28]
    intervals = tuple(
        PriceInterval(now + timedelta(hours=h), now + timedelta(hours=h + 1), price)
        for h, price in enumerate(hourly_prices * 3)
    )
    prices = PriceSeries(intervals, updated_at=now)
    currents = PhaseCurrents(now, l1=12.0, l2=10.0, l3=4.0)
    snapshot = Snapshot(
        now, settings, chargers, states, sessions, prices, phase_currents=currents
    )

    decision = decide(snapshot)

    print(f"status: {decision.status}")
    for charger in decision.chargers:
        print(f"{charger.charger_id}: {charger.allocation} A - {charger.reason.text}")
    for action in decision.actions:
        print(f"action: {action.kind} {action.charger_id} {action.target_current} A")

    # Without phase measurements every charger is capped at the fallback current.
    stale = replace(snapshot, phase_currents=None)

    decision = decide(stale)

    print(f"without measurements, status: {decision.status}")
    for charger in decision.chargers:
        print(f"{charger.charger_id}: {charger.allocation} A - {charger.reason.text}")


if __name__ == "__main__":
    main()
