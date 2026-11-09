# Structure

Map of everything in this repo. Loaded into context at the start of every session via the
`@STRUCTURE.md` import in `CLAUDE.md`, so it is what Claude uses to find things without
searching: every module, and every public class and function with its signature and return
type. Read it before opening source files. `/finish` rewrites it from the code once per
feature; `check_sync.py` fails when a Python file under `src/`, `tests/` or `.claude/hooks/`
is missing from it or named here without existing. Private names (`_x`) stay out.

A change outside the pipeline (a `chore/` branch) updates it in the same change.

## Growth

Everything lives here for now, sub-packages included: every module keeps its own section,
because `check_sync.py` checks the module paths named in this file. When the file grows too
long to keep in context, keep the tree and one line per sub-package and every module path
here, and move per-sub-package detail into `.claude/rules/structure-<subpackage>.md` with
`paths: ["<subpackage>/**"]` so it loads only when Claude works in that sub-package. Split
rather than delete: everything in this file is in context every session. The `ev/` tables
stay here as they are.

## Tree

```
src/                    everything installable; nothing outside it is packaged
  energy_optimization/  the package itself
    common.py           shared foundation: errors, price types, quarter grid
    ev/                 smart EV charging decision core (see its modules below)
    interruptible/      interruptible-load scheduler (see its modules below)
tests/                  pytest suite, one test_<module>.py per module
development/            the pipeline's state: one folder per feature branch (see its README)
.claude/
  skills/               /feature, /fix, /round, /finish
  agents/               the pipeline's agents, one file each
  workflows/            diagnose.js (a fix's diagnosis), round.js (one round), final.js (the whole-feature check)
  hooks/                session brief, git guard, Ruff on edit, state reader, sync checker, test selector
  rules/python.md       coding conventions, loaded for **/*.py
.github/workflows/      CI: the gate and check_sync
.vscode/                editor config (Ruff as formatter, format on save)
pyproject.toml          package, version, Ruff/mypy/pytest config
DEVELOPMENT.md          development-side open questions and things to fix later (see CLAUDE.md)
DEVLOG.md               one entry per version
README.md               human setup guide, with the version
CLAUDE.md               routing map and rules for Claude
STRUCTURE.md            this file
```

## Package: `src/energy_optimization/`

### `src/energy_optimization/__init__.py`

Package entry point. Nothing is re-exported at the root: the EV charging core is the
subpackage `energy_optimization.ev`, imported as `from energy_optimization.ev import decide`,
and the load scheduler is `energy_optimization.interruptible`
(`from energy_optimization.interruptible import schedule`). The docstring names both and
`common`.

Every package directory under `src/`, including every subpackage added later, needs one of
these. A directory of modules without it is not a package, so it will not install.

### `src/energy_optimization/common.py`

The foundation both cores share, a leaf with the standard library only. `ev.model`,
`ev.quarters` and `ev.optimize` re-export the names that used to live in them, so every
import path that worked before still works and `ev.model.ConfigError is common.ConfigError`.
All grid arithmetic is in UTC; datetimes sharing a `ZoneInfo` are never compared directly
because that ignores `fold`.

| Signature | Description |
|---|---|
| `class ConfigError(ValueError)`; `__init__(self, field: str, message: str) -> None`; attributes `field`, `message`; `str(err) == f"{field}: {message}"` | Invalid input, naming the field. |
| `class SolverError(RuntimeError)`; `__init__(self, status: int, message: str) -> None`; attributes `status`, `message`; `str(err) == f"HiGHS status {status}: {message}"` | The solver failed; carries scipy's `milp` status and message. |
| `fail(field: str, message: str, ctx: str = "") -> ConfigError` | Build a `ConfigError`, prefixing the message with `ctx`. |
| `check_number(field: str, value: object, ctx: str = "") -> float` | A finite float; bools, non-numbers, NaN and inf refused. |
| `check_whole(field: str, value: object, ctx: str = "") -> int` | An int; an integral float accepted. |
| `check_aware(field: str, value: object, ctx: str = "") -> datetime` | An aware datetime. |
| `check_bool(field: str, value: object, ctx: str = "") -> bool` | A bool. |
| `check_text(field: str, value: object, ctx: str = "") -> str` | A non-blank string. |
| `set_field(obj: object, field: str, value: object) -> None` | Set a field on a frozen dataclass. |
| `PriceInterval(start: datetime, end: datetime, price: float)` | One priced interval on quarter boundaries. |
| `PriceSeries(intervals: tuple[PriceInterval, ...], updated_at: datetime)` | The price series; intervals must not overlap. |
| `QUARTER: timedelta` | The planning step, 15 minutes. |
| `to_utc(moment: datetime, name: str) -> datetime` | An aware moment in UTC; `ValueError` naming `name` if naive. |
| `floor_to_quarter(moment: datetime) -> datetime` | Start of the quarter containing an aware moment, in UTC; `ValueError` if naive. |
| `quarter_starts(start: datetime, end: datetime) -> list[datetime]` | UTC quarter starts from the quarter containing `start` while before `end`; `ValueError` if either is naive. |
| `quarter_prices(series: PriceSeries) -> dict[datetime, float]` | Every interval split into quarters, keyed by UTC start. |
| `prices_fresh(series: PriceSeries, now: datetime, max_age: timedelta) -> bool` | `abs(now - updated_at) <= max_age`; `ValueError` if `now` is naive. |
| `main() -> None` | Showcase: a price series split into quarters, a rejected field. |

### Subpackage `src/energy_optimization/ev/`

The smart EV charging decision core: a pure function from one complete snapshot (time,
prices, chargers, sessions, measured phase currents, settings) to one decision (ordered
actions, a plan and a reason per charger, available amps, status, events). No I/O, never
reads the clock; standard library plus `numpy` and `scipy` (the HiGHS solver). Every public type is a frozen dataclass; input
types validate themselves on construction and raise `ConfigError` naming the field.
Wherever output depends on a set of phases, phases are iterated in `Phase` order
(L1, L2, L3), never in `frozenset` order. Every module is runnable with
`python -m energy_optimization.ev.<module>` and ends in a `main()` showcase.

Dependency order (all of it on top of `common`, which `model`, `quarters` and `optimize`
import and re-export from): `model` and `result` are leaves; `quarters` uses `model`; `planning`
uses `model`, `result`; `allocation` uses `model`; `optimize` uses `model`, `planning`,
`quarters`, `result`; `decide` uses all of those; `codec` uses `model` and `result`.

### `src/energy_optimization/ev/__init__.py`

Re-exports the public API with `__all__`: every public type in `model` and `result` (except
the `Params` alias, importable from `result`), `decide`, `SolverError` and the four codec
functions. The helpers in `quarters`, `planning`, `allocation` and `optimize` are importable
from their own modules.

### `src/energy_optimization/ev/model.py`

Input types and their validation. `Phase`, `ChargerKind` are `StrEnum`s; the dataclasses
below are frozen and check themselves in `__post_init__`. Tuple fields accept any sequence
and become tuples. Numeric fields reject `bool` and non-finite floats; float fields accept
ints.

| Signature | Description |
|---|---|
| `class Phase(StrEnum)`: `L1`, `L2`, `L3` | A phase of the grid connection. |
| `class ChargerKind(StrEnum)`: `ADJUSTABLE`, `ON_OFF`, `FIXED` | What a charger can do. |
| `ConfigError`, `PriceInterval`, `PriceSeries` | Re-exported from `common`, where they are defined. |
| `Charger(id: str, phases: frozenset[Phase], kind: ChargerKind, min_current: int \| None = None, max_current: int \| None = None, assumed_current: float \| None = None, fallback_current: int \| None = None)` | A charge point; the fields that apply depend on `kind`. |
| `ChargerState(charger_id: str, car_connected: bool, current_setting: float, paused: bool, measured_power_w: float \| None = None, available: bool = True)` | A charger's state now. |
| `Session(charger_id: str, ready_time: time, energy_need_kwh: float \| None = None, soc_percent: float \| None = None, target_soc_percent: float \| None = None, battery_capacity_kwh: float \| None = None, delivered_kwh: float = 0.0, charge_now: bool = False, smart_charging: bool = True, user_current_limit: int \| None = None, plugged_in_at: datetime \| None = None)` | The car on one charger; `ready_time` is naive local time. |
| `PhaseCurrents(measured_at: datetime, l1: float \| None, l2: float \| None, l3: float \| None)`; `get(self, phase: Phase) -> float \| None` | Measured current per phase for the whole connection. |
| `Settings(phase_budget: float, phase_threshold: float, fallback_current: int, timezone: str, voltage: float = 230.0, min_block_quarters: int = 2, price_cap: float \| None = None, default_energy_need_kwh: float = 30.0, max_price_age: timedelta = timedelta(minutes=60), max_measurement_age: timedelta = timedelta(seconds=60), waiting_premium_per_hour: float = 0.0)` | Installation settings; budget, threshold, fallback and timezone are required. `waiting_premium_per_hour` is the owner's price of waiting as a fraction of the price per hour (0.01 is 1% per hour), 0 to 1 inclusive, applied to positive prices in the planned tier only. |
| `Snapshot(now: datetime, settings: Settings, chargers: tuple[Charger, ...], states: tuple[ChargerState, ...], sessions: tuple[Session, ...], prices: PriceSeries, phase_currents: PhaseCurrents \| None = None)` | One call's input; cross-checks ids between chargers, states and sessions. |
| `main() -> None` | Showcase: build a snapshot, then trigger a `ConfigError`. |

### `src/energy_optimization/ev/result.py`

Output types. No logic beyond `PhaseAmps.get` and the tuple normalisation in `ChargerDecision` and `Decision`.

| Signature | Description |
|---|---|
| `type Params = dict[str, str \| float \| int \| bool]` | Parameter values carried by `Reason.params` and `Event.params`. |
| `class Stage(StrEnum)`: `DOWN`, `UP` | Which phase of execution an action belongs to. |
| `class ActionKind(StrEnum)`: `SET_CURRENT`, `PAUSE`, `RESUME` | What to do to a charger. |
| `Action(charger_id: str, kind: ActionKind, target_current: float, stage: Stage)` | One change with an absolute target. |
| `class ReasonCode(StrEnum)`: `CHARGE_NOW`, `IN_PLAN`, `READY_TIME_PASSED`, `PLAN_COMPLETED`, `PRICES_STALE`, `PLANNED_LATER`, `WAITING_FOR_HIGHER_PRIORITY`, `WAITING_FOR_CAPACITY`, `TARGET_REACHED`, `BELOW_MINIMUM`, `SMART_CHARGING_OFF`, `FIXED`, `UNAVAILABLE`, `NO_CAR`, `NO_SESSION` | Stable reason codes; values are the lower-case names. |
| `Reason(code: ReasonCode, text: str, params: Params = field(default_factory=dict))` | Why a charger got what it got. |
| `PlannedQuarter(start: datetime, price: float \| None, current: float)` | One planned quarter; `price` None means unpriced. |
| `ChargerDecision(charger_id: str, allocation: float, priority: int \| None, reason: Reason, plan: tuple[PlannedQuarter, ...] = (), remaining_need_kwh: float \| None = None, deadline: datetime \| None = None, expected_finish: datetime \| None = None, reaches_target: bool \| None = None)` | Everything decided for one charger. |
| `class Status(StrEnum)`: `NORMAL`, `LIMITED_BY_BUDGET`, `LIMITED_BY_PHASE`, `WAITING_FOR_CAPACITY`, `PHASE_DATA_STALE`, `PRICES_STALE` | Overall status. |
| `class EventKind(StrEnum)`: `TARGET_UNREACHABLE`, `PRICE_CAP_EXCEEDED`, `CHARGE_NOW_NEED_COVERED`, `PHASE_DATA_STALE`, `PRICES_STALE`, `PLAN_NOT_PROVEN_OPTIMAL` | Notification-worthy events; the last says a solve stopped at the node budget. |
| `Event(kind: EventKind, text: str, charger_id: str \| None = None, params: Params = field(default_factory=dict))` | One event. |
| `PhaseAmps(l1: float, l2: float, l3: float)`; `get(self, phase: Phase) -> float` | Amps per phase. |
| `Decision(actions: tuple[Action, ...], chargers: tuple[ChargerDecision, ...], available: PhaseAmps, status: Status, events: tuple[Event, ...])` | One call's answer; `chargers` in snapshot charger order. |
| `main() -> None` | Showcase: build a small decision by hand. |

### `src/energy_optimization/ev/quarters.py`

The EV side of the quarter-hour grid. `QUARTER`, `floor_to_quarter`, `quarter_starts`,
`quarter_prices` and `prices_fresh` are re-exported from `common`, where they are defined;
`next_occurrence` stays here because local ready times are EV-only. All arithmetic in UTC;
datetimes sharing a `ZoneInfo` are never compared directly because that ignores `fold`.

| Signature | Description |
|---|---|
| `QUARTER`, `floor_to_quarter`, `quarter_starts`, `quarter_prices`, `prices_fresh` | Re-exported from `common`, same signatures. |
| `next_occurrence(ready_time: time, after: datetime, timezone: str) -> datetime` | First moment strictly after `after` at which local time is `ready_time`, in UTC; a gap time shifts forward, a fold time is the first occurrence unless `after` is already past it (then the second); `ready_time.fold` is ignored; `ValueError` if `after` is naive. |
| `main() -> None` | Showcase: deadlines and quarter counts across the 2026 DST nights. |

### `src/energy_optimization/ev/planning.py`

Planning vocabulary: remaining need, the energy of a quarter, and the plan type the
optimiser returns. The plan itself is made in `optimize`.

| Signature | Description |
|---|---|
| `class NeedSource(StrEnum)`: `ENERGY`, `SOC`, `DEFAULT` | Where a remaining need came from. |
| `remaining_need_kwh(session: Session, default_need_kwh: float) -> tuple[float, NeedSource]` | Remaining need, clipped at 0, and its source. |
| `energy_per_quarter_kwh(current: float, phase_count: int, voltage: float) -> float` | Energy delivered in one quarter. |
| `SessionPlan(charger_id: str, quarters: tuple[PlannedQuarter, ...], planned_kwh: float, reachable: bool, price_cap_exceeded: bool, uses_unpriced: bool)` | One session's plan, quarters in time order. |
| `main() -> None` | Showcase: a remaining need from energy and from the default; energy per quarter. |

### `src/energy_optimization/ev/allocation.py`

Phase capacity, draw and reservations, and measurement freshness.

| Signature | Description |
|---|---|
| `measurements_fresh(currents: PhaseCurrents \| None, now: datetime, max_age: timedelta, phases: frozenset[Phase]) -> bool` | Measurement present, recent and complete for `phases`. |
| `present_draw(charger: Charger, state: ChargerState, voltage: float) -> float` | What a charger is taken to draw now on each phase, for the phase guard: 0 when paused or without a car, the smaller of setting and measured draw otherwise; a fixed or unavailable charger counts at its measured draw (its reservation when power is missing), the rest of its reservation being held back by `decide`. |
| `reserved_current(charger: Charger, state: ChargerState, voltage: float) -> float` | What a fixed or unavailable charger is reserved at on each phase. |
| `phase_capacity(snapshot: Snapshot, fresh: bool) -> dict[Phase, float]` | Per phase, budget intersected with the phase guard when `fresh`; may be negative. |
| `main() -> None` | Showcase: measurement freshness and a reservation. |

### `src/energy_optimization/ev/optimize.py`

The optimiser: one mixed-integer program solved by HiGHS through `scipy.optimize.milp`,
level by level (lexicographically). It plans every quarter from now to each car's ready
time and shares quarter 0 tier by tier on what the tiers above leave: smart charging off,
charge now, planned cars, then spare capacity. Quarter-0-only tiers are solved on small
models before and after the planning model. Within the planning model the cost level minimises the price weighted by `waiting_premium_per_hour` (positive prices only, by horizon index from the quarter containing `now`; at premium 0 the raw prices exactly), and is held exactly in whole ticks of the grid of those values when every value is a whole multiple of a tick of at least 1e-7 and every current is whole, and to a tolerance otherwise. As many planned cars as possible are on now, the better-ranked first, and they share the first quarter by progressive filling in every tier (when no car can rise with the others, the leftover goes one amp at a time to the lowest, ties in ranking order: round 1's `share`). An unpriced first quarter carries only the energy the need forces. Deterministic: one thread, a fixed node budget
per solve (`NODE_LIMIT`), never a wall-clock limit; all time arithmetic in UTC. No greedy
fallback: a solve that HiGHS reports infeasible (status 2, with or without a point) or that returns no point at all (any status) is retried once with presolve off, and the retry's result is the one judged: a second failure raises `SolverError`, while a result with a point and any other status (a node-budget stop is kept as not proven; a status 3 or 4 below the limit raises) is never retried.

| Signature | Description |
|---|---|
| `SolverError` | Re-exported from `common`, where it is defined. |
| `NODE_LIMIT: int` | Branch-and-bound nodes allowed per level solve (8000; see the benchmark in the round-2 plan). |
| `class Tier(IntEnum)`: `SMART_OFF = 0`, `CHARGE_NOW = 1`, `PLANNED = 2`, `SPARE = 3` | Which precedence stage a demand belongs to. |
| `Demand(charger_id: str, phases: frozenset[Phase], tier: Tier, min_current: float, max_current: float, now_max_current: float, need_kwh: float = 0.0, deadline: datetime \| None = None)` | One charger asking for current, given in ranking order. `min_current == max_current` is on/off at exactly that current in every tier; otherwise it runs in whole amps, so a fractional `min_current` is refused unless both maxima are below it (off in every quarter). A maximum below `min_current` means off in those quarters. Validates itself with `ValueError` naming the field (`tier` must be a `Tier`; `deadline` must be aware and is required for `PLANNED`). |
| `Outcome(allocations: dict[str, float], plans: dict[str, SessionPlan], proven: bool)` | Quarter-0 current of every demand, a plan per `PLANNED` demand. `proven` is False when any solve (a level, or a probe that decides a level) stopped at the node budget with a point; for a retried solve the retry's result counts, so a first attempt without a point followed by a proven retry leaves it True. |
| `optimize(demands: Sequence[Demand], now: datetime, prices: Mapping[datetime, float], capacity: Mapping[Phase, float], now_capacity: Mapping[Phase, float], voltage: float, min_block_quarters: int, price_cap: float \| None, timezone: str, waiting_premium_per_hour: float) -> Outcome` | Solve the lexicographic model. The planned tier's lowest-price level compares plans with each positive price `k` quarters after the one containing `now` raised to `price * (1 + w * k / 4)` (zero and negative prices, the other levels, the price cap and `PlannedQuarter.price` use raw prices); `SolverError` when a solve fails (infeasible or no point: only after its one retry with presolve off fails too, carrying the retry's status and message; a status 3 or 4 below the limit with a point: at once), `ValueError` for a naive `now`, a repeated charger id or a `waiting_premium_per_hour` that is a bool, not a number, not finite or below 0 (no upper bound here; `Settings` caps it at 1), `ZoneInfoNotFoundError` for an unknown `timezone`; empty `demands` returns before those checks; a limit broken by rounding raises `SolverError` with status 0. |
| `main() -> None` | Showcase: a charge-now car, two planned cars and a spare-capacity car on one phase, then a phase with no room, then the first case again with a waiting premium of 0.5 per hour. |

### `src/energy_optimization/ev/decide.py`

The decision core: classification, priority tiers, demands for `optimize`, actions, reasons,
status and events. Its time arithmetic is in UTC throughout (every aware input is
converted before it is added to or compared; only output datetimes are presented in the
settings timezone). Both expected-finish branches add in UTC: a planned one adds its last
quarter's length to that quarter's UTC start, so it is right in the repeated hour of the
fall-back night, and an unplanned one is `None` for a trickle of current no calendar can
hold, instead of raising `OverflowError`.

| Signature | Description |
|---|---|
| `decide(snapshot: Snapshot) -> Decision` | Decide what every charger should be set to now; raises `SolverError` when the solver fails. |
| `main() -> None` | Showcase: three chargers and two sessions, then the same snapshot without phase measurements. |

### `src/energy_optimization/ev/codec.py`

Snapshot and decision to and from plain JSON-compatible dicts. Keys are field names, enums
go by value, datetimes are ISO 8601 with offset, `time` is `time.isoformat()`, `timedelta`
is seconds, phase sets are sorted lists. Decoding rejects missing, unknown and wrongly
typed fields with a `ConfigError` whose `field` is a dotted path such as
`chargers[1].max_current`. `settings.waiting_premium_per_hour` is written always and may be omitted
on decoding (0.0).

| Signature | Description |
|---|---|
| `snapshot_to_dict(snapshot: Snapshot) -> dict[str, object]` | Snapshot to a plain dict. |
| `snapshot_from_dict(data: Mapping[str, object]) -> Snapshot` | Dict to snapshot; `ConfigError` with a dotted path on bad input. |
| `decision_to_dict(decision: Decision) -> dict[str, object]` | Decision to a plain dict. |
| `decision_from_dict(data: Mapping[str, object]) -> Decision` | Dict to decision, same error rules. |
| `main() -> None` | Showcase: snapshot to JSON and back; a rejected bad field. |

### Subpackage `src/energy_optimization/interruptible/`

The interruptible-load scheduler: a pure function from one snapshot (time, a load with its
off-time limits, prices, the load's recent history, an override) to a `LoadSchedule` (on or
off now, a plan for every priced quarter, the saving against always-on, a reason). For
devices that are normally on and may be off for bounded periods: a fridge, a water heater,
a pump. No I/O, never reads the clock; standard library plus `numpy` and `scipy`. Every
public type is a frozen dataclass; input types validate themselves and raise `ConfigError`
naming the field. It does not call `ev` and `ev` does not call it; both import `common`.
Dependency order: `model` is a leaf; `result` uses `model`; `scheduler` uses `model` and
`result`. Every module is runnable with `python -m energy_optimization.interruptible.<module>`
and ends in a `main()` showcase.

### `src/energy_optimization/interruptible/__init__.py`

Re-exports with `__all__`: `LoadState`, `Limits`, `Load`, `LoadSnapshot`, `ReasonCode`,
`Reason`, `ScheduledQuarter`, `LoadSchedule`, `schedule`, and from `common` `ConfigError`,
`SolverError`, `PriceInterval` and `PriceSeries`, so a caller needs one import.

### `src/energy_optimization/interruptible/model.py`

Input types. All limits are whole quarters; integral floats are accepted, bools and
non-finite numbers refused. Per-field ranges are checked first, then the cross-checks.

| Signature | Description |
|---|---|
| `class LoadState(StrEnum)`: `ON = "on"`, `OFF = "off"` | A quarter's state, in history, plan and decision. |
| `Limits(max_off_run: int, window_off_budget: int, window_length: int, min_on_after_off: int = 0, min_off_run: int = 1)`; property `history_needed -> int` | X, Y, Z, N and M. X, Y, Z, M at least 1, N at least 0 (0 behaves as 1); Y at most Z (names `window_off_budget`); M at most X and at most Y (names `min_off_run`). `history_needed` is `max(Z - 1, X, N)`. |
| `Load(limits: Limits, rebound: float, min_saving_percent: float = 0.0, power_kw: float \| None = None)` | `rebound` in [0, 1]; `min_saving_percent` at least 0; `power_kw` above 0 or None. |
| `LoadSnapshot(now: datetime, load: Load, prices: PriceSeries, history: tuple[LoadState, ...] = (), force_on: bool = False)` | One call's input; `history` oldest first, any ordered iterable accepted (a string, set or mapping is refused), a non-`LoadState` entry refused naming its index. |
| `main() -> None` | Showcase: a fridge's limits and a snapshot, then `ConfigError` for M > X. |

### `src/energy_optimization/interruptible/result.py`

Output types. No logic beyond the tuple normalisation of `LoadSchedule.plan`.

| Signature | Description |
|---|---|
| `class ReasonCode(StrEnum)`: `NO_PRICES`, `FORCED_ON`, `MAX_OFF_RUN`, `WINDOW_BUDGET`, `MIN_ON_AFTER_OFF`, `END_OF_HORIZON`, `MIN_OFF_RUN`, `PLANNED_OFF`, `PLANNED_ON`, `NO_SAVING` | Why the current quarter is what it is; values are the lower-case names. |
| `Reason(code: ReasonCode, text: str)` | The code plus a sentence naming the numbers. |
| `ScheduledQuarter(start: datetime, price: float, state: LoadState)` | One planned quarter; `start` in UTC. |
| `LoadSchedule(state: LoadState, reason: Reason, plan: tuple[ScheduledQuarter, ...], saving_percent: float \| None, always_on_cost: float \| None = None, planned_cost: float \| None = None, saving: float \| None = None, history_short: bool = False, proven: bool = True)` | The answer; the money fields are set only when `power_kw` is given; `proven` is False when a solve stopped at the node budget. |
| `main() -> None` | Showcase: a small schedule built by hand. |

### `src/energy_optimization/interruptible/scheduler.py`

The scheduler: a shortest path over the quarter boundaries of the priced horizon, with
the sliding windows as side constraints, solved as a MILP by HiGHS through
`scipy.optimize.milp` in two levels (cost, then fewest off quarters, earlier first). Off
arcs carry the rebound of the off period on the on-quarters after it; the limits, the
minimum saving and the end-of-horizon rule decide which arcs exist. History is a
commitment: an off run in progress continues to its minimum, history that already breaks
a limit is accepted and the quarters it constrains are on. Deterministic: one thread, a
fixed node budget (`NODE_LIMIT`), no time limit; a solve HiGHS reports infeasible or
without a point is retried once with presolve off. The module is named `scheduler`, not
`schedule`, so the re-exported function does not shadow it.

| Signature | Description |
|---|---|
| `NODE_LIMIT: int` | Branch-and-bound nodes allowed per level solve (8000). |
| `schedule(snapshot: LoadSnapshot) -> LoadSchedule` | Plan the load; `SolverError` when a solve has no point (or status 2) after its one retry with presolve off, or at once on a status other than 0, 1 or a node-budget 4 when it has a point. |
| `main() -> None` | Showcase: a fridge across an evening price peak, then the same load with the override on. |

## Tests: `tests/`

All tests live here and nowhere else: `testpaths = ["tests"]` in `pyproject.toml` means
`pytest` collects nothing outside this directory (a fix's reproduction waits in its
`development/<branch>/diagnosis/` folder until the fix round moves it here).

### `tests/test_ev_model.py`

Covers `src/energy_optimization/ev/model.py` (T1): every invalid case in A1 for every type, direct construction,
asserting the `ConfigError.field` — a missing budget, threshold or fallback passed as
`None`, bools and non-finite or huge numbers, fields that do not belong to a kind,
duplicate phases given as a list, an unloadable timezone, naive timestamps, overlapping or
misaligned price intervals (judged in UTC, including across the fall-back fold), snapshot
cross-links — plus the accepted boundaries, normalisation to tuples, ints and floats, the
waiting premium (T6: negative, NaN, infinite, bool, string, `None`, list, bytes and
above-1.0 values refused naming `waiting_premium_per_hour`, 0, -0.0, an int, 1.0 and 1e-12
accepted and stored as a float, the default 0.0 as the last field, frozen, an earlier bad
field named first), and the `main` showcase.

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

Covers `src/energy_optimization/ev/planning.py`: `remaining_need_kwh` and
`energy_per_quarter_kwh` tables, and the `main` showcase. The greedy planner's tests went
with the planner.

### `tests/test_ev_optimize.py`

Covers `src/energy_optimization/ev/optimize.py` (T1, T2, T4, T6, T7, T8): the quarter-0
tiers reproduce round 1's `share` rule (zero-or-min in ranking order, max-min fairness on
mixed phases, all-or-nothing claims including a fractional current, tolerance, empty and
negative capacity, purity) and its named results in every quarter-0 tier; the planning
stage on hand-checked plans (minimum blocks and a block longer than the window, a tiny need
under a block, partly and fully unpriced horizons, negative prices overfilling by at most a
quarter, the price cap at its boundary, a shortfall, first-quarter capacity, deadlines at or
before now, the fall-back night in UTC, a 48-hour horizon, fractional on/off currents, the
better-ranked car getting the earlier quarter and being on now first); the tiers on the
leftover for every pair; smart-off held to `capacity` in later quarters; `Demand`
validation naming each field (including `tier`), `Outcome`, `SolverError` and `NODE_LIMIT`;
the `Raises:` branches, the empty-demands early return and negative or absent capacity; the
rounding checks that raise with status 0; the solver call spied for no `time_limit`, one
thread, the node budget, a zero gap and presolve on except for the one retry that follows a failed attempt and changes only that option; the retry (a failed solve retried once with presolve off, the retry's answer proven or not, a second failure raising with the retry's status and message for each failure kind, a stop at the node budget with a point not retried, the planned remainder through a retry of every solve, identical solver calls across retried runs, seeds 1931 and 5662 of step 6's tiny set solved); a node-budget stop with a point being not proven and a status 4
below the limit failing without a retry; a `hypothesis` property that no limit is ever broken (T1) and one
that the level-by-level tuple equals a brute-force enumeration (T2); the one-amp remainder (fractional first-quarter limits, a binding phase beside a free one, an on/off car inside the ranking, unequal minimums, smart-off's two rooms, remainders of 0 and 1 and a half amp of room, a raise stopped at the node budget, an odd first quarter forced by prices, the planned tier against round 1's `share` on generated claims, and the accepted solver-tolerance slack); and the planned tier's
full progressive fill (8 / 11 / 11 on 30 A, 8 / 8 for two cars forced into an unpriced first
quarter, an unpriced first quarter not filled beyond what the need forces, the leftover one amp
at a time to the lowest (7 / 7 / 6 on 20 A, 2 / 2 / 1 for minimums 2, 1, 1 on 5 A, in every tier, and equal to a restatement of `share` on generated claims), 8 / 8 / 8 on mixed phases; cars tied at a cap, one
binding phase, on/off and fractional on/off cars, unequal minimums, a car off now, a
need-limited car found by a probe, a forced dear first quarter split evenly, the cheaper
quarter never given up for a dearer one by a price tick, the forced unpriced energy held in
kWh not amps, the fill robust to integrality noise, probes failing with `SolverError` or
stopping at the node budget, identical solver calls on repeated runs); the brute-force oracle
follows the amended P5 and P6 and generates three-phase demands; a fractional `min_current`
on an adjustable `Demand` is refused and accepted when the demand is on/off or always off; the
private tick helper's grid detection and its give-ups (grids finer than 1e-7, more than
10^7 ticks, all-zero prices), and a first quarter dearer by one tick getting no extra amp
while equal prices still split freely; the waiting premium (T2-T6, T8-T10: the A2 flip boundaries at 8 and 96 quarters
with break-even ties, quarter 0 never weighted, the index counted from the quarter
containing `now` and in UTC across the fall-back night, non-positive prices unweighted at
every premium, a tiny premium keeping the plan, no planned car meaning identical
outcomes, smart-off and charge-now allocations unchanged with spare taking what the
planned cars leave, the progressive fill unchanged, two deadlines sharing the horizon
index, the price cap and `PlannedQuarter.price` on raw prices, the validation table and
its order after the empty-demands and repeated-id checks, -0.0 on the raw path, the exact
and tolerance cost paths both used, solver options unchanged, a 48-hour horizon, and the
brute-force oracle `level_tuple` taking a premium in 0, 0.01 and 1.0 over prices
including zero and negative, plus a second oracle test built so the premium often moves
the plan); and the `main` showcase.

### `tests/test_ev_allocation.py`

Covers `src/energy_optimization/ev/allocation.py` (T7, T13, T14): `measurements_fresh` boundaries, `reserved_current`, `present_draw`
(smaller of setting and measured, paused, no car, fixed at its measured draw) and
`phase_capacity` (negative values, missing phases, fixed reservations); and the `main`
showcase.

### `tests/test_ev_decide.py`

Covers `src/energy_optimization/ev/decide.py` (T3, T5-T7, T9, T13-T18, T20): doc scenarios through `decide`, the
priority tiers and reason codes, reservations and smart charging off, the fallback table,
actions and their order, events, expected finish and `reaches_target`, DST nights, the
float-residue and `user_current_limit=0` regressions, determinism across hash seeds, the
AST scan (standard library, numpy and scipy only) and the `pyproject.toml` dependencies, the A5 tier timing tests (3, 7 and 10 chargers within 10, 60 and 120 s on five seeds each), and two `hypothesis`
properties over generated snapshots: one (T17) whose invariants are computed independently of
the module's helpers, and one that sends each snapshot through the dict round trip and gets
the same decision; the invariants include a plan-fit oracle keyed in UTC and
`expected_finish` after the first planned quarter, compared as instants. Also the
stateless-core tests: the unplanned expected finish added in UTC across both 2026 DST
nights (including an unplanned finish landing exactly on the spring-forward transition),
a planned expected finish ending in or at the repeated hour of the fall-back night and at
the spring-forward transition, `reaches_target` across the fold, a trickle of current (including a reserved
charger's tiny measured draw) giving `None` and surviving the codec round trip, no state
between interleaved snapshots, the solver's failure statuses raising `SolverError` from `decide` (including a later level stopping without a point, the retry's status and message being the ones reported rather than the first attempt's, and a status 4 below the node limit raising whatever its message), node-budget stops (stubbed and real) adding `PLAN_NOT_PROVEN_OPTIMAL` last and round-tripping the codec, the solver call spied, decisions equal under permuted chargers, states and sessions, planned fractional on/off chargers, the fallback capping only the first planned quarter, a ready time inside the current quarter, tiny needs planned in the ready-time quarter (or now with a one-quarter block), two planned cars tied at a user limit leaving the rest to the other, two cars forced into an unpriced current quarter sharing it in plan, the hash-seed check through the planned fill, the first-ranked planned car charging first on equal prices and a plan below its max staying normal, a mid-quarter ready time credited the whole quarter (pinned, not endorsed), unpriced and empty price series, the price-cap event boundary, the tiers' waiting reason codes and stale prices sharing evenly (T4-T7), the two step-6 snapshots HiGHS called infeasible without presolve (explicit literals), a fractional on/off charger staying off under a lower user limit, a zero limit or the fallback cap, the tier invariant not counting a charger the fallback cap keeps off, and the public surface equal to round 1's plus `SolverError`, `PLAN_NOT_PROVEN_OPTIMAL`, `optimize`'s names and `waiting_premium_per_hour` (exports, removed modules and names, dataclass fields, signatures, enum members); the waiting premium through `decide` (T2, T4, T5, T8, T9: passed on to `optimize` as its last argument, a planned car moving between now and later by the A2 numbers at 8 and 96 quarters, 0 and an omitted premium deciding alike, snapshots without a planned car deciding identically at any premium, the other tiers keeping their allocations, the cap event and plan prices on raw prices, the UTC quarter index across the fall-back night, the codec round trip, hash seeds, repeated runs and unchanged solver options); and the `main` showcase.

### `tests/test_ev_codec.py`

Covers `src/energy_optimization/ev/codec.py` (T2, T19): JSON round trips of snapshots and decisions (negative
prices, odd offsets, `None` measurements, every enum, parameter types kept), plain-type
output, and rejection of missing, unknown, wrongly typed, huge, null-on-defaulted and
invalid fields with the exact dotted path; the literal round-1 key sets of snapshot and
decision dicts (the settings set carrying `waiting_premium_per_hour`); the premium (T7)
round-tripping through dicts and JSON text, present as 0.0 when unset, an omitted key
decoding as 0.0, an int decoding as a float, null, strings, bools, lists, dicts, NaN, inf
and out-of-range values refused with the path `settings.waiting_premium_per_hour`, a
misspelt key still unknown, and the decision dict gaining no key; the rejection of `memory`, the 15 round-2 settings and the round-2
decision keys, each naming that field; and the `main` showcase.

### `tests/test_common.py`

Covers `src/energy_optimization/common.py` (T10, T15): the moved names are the same objects
from `common`, `ev.model`, `ev.quarters`, `ev.optimize`, `ev` and `interruptible`, and the
private helper aliases in `ev.model` and `ev.quarters` are the `common` helpers; every name
in both `__all__` lists imports; `ConfigError` and `SolverError` (base class,
`field`/`status`, `message`, unchanged `str`); the validation helpers' edges (`fail`'s context
prefix, `check_number`/`check_whole` on bools, huge ints, NaN and numpy scalars,
`check_aware` with an offset-less tzinfo); `to_utc` across an odd offset and the fall-back
fold; the grid helpers and price types; and the `main` showcase.

### `tests/test_interruptible_model.py`

Covers `src/energy_optimization/interruptible/model.py` (T7, T8): `Limits` range checks
before cross-checks and the field each names, integral floats, bools and fractions per field,
`history_needed`; `Load` bounds for `rebound`, `min_saving_percent` and `power_kw`;
`LoadSnapshot` history normalisation, the index named in a bad entry, unordered history
refused, `force_on`, naive `now`, wrong `load` and `prices`; the `LoadState` members and values; and the `main` showcase.

### `tests/test_interruptible_result.py`

Covers `src/energy_optimization/interruptible/result.py`: the exact `ReasonCode` members and
values, `LoadSchedule` defaults and plan normalisation, frozenness, comparison by value, and the `main` showcase.

### `tests/test_interruptible_schedule.py`

Covers `src/energy_optimization/interruptible/scheduler.py` (T1-T9, T11-T14, T16): two
`hypothesis` properties checked by an oracle written in the test file from the concept's
rules (T1: no limit broken, counting history; T2: the plan equals the brute-force cheapest,
then fewest off, then earliest off, with history, rebound and minimum saving); flat prices at
r = 1 and the run-in-progress readings of A3 (T3); each history boundary for X, the window
and N (T4); the override (T5); the figures, including the forced continuation longer than the
horizon (the defect this step found) and negative or zero always-on cost (T6); history
length, older history changing nothing and a huge window (T8); purity, determinism and the
spied `milp` options (T9); the minimum saving at its inclusive boundary (T11); the
end-of-horizon rule (T12); a 192-quarter horizon within 10 s on five seeds (T13); the reason
table and its precedence, including no price for the current quarter, an unpriced gap ending
the horizon, and prices that start after or end before now (T14); the solver retry and
failure statuses through a spy that patches the `scheduler` module (T16); a naive `now`
refused with `ConfigError` naming `now` (T7); `N = 0` behaving as `N = 1`; hand-checked
plans, with unsorted price intervals read in time order and prices beyond a gap ignored; and
the `main` showcase.

### `tests/test_guard_git.py`

Covers `.claude/hooks/guard_git.py` — every public function plus `main` end to end against a
throwaway repository, over both Bash and PowerShell command forms — and is the regression
suite for the quoting defect its parser was rebuilt to fix. One test asserts a documented
miss rather than a fix: `sudo -u me git push` is allowed, because only options are skipped
after a wrapper and never a bare word.

### Pipeline tests

The other pipeline tests (`tests/test_guard_git.py` is described above):

| File | Covers |
|---|---|
| `tests/test_dev_state.py` | parsing, malformed files, active-brief choice, git helpers; its `write_brief`/`write_round` helpers are reused by other tests |
| `tests/test_session_brief.py` | the brief text and `main`'s output |
| `tests/test_check_sync.py` | every check, including the version raise against a throwaway repo |
| `tests/test_select_tests.py` | every selection rule on a throwaway repo (transitive and relative imports, re-exporting `__init__`, helpers, deletions, renames, cycles, unparseable files), `changed_files` and the CLI |

## Plans: `development/`

The pipeline's state, laid out in `development/README.md`. The numbered `NN-<slug>.md` files
under `development/feat/ev-charging-core/`, `feat/stateless-milp-planner/` and
`feat/interruptible-load-scheduler/` are the record of the earlier ten-step pipeline:
read-only history, ignored by the hooks.

## Hooks: `.claude/hooks/`

Stdlib only; importable by each other and by the tests (`pythonpath` in `pyproject.toml`).

### `.claude/hooks/dev_state.py`

Reads `brief.json` and `round.json` leniently: a malformed file reads as None.

| Signature | Returns |
|---|---|
| `RoundEntry` | Frozen dataclass: `number`, `name`, `slug`, `branch`, `status`, `summary`; property `folder -> str` (`r<n>-<slug>`). |
| `Brief` | Frozen dataclass: `path`, `feature`, `title`, `branch`, `bump`, `status`, `summary`, `current_round`, `rounds: tuple[RoundEntry, ...]`, `pr`, `kind` (`feature` or `fix`); properties `folder -> Path`, `live -> bool`; `round_entry(number: int) -> RoundEntry \| None`. |
| `RoundState` | Frozen dataclass: `path`, `round`, `name`, `branch`, `status`, `step`, `steps: tuple[str, ...]`, `attempt`, `question`, `note`; property `step_name -> str`. |
| `read_brief(path: Path) -> Brief \| None` | One `brief.json`, or None if unusable. |
| `read_round(path: Path) -> RoundState \| None` | One `round.json`, or None if unusable. |
| `all_briefs(project_dir: Path) -> list[Brief]` | Every brief under `development/`, newest first. |
| `active_brief(project_dir: Path, branch: str = "") -> Brief \| None` | The live brief, preferring the checked-out feature or round branch. |
| `round_state(brief: Brief, number: int) -> RoundState \| None` | That round's `round.json`. |
| `read_diagnosis(path: Path) -> RoundState \| None` | A fix's diagnosis `state.json`, read as a `RoundState` with `round` 0 and the diagnosis steps. |
| `diagnosis_state(brief: Brief) -> RoundState \| None` | The feature's `diagnosis/state.json`, or None. |
| `git_lines(project_dir: Path, args: list[str]) -> list[str]` | Non-empty output lines of a git command, `[]` on failure. |
| `current_branch(project_dir: Path) -> str` | The checked-out branch, or `""`. |
| `main() -> None` | Prints the live feature and its current round. |

Constants: `DEV_DIR`, `DIAGNOSIS_DIR`, `KINDS`, `LIVE_STATUSES`, `FEATURE_STATUSES`,
`ROUND_STATUSES`, `STEP_STATUSES`, `ROUND_STEPS` (the nine step names), `DIAGNOSIS_STEPS` (the
four diagnosis steps).

### `.claude/hooks/session_brief.py`

`SessionStart` hook: injects the live feature's state and the skill that resumes it.

| Signature | Returns |
|---|---|
| `brief(project_dir: Path, feature: Brief) -> str` | The paragraph for the session's context. |
| `main() -> None` | Emits it as `additionalContext`, or nothing when no feature is live. |

### `.claude/hooks/check_sync.py`

Checks the documents agree with each other and with the code. Run by `/finish` and CI.

| Signature | Returns |
|---|---|
| `parse_version(text: str) -> tuple[int, int, int] \| None` | The version, or None if not exactly `X.Y.Z`. |
| `bumped(version: tuple[int, int, int], bump: str) -> tuple[int, int, int]` | The version raised by `major`/`minor`/`patch`; raises `ValueError` otherwise. |
| `project_version(pyproject_text: str) -> str` | `[project] version`, or `""`. |
| `version_problems(project_dir: Path) -> list[str]` | pyproject, README `**Version:**` and newest DEVLOG entry disagree; DEVLOG order. |
| `structure_problems(project_dir: Path) -> list[str]` | Python files missing from STRUCTURE.md, or named but absent. |
| `state_problems(project_dir: Path) -> list[str]` | Unparseable state files, unknown statuses. |
| `branch_brief(project_dir: Path, branch: str) -> Brief \| None` | The brief whose feature or round branch is `branch`. |
| `bump_problems(project_dir: Path, base: str) -> list[str]` | Version not raised above `base`'s by this branch's brief's bump (any raise without one). |
| `problems(project_dir: Path, base: str = "") -> list[str]` | All of the above. |
| `main() -> None` | CLI: `[--base REF] [--root DIR]`; prints problems, exits 1 if any. |

### `.claude/hooks/select_tests.py`

Picks the test files a change can affect, so a round runs those and CI runs the whole suite.
The changes are `git diff <base>...HEAD` plus uncommitted and untracked files. The full suite
(None) for `pyproject.toml`, a `conftest.py`, a non-test file under the test directory, a
re-exporting `__init__.py`, an unparseable `.py` file or a file it cannot place; otherwise
the changed test files, `test_<module>.py` (and `test_<pkg>_<module>.py`) of each changed
module, and every test file that depends on a changed module transitively, read with `ast`
(imports, `import_module("...")`, and strings naming a module or its path). Prose,
`development/` and `.claude/` outside `hooks/` select nothing; other configuration selects
the tests that name it. Module roots come from `pythonpath` and `testpaths`;
`[tool.select_tests] ignore` adds path prefixes that select nothing.

| Signature | Returns |
|---|---|
| `Selection` | Frozen dataclass: `tests: tuple[str, ...] \| None` (None = full suite), `reason`, `total`; `summary() -> str` (`selected 4 of 61: ...`, `full suite: ...`, `no tests apply: ...`). |
| `changed_files(project_dir: Path, base: str) -> list[str]` | Changed paths against `base...HEAD`, committed or not, sorted; renames as both paths. Raises `ValueError` when git fails. |
| `selection(project_dir: Path, changed: Iterable[str]) -> Selection` | The selection with its reason. |
| `select_tests(project_dir: Path, changed: Iterable[str]) -> list[str] \| None` | The test paths, `[]` when none applies, None for the full suite. |
| `main() -> None` | CLI: `[--base REF] [--root DIR]`; one test per line, `ALL`, or nothing on stdout, the reason on stderr; `ALL` when the changes cannot be listed. |

### `.claude/hooks/guard_git.py`

`PreToolUse` hook on `Bash` and `PowerShell`: refuses a `git commit` or `git push` that would
land on `main`, reading the command as a shell would (quoting, separators, subshells, branch
switches chained with `&&`). Vendored from `template_repo`.

| Signature | Returns |
|---|---|
| `Segment` | Frozen dataclass: `tokens`, `separator`. |
| `segments(command: str) -> list[Segment] \| None` | The command split into invocations, or None if unreadable. |
| `git_subcommand(tokens: tuple[str, ...]) -> tuple[str, tuple[str, ...]]` | The git subcommand and its arguments. |
| `push_targets_main(args: tuple[str, ...], branch: str) -> bool` | Whether a push would update `main`. |
| `switch_target(subcommand: str, args: tuple[str, ...]) -> str` | The branch a checkout/switch moves to, `""`, or `UNRESOLVED`. |
| `violation(command: str, branch: str) -> str` | The reason to refuse, or `""`. |
| `main() -> None` | Entry point: allow or refuse. |

### `.claude/hooks/lint_py.py`

`PostToolUse` hook: `ruff format` and `ruff check --fix` on an edited `.py` file.

| Signature | Returns |
|---|---|
| `find_ruff(project_dir: Path) -> Path \| None` | Ruff in the project venv. |
| `run(ruff: Path, args: list[str]) -> CompletedProcess[str]` | Ruff's result. |
| `target_file(payload: dict[str, object], project_dir: Path) -> Path \| None` | The edited `.py` file. |
| `main() -> None` | Format, fix, report. |

## Pipeline configuration: `.claude/`

| Path | Role |
|---|---|
| `skills/feature/` | `/feature`: Main Conceptualize (Opus medium), background `repo-scout`s, BRIEF, starts round 1 |
| `skills/fix/` | `/fix`: intake, runs `workflows/diagnose.js`, agrees cause, scope and bump with the user, then the rounds with `kind: "fix"` |
| `skills/round/` | `/round`: coordinates one round (Sonnet high), runs `workflows/round.js`, answers or escalates questions |
| `skills/finish/` | `/finish`: runs `workflows/final.js`, extras, `docs-writer`, version, pull request, then CI through to green |
| `workflows/diagnose.js` | A fix's four diagnosis steps; args `feature_dir`, `branch`, `symptom`, `start_step`, `answers`; returns `diagnosed` (with `is_bug` and the `defect`), `needs_input` or `failed` |
| `workflows/round.js` | The nine round steps (step 7 runs the tests `select_tests.py` picks against the feature branch, `--ff` plus the earlier failures in the fix loop); args `feature_dir`, `round_dir`, `round`, `feature_branch`, `round_branch`, `start_step`, `attempt`, `kind`, `answers` (`kind` `fix` adds the fix-round rules); returns `merged`, `needs_input` or `failed` |
| `workflows/final.js` | Read-only whole-feature check: test gate first (the selection against `origin/main`, not the full suite), then three independent concept checks and two extras scouts; returns `tests`, `checks`, `extras`, `missing` |
| `agents/reproducer.md`, `cause-hunter.md`, `root-cause-analyst.md`, `diagnosis-critic.md` | The diagnosis's four steps (`/fix`) |
| `agents/repo-scout.md` | Read-only investigation for `/feature`, one angle each |
| `agents/round-conceptualizer.md`, `round-planner.md` | Round steps 1 and 2 (Opus) |
| `agents/implementer.md` | Step 3, and fix mode for failing gates |
| `agents/verifier.md`, `code-reviewer.md` | Step 4 |
| `agents/test-designer.md`, `test-writer.md`, `test-runner.md` | Steps 5 to 7 |
| `agents/concept-checker.md` | Step 8 and the final check |
| `agents/round-closer.md` | Step 9: merge into the feature branch |
| `agents/extras-scout.md`, `docs-writer.md` | `/finish` |
| `rules/python.md` | Conventions for `**/*.py` |
| `settings.json` | Hooks and pre-approved commands |
