"""Tests for energy_optimization.ev.guard (T9): the phase-guard level over time."""

from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from energy_optimization.ev import GuardLevel, Memory, Phase, PhaseCurrents, Settings
from energy_optimization.ev.guard import GuardState, advance_guard, instant_level
from energy_optimization.ev.guard import main as showcase

NOW = datetime(2026, 10, 21, 20, 0, tzinfo=UTC)
CPH = ZoneInfo("Europe/Copenhagen")
ALL = frozenset(Phase)
GREEN, YELLOW, RED, EMERGENCY = (
    GuardLevel.GREEN,
    GuardLevel.YELLOW,
    GuardLevel.RED,
    GuardLevel.EMERGENCY,
)


def cfg(**kwargs: object) -> Settings:
    return Settings(32.0, 32.0, 6, "Europe/Copenhagen", **kwargs)  # type: ignore[arg-type]  # test passes through


def reading(
    l1: float | None, l2: float | None = 0.0, l3: float | None = 0.0
) -> PhaseCurrents:
    return PhaseCurrents(NOW, l1, l2, l3)


def mem(**kwargs: object) -> Memory:
    return Memory(saved_at=NOW - timedelta(seconds=30), **kwargs)  # type: ignore[arg-type]  # test passes through


# --- instant_level -------------------------------------------------------------


@pytest.mark.parametrize(
    ("amps", "expected"),
    [
        (0.0, GREEN),
        (29.999, GREEN),
        (30.0, YELLOW),
        (32.0, YELLOW),
        (32.0001, RED),
        (40.0, RED),
        (40.0001, EMERGENCY),
    ],
)
def test_instant_level_boundaries_with_an_emergency_threshold(
    amps: float, expected: GuardLevel
) -> None:
    assert instant_level(reading(amps), ALL, cfg(emergency_threshold=40.0)) is expected


def test_instant_level_tops_out_at_red_without_an_emergency_threshold() -> None:
    assert instant_level(reading(10_000.0), ALL, cfg()) is RED


def test_instant_level_zero_margin_makes_yellow_exactly_the_threshold() -> None:
    settings = cfg(yellow_margin=0.0)

    assert instant_level(reading(32.0), ALL, settings) is YELLOW
    assert instant_level(reading(31.999), ALL, settings) is GREEN


def test_instant_level_a_margin_as_large_as_the_threshold_is_always_yellow() -> None:
    settings = Settings(32.0, 10.0, 6, "Europe/Copenhagen", yellow_margin=10.0)

    assert instant_level(reading(0.0), ALL, settings) is GuardLevel.YELLOW


def test_instant_level_any_one_phase_decides() -> None:
    assert instant_level(reading(10.0, 20.0, 33.0), ALL, cfg()) is RED


def test_instant_level_skips_a_missing_reading_and_unlisted_phases() -> None:
    currents = reading(None, 10.0, 50.0)

    assert instant_level(currents, frozenset({Phase.L1, Phase.L2}), cfg()) is GREEN
    assert instant_level(reading(None, 100.0, 100.0), frozenset({Phase.L1}), cfg()) is (
        GREEN
    )


def test_instant_level_with_no_phases_is_green() -> None:
    assert instant_level(reading(100.0, 100.0, 100.0), frozenset(), cfg()) is GREEN


def test_instant_level_reports_the_worst_phase_when_several_are_high() -> None:
    settings = cfg(emergency_threshold=40.0)

    assert instant_level(reading(31.0, 33.0, 41.0), ALL, settings) is EMERGENCY


# --- advance_guard: green hold -------------------------------------------------


def test_advance_guard_without_memory_starts_the_green_hold_now() -> None:
    state = advance_guard(None, GREEN, NOW, cfg())

    assert state == GuardState(GREEN, NOW, None, False, False)


def test_advance_guard_without_memory_and_no_hold_allows_increases() -> None:
    state = advance_guard(None, GREEN, NOW, cfg(green_hold=timedelta(0)))

    assert state.increases_allowed


@pytest.mark.parametrize(
    ("age", "allowed"),
    [
        (timedelta(seconds=60), True),
        (timedelta(seconds=59, microseconds=999_999), False),
        (timedelta(hours=1), True),
        (timedelta(0), False),
    ],
)
def test_advance_guard_green_hold_boundary(age: timedelta, allowed: bool) -> None:
    state = advance_guard(mem(level=GREEN, green_since=NOW - age), GREEN, NOW, cfg())

    assert state.increases_allowed is allowed
    assert state.green_since == NOW - age


@pytest.mark.parametrize(("instant", "intervened"), [(YELLOW, False), (RED, True)])
def test_advance_guard_yellow_and_red_block_increases(
    instant: GuardLevel, intervened: bool
) -> None:
    memory = mem(level=GREEN, green_since=NOW - timedelta(hours=1))

    state = advance_guard(memory, instant, NOW, cfg())

    assert state == GuardState(instant, None, None, False, intervened)


def test_advance_guard_green_must_be_continuous_across_calls() -> None:
    settings = cfg()
    readings = [GREEN, YELLOW, GREEN, GREEN, GREEN]  # 30 s apart
    memory: Memory | None = None
    states: list[GuardState] = []

    for step, instant in enumerate(readings):
        now = NOW + timedelta(seconds=30 * step)
        state = advance_guard(memory, instant, now, settings)
        states.append(state)
        memory = Memory(
            saved_at=now,
            level=state.level,
            green_since=state.green_since,
            emergency_at=state.emergency_at,
        )

    assert [s.increases_allowed for s in states] == [False] * 4 + [True]
    assert states[3].green_since == NOW + timedelta(seconds=60)
    assert states[1].green_since is None


def test_advance_guard_a_fresh_call_after_a_stale_one_needs_a_new_hold() -> None:
    memory = mem(level=None, green_since=None)

    state = advance_guard(memory, GREEN, NOW, cfg())

    assert state.green_since == NOW
    assert not state.increases_allowed


@pytest.mark.parametrize("past_level", [YELLOW, RED])
def test_advance_guard_ignores_a_green_since_left_by_a_non_green_call(
    past_level: GuardLevel,
) -> None:
    memory = mem(level=past_level, green_since=NOW - timedelta(hours=1))

    state = advance_guard(memory, GREEN, NOW, cfg())

    assert state.green_since == NOW
    assert not state.increases_allowed


def test_advance_guard_measures_the_hold_in_utc_across_the_fall_back_fold() -> None:
    first = datetime(2026, 10, 25, 2, 30, tzinfo=CPH)  # fold=0, 00:30 UTC
    later = first.replace(fold=1)  # 01:30 UTC: a real hour later
    memory = Memory(saved_at=first, level=GREEN, green_since=first)
    settings = cfg(green_hold=timedelta(minutes=30))

    state = advance_guard(memory, GREEN, later, settings)

    assert state.increases_allowed


def test_advance_guard_compares_times_in_other_offsets_in_utc() -> None:
    plus_two = timezone(timedelta(hours=2))
    green_since = (NOW - timedelta(seconds=60)).astimezone(plus_two)
    memory = Memory(saved_at=NOW, level=GREEN, green_since=green_since)

    state = advance_guard(memory, GREEN, NOW, cfg())

    assert state.increases_allowed


# --- advance_guard: emergency latch --------------------------------------------


def test_advance_guard_an_instant_emergency_overrides_a_green_memory() -> None:
    memory = mem(level=GREEN, green_since=NOW - timedelta(hours=1))

    state = advance_guard(memory, EMERGENCY, NOW, cfg(emergency_threshold=40.0))

    assert state == GuardState(EMERGENCY, None, NOW, False, True)


def test_advance_guard_a_second_emergency_refreshes_the_latch_time() -> None:
    earlier = NOW - timedelta(minutes=3)
    memory = mem(level=EMERGENCY, emergency_at=earlier)

    state = advance_guard(memory, EMERGENCY, NOW, cfg(emergency_threshold=40.0))

    assert state.emergency_at == NOW


def test_advance_guard_calm_release_boundary() -> None:
    settings = cfg(emergency_threshold=40.0)
    at = NOW - timedelta(minutes=6)
    on_time = mem(
        level=EMERGENCY, green_since=NOW - timedelta(minutes=5), emergency_at=at
    )
    late = mem(
        level=EMERGENCY,
        green_since=NOW - timedelta(minutes=5) + timedelta(seconds=1),
        emergency_at=at,
    )

    released = advance_guard(on_time, GREEN, NOW, settings)
    held = advance_guard(late, GREEN, NOW, settings)

    assert released == GuardState(GREEN, NOW - timedelta(minutes=5), None, True, False)
    assert held == GuardState(
        EMERGENCY, NOW - timedelta(minutes=5) + timedelta(seconds=1), at, False, True
    )


@pytest.mark.parametrize("instant", [YELLOW, RED])
def test_advance_guard_a_non_green_call_keeps_the_latch_and_restarts_the_calm(
    instant: GuardLevel,
) -> None:
    at = NOW - timedelta(minutes=8)
    memory = mem(
        level=EMERGENCY, green_since=NOW - timedelta(minutes=4), emergency_at=at
    )

    state = advance_guard(memory, instant, NOW, cfg(emergency_threshold=40.0))

    assert state == GuardState(EMERGENCY, None, at, False, True)


def test_advance_guard_the_first_green_call_after_an_emergency_starts_the_calm() -> (
    None
):
    at = NOW - timedelta(minutes=1)
    memory = mem(level=EMERGENCY, green_since=None, emergency_at=at)

    state = advance_guard(memory, GREEN, NOW, cfg(emergency_threshold=40.0))

    assert state == GuardState(EMERGENCY, NOW, at, False, True)


def test_advance_guard_no_calm_releases_at_once_but_the_green_hold_still_applies() -> (
    None
):
    memory = mem(level=EMERGENCY, green_since=None, emergency_at=NOW)
    settings = cfg(emergency_threshold=40.0, emergency_calm=timedelta(0))

    state = advance_guard(memory, GREEN, NOW, settings)

    assert state == GuardState(GREEN, NOW, None, False, False)


def test_advance_guard_a_hold_longer_than_the_calm_still_blocks_after_release() -> None:
    memory = mem(
        level=EMERGENCY,
        green_since=NOW - timedelta(minutes=5),
        emergency_at=NOW - timedelta(minutes=9),
    )
    settings = cfg(
        emergency_threshold=40.0,
        green_hold=timedelta(minutes=10),
        emergency_calm=timedelta(minutes=5),
    )

    state = advance_guard(memory, GREEN, NOW, settings)

    assert state.level is GREEN
    assert not state.increases_allowed


def test_advance_guard_measures_the_calm_in_utc_across_the_fold() -> None:
    first = datetime(2026, 10, 25, 2, 30, tzinfo=CPH)
    later = first.replace(fold=1)
    memory = Memory(
        saved_at=first, level=EMERGENCY, green_since=first, emergency_at=first
    )
    settings = cfg(emergency_threshold=40.0, emergency_calm=timedelta(minutes=30))

    state = advance_guard(memory, GREEN, later, settings)

    assert state.level is GREEN
    assert state.emergency_at is None


# --- advance_guard: stale data -------------------------------------------------


def test_advance_guard_stale_with_a_latched_emergency_stays_in_emergency() -> None:
    at = NOW - timedelta(minutes=2)
    memory = mem(
        level=EMERGENCY, green_since=NOW - timedelta(minutes=4), emergency_at=at
    )

    state = advance_guard(memory, None, NOW, cfg(emergency_threshold=40.0))

    assert state == GuardState(EMERGENCY, None, at, False, True)


@pytest.mark.parametrize(
    "memory",
    [None, mem(level=GREEN, green_since=NOW - timedelta(hours=1)), mem(level=None)],
)
def test_advance_guard_stale_without_a_latch_leaves_increases_to_the_fallback(
    memory: Memory | None,
) -> None:
    state = advance_guard(memory, None, NOW, cfg())

    assert state == GuardState(None, None, None, True, False)


def test_advance_guard_stale_without_a_latch_carries_the_emergency_time() -> None:
    at = NOW - timedelta(hours=1)

    state = advance_guard(mem(level=None, emergency_at=at), None, NOW, cfg())

    assert state == GuardState(None, None, at, True, False)


def test_advance_guard_a_stale_call_restarts_the_calm_progress() -> None:
    settings = cfg(emergency_threshold=40.0)
    latched = mem(
        level=EMERGENCY,
        green_since=NOW - timedelta(minutes=4),
        emergency_at=NOW - timedelta(minutes=5),
    )

    stale = advance_guard(latched, None, NOW, settings)
    after = advance_guard(
        Memory(
            saved_at=NOW,
            level=stale.level,
            green_since=stale.green_since,
            emergency_at=stale.emergency_at,
        ),
        GREEN,
        NOW + timedelta(minutes=2),
        settings,
    )

    assert after.level is EMERGENCY
    assert after.green_since == NOW + timedelta(minutes=2)


def test_advance_guard_does_not_change_its_arguments() -> None:
    memory = mem(level=GREEN, green_since=NOW - timedelta(minutes=2))
    before = memory

    first = advance_guard(memory, GREEN, NOW, cfg())
    second = advance_guard(memory, GREEN, NOW, cfg())

    assert memory == before
    assert first == second


def test_main_showcase_runs_and_prints(capsys: pytest.CaptureFixture[str]) -> None:
    showcase()

    output = capsys.readouterr().out
    assert "red" in output
    assert "stale" in output
