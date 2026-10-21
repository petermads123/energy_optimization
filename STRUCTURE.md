# Structure

Map of everything in this repo. Loaded into context at the start of every session via the
`@STRUCTURE.md` import in `CLAUDE.md`, so it is what Claude uses to find things without
searching.

## Keeping this file current

Update it **in the same change** that causes any of the following:

- a module is added, deleted, renamed or moved
- a public function or class is added or removed
- a public signature changes (parameters, defaults, return type)
- a module's purpose changes

Private helpers (names starting with `_`) are intentionally left out. They are
implementation detail, and listing them is what makes a file like this rot.

The stop gate (`.claude/hooks/stop_gate.py`) cross-checks the module paths named here
against the `.py` files on disk and blocks on a mismatch. It only sees file-level drift —
signature drift is on you, or run the `structure-auditor` subagent.

That covers `src/`, `tests/` and `.claude/hooks/`. The hooks are documented here with full
signature tables, type-checked like the package (`[tool.mypy] files` names all three) and
importable from the suite (`[tool.pytest.ini_options] pythonpath` names `src` and
`.claude/hooks`, so a test imports a hook by module name the same way a sibling hook does
at runtime), so they are held to the same standard despite not being installable.

## Growth

While the package is flat, keep everything here. Once it has subpackages, keep the tree and
one line per subpackage in this file, and move per-subpackage detail into
`.claude/rules/structure-<subpackage>.md` with `paths: ["<subpackage>/**"]` so it loads only
when Claude works in that subpackage. Split rather than delete — there is no length limit
here, but everything in this file is in context every session.

`ev/` is the exception for now: its tables stay here until a round that may touch `.claude/`
moves them, because the `structure-auditor` and the stop gate read only this file.

## Tree

```
src/                    everything installable; nothing outside it is packaged
  energy_optimization/  the package itself
    ev/                 smart EV charging decision core (see its modules below)
tests/                  pytest suite, one test_<module>.py per module
development/            one folder per branch, one file per round: the pipeline's state
.claude/                Claude Code configuration: rules, skills, agents, hooks
.vscode/                editor config (Ruff as formatter, format on save)
pyproject.toml          packaging, Ruff, mypy and pytest configuration
DEVELOPMENT.md          development-side open questions and things to fix later (see CLAUDE.md)
README.md               human setup guide
CLAUDE.md               routing map for Claude
STRUCTURE.md            this file
```

## Package: `src/energy_optimization/`

### `src/energy_optimization/__init__.py`

Package entry point. Nothing is re-exported at the root: the EV charging core is the
subpackage `energy_optimization.ev`, imported as `from energy_optimization.ev import decide`.

Every package directory under `src/`, including every subpackage added later, needs one of
these. The stop gate blocks on a directory of modules without it: it is not a package, so
it will not install.

### Subpackage `src/energy_optimization/ev/`

The smart EV charging decision core: a pure function from one complete snapshot (time,
prices, chargers, sessions, measured phase currents, settings, the previous decision's
memory) to one decision (ordered actions, a plan and a reason per charger, available amps,
status, events, the memory for the next call, the phase-guard level, the next check). No
I/O, never reads the clock, standard library only. Memory missing, malformed, out of date
or future-dated is a conservative start (nothing increased), never an error. Every public type is a frozen dataclass; input
types validate themselves on construction and raise `ConfigError` naming the field.
Wherever output depends on a set of phases, phases are iterated in `Phase` order
(L1, L2, L3), never in `frozenset` order. Every module is runnable with
`python -m energy_optimization.ev.<module>` and ends in a `main()` showcase.

Dependency order: `model` is the leaf; `result` uses `model`; `quarters` uses `model`;
`guard` uses `model`; `regulation` uses `model`; `planning` uses `model`, `quarters`,
`result`; `allocation` uses `model`; `decide` uses all of those; `codec` uses `model` and
`result`.

### `src/energy_optimization/ev/__init__.py`

Re-exports the public API with `__all__`: every public type in `model` and `result` (except
the `Params` alias, importable from `result`), `decide`, and the six codec functions
(snapshot, decision and memory, each way). The helpers in `quarters`, `planning`,
`allocation`, `guard` and `regulation` are importable from their own modules.

### `src/energy_optimization/ev/model.py`

Input types and their validation. `Phase`, `ChargerKind`, `Reduction`, `GuardLevel` are `StrEnum`s; the dataclasses
below are frozen and check themselves in `__post_init__`. Tuple fields accept any sequence
and become tuples. Numeric fields reject `bool` and non-finite floats; float fields accept
ints.

| Signature | Description |
|---|---|
| `class Phase(StrEnum)`: `L1`, `L2`, `L3` | A phase of the grid connection. |
| `class ChargerKind(StrEnum)`: `ADJUSTABLE`, `ON_OFF`, `FIXED` | What a charger can do. |
| `class ConfigError(ValueError)`; `__init__(self, field: str, message: str) -> None`; attributes `field`, `message`; `str(err) == f"{field}: {message}"` | Invalid input, naming the field. |
| `Charger(id: str, phases: frozenset[Phase], kind: ChargerKind, min_current: int \| None = None, max_current: int \| None = None, assumed_current: float \| None = None, fallback_current: int \| None = None)` | A charge point; the fields that apply depend on `kind`. |
| `ChargerState(charger_id: str, car_connected: bool, current_setting: float, paused: bool, measured_power_w: float \| None = None, available: bool = True)` | A charger's state now. |
| `Session(charger_id: str, ready_time: time, energy_need_kwh: float \| None = None, soc_percent: float \| None = None, target_soc_percent: float \| None = None, battery_capacity_kwh: float \| None = None, delivered_kwh: float = 0.0, charge_now: bool = False, smart_charging: bool = True, user_current_limit: int \| None = None, plugged_in_at: datetime \| None = None)` | The car on one charger; `ready_time` is naive local time. |
| `PriceInterval(start: datetime, end: datetime, price: float)` | One priced interval on quarter boundaries. |
| `PriceSeries(intervals: tuple[PriceInterval, ...], updated_at: datetime)` | The price series; intervals must not overlap. |
| `class Reduction(StrEnum)`: `TO_USE`, `HALVE` | How an under-use cut is computed. |
| `class GuardLevel(StrEnum)`: `GREEN`, `YELLOW`, `RED`, `EMERGENCY` | The phase guard's level. |
| `PhaseCurrents(measured_at: datetime, l1: float \| None, l2: float \| None, l3: float \| None)`; `get(self, phase: Phase) -> float \| None` | Measured current per phase for the whole connection. |
| `ChargerMemory(charger_id: str, session_start: datetime \| None = None, car_connected: bool = False, session_key: str = "", running: bool = False, switched_at: datetime \| None = None, stopped_for_capacity: bool = False, selected: bool = False, regulated_cap: int \| None = None, under_use_since: datetime \| None = None, full_use_since: datetime \| None = None, under_use_stop_at: datetime \| None = None, last_target: float \| None = None, last_action_at: datetime \| None = None, decreased_at: datetime \| None = None)` | What one charger carries between calls; datetimes aware. |
| `Memory(saved_at: datetime, chargers: tuple[ChargerMemory, ...] = (), level: GuardLevel \| None = None, green_since: datetime \| None = None, emergency_at: datetime \| None = None)`; `charger(self, charger_id: str) -> ChargerMemory \| None` | Everything carried between calls; charger ids unique. |
| `Settings(phase_budget: float, phase_threshold: float, fallback_current: int, timezone: str, voltage: float = 230.0, min_block_quarters: int = 2, price_cap: float \| None = None, default_energy_need_kwh: float = 30.0, max_price_age: timedelta = timedelta(minutes=60), max_measurement_age: timedelta = timedelta(seconds=60), emergency_threshold: float \| None = None, emergency_calm: timedelta = timedelta(minutes=5), yellow_margin: float = 2.0, green_hold: timedelta = timedelta(seconds=60), deadband: float = 2.0, ramp_step: int = 2, ramp_period: timedelta = timedelta(minutes=5), under_use_period: timedelta = timedelta(minutes=15), reduction: Reduction = Reduction.TO_USE, under_use_margin: float = 1.0, min_pause: timedelta = timedelta(minutes=15), hysteresis: timedelta = timedelta(minutes=30), resume_probe: timedelta = timedelta(minutes=60), min_action_interval: timedelta = timedelta(seconds=30), max_memory_age: timedelta = timedelta(minutes=30))` | Installation settings; budget, threshold, fallback and timezone are required; the rest govern behaviour over time (emergency off when unset). |
| `Snapshot(now: datetime, settings: Settings, chargers: tuple[Charger, ...], states: tuple[ChargerState, ...], sessions: tuple[Session, ...], prices: PriceSeries, phase_currents: PhaseCurrents \| None = None, memory: Memory \| None = None)` | One call's input; cross-checks ids between chargers, states and sessions (memory entries for unknown chargers are allowed and dropped by `decide`). |
| `main() -> None` | Showcase: build a snapshot carrying a memory, then trigger a `ConfigError`. |

### `src/energy_optimization/ev/result.py`

Output types. No logic beyond `PhaseAmps.get` and the tuple normalisation in `ChargerDecision` and `Decision`.

| Signature | Description |
|---|---|
| `type Params = dict[str, str \| float \| int \| bool]` | Parameter values carried by `Reason.params` and `Event.params`. |
| `class Stage(StrEnum)`: `DOWN`, `UP` | Which phase of execution an action belongs to. |
| `class ActionKind(StrEnum)`: `SET_CURRENT`, `PAUSE`, `RESUME` | What to do to a charger. |
| `Action(charger_id: str, kind: ActionKind, target_current: float, stage: Stage)` | One change with an absolute target. |
| `class ReasonCode(StrEnum)`: `CHARGE_NOW`, `IN_PLAN`, `READY_TIME_PASSED`, `PLAN_COMPLETED`, `PRICES_STALE`, `PLANNED_LATER`, `WAITING_FOR_HIGHER_PRIORITY`, `WAITING_FOR_CAPACITY`, `TARGET_REACHED`, `BELOW_MINIMUM`, `SMART_CHARGING_OFF`, `FIXED`, `UNAVAILABLE`, `NO_CAR`, `NO_SESSION`, `MINIMUM_PAUSE`, `UNDER_USE`, `EMERGENCY` | Stable reason codes; values are the lower-case names. |
| `Reason(code: ReasonCode, text: str, params: Params = field(default_factory=dict))` | Why a charger got what it got. |
| `PlannedQuarter(start: datetime, price: float \| None, current: float)` | One planned quarter; `price` None means unpriced. |
| `ChargerDecision(charger_id: str, allocation: float, priority: int \| None, reason: Reason, plan: tuple[PlannedQuarter, ...] = (), remaining_need_kwh: float \| None = None, deadline: datetime \| None = None, expected_finish: datetime \| None = None, reaches_target: bool \| None = None)` | Everything decided for one charger. |
| `class Status(StrEnum)`: `EMERGENCY`, `NORMAL`, `LIMITED_BY_BUDGET`, `LIMITED_BY_PHASE`, `WAITING_FOR_CAPACITY`, `PHASE_DATA_STALE`, `PRICES_STALE` | Overall status. |
| `class EventKind(StrEnum)`: `TARGET_UNREACHABLE`, `PRICE_CAP_EXCEEDED`, `CHARGE_NOW_NEED_COVERED`, `PHASE_DATA_STALE`, `PRICES_STALE`, `MEMORY_RESET`, `PHASE_GUARD_INTERVENED` | Notification-worthy events. |
| `Event(kind: EventKind, text: str, charger_id: str \| None = None, params: Params = field(default_factory=dict))` | One event. |
| `PhaseAmps(l1: float, l2: float, l3: float)`; `get(self, phase: Phase) -> float` | Amps per phase. |
| `Decision(actions: tuple[Action, ...], chargers: tuple[ChargerDecision, ...], available: PhaseAmps, status: Status, events: tuple[Event, ...], memory: Memory \| None = None, guard_level: GuardLevel \| None = None, next_check: datetime \| None = None)` | One call's answer; `chargers` in snapshot charger order; `decide` always sets `memory`, `guard_level` is None under stale measurements unless an emergency is latched, `next_check` is in the settings timezone. |
| `main() -> None` | Showcase: build a small decision by hand. |

### `src/energy_optimization/ev/quarters.py`

The quarter-hour grid. All arithmetic in UTC; datetimes sharing a `ZoneInfo` are never
compared directly because that ignores `fold`.

| Signature | Description |
|---|---|
| `QUARTER: timedelta` | The planning step, 15 minutes. |
| `floor_to_quarter(moment: datetime) -> datetime` | Start of the quarter containing an aware moment, in UTC; `ValueError` if naive. |
| `next_occurrence(ready_time: time, after: datetime, timezone: str) -> datetime` | First moment strictly after `after` at which local time is `ready_time`, in UTC; a gap time shifts forward, a fold time is the first occurrence unless `after` is already past it (then the second); `ready_time.fold` is ignored; `ValueError` if `after` is naive. |
| `quarter_starts(start: datetime, end: datetime) -> list[datetime]` | UTC quarter starts from the quarter containing `start` while before `end`; `ValueError` if either is naive. |
| `quarter_prices(series: PriceSeries) -> dict[datetime, float]` | Every interval split into quarters, keyed by UTC start. |
| `prices_fresh(series: PriceSeries, now: datetime, max_age: timedelta) -> bool` | `abs(now - updated_at) <= max_age`; `ValueError` if `now` is naive. |
| `main() -> None` | Showcase: deadlines and quarter counts across the 2026 DST nights. |

### `src/energy_optimization/ev/planning.py`

Remaining need and cheapest-block planning with shared capacity, price cap and unpriced
quarters.

| Signature | Description |
|---|---|
| `class NeedSource(StrEnum)`: `ENERGY`, `SOC`, `DEFAULT` | Where a remaining need came from. |
| `remaining_need_kwh(session: Session, default_need_kwh: float) -> tuple[float, NeedSource]` | Remaining need, clipped at 0, and its source. |
| `energy_per_quarter_kwh(current: float, phase_count: int, voltage: float) -> float` | Energy delivered in one quarter. |
| `PlanRequest(charger_id: str, phases: frozenset[Phase], min_current: float, max_current: float, need_kwh: float, deadline: datetime, skip_current: bool = False)` | One session to plan; equal min and max means all-or-nothing; `skip_current` closes the quarter containing `now` to it (it is held off for now). |
| `SessionPlan(charger_id: str, quarters: tuple[PlannedQuarter, ...], planned_kwh: float, reachable: bool, price_cap_exceeded: bool, uses_unpriced: bool)` | One session's plan, quarters in time order. |
| `plan_sessions(requests: Sequence[PlanRequest], now: datetime, prices: Mapping[datetime, float], capacity: Mapping[Phase, float], voltage: float, min_block_quarters: int, price_cap: float \| None, timezone: str, first: Sequence[str] = ()) -> dict[str, SessionPlan]` | Plan every request against shared per-phase capacity, most urgent first. The sessions named in `first`, in the order they contest it, keep the quarter containing `now` if their own plan has it there (each planned alone on what the earlier ones left of it); everything else, their later quarters included, is planned most urgent first on what remains, so the plans together never book more than `capacity`. A bare string is a `TypeError`. |
| `main() -> None` | Showcase: two cars on a shared phase; a default need. |

### `src/energy_optimization/ev/guard.py`

The phase guard over time. One reading is classified green / yellow / red / emergency;
the level is then carried across calls through `Memory`: increases need the phases
continuously green for the green hold, red and emergency count as an intervention, and an
emergency latches until the calm period has passed (it survives stale data). Under stale
or missing measurements increases stay allowed, because round 1's fallback cap governs
them, unless an emergency is latched.

| Signature | Description |
|---|---|
| `instant_level(currents: PhaseCurrents, phases: frozenset[Phase], settings: Settings) -> GuardLevel` | Level of one reading over `phases`; a missing value is skipped. |
| `GuardState(level: GuardLevel \| None, green_since: datetime \| None, emergency_at: datetime \| None, increases_allowed: bool, intervened: bool)` | The guard after one call. |
| `advance_guard(memory: Memory \| None, instant: GuardLevel \| None, now: datetime, settings: Settings) -> GuardState` | One step of the guard over time. |
| `main() -> None` | Showcase: a red reading, then green calls until increases are allowed, then a stale call that leaves increases to the fallback cap. |

### `src/energy_optimization/ev/regulation.py`

Regulation of one adjustable charger to what its car actually draws: after a whole
under-use period the charger is cut to its measured draw plus a margin (or halved), a cut
below the minimum becomes 0, each whole ramp period of full use then raises the cap one
step, and a charger cut to 0 is offered current again after the resume probe or when the
session is reset. A cut only ever lowers (never above the setting, the existing cap or the
maximum) and the ramp never lowers a cap or leaves one between 0 and the minimum; a cap
the charger cannot run at, or a 0 with no stop time, is taken as a stop and probed from
the first sight. On/off chargers and chargers without a power reading are never
regulated.

| Signature | Description |
|---|---|
| `Regulation(regulated_cap: int \| None, under_use_since: datetime \| None, full_use_since: datetime \| None, under_use_stop_at: datetime \| None, cut: bool)` | One charger's regulation after one call; cap 0 means stopped for under-use. |
| `regulate(previous: ChargerMemory \| None, charger: Charger, state: ChargerState, reset: bool, now: datetime, settings: Settings) -> Regulation` | One step of under-use cut, ramp and resume probe. |
| `main() -> None` | Showcase: a cut, a cut to zero, a steady draw inside the deadband, a ramp step. |

### `src/energy_optimization/ev/allocation.py`

Phase capacity, draw and reservations, measurement freshness and max-min-fair sharing.

| Signature | Description |
|---|---|
| `Claim(charger_id: str, phases: frozenset[Phase], min_current: float, max_current: float)` | One charger asking for current; equal min and max means all-or-nothing. |
| `share(claims: Sequence[Claim], capacity: Mapping[Phase, float]) -> dict[str, float]` | Share capacity among claims given in ranking order: 0/min selection, then max-min-fair 1 A steps. |
| `measurements_fresh(currents: PhaseCurrents \| None, now: datetime, max_age: timedelta, phases: frozenset[Phase]) -> bool` | Measurement present, recent and complete for `phases`. |
| `present_draw(charger: Charger, state: ChargerState, voltage: float) -> float` | What a charger is taken to draw now on each phase, for the phase guard: 0 when paused or without a car, the smaller of setting and measured draw otherwise; a fixed or unavailable charger counts at its measured draw (its reservation when power is missing), the rest of its reservation being held back by `decide`. |
| `reserved_current(charger: Charger, state: ChargerState, voltage: float) -> float` | What a fixed or unavailable charger is reserved at on each phase. |
| `phase_capacity(snapshot: Snapshot, fresh: bool, settling: frozenset[str] = frozenset()) -> dict[Phase, float]` | Per phase, budget intersected with the phase guard when `fresh`; may be negative. A charger in `settling` (sent a decrease a moment ago) counts at its measured draw instead of the smaller of setting and measured, provided it has a car and a power reading. |
| `main() -> None` | Showcase: sharing on mixed phases and on a tight phase. |

### `src/energy_optimization/ev/decide.py`

The decision core: memory usability, classification, regulation, priority tiers with
anti-flapping (minimum pause, hysteresis; in the planned tier the planner lets the sessions that
contest a place keep the current quarter, in the ranked tier's order, and plans every later
quarter most urgent first; a charger held off by the minimum pause claims nothing of the
current quarter; a running charger the plan still drops for capacity is a capacity stop held
off by the minimum pause), planning,
sharing, the lowering steps (emergency, no increases, pacing), actions with in-flight
suppression, reasons, status, events, the new memory and the next check. A command sent
within the action interval counts as where the charger is (it may not have landed), so
an emergency, a smaller share or a pacing hold is judged against it; an unplug keeps
what the minimum pause needs; the next check is never later than the new memory's
expiry; all arithmetic is in UTC and a time no UTC date can hold is an invalid memory.

| Signature | Description |
|---|---|
| `decide(snapshot: Snapshot) -> Decision` | Decide what every charger should be set to now; the decision's `memory` goes into the next snapshot. |
| `main() -> None` | Showcase: three chargers and two sessions, a follow-up call fed with the memory, then the same snapshot without phase measurements. |

### `src/energy_optimization/ev/codec.py`

Snapshot, decision and memory to and from plain JSON-compatible dicts. Keys are field names, enums
go by value, datetimes are ISO 8601 with offset, `time` is `time.isoformat()`, `timedelta`
is seconds, phase sets are sorted lists. A snapshot's `memory` that cannot be decoded
does not fail the snapshot: `decide` sees it as invalid and starts conservatively. Inside a
decision the memory is decoded strictly, like every other field. Decoding rejects missing, unknown and wrongly
typed fields with a `ConfigError` whose `field` is a dotted path such as
`chargers[1].max_current`.

| Signature | Description |
|---|---|
| `snapshot_to_dict(snapshot: Snapshot) -> dict[str, object]` | Snapshot to a plain dict. |
| `snapshot_from_dict(data: Mapping[str, object]) -> Snapshot` | Dict to snapshot; `ConfigError` with a dotted path on bad input, except that a malformed `memory` becomes the invalid-memory marker. |
| `decision_to_dict(decision: Decision) -> dict[str, object]` | Decision to a plain dict. |
| `decision_from_dict(data: Mapping[str, object]) -> Decision` | Dict to decision; `ConfigError` with a dotted path on bad input, including bad `memory`. |
| `memory_to_dict(memory: Memory) -> dict[str, object]` | Memory to a plain dict. |
| `memory_from_dict(data: object) -> Memory \| None` | Dict to memory; never raises, anything malformed gives None. |
| `main() -> None` | Showcase: snapshot to JSON and back; memory to JSON and back, and malformed memory decoding to None; a rejected bad field. |

## Tests: `tests/`

All tests live here and nowhere else — `testpaths = ["tests"]` in `pyproject.toml` means
`pytest` collects nothing outside this directory, and the stop gate blocks on a test file
found anywhere else.

### `tests/test_ev_model.py`

Covers `src/energy_optimization/ev/model.py` (T1): every invalid case in A1 for every type, direct construction,
asserting the `ConfigError.field` — a missing budget, threshold or fallback passed as
`None`, bools and non-finite or huge numbers, fields that do not belong to a kind,
duplicate phases given as a list, an unloadable timezone, naive timestamps, overlapping or
misaligned price intervals (judged in UTC, including across the fall-back fold), snapshot
cross-links — plus the accepted boundaries, normalisation to tuples, ints and floats, and
the `main` showcase. Round 2 (T1) adds the new `Settings` fields (every span, margin,
step and the emergency threshold against the phase threshold), `ChargerMemory` (every
time field, flags, cap and last target, session key), `Memory` (ids, level, times,
lookup) and `Snapshot.memory`.

### `tests/test_ev_result.py`

Covers `src/energy_optimization/ev/result.py`: the exact enum member sets and lower-case values, `PhaseAmps.get`,
the defaults and tuple normalisation of the output dataclasses, frozenness, and the `main`
showcase.

### `tests/test_ev_quarters.py`

Covers `src/energy_optimization/ev/quarters.py` (T11): `floor_to_quarter` in UTC including the fold,
`next_occurrence` across both 2026 Copenhagen DST nights (gap shifts forward, fold gives the
first occurrence and then the second, `fold` on the ready time ignored), the 92- and
100-quarter days, `quarter_starts` and `prices_fresh` compared in UTC with naive input
refused, `quarter_prices` splitting, and the `main` showcase.

### `tests/test_ev_planning.py`

Covers `src/energy_optimization/ev/planning.py` (T8-T10, T12): `remaining_need_kwh` and `energy_per_quarter_kwh`
tables, the cheapest-block planner against a brute-force optimum on hand-checked series,
the finishing rule, the price-cap precedence and boundary, unpriced quarters used last and
latest first, shared capacity with the least-slack session first, the sessions named in
`first` keeping only the current quarter (in the order given; never overbooking, a bare
string refused, a `skip_current` request closed to it), whole-amp flooring, a
zero `max_current` as unreachable, DST horizons, and a `hypothesis` property over generated
requests; and the `main` showcase.

### `tests/test_ev_allocation.py`

Covers `src/energy_optimization/ev/allocation.py` (T4, T7, T13, T14): `share` (zero-or-min in ranking order,
max-min fairness on mixed phases, all-or-nothing claims, tolerance, empty and negative
capacity, purity), `measurements_fresh` boundaries, `reserved_current`, `present_draw`
(smaller of setting and measured, paused, no car, fixed at its measured draw) and
`phase_capacity` (negative values, missing phases, fixed reservations); and the `main`
showcase. Round 2 (T11) adds `phase_capacity` with `settling`: the measured draw of a
charger still ramping down, a paused car still drawing, no car or no reading, three
phases, and the cases where it is ignored (stale, unknown id, fixed, unavailable).

### `tests/test_ev_decide.py`

Covers `src/energy_optimization/ev/decide.py` (T3, T5-T7, T9, T13-T18, T20): doc scenarios through `decide`, the
priority tiers and reason codes, reservations and smart charging off, the fallback table,
actions and their order, events, expected finish and `reaches_target`, DST nights, the
float-residue and `user_current_limit=0` regressions, determinism across hash seeds, the
standard-library-only AST scan, the 100 ms performance check, and a `hypothesis` property
(T17) whose invariants are computed independently of the module's helpers; and the `main`
showcase. Its `snap` helper hands every snapshot a warm memory (saved a minute ago, green
for an hour, no charger history) so the round-1 scenarios run in steady state instead of
the conservative start.

Round 2 adds multi-call scenarios that feed each decision's memory into the next
snapshot (T3-T13): the conservative start and every memory-usability boundary, times no
UTC date can hold, huge duration settings; minimum pause (including across an unplug and
charge now), selection hysteresis and minimum on-time in both the tier ranking and the
planned tier (a started charger kept against a more urgent newcomer without starving it of
later quarters, only the contender that takes the place being more than the hysteresis
earlier, a capacity stop held off by the minimum pause and claiming nothing meanwhile, plans
that never book more than the phase holds, a price-driven stop that is not a capacity
stop), an in-flight resume; regulation through `decide` (a cut freeing current,
a stop, the probe, a session change or re-plug clearing it, ramp back, never-regulated
chargers, smart charging off); the guard levels (yellow, red, green hold, stale data,
emergency and its calm period, emergency countermanding an in-flight resume); pacing,
in-flight suppression and settling; `next_check` for every source, the earliest winning,
the memory expiry clamp and UTC arithmetic across the fold; the session start taken from
memory. Two `hypothesis` properties (T15) run sequences of calls feeding memory forward and
check, independently of the module, the budget and amended phase-guard bounds, legal
currents, no increase in yellow, red or a restart, the green hold, pacing, the
minimum pause, emergency, action existence and order against the command in flight, and
the memory round trip, and that no plan books more than a phase holds; one over arbitrary
installations, one over plain installations where every timer can fire, and one over crowded
installations with memory fed forward that checks only the plans. The 100 ms check also runs
with a fully populated memory and with ten running chargers whose plan puts them later.

### `tests/test_ev_codec.py`

Covers `src/energy_optimization/ev/codec.py` (T2, T19): JSON round trips of snapshots and decisions (negative
prices, odd offsets, `None` measurements, every enum, parameter types kept), plain-type
output, and rejection of missing, unknown, wrongly typed, huge, null-on-defaulted and
invalid fields with the exact dotted path; and the `main` showcase. Round 2 (T2) adds
`memory_to_dict` / `memory_from_dict` (every field, every level, offsets and the fall-back
fold compared as instants, plain types, none-for-anything-malformed including deep
nesting), the `memory` key in snapshot dicts (absent, null, malformed becoming the invalid
marker that survives a round trip, a bad charger field still reported), the new decision
keys read strictly with dotted paths, and the new settings fields.

### `tests/test_ev_guard.py`

Covers `src/energy_optimization/ev/guard.py` (round 2 T9): `instant_level` at every
boundary (just under, at and just over the yellow, red and emergency limits, no emergency
threshold, zero margin, missing and unlisted phases, no phases) and `advance_guard` over
call sequences — the green hold at its boundary, yellow and red blocking increases, green
having to be continuous, a green run ended by a non-green memory, the emergency latch and
calm release at their boundary (zero calm, hold longer than calm, a non-green call
restarting it), stale data with and without a latch, and times compared in UTC across the
fold and in other offsets; and the `main` showcase.

### `tests/test_ev_regulation.py`

Covers `src/energy_optimization/ev/regulation.py` (round 2 T6, T8): the concept's own
scenarios (a cut to draw plus margin, a cut below the minimum, the deadband, a ramp step,
the probe), the boundaries of the under-use, ramp and probe periods and of the three
zones, both reduction methods, a steady draw after a cut neither ramped nor cut again over
several calls, a held setting neither raising nor lowering the cap, a cut that never
raises (deadband below the margin, an existing cap), a cap between 0 and the minimum or a
0 with no stop time treated as a stop, reset, on/off and fixed chargers, missing power,
paused and no-car calls, and times compared in UTC; and the `main` showcase.

### `tests/test_guard_git.py`

Covers `.claude/hooks/guard_git.py` — every public function plus `main` end to end against a
throwaway repository, over both Bash and PowerShell command forms — and is the regression
suite for the quoting defect its parser was rebuilt to fix. One test asserts a documented
miss rather than a fix: `sudo -u me git push` is allowed, because only options are skipped
after a wrapper and never a bare word.

### `tests/test_plan_state.py`

Covers `.claude/hooks/plan_state.py`: every public function, the `Plan` properties either
side of `GATE_FROM_STEP`, and the git helpers against a throwaway repository.

### `tests/test_stop_gate.py`

Covers `.claude/hooks/stop_gate.py`: every public function and `main`'s paths through the
gate. `gate_failures`, `enforce` and `main` are driven through a monkeypatched `venv_tool`,
`capture` and checks rather than real executables: running them for real would invoke Ruff,
mypy and `pytest` from inside `pytest`, and stub executables would need a shell script on
POSIX and an `.exe` on Windows.


## Plans: `development/`

One folder per branch, one numbered file per round inside it, created at the close of step
1 from `TEMPLATE.md` and carried through all ten steps:

```
development/
  TEMPLATE.md                     copied for each new round; never itself active
  feat/csv-export/                the folder is the branch name, so it nests one level
    01-csv-export.md              round 1, shipped
    02-streaming-writer.md        round 2, opened from round 1's recommendation R2
```

Each file holds the concept and acceptance criteria, the plan, the verification and test
logs, the concept-check audit, the recommendations and the pull request, plus a `Halted`
section if the build stopped to ask. A fix round's section 1 also carries a **Defect**
block — reproduction, root cause, class, blast radius, scope — filled from the `/fix`
diagnosis; its presence is what tells the later steps the round is a fix. Every round
of a feature shares one branch and one pull request; a later round's **Builds on** section
names what the earlier rounds delivered, and its step 6 re-checks their acceptance criteria
as a regression pass.

The first line after the title is the workflow's state and is read by the hooks:

```
<!-- claude-plan step=3 status=active -->
```

`step` is 1 to 10; `status` is `active`, `done`, `parked` or `template`. Exactly one file
across the whole repo should be `active` — opening a round stands its predecessor down to
`done`, and step 9 marks the newest round `done` in the commit that opens the pull request,
so `main` never carries a live marker. Every step commits and pushes the file with what it
produced: plan files are the record of why the code looks the way it is, the state any
session resumes from, and what `/create-pr` builds the pull request body from.

## Claude configuration: `.claude/`

| Path | Role |
|---|---|
| `settings.json` | Registers the four hooks; pre-approves ruff/mypy/pytest, `python -m`, and the git commands the pipeline uses (read-only ones plus add, commit, push, fetch, checkout, switch, merge, mv) so an unattended build never stalls on a prompt — `guard_git.py` is what keeps that safe |
| — | Every skill pins `model` and `effort` in its frontmatter; the table in `CLAUDE.md` says which and why |
| `skills/build/` | `/build` — steps 3 to 7 as one unattended block, with `models.md` holding the per-step model and effort table and its rationale: a subagent per step on its pinned model, the step's readers (`structure-auditor`, `test-designer`) run by the orchestrator and handed over in the brief, work-in-progress commits, commit and push after each, halting rules, trace relay, resume from the marker or an interrupted run |
| `rules/python.md` | Coding conventions, auto-loaded for `**/*.py` |
| `skills/repo-setup/` | `/repo-setup` — one-time setup of a repo made from this template; carries `main_protect.solo.json` and `main_protect.collab.json` |
| `skills/feature/` | `/feature` — starts or resumes the pipeline |
| `skills/fix/` | `/fix` — starts the pipeline from a defect: reproduces, finds the root cause, sizes the class, has the diagnosis criticised, decides whether it is a bug at all, then hands to `/conceptualize` as a fix round |
| `skills/conceptualize/` | `/conceptualize` — step 1, agree the concept |
| `skills/plan/` | `/plan` — step 2, design it |
| `skills/implement/` | `/implement` — step 3, write the code (inside `/build`) |
| `skills/verify/` | `/verify` — step 4, static verification |
| `skills/test/` | `/test` — step 5, edge-case suite |
| `skills/concept-check/` | `/concept-check` — step 6, audit against the concept |
| `skills/ship/` | `/ship` — step 7, close the round: whole-tree gates and diff review (inside `/build`) |
| `skills/recommend/` | `/recommend` — step 8: critical follow-ups only, usually none, decided with the user; lesser ideas noted in `DEVELOPMENT.md`; with nothing critical it hands on to `/create-pr` |
| `skills/create-pr/` | `/create-pr` — step 9, pull request ready for review |
| `skills/watch-pr/` | `/watch-pr` — step 10, hourly review watch until merge or close |
| `skills/small-change/` | `/small-change` — cosmetic edits, outside the pipeline |
| `agents/diagnosis-critic.md` | Subagent that tries to falsify a defect diagnosis before step 1 agrees a fix on it — re-runs the reproduction, traces the cause independently, checks the class (feeds `/fix`); may run code from a scratch directory but never writes to the tree; pinned to `opus` |
| `agents/plan-critic.md` | Read-only subagent that reads a plan against its concept and the repo before the user accepts it (feeds step 2); pinned to `opus` |
| `agents/test-designer.md` | Read-only subagent that finds edge cases; run twice at step 5 with the `input-space` and `contract` briefs |
| `agents/brainstormer.md` | Read-only subagent that proposes at most three follow-ups through one lens — `user`, `maintainer`, `integrator` or `defect-class` — or none; step 8 runs only `defect-class`, on a fix round, and the others run when the user asks what to build next |
| `agents/structure-auditor.md` | Read-only subagent that reconciles this file (feeds steps 4 and 6) |

### `.claude/hooks/plan_state.py`

Shared by the other hooks: parses the `claude-plan` marker out of the plan files and
answers which plan is active, plus the small git helpers the hooks need. Importable by its
siblings because Python puts a script's own directory on `sys.path`. Stdlib only.

| Signature | Description |
|---|---|
| `Plan` | Frozen dataclass: `path`, `step`, `status`, `title`, `branch`, `feature` (the folder relative to `development/`, so the branch name with its `/`; empty for `TEMPLATE.md`), `round_number`, plus `step_name` and `gated` properties. |
| `parse(path: Path) -> Plan \| None` | Parse one plan file, or None if it has no valid marker. |
| `all_plans(project_dir: Path) -> list[Plan]` | Every parseable plan in every feature folder, most recently modified first. |
| `active_plan(project_dir: Path) -> Plan \| None` | The plan the pipeline is working through. |
| `feature_rounds(project_dir: Path, feature: str) -> list[Plan]` | One feature's rounds, oldest first; `feature` is the folder relative to `development/`, as on `Plan.feature`. |
| `git_lines(project_dir: Path, args: list[str]) -> list[str]` | Run git, return output lines. |
| `current_branch(project_dir: Path) -> str` | The checked-out branch, or `""`. |
| `main() -> None` | Showcase: prints the plans found, the active one and its sibling rounds. |

`GATE_FROM_STEP = 8` is the step at which the stop gate starts blocking: steps 3 to 7 are
the build, which carries its own gates and must be able to halt on a red tree.
`PLAN_DIR = development` is the directory it scans; `Plan.feature` is a folder path relative
to it, so a branch-named folder such as `feat/csv-export` comes back with its `/`.

### `.claude/hooks/session_brief.py`

`SessionStart` hook. Injects the active plan's step into a new session's context, the skill
that resumes it (`/build` for steps 3 to 7), plus what any earlier rounds of the same
feature delivered, so work resumes without the user having to re-explain it. Silent when
no plan is active — including while a pull request is open, since step 9 closes the plan.
Stdlib only.

| Signature | Description |
|---|---|
| `brief(project_dir: Path, plan: Plan) -> str` | Describe one active plan's state. |
| `main() -> None` | Entry point: emit the brief as session context. |

### `.claude/hooks/guard_git.py`

`PreToolUse` hook on `Bash` and `PowerShell`. Refuses a `git commit` or `git push` that
would land on `main`. Reads the command the way a shell does — `shlex` resolves quoting, so a `;` or `|`
inside a commit message stays part of the message — then splits it on the real separators
into one invocation per segment. The grouping delimiters `(`, `)`, `{` and `}` split too, so
a command hidden inside `(git commit -m "x")` is seen rather than left with `(` sitting
where its name should be.

Each segment is judged against the branch that will be checked out when it runs. Only `&&`
guarantees its left side succeeded, so a branch switch carries forward across a run of
separators that is `&&` and newlines, and across nothing else: `git checkout -b feat/x &&
git commit` is allowed from `main`, and so is the same pair with the `&&` ending the line,
while `;`, `|`, `||`, `&`, a bare newline or a mixed run such as `; &&` is refused. A switch
that may not have taken effect here is distrusted the same way: one made inside a subshell
that has since closed, or aimed elsewhere by a global `-C`, `--git-dir` or `--work-tree`,
leaves the branch as it was. What still cannot be read is refused when it names `commit` or
`push` — matched on word boundaries, so `committee` is not a commit — while `main` is
checked out, and allowed anywhere else.

Within a segment it finds the command name where a shell would, after the prefix of
variable assignments and redirections, so `GIT_EDITOR=true git commit` and
`>log git commit` are seen. Backticks around a substitution are stripped, the executable is
matched without regard to case, and a short list of wrapper programs — `sudo`, `env`,
`time`, `nohup`, `doas` — is stepped over. That list is deliberately incomplete: a wrapper
nobody listed is a miss, which is safe, while scanning a segment for any `git` token would
refuse `echo git commit`, which is the failure this module treats as worse. Only options are
skipped after a wrapper, never a bare word, so an option that takes a value hides what
follows it.

A push's destination is read with the same care. The arguments are walked rather than
filtered, so an option that takes a value — `-o`, `--push-option`, `--repo`,
`--receive-pack`, `--exec` — does not leave its value standing where the remote should be,
and `git push -o ci.skip origin` is seen as the bare push it is. Every ref is then reduced
to the branch it names: a leading `+` dropped, the destination half of a `src:dst` pair
taken, `refs/heads/` stripped, backticks removed and `@` read as `HEAD`. Switch targets go
through the same reduction, so `git checkout refs/heads/main` is a switch to `main`.

A branch switch whose target only the running shell can resolve — `git checkout -`,
`@{-1}` — leaves the branch *unknown* rather than unchanged, and a `commit` or `push` that
meets an unknown branch is refused with a message saying so rather than the one about
`main`. Stdlib only.

| Signature | Description |
|---|---|
| `Segment` | Frozen dataclass: `tokens` and the `separator` that preceded them — one of `SEPARATORS`, a newline, or a grouping delimiter (`""` for the first). |
| `segments(command: str) -> list[Segment] \| None` | Split a command into invocations, or None if it cannot be read. |
| `git_subcommand(tokens: tuple[str, ...]) -> tuple[str, tuple[str, ...]]` | Identify the git subcommand and its arguments. |
| `push_targets_main(args: tuple[str, ...], branch: str) -> bool` | Whether a push would update `main`. |
| `switch_target(subcommand: str, args: tuple[str, ...]) -> str` | The branch a `checkout`/`switch` moves to, `""` when it moves none, or the sentinel `UNRESOLVED` (`"?"`) for a target only the running shell can resolve — `-` and `@{-1}`. |
| `violation(command: str, branch: str) -> str` | The reason to refuse, or `""` to allow. |
| `main() -> None` | Entry point: allow or refuse the command. |

### `.claude/hooks/lint_py.py`

`PostToolUse` hook. Runs `ruff format` and `ruff check --fix` on any `.py` file Claude
writes or edits, and reports unfixable issues back via exit code 2. Stdlib only.

| Signature | Description |
|---|---|
| `find_ruff(project_dir: Path) -> Path \| None` | Locate Ruff in the project venv. |
| `run(ruff: Path, args: list[str]) -> CompletedProcess[str]` | Run Ruff, capturing output. |
| `target_file(payload: dict[str, object], project_dir: Path) -> Path \| None` | Extract the edited `.py` file from the hook payload. |
| `main() -> None` | Entry point: format, fix, report. |

### `.claude/hooks/stop_gate.py`

`Stop` hook. Reads the active plan's step to decide how strict to be: advisory through step
7, blocking from step 8 and whenever no plan is active, and only when a Python file changed
in the tree or on the branch. When it blocks it runs ruff, mypy
and pytest, cross-checks `STRUCTURE.md` against the modules on disk, reports any test file
sitting outside `tests/` where `pytest` would silently never collect it, and reports any
package directory under `src/` missing its `__init__.py`. Bypass with
`.claude/.skip-gate`. Stdlib only.

| Signature | Description |
|---|---|
| `venv_tool(project_dir: Path, name: str) -> Path \| None` | Locate a tool in the project venv. |
| `capture(cmd: list[str], cwd: Path, timeout: int) -> CompletedProcess[str]` | Run a command, capturing output. |
| `changed_python_files(project_dir: Path) -> set[str]` | Python files changed in the tree or on this branch. |
| `tracked_python_files(project_dir: Path) -> set[str]` | All non-ignored Python files. |
| `structure_problems(project_dir: Path) -> list[str]` | File-level drift between this file and disk. |
| `stray_test_files(project_dir: Path) -> list[str]` | Test files outside `tests/`, which pytest never collects. |
| `missing_init_files(project_dir: Path) -> list[str]` | Package directories under `src/` with no `__init__.py`. |
| `gate_failures(project_dir: Path) -> list[str]` | Run the verification set, collect failures. |
| `advisory_notes(project_dir: Path, plan: Plan, changed: set[str]) -> list[str]` | Non-blocking observations for the steps below the gate. |
| `notice(message: str) -> None` | Show the user a message without blocking. |
| `block(reason: str) -> None` | Emit the block decision and exit. |
| `enforce(project_dir: Path) -> None` | Run the verification set and block on failure. |
| `main() -> None` | Entry point: decide whether the turn may end. |
