"""Output types of the EV charging decision core.

Plain frozen dataclasses: one `Decision` carries the ordered actions, a decision per
charger, the available amps per phase, the overall status and the events.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from .model import Phase

type Params = dict[str, str | float | int | bool]


class Stage(StrEnum):
    """Which phase of execution an action belongs to."""

    DOWN = "down"
    UP = "up"


class ActionKind(StrEnum):
    """What to do to a charger."""

    SET_CURRENT = "set_current"
    PAUSE = "pause"
    RESUME = "resume"


@dataclass(frozen=True)
class Action:
    """One change with an absolute target.

    Attributes:
        charger_id: The charger to act on.
        kind: What to do.
        target_current: Target in A; 0.0 for a pause, the current to resume at for
            a resume.
        stage: Whether to run it in the down or the up phase.
    """

    charger_id: str
    kind: ActionKind
    target_current: float
    stage: Stage


class ReasonCode(StrEnum):
    """Stable reason codes."""

    CHARGE_NOW = "charge_now"
    IN_PLAN = "in_plan"
    READY_TIME_PASSED = "ready_time_passed"
    PLAN_COMPLETED = "plan_completed"
    PRICES_STALE = "prices_stale"
    PLANNED_LATER = "planned_later"
    WAITING_FOR_HIGHER_PRIORITY = "waiting_for_higher_priority"
    WAITING_FOR_CAPACITY = "waiting_for_capacity"
    TARGET_REACHED = "target_reached"
    BELOW_MINIMUM = "below_minimum"
    SMART_CHARGING_OFF = "smart_charging_off"
    FIXED = "fixed"
    UNAVAILABLE = "unavailable"
    NO_CAR = "no_car"
    NO_SESSION = "no_session"


@dataclass(frozen=True)
class Reason:
    """Why a charger got what it got.

    Attributes:
        code: Stable reason code.
        text: An English sentence.
        params: The values the sentence was built from.
    """

    code: ReasonCode
    text: str
    params: Params = field(default_factory=dict)


@dataclass(frozen=True)
class PlannedQuarter:
    """One planned quarter.

    Attributes:
        start: Start of the quarter, in the settings timezone.
        price: Its price, None when the quarter is unpriced.
        current: The planned current in A.
    """

    start: datetime
    price: float | None
    current: float


@dataclass(frozen=True)
class ChargerDecision:
    """Everything decided for one charger.

    Attributes:
        charger_id: The charger.
        allocation: Current in A; for a fixed or unavailable charger the current
            reserved for it.
        priority: Priority 1 to 4, None when the charger has none.
        reason: Why it got this.
        plan: Upcoming planned quarters.
        remaining_need_kwh: Energy still needed, None without a session.
        deadline: The ready time as a moment, None without a session.
        expected_finish: When the need is expected to be met.
        reaches_target: Whether the need is met by the deadline, None without a
            session.
    """

    charger_id: str
    allocation: float
    priority: int | None
    reason: Reason
    plan: tuple[PlannedQuarter, ...] = ()
    remaining_need_kwh: float | None = None
    deadline: datetime | None = None
    expected_finish: datetime | None = None
    reaches_target: bool | None = None

    def __post_init__(self) -> None:
        """Normalise the plan to a tuple."""
        object.__setattr__(self, "plan", tuple(self.plan))


class Status(StrEnum):
    """Overall status."""

    NORMAL = "normal"
    LIMITED_BY_BUDGET = "limited_by_budget"
    LIMITED_BY_PHASE = "limited_by_phase"
    WAITING_FOR_CAPACITY = "waiting_for_capacity"
    PHASE_DATA_STALE = "phase_data_stale"
    PRICES_STALE = "prices_stale"


class EventKind(StrEnum):
    """Notification-worthy events."""

    TARGET_UNREACHABLE = "target_unreachable"
    PRICE_CAP_EXCEEDED = "price_cap_exceeded"
    CHARGE_NOW_NEED_COVERED = "charge_now_need_covered"
    PHASE_DATA_STALE = "phase_data_stale"
    PRICES_STALE = "prices_stale"


@dataclass(frozen=True)
class Event:
    """One event.

    Attributes:
        kind: What happened.
        text: An English sentence.
        charger_id: The charger concerned, None for a global event.
        params: The values the sentence was built from.
    """

    kind: EventKind
    text: str
    charger_id: str | None = None
    params: Params = field(default_factory=dict)


@dataclass(frozen=True)
class PhaseAmps:
    """Amps per phase.

    Attributes:
        l1: Amps on L1.
        l2: Amps on L2.
        l3: Amps on L3.
    """

    l1: float
    l2: float
    l3: float

    def get(self, phase: Phase) -> float:
        """Return the amps on one phase.

        Args:
            phase: The phase to read.

        Returns:
            The amps on that phase.
        """
        return {Phase.L1: self.l1, Phase.L2: self.l2, Phase.L3: self.l3}[phase]


@dataclass(frozen=True)
class Decision:
    """One call's answer.

    Attributes:
        actions: Down-stage actions first, then up-stage ones.
        chargers: One decision per charger, in snapshot charger order.
        available: Amps still available per phase after reservations.
        status: Overall status.
        events: Notification-worthy events.
    """

    actions: tuple[Action, ...]
    chargers: tuple[ChargerDecision, ...]
    available: PhaseAmps
    status: Status
    events: tuple[Event, ...]

    def __post_init__(self) -> None:
        """Normalise the sequences to tuples."""
        object.__setattr__(self, "actions", tuple(self.actions))
        object.__setattr__(self, "chargers", tuple(self.chargers))
        object.__setattr__(self, "events", tuple(self.events))


def main() -> None:
    """Showcase this module's functionality."""
    pause = Action("garage", ActionKind.PAUSE, 0.0, Stage.DOWN)  # PAUSE, SET_CURRENT
    resume = Action("carport", ActionKind.RESUME, 10.0, Stage.UP)  # RESUME, SET_CURRENT
    reason = Reason(
        code=ReasonCode.PLAN_COMPLETED,  # CHARGE_NOW, IN_PLAN, PLAN_COMPLETED, ...
        text="Priority 3 — plan completed, charging on spare capacity",
        params={"priority": 3, "allocation": 10.0},
    )
    quarter = PlannedQuarter(
        start=datetime(2026, 10, 21, 20, 0, tzinfo=UTC), price=0.42, current=10.0
    )
    carport = ChargerDecision(
        charger_id="carport",
        allocation=10.0,
        priority=3,
        reason=reason,
        plan=(quarter,),
    )
    available = PhaseAmps(l1=5.0, l2=15.0, l3=15.0)
    status = Status.NORMAL  # NORMAL, LIMITED_BY_BUDGET, WAITING_FOR_CAPACITY, ...

    decision = Decision(
        actions=(pause, resume),
        chargers=(carport,),
        available=available,
        status=status,
        events=(),
    )

    for action in decision.actions:
        print(
            f"{action.stage}: {action.kind} {action.charger_id} -> "
            f"{action.target_current} A"
        )
    print(f"reason {decision.chargers[0].reason.code}: {reason.text}")
    print(
        f"status {decision.status}, available on L1: {decision.available.get(Phase.L1)}"
    )


if __name__ == "__main__":
    main()
