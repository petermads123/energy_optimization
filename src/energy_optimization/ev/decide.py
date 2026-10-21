"""The EV charging decision core: one snapshot in, one decision out.

`decide` classifies each charger, assigns priority tiers, plans the cheapest quarters,
shares the capacity of the shared grid connection among the chargers of the highest
occupied tier and returns the actions that get there in a safe order, with a
plain-language reason for each charger. Round 2 adds memory: the decision carries a
`Memory` the caller hands back in the next snapshot, and with it the core holds off
flapping chargers, regulates to the measured draw, runs the phase-guard levels over
time, paces the actuators and says when to call next. A call without usable memory is
a conservative start: nothing is increased.
"""

import math
from dataclasses import dataclass, replace
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from .allocation import (
    Claim,
    measurements_fresh,
    phase_capacity,
    reserved_current,
    share,
)
from .guard import GuardState, advance_guard, instant_level
from .model import (
    _INVALID_MEMORY,
    Charger,
    ChargerKind,
    ChargerMemory,
    ChargerState,
    GuardLevel,
    Memory,
    Phase,
    PhaseCurrents,
    PriceInterval,
    PriceSeries,
    Session,
    Settings,
    Snapshot,
)
from .planning import (
    NeedSource,
    PlanRequest,
    SessionPlan,
    energy_per_quarter_kwh,
    plan_sessions,
    remaining_need_kwh,
)
from .quarters import (
    QUARTER,
    floor_to_quarter,
    next_occurrence,
    prices_fresh,
    quarter_prices,
)
from .regulation import Regulation, regulate
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
_TIERS = (1, 2, 3)


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
    start: datetime | None = None
    key: str = ""
    reset: bool = True
    mem: ChargerMemory | None = None
    reg: Regulation | None = None
    held_off: bool = False
    protected: bool = False
    was_selected: bool = False
    displaced: bool = False
    held_by: str | None = None


@dataclass(frozen=True)
class _Context:
    """Per-call values shared by the steps."""

    now: datetime
    zone: ZoneInfo
    settings: Settings
    prices_ok: bool
    fallback: bool
    conservative: bool
    guard: GuardState


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
    charger: Charger,
    state: ChargerState,
    session: Session | None,
    ctx: _Context,
    start: datetime,
) -> _Item:
    """Classify one charger by the first rule that applies.

    `start` is when the session began as far as the core knows: the call that first
    saw the car connected. A `plugged_in_at` given in the session overrides it.
    """
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
        session.ready_time, session.plugged_in_at or start, ctx.settings.timezone
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
    if item.reg is not None and item.reg.regulated_cap is not None:
        eff = min(eff, float(item.reg.regulated_cap))
    item.eff_max = eff
    if charger.kind is ChargerKind.ADJUSTABLE:
        item.can_run = eff >= _low_current(charger)
    else:
        item.can_run = eff >= top


def _claim(item: _Item) -> Claim:
    """Build a sharing claim for a charger that can run."""
    low = _low_current(item.charger)
    if item.charger.kind is ChargerKind.ADJUSTABLE:
        return Claim(item.charger.id, item.charger.phases, low, item.eff_max)
    return Claim(item.charger.id, item.charger.phases, low, low)


def _rank_key(item: _Item) -> tuple[datetime, float, str]:
    """Rank earliest deadline first, then largest remaining need, then id."""
    deadline = item.deadline or datetime.max.replace(tzinfo=UTC)
    return deadline, -(item.need or 0.0), item.charger.id


def _tier_key(item: _Item, ctx: _Context) -> tuple[bool, datetime, bool, float, str]:
    """Rank within a tier with anti-flapping: protected, then effective deadline.

    A charger running within the minimum pause of its start ranks first; a selected
    one has its deadline taken the hysteresis earlier, and wins a tie.
    """
    deadline = item.deadline or datetime.max.replace(tzinfo=UTC)
    if item.was_selected and item.deadline is not None:
        deadline = _earlier(deadline, ctx.settings.hysteresis)
    return (
        not item.protected,
        deadline,
        not item.was_selected,
        -(item.need or 0.0),
        item.charger.id,
    )


def _subtract(cap: dict[Phase, float], phases: frozenset[Phase], amount: float) -> None:
    """Take current off the capacity of every phase in `phases`."""
    for phase in phases:
        cap[phase] -= amount


def _clip(cap: dict[Phase, float]) -> dict[Phase, float]:
    """Return the capacity with negative values raised to 0."""
    return {phase: max(0.0, value) for phase, value in cap.items()}


def _plan_requests(items: list[_Item]) -> list[PlanRequest]:
    """Build one plan request per session still to be planned."""
    requests: list[PlanRequest] = []
    for item in items:
        if item.session is None or item.deadline is None:
            continue
        top = _top_current(item.charger)
        limit = item.session.user_current_limit
        if limit is not None:
            top = min(top, float(limit))
        low = _low_current(item.charger)
        cap = item.reg.regulated_cap if item.reg is not None else None
        if cap is not None and cap >= low:
            top = min(top, float(cap))
        requests.append(
            PlanRequest(
                item.charger.id,
                item.charger.phases,
                _low_current(item.charger),
                top,
                item.need or 0.0,
                item.deadline,
                skip_current=item.held_off,
            )
        )
    return requests


def _held_off(item: _Item, ctx: _Context) -> bool:
    """Whether the minimum pause still keeps a charger stopped for capacity off."""
    prev = item.mem
    return bool(
        prev is not None
        and prev.stopped_for_capacity
        and not prev.running
        and prev.switched_at is not None
        and ctx.now.astimezone(UTC) - prev.switched_at.astimezone(UTC)
        < ctx.settings.min_pause
    )


def _holders(items: list[_Item], ctx: _Context) -> tuple[str, ...]:
    """Name the planned sessions that contest the current quarter, best claim first.

    The planner gives the current quarter to the session with the least slack, so a
    newcomer whose ready time is only a little earlier would displace a charger that
    is already running. That is the flapping the minimum on-time and the selection
    hysteresis prevent, and they hold in this tier as they do in the ranked ones: a
    charger that started less than the minimum pause ago ranks first, then the
    ready times in order with a selected charger's taken the hysteresis earlier. A
    session takes part when it is protected or selected, or when its ready time is
    more than the hysteresis earlier than that of a selected charger it shares a
    phase with, since only such a session can take that charger's place. A charger
    held off by the minimum pause never takes part.

    The planner lets each of them keep the current quarter, if its own plan has it
    there, in this order; every later quarter is planned most urgent first.
    """
    settings = ctx.settings
    candidates = [
        item
        for item in items
        if item.can_run and item.deadline is not None and not item.held_off
    ]
    selected = [item for item in candidates if item.was_selected and not item.protected]

    def challenges(item: _Item) -> bool:
        return any(
            other is not item
            and other.deadline is not None
            and item.deadline is not None
            and bool(other.charger.phases & item.charger.phases)
            and item.deadline < _earlier(other.deadline, settings.hysteresis)
            for other in selected
        )

    holders = [
        item
        for item in candidates
        if item.protected or item.was_selected or challenges(item)
    ]
    holders.sort(key=lambda item: _tier_key(item, ctx))
    return tuple(item.charger.id for item in holders)


def _plans_now(
    item: _Item,
    ctx: _Context,
    prices: dict[datetime, float],
    plan_cap: dict[Phase, float],
) -> bool:
    """Whether a session, planned alone on the shared capacity, charges in this quarter."""
    settings = ctx.settings
    request = replace(_plan_requests([item])[0], skip_current=False)
    alone = plan_sessions(
        [request],
        ctx.now,
        prices,
        plan_cap,
        settings.voltage,
        settings.min_block_quarters,
        settings.price_cap,
        settings.timezone,
    )[item.charger.id]
    this_quarter = floor_to_quarter(ctx.now)
    return any(q.start.astimezone(UTC) == this_quarter for q in alone.quarters)


def _mark_displaced(
    items: list[_Item],
    ctx: _Context,
    prices: dict[datetime, float],
    plan_cap: dict[Phase, float],
) -> None:
    """Mark the running chargers the plan dropped from this quarter for lack of capacity.

    A running charger whose plan puts it later is stopped either because it is
    cheaper later or because another session claimed this quarter. Only the second is
    a capacity stop, which the minimum pause then holds off: it is the case when the
    charger, planned alone on the capacity the others share, would have had this
    quarter. What smart-charging-off chargers and reservations take is already out of
    that capacity, so it is not counted as another session's claim.
    """
    for item in items:
        if (
            item.priority != 4
            or not item.can_run
            or item.held_off
            or _commanded(item, ctx) <= _EPS
        ):
            continue
        item.displaced = _plans_now(item, ctx, prices, plan_cap)


def _present(item: _Item) -> float:
    """Return the current a charger is at now, 0 when paused."""
    if item.state.paused:
        return 0.0
    if item.charger.kind is ChargerKind.ADJUSTABLE:
        return item.state.current_setting
    return float(item.charger.assumed_current or 0.0)


def _shifted(moment: datetime, span: timedelta = timedelta(0)) -> datetime | None:
    """Return `moment + span` in UTC, None when that leaves the datetime range."""
    try:
        return moment.astimezone(UTC) + span
    except OverflowError:
        return None


def _earlier(moment: datetime, span: timedelta) -> datetime:
    """Return `moment - span`, the earliest representable time if that underflows."""
    try:
        return moment - span
    except OverflowError:
        return datetime.min.replace(tzinfo=UTC)


def _window_open(item: _Item, ctx: _Context) -> bool:
    """Say whether a command was sent to the charger within the action interval."""
    mem = item.mem
    if mem is None or mem.last_target is None or mem.last_action_at is None:
        return False
    age = ctx.now.astimezone(UTC) - mem.last_action_at.astimezone(UTC)
    return timedelta(0) <= age < ctx.settings.min_action_interval


def _commanded(item: _Item, ctx: _Context) -> float:
    """Return the current a charger is at, or on its way to.

    A command sent within the action interval may not have landed, so the state
    still shows the old value; the charger is taken to be at the command, which is
    what it will draw. Lowering and pacing are judged against this, so an increase
    still in flight is countermanded by an emergency or a smaller share instead of
    landing unchecked.
    """
    mem = item.mem
    if mem is not None and mem.last_target is not None and _window_open(item, ctx):
        return mem.last_target
    return _present(item)


def _in_flight(item: _Item, target: float, ctx: _Context) -> bool:
    """Say whether the same command was sent a moment ago and may not have landed."""
    mem = item.mem
    if mem is None or mem.last_target is None or not _window_open(item, ctx):
        return False
    return abs(mem.last_target - target) <= _EPS


def _actions(items: list[_Item], ctx: _Context) -> tuple[Action, ...]:
    """Build the ordered actions: every decrease and pause before every increase.

    A command identical to one still in flight is left out.
    """
    down: list[Action] = []
    up: list[Action] = []
    for item in items:
        if not item.acts:
            continue
        target = item.allocation
        present = _commanded(item, ctx)
        charger_id = item.charger.id
        if _in_flight(item, target, ctx):
            continue
        if target <= 0:
            if present > 0:
                down.append(Action(charger_id, ActionKind.PAUSE, 0.0, Stage.DOWN))
        elif item.state.paused and not _window_open(item, ctx):
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
                return (quarter.start + QUARTER).astimezone(ctx.zone)
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


def _capped_by(item: _Item, ctx: _Context, raw: dict[Phase, float]) -> str | None:
    """Say what holds an allocated charger below its own maximum, if anything."""
    top = _top_current(item.charger)
    if item.group is _Group.RESERVED or item.allocation <= 0:
        return None
    if item.allocation >= top - _EPS:
        return None
    if item.allocation >= item.eff_max - _EPS:
        limit = item.session.user_current_limit if item.session else None
        cap = item.reg.regulated_cap if item.reg is not None else None
        if cap is not None and abs(item.eff_max - cap) <= _EPS:
            return "regulation"
        if ctx.fallback and (limit is None or limit >= item.eff_max):
            return "fallback"
        return "user_limit"
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
        ReasonCode.MINIMUM_PAUSE: (
            "stopped for capacity a moment ago, held off by the minimum pause"
        ),
        ReasonCode.UNDER_USE: (
            "stopped because the car draws far less than its setting, "
            "waiting to probe again"
        ),
        ReasonCode.EMERGENCY: "paused because a phase is over the emergency threshold",
    }
    body = texts[code]
    text = prefix + body if prefix else body[0].upper() + body[1:]
    if item.group is _Group.SMART and item.source is NeedSource.DEFAULT:
        text += f" (assumed need {ctx.settings.default_energy_need_kwh:g} kWh)"
    if item.held_by is not None:
        text += f" (held by {item.held_by.replace('_', ' ')})"
    return text


def _reason(item: _Item, ctx: _Context, raw: dict[Phase, float]) -> Reason:
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
    if item.reg is not None and item.reg.regulated_cap is not None:
        params["regulated_cap"] = item.reg.regulated_cap
    if (
        code is ReasonCode.MINIMUM_PAUSE
        and item.mem is not None
        and item.mem.switched_at is not None
    ):
        until = _shifted(item.mem.switched_at, ctx.settings.min_pause)
        if until is not None:
            params["until"] = until.astimezone(ctx.zone).isoformat()
    if item.held_by is not None:
        params["held_by"] = item.held_by
    else:
        capped = _capped_by(item, ctx, raw)
        if capped is not None:
            params["capped_by"] = capped
    return Reason(code, _sentence(item, code, ctx), params)


def _events(
    items: list[_Item], ctx: _Context, reset_reason: str | None
) -> tuple[Event, ...]:
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
    if reset_reason is not None:
        events.append(
            Event(
                EventKind.MEMORY_RESET,
                f"No usable memory ({reset_reason}): starting conservatively, "
                "nothing is increased this call",
                None,
                {"reason": reset_reason},
            )
        )
    if ctx.guard.intervened and ctx.guard.level is not None:
        events.append(
            Event(
                EventKind.PHASE_GUARD_INTERVENED,
                f"The phase guard is at {ctx.guard.level.value}",
                None,
                {"level": ctx.guard.level.value},
            )
        )
    return tuple(events)


def _status(
    ctx: _Context,
    items: list[_Item],
    active: list[_Item],
    raw: dict[Phase, float],
) -> Status:
    """Pick the overall status: the first that applies."""
    if ctx.guard.level is GuardLevel.EMERGENCY:
        return Status.EMERGENCY
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
    short = [item for item in served if item.allocation < item.eff_max - _EPS]
    if not short:
        return Status.NORMAL
    budget = ctx.settings.phase_budget
    for item in short:
        if any(raw[phase] < budget - _EPS for phase in item.charger.phases):
            return Status.LIMITED_BY_PHASE
    return Status.LIMITED_BY_BUDGET


def _timestamps(memory: Memory, known: set[str]) -> list[datetime]:
    """Collect every timestamp in a memory that concerns a charger in the snapshot."""
    found = [memory.green_since, memory.emergency_at]
    for entry in memory.chargers:
        if entry.charger_id in known:
            found += [
                entry.session_start,
                entry.switched_at,
                entry.under_use_since,
                entry.full_use_since,
                entry.under_use_stop_at,
                entry.last_action_at,
                entry.decreased_at,
            ]
    return [moment for moment in found if moment is not None]


def _usable_memory(snapshot: Snapshot) -> tuple[Memory | None, str | None]:
    """Return the memory if it can be used, else None and why not.

    The reason is one of "missing", "invalid", "future" and "out_of_date".
    """
    memory = snapshot.memory
    if memory is None:
        return None, "missing"
    if memory == _INVALID_MEMORY:
        return None, "invalid"
    now = snapshot.now.astimezone(UTC)
    known = {charger.id for charger in snapshot.chargers}
    try:
        saved = memory.saved_at.astimezone(UTC)
        future = saved > now or any(
            moment.astimezone(UTC) > saved for moment in _timestamps(memory, known)
        )
    except OverflowError:
        return None, "invalid"  # a time no UTC date can hold is not a time
    if future:
        return None, "future"
    if now - saved > snapshot.settings.max_memory_age:
        return None, "out_of_date"
    return memory, None


def _session_key(session: Session | None) -> str:
    """Fingerprint the session fields whose change means a new session."""
    if session is None:
        return ""
    plugged = ""
    if session.plugged_in_at is not None:
        # The same instant written with another offset is the same plug-in.
        moment = _shifted(session.plugged_in_at) or session.plugged_in_at
        plugged = moment.isoformat()
    return "|".join(
        (
            session.ready_time.isoformat(),
            repr(session.energy_need_kwh),
            repr(session.target_soc_percent),
            str(session.charge_now),
            repr(session.user_current_limit),
            plugged,
        )
    )


def _held(item: _Item, present: float) -> float:
    """Return the level a controllable charger can be held at: 0 or a legal current."""
    if item.charger.kind is ChargerKind.ADJUSTABLE:
        whole = float(math.floor(present + _EPS))
        return whole if whole >= _low_current(item.charger) else 0.0
    return present


def _lower(items: list[_Item], ctx: _Context) -> None:
    """Lower or hold the shares: emergency, no increases, pacing. Never raises one."""
    for item in items:
        if item.group not in (_Group.SMART, _Group.SMART_OFF, _Group.IDLE):
            continue
        if not item.acts:
            continue
        share_ = item.allocation
        present = _commanded(item, ctx)
        if ctx.guard.level is GuardLevel.EMERGENCY:
            if share_ > 0 or present > 0:
                item.final_code = ReasonCode.EMERGENCY
            item.allocation = 0.0
            continue
        if share_ <= 0:
            continue
        target = share_
        held_by: str | None = None
        if ctx.conservative or not ctx.guard.increases_allowed:
            target = min(target, _held(item, present))
            held_by = "memory_reset" if ctx.conservative else "phase_guard"
        if target > present + _EPS and _window_open(item, ctx):
            target = _held(item, present)
            held_by = "pacing"
        if target < share_ - _EPS:
            item.allocation = target
            item.held_by = held_by


def _new_memory(
    items: list[_Item],
    ctx: _Context,
    active: list[_Item],
    actions: tuple[Action, ...],
) -> Memory:
    """Build the memory the caller hands back in the next snapshot."""
    by_charger = {action.charger_id: action for action in actions}
    entries: list[ChargerMemory] = []
    for item in items:
        charger_id = item.charger.id
        previous = item.mem or ChargerMemory(charger_id)
        if not item.state.car_connected:
            # An unplug keeps what the minimum pause needs: a re-plug is no reason to
            # skip it.
            entries.append(
                ChargerMemory(
                    charger_id,
                    switched_at=previous.switched_at,
                    stopped_for_capacity=previous.stopped_for_capacity
                    and not previous.running,
                )
            )
            continue
        fields: dict[str, object] = {
            "session_start": item.start,
            "car_connected": True,
            "session_key": item.key,
        }
        if item.reset:
            fields.update(
                regulated_cap=None,
                under_use_since=None,
                full_use_since=None,
                under_use_stop_at=None,
            )
        if item.reg is not None:
            fields.update(
                regulated_cap=item.reg.regulated_cap,
                under_use_since=item.reg.under_use_since,
                full_use_since=item.reg.full_use_since,
                under_use_stop_at=item.reg.under_use_stop_at,
            )
        else:
            fields.update(under_use_since=None, full_use_since=None)
        if item.acts:
            running = item.allocation > _EPS
            fields["running"] = running
            if running != (_commanded(item, ctx) > _EPS):
                fields["switched_at"] = ctx.now
                fields["stopped_for_capacity"] = (
                    not running
                    and item.can_run
                    and (
                        item.final_code
                        in (ReasonCode.WAITING_FOR_CAPACITY, ReasonCode.MINIMUM_PAUSE)
                        or item.displaced
                    )
                )
            fields["selected"] = running and item in active
        action = by_charger.get(charger_id)
        if action is not None:
            fields["last_target"] = action.target_current
            fields["last_action_at"] = ctx.now
            if action.stage is Stage.DOWN:
                fields["decreased_at"] = ctx.now
        entries.append(replace(previous, **fields))  # type: ignore[arg-type]  # keys are ChargerMemory fields
    return Memory(
        saved_at=ctx.now,
        chargers=tuple(entries),
        level=ctx.guard.level,
        green_since=ctx.guard.green_since,
        emergency_at=ctx.guard.emergency_at,
    )


def _next_check(
    items: list[_Item], ctx: _Context, memory: Memory, prices: PriceSeries
) -> datetime | None:
    """Find the earliest moment a new call could change the decision.

    Everything is added in UTC. When anything is pending the answer is never later
    than the moment the new memory goes out of date, so a caller that wakes only at
    this time still finds its memory usable.
    """
    settings = ctx.settings
    now = ctx.now.astimezone(UTC)
    found: list[datetime | None] = []
    if any(item.plan is not None and item.plan.quarters for item in items):
        found.append(_shifted(floor_to_quarter(ctx.now) + QUARTER))
    guard = ctx.guard
    if guard.green_since is not None:
        if guard.level is GuardLevel.EMERGENCY:
            found.append(_shifted(guard.green_since, settings.emergency_calm))
        elif not guard.increases_allowed:
            found.append(_shifted(guard.green_since, settings.green_hold))
    for item in items:
        mem = item.mem
        if (
            mem is not None
            and mem.switched_at is not None
            and (item.held_off or item.protected)
        ):
            found.append(_shifted(mem.switched_at, settings.min_pause))
        if item.state.car_connected and item.deadline is not None:
            found.append(_shifted(item.deadline))
    for entry in memory.chargers:
        if entry.under_use_since is not None:
            found.append(_shifted(entry.under_use_since, settings.under_use_period))
        if (
            entry.full_use_since is not None
            and entry.regulated_cap is not None
            and entry.regulated_cap > 0
        ):
            found.append(_shifted(entry.full_use_since, settings.ramp_period))
        if entry.under_use_stop_at is not None:
            found.append(_shifted(entry.under_use_stop_at, settings.resume_probe))
        if entry.last_action_at is not None:
            found.append(_shifted(entry.last_action_at, settings.min_action_interval))
    if ctx.prices_ok:
        found.append(_shifted(prices.updated_at, settings.max_price_age))
    later = [moment for moment in found if moment is not None and moment > now]
    if not later:
        return None
    expiry = _shifted(ctx.now, settings.max_memory_age)
    if expiry is not None:
        later.append(expiry)
    return min(later).astimezone(ctx.zone)


def decide(snapshot: Snapshot) -> Decision:
    """Decide what every charger should be set to now.

    Args:
        snapshot: One complete, validated snapshot of the installation. Its `memory`
            is the previous decision's; missing, malformed, out-of-date or
            future-dated memory is a conservative start, never an error.

    Returns:
        The ordered actions (every decrease and pause before every increase), a
        decision with reason per charger in snapshot order, the amps still
        available per phase, the overall status, the events, the memory for the
        next call, the phase-guard level and the earliest moment to call again.
    """
    settings = snapshot.settings
    now = snapshot.now
    zone = ZoneInfo(settings.timezone)
    states = {state.charger_id: state for state in snapshot.states}
    sessions = {session.charger_id: session for session in snapshot.sessions}
    memory, reset_reason = _usable_memory(snapshot)
    known = {charger.id for charger in snapshot.chargers}
    entries = (
        {e.charger_id: e for e in memory.chargers if e.charger_id in known}
        if memory
        else {}
    )
    controllable = frozenset(
        phase
        for charger in snapshot.chargers
        if charger.kind is not ChargerKind.FIXED and states[charger.id].available
        for phase in charger.phases
    )
    fresh = measurements_fresh(
        snapshot.phase_currents, now, settings.max_measurement_age, controllable
    )
    instant = (
        instant_level(snapshot.phase_currents, controllable, settings)
        if fresh and snapshot.phase_currents is not None
        else None
    )
    guard = advance_guard(memory, instant, now, settings)
    ctx = _Context(
        now=now,
        zone=zone,
        settings=settings,
        prices_ok=prices_fresh(snapshot.prices, now, settings.max_price_age),
        fallback=not fresh,
        conservative=memory is None,
        guard=guard,
    )
    items: list[_Item] = []
    for charger in snapshot.chargers:
        state = states[charger.id]
        session = sessions.get(charger.id)
        previous = entries.get(charger.id)
        key = _session_key(session)
        replugged = previous is None or not previous.car_connected
        start = now
        if previous is not None and previous.car_connected and previous.session_start:
            start = previous.session_start
        item = _classify(charger, state, session, ctx, start)
        item.start = start
        item.key = key
        item.mem = previous
        item.reset = replugged or previous.session_key != key  # type: ignore[union-attr]  # previous is set when not replugged
        if previous is not None and state.car_connected:
            item.protected = (
                previous.running
                and previous.switched_at is not None
                and now.astimezone(UTC) - previous.switched_at.astimezone(UTC)
                < settings.min_pause
            )
            item.was_selected = previous.selected
        if (
            item.group is _Group.SMART
            and charger.kind is ChargerKind.ADJUSTABLE
            and state.car_connected
        ):
            item.reg = regulate(previous, charger, state, item.reset, now, settings)
        items.append(item)
    for item in items:
        if item.group in (_Group.SMART, _Group.SMART_OFF):
            _set_limits(item, ctx)

    settling = frozenset(
        entry.charger_id
        for entry in entries.values()
        if entry.decreased_at is not None
        and timedelta(0)
        <= now.astimezone(UTC) - entry.decreased_at.astimezone(UTC)
        < settings.min_action_interval
    )
    raw = phase_capacity(snapshot, fresh, settling)
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

    off = sorted(
        (i for i in items if i.group is _Group.SMART_OFF and i.can_run), key=_rank_key
    )
    shares = share([_claim(i) for i in off], cap)
    for item in off:
        item.allocation = shares[item.charger.id]
        _subtract(cap, item.charger.phases, item.allocation)
        _subtract(plan_cap, item.charger.phases, item.allocation)
    cap = _clip(cap)
    plan_cap = _clip(plan_cap)

    to_plan = [
        i
        for i in items
        if i.group is _Group.SMART
        and i.priority is None
        and not (i.reg is not None and i.reg.regulated_cap == 0)
    ]
    if to_plan:
        prices = quarter_prices(snapshot.prices)
        for item in to_plan:
            item.held_off = _held_off(item, ctx)
        plans = plan_sessions(
            _plan_requests(to_plan),
            now,
            prices,
            plan_cap,
            settings.voltage,
            settings.min_block_quarters,
            settings.price_cap,
            settings.timezone,
            _holders(to_plan, ctx),
        )
        this_quarter = floor_to_quarter(now)
        for item in to_plan:
            item.plan = plans[item.charger.id]
            in_plan = any(
                q.start.astimezone(UTC) == this_quarter for q in item.plan.quarters
            )
            # A charger the minimum pause holds off cannot use the quarter, so the
            # plan leaves it out; it still wants it when it would have it alone, and
            # then occupies the tier like the held-off chargers of the ranked ones.
            if item.held_off and item.can_run and not in_plan:
                in_plan = _plans_now(item, ctx, prices, plan_cap)
            item.priority = 2 if in_plan else 4
            item.code = ReasonCode.IN_PLAN if in_plan else ReasonCode.PLANNED_LATER
            # A session that can run but got no quarter at all is waiting for
            # capacity, whatever consumed it: the reservations, the smart-charging-off
            # allocations or another session's plan. Calling it "planned for later"
            # would be false, since nothing is planned.
            item.starved = item.can_run and not item.held_off and not item.plan.quarters
        _mark_displaced(to_plan, ctx, prices, plan_cap)

    smart = [i for i in items if i.group is _Group.SMART]
    tier = next(
        (t for t in _TIERS if any(i.priority == t and i.can_run for i in smart)), None
    )
    active = sorted(
        (i for i in smart if tier is not None and i.priority == tier and i.can_run),
        key=lambda i: _rank_key(i) if tier == 1 else _tier_key(i, ctx),  # type: ignore[arg-type,return-value]  # one key shape per tier
    )
    if tier != 1:
        for item in active:
            item.held_off = _held_off(item, ctx)
    claimants = [i for i in active if not i.held_off]
    shares = share([_claim(i) for i in claimants], cap)
    for item in claimants:
        item.allocation = shares[item.charger.id]
    for item in smart:
        if not item.can_run:
            item.final_code = (
                ReasonCode.UNDER_USE
                if item.reg is not None and item.reg.regulated_cap == 0
                else ReasonCode.BELOW_MINIMUM
            )
        elif item.starved:
            item.final_code = ReasonCode.WAITING_FOR_CAPACITY
        elif item.priority == 4:
            item.final_code = ReasonCode.PLANNED_LATER
        elif item not in active:
            item.final_code = ReasonCode.WAITING_FOR_HIGHER_PRIORITY
        elif item.held_off:
            item.final_code = ReasonCode.MINIMUM_PAUSE
        elif item.allocation <= 0:
            item.final_code = ReasonCode.WAITING_FOR_CAPACITY
    for item in items:
        if item.group is _Group.SMART_OFF and not item.can_run:
            item.final_code = ReasonCode.BELOW_MINIMUM

    _lower(items, ctx)
    actions = _actions(items, ctx)
    new_memory = _new_memory(items, ctx, active, actions)

    decisions = tuple(
        ChargerDecision(
            charger_id=item.charger.id,
            allocation=item.allocation,
            priority=item.priority,
            reason=_reason(item, ctx, raw),
            plan=item.plan.quarters if item.plan else (),
            remaining_need_kwh=item.need,
            deadline=(item.deadline.astimezone(ctx.zone) if item.deadline else None),
            expected_finish=_expected_finish(item, ctx),
            reaches_target=_reaches(item, ctx),
        )
        for item in items
    )
    return Decision(
        actions=actions,
        chargers=decisions,
        available=available,
        status=_status(ctx, items, active, raw),
        events=_events(items, ctx, reset_reason),
        memory=new_memory,
        guard_level=guard.level,
        next_check=_next_check(items, ctx, new_memory, snapshot.prices),
    )


def _reaches(item: _Item, ctx: _Context) -> bool | None:
    """Say whether the need is met by the deadline; None without a session."""
    if item.session is None or item.deadline is None:
        return None
    finish = _expected_finish(item, ctx)
    now = ctx.now.astimezone(UTC)
    return finish is not None and item.deadline > now and finish <= item.deadline


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

    # The first call had no memory, so it was a conservative start: nothing was
    # increased. Feed its memory into the next snapshot and the chargers start.
    follow_up = timedelta(minutes=5)
    later = replace(
        snapshot,
        now=now + follow_up,
        phase_currents=PhaseCurrents(now + follow_up, l1=12.0, l2=10.0, l3=4.0),
        memory=decision.memory,
    )

    follow_up_decision = decide(later)

    print(f"after {follow_up}: status {follow_up_decision.status}")
    for charger in follow_up_decision.chargers:
        print(f"{charger.charger_id}: {charger.allocation} A - {charger.reason.text}")
    print(f"next check: {follow_up_decision.next_check}")

    # Without phase measurements every charger is capped at the fallback current
    # (this snapshot carries no memory, so it is a conservative start as well).
    stale = replace(snapshot, phase_currents=None)

    decision = decide(stale)

    print(f"without measurements, status: {decision.status}")
    for charger in decision.chargers:
        print(f"{charger.charger_id}: {charger.allocation} A - {charger.reason.text}")


if __name__ == "__main__":
    main()
