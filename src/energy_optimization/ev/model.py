"""Input types for the EV charging decision core, validated on construction.

Every type is a frozen dataclass that checks itself in `__post_init__` and raises
`ConfigError` naming the offending field, so an invalid snapshot cannot exist and
the decision code never has to re-check its input.
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class Phase(StrEnum):
    """A phase of the grid connection."""

    L1 = "L1"
    L2 = "L2"
    L3 = "L3"


class ChargerKind(StrEnum):
    """What a charger can do."""

    ADJUSTABLE = "adjustable"
    ON_OFF = "on_off"
    FIXED = "fixed"


class ConfigError(ValueError):
    """Invalid input, naming the offending field.

    Attributes:
        field: The name of the offending field, dotted when nested.
    """

    def __init__(self, field: str, message: str) -> None:
        """Create the error.

        Args:
            field: The name of the offending field.
            message: What is wrong with it, naming the value.
        """
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message


def _fail(field: str, message: str, ctx: str = "") -> ConfigError:
    """Build a `ConfigError`, prefixing the message with its context."""
    return ConfigError(field, f"{ctx}{message}")


def _number(field: str, value: object, ctx: str = "") -> float:
    """Return `value` as a finite float; reject bools, non-numbers, NaN and inf."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _fail(field, f"must be a number, got {value!r}", ctx)
    try:
        result = float(value)
    except OverflowError:
        raise _fail(
            field, "must be finite, got an integer too large for a float", ctx
        ) from None
    if not math.isfinite(result):
        raise _fail(field, f"must be finite, got {value!r}", ctx)
    return result


def _whole(field: str, value: object, ctx: str = "") -> int:
    """Return `value` as an int; accept an integral float, reject everything else."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _fail(field, f"must be a whole number, got {value!r}", ctx)
    if isinstance(value, float) and (not math.isfinite(value) or value != int(value)):
        raise _fail(field, f"must be a whole number, got {value!r}", ctx)
    return int(value)


def _aware(field: str, value: object, ctx: str = "") -> datetime:
    """Return `value` if it is a timezone-aware datetime, else raise."""
    if not isinstance(value, datetime):
        raise _fail(field, f"must be a datetime, got {value!r}", ctx)
    if value.tzinfo is None or value.utcoffset() is None:
        raise _fail(
            field, f"must be timezone-aware, got naive {value.isoformat()}", ctx
        )
    return value


def _bool(field: str, value: object, ctx: str = "") -> bool:
    """Return `value` if it is a bool, else raise."""
    if not isinstance(value, bool):
        raise _fail(field, f"must be a bool, got {value!r}", ctx)
    return value


def _text(field: str, value: object, ctx: str = "") -> str:
    """Return `value` if it is a non-empty string after stripping, else raise."""
    if not isinstance(value, str) or not value.strip():
        raise _fail(field, f"must be a non-empty string, got {value!r}", ctx)
    return value


def _set(obj: object, field: str, value: object) -> None:
    """Set a field on a frozen dataclass."""
    object.__setattr__(obj, field, value)


@dataclass(frozen=True)
class Charger:
    """A charge point.

    Adjustable: `min_current` and `max_current` required, `0 < min <= max`,
    `assumed_current` must be None. On/off: `assumed_current > 0` required (its draw
    when on), min and max must be None. Fixed: `assumed_current > 0` required (the
    level it is stuck at), min, max and fallback must be None.

    Attributes:
        id: Non-empty identifier.
        phases: The phases it is wired to; it is counted on each. Non-empty,
            every member a `Phase`, none listed twice.
        kind: What it can do.
        min_current: Lowest non-zero current in A (adjustable).
        max_current: Highest current in A (adjustable).
        assumed_current: Current in A when on (on/off) or stuck at (fixed).
        fallback_current: Cap in A under doubt, overriding the global one.
    """

    id: str
    phases: frozenset[Phase]
    kind: ChargerKind
    min_current: int | None = None
    max_current: int | None = None
    assumed_current: float | None = None
    fallback_current: int | None = None

    def __post_init__(self) -> None:
        """Validate the charger."""
        _text("id", self.id)
        ctx = f"charger {self.id!r}: "
        if isinstance(self.phases, str) or not isinstance(self.phases, Iterable):
            raise _fail("phases", f"must be a set of phases, got {self.phases!r}", ctx)
        listed = list(self.phases)
        if not listed:
            raise _fail("phases", "must not be empty", ctx)
        for phase in listed:
            if not isinstance(phase, Phase):
                raise _fail("phases", f"unknown phase {phase!r}", ctx)
        phases = frozenset(listed)
        if len(phases) != len(listed):
            duplicate = next(p for p in listed if listed.count(p) > 1)
            raise _fail("phases", f"duplicate phase {duplicate.value!r}", ctx)
        _set(self, "phases", phases)
        if not isinstance(self.kind, ChargerKind):
            raise _fail("kind", f"unknown kind {self.kind!r}", ctx)
        if self.fallback_current is not None:
            fallback = _whole("fallback_current", self.fallback_current, ctx)
            if fallback < 0:
                raise _fail("fallback_current", f"must be ≥ 0, got {fallback}", ctx)
            _set(self, "fallback_current", fallback)
        if self.kind is ChargerKind.ADJUSTABLE:
            self._check_adjustable(ctx)
        else:
            self._check_single_level(ctx)

    def _check_adjustable(self, ctx: str) -> None:
        """Validate the fields of an adjustable charger."""
        if self.min_current is None:
            raise _fail("min_current", "required for an adjustable charger", ctx)
        if self.max_current is None:
            raise _fail("max_current", "required for an adjustable charger", ctx)
        if self.assumed_current is not None:
            raise _fail("assumed_current", "not allowed for an adjustable charger", ctx)
        low = _whole("min_current", self.min_current, ctx)
        high = _whole("max_current", self.max_current, ctx)
        if low <= 0:
            raise _fail("min_current", f"must be > 0, got {low}", ctx)
        if high <= 0:
            raise _fail("max_current", f"must be > 0, got {high}", ctx)
        if high < low:
            raise _fail(
                "max_current", f"must be ≥ min_current ({low}), got {high}", ctx
            )
        _set(self, "min_current", low)
        _set(self, "max_current", high)

    def _check_single_level(self, ctx: str) -> None:
        """Validate the fields of an on/off or fixed charger."""
        if self.min_current is not None:
            raise _fail("min_current", f"not allowed for a {self.kind} charger", ctx)
        if self.max_current is not None:
            raise _fail("max_current", f"not allowed for a {self.kind} charger", ctx)
        if self.assumed_current is None:
            raise _fail("assumed_current", f"required for a {self.kind} charger", ctx)
        assumed = _number("assumed_current", self.assumed_current, ctx)
        if assumed <= 0:
            raise _fail("assumed_current", f"must be > 0, got {assumed}", ctx)
        if self.kind is ChargerKind.FIXED and self.fallback_current is not None:
            raise _fail("fallback_current", "not allowed for a fixed charger", ctx)
        _set(self, "assumed_current", assumed)


@dataclass(frozen=True)
class ChargerState:
    """A charger's state now.

    Attributes:
        charger_id: The charger it describes.
        car_connected: Whether a car is plugged in.
        current_setting: Current setting in A; for a paused charger the value it
            resumes at.
        paused: Whether the charger is paused.
        measured_power_w: Measured power in W, None when missing.
        available: Whether the charger answers; False means it is reserved only.
    """

    charger_id: str
    car_connected: bool
    current_setting: float
    paused: bool
    measured_power_w: float | None = None
    available: bool = True

    def __post_init__(self) -> None:
        """Validate the state."""
        _text("charger_id", self.charger_id)
        ctx = f"charger {self.charger_id!r}: "
        _bool("car_connected", self.car_connected, ctx)
        _bool("paused", self.paused, ctx)
        _bool("available", self.available, ctx)
        setting = _number("current_setting", self.current_setting, ctx)
        if setting < 0:
            raise _fail("current_setting", f"must be ≥ 0, got {setting}", ctx)
        _set(self, "current_setting", setting)
        if self.measured_power_w is not None:
            power = _number("measured_power_w", self.measured_power_w, ctx)
            if power < 0:
                raise _fail("measured_power_w", f"must be ≥ 0, got {power}", ctx)
            _set(self, "measured_power_w", power)


@dataclass(frozen=True)
class Session:
    """The car on one charger.

    Attributes:
        charger_id: The charger the car is on.
        ready_time: Time of day the car should be ready; naive, local to
            `Settings.timezone`.
        energy_need_kwh: Energy still wanted in kWh, if given directly.
        soc_percent: State of charge, None when missing.
        target_soc_percent: State of charge wanted.
        battery_capacity_kwh: Battery capacity in kWh.
        delivered_kwh: Energy delivered in this session.
        charge_now: Charge immediately, whatever the price.
        smart_charging: Whether prices are planned for; False charges unmanaged.
        user_current_limit: The user's own limit in A.
        plugged_in_at: When the session began, aware.
    """

    charger_id: str
    ready_time: time
    energy_need_kwh: float | None = None
    soc_percent: float | None = None
    target_soc_percent: float | None = None
    battery_capacity_kwh: float | None = None
    delivered_kwh: float = 0.0
    charge_now: bool = False
    smart_charging: bool = True
    user_current_limit: int | None = None
    plugged_in_at: datetime | None = None

    def __post_init__(self) -> None:
        """Validate the session."""
        _text("charger_id", self.charger_id)
        ctx = f"charger {self.charger_id!r}: "
        if not isinstance(self.ready_time, time):
            raise _fail("ready_time", f"must be a time, got {self.ready_time!r}", ctx)
        if self.ready_time.tzinfo is not None:
            raise _fail(
                "ready_time",
                f"must be naive (local time), got {self.ready_time.isoformat()}",
                ctx,
            )
        _bool("charge_now", self.charge_now, ctx)
        _bool("smart_charging", self.smart_charging, ctx)
        self._non_negative("energy_need_kwh", ctx)
        self._non_negative("delivered_kwh", ctx)
        for name in ("soc_percent", "target_soc_percent"):
            value = getattr(self, name)
            if value is not None:
                number = _number(name, value, ctx)
                if not 0 <= number <= 100:
                    raise _fail(name, f"must be within 0..100, got {number}", ctx)
                _set(self, name, number)
        if self.target_soc_percent is not None and self.target_soc_percent <= 0:
            raise _fail(
                "target_soc_percent", f"must be > 0, got {self.target_soc_percent}", ctx
            )
        if self.battery_capacity_kwh is not None:
            capacity = _number("battery_capacity_kwh", self.battery_capacity_kwh, ctx)
            if capacity <= 0:
                raise _fail("battery_capacity_kwh", f"must be > 0, got {capacity}", ctx)
            _set(self, "battery_capacity_kwh", capacity)
        if self.user_current_limit is not None:
            limit = _whole("user_current_limit", self.user_current_limit, ctx)
            if limit < 0:
                raise _fail("user_current_limit", f"must be ≥ 0, got {limit}", ctx)
            _set(self, "user_current_limit", limit)
        if self.plugged_in_at is not None:
            _aware("plugged_in_at", self.plugged_in_at, ctx)

    def _non_negative(self, name: str, ctx: str) -> None:
        """Validate an optional number that must be ≥ 0, and store it as a float."""
        value = getattr(self, name)
        if value is None:
            return
        number = _number(name, value, ctx)
        if number < 0:
            raise _fail(name, f"must be ≥ 0, got {number}", ctx)
        _set(self, name, number)


@dataclass(frozen=True)
class PriceInterval:
    """One priced interval.

    Attributes:
        start: Start, aware, on a quarter boundary in UTC.
        end: End, aware, on a quarter boundary in UTC, after `start`.
        price: Full consumer price per kWh; finite, may be negative.
    """

    start: datetime
    end: datetime
    price: float

    def __post_init__(self) -> None:
        """Validate the interval."""
        start = _aware("start", self.start)
        end = _aware("end", self.end)
        if end.astimezone(UTC) <= start.astimezone(UTC):
            raise _fail(
                "end", f"must be after start {start.isoformat()}, got {end.isoformat()}"
            )
        for name, moment in (("start", start), ("end", end)):
            utc = moment.astimezone(UTC)
            if utc.minute % 15 or utc.second or utc.microsecond:
                raise _fail(
                    name,
                    f"must be on a quarter-hour boundary, got {moment.isoformat()}",
                )
        _set(self, "price", _number("price", self.price))


@dataclass(frozen=True)
class PriceSeries:
    """The price series.

    Attributes:
        intervals: Priced intervals, in any order but not overlapping.
        updated_at: When the series was last updated, aware.
    """

    intervals: tuple[PriceInterval, ...]
    updated_at: datetime

    def __post_init__(self) -> None:
        """Validate the series."""
        if isinstance(self.intervals, str) or not isinstance(self.intervals, Iterable):
            raise _fail("intervals", f"must be a sequence, got {self.intervals!r}")
        intervals = tuple(self.intervals)
        for interval in intervals:
            if not isinstance(interval, PriceInterval):
                raise _fail("intervals", f"must hold PriceInterval, got {interval!r}")
        _set(self, "intervals", intervals)
        _aware("updated_at", self.updated_at)
        ordered = sorted(intervals, key=lambda item: item.start.astimezone(UTC))
        for earlier, later in zip(ordered, ordered[1:], strict=False):
            if later.start.astimezone(UTC) < earlier.end.astimezone(UTC):
                raise _fail(
                    "intervals",
                    f"overlap: {earlier.start.isoformat()}..{earlier.end.isoformat()} "
                    f"and {later.start.isoformat()}..{later.end.isoformat()}",
                )


@dataclass(frozen=True)
class PhaseCurrents:
    """Measured current per phase for the whole connection.

    Attributes:
        measured_at: When it was measured, aware.
        l1: Current on L1 in A, None when missing.
        l2: Current on L2 in A, None when missing.
        l3: Current on L3 in A, None when missing.
    """

    measured_at: datetime
    l1: float | None
    l2: float | None
    l3: float | None

    def __post_init__(self) -> None:
        """Validate the measurement."""
        _aware("measured_at", self.measured_at)
        for name in ("l1", "l2", "l3"):
            value = getattr(self, name)
            if value is None:
                continue
            number = _number(name, value)
            if number < 0:
                raise _fail(name, f"must be ≥ 0, got {number}")
            _set(self, name, number)

    def get(self, phase: Phase) -> float | None:
        """Return the measured current on one phase.

        Args:
            phase: The phase to read.

        Returns:
            The current in A, or None when it is missing.
        """
        return {Phase.L1: self.l1, Phase.L2: self.l2, Phase.L3: self.l3}[phase]


@dataclass(frozen=True)
class Settings:
    """Installation settings.

    Attributes:
        phase_budget: Per-phase budget for the chargers in A; required.
        phase_threshold: Per-phase phase-guard threshold in A; required.
        fallback_current: Global cap in A under doubt; required.
        timezone: IANA name of the installation's timezone; required.
        voltage: Phase voltage in V.
        min_block_quarters: Minimum contiguous planned quarters.
        price_cap: Price above which quarters are avoided, None for off.
        default_energy_need_kwh: Need used when a session gives none.
        max_price_age: How old the price series may be.
        max_measurement_age: How old phase measurements may be.
    """

    phase_budget: float
    phase_threshold: float
    fallback_current: int
    timezone: str
    voltage: float = 230.0
    min_block_quarters: int = 2
    price_cap: float | None = None
    default_energy_need_kwh: float = 30.0
    max_price_age: timedelta = timedelta(minutes=60)
    max_measurement_age: timedelta = timedelta(seconds=60)

    def __post_init__(self) -> None:
        """Validate the settings."""
        for name in ("phase_budget", "phase_threshold", "voltage"):
            value = getattr(self, name)
            if value is None:
                raise _fail(name, "required")
            number = _number(name, value)
            if number <= 0:
                raise _fail(name, f"must be > 0, got {number}")
            _set(self, name, number)
        if self.fallback_current is None:
            raise _fail("fallback_current", "required")
        fallback = _whole("fallback_current", self.fallback_current)
        if fallback < 0:
            raise _fail("fallback_current", f"must be ≥ 0, got {fallback}")
        _set(self, "fallback_current", fallback)
        if self.timezone is None:
            raise _fail("timezone", "required")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError, TypeError, OSError) as error:
            raise _fail(
                "timezone", f"unknown timezone {self.timezone!r} ({error})"
            ) from error
        blocks = _whole("min_block_quarters", self.min_block_quarters)
        if blocks < 1:
            raise _fail("min_block_quarters", f"must be ≥ 1, got {blocks}")
        _set(self, "min_block_quarters", blocks)
        if self.price_cap is not None:
            _set(self, "price_cap", _number("price_cap", self.price_cap))
        need = _number("default_energy_need_kwh", self.default_energy_need_kwh)
        if need < 0:
            raise _fail("default_energy_need_kwh", f"must be ≥ 0, got {need}")
        _set(self, "default_energy_need_kwh", need)
        for name in ("max_price_age", "max_measurement_age"):
            age = getattr(self, name)
            if not isinstance(age, timedelta) or age <= timedelta(0):
                raise _fail(name, f"must be a positive timedelta, got {age!r}")


@dataclass(frozen=True)
class Snapshot:
    """One call's input.

    Attributes:
        now: The time now, aware.
        settings: Installation settings.
        chargers: The chargers, ids unique.
        states: Exactly one state per charger.
        sessions: At most one session per charger.
        prices: The price series.
        phase_currents: Measured phase currents, None when missing.
    """

    now: datetime
    settings: Settings
    chargers: tuple[Charger, ...]
    states: tuple[ChargerState, ...]
    sessions: tuple[Session, ...]
    prices: PriceSeries
    phase_currents: PhaseCurrents | None = None

    def __post_init__(self) -> None:
        """Validate the snapshot and the links between its parts."""
        _aware("now", self.now)
        if not isinstance(self.settings, Settings):
            raise _fail("settings", f"must be Settings, got {self.settings!r}")
        if not isinstance(self.prices, PriceSeries):
            raise _fail("prices", f"must be a PriceSeries, got {self.prices!r}")
        if self.phase_currents is not None and not isinstance(
            self.phase_currents, PhaseCurrents
        ):
            raise _fail(
                "phase_currents",
                f"must be PhaseCurrents or None, got {self.phase_currents!r}",
            )
        chargers = self._items("chargers", Charger)
        states = self._items("states", ChargerState)
        sessions = self._items("sessions", Session)
        ids: set[str] = set()
        for charger in chargers:
            if charger.id in ids:
                raise _fail("chargers", f"duplicate charger id {charger.id!r}")
            ids.add(charger.id)
        self._one_per_charger("states", [s.charger_id for s in states], ids, True)
        self._one_per_charger("sessions", [s.charger_id for s in sessions], ids, False)

    def _items[T](self, name: str, kind: type[T]) -> tuple[T, ...]:
        """Convert a sequence field to a tuple of `kind` and store it."""
        value = getattr(self, name)
        if isinstance(value, str) or not isinstance(value, Iterable):
            raise _fail(name, f"must be a sequence, got {value!r}")
        items = tuple(value)
        for item in items:
            if not isinstance(item, kind):
                raise _fail(name, f"must hold {kind.__name__}, got {item!r}")
        _set(self, name, items)
        return items

    @staticmethod
    def _one_per_charger(
        name: str, charger_ids: list[str], known: set[str], exactly_one: bool
    ) -> None:
        """Check a list of charger ids against the known chargers."""
        seen: set[str] = set()
        for charger_id in charger_ids:
            if charger_id not in known:
                raise _fail(name, f"refers to unknown charger {charger_id!r}")
            if charger_id in seen:
                raise _fail(name, f"more than one entry for charger {charger_id!r}")
            seen.add(charger_id)
        if exactly_one:
            for charger_id in sorted(known - seen):
                raise _fail(name, f"no entry for charger {charger_id!r}")


def main() -> None:
    """Showcase this module's functionality."""
    now = datetime(2026, 10, 21, 18, 0, tzinfo=UTC)
    settings = Settings(
        phase_budget=25.0,
        phase_threshold=32.0,
        fallback_current=6,
        timezone="Europe/Copenhagen",
    )
    chargers = (
        Charger(
            id="carport",
            phases=frozenset({Phase.L1, Phase.L2, Phase.L3}),
            kind=ChargerKind.ADJUSTABLE,  # ADJUSTABLE, ON_OFF, FIXED
            min_current=6,
            max_current=16,
        ),
        Charger(
            id="garage",
            phases=frozenset({Phase.L1}),
            kind=ChargerKind.FIXED,
            assumed_current=10.0,
        ),
    )
    states = (
        ChargerState(
            charger_id="carport", car_connected=True, current_setting=0, paused=True
        ),
        ChargerState(
            charger_id="garage", car_connected=True, current_setting=10, paused=False
        ),
    )
    sessions = (
        Session(charger_id="carport", ready_time=time(7, 0), energy_need_kwh=20.0),
    )
    prices = PriceSeries(intervals=(), updated_at=now)

    snapshot = Snapshot(
        now=now,
        settings=settings,
        chargers=chargers,
        states=states,
        sessions=sessions,
        prices=prices,
    )

    print(f"snapshot has {len(snapshot.chargers)} chargers at {snapshot.now}")
    print(f"first charger: {snapshot.chargers[0]}")

    # An invalid charger is rejected with the field named.
    min_current = 16
    max_current = 6

    try:
        Charger(
            id="broken",
            phases=frozenset({Phase.L1}),
            kind=ChargerKind.ADJUSTABLE,
            min_current=min_current,
            max_current=max_current,
        )
    except ConfigError as error:
        print(f"rejected: {error} (field={error.field})")


if __name__ == "__main__":
    main()
