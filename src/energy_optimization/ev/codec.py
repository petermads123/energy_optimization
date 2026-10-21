"""Conversion of snapshots and decisions to and from plain JSON-compatible dicts.

Keys are the dataclass field names, enums go by value, datetimes are ISO 8601 with
their offset, a time of day is `time.isoformat()`, a timedelta is a number of
seconds, phase sets are sorted lists and tuples are lists. `None` is `null`.
Decoding rejects missing, unknown and wrongly typed fields with a `ConfigError`
whose field is the dotted path to the offending value, such as
`chargers[1].max_current`.
"""

import json
import math
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum

from .model import (
    Charger,
    ChargerKind,
    ChargerState,
    ConfigError,
    Phase,
    PhaseCurrents,
    PriceInterval,
    PriceSeries,
    Session,
    Settings,
    Snapshot,
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
    PlannedQuarter,
    Reason,
    ReasonCode,
    Stage,
    Status,
)

type _Converter[T] = Callable[[object, str], T]


def _join(path: str, key: str) -> str:
    """Return the dotted path of a key below a path."""
    return f"{path}.{key}" if path else key


def _make[T](path: str, factory: Callable[..., T], **kwargs: object) -> T:
    """Call a constructor, prefixing any `ConfigError` field with the path.

    The keyword arguments are evaluated by the caller, so only errors raised by the
    constructor itself are prefixed; errors from reading the dict already carry
    their full path.
    """
    try:
        return factory(**kwargs)
    except ConfigError as error:
        field = _join(path, error.field)
        raise ConfigError(field, error.message) from error


def _num(value: object, path: str) -> float:
    """Convert a JSON number to a finite float."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigError(path, f"must be a number, got {value!r}")
    if not math.isfinite(value):
        raise ConfigError(path, f"must be finite, got {value!r}")
    return float(value)


def _int(value: object, path: str) -> int:
    """Convert a JSON number to an int; an integral float is accepted."""
    number = _num(value, path)
    if number != int(number):
        raise ConfigError(path, f"must be a whole number, got {value!r}")
    return int(number)


def _bool(value: object, path: str) -> bool:
    """Check a JSON boolean."""
    if not isinstance(value, bool):
        raise ConfigError(path, f"must be true or false, got {value!r}")
    return value


def _str(value: object, path: str) -> str:
    """Check a JSON string."""
    if not isinstance(value, str):
        raise ConfigError(path, f"must be a string, got {value!r}")
    return value


def _datetime(value: object, path: str) -> datetime:
    """Parse an ISO 8601 timestamp with an offset."""
    text = _str(value, path)
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as error:
        raise ConfigError(
            path, f"must be an ISO 8601 timestamp, got {text!r}"
        ) from error
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ConfigError(path, f"must include a UTC offset, got {text!r}")
    return moment


def _time(value: object, path: str) -> time:
    """Parse an ISO 8601 time of day."""
    text = _str(value, path)
    try:
        return time.fromisoformat(text)
    except ValueError as error:
        raise ConfigError(path, f"must be an ISO 8601 time, got {text!r}") from error


def _seconds(value: object, path: str) -> timedelta:
    """Convert a number of seconds to a timedelta."""
    return timedelta(seconds=_num(value, path))


def _list(value: object, path: str) -> list[object]:
    """Check a JSON array."""
    if not isinstance(value, list):
        raise ConfigError(path, f"must be a list, got {value!r}")
    return value


def _enum[E: StrEnum](cls: type[E]) -> _Converter[E]:
    """Build a converter for the values of an enum."""

    def convert(value: object, path: str) -> E:
        text = _str(value, path)
        try:
            return cls(text)
        except ValueError as error:
            known = ", ".join(repr(member.value) for member in cls)
            raise ConfigError(path, f"must be one of {known}, got {text!r}") from error

    return convert


def _phases(value: object, path: str) -> frozenset[Phase]:
    """Convert a list of phase names to a set, rejecting duplicates."""
    phases: list[Phase] = []
    for index, item in enumerate(_list(value, path)):
        phase = _enum(Phase)(item, f"{path}[{index}]")
        if phase in phases:
            raise ConfigError(f"{path}[{index}]", f"duplicate phase {phase.value!r}")
        phases.append(phase)
    return frozenset(phases)


def _params(value: object, path: str) -> Params:
    """Check a mapping of reason or event parameters."""
    if not isinstance(value, Mapping):
        raise ConfigError(path, f"must be an object, got {value!r}")
    params: Params = {}
    for key, item in value.items():
        item_path = _join(path, str(key))
        if not isinstance(key, str):
            raise ConfigError(item_path, f"key must be a string, got {key!r}")
        if not isinstance(item, str | int | float | bool):
            raise ConfigError(item_path, f"must be a string or number, got {item!r}")
        if isinstance(item, float) and not math.isfinite(item):
            raise ConfigError(item_path, f"must be finite, got {item!r}")
        params[key] = item
    return params


class _Reader:
    """Typed access to the keys of one JSON object, tracking the path."""

    def __init__(self, data: object, path: str, keys: Iterable[str]) -> None:
        if not isinstance(data, Mapping):
            raise ConfigError(path or "data", f"must be an object, got {data!r}")
        self.path = path
        self.data = data
        known = set(keys)
        for key in data:
            if key not in known:
                raise ConfigError(_join(path, str(key)), "unknown field")

    def has(self, key: str) -> bool:
        """Say whether the key is present, even as null."""
        return key in self.data

    def req[T](self, key: str, convert: _Converter[T]) -> T:
        """Read a required key."""
        path = _join(self.path, key)
        if key not in self.data:
            raise ConfigError(path, "required")
        return convert(self.data[key], path)

    def opt[T](self, key: str, convert: _Converter[T]) -> T | None:
        """Read an optional key; omitted and null both give None."""
        value = self.data.get(key)
        if value is None:
            return None
        return convert(value, _join(self.path, key))

    def given[T](
        self, key: str, convert: _Converter[T], into: dict[str, object]
    ) -> None:
        """Copy a key into `into` when present, leaving a default alone otherwise."""
        if key in self.data:
            into[key] = convert(self.data[key], _join(self.path, key))

    def items[T](self, key: str, build: Callable[[object, str], T]) -> tuple[T, ...]:
        """Read a required list, building one object per element."""
        path = _join(self.path, key)
        if key not in self.data:
            raise ConfigError(path, "required")
        return tuple(
            build(item, f"{path}[{index}]")
            for index, item in enumerate(_list(self.data[key], path))
        )


def _iso(moment: datetime | None) -> str | None:
    """Format an optional timestamp."""
    return None if moment is None else moment.isoformat()


# --- Snapshot ---------------------------------------------------------------


def _charger_from(data: object, path: str) -> Charger:
    """Build a charger from its dict."""
    reader = _Reader(
        data,
        path,
        (
            "id",
            "phases",
            "kind",
            "min_current",
            "max_current",
            "assumed_current",
            "fallback_current",
        ),
    )
    return _make(
        path,
        Charger,
        id=reader.req("id", _str),
        phases=reader.req("phases", _phases),
        kind=reader.req("kind", _enum(ChargerKind)),
        min_current=reader.opt("min_current", _int),
        max_current=reader.opt("max_current", _int),
        assumed_current=reader.opt("assumed_current", _num),
        fallback_current=reader.opt("fallback_current", _int),
    )


def _state_from(data: object, path: str) -> ChargerState:
    """Build a charger state from its dict."""
    reader = _Reader(
        data,
        path,
        (
            "charger_id",
            "car_connected",
            "current_setting",
            "paused",
            "measured_power_w",
            "available",
        ),
    )
    extra: dict[str, object] = {}
    reader.given("available", _bool, extra)
    return _make(
        path,
        ChargerState,
        charger_id=reader.req("charger_id", _str),
        car_connected=reader.req("car_connected", _bool),
        current_setting=reader.req("current_setting", _num),
        paused=reader.req("paused", _bool),
        measured_power_w=reader.opt("measured_power_w", _num),
        **extra,
    )


def _session_from(data: object, path: str) -> Session:
    """Build a session from its dict."""
    reader = _Reader(
        data,
        path,
        (
            "charger_id",
            "ready_time",
            "energy_need_kwh",
            "soc_percent",
            "target_soc_percent",
            "battery_capacity_kwh",
            "delivered_kwh",
            "charge_now",
            "smart_charging",
            "user_current_limit",
            "plugged_in_at",
        ),
    )
    extra: dict[str, object] = {}
    reader.given("delivered_kwh", _num, extra)
    reader.given("charge_now", _bool, extra)
    reader.given("smart_charging", _bool, extra)
    return _make(
        path,
        Session,
        charger_id=reader.req("charger_id", _str),
        ready_time=reader.req("ready_time", _time),
        energy_need_kwh=reader.opt("energy_need_kwh", _num),
        soc_percent=reader.opt("soc_percent", _num),
        target_soc_percent=reader.opt("target_soc_percent", _num),
        battery_capacity_kwh=reader.opt("battery_capacity_kwh", _num),
        user_current_limit=reader.opt("user_current_limit", _int),
        plugged_in_at=reader.opt("plugged_in_at", _datetime),
        **extra,
    )


def _interval_from(data: object, path: str) -> PriceInterval:
    """Build a price interval from its dict."""
    reader = _Reader(data, path, ("start", "end", "price"))
    return _make(
        path,
        PriceInterval,
        start=reader.req("start", _datetime),
        end=reader.req("end", _datetime),
        price=reader.req("price", _num),
    )


def _prices_from(data: object, path: str) -> PriceSeries:
    """Build a price series from its dict."""
    reader = _Reader(data, path, ("intervals", "updated_at"))
    return _make(
        path,
        PriceSeries,
        intervals=reader.items("intervals", _interval_from),
        updated_at=reader.req("updated_at", _datetime),
    )


def _currents_from(data: object, path: str) -> PhaseCurrents:
    """Build phase currents from their dict."""
    reader = _Reader(data, path, ("measured_at", "l1", "l2", "l3"))
    return _make(
        path,
        PhaseCurrents,
        measured_at=reader.req("measured_at", _datetime),
        l1=reader.opt("l1", _num),
        l2=reader.opt("l2", _num),
        l3=reader.opt("l3", _num),
    )


_SETTINGS_FIELDS: dict[str, _Converter[object]] = {
    "voltage": _num,
    "min_block_quarters": _int,
    "price_cap": _num,
    "default_energy_need_kwh": _num,
    "max_price_age": _seconds,
    "max_measurement_age": _seconds,
}


def _settings_from(data: object, path: str) -> Settings:
    """Build settings from their dict."""
    reader = _Reader(
        data,
        path,
        (
            "phase_budget",
            "phase_threshold",
            "fallback_current",
            "timezone",
            *_SETTINGS_FIELDS,
        ),
    )
    extra: dict[str, object] = {}
    for key, convert in _SETTINGS_FIELDS.items():
        if key == "price_cap":
            extra[key] = reader.opt(key, convert)
        else:
            reader.given(key, convert, extra)
    return _make(
        path,
        Settings,
        phase_budget=reader.req("phase_budget", _num),
        phase_threshold=reader.req("phase_threshold", _num),
        fallback_current=reader.req("fallback_current", _int),
        timezone=reader.req("timezone", _str),
        **extra,
    )


def snapshot_to_dict(snapshot: Snapshot) -> dict[str, object]:
    """Convert a snapshot to a plain JSON-compatible dict.

    Args:
        snapshot: The snapshot.

    Returns:
        A dict of numbers, strings, booleans, lists, dicts and `None`, with times
        as ISO 8601 strings including the offset.
    """
    settings = snapshot.settings
    currents = snapshot.phase_currents
    return {
        "now": snapshot.now.isoformat(),
        "settings": {
            "phase_budget": settings.phase_budget,
            "phase_threshold": settings.phase_threshold,
            "fallback_current": settings.fallback_current,
            "timezone": settings.timezone,
            "voltage": settings.voltage,
            "min_block_quarters": settings.min_block_quarters,
            "price_cap": settings.price_cap,
            "default_energy_need_kwh": settings.default_energy_need_kwh,
            "max_price_age": settings.max_price_age.total_seconds(),
            "max_measurement_age": settings.max_measurement_age.total_seconds(),
        },
        "chargers": [
            {
                "id": c.id,
                "phases": [p.value for p in sorted(c.phases)],
                "kind": c.kind.value,
                "min_current": c.min_current,
                "max_current": c.max_current,
                "assumed_current": c.assumed_current,
                "fallback_current": c.fallback_current,
            }
            for c in snapshot.chargers
        ],
        "states": [
            {
                "charger_id": s.charger_id,
                "car_connected": s.car_connected,
                "current_setting": s.current_setting,
                "paused": s.paused,
                "measured_power_w": s.measured_power_w,
                "available": s.available,
            }
            for s in snapshot.states
        ],
        "sessions": [
            {
                "charger_id": s.charger_id,
                "ready_time": s.ready_time.isoformat(),
                "energy_need_kwh": s.energy_need_kwh,
                "soc_percent": s.soc_percent,
                "target_soc_percent": s.target_soc_percent,
                "battery_capacity_kwh": s.battery_capacity_kwh,
                "delivered_kwh": s.delivered_kwh,
                "charge_now": s.charge_now,
                "smart_charging": s.smart_charging,
                "user_current_limit": s.user_current_limit,
                "plugged_in_at": _iso(s.plugged_in_at),
            }
            for s in snapshot.sessions
        ],
        "prices": {
            "intervals": [
                {
                    "start": i.start.isoformat(),
                    "end": i.end.isoformat(),
                    "price": i.price,
                }
                for i in snapshot.prices.intervals
            ],
            "updated_at": snapshot.prices.updated_at.isoformat(),
        },
        "phase_currents": (
            None
            if currents is None
            else {
                "measured_at": currents.measured_at.isoformat(),
                "l1": currents.l1,
                "l2": currents.l2,
                "l3": currents.l3,
            }
        ),
    }


def snapshot_from_dict(data: Mapping[str, object]) -> Snapshot:
    """Build a snapshot from a plain dict.

    Args:
        data: A dict as produced by `snapshot_to_dict`. Optional fields may be
            omitted or null.

    Returns:
        The snapshot.

    Raises:
        ConfigError: If a field is missing, unknown, wrongly typed or invalid; its
            `field` is the dotted path, such as `chargers[1].max_current`.
    """
    reader = _Reader(
        data,
        "",
        (
            "now",
            "settings",
            "chargers",
            "states",
            "sessions",
            "prices",
            "phase_currents",
        ),
    )
    return _make(
        "",
        Snapshot,
        now=reader.req("now", _datetime),
        settings=reader.req("settings", _settings_from),
        chargers=reader.items("chargers", _charger_from),
        states=reader.items("states", _state_from),
        sessions=reader.items("sessions", _session_from),
        prices=reader.req("prices", _prices_from),
        phase_currents=reader.opt("phase_currents", _currents_from),
    )


# --- Decision ---------------------------------------------------------------


def _action_from(data: object, path: str) -> Action:
    """Build an action from its dict."""
    reader = _Reader(data, path, ("charger_id", "kind", "target_current", "stage"))
    return Action(
        charger_id=reader.req("charger_id", _str),
        kind=reader.req("kind", _enum(ActionKind)),
        target_current=reader.req("target_current", _num),
        stage=reader.req("stage", _enum(Stage)),
    )


def _reason_from(data: object, path: str) -> Reason:
    """Build a reason from its dict."""
    reader = _Reader(data, path, ("code", "text", "params"))
    return Reason(
        code=reader.req("code", _enum(ReasonCode)),
        text=reader.req("text", _str),
        params=reader.opt("params", _params) or {},
    )


def _quarter_from(data: object, path: str) -> PlannedQuarter:
    """Build a planned quarter from its dict."""
    reader = _Reader(data, path, ("start", "price", "current"))
    return PlannedQuarter(
        start=reader.req("start", _datetime),
        price=reader.opt("price", _num),
        current=reader.req("current", _num),
    )


def _charger_decision_from(data: object, path: str) -> ChargerDecision:
    """Build a charger decision from its dict."""
    reader = _Reader(
        data,
        path,
        (
            "charger_id",
            "allocation",
            "priority",
            "reason",
            "plan",
            "remaining_need_kwh",
            "deadline",
            "expected_finish",
            "reaches_target",
        ),
    )
    plan = reader.items("plan", _quarter_from) if reader.has("plan") else ()
    return ChargerDecision(
        charger_id=reader.req("charger_id", _str),
        allocation=reader.req("allocation", _num),
        priority=reader.opt("priority", _int),
        reason=reader.req("reason", _reason_from),
        plan=plan,
        remaining_need_kwh=reader.opt("remaining_need_kwh", _num),
        deadline=reader.opt("deadline", _datetime),
        expected_finish=reader.opt("expected_finish", _datetime),
        reaches_target=reader.opt("reaches_target", _bool),
    )


def _event_from(data: object, path: str) -> Event:
    """Build an event from its dict."""
    reader = _Reader(data, path, ("kind", "text", "charger_id", "params"))
    return Event(
        kind=reader.req("kind", _enum(EventKind)),
        text=reader.req("text", _str),
        charger_id=reader.opt("charger_id", _str),
        params=reader.opt("params", _params) or {},
    )


def _amps_from(data: object, path: str) -> PhaseAmps:
    """Build amps per phase from their dict."""
    reader = _Reader(data, path, ("l1", "l2", "l3"))
    return PhaseAmps(
        l1=reader.req("l1", _num), l2=reader.req("l2", _num), l3=reader.req("l3", _num)
    )


def decision_to_dict(decision: Decision) -> dict[str, object]:
    """Convert a decision to a plain JSON-compatible dict.

    Args:
        decision: The decision.

    Returns:
        A dict of numbers, strings, booleans, lists, dicts and `None`, with times
        as ISO 8601 strings including the offset.
    """
    return {
        "actions": [
            {
                "charger_id": a.charger_id,
                "kind": a.kind.value,
                "target_current": a.target_current,
                "stage": a.stage.value,
            }
            for a in decision.actions
        ],
        "chargers": [
            {
                "charger_id": c.charger_id,
                "allocation": c.allocation,
                "priority": c.priority,
                "reason": {
                    "code": c.reason.code.value,
                    "text": c.reason.text,
                    "params": dict(c.reason.params),
                },
                "plan": [
                    {
                        "start": q.start.isoformat(),
                        "price": q.price,
                        "current": q.current,
                    }
                    for q in c.plan
                ],
                "remaining_need_kwh": c.remaining_need_kwh,
                "deadline": _iso(c.deadline),
                "expected_finish": _iso(c.expected_finish),
                "reaches_target": c.reaches_target,
            }
            for c in decision.chargers
        ],
        "available": {
            "l1": decision.available.l1,
            "l2": decision.available.l2,
            "l3": decision.available.l3,
        },
        "status": decision.status.value,
        "events": [
            {
                "kind": e.kind.value,
                "text": e.text,
                "charger_id": e.charger_id,
                "params": dict(e.params),
            }
            for e in decision.events
        ],
    }


def decision_from_dict(data: Mapping[str, object]) -> Decision:
    """Build a decision from a plain dict.

    Args:
        data: A dict as produced by `decision_to_dict`.

    Returns:
        The decision.

    Raises:
        ConfigError: If a field is missing, unknown, wrongly typed or invalid; its
            `field` is the dotted path.
    """
    reader = _Reader(data, "", ("actions", "chargers", "available", "status", "events"))
    return Decision(
        actions=reader.items("actions", _action_from),
        chargers=reader.items("chargers", _charger_decision_from),
        available=reader.req("available", _amps_from),
        status=reader.req("status", _enum(Status)),
        events=reader.items("events", _event_from),
    )


def main() -> None:
    """Showcase this module's functionality."""
    now = datetime(2026, 10, 21, 20, 0, tzinfo=UTC)
    snapshot = Snapshot(
        now=now,
        settings=Settings(25.0, 32.0, 6, "Europe/Copenhagen"),
        chargers=(
            Charger("carport", frozenset({Phase.L1}), ChargerKind.ADJUSTABLE, 6, 16),
        ),
        states=(ChargerState("carport", True, 0, True),),
        sessions=(Session("carport", time(7, 0), energy_need_kwh=10.0),),
        prices=PriceSeries((), updated_at=now),
        phase_currents=PhaseCurrents(now, l1=3.0, l2=None, l3=None),
    )

    data = snapshot_to_dict(snapshot)
    text = json.dumps(data)
    restored = snapshot_from_dict(json.loads(text))

    print(text)
    print(f"round trip equal: {restored == snapshot}")

    # A wrongly typed field is rejected with its dotted path.
    broken = json.loads(text)
    broken["chargers"][0]["max_current"] = "sixteen"

    try:
        snapshot_from_dict(broken)
    except ConfigError as error:
        print(f"rejected: {error}")


if __name__ == "__main__":
    main()
