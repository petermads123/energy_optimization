"""Output types for the interruptible-load scheduler.

No logic beyond the tuple normalisation of `LoadSchedule.plan`.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from energy_optimization.common import set_field
from energy_optimization.interruptible.model import LoadState


class ReasonCode(StrEnum):
    """Why the current quarter is what it is; values are the lower-case names."""

    NO_PRICES = "no_prices"
    FORCED_ON = "forced_on"
    MAX_OFF_RUN = "max_off_run"
    WINDOW_BUDGET = "window_budget"
    MIN_ON_AFTER_OFF = "min_on_after_off"
    END_OF_HORIZON = "end_of_horizon"
    MIN_OFF_RUN = "min_off_run"
    PLANNED_OFF = "planned_off"
    PLANNED_ON = "planned_on"
    NO_SAVING = "no_saving"


@dataclass(frozen=True)
class Reason:
    """The code for the current quarter and a sentence naming the numbers.

    Attributes:
        code: The stable reason code.
        text: A human-readable sentence.
    """

    code: ReasonCode
    text: str


@dataclass(frozen=True)
class ScheduledQuarter:
    """One planned quarter.

    Attributes:
        start: The quarter's start, in UTC.
        price: The quarter's price per kWh.
        state: Whether the load is on or off in it.
    """

    start: datetime
    price: float
    state: LoadState


@dataclass(frozen=True)
class LoadSchedule:
    """The answer for one load.

    Attributes:
        state: The decision for the current quarter: `plan[0].state`, or `ON`
            when the plan is empty.
        reason: Why.
        plan: On or off for every quarter of the horizon, in time order.
        saving_percent: The plan's saving against always-on in percent, None when
            the always-on cost is 0 (within a rounding tolerance) or there are no
            prices.
        always_on_cost: Expected cost of switching on now and staying on, in the
            price's currency; set only when the power is given.
        planned_cost: Expected cost of the plan; set only when the power is given.
        saving: `always_on_cost - planned_cost`; set only when the power is given.
        history_short: Whether the history was shorter than the look-back and was
            padded with on.
        proven: False when a solve stopped at the node budget, so the plan may not
            be the cheapest.
    """

    state: LoadState
    reason: Reason
    plan: tuple[ScheduledQuarter, ...]
    saving_percent: float | None
    always_on_cost: float | None = None
    planned_cost: float | None = None
    saving: float | None = None
    history_short: bool = False
    proven: bool = True

    def __post_init__(self) -> None:
        """Normalise the plan to a tuple."""
        set_field(self, "plan", tuple(self.plan))


def main() -> None:
    """Showcase this module's functionality."""
    start = datetime(2026, 10, 21, 17, 0, tzinfo=UTC)
    price = 2.0
    state = LoadState.OFF  # ON, OFF
    code = ReasonCode.PLANNED_OFF  # see ReasonCode for the others
    text = "off in the dearest quarter"

    quarter = ScheduledQuarter(start=start, price=price, state=state)
    reason = Reason(code=code, text=text)
    following = ScheduledQuarter(
        start=start + timedelta(minutes=15), price=1.0, state=LoadState.ON
    )
    schedule = LoadSchedule(
        state=state,
        reason=reason,
        plan=[quarter, following],  # type: ignore[arg-type]  # normalised to a tuple
        saving_percent=12.5,
    )

    print(f"now: {schedule.state} ({schedule.reason.code}: {schedule.reason.text})")
    print(f"plan: {[q.state.value for q in schedule.plan]}")
    print(f"saving: {schedule.saving_percent} %, proven={schedule.proven}")


if __name__ == "__main__":
    main()
