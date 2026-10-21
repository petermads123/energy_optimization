"""Tests for energy_optimization.ev.codec (T2, T19): plain-dict conversion."""

import copy
import json
from collections.abc import Callable, Iterator, Mapping
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from energy_optimization.ev import (
    Action,
    ActionKind,
    Charger,
    ChargerDecision,
    ChargerKind,
    ChargerMemory,
    ChargerState,
    ConfigError,
    Decision,
    Event,
    EventKind,
    GuardLevel,
    Memory,
    Phase,
    PhaseAmps,
    PhaseCurrents,
    PlannedQuarter,
    PriceInterval,
    PriceSeries,
    Reason,
    ReasonCode,
    Reduction,
    Session,
    Settings,
    Snapshot,
    Stage,
    Status,
    decide,
    decision_from_dict,
    decision_to_dict,
    memory_from_dict,
    memory_to_dict,
    snapshot_from_dict,
    snapshot_to_dict,
)
from energy_optimization.ev.codec import main as showcase
from energy_optimization.ev.quarters import next_occurrence

CPH = ZoneInfo("Europe/Copenhagen")
NOW = datetime(2026, 10, 21, 18, 0, tzinfo=UTC)
ALL = frozenset(Phase)
DELETE = object()


def base_snapshot() -> Snapshot:
    return Snapshot(
        now=NOW,
        settings=Settings(
            25.0,
            32.0,
            6,
            "Europe/Copenhagen",
            price_cap=0.3,
            max_price_age=timedelta(minutes=45),
        ),
        chargers=(
            Charger("carport", ALL, ChargerKind.ADJUSTABLE, 6, 16, fallback_current=8),
            Charger(
                "pump",
                frozenset({Phase.L2}),
                ChargerKind.ON_OFF,
                assumed_current=10.0,
                fallback_current=0,
            ),
            Charger(
                "wall", frozenset({Phase.L3}), ChargerKind.FIXED, assumed_current=8.0
            ),
        ),
        states=(
            ChargerState("carport", True, 0.0, True, 1200.0),
            ChargerState("pump", False, 0.0, True, None, False),
            ChargerState("wall", True, 8.0, False),
        ),
        sessions=(
            Session(
                "carport",
                time(7, 0),
                energy_need_kwh=20.0,
                soc_percent=40,
                target_soc_percent=80,
                battery_capacity_kwh=50,
                delivered_kwh=2.5,
                charge_now=True,
                smart_charging=False,
                user_current_limit=10,
                plugged_in_at=NOW - timedelta(hours=1),
            ),
            Session("pump", time(6, 30)),
        ),
        prices=PriceSeries(
            (
                PriceInterval(NOW, NOW + timedelta(hours=1), 0.25),
                PriceInterval(
                    NOW + timedelta(hours=1), NOW + timedelta(minutes=75), -0.1
                ),
            ),
            NOW,
        ),
        phase_currents=PhaseCurrents(NOW, 10.0, None, 3.5),
    )


def encoded() -> dict[str, Any]:
    return json.loads(json.dumps(snapshot_to_dict(base_snapshot())))


def edit(data: dict[str, Any], path: list[str | int], value: object) -> dict[str, Any]:
    """Return a deep copy of `data` with the value at `path` replaced or deleted."""
    result = copy.deepcopy(data)
    target: Any = result
    for step in path[:-1]:
        target = target[step]
    if value is DELETE:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return result


def dotted(path: list[str | int]) -> str:
    """The dotted path the codec reports: `chargers[1].max_current`."""
    text = ""
    for step in path:
        text += f"[{step}]" if isinstance(step, int) else f".{step}"
    return text.lstrip(".")


def error_of(build: Callable[[], object]) -> ConfigError:
    with pytest.raises(ConfigError) as caught:
        build()
    return caught.value


def decode_field(data: dict[str, Any]) -> str:
    return error_of(lambda: snapshot_from_dict(data)).field


def round_trip(snapshot: Snapshot) -> Snapshot:
    data = json.loads(json.dumps(snapshot_to_dict(snapshot), allow_nan=False))
    return snapshot_from_dict(data)


# --- snapshot round trip (T19) -------------------------------------------------


def test_snapshot_round_trips_through_json() -> None:
    snapshot = base_snapshot()

    assert round_trip(snapshot) == snapshot


def with_no_phase_currents(s: Snapshot) -> Snapshot:
    return replace(s, phase_currents=None)


def with_all_phase_currents(s: Snapshot) -> Snapshot:
    return replace(s, phase_currents=PhaseCurrents(NOW, 1.0, 2.0, 3.0))


def with_odd_offsets(s: Snapshot) -> Snapshot:
    plus = timezone(timedelta(hours=5, minutes=45))
    start = datetime(2026, 10, 21, 23, 45, tzinfo=plus)
    return replace(
        s,
        now=datetime(2026, 10, 21, 23, 45, tzinfo=plus),
        prices=PriceSeries(
            (PriceInterval(start, start + timedelta(minutes=30), 1.0),), start
        ),
    )


def with_fold_datetimes(s: Snapshot) -> Snapshot:
    first = datetime(2026, 10, 25, 2, 0, tzinfo=CPH)
    second = datetime(2026, 10, 25, 2, 0, fold=1, tzinfo=CPH)
    return replace(
        s,
        now=datetime(2026, 10, 25, 2, 30, fold=1, tzinfo=CPH),
        prices=PriceSeries((PriceInterval(first, second, 0.4),), second),
    )


def with_fractional_ready_time(s: Snapshot) -> Snapshot:
    session = replace(s.sessions[1], ready_time=time(6, 45, 30, 500))
    return replace(s, sessions=(s.sessions[0], session))


def with_no_price_cap(s: Snapshot) -> Snapshot:
    return replace(s, settings=replace(s.settings, price_cap=None))


def with_odd_settings(s: Snapshot) -> Snapshot:
    odd = replace(
        s.settings,
        max_price_age=timedelta(seconds=90, microseconds=500),
        min_block_quarters=1,
        voltage=400.0,
        default_energy_need_kwh=0.0,
    )
    return replace(s, settings=odd)


def with_zero_user_limit(s: Snapshot) -> Snapshot:
    session = replace(s.sessions[0], user_current_limit=0, plugged_in_at=None)
    return replace(s, sessions=(session, s.sessions[1]))


def with_nothing(s: Snapshot) -> Snapshot:
    return replace(s, chargers=(), states=(), sessions=())


@pytest.mark.parametrize(
    "variant",
    [
        with_no_phase_currents,
        with_all_phase_currents,
        with_odd_offsets,
        with_fractional_ready_time,
        with_no_price_cap,
        with_odd_settings,
        with_zero_user_limit,
        with_nothing,
    ],
    ids=lambda f: f.__name__,
)
def test_snapshot_variants_round_trip_through_json(
    variant: Callable[[Snapshot], Snapshot],
) -> None:
    snapshot = variant(base_snapshot())

    assert round_trip(snapshot) == snapshot


def test_round_trip_keeps_the_instants_of_datetimes_inside_the_fall_back_hour() -> None:
    """The instant and offset survive; `==` cannot say so for an ambiguous time.

    Python (PEP 495) never equates a `ZoneInfo` datetime inside the repeated hour with
    the same instant in a fixed offset, which is all a decoded string can carry, so the
    comparison is made on the instants and on the encoded form.
    """
    snapshot = with_fold_datetimes(base_snapshot())

    restored = round_trip(snapshot)

    assert restored.now.astimezone(UTC) == snapshot.now.astimezone(UTC)
    assert restored.now.utcoffset() == snapshot.now.utcoffset() == timedelta(hours=1)
    assert [i.end.astimezone(UTC) for i in restored.prices.intervals] == [
        i.end.astimezone(UTC) for i in snapshot.prices.intervals
    ]
    assert snapshot_to_dict(restored) == snapshot_to_dict(snapshot)


def test_round_trip_preserves_every_charger_kind() -> None:
    restored = round_trip(base_snapshot())

    assert {c.kind for c in restored.chargers} == set(ChargerKind)


def test_round_trip_keeps_a_fold_ready_time_giving_the_same_deadline() -> None:
    snapshot = base_snapshot()
    session = replace(snapshot.sessions[1], ready_time=time(2, 30, fold=1))
    snapshot = replace(snapshot, sessions=(snapshot.sessions[0], session))

    restored = round_trip(snapshot)
    after = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)

    assert next_occurrence(
        restored.sessions[1].ready_time, after, "Europe/Copenhagen"
    ) == (next_occurrence(session.ready_time, after, "Europe/Copenhagen"))


# --- snapshot_to_dict ----------------------------------------------------------

PLAIN_LEAVES = {str, int, float, bool, type(None)}


def assert_plain(value: object) -> None:
    if isinstance(value, dict):
        assert type(value) is dict
        for key, item in value.items():
            assert type(key) is str
            assert_plain(item)
    elif isinstance(value, list):
        assert type(value) is list
        for item in value:
            assert_plain(item)
    else:
        assert type(value) in PLAIN_LEAVES, f"{value!r} is a {type(value).__name__}"


def test_snapshot_to_dict_holds_only_plain_json_types() -> None:
    assert_plain(snapshot_to_dict(base_snapshot()))


def test_snapshot_to_dict_encodes_enums_by_value_and_times_with_their_offset() -> None:
    data = snapshot_to_dict(base_snapshot())

    assert data["now"] == "2026-10-21T18:00:00+00:00"
    assert [c["kind"] for c in data["chargers"]] == ["adjustable", "on_off", "fixed"]  # type: ignore[attr-defined,index,union-attr]  # dict walk
    assert data["chargers"][0]["phases"] == ["L1", "L2", "L3"]  # type: ignore[index]  # dict walk
    assert data["sessions"][1]["ready_time"] == "06:30:00"  # type: ignore[index]  # dict walk


def test_snapshot_to_dict_writes_a_missing_value_as_null() -> None:
    data = snapshot_to_dict(base_snapshot())

    assert data["phase_currents"]["l2"] is None  # type: ignore[index]  # dict walk
    assert data["states"][2]["measured_power_w"] is None  # type: ignore[index]  # dict walk
    assert (
        snapshot_to_dict(with_no_phase_currents(base_snapshot()))["phase_currents"]
        is None
    )


def test_snapshot_to_dict_writes_a_fold_datetime_with_its_real_offset() -> None:
    data = snapshot_to_dict(with_fold_datetimes(base_snapshot()))

    assert data["now"] == "2026-10-25T02:30:00+01:00"


def test_snapshot_to_dict_writes_a_timedelta_as_seconds() -> None:
    data = snapshot_to_dict(base_snapshot())

    assert data["settings"]["max_price_age"] == 2700.0  # type: ignore[index]  # dict walk


def test_snapshot_to_dict_survives_json_without_nan() -> None:
    json.dumps(snapshot_to_dict(base_snapshot()), allow_nan=False)


# --- snapshot_from_dict: required, unknown, wrong type -------------------------


@pytest.mark.parametrize(
    "path",
    [
        ["now"],
        ["settings"],
        ["settings", "phase_budget"],
        ["settings", "phase_threshold"],
        ["settings", "fallback_current"],
        ["settings", "timezone"],
        ["chargers"],
        ["chargers", 0, "id"],
        ["chargers", 0, "phases"],
        ["chargers", 1, "kind"],
        ["states"],
        ["states", 0, "paused"],
        ["states", 1, "car_connected"],
        ["states", 2, "current_setting"],
        ["states", 0, "charger_id"],
        ["sessions"],
        ["sessions", 0, "ready_time"],
        ["sessions", 1, "charger_id"],
        ["prices"],
        ["prices", "updated_at"],
        ["prices", "intervals"],
        ["prices", "intervals", 0, "price"],
        ["phase_currents", "measured_at"],
    ],
    ids=lambda p: ".".join(str(step) for step in p),
)
def test_snapshot_from_dict_rejects_a_missing_required_field_with_its_path(
    path: list[str | int],
) -> None:
    error = error_of(lambda: snapshot_from_dict(edit(encoded(), path, DELETE)))

    assert error.field == dotted(path)
    assert error.message == "required"


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (["chargers", 1, "max_current"], "16"),
        (["chargers", 0, "max_current"], True),
        (["chargers", 0, "min_current"], 6.5),
        (["chargers", 0, "min_current"], "6"),
        (["states", 0, "car_connected"], 1),
        (["states", 0, "current_setting"], "0"),
        (["settings", "max_price_age"], "3600"),
        (["settings", "timezone"], 5),
        (["settings", "phase_budget"], True),
        (["now"], 1700000000),
        (["now"], None),
        (["chargers"], "x"),
        (["chargers", 0], 5),
        (["chargers", 0, "phases"], "L1"),
        (["sessions", 0, "ready_time"], 7),
        (["sessions", 0, "charge_now"], "yes"),
        (["prices", "intervals"], {}),
        (["prices", "intervals", 0, "price"], "1"),
    ],
    ids=lambda v: str(v),
)
def test_snapshot_from_dict_rejects_a_wrongly_typed_field_with_its_path(
    path: list[str | int], value: object
) -> None:
    error = error_of(lambda: snapshot_from_dict(edit(encoded(), path, value)))

    assert error.field == dotted(path)


@pytest.mark.parametrize(
    ("path", "field"),
    [
        (["extra"], "extra"),
        (["chargers", 1, "colour"], "chargers[1].colour"),
        (["settings", "dark_mode"], "settings.dark_mode"),
        (["states", 0, "soc"], "states[0].soc"),
        (["sessions", 0, "mood"], "sessions[0].mood"),
        (["prices", "source"], "prices.source"),
        (["prices", "intervals", 0, "currency"], "prices.intervals[0].currency"),
        (["phase_currents", "l4"], "phase_currents.l4"),
    ],
)
def test_snapshot_from_dict_rejects_an_unknown_key_with_its_path(
    path: list[str | int], field: str
) -> None:
    assert decode_field(edit(encoded(), path, 1)) == field


@pytest.mark.parametrize("data", [[], None, "x", 5, [("now", 1)]])
def test_both_decoders_reject_a_non_object_with_the_field_data(data: object) -> None:
    assert error_of(lambda: snapshot_from_dict(data)).field == "data"  # type: ignore[arg-type]  # bad on purpose
    assert error_of(lambda: decision_from_dict(data)).field == "data"  # type: ignore[arg-type]  # bad on purpose


def test_decoding_accepts_any_mapping_not_only_a_dict() -> None:
    class View(Mapping[str, object]):
        def __init__(self, data: dict[str, Any]) -> None:
            self._data = data

        def __getitem__(self, key: str) -> object:
            return self._data[key]

        def __iter__(self) -> Iterator[str]:
            return iter(self._data)

        def __len__(self) -> int:
            return len(self._data)

    assert snapshot_from_dict(View(encoded())) == base_snapshot()


# --- snapshot_from_dict: invalid values reported with the model's field --------


@pytest.mark.parametrize(
    ("path", "value", "field"),
    [
        (["chargers", 0, "min_current"], 17, "chargers[0].max_current"),
        (["chargers", 1, "min_current"], 6, "chargers[1].min_current"),
        (["chargers", 0, "phases"], [], "chargers[0].phases"),
        (["chargers", 0, "id"], "", "chargers[0].id"),
        (["chargers", 2, "fallback_current"], 6, "chargers[2].fallback_current"),
        (["settings", "timezone"], "Mars/Base", "settings.timezone"),
        (["settings", "max_price_age"], 0, "settings.max_price_age"),
        (["settings", "phase_budget"], -1, "settings.phase_budget"),
        (["sessions", 0, "ready_time"], "07:00+02:00", "sessions[0].ready_time"),
        (["sessions", 0, "soc_percent"], 120, "sessions[0].soc_percent"),
        (["now"], "2026-10-21T20:00:00", "now"),
        (["states", 1, "current_setting"], -1, "states[1].current_setting"),
        (
            ["prices", "intervals", 0, "start"],
            "2026-10-21T18:07:00+00:00",
            "prices.intervals[0].start",
        ),
        (
            ["prices", "intervals", 0, "end"],
            "2026-10-21T17:00:00+00:00",
            "prices.intervals[0].end",
        ),
        (["phase_currents", "l1"], -1, "phase_currents.l1"),
    ],
)
def test_snapshot_from_dict_prefixes_a_model_error_with_the_path(
    path: list[str | int], value: object, field: str
) -> None:
    error = error_of(lambda: snapshot_from_dict(edit(encoded(), path, value)))

    assert error.field == field
    assert not error.message.startswith(error.field)  # no double prefix


def test_snapshot_from_dict_reports_overlapping_intervals_on_the_series() -> None:
    data = edit(
        encoded(),
        ["prices", "intervals", 1, "start"],
        "2026-10-21T18:45:00+00:00",
    )

    assert decode_field(data) == "prices.intervals"


def test_snapshot_from_dict_reports_duplicate_ids_and_unknown_chargers_on_the_list() -> (
    None
):
    duplicate = edit(encoded(), ["chargers", 1, "id"], "carport")
    unknown = edit(encoded(), ["sessions", 1, "charger_id"], "ghost")

    assert decode_field(duplicate) == "chargers"
    assert decode_field(unknown) == "sessions"


def test_snapshot_from_dict_reports_a_missing_state_on_the_list() -> None:
    data = edit(encoded(), ["states", 1], DELETE)

    assert decode_field(data) == "states"


@pytest.mark.parametrize(
    ("phases", "field"),
    [
        (["L1", "L1"], "chargers[0].phases[1]"),
        (["L1", "L2", "L1"], "chargers[0].phases[2]"),
        (["L4"], "chargers[0].phases[0]"),
        (["l1"], "chargers[0].phases[0]"),
        ([1], "chargers[0].phases[0]"),
        ([None], "chargers[0].phases[0]"),
    ],
)
def test_snapshot_from_dict_rejects_duplicate_and_unknown_phases(
    phases: list[object], field: str
) -> None:
    assert decode_field(edit(encoded(), ["chargers", 0, "phases"], phases)) == field


def test_snapshot_from_dict_accepts_phases_in_any_order() -> None:
    restored = snapshot_from_dict(
        edit(encoded(), ["chargers", 0, "phases"], ["L3", "L1", "L2"])
    )

    assert restored.chargers[0].phases == ALL


# --- snapshot_from_dict: numbers -----------------------------------------------


@pytest.mark.parametrize(
    ("path", "value", "field"),
    [
        (["settings", "phase_budget"], 10**400, "settings.phase_budget"),
        (["settings", "max_price_age"], 10**400, "settings.max_price_age"),
        (["settings", "max_price_age"], 1e20, "settings.max_price_age"),
        (["settings", "max_measurement_age"], 1e15, "settings.max_measurement_age"),
        (["chargers", 0, "max_current"], 10**400, "chargers[0].max_current"),
        (["states", 0, "current_setting"], 10**400, "states[0].current_setting"),
    ],
)
def test_snapshot_from_dict_turns_a_huge_number_into_a_config_error(
    path: list[str | int], value: object, field: str
) -> None:
    assert decode_field(edit(encoded(), path, value)) == field


@pytest.mark.parametrize("text", ["NaN", "Infinity", "-Infinity"])
def test_snapshot_from_dict_rejects_non_finite_numbers_read_from_json(
    text: str,
) -> None:
    data = json.loads(
        json.dumps(snapshot_to_dict(base_snapshot())).replace(
            '"phase_budget": 25.0', f'"phase_budget": {text}'
        )
    )

    assert decode_field(data) == "settings.phase_budget"


@pytest.mark.parametrize(("value", "expected"), [(6.0, 6), (6, 6)])
def test_snapshot_from_dict_accepts_an_integral_float_for_an_int_field(
    value: float, expected: int
) -> None:
    restored = snapshot_from_dict(
        edit(encoded(), ["chargers", 0, "min_current"], value)
    )

    assert restored.chargers[0].min_current == expected
    assert type(restored.chargers[0].min_current) is int


def test_snapshot_from_dict_accepts_an_int_for_a_float_field() -> None:
    restored = snapshot_from_dict(edit(encoded(), ["settings", "phase_budget"], 25))

    assert restored.settings.phase_budget == 25.0


# --- snapshot_from_dict: times -------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["2026-10-21T18:00:00Z", "2026-10-21T20:00:00+02:00", "2026-10-21T18:00:00+00:00"],
)
def test_snapshot_from_dict_accepts_an_iso_timestamp_with_an_offset(text: str) -> None:
    assert snapshot_from_dict(edit(encoded(), ["now"], text)).now == NOW


@pytest.mark.parametrize(
    "text",
    ["2026-10-21T18:00:00", "2026-10-21", "", "yesterday", "2026-13-01T00:00:00Z"],
)
def test_snapshot_from_dict_rejects_a_timestamp_without_an_offset_or_unparseable(
    text: str,
) -> None:
    assert decode_field(edit(encoded(), ["now"], text)) == "now"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("07:00", time(7, 0)),
        ("07:00:00", time(7, 0)),
        ("07:00:30", time(7, 0, 30)),
        ("06:45:30.000500", time(6, 45, 30, 500)),
    ],
)
def test_snapshot_from_dict_accepts_ready_time_spellings(
    text: str, expected: time
) -> None:
    restored = snapshot_from_dict(edit(encoded(), ["sessions", 0, "ready_time"], text))

    assert restored.sessions[0].ready_time == expected


@pytest.mark.parametrize("text", ["24:00", "7", "seven", "", "07:60", "25:00"])
def test_snapshot_from_dict_rejects_unparseable_ready_times(text: str) -> None:
    assert (
        decode_field(edit(encoded(), ["sessions", 0, "ready_time"], text))
        == "sessions[0].ready_time"
    )


# --- snapshot_from_dict: null and omitted --------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        ["states", 0, "measured_power_w"],
        ["sessions", 0, "energy_need_kwh"],
        ["sessions", 0, "plugged_in_at"],
        ["sessions", 0, "user_current_limit"],
        ["settings", "price_cap"],
        ["chargers", 0, "fallback_current"],
        ["chargers", 1, "min_current"],
        ["phase_currents", "l1"],
    ],
    ids=lambda p: ".".join(str(step) for step in p),
)
def test_snapshot_from_dict_takes_null_and_omitted_alike_for_optional_fields(
    path: list[str | int],
) -> None:
    nulled = snapshot_from_dict(edit(encoded(), path, None))
    omitted = snapshot_from_dict(edit(encoded(), path, DELETE))

    assert nulled == omitted


def test_snapshot_from_dict_takes_null_phase_currents_as_missing() -> None:
    assert (
        snapshot_from_dict(edit(encoded(), ["phase_currents"], None)).phase_currents
        is None
    )
    assert (
        snapshot_from_dict(edit(encoded(), ["phase_currents"], DELETE)).phase_currents
        is None
    )


def test_snapshot_from_dict_fills_defaults_for_omitted_defaulted_fields() -> None:
    data = encoded()
    for key in (
        "voltage",
        "min_block_quarters",
        "price_cap",
        "default_energy_need_kwh",
        "max_price_age",
        "max_measurement_age",
    ):
        del data["settings"][key]
    del data["states"][0]["available"]
    del data["states"][0]["measured_power_w"]
    for key in ("delivered_kwh", "charge_now", "smart_charging", "plugged_in_at"):
        del data["sessions"][0][key]
    del data["phase_currents"]

    restored = snapshot_from_dict(data)

    assert restored.settings.voltage == 230.0
    assert restored.settings.min_block_quarters == 2
    assert restored.settings.price_cap is None
    assert restored.settings.default_energy_need_kwh == 30.0
    assert restored.settings.max_price_age == timedelta(minutes=60)
    assert restored.settings.max_measurement_age == timedelta(seconds=60)
    assert restored.states[0].available is True
    assert restored.states[0].measured_power_w is None
    assert restored.sessions[0].delivered_kwh == 0.0
    assert restored.sessions[0].charge_now is False
    assert restored.sessions[0].smart_charging is True
    assert restored.phase_currents is None


@pytest.mark.parametrize(
    ("path", "field"),
    [
        (["states", 0, "available"], "states[0].available"),
        (["sessions", 0, "delivered_kwh"], "sessions[0].delivered_kwh"),
        (["sessions", 0, "charge_now"], "sessions[0].charge_now"),
        (["sessions", 0, "smart_charging"], "sessions[0].smart_charging"),
        (["settings", "voltage"], "settings.voltage"),
        (["settings", "min_block_quarters"], "settings.min_block_quarters"),
        (["settings", "default_energy_need_kwh"], "settings.default_energy_need_kwh"),
        (["settings", "max_price_age"], "settings.max_price_age"),
    ],
)
def test_snapshot_from_dict_rejects_null_for_a_field_that_only_has_a_default(
    path: list[str | int], field: str
) -> None:
    """A null there would be a guess (an unknown `available` read as available)."""
    assert decode_field(edit(encoded(), path, None)) == field


def test_snapshot_from_dict_does_not_mutate_its_input() -> None:
    data = encoded()
    before = copy.deepcopy(data)

    snapshot_from_dict(data)

    assert data == before


def test_snapshot_from_dict_accepts_non_ascii_text() -> None:
    data = edit(encoded(), ["chargers", 0, "id"], "bilen på carporten")
    data = edit(data, ["states", 0, "charger_id"], "bilen på carporten")
    data = edit(data, ["sessions", 0, "charger_id"], "bilen på carporten")

    assert snapshot_from_dict(data).chargers[0].id == "bilen på carporten"


# --- decisions (T19) -----------------------------------------------------------


def full_decision() -> Decision:
    start = datetime(2026, 10, 22, 6, 30, tzinfo=CPH)
    reasons = [
        Reason(
            code,
            f"text for {code.value}",
            {"priority": 3, "allocation": 10.0, "ok": True, "note": "x", "ratio": 2.5},
        )
        for code in ReasonCode
    ]
    chargers = tuple(
        ChargerDecision(
            charger_id=f"c{i}",
            allocation=float(i),
            priority=None if i % 2 else (i % 4) + 1,
            reason=reason,
            plan=(
                PlannedQuarter(start, None, 6.0),
                PlannedQuarter(start + timedelta(minutes=15), -0.5, 16.0),
            ),
            remaining_need_kwh=None if i == 0 else 3.5,
            deadline=start,
            expected_finish=None if i == 1 else start + timedelta(hours=1),
            reaches_target=(None, True, False)[i % 3],
        )
        for i, reason in enumerate(reasons)
    )
    return Decision(
        actions=tuple(
            Action("c0", kind, 0.0 if kind is ActionKind.PAUSE else 8.0, stage)
            for kind in ActionKind
            for stage in Stage
        ),
        chargers=chargers,
        available=PhaseAmps(1.0, 2.5, 0.0),
        status=Status.LIMITED_BY_PHASE,
        events=tuple(
            Event(
                kind,
                f"event {kind.value}",
                None if i % 2 else "c0",
                {"deadline": "2026-10-22T07:00:00+02:00", "n": 1},
            )
            for i, kind in enumerate(EventKind)
        ),
    )


def decision_round_trip(decision: Decision) -> Decision:
    data = json.loads(json.dumps(decision_to_dict(decision), allow_nan=False))
    return decision_from_dict(data)


def test_hand_built_decision_round_trips_through_json() -> None:
    decision = full_decision()

    assert decision_round_trip(decision) == decision


def test_a_real_decision_round_trips_through_json() -> None:
    decision = decide(base_snapshot())

    assert decision_round_trip(decision) == decision


@pytest.mark.parametrize("status", list(Status))
def test_every_status_round_trips(status: Status) -> None:
    decision = replace(full_decision(), status=status)

    assert decision_round_trip(decision).status is status


def test_decision_round_trip_keeps_param_types() -> None:
    reason = Reason(
        ReasonCode.IN_PLAN,
        "x",
        {
            "priority": 3,
            "allocation": 10.0,
            "ok": True,
            "deadline": "2026-10-22T07:00:00+02:00",
        },
    )
    decision = replace(
        full_decision(), chargers=(ChargerDecision("a", 1.0, 2, reason),), events=()
    )

    params = decision_round_trip(decision).chargers[0].reason.params

    assert [type(params[k]) for k in ("priority", "allocation", "ok", "deadline")] == [
        int,
        float,
        bool,
        str,
    ]


def test_decision_round_trip_decodes_times_as_fixed_offsets_with_the_same_instants() -> (
    None
):
    decision = full_decision()

    restored = decision_round_trip(decision)

    original = decision.chargers[0].deadline
    decoded = restored.chargers[0].deadline
    assert original is not None
    assert decoded is not None
    assert decoded == original
    assert decoded.utcoffset() == original.utcoffset()
    assert decoded.tzinfo != CPH


def test_decision_to_dict_holds_only_plain_json_types() -> None:
    assert_plain(decision_to_dict(full_decision()))
    assert_plain(decision_to_dict(decide(base_snapshot())))


def test_decision_to_dict_copies_params_so_the_decision_cannot_change() -> None:
    decision = full_decision()
    out = decision_to_dict(decision)

    out["chargers"][0]["reason"]["params"]["injected"] = 1  # type: ignore[index]  # dict walk
    out["events"][0]["params"]["injected"] = 1  # type: ignore[index]  # dict walk

    assert "injected" not in decision.chargers[0].reason.params
    assert "injected" not in decision.events[0].params


def encoded_decision() -> dict[str, Any]:
    return json.loads(json.dumps(decision_to_dict(full_decision())))


def decision_field(data: dict[str, Any]) -> str:
    return error_of(lambda: decision_from_dict(data)).field


@pytest.mark.parametrize(
    ("path", "value", "field"),
    [
        (["status"], DELETE, "status"),
        (["status"], "NORMAL", "status"),
        (["status"], "bogus", "status"),
        (["available", "l2"], DELETE, "available.l2"),
        (["available", "l3"], "1", "available.l3"),
        (["actions", 0, "stage"], "sideways", "actions[0].stage"),
        (["actions", 0, "kind"], 5, "actions[0].kind"),
        (["chargers", 0, "reason", "code"], "bogus", "chargers[0].reason.code"),
        (["chargers", 0, "reason", "text"], 5, "chargers[0].reason.text"),
        (["chargers", 0, "priority"], 2.5, "chargers[0].priority"),
        (["chargers", 0, "priority"], True, "chargers[0].priority"),
        (
            ["chargers", 0, "reason", "params"],
            {"x": [1]},
            "chargers[0].reason.params.x",
        ),
        (
            ["chargers", 0, "reason", "params"],
            {"x": {"y": 1}},
            "chargers[0].reason.params.x",
        ),
        (["chargers", 0, "reason", "params"], [], "chargers[0].reason.params"),
        (["chargers", 0, "plan"], None, "chargers[0].plan"),
        (["chargers", 0, "plan", 0, "current"], DELETE, "chargers[0].plan[0].current"),
        (["chargers", 0, "deadline"], "2026-10-22T07:00:00", "chargers[0].deadline"),
        (["chargers", 0, "reaches_target"], 1, "chargers[0].reaches_target"),
        (["chargers", 0, "foo"], 1, "chargers[0].foo"),
        (["events", 0, "kind"], "boom", "events[0].kind"),
        (["events", 0, "charger_id"], 5, "events[0].charger_id"),
        (["events", 1, "params"], {"a": "b", "c": None}, "events[1].params.c"),
        (["extra"], 1, "extra"),
    ],
    ids=lambda v: str(v),
)
def test_decision_from_dict_rejects_bad_fields_with_their_path(
    path: list[str | int], value: object, field: str
) -> None:
    assert decision_field(edit(encoded_decision(), path, value)) == field


def test_decision_from_dict_rejects_a_non_finite_param_given_directly() -> None:
    data = edit(encoded_decision(), ["events", 0, "params"], {"x": float("nan")})

    assert decision_field(data) == "events[0].params.x"


def test_decision_from_dict_accepts_an_omitted_plan_as_empty() -> None:
    data = edit(encoded_decision(), ["chargers", 0, "plan"], DELETE)

    assert decision_from_dict(data).chargers[0].plan == ()


def test_decision_from_dict_takes_null_or_omitted_params_as_empty() -> None:
    nulled = edit(encoded_decision(), ["chargers", 0, "reason", "params"], None)
    omitted = edit(encoded_decision(), ["events", 0, "params"], DELETE)

    assert decision_from_dict(nulled).chargers[0].reason.params == {}
    assert decision_from_dict(omitted).events[0].params == {}


def test_decision_from_dict_does_not_mutate_its_input() -> None:
    data = encoded_decision()
    before = copy.deepcopy(data)

    decision_from_dict(data)

    assert data == before


def test_decision_from_dict_of_an_empty_decision() -> None:
    empty = Decision((), (), PhaseAmps(0.0, 0.0, 0.0), Status.NORMAL, ())

    assert decision_round_trip(empty) == empty


# --- Round 2: memory (T2) ------------------------------------------------------------

PLUS2 = timezone(timedelta(hours=2))
MINUS530 = timezone(timedelta(hours=-5, minutes=-30))


def rich_memory(level: GuardLevel | None = GuardLevel.GREEN) -> Memory:
    """A memory with every field set, written in assorted offsets."""
    full = ChargerMemory(
        "carport",
        session_start=NOW - timedelta(hours=1),
        car_connected=True,
        session_key="07:00:00|10.0|None|False|None|",
        running=True,
        switched_at=(NOW - timedelta(minutes=20)).astimezone(PLUS2),
        stopped_for_capacity=True,
        selected=True,
        regulated_cap=8,
        under_use_since=(NOW - timedelta(minutes=5)).astimezone(MINUS530),
        full_use_since=NOW - timedelta(minutes=2),
        under_use_stop_at=NOW - timedelta(minutes=40),
        last_target=6.5,
        last_action_at=NOW - timedelta(seconds=40),
        decreased_at=NOW - timedelta(seconds=45),
    )
    return Memory(
        saved_at=NOW,
        chargers=(full, ChargerMemory("pump")),
        level=level,
        green_since=NOW - timedelta(minutes=3),
        emergency_at=(NOW - timedelta(hours=2)).astimezone(PLUS2),
    )


def memory_round_trip(memory: Memory) -> Memory | None:
    data = json.loads(json.dumps(memory_to_dict(memory), allow_nan=False))
    return memory_from_dict(data)


@pytest.mark.parametrize("level", [None, *GuardLevel])
def test_memory_round_trips_through_json(level: GuardLevel | None) -> None:
    memory = rich_memory(level)

    assert memory_round_trip(memory) == memory


def test_memory_without_history_round_trips() -> None:
    memory = Memory(saved_at=NOW)

    assert memory_round_trip(memory) == memory


def test_memory_round_trip_keeps_instants_across_the_fall_back_fold() -> None:
    first = datetime(2026, 10, 25, 2, 30, tzinfo=CPH)
    second = first.replace(fold=1)
    memory = Memory(
        saved_at=second,
        chargers=(ChargerMemory("a", switched_at=first, last_action_at=second),),
        green_since=second,
    )

    restored = memory_round_trip(memory)

    assert restored is not None
    assert restored.saved_at.astimezone(UTC) == second.astimezone(UTC)
    assert restored.green_since is not None
    assert restored.green_since.astimezone(UTC) == second.astimezone(UTC)
    entry = restored.chargers[0]
    assert entry.switched_at is not None
    assert entry.last_action_at is not None
    assert entry.switched_at.astimezone(UTC) == first.astimezone(UTC)
    assert entry.last_action_at.astimezone(UTC) == second.astimezone(UTC)
    assert entry.switched_at.astimezone(UTC) != entry.last_action_at.astimezone(UTC)


def test_memory_to_dict_holds_only_plain_json_types() -> None:
    data = memory_to_dict(rich_memory())

    assert json.loads(json.dumps(data, allow_nan=False)) == data
    assert isinstance(data["chargers"], list)
    assert data["level"] == "green"
    assert str(data["saved_at"]).endswith("+00:00")


def test_memory_to_dict_writes_none_as_null() -> None:
    data = memory_to_dict(Memory(saved_at=NOW))

    assert data["level"] is None
    assert data["green_since"] is None
    assert data["chargers"] == []


def encoded_memory() -> dict[str, Any]:
    return json.loads(json.dumps(memory_to_dict(rich_memory())))


def test_memory_from_dict_accepts_its_own_output() -> None:
    assert memory_from_dict(encoded_memory()) == rich_memory()


@pytest.mark.parametrize(
    "junk", [None, [], "x", 0, False, True, {}, 3.5, [encoded_memory()]]
)
def test_memory_from_dict_returns_none_for_things_that_are_not_a_memory(
    junk: object,
) -> None:
    assert memory_from_dict(junk) is None


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (["saved_at"], DELETE),
        (["saved_at"], "2026-10-21T20:00:00"),
        (["saved_at"], "not a date"),
        (["saved_at"], 12),
        (["saved_at"], None),
        (["extra"], 1),
        (["chargers"], "x"),
        (["chargers"], [None]),
        (["chargers"], None),
        (["chargers"], {"carport": 1}),
        (["chargers", 0, "regulated_cap"], -1),
        (["chargers", 0, "regulated_cap"], 6.5),
        (["chargers", 0, "regulated_cap"], 10**400),
        (["chargers", 0, "regulated_cap"], True),
        (["chargers", 0, "last_target"], "6"),
        (["chargers", 0, "last_target"], float("nan")),
        (["chargers", 0, "last_target"], -1),
        (["chargers", 0, "charger_id"], " "),
        (["chargers", 0, "charger_id"], DELETE),
        (["chargers", 0, "charger_id"], 7),
        (["chargers", 0, "bogus"], 1),
        (["chargers", 0, "switched_at"], "2026-10-21T20:00:00"),
        (["chargers", 0, "running"], 1),
        (["chargers", 0, "session_key"], None),
        (["level"], "purple"),
        (["level"], "GREEN"),
        (["level"], 3),
        (["green_since"], "yesterday"),
        (["emergency_at"], 5),
    ],
    ids=lambda v: str(v)[:30],
)
def test_memory_from_dict_never_raises_and_returns_none_for_malformed_input(
    path: list[str | int], value: object
) -> None:
    assert memory_from_dict(edit(encoded_memory(), path, value)) is None


def test_memory_from_dict_returns_none_for_duplicate_charger_ids() -> None:
    data = encoded_memory()
    data["chargers"].append(copy.deepcopy(data["chargers"][0]))

    assert memory_from_dict(data) is None


def test_memory_from_dict_returns_none_for_a_non_string_key() -> None:
    data: dict[Any, Any] = encoded_memory()
    data[1] = "x"

    assert memory_from_dict(data) is None


def test_memory_from_dict_is_lenient_about_omitted_optional_parts() -> None:
    minimal = memory_from_dict({"saved_at": "2026-10-21T20:00:00Z"})
    entry = memory_from_dict(
        {"saved_at": "2026-10-21T20:00:00Z", "chargers": [{"charger_id": "a"}]}
    )

    assert minimal == Memory(saved_at=NOW + timedelta(hours=2))
    assert entry == Memory(
        saved_at=NOW + timedelta(hours=2), chargers=(ChargerMemory("a"),)
    )


def test_memory_from_dict_does_not_mutate_its_input() -> None:
    data = encoded_memory()
    before = copy.deepcopy(data)

    memory_from_dict(data)

    assert data == before


def test_memory_from_dict_survives_deep_nesting() -> None:
    deep: object = []
    for _ in range(100_000):
        deep = [deep]

    assert memory_from_dict({"saved_at": deep}) is None


# --- Round 2: memory inside snapshot dicts (T2, T3) ------------------------------------


def test_snapshot_round_trips_with_a_memory() -> None:
    snapshot = replace(base_snapshot(), memory=rich_memory())

    data = json.loads(json.dumps(snapshot_to_dict(snapshot)))

    assert snapshot_from_dict(data) == snapshot


@pytest.mark.parametrize("variant", ["absent", "null"])
def test_snapshot_from_dict_takes_a_missing_or_null_memory_as_none(
    variant: str,
) -> None:
    data = encoded()
    if variant == "absent":
        del data["memory"]

    result = snapshot_from_dict(data)

    assert result.memory is None
    assert reset_reason(result) == "missing"


@pytest.mark.parametrize(
    "junk", [{}, False, 0, "", "garbage", [], {"saved_at": "nope"}]
)
def test_snapshot_from_dict_turns_malformed_memory_into_an_invalid_restart(
    junk: object,
) -> None:
    result = snapshot_from_dict(edit(encoded(), ["memory"], junk))

    assert result.memory is not None
    assert reset_reason(result) == "invalid"


def test_the_invalid_memory_marker_survives_a_snapshot_round_trip() -> None:
    first = snapshot_from_dict(edit(encoded(), ["memory"], "garbage"))

    again = snapshot_from_dict(json.loads(json.dumps(snapshot_to_dict(first))))

    assert reset_reason(first) == "invalid"
    assert reset_reason(again) == "invalid"
    assert again == first


def test_a_malformed_memory_does_not_hide_a_bad_charger_field() -> None:
    data = edit(encoded(), ["memory"], "garbage")
    data = edit(data, ["chargers", 0, "max_current"], "sixteen")

    assert error_of(lambda: snapshot_from_dict(data)).field == "chargers[0].max_current"


def test_a_decision_memory_feeds_the_next_snapshot_without_a_reset() -> None:
    first = decide(base_snapshot())
    assert first.memory is not None
    later = NOW + timedelta(minutes=1)
    second = replace(
        base_snapshot(),
        now=later,
        phase_currents=PhaseCurrents(later, 10.0, None, 3.5),
    )
    data = snapshot_to_dict(second)
    data["memory"] = json.loads(json.dumps(memory_to_dict(first.memory)))

    result = decide(snapshot_from_dict(data))

    assert EventKind.MEMORY_RESET not in [event.kind for event in result.events]


def reset_reason(snapshot: Snapshot) -> str | None:
    """The `MEMORY_RESET` reason `decide` reports for a snapshot, None for none."""
    for event in decide(snapshot).events:
        if event.kind is EventKind.MEMORY_RESET:
            return str(event.params["reason"])
    return None


# --- Round 2: memory, guard level and next check in decision dicts (T2) -----------------


def rich_decision(
    level: GuardLevel | None = GuardLevel.YELLOW,
    next_check: datetime | None = NOW + timedelta(minutes=5),
) -> Decision:
    return replace(
        full_decision(),
        memory=rich_memory(),
        guard_level=level,
        next_check=next_check,
    )


@pytest.mark.parametrize("level", [None, *GuardLevel])
def test_decision_round_trips_with_every_guard_level(level: GuardLevel | None) -> None:
    decision = rich_decision(level)

    assert decision_round_trip(decision) == decision


def test_decision_round_trips_a_next_check_across_the_fall_back_fold() -> None:
    moment = datetime(2026, 10, 25, 2, 30, tzinfo=CPH, fold=1)

    restored = decision_round_trip(rich_decision(next_check=moment))

    assert restored.next_check is not None
    assert restored.next_check.astimezone(UTC) == moment.astimezone(UTC)
    assert restored.next_check.utcoffset() == timedelta(hours=1)


def test_decision_round_trips_without_a_next_check() -> None:
    decision = rich_decision(next_check=None)

    assert decision_round_trip(decision).next_check is None


def test_decision_from_dict_takes_omitted_round_two_fields_as_none() -> None:
    data = encoded_decision()
    for key in ("memory", "guard_level", "next_check"):
        del data[key]

    result = decision_from_dict(data)

    assert result.memory is None
    assert result.guard_level is None
    assert result.next_check is None


def encoded_rich_decision() -> dict[str, Any]:
    return json.loads(json.dumps(decision_to_dict(rich_decision())))


@pytest.mark.parametrize(
    ("path", "value", "field"),
    [
        (["memory", "saved_at"], "bad", "memory.saved_at"),
        (["memory", "saved_at"], "2026-10-21T20:00:00", "memory.saved_at"),
        (["memory", "saved_at"], DELETE, "memory.saved_at"),
        (
            ["memory", "chargers", 0, "regulated_cap"],
            -1,
            "memory.chargers[0].regulated_cap",
        ),
        (["memory", "chargers", 1, "charger_id"], " ", "memory.chargers[1].charger_id"),
        (["memory", "chargers", 0, "foo"], 1, "memory.chargers[0].foo"),
        (["memory", "level"], "purple", "memory.level"),
        (["memory", "extra"], 1, "memory.extra"),
        (["memory"], "x", "memory"),
        (["guard_level"], "Green", "guard_level"),
        (["guard_level"], 3, "guard_level"),
        (["next_check"], "2026-10-21T20:00:00", "next_check"),
        (["next_check"], 5, "next_check"),
    ],
    ids=lambda v: str(v),
)
def test_decision_from_dict_reads_the_round_two_fields_strictly(
    path: list[str | int], value: object, field: str
) -> None:
    assert decision_field(edit(encoded_rich_decision(), path, value)) == field


def test_decision_from_dict_rejects_duplicate_memory_charger_ids_with_a_path() -> None:
    data = encoded_rich_decision()
    data["memory"]["chargers"].append(copy.deepcopy(data["memory"]["chargers"][0]))

    assert decision_field(data) == "memory.chargers"


# --- Round 2: settings fields (T2) ---------------------------------------------------------


def settings_data() -> dict[str, Any]:
    return encoded()["settings"]


def test_settings_dict_carries_every_round_two_field() -> None:
    result = snapshot_from_dict(
        edit(
            edit(encoded(), ["settings", "reduction"], "halve"),
            ["settings", "emergency_threshold"],
            41.0,
        )
    ).settings

    assert result.reduction is Reduction.HALVE
    assert result.emergency_threshold == 41.0
    assert result.min_pause == timedelta(minutes=15)
    assert result.max_memory_age == timedelta(minutes=30)


def test_settings_dict_takes_a_null_emergency_threshold_as_off() -> None:
    result = snapshot_from_dict(
        edit(encoded(), ["settings", "emergency_threshold"], None)
    )

    assert result.settings.emergency_threshold is None


def test_settings_dict_accepts_a_zero_green_hold() -> None:
    result = snapshot_from_dict(edit(encoded(), ["settings", "green_hold"], 0))

    assert result.settings.green_hold == timedelta(0)


def test_settings_dict_rounds_a_tiny_green_hold_to_zero() -> None:
    result = snapshot_from_dict(edit(encoded(), ["settings", "green_hold"], 4e-7))

    assert result.settings.green_hold == timedelta(0)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("ramp_period", 4e-7),
        ("ramp_period", 0),
        ("reduction", None),
        ("reduction", "HALVE"),
        ("reduction", 1),
        ("ramp_step", 2.5),
        ("ramp_step", 0),
        ("emergency_threshold", 32.0),
        ("emergency_threshold", "40"),
        ("min_action_interval", -1),
        ("under_use_margin", -0.5),
        ("max_memory_age", 0),
    ],
)
def test_settings_dict_rejects_bad_round_two_values_with_their_path(
    key: str, value: object
) -> None:
    data = edit(encoded(), ["settings", key], value)

    assert error_of(lambda: snapshot_from_dict(data)).field == f"settings.{key}"


def test_main_showcase_runs_and_prints(capsys: pytest.CaptureFixture[str]) -> None:
    showcase()

    output = capsys.readouterr().out
    assert "round trip equal: True" in output
    assert "memory round trip equal: True" in output
    assert "malformed memory decodes to: None" in output
