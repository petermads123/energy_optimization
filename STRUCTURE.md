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
prices, chargers, sessions, measured phase currents, settings) to one decision (ordered
actions, a plan and a reason per charger, available amps, status, events). No I/O, never
reads the clock; standard library plus `numpy` and `scipy` (the HiGHS solver). Every public type is a frozen dataclass; input
types validate themselves on construction and raise `ConfigError` naming the field.
Wherever output depends on a set of phases, phases are iterated in `Phase` order
(L1, L2, L3), never in `frozenset` order. Every module is runnable with
`python -m energy_optimization.ev.<module>` and ends in a `main()` showcase.

Dependency order: `model` and `result` are leaves; `quarters` uses `model`; `planning`
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
| `class ConfigError(ValueError)`; `__init__(self, field: str, message: str) -> None`; attributes `field`, `message`; `str(err) == f"{field}: {message}"` | Invalid input, naming the field. |
| `Charger(id: str, phases: frozenset[Phase], kind: ChargerKind, min_current: int \| None = None, max_current: int \| None = None, assumed_current: float \| None = None, fallback_current: int \| None = None)` | A charge point; the fields that apply depend on `kind`. |
| `ChargerState(charger_id: str, car_connected: bool, current_setting: float, paused: bool, measured_power_w: float \| None = None, available: bool = True)` | A charger's state now. |
| `Session(charger_id: str, ready_time: time, energy_need_kwh: float \| None = None, soc_percent: float \| None = None, target_soc_percent: float \| None = None, battery_capacity_kwh: float \| None = None, delivered_kwh: float = 0.0, charge_now: bool = False, smart_charging: bool = True, user_current_limit: int \| None = None, plugged_in_at: datetime \| None = None)` | The car on one charger; `ready_time` is naive local time. |
| `PriceInterval(start: datetime, end: datetime, price: float)` | One priced interval on quarter boundaries. |
| `PriceSeries(intervals: tuple[PriceInterval, ...], updated_at: datetime)` | The price series; intervals must not overlap. |
| `PhaseCurrents(measured_at: datetime, l1: float \| None, l2: float \| None, l3: float \| None)`; `get(self, phase: Phase) -> float \| None` | Measured current per phase for the whole connection. |
| `Settings(phase_budget: float, phase_threshold: float, fallback_current: int, timezone: str, voltage: float = 230.0, min_block_quarters: int = 2, price_cap: float \| None = None, default_energy_need_kwh: float = 30.0, max_price_age: timedelta = timedelta(minutes=60), max_measurement_age: timedelta = timedelta(seconds=60))` | Installation settings; budget, threshold, fallback and timezone are required. |
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
models before and after the planning model. Deterministic: one thread, a fixed node budget
per solve (`NODE_LIMIT`), never a wall-clock limit; all time arithmetic in UTC. No greedy
fallback: a solve that ends without a point raises `SolverError`.

| Signature | Description |
|---|---|
| `class SolverError(RuntimeError)`; `__init__(self, status: int, message: str) -> None`; attributes `status`, `message`; `str(err) == f"HiGHS status {status}: {message}"` | The solver failed; carries scipy's `milp` status and message. |
| `NODE_LIMIT: int` | Branch-and-bound nodes allowed per level solve (8000; see the benchmark in the round-2 plan). |
| `class Tier(IntEnum)`: `SMART_OFF = 0`, `CHARGE_NOW = 1`, `PLANNED = 2`, `SPARE = 3` | Which precedence stage a demand belongs to. |
| `Demand(charger_id: str, phases: frozenset[Phase], tier: Tier, min_current: float, max_current: float, now_max_current: float, need_kwh: float = 0.0, deadline: datetime \| None = None)` | One charger asking for current, given in ranking order; validates itself with `ValueError` naming the field. |
| `Outcome(allocations: dict[str, float], plans: dict[str, SessionPlan], proven: bool)` | Quarter-0 current of every demand, a plan per `PLANNED` demand, `proven` False when a solve stopped at the node budget with a point. |
| `optimize(demands: Sequence[Demand], now: datetime, prices: Mapping[datetime, float], capacity: Mapping[Phase, float], now_capacity: Mapping[Phase, float], voltage: float, min_block_quarters: int, price_cap: float \| None, timezone: str) -> Outcome` | Solve the lexicographic model; `SolverError` on failure, `ValueError` for a naive `now` or a repeated charger id, `ZoneInfoNotFoundError` for an unknown `timezone`; empty `demands` returns before those checks; a limit broken by rounding raises `SolverError` with status 0. |
| `main() -> None` | Showcase: a charge-now car, two planned cars and a spare-capacity car on one phase, then a phase with no room. |

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
`chargers[1].max_current`.

| Signature | Description |
|---|---|
| `snapshot_to_dict(snapshot: Snapshot) -> dict[str, object]` | Snapshot to a plain dict. |
| `snapshot_from_dict(data: Mapping[str, object]) -> Snapshot` | Dict to snapshot; `ConfigError` with a dotted path on bad input. |
| `decision_to_dict(decision: Decision) -> dict[str, object]` | Decision to a plain dict. |
| `decision_from_dict(data: Mapping[str, object]) -> Decision` | Dict to decision, same error rules. |
| `main() -> None` | Showcase: snapshot to JSON and back; a rejected bad field. |

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
the `main` showcase.

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
thread and the node budget; a node-budget stop with a point being not proven and a status 4
below the limit failing; a `hypothesis` property that no limit is ever broken (T1) and one
that the level-by-level tuple equals a brute-force enumeration (T2); and the planned tier's
full progressive fill (8 / 11 / 11 on 30 A, 8 / 8 for two cars forced into an unpriced first
quarter, the ranking remainder when no car can be saturated); and the `main`
showcase.

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
between interleaved snapshots, the solver's failure statuses raising `SolverError` from `decide` (including a later level stopping without a point), node-budget stops (stubbed and real) adding `PLAN_NOT_PROVEN_OPTIMAL` last and round-tripping the codec, the solver call spied, decisions equal under permuted chargers, states and sessions, planned fractional on/off chargers, the fallback capping only the first planned quarter, a ready time inside the current quarter, tiny needs planned in the ready-time quarter (or now with a one-quarter block), the first-ranked planned car charging first on equal prices and a plan below its max staying normal, a mid-quarter ready time credited the whole quarter (pinned, not endorsed), unpriced and empty price series, the price-cap event boundary, the tiers' waiting reason codes and stale prices sharing evenly (T4-T7), the two step-6 snapshots HiGHS called infeasible without presolve (explicit literals), and the public surface equal to round 1's plus `SolverError`, `PLAN_NOT_PROVEN_OPTIMAL` and `optimize`'s names (exports, removed modules and names, dataclass fields, signatures, enum members); and the `main` showcase.

### `tests/test_ev_codec.py`

Covers `src/energy_optimization/ev/codec.py` (T2, T19): JSON round trips of snapshots and decisions (negative
prices, odd offsets, `None` measurements, every enum, parameter types kept), plain-type
output, and rejection of missing, unknown, wrongly typed, huge, null-on-defaulted and
invalid fields with the exact dotted path; the literal round-1 key sets of snapshot and
decision dicts; the rejection of `memory`, the 15 round-2 settings and the round-2
decision keys, each naming that field; and the `main` showcase.

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
