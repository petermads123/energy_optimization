"""Input types for the interruptible-load scheduler, validated on construction.

Every type is a frozen dataclass that checks itself in `__post_init__` and raises
`ConfigError` naming the offending field, so an invalid snapshot cannot exist and the
scheduler never has to re-check its input. All limits are counted in whole quarters.
"""

from collections.abc import Iterable, Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from energy_optimization.common import (
    ConfigError,
    PriceInterval,
    PriceSeries,
    check_aware,
    check_bool,
    check_number,
    check_whole,
    fail,
    set_field,
)


class LoadState(StrEnum):
    """A quarter's state, in history, plan and decision."""

    ON = "on"
    OFF = "off"


@dataclass(frozen=True)
class Limits:
    """How long and how often a load may be off, in whole quarters.

    Attributes:
        max_off_run: The load is never off for more than this many quarters in a
            row; at least 1.
        window_off_budget: In every window of `window_length` consecutive quarters
            the load is off in at most this many; at least 1 and at most
            `window_length`. Equal to `window_length` means no window limit.
        window_length: The length of the sliding window; at least 1.
        min_on_after_off: Once switched back on, the load stays on at least this
            many quarters; at least 0, and 0 behaves as 1.
        min_off_run: An off period lasts at least this many quarters; at least 1,
            and at most `max_off_run` and `window_off_budget`.
    """

    max_off_run: int
    window_off_budget: int
    window_length: int
    min_on_after_off: int = 0
    min_off_run: int = 1

    def __post_init__(self) -> None:
        """Validate the limits."""
        max_off = check_whole("max_off_run", self.max_off_run)
        budget = check_whole("window_off_budget", self.window_off_budget)
        length = check_whole("window_length", self.window_length)
        min_on = check_whole("min_on_after_off", self.min_on_after_off)
        min_off = check_whole("min_off_run", self.min_off_run)
        for name, value, low in (
            ("max_off_run", max_off, 1),
            ("window_off_budget", budget, 1),
            ("window_length", length, 1),
            ("min_on_after_off", min_on, 0),
            ("min_off_run", min_off, 1),
        ):
            if value < low:
                raise fail(name, f"must be ≥ {low}, got {value}")
        if budget > length:
            raise fail(
                "window_off_budget", f"must be ≤ window_length ({length}), got {budget}"
            )
        if min_off > max_off:
            raise fail(
                "min_off_run", f"must be ≤ max_off_run ({max_off}), got {min_off}"
            )
        if min_off > budget:
            raise fail(
                "min_off_run", f"must be ≤ window_off_budget ({budget}), got {min_off}"
            )
        set_field(self, "max_off_run", max_off)
        set_field(self, "window_off_budget", budget)
        set_field(self, "window_length", length)
        set_field(self, "min_on_after_off", min_on)
        set_field(self, "min_off_run", min_off)

    @property
    def history_needed(self) -> int:
        """How many quarters of history the scheduler looks back.

        Returns:
            `max(window_length - 1, max_off_run, min_on_after_off)`.
        """
        return max(self.window_length - 1, self.max_off_run, self.min_on_after_off)


@dataclass(frozen=True)
class Load:
    """An interruptible load: its limits and how it behaves around an off period.

    Attributes:
        limits: The off-time limits.
        rebound: The share, in [0, 1], of the energy not used while off that is
            used again in the on-quarters right after; 1 is everything coming
            back, 0 a true saving.
        min_saving_percent: An off period is only planned if it saves at least
            this percentage of what its affected quarters would cost always on;
            at least 0.
        power_kw: Steady-state power while running, in kW; used only for the money
            figures. None when not known.
    """

    limits: Limits
    rebound: float
    min_saving_percent: float = 0.0
    power_kw: float | None = None

    def __post_init__(self) -> None:
        """Validate the load."""
        if not isinstance(self.limits, Limits):
            raise fail("limits", f"must be Limits, got {self.limits!r}")
        rebound = check_number("rebound", self.rebound)
        if not 0 <= rebound <= 1:
            raise fail("rebound", f"must be within 0..1, got {rebound}")
        saving = check_number("min_saving_percent", self.min_saving_percent)
        if saving < 0:
            raise fail("min_saving_percent", f"must be ≥ 0, got {saving}")
        set_field(self, "rebound", rebound)
        set_field(self, "min_saving_percent", saving)
        if self.power_kw is not None:
            power = check_number("power_kw", self.power_kw)
            if power <= 0:
                raise fail("power_kw", f"must be > 0, got {power}")
            set_field(self, "power_kw", power)


@dataclass(frozen=True)
class LoadSnapshot:
    """One call's input.

    Attributes:
        now: The time now, aware.
        load: The load to plan.
        prices: The price series; the horizon is its contiguous priced quarters
            from the one holding `now`.
        history: The load's state in the quarters before the current one, oldest
            first, so the last entry is the quarter before the one holding `now`.
        force_on: The caller's override: the load may not be off right now.
    """

    now: datetime
    load: Load
    prices: PriceSeries
    history: tuple[LoadState, ...] = ()
    force_on: bool = False

    def __post_init__(self) -> None:
        """Validate the snapshot."""
        check_aware("now", self.now)
        if not isinstance(self.load, Load):
            raise fail("load", f"must be a Load, got {self.load!r}")
        if not isinstance(self.prices, PriceSeries):
            raise fail("prices", f"must be a PriceSeries, got {self.prices!r}")
        if isinstance(self.history, str | Mapping | AbstractSet) or not isinstance(
            self.history, Iterable
        ):
            raise fail("history", f"must be an ordered sequence, got {self.history!r}")
        history = tuple(self.history)
        for index, entry in enumerate(history):
            if not isinstance(entry, LoadState):
                raise fail(
                    "history", f"entry {index} must be a LoadState, got {entry!r}"
                )
        set_field(self, "history", history)
        check_bool("force_on", self.force_on)


def main() -> None:
    """Showcase this module's functionality."""
    now = datetime(2026, 10, 21, 17, 0, tzinfo=UTC)
    max_off_run = 4  # quarters in a row
    window_off_budget = 8  # off quarters in any window
    window_length = 16  # quarters
    min_on_after_off = 2
    min_off_run = 2
    rebound = 0.8
    price = 1.0
    prices = PriceSeries(
        intervals=(
            PriceInterval(start=now, end=now + timedelta(hours=4), price=price),
        ),
        updated_at=now,
    )

    limits = Limits(
        max_off_run=max_off_run,
        window_off_budget=window_off_budget,
        window_length=window_length,
        min_on_after_off=min_on_after_off,
        min_off_run=min_off_run,
    )
    load = Load(limits=limits, rebound=rebound)
    snapshot = LoadSnapshot(now=now, load=load, prices=prices)

    print(f"look-back: {limits.history_needed} quarters")
    print(f"snapshot: {snapshot.load.limits} at {snapshot.now.isoformat()}")

    # A minimum off block longer than the longest off run cannot be met.
    min_off_run = 6

    try:
        Limits(
            max_off_run=max_off_run,
            window_off_budget=window_off_budget,
            window_length=window_length,
            min_off_run=min_off_run,
        )
    except ConfigError as error:
        print(f"rejected: {error} (field={error.field})")


if __name__ == "__main__":
    main()
