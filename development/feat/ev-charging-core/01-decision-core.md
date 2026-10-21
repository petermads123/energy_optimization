# EV charging decision core

<!-- claude-plan step=8 status=done -->

| Field | Value |
|---|---|
| Feature | `feat/ev-charging-core` |
| Round | `1` |
| Branch | `feat/ev-charging-core` |
| Started | `2026-10-21` |

## Progress

| # | Step | Skill | Runs | Status |
|---|---|---|---|---|
| 1 | Conceptualize | `/conceptualize` | with the user | done |
| 2 | Plan | `/plan` | with the user | done |
| 3 | Implement | `/implement` | in `/build` | done |
| 4 | Verify | `/verify` | in `/build` | done |
| 5 | Test | `/test` | in `/build` | done |
| 6 | Concept check | `/concept-check` | in `/build` | done |
| 7 | Ship | `/ship` | in `/build` | done |
| 8 | Recommend | `/recommend` | with the user | done |
| 9 | Pull request | `/create-pr` | with the user | pending |
| 10 | Review | `/watch-pr` | on the pull request | pending |

Statuses: `pending`, `in progress`, `done`.

## Builds on

Nothing — this is the first round.

The source concept is the user's document "Smart EV charging — concept for the optimization
package" (sections §1–§19 below refer to it). Its acceptance scenarios are cited as
"doc scenario N". This round covers the single-call decision; round 2 on this branch covers
behaviour over time (see Explicitly out of scope).

---

## 1. Concept

### What this is

A pure Python decision function for any number of EV chargers behind one shared grid
connection. Each call receives a complete snapshot — the time now, the price series, the
chargers and their current state, the sessions, the measured phase currents and the
settings — and answers with what every charger should be set to now, the actions to get
there in a safe order, each charger's plan and a plain-language reason. It recomputes
everything on every call (every quarter-hour and on events); no plan ever locks in. It does
no I/O, never reads the clock, and depends on nothing outside the standard library.

The package is generic — no entity IDs, no household names, no Home Assistant — but its
first caller is an AppDaemon app in Home Assistant, and the input/output shapes are chosen
so that app is thin: plain-dict conversion, missing values allowed, absolute action
targets, price intervals passed nearly as HA integrations deliver them.

### Why it is worth building

Several cars share one grid connection with hard per-phase fuse limits and a spot price
that changes every quarter. Without a decision core they either trip the fuse, charge at
expensive times, or one car takes everything while another misses its morning deadline. A
generic core with no HA code can be tested exhaustively on its own; the AppDaemon app then
only gathers input and executes actions.

### Inputs and outputs

**Inputs (one snapshot per call):**

- **Time:** now, timezone-aware. Naive timestamps are rejected.
- **Price series:** a list of intervals, each with start, end and price (full consumer
  price, one unit throughout). An interval may span any whole number of quarters (15-min
  spot prices, hourly tariffs); the package splits it into quarters. Also: when the series
  was last updated. Quarters not covered by any interval (e.g. tomorrow before
  publication) are *unpriced*.
- **Chargers**, each with:
  - an id;
  - the phases it is wired to — a non-empty set from L1/L2/L3 — and it is counted on each;
  - a **kind**, described by what it can do, not as separate logic:
    - **adjustable** — 0, or any whole number of amps from min to max;
    - **on/off** — 0, or its assumed current (unmeasurable, always accounted at the assumed
      value);
    - **fixed** — does not communicate and is stuck at a given level; never controlled;
  - min and max current (adjustable), assumed current (on/off), fixed level (fixed);
  - an optional **fallback current** overriding the global one;
  - state: car connected?, current setting, paused?, measured power (may be missing),
    available?
- **Session per charger** (when a car is connected): energy need, *or* SOC + battery
  capacity + target SOC (SOC may be missing); energy delivered in the session; ready time
  (a time of day); "charge now" on/off; smart charging on/off; the user's own current limit
  (optional).
- **Measured phase currents** for the whole connection (L1/L2/L3), each possibly missing,
  and when they were measured.
- **Settings:** per-phase budget for the chargers, phase-guard threshold and global
  fallback current — installation values, **required, no default**; the installation's
  timezone as an IANA name (e.g. `Europe/Copenhagen`), required; voltage (230 V); minimum
  contiguous quarters (2); price cap (off); default energy need (30 kWh); max age of prices
  (60 min); max age of phase measurements (60 s).

**Outputs:**

- **Ordered actions:** every decrease and pause (down phase) before every increase and
  resume (up phase). Each carries an **absolute target** — set to N A, pause, resume — so a
  repeated or retried action is harmless. No action for a charger whose setting does not
  change, and never one for a fixed or unavailable charger.
- **Per charger:** allocation (A), priority (1–4, or none), a reason — stable code, its
  parameters and an English sentence, e.g. "Priority 3 — plan completed, charging on spare
  capacity" — the plan (upcoming quarters with price; unpriced quarters flagged), expected
  finish time, and whether the target is reached by the ready time.
- **Overall:** available amps per phase and a status: normal / limited by budget / limited
  by phase / waiting for capacity / phase data stale – fallback / prices stale.
- **Events:** target will not be reached (with the kWh it will reach), price cap exceeded
  to meet the ready time, charge-now need covered, data stale.

Both the snapshot and the result convert to and from plain JSON-compatible dicts (ISO 8601
times with offset, numbers, strings, lists; `null` for a missing measurement), so the app
builds the input from entity states and publishes the result as sensor attributes.

**Decisions taken with the user:**

- **Order of service.** (1) Fixed and unavailable chargers are reserved first, on their
  phases, at the higher of their fixed level / last known setting and measured draw. This
  is stricter than doc §16, which counted an unavailable charger as 0 in the budget: the
  user's "fixed position" case is a charger that does not communicate, and the others must
  take its draw into account. (2) **Smart charging off** chargers come next, at the lower
  of their max and the user's limit — no price planning, and cut only after every smart
  charger is already at 0. (3) Smart chargers share the rest by priority tier.
- **Priority (doc §6):** 1 charge now; 2 the current quarter is in the plan; 3 ready time
  passed or plan completed but the car can take more; 4 planned quarters lie in the future.
  Only the highest occupied tier gets current. 0 or ≥ min, never between. Ranking: earliest
  ready time, then largest remaining need. Equal share, floored to whole amps, capped at the
  lower of max and the user's limit; remainder 1 A at a time in ranking order.
- **Mixed phases share max-min fairly:** every charger in the active group rises 1 A at a
  time until a phase it uses is full; remainders go in ranking order.
- **Unknown prices: known quarters only.** If published quarters cannot cover the need
  before the ready time, the *latest* unpriced quarters are added and the plan is flagged
  as containing unpriced quarters. A stale price series (older than the max age) means no
  plan at all: smart sessions without charge-now get priority 3, status "prices stale".
- **Fallback under doubt (replaces doc §12.5 and the "all paused" safe state).** With phase
  measurements stale or missing, every controllable charger is capped at its own fallback
  current, or the global one (e.g. 6 A). A charger whose min — or whose on/off assumed
  current — is above its fallback gets 0. Tiers and the per-phase budget still decide who
  charges; only the measured phase-guard term drops out. Increases up to the fallback are
  allowed. There is no separate "stale for long" stage.
- **Safety always wins:** "charge now" and price never override the budget, the phase-guard
  limit or the fallback cap.
- **No SOC and no need:** the default need from settings is used, and the reason says so.
- **Missing charger power:** assume the charger draws its current setting. Contradictory
  input (e.g. a paused charger with measured power) resolves to whatever charges least.
- **Dev dependencies:** `hypothesis` (property-based tests) and `tzdata` (so `zoneinfo`
  works in tests on Windows). The package itself has no runtime dependencies.

### How it connects to the rest of the repo

`src/energy_optimization/` gains the decision core; the placeholder `hello_world.py` and
`tests/test_hello_world.py` are removed. Nothing in the repo calls it yet. Its caller is the
user's AppDaemon app outside this repo, which reads HA states, builds the snapshot, calls
the package, executes the down-phase actions, verifies them, executes the up phase, and
calls again. The AppDaemon add-on runs Python 3.13, matching `requires-python = ">=3.13"`;
because the package has no dependencies it can be installed by copying the package folder
into the apps directory. Nothing under `.claude/` is touched.

### Explicitly out of scope

- **Round 2 on this branch — behaviour over time:** memory between calls (and a
  conservative start when it is missing or invalid), minimum on/off pause, selection
  hysteresis, deadband, ramp-up, under-use reduction, the resume probe, the phase guard's
  yellow/red/emergency levels over time, and the minimum time between actions on one
  charger. Doc scenarios 6, 8–11, 16 and 21.
- An example AppDaemon app and a README "Using from Home Assistant" section — noted in
  `DEVELOPMENT.md`.
- Judging the age of a car's SOC reading — the caller's job for now; noted in
  `DEVELOPMENT.md`.
- Not at all: exact (MILP) optimization, solar surplus, billing per household, coordination
  with other large loads, V2G.
- Never part of the package: fetching prices, talking to chargers, cars or HA, executing or
  verifying actions, persisting anything.

### Acceptance criteria

| # | The finished feature... |
|---|---|
| A1 | **Rejects invalid configuration** with an error naming the offending field, never a best guess: min > max, negative or zero limits, unknown or duplicate phases, duplicate charger ids, a session for an unknown charger, naive timestamps, an unknown timezone name, and a missing budget, threshold or global fallback. |
| A2 | **Assigns priority 1–4 per doc §6** to every connected smart charger; only the highest occupied tier gets current; each gets 0 or ≥ its min; ranking is earliest ready time then largest remaining need; the split is equal, floored, capped at the lower of max and the user's limit, with the remainder 1 A at a time in ranking order; with less than min available everyone gets 0 and the status is "waiting for capacity". Doc scenarios 1–5. |
| A3 | **Respects amp limits.** Per phase, the allocations to smart chargers stay within the budget minus what is reserved, and within threshold − (measured − what the chargers are set to); a three-phase charger counts on all three; mixed phases share max-min fairly. On/off chargers are all-or-nothing at their assumed current. Fixed and unavailable chargers receive no actions and are reserved at the higher of their fixed level / last setting and measured draw. Smart-off chargers are served at the lower of max and the user's limit and cut only after every smart charger is at 0. Doc scenarios 7, 12, 20. |
| A4 | **Plans the cheapest quarters before the ready time.** The horizon runs from now to the next occurrence of the ready time in the settings' timezone (23- and 25-hour days give 92 and 100 quarters); price intervals of any whole number of quarters are split into quarters; quarters needed = ceil(remaining need / (V × A × phases / 4)); quarters come in contiguous blocks of at least the minimum length; the most urgent session (least slack) claims shared capacity first and no quarter is planned beyond the per-phase budget; the price cap is broken only to meet the ready time, with an event; an unreachable target plans every quarter with an event stating the kWh it will reach; unpriced quarters are used only, latest first, when priced ones cannot cover the need, and the plan is flagged. Doc scenarios 14, 15, 17 (known quarters only), 18. |
| A5 | **Falls back under doubt.** With stale or missing phase measurements, every controllable charger is capped at its own fallback current or the global one; chargers that cannot run that low get 0; budget and tiers still apply; status "phase data stale – fallback" with an event. A stale price series means no plan, smart sessions at priority 3, status "prices stale" with an event. Contradictory input resolves to whatever charges least; missing charger power is taken as drawing its setting. Doc scenario 13, revised. |
| A6 | **Returns explainable, ordered output.** Every decrease and pause precedes every increase and resume; each action carries an absolute target; no action where nothing changes. Each charger carries its allocation, priority, reason (stable code, parameters, English sentence), plan with prices and unpriced quarters flagged, expected finish time and whether the ready time is met. The result carries available amps per phase, the status and the events listed above. Doc scenario 19. |
| A7 | **Holds the doc §12 invariants for any input:** under randomly generated inputs every output satisfies 0 or min…max, the per-phase budget, the phase-guard limit when measurements are fresh, the fallback cap when they are not, and down before up — shown by a property-based test. Doc scenario 22. |
| A8 | **Is deterministic and dependency-free:** the same input gives an equal output, and the package imports nothing outside the standard library. |
| A9 | **Round-trips plain dicts:** a snapshot and a result both convert to plain JSON-compatible dicts (ISO 8601 times with offset, numbers, strings, lists, `null` for a missing measurement) and back without loss; a dict with a missing or wrongly typed required field is rejected with an error naming the field. |
| A10 | **Is fast:** one call with 10 chargers and a 48-hour price series completes in under 100 ms on the development machine. |

### Open questions

None.

---

## 2. Plan

Accepted by the user in advance, before it was written: "You don't need to stop and wait
for my permission before moving on from the plan, start immediately step 3 after the plan
is done and reviewed". Step 3 opens once the `plan-critic` findings below are applied.

### Approach

**Chosen: a subpackage `energy_optimization.ev` of small modules around one orchestrator,
with defensive, validated input types.** Frozen dataclasses for every input and output;
each validates itself in `__post_init__` and raises `ConfigError` naming the field, so an
invalid snapshot cannot exist and `decide` never has to re-check. Time and price handling
(`quarters`), price planning (`planning`) and current sharing / phase limits
(`allocation`) are pure functions over plain values with no knowledge of each other;
`decide` is the only module that knows the priority rules and wires them together;
`codec` is the only module that knows the dict shape. The repo is meant to grow into
broader energy optimization, so the EV core lives in its own subpackage rather than at the
package root.

**Rejected.** *One `ev.py` module* — the minimal shape — would be well over a thousand
lines with planning, sharing, priority rules and dict conversion interleaved, and the
property tests would have no seams to test the sharing and planning rules on their own.
*A capability-object design* (each charger kind a class with `can_set(current)` methods)
reads well but scatters the 0/min, all-or-nothing and reservation rules across classes;
here every kind reduces to two numbers — min and max current, equal for all-or-nothing —
plus "controllable or reserved", which is what doc §11 asks for ("a description of what
they can do, not new logic"). A new kind is a new `ChargerKind` member and one branch in
the reduction, nothing else.

### Modules

| Path | New or changed | Purpose |
|---|---|---|
| `src/energy_optimization/hello_world.py` | deleted | Placeholder; the package has real code now |
| `tests/test_hello_world.py` | deleted | Its test |
| `src/energy_optimization/ev/__init__.py` | new | Re-exports the public API: the model, the result types, `decide` and the codec |
| `src/energy_optimization/ev/model.py` | new | Input types and their validation; `ConfigError` (A1) |
| `src/energy_optimization/ev/result.py` | new | Output types: actions, reasons, per-charger decisions, status, events (A6) |
| `src/energy_optimization/ev/quarters.py` | new | Quarter grid, ready-time deadlines across DST, splitting price intervals, price freshness (A4) |
| `src/energy_optimization/ev/planning.py` | new | Remaining need, energy per quarter, cheapest-block planning with shared capacity, price cap, unpriced quarters (A4) |
| `src/energy_optimization/ev/allocation.py` | new | Phase capacity (budget ∩ phase guard), present draw, reservations, measurement freshness, max-min-fair 0/min sharing (A2, A3, A5) |
| `src/energy_optimization/ev/decide.py` | new | `decide`: classification, priority tiers, orchestration, actions, reasons, status, events (A2–A8, A10) |
| `src/energy_optimization/ev/codec.py` | new | Snapshot and Decision to and from plain JSON-compatible dicts (A1, A9) |
| `tests/test_ev_model.py` … `tests/test_ev_codec.py` | new | One test file per module; property-based invariants and the performance check live in `tests/test_ev_decide.py` |
| `pyproject.toml` | changed | Dev dependencies `hypothesis>=6.170,<7` and `tzdata>=2026.5` |
| `STRUCTURE.md` | changed | Tree gains `ev/`; a section per `ev/` module and test file by full path (so the stop gate sees them) with its signature table; hello_world entries removed. The tables stay here this round rather than in `.claude/rules/`: section 1 touches nothing under `.claude/`, and the `structure-auditor` reads only STRUCTURE.md |

### Public API

Every type below is a `@dataclass(frozen=True)` unless it is an enum (`enum.StrEnum`) or
an exception. Tuple-typed fields also accept any sequence and are converted to a tuple in
`__post_init__` (via `object.__setattr__`), so equality and repr are stable. **Wherever output depends on a set of phases — text, params, lists, dict order — iterate phases in `Phase` order (L1, L2, L3), never in `frozenset` order**, which is hash-randomised between processes.

**`ev/model.py`**

| Signature | Purpose | Covers |
|---|---|---|
| `class Phase(StrEnum)`: `L1 = "L1"`, `L2 = "L2"`, `L3 = "L3"` | A phase of the grid connection | A3 |
| `class ChargerKind(StrEnum)`: `ADJUSTABLE = "adjustable"`, `ON_OFF = "on_off"`, `FIXED = "fixed"` | What a charger can do | A3 |
| `class ConfigError(ValueError)` with `__init__(self, field: str, message: str) -> None`; attribute `field: str`; `str(err) == f"{field}: {message}"` | Invalid input, naming the field | A1, A9 |
| `Charger(id: str, phases: frozenset[Phase], kind: ChargerKind, min_current: int \| None = None, max_current: int \| None = None, assumed_current: float \| None = None, fallback_current: int \| None = None)` | A charge point. Adjustable: `min_current`/`max_current` required, `0 < min ≤ max`, `assumed_current` must be None. On/off: `assumed_current > 0` required (its draw when on), min/max must be None. Fixed: `assumed_current > 0` required (the level it is stuck at), min/max/fallback must be None. `fallback_current` (adjustable, on/off) overrides the global fallback, `≥ 0`. `id` non-empty after strip; `phases` non-empty, every member a `Phase`. | A1, A3, A5 |
| `ChargerState(charger_id: str, car_connected: bool, current_setting: float, paused: bool, measured_power_w: float \| None = None, available: bool = True)` | A charger's state now. `current_setting ≥ 0` (A; for a paused charger the value it resumes at); `measured_power_w` `None` = missing, else `≥ 0`. | A1, A3, A5 |
| `Session(charger_id: str, ready_time: time, energy_need_kwh: float \| None = None, soc_percent: float \| None = None, target_soc_percent: float \| None = None, battery_capacity_kwh: float \| None = None, delivered_kwh: float = 0.0, charge_now: bool = False, smart_charging: bool = True, user_current_limit: int \| None = None, plugged_in_at: datetime \| None = None)` | The car on one charger. `ready_time` must be naive (local to `Settings.timezone`); need, delivered `≥ 0`; SOC and target in `[0, 100]`, target `> 0`; capacity `> 0`; user limit `≥ 0`; `plugged_in_at` aware. | A1, A2, A4 |
| `PriceInterval(start: datetime, end: datetime, price: float)` | One priced interval. Aware; `end > start`; both on a quarter boundary in UTC (minute % 15 == 0, zero seconds and microseconds); `price` finite (may be negative). | A1, A4 |
| `PriceSeries(intervals: tuple[PriceInterval, ...], updated_at: datetime)` | The price series. `updated_at` aware; intervals may come in any order but must not overlap. | A1, A4, A5 |
| `PhaseCurrents(measured_at: datetime, l1: float \| None, l2: float \| None, l3: float \| None)` | Measured current per phase for the whole connection. Aware; each value `None` (missing) or `≥ 0`. Method `get(self, phase: Phase) -> float \| None`. | A1, A3, A5 |
| `Settings(phase_budget: float, phase_threshold: float, fallback_current: int, timezone: str, voltage: float = 230.0, min_block_quarters: int = 2, price_cap: float \| None = None, default_energy_need_kwh: float = 30.0, max_price_age: timedelta = timedelta(minutes=60), max_measurement_age: timedelta = timedelta(seconds=60), emergency_threshold: float \| None = None, emergency_calm: timedelta = timedelta(minutes=5), yellow_margin: float = 2.0, green_hold: timedelta = timedelta(seconds=60), deadband: float = 2.0, ramp_step: int = 2, ramp_period: timedelta = timedelta(minutes=5), under_use_period: timedelta = timedelta(minutes=15), reduction: Reduction = Reduction.TO_USE, under_use_margin: float = 1.0, min_pause: timedelta = timedelta(minutes=15), hysteresis: timedelta = timedelta(minutes=30), resume_probe: timedelta = timedelta(minutes=60), min_action_interval: timedelta = timedelta(seconds=30), max_memory_age: timedelta = timedelta(minutes=30))` | Installation settings. Budget, threshold, voltage `> 0`; fallback `≥ 0`; `timezone` must load with `zoneinfo.ZoneInfo`; `min_block_quarters ≥ 1`; default need `≥ 0`; both ages `> 0`; `price_cap` finite when set. *Extended in round 2 (`02-behaviour-over-time.md`); signature updated at step 9.* | A1, A3, A4, A5 |
| `Snapshot(now: datetime, settings: Settings, chargers: tuple[Charger, ...], states: tuple[ChargerState, ...], sessions: tuple[Session, ...], prices: PriceSeries, phase_currents: PhaseCurrents \| None = None, memory: Memory \| None = None)` | One call's input. `now` aware; charger ids unique; exactly one state per charger and none for an unknown id; at most one session per charger, none for an unknown id. A session on a fixed charger is accepted and ignored for control. `phase_currents=None` = missing. *Extended in round 2 (`02-behaviour-over-time.md`); signature updated at step 9.* | A1 |

Numeric fields reject `bool` and non-finite floats; float fields accept ints. A1's "negative or zero limits" means `phase_budget`, `phase_threshold`, `voltage`, `min_current`, `max_current` and `assumed_current` (all `> 0`); `fallback_current` and `user_current_limit` may be 0 on purpose ("never charge under doubt", "user says stop"). `ConfigError.field` is the dataclass
field name (`"max_current"`), and the message names the offending value and, inside a
charger, its id: `ConfigError("max_current", "charger 'carport': must be ≥ min_current (6), got 4")`.

**`ev/result.py`**

| Signature | Purpose | Covers |
|---|---|---|
| `class Stage(StrEnum)`: `DOWN = "down"`, `UP = "up"` | Which phase of execution an action belongs to | A6 |
| `class ActionKind(StrEnum)`: `SET_CURRENT = "set_current"`, `PAUSE = "pause"`, `RESUME = "resume"` | What to do to a charger | A6 |
| `Action(charger_id: str, kind: ActionKind, target_current: float, stage: Stage)` | One change with an absolute target: `PAUSE` has `target_current=0.0`; `RESUME` carries the current to resume at | A6 |
| `class ReasonCode(StrEnum)`: `CHARGE_NOW`, `IN_PLAN`, `READY_TIME_PASSED`, `PLAN_COMPLETED`, `PRICES_STALE`, `PLANNED_LATER`, `WAITING_FOR_HIGHER_PRIORITY`, `WAITING_FOR_CAPACITY`, `TARGET_REACHED`, `BELOW_MINIMUM`, `SMART_CHARGING_OFF`, `FIXED`, `UNAVAILABLE`, `NO_CAR`, `NO_SESSION`, `MINIMUM_PAUSE`, `UNDER_USE`, `EMERGENCY` — values are the lower-case names | Stable reason codes *Extended in round 2 (`02-behaviour-over-time.md`); signature updated at step 9.* | A6 |
| `Reason(code: ReasonCode, text: str, params: dict[str, str \| float \| int \| bool] = field(default_factory=dict))` | Why a charger got what it got, as a code, its parameters and an English sentence | A6 |
| `PlannedQuarter(start: datetime, price: float \| None, current: float)` | One planned quarter: its start (in the settings timezone), its price (`None` = unpriced) and the planned amps | A4, A6 |
| `ChargerDecision(charger_id: str, allocation: float, priority: int \| None, reason: Reason, plan: tuple[PlannedQuarter, ...] = (), remaining_need_kwh: float \| None = None, deadline: datetime \| None = None, expected_finish: datetime \| None = None, reaches_target: bool \| None = None)` | Everything decided for one charger. For a fixed or unavailable charger `allocation` is the current reserved for it | A6 |
| `class Status(StrEnum)`: `EMERGENCY`, `NORMAL`, `LIMITED_BY_BUDGET`, `LIMITED_BY_PHASE`, `WAITING_FOR_CAPACITY`, `PHASE_DATA_STALE`, `PRICES_STALE` — values are the lower-case names | Overall status *Extended in round 2 (`02-behaviour-over-time.md`); signature updated at step 9.* | A2, A5, A6 |
| `class EventKind(StrEnum)`: `TARGET_UNREACHABLE`, `PRICE_CAP_EXCEEDED`, `CHARGE_NOW_NEED_COVERED`, `PHASE_DATA_STALE`, `PRICES_STALE`, `MEMORY_RESET`, `PHASE_GUARD_INTERVENED` — values are the lower-case names | Notification-worthy events *Extended in round 2 (`02-behaviour-over-time.md`); signature updated at step 9.* | A4, A5, A6 |
| `Event(kind: EventKind, text: str, charger_id: str \| None = None, params: dict[str, str \| float \| int \| bool] = field(default_factory=dict))` | One event | A6 |
| `PhaseAmps(l1: float, l2: float, l3: float)` with `get(self, phase: Phase) -> float` | Amps per phase | A6 |
| `Decision(actions: tuple[Action, ...], chargers: tuple[ChargerDecision, ...], available: PhaseAmps, status: Status, events: tuple[Event, ...], memory: Memory \| None = None, guard_level: GuardLevel \| None = None, next_check: datetime \| None = None)` | One call's answer; `chargers` in snapshot charger order *Extended in round 2 (`02-behaviour-over-time.md`); signature updated at step 9.* | A6 |

**`ev/quarters.py`**

| Signature | Purpose | Covers |
|---|---|---|
| `QUARTER: timedelta = timedelta(minutes=15)` | The planning step | A4 |
| `floor_to_quarter(moment: datetime) -> datetime` | Start of the quarter containing an aware moment, in UTC; `ValueError` if naive | A4 |
| `next_occurrence(ready_time: time, after: datetime, timezone: str) -> datetime` | First moment strictly after `after` whose local time in `timezone` is `ready_time`, in UTC. A ready time inside a spring-forward gap is shifted forward by the gap's length, as `astimezone` gives (02:30 on 2026-03-29 in Copenhagen → 03:30 CEST = 01:30 UTC); inside a fall-back fold it is the first occurrence (02:30 on 2026-10-25 → 02:30 CEST = 00:30 UTC) *unless `after` is already at or past it, when the second occurrence (01:30 UTC) is the first moment strictly after* (step 5). `ready_time.fold` is ignored. `ValueError` if `after` is naive | A4 |
| `quarter_starts(start: datetime, end: datetime) -> list[datetime]` | UTC quarter starts from `floor_to_quarter(start)` while `< end`; empty when `end <= start`; `ValueError` if either is naive (step 5) | A4 |
| `quarter_prices(series: PriceSeries) -> dict[datetime, float]` | Every interval split into quarters, keyed by UTC quarter start | A4 |
| `prices_fresh(series: PriceSeries, now: datetime, max_age: timedelta) -> bool` | `abs(now - series.updated_at) <= max_age` — a timestamp far in the future is as untrustworthy as one far in the past; `ValueError` if `now` is naive (step 5) | A5 |

**`ev/planning.py`**

| Signature | Purpose | Covers |
|---|---|---|
| `class NeedSource(StrEnum)`: `ENERGY = "energy"`, `SOC = "soc"`, `DEFAULT = "default"` | Where a remaining need came from | A4, A6 |
| `remaining_need_kwh(session: Session, default_need_kwh: float) -> tuple[float, NeedSource]` | `energy_need_kwh − delivered` if a need is given; else `(target − soc) / 100 × capacity` if all three SOC fields are given; else `default_need_kwh − delivered`. Clipped at 0 | A4 |
| `energy_per_quarter_kwh(current: float, phase_count: int, voltage: float) -> float` | `voltage × current × phase_count / 4 / 1000` | A4 |
| `PlanRequest(charger_id: str, phases: frozenset[Phase], min_current: float, max_current: float, need_kwh: float, deadline: datetime, skip_current: bool = False)` | One session to plan; `min_current == max_current` means all-or-nothing *Extended in round 2 (`02-behaviour-over-time.md`); signature updated at step 9.* | A4 |
| `SessionPlan(charger_id: str, quarters: tuple[PlannedQuarter, ...], planned_kwh: float, reachable: bool, price_cap_exceeded: bool, uses_unpriced: bool)` | One session's plan, quarters in time order | A4 |
| `plan_sessions(requests: Sequence[PlanRequest], now: datetime, prices: Mapping[datetime, float], capacity: Mapping[Phase, float], voltage: float, min_block_quarters: int, price_cap: float \| None, timezone: str, first: Sequence[str] = ()) -> dict[str, SessionPlan]` | Plan every request against shared per-phase capacity (constant over the horizon), most urgent first; keyed by charger id. `timezone` only sets the zone of `PlannedQuarter.start` *Extended in round 2 (`02-behaviour-over-time.md`); signature updated at step 9.* | A4 |

**`ev/allocation.py`**

| Signature | Purpose | Covers |
|---|---|---|
| `Claim(charger_id: str, phases: frozenset[Phase], min_current: float, max_current: float)` | One charger asking for current; `min_current == max_current` means all-or-nothing | A2, A3 |
| `share(claims: Sequence[Claim], capacity: Mapping[Phase, float]) -> dict[str, float]` | Share capacity among claims given in ranking order: 0/min selection in order, then max-min-fair 1 A increments; every claim's id is in the result | A2, A3 |
| `measurements_fresh(currents: PhaseCurrents \| None, now: datetime, max_age: timedelta, phases: frozenset[Phase]) -> bool` | Not None, `abs(now − measured_at) <= max_age`, and every phase in `phases` has a value | A5 |
| `present_draw(charger: Charger, state: ChargerState, voltage: float) -> float` | What a charger is taken to draw now on each of its phases, for the phase-guard calculation | A3 |
| `reserved_current(charger: Charger, state: ChargerState, voltage: float) -> float` | What a fixed or unavailable charger is reserved at on each of its phases | A3 |
| `phase_capacity(snapshot: Snapshot, fresh: bool, settling: frozenset[str] = frozenset()) -> dict[Phase, float]` | Per phase, what all chargers together may use: `min(budget, threshold − (measured − Σ present_draw))` when `fresh`, else `budget`; a phase whose measurement is `None` gets `budget` (when `fresh`, no controllable charger uses it). May be negative *Extended in round 2 (`02-behaviour-over-time.md`); signature updated at step 9.* | A3, A5 |

**`ev/decide.py`**

| Signature | Purpose | Covers |
|---|---|---|
| `decide(snapshot: Snapshot) -> Decision` | The decision core | A2–A8, A10 |

**`ev/codec.py`**

| Signature | Purpose | Covers |
|---|---|---|
| `snapshot_to_dict(snapshot: Snapshot) -> dict[str, object]` | Snapshot to a plain JSON-compatible dict | A9 |
| `snapshot_from_dict(data: Mapping[str, object]) -> Snapshot` | Dict to Snapshot; `ConfigError` with a dotted path field (`"chargers[1].max_current"`) for a missing, unknown, wrongly typed or invalid field | A1, A9 |
| `decision_to_dict(decision: Decision) -> dict[str, object]` | Decision to a plain JSON-compatible dict | A9 |
| `decision_from_dict(data: Mapping[str, object]) -> Decision` | Dict to Decision, same error rules | A9 |

Every module also has `main() -> None`, the showcase the conventions require.

### Implementation guide

1. **Dependencies.** Add `"hypothesis>=6.170,<7"` and `"tzdata>=2026.5"` to
   `[project.optional-dependencies] dev` in `pyproject.toml`, with a one-line comment that
   `tzdata` is for `zoneinfo` on Windows. `pip install -e ".[dev]"`. `[project]
   dependencies` stays empty.
2. **Remove the placeholder.** Delete `src/energy_optimization/hello_world.py` and
   `tests/test_hello_world.py` (`git rm`), and their STRUCTURE.md entries.
   `src/energy_optimization/__init__.py` keeps its relative-import docstring; its text now
   says the EV core is `energy_optimization.ev`.
3. **`ev/model.py`.** Enums, `ConfigError`, then the dataclasses in the order of the
   Public API table, each validating in `__post_init__` exactly the rules in its row.
   Shared private helpers: `_aware(field, value)`, `_finite(field, value)` (rejects bool,
   NaN, inf), `_whole(field, value)`. `Settings.__post_init__` loads the zone once to
   validate it (`ZoneInfoNotFoundError` and `ValueError` → `ConfigError("timezone", ...)`).
   `Snapshot.__post_init__` runs the cross-object checks. Showcase: build a valid snapshot
   with one adjustable and one fixed charger and print it; then build a charger with
   `min_current > max_current`, catch `ConfigError`, print it.
4. **`ev/result.py`.** The types only, no logic beyond `PhaseAmps.get`. Showcase: build a
   small `Decision` by hand and print its actions and one reason.
5. **`ev/quarters.py`.** All internal arithmetic in UTC. **Never compare two datetimes
   that share a `ZoneInfo` tzinfo** — Python then compares wall-clock time and ignores
   `fold`; convert to UTC first. `next_occurrence`: take `after` in the zone, build the
   candidate with `datetime.combine(local_date, ready_time, tzinfo=zone)`, normalise it with
   `.astimezone(UTC)` (this moves a gap time forward and picks fold=0 in a fold), and if it
   is not strictly after `after` (in UTC) repeat with the next local date. `quarter_prices`
   walks each interval in `QUARTER` steps. Showcase: a DST night in `Europe/Copenhagen` —
   the deadline and `len(quarter_starts(...))` for 07:00 → 07:00 across 2026-03-29 (92) and
   2026-10-25 (100).
6. **`ev/planning.py`.** `remaining_need_kwh` and `energy_per_quarter_kwh` per their rows.
   `plan_sessions`:
   1. For each request, its horizon is `quarter_starts(now, deadline)`.
   2. Urgency order: slack = number of horizon quarters − `ceil(need / energy at
      max_current)` assuming full capacity; least slack first, then earliest deadline,
      then `charger_id`.
   3. Per-quarter remaining capacity starts at `capacity[p]` for every quarter and phase.
   4. For one request, a quarter is *usable* when `r = min(remaining[q][p] for p in
      phases)` allows a current: all-or-nothing needs `r ≥ max_current`, planned at
      `max_current`; otherwise the planned current is `min(max_current, floor(r))`, usable
      when `≥ min_current`. Its energy is `energy_per_quarter_kwh(current, len(phases),
      voltage)`.
   5. If the energy of all usable quarters is below the need, the plan is **every** usable
      quarter, `reachable=False`.
   6. Otherwise select in passes until the selected energy reaches the need. Each usable
      quarter has a **category**: 1 = priced at or below `price_cap` (every priced quarter
      when there is no cap), 2 = priced above the cap, 3 = unpriced. Pass *k* may use
      quarters of categories ≤ *k*.
      - **Blocks first.** The unit of choice is a block: a run of `L = min_block_quarters`
        consecutive usable quarters (consecutive = starts `QUARTER` apart) whose categories
        are all ≤ *k*, which may overlap quarters already selected and may straddle
        categories. A block is eligible in pass *k* when it adds at least one new quarter
        and at least one of its new quarters is of category *k*. Its score is the mean price
        of its new quarters (pass 3: its latest new quarter's start, latest first; unpriced
        quarters have no price). Each step takes the lowest score, ties to the earliest
        start (pass 3: ties to the cheapest priced part, then latest). **Finishing rule:**
        let `R` be the energy still needed; when some eligible block *finishes* the plan —
        its new energy `≥ R` and its new energy minus the smallest energy among its new
        quarters `< R` — only finishing blocks are eligible.
      - **Then single quarters of category *k*,** cheapest first (pass 3: latest first),
        but only when pass *k* has no eligible block left and the need is still not met.
      - **Then pass *k + 1*.**
      **Precedence, stated outright:** contiguity is a preference (doc §7.6 "prefer blocks
      of a minimum length"); the price cap and known-before-unpriced are rules (doc §7.8,
      §1 "known quarters only"). So an isolated quarter under the cap is taken before a
      block that would break the cap, and a priced single quarter before an unpriced
      block. A4's "contiguous blocks" therefore holds whenever the cap, the horizon and
      the capacity leave room for blocks; it gives way only to those three.
      `price_cap_exceeded` is set when any chosen quarter is category 2, `uses_unpriced`
      when any is category 3 — in the unreachable case of step 5 too.
   7. Subtract the chosen currents from `remaining` on the request's phases, then plan the
      next request.
   8. `PlannedQuarter.start` is converted to `timezone`; quarters are returned in time
      order; `planned_kwh` is the sum of their energy.
   Showcase: two cars on a shared phase, the second planned around the first.
7. **`ev/allocation.py`.**
   - `present_draw` (controllable, available charger): 0 if `state.paused` or not
     `state.car_connected` (a charger without a car cannot draw); else nominal =
     `current_setting` (adjustable) or `assumed_current` (on/off). If `measured_power_w`
     is known, measured per-phase draw = `measured_power_w / (voltage × len(phases))`, and
     the result is `min(nominal, measured)`; else nominal. For a fixed or unavailable
     charger `present_draw` returns `reserved_current` (changed in step 5: its measured
     draw when power is known, `reserved_current` otherwise — see section 3). (The doc §13 formula subtracts the
     *setting*; taking the smaller of setting and measured draw estimates the other load
     as at least as large, so the free current is never larger than A3's bound — a car
     drawing less than it is set to no longer inflates the free current.)
   - `reserved_current`: fixed → `max(assumed_current, measured per-phase draw or 0)`;
     unavailable → `max(current_setting` for adjustable or `assumed_current` for on/off`,
     measured per-phase draw or 0)`. `paused` and `car_connected` are ignored for both: a
     charger that does not communicate, or has stopped communicating, is assumed to go on
     drawing its last known level.
   - `phase_capacity`: as its row; the Σ runs over every charger on that phase.
   - `share`: work on a copy of `capacity`. Pass 1, in claim order: a claim with `max <
     min` is skipped (0); otherwise include it at `min_current` if `min_current ≤
     remaining[p]` (tolerance `1e-9`) for every one of its phases, subtracting it. Pass 2:
     repeatedly take the included adjustable claim (min < max) with the **lowest current
     allocation**, ties to claim order, that is below its max and has `1.0` A free on every
     phase, and give it `+1.0` (capped at its max — a final step may be smaller when max is
     not whole, which cannot happen for validated input but keep the cap); stop when no
     claim can grow. Return every claim's id, excluded ones at `0.0`.
   Showcase: three claims on mixed phases sharing a constrained phase.
8. **`ev/decide.py`.** `decide(snapshot)`:
   1. `zone` = settings timezone; `now`; `prices_ok = prices_fresh(...)`; the
      controllable phases = union of phases of non-fixed, available chargers; `fresh =
      measurements_fresh(..., controllable phases)`; `fallback = not fresh`.
   2. **Classify** each charger, in snapshot order, by the first rule that applies:
      unavailable → reserved (`UNAVAILABLE`); fixed → reserved (`FIXED`; a session on it
      only reports its need); no car → 0 (`NO_CAR`); no session → 0 (`NO_SESSION`);
      `smart_charging` False → smart-off group (`SMART_CHARGING_OFF`; its need is reported
      but not acted on — an unmanaged charger charges until the car stops it);
      `soc_percent` known and `≥ 100` → 0 (`TARGET_REACHED`: the car cannot take more);
      `charge_now` → priority 1 (`CHARGE_NOW`), plus a `CHARGE_NOW_NEED_COVERED` event when
      its remaining need is 0 — it keeps priority 1 until the caller resets the flag (doc
      §8); remaining need 0 → priority 3 (`PLAN_COMPLETED`: "plan completed, charging on
      spare capacity", doc §6 and §1 Outputs); prices not fresh → priority 3
      (`PRICES_STALE`); deadline ≤ now → priority 3 (`READY_TIME_PASSED`); otherwise
      **to be planned** — step 6 plans it and then assigns priority 2 if the quarter
      containing `now` is in its plan (`IN_PLAN`), else priority 4 (`PLANNED_LATER`).
      **Deadline** = `next_occurrence(ready_time, plugged_in_at or now, zone)`: seen from
      the session start when the caller gives one (doc §9), else from now.
   3. **Effective max** for a controllable charger: adjustable `min(max_current,
      user_current_limit, fallback cap)`; on/off its `assumed_current`, set to "cannot run"
      when the user limit or the fallback cap is below it. Fallback cap (only when
      `fallback`) = `charger.fallback_current` if set, else `settings.fallback_current`. A
      charger whose effective max is below its minimum cannot run: allocation 0,
      `BELOW_MINIMUM` (its priority is still reported), and it does not occupy its tier.
   4. **Capacity now:** `cap = phase_capacity(snapshot, fresh)`; subtract each reserved
      charger's `reserved_current` on its phases; clip at 0. `available` in the result is
      this value per phase.
   5. **Smart-off group:** claims ranked by deadline, then larger remaining need, then id;
      `share` against `cap`; subtract.
   6. **Planning:** capacity per phase = `budget − reserved − smart-off allocations`,
      clipped at 0 (the phase guard is a now-quantity and is not projected); one
      `PlanRequest` per "to be planned" session: adjustable `min_current=min_current`,
      `max_current=min(max_current, user_current_limit)` (no fallback cap, per Risk e);
      on/off `assumed_current` for both. A session whose user limit is below its minimum
      is still requested: it plans nothing, so it is unreachable and raises
      `TARGET_UNREACHABLE`. `plan_sessions`, then priority 2 or 4 per step 2. Charge-now
      sessions are not planned and do not consume planning capacity.
   7. **Active tier:** the lowest of priorities 1, 2, 3 that has at least one charger that
      can run; nobody in priority 4 ever gets current. Active claims ranked by deadline
      (earliest first; for priority 3 the most overdue first), then larger remaining need,
      then id; `share` against the remaining `cap`. Chargers in lower tiers get 0 with
      `WAITING_FOR_HIGHER_PRIORITY` (priority 4 keeps `PLANNED_LATER` unless it is
      *starved*, below); an active-tier charger that got 0 gets `WAITING_FOR_CAPACITY`. A
      charger that can run, was planned and got an empty plan is *starved*: its reason is
      `WAITING_FOR_CAPACITY` and its priority stays 4, whatever used the planning capacity
      up (reservations, smart-charging-off allocations or another session's plan) *(changed
      in step 5 of the re-run: it was "below its minimum on one of its phases")*.
   8. **Actions**, compared with the present state (present = 0 if paused, else
      `current_setting`, for on/off `assumed_current` when not paused), never for reserved
      or carless chargers: target 0 and present > 0 → `PAUSE` (down); target > 0 and
      paused → `RESUME` at target (up); not paused and target ≠ present → `SET_CURRENT`,
      down if lower, up if higher; equal → none. Down actions, then up actions, each sorted
      by charger id.
   9. **Expected finish and reaches_target:** with a plan, the end of the first quarter at
      which cumulative planned energy reaches the need, else None; without a plan and with
      allocation > 0, `now + timedelta(hours=need_kwh * 1000 / (voltage * allocation * len(phases)))`; a need of 0 finishes at `now`; else None.
      `reaches_target` = finish is not None and finish ≤ deadline (False once the deadline
      has passed); None when there is no session. Datetimes in the result are in `zone`.
   10. **Events:** `TARGET_UNREACHABLE` per unreachable plan (params `reachable_kwh` =
       delivered + planned, `target_kwh` = delivered + need, `deadline` as an ISO 8601 string, since params hold only `str | float | int | bool`);
       `PRICE_CAP_EXCEEDED` per plan that broke the cap; `CHARGE_NOW_NEED_COVERED`;
       `PHASE_DATA_STALE` when `fallback`; `PRICES_STALE` when not `prices_ok`.
   11. **Status**, first that applies: `PHASE_DATA_STALE` if fallback; `PRICES_STALE` if
       prices stale; `WAITING_FOR_CAPACITY` if the active tier has a charger that can run
       and every active-tier charger got 0, or if a starved charger exists (a session that
       can run, was planned and got an empty plan — its reason is `WAITING_FOR_CAPACITY`,
       its priority stays 4) and every active-tier and every starved charger got 0 *(changed
       in the step-3 re-run after step 6, A2, and in step 5 of the re-run; see section 3)*;
       `LIMITED_BY_PHASE` if an active-tier, smart-off or starved charger got less than its
       effective max and one of its phases has
       `threshold − other load < budget`; `LIMITED_BY_BUDGET` if one got less than its
       effective max otherwise; `NORMAL`. A starved charger beside a served one therefore
       reads as limited, never `NORMAL`.
   12. **Reasons:** a code, params (always `priority` when there is one; `allocation`;
       `need_source` when a session exists; `capped_by` one of `"max"`, `"user_limit"`,
       `"fallback"`, `"budget"`, `"phase"` when an allocated charger is below its own max)
       and an English sentence, e.g. "Priority 3 — ready time 07:00 passed, charging on
       spare capacity"; a default need adds "(assumed need 30 kWh)".
   Showcase: three chargers (adjustable three-phase, adjustable single-phase, fixed) with
   two sessions; print status, every charger's allocation and reason, and the actions;
   then the same snapshot with stale measurements, to show the fallback.
9. **`ev/codec.py`.** Keys are the dataclass field names; enums by value; datetimes
   `isoformat()` (offset included) and parsed with `datetime.fromisoformat`, rejecting a
   naive result; `time` encoded with `time.isoformat()` and parsed with `time.fromisoformat` (so `"07:00"` and `"07:00:00"` both parse, and seconds survive the round trip); `timedelta` as seconds (number);
   `frozenset[Phase]` as a sorted list, where a duplicate is rejected; tuples as lists.
   Optional fields may be omitted. Unknown keys are rejected. Int fields accept an
   integral float; bool is never a number. Any `ConfigError` raised while building an
   object is re-raised with the dotted path prefixed (`"chargers[1].max_current"`).
   Showcase: snapshot → dict → `json.dumps` → back, printing the JSON and the equality.
10. **`ev/__init__.py`.** Relative re-exports of every public name above except the
    planning/allocation/quarters helpers (importable from their modules), with `__all__`.
11. **STRUCTURE.md.** Tree gains `ev/`; one section per `ev/` module and per test file,
    by full path, each with its signature table, in the existing style.

### Test intents

| # | Must prove | Covers |
|---|---|---|
| T1 | Every invalid case listed in A1 raises `ConfigError` whose `field` names the field, for direct construction (a missing budget, threshold or fallback is passed as `None`; omitting the argument is Python's `TypeError` and is proven through the codec in T2) — including fields that do not belong to a kind, an unloadable timezone, overlapping or misaligned price intervals, non-finite numbers and bools as numbers | A1 |
| T2 | `snapshot_from_dict` and `decision_from_dict` reject missing, unknown, wrongly typed and invalid fields with a dotted path, and a duplicated phase in a list | A1, A9 |
| T3 | Doc scenarios 1–5 through `decide`: tier exclusivity, 15/15 split, 7/7/0 with the latest ready time cut, all-0 with "waiting for capacity", earliest ready time wins at 6 A | A2 |
| T4 | `share`: 0/min selection in ranking order, a later claim on another phase still fits, floor and 1 A remainder in order, max and all-or-nothing respected, empty claims, zero or negative capacity | A2, A3 |
| T5 | Priority assignment: charge-now → 1, staying 1 with the `CHARGE_NOW_NEED_COVERED` event once its need is 0; current quarter in plan → 2; deadline passed (from `plugged_in_at`) → 3; need 0 with SOC below 100 or unknown → 3 `PLAN_COMPLETED`; SOC ≥ 100 → 0 `TARGET_REACHED`; plan later → 4; a priority-1 charger below its minimum does not occupy the tier; a session on a fixed charger is accepted and changes nothing | A2 |
| T6 | Doc scenarios 7 (the phase binds at 6 A), 12 (16 A three-phase + 10 A single-phase on a shared phase, budget 25 → 15 A and 10 A; the shared phase never exceeds the budget) and 20 (on/off accounted at its assumed current, never partially) | A3 |
| T7 | Fixed and unavailable chargers are reserved at the higher of level/setting and measured draw, receive no actions, and shrink everyone else's capacity — a fixed charger flagged `paused` is still reserved at its level, an unavailable on/off charger at its assumed current; smart-off chargers are served first and cut only when every smart charger is at 0; `present_draw` takes the smaller of setting and measured draw, and counts 0 when paused or without a car | A3, A5 |
| T8 | Doc scenario 14: 20 kWh at 11 kW in an 8-hour horizon plans 8 quarters with no run shorter than 2; on a hand-checked series where it is also the cheapest selection with runs ≥ 2, it matches the greedy selection of guide 6 | A4 |
| T9 | Doc scenario 15: an unreachable target plans every usable quarter and raises `TARGET_UNREACHABLE` with reachable and target kWh | A4 |
| T10 | Doc scenario 17 (known quarters only): published quarters are used first, unpriced quarters only when needed and latest first, flagged; supplying the prices changes the plan | A4 |
| T11 | Doc scenario 18: 92 and 100 quarters on the 2026 Copenhagen DST days, a correct deadline across midnight and across both transitions; 02:30 on 2026-03-29 → 01:30 UTC, 02:30 on 2026-10-25 → 00:30 UTC | A4 |
| T12 | Price cap skipped when the need fits under it, broken with `PRICE_CAP_EXCEEDED` when it does not; alternating prices around a cap give single quarters under the cap rather than a block above it (guide 6.6 precedence), while without a cap the same series gives no run shorter than L; hourly intervals split into four equal quarters; shared capacity plans the least-slack session first and never plans a phase beyond its capacity; the unreachable plan's `price_cap_exceeded`/`uses_unpriced` flags | A4 |
| T13 | Fallback: stale, missing (None object, a missing used phase, a reading dated beyond the max age in the future), while a `None` on a phase only fixed chargers use keeps fresh and gets the budget → every controllable charger ≤ its own fallback or the global one; below-min and on/off-above-fallback → 0; budget and tiers still apply; status and event. Stale prices → no plans, priority 3, status and event | A5 |
| T14 | Contradictory input charges least: a paused charger with measured power adds to the other load; measured draw above the setting does not raise free current; a charger without a car counts 0 in the phase-guard draw; a connected car with SOC above target needs 0 | A5 |
| T15 | Doc scenario 19: every down action precedes every up action; targets are absolute; no action when nothing changes, for reserved chargers or for chargers without a car; pause/resume/set-current chosen per the guide | A6 |
| T16 | Every charger carries the right reason code and params; `expected_finish` and `reaches_target` for planned, unplanned and passed-deadline sessions; `available` per phase; result datetimes are in the settings timezone | A6 |
| T17 | Property-based (`hypothesis`): for generated snapshots, every output holds 0 or min…effective max (on/off: 0 or assumed), Σ on each phase ≤ budget − reserved, ≤ the phase-guard limit when fresh, ≤ fallback when not, and down before up — computed independently in the test, not via the module's helpers | A7 |
| T18 | `decide` called twice on the same snapshot gives equal decisions, and output text and lists name phases in L1, L2, L3 order and leaves the snapshot equal to a deep copy taken before; every module under `src/energy_optimization/` imports only the standard library and itself (AST scan against `sys.stdlib_module_names`) | A8 |
| T19 | `snapshot_from_dict(json.loads(json.dumps(snapshot_to_dict(s)))) == s`, same for decisions, including `None` measurements, negative prices, non-UTC offsets and every enum value | A9 |
| T20 | 10 chargers and a 48-hour series: the best of five `decide` calls is under 100 ms | A10 |

**Coverage check.** Every criterion has a Public API entry: A1 model + codec; A2 `share`,
`decide`; A3 allocation helpers, `decide`; A4 quarters, planning; A5
`measurements_fresh`, `phase_capacity`, `decide`; A6 result types, `decide`; A7, A8, A10
`decide`; A9 codec. Every criterion has a test intent: A1 T1–T2; A2 T3–T5; A3 T4, T6–T7;
A4 T8–T12; A5 T7, T13–T14; A6 T15–T16; A7 T17; A8 T18; A9 T2, T19; A10 T20. Every
Public API entry names a criterion; nothing is there without one.

### Risks

- **Interpretations the concept left implicit, decided here.** The build follows them and
  does not halt over them: (a) the deadline is seen from `plugged_in_at` when given (doc
  §9), else from now — without it a ready time could never "pass" and priority 3 would be
  dead; (b) the phase-guard draw is the smaller of setting and measured draw, which is
  within A3's bound and more conservative than doc §13; (c) a charger without a car gets no
  action; (d) a tier is occupied only by chargers that can run; (e) planning ignores the
  phase guard and fallback (now-quantities) and does not reserve capacity for charge-now
  sessions; (f) the block rule may plan up to `L − 1` quarters beyond the need when no
  extension fits; (g) contiguity yields to the price cap and to known-before-unpriced
  (guide 6.6); (h) a car whose need is covered but whose SOC is below 100 % (or unknown)
  stays at priority 3 and charges on spare capacity (doc §6), with `TARGET_REACHED` only
  for SOC ≥ 100; (i) doc scenario 12's "single-phase charger is cut" is read under the
  agreed max-min-fair rule as "the shared phase is held to the budget": 16 A three-phase +
  10 A single-phase on one shared phase with budget 25 gives 15 A and 10 A. Anything **else** the
  build finds undecided by section 1 or this plan is a halt.
- **Datetime pitfalls.** Same-`ZoneInfo` comparisons ignore `fold`; compare in UTC
  everywhere (guide 5). If a DST test fails in a way that suggests the deadline semantics
  rather than arithmetic are wrong, halt.
- **A10 too slow.** Block scoring is O(quarters × blocks) per session. If T20 fails, the
  build may precompute per-pass block lists, skip blocks with no new quarter cheaply, and
  short-circuit the unreachable case; it must not drop or weaken T20. A second failure for
  the same reason is a halt.
- **Property test finds an invariant break.** Fix the code, never the property. If the
  break shows two acceptance criteria contradict each other, halt.
- **Hypothesis flakiness.** Use `@settings(deadline=None, max_examples=200,
  derandomize=True)` so the suite is deterministic and not timing-sensitive.
- **The stop gate's file check** needs every `ev/` path written in full in STRUCTURE.md.
### Critique

The `plan-critic` read section 1, then this section, the source doc and the repo; verdict
"accept with changes". Each finding and what was done:

1. *"Plan completed" priority 3 missing (A2, §1 example reason).* **Applied:** need 0 with
   SOC below 100 or unknown → priority 3 `PLAN_COMPLETED`; `TARGET_REACHED` only for
   SOC ≥ 100; charge-now keeps priority 1 with the need-covered event. This is the literal
   reading of section 1, so no question to the user; flagged in the report.
2. *Block/pass rules break contiguity and leave choices open.* **Applied** the straddling
   passes, the finishing-rule definition and the unreachable-case flags. **Rebutted** the
   suggested precedence: the critic proposed contiguity beats the cap; doc §7.6 calls
   contiguity a preference and §7.8 calls the cap a rule, so the cap wins and an isolated
   quarter under the cap is taken first. Stated in guide 6.6 and Risk (g), and flagged in
   the report.
3. *Gap semantics contradict the guide.* **Applied:** the `astimezone` behaviour (shift
   forward by the gap) with exact UTC instants in T11.
4. *`phase_capacity` crashes on a `None` phase that is still "fresh".* **Applied:** that
   phase gets the budget; T13 case.
5. *`present_draw`/`reserved_current` miss A3 or are unsafe.* **Applied** all three: fixed
   ignores `paused`; unavailable uses setting or assumed current and ignores `paused`; no
   car → 0 in the phase-guard draw. T7/T14 cases.
6. *Signature tables in `.claude/rules/` contradict §1 and the structure-auditor.*
   **Applied:** tables stay in STRUCTURE.md; the rules-file row is dropped.
7. *`PlanRequest` min/max unspecified.* **Applied** (guide 8.6).
8. *Scenario 12 under max-min fairness unstated.* **Applied:** 15 A / 10 A, in T6 and Risk (i).
9. *Expected finish off by 1000.* **Applied.**
10. *Session on a fixed charger rejected (widens A1).* **Applied:** accepted, ignored for
    control.
11. *T1 and T8 promise the unbuildable.* **Applied** both rewordings.
12. *Smaller items.* **Applied:** 8.2/8.6 order, phase iteration order, `abs()` freshness
    for measurements and prices (replacing the old Risk g), `time.isoformat()` round trip,
    ISO `deadline` in event params, ints accepted for float fields, A1's "limits" named.
    **Rebutted** the dependency-pin concern: `pip index versions` in this environment lists
    hypothesis 6.170.5 and tzdata 2026.5, so both pins resolve.

Coverage re-checked after these changes: every criterion still has a Public API entry and a
test intent (the new `PLAN_COMPLETED` code is A2/A6), and nothing was added without one.

---

## 3. Implementation notes

> Written in step 3. Only deviations from the plan above, each with its reason. "Built as
> planned" is a complete and good entry. On a fix round, also the reproduction test's red
> run, pasted here before the fix was written — step 6 cites it.

Built as planned. The Public API table needed no correction; the points below are
additions or choices inside it, none of which touches an acceptance criterion:

- `ConfigError` also carries a `message` attribute (the text without the field), which the
  codec uses to re-raise with a dotted path prefix.
- `ev/result.py` declares `type Params = dict[str, str | float | int | bool]` and uses it
  for `Reason.params` and `Event.params`; the type is the one in the table.
- Whole-number fields in `model.py` (`min_current`, `max_current`, `fallback_current`,
  `user_current_limit`, `min_block_quarters`) accept an integral float and store an int,
  matching the codec rule; a non-integral float or a bool is rejected.
- A session whose SOC is at least 100 has its remaining need reported as 0 (reason
  `TARGET_REACHED`), so `expected_finish` is `now`.
- `PRICE_CAP_EXCEEDED` events carry the cap in `params["price_cap"]`.
- STRUCTURE.md gained sections for every `ev/` module (full paths, signature tables) and
  lost the hello_world entries. The sections for the new `tests/test_ev_*.py` files are
  left to step 5, because the stop gate blocks on a path named in STRUCTURE.md that does
  not exist on disk yet.

### Changed in step 5

Bugs the step-5 tests found, fixed in the code. None changes a public signature or an
acceptance criterion; the rows below are the behaviour that differs from section 2 or from
what step 3 built.

- `present_draw` for a fixed or unavailable charger (guide 7): it returned
  `reserved_current`, `phase_capacity` added it back as "draw" and `decide` subtracted the
  reservation again, so the reservation cancelled out of the phase-guard term and a fixed
  charger that was not drawing its level yet could start and push a phase past the
  threshold. It now returns the charger's *measured* per-phase draw when power is known
  (the part already inside the phase reading), else `reserved_current`; `decide` holds the
  full reservation back once. A3's bound is met more tightly, never more loosely.
- `next_occurrence`: ignores `ready_time.fold` (it moved a gap time an hour backward and
  chose the second occurrence of a fold time), and returns the second occurrence of a
  fold-hour ready time when `after` lies between the two (it skipped a whole day). The
  second is decided by the row's own definition, "first moment strictly after `after`".
- `quarter_starts` and `prices_fresh` raise `ValueError` for a naive moment instead of
  reading it in the host's timezone.
- `plan_sessions`: a request with `max_current == 0` (a `user_current_limit` of 0)
  divided by zero while ordering by urgency; it now sorts as least slack and plans nothing
  (guide 8.6).
- `decide`: a remaining need within 1e-9 kWh is 0 everywhere (it was 0 for the planner but
  not for the priority rule, so float residue demoted a finished car to priority 4);
  `capped_by` is never reported for a fixed or unavailable charger.
- `model`: `PriceInterval` orders `start`/`end` in UTC (it compared wall clocks for a shared
  `ZoneInfo`); `PriceSeries` rejects a non-sequence `intervals` with `ConfigError`;
  `Charger` rejects a duplicate phase given as a list (A1); a huge integer is a
  `ConfigError`, not an `OverflowError`.
- `codec`: a huge integer or duration is a `ConfigError` naming its path;
  `snapshot_from_dict` documents that a field that only has a default may be omitted but
  not null.

### Changed in step 3, re-run after step 6 (A2, first send-back)

Section 6 found "waiting for capacity" missing when the budget left after reservations is
below a charger's minimum and the sessions are ordinary smart ones: they fell to priority 4
with an empty plan, reason `planned_later`, status `normal`. Fixed in `ev/decide.py` only;
no public signature, no `ReasonCode` member and no tier changed (the session keeps priority 4).

- A smart session that can run, was planned, and got an empty plan while the planning
  capacity (budget minus reservations and smart-charging-off allocations, `plan_cap`) is below
  its minimum on one of its phases is marked *starved* (private `_Item.starved`). Its final
  reason is `WAITING_FOR_CAPACITY` ("Priority 4 — waiting for capacity") instead of
  `PLANNED_LATER`.
- `_status` returns `WAITING_FOR_CAPACITY` when every charger in the active tier **and** every
  starved charger got 0 (and at least one exists), where it used to look at the active tier
  alone. A priority-4 charger with a non-empty later plan, held back only by the current
  (phase-guard) capacity, is not starved and keeps `normal`/`planned_later`.
- Tests added to `tests/test_ev_decide.py` (the old suite needed no change; it never encoded
  the wrong behaviour): `test_doc_scenario_4_with_ordinary_smart_sessions_waits_for_capacity`
  (the section 6 input; red on the previous code, green now) and two pins for the boundary,
  `test_a_priority_four_charger_with_a_later_plan_is_not_waiting_for_capacity` and
  `test_a_planned_charger_held_back_only_by_the_phase_guard_stays_normal`.
- Not touched: `STRUCTURE.md` (no public change).

### Changed in step 5, re-run (after the step-3 re-run for A2)

Two test-designer reports on the starved rule found two ways the re-run left a reason or a
status untruthful. Both are fixed in `ev/decide.py` only; no public signature, no
`ReasonCode` member, no priority and no tier changed.

- *Starved* is now "can run, was planned, got an empty plan" (was: and the planning capacity
  is below its minimum on one of its phases). An empty plan can only come from capacity — the
  horizon is never empty for a session with a future deadline and unpriced quarters are
  usable — so the narrower test only missed capacity consumed by another session's plan, whose
  charger still read "charging is planned for later quarters" (A6: the reason must be true).
- `_status` counts a starved charger as *short* beside served ones, so a starved charger next
  to an active charger that is fully served reads `limited_by_budget` / `limited_by_phase`,
  not `normal` (section 6's complaint that the status said "normal" while nothing could ever
  be planned). Every case where all active and starved chargers get 0 is unchanged.
- Guide 8.7 and 8.11 now describe this.

---

## 4. Verification log

> Written in step 4: the static half. Command output, not a summary of it.

| Check | Result |
|---|---|
| `ruff check .` | `All checks passed!` (before and after the step's edits) |
| `ruff format --check .` | `45 files already formatted` |
| `mypy` | `Success: no issues found in 17 source files` |
| `pytest` | `307 passed` (existing hook suites; the `tests/test_ev_*.py` files are step 5) |
| Plan completeness | every signature in the Public API table exists as written, checked with `inspect` over all seven `ev/` modules (parameter names, defaults, annotations, return types, enum members and values, `ev.__all__`). Missing: none. Deviation: none. Unplanned: `type Params` in `result.py` (justified in section 3) and `ConfigError.message` (recorded in section 3). `codec.Converter` was an unplanned public alias; made private as `_Converter`. |
| `STRUCTURE.md` | in sync after the auditor's four findings were applied (below); stop gate's `structure_problems`, `stray_test_files`, `missing_init_files` all empty |
| `python -m energy_optimization.ev.<module>` | `model`, `result`, `quarters`, `planning`, `allocation`, `decide`, `codec` all exit 0 with readable output, plus the expected `RuntimeWarning` from the package re-export |

Structure-auditor findings and what was done:

1. `Reason`/`Event` rows showed `params: Params = {}`; code uses `field(default_factory=dict)`.
   **Applied:** both rows now say `field(default_factory=dict)`.
2. `Params` had no row and the `ev/__init__.py` section claimed it re-exports everything in
   `result`. **Applied:** a `type Params = ...` row in `result.py`'s table; the `__init__`
   section now says it re-exports every public type in `model` and `result` except `Params`.
3. `codec.py` public `type Converter[T]` used only by private helpers. **Applied:** renamed to
   `_Converter` (preferred option), so it stays out of STRUCTURE.md.
4. `## Growth` stale for `ev/`. **Applied:** one sentence saying `ev/` is the exception for now.

Showcase form: each `main()` runs and prints something a reader can learn from. The function
under showcase is called on its own line with named inputs in every module; the nested
constructors that build the larger inputs (`Snapshot` in `decide` and `codec`, `Action` in
`result`) use inline literals, left as is because they are the input data, not the call.

### Re-run after step 6 (A2 send-back, step-3 re-run commit `9e8b11c`)

The earlier record above stands; this is the verification of the tree as the step-3 re-run
left it.

| Check | Result |
|---|---|
| `ruff check .` | `All checks passed!` |
| `ruff format --check .` | `52 files already formatted` |
| `mypy` | `Success: no issues found in 24 source files` |
| `pytest` | `1113 passed` (1110 before the re-run plus its three new tests in `tests/test_ev_decide.py`) |
| Plan completeness | The re-run changed `ev/decide.py` privately (`_Item.starved`, `_status`, the reason choice); `decide` and `main` are the only public names in the module and match the table (`decide(snapshot: Snapshot) -> Decision`, `main() -> None`), checked with `inspect`. No other `src/` file changed. Missing: none. Deviation: none. Unplanned: none. |
| `STRUCTURE.md` | in sync, no edit needed; stop gate's `structure_problems`, `stray_test_files`, `missing_init_files` all empty |
| `python -m energy_optimization.ev.decide` | exit 0, same readable showcase output (status, allocations, reasons, the action, then the stale-measurement run), plus the expected `RuntimeWarning` |

Structure-auditor finding and what was done: STRUCTURE.md needs no edit (public names and
rows unchanged; its entry never described when `WAITING_FOR_CAPACITY` applies, so the new
rule contradicts nothing). One finding outside STRUCTURE.md: guide 8.11 in section 2 still
gave the old status rule, leaving out starved chargers. **Applied:** section 2 describes the
code, so 8.11 now states the shipped rule and notes that it changed in the re-run. No code
change was needed in this step.

---

## 5. Test log

> Written in step 5: the dynamic half.

| Intent | Test names | Result |
|---|---|---|
| T1 | `test_ev_model.py`: `test_adjustable_charger_rejects_invalid_configuration`, `test_single_level_charger_rejects_invalid_configuration`, `test_charger_state_rejects_invalid_values`, `test_session_rejects_invalid_values`, `test_price_interval_rejects_misaligned_reversed_empty_and_naive_bounds`, `test_price_series_rejects_overlapping_intervals`, `test_settings_require_budget_threshold_fallback_and_timezone`, `test_settings_reject_invalid_values`, `test_snapshot_rejects_bad_links_and_types`, `test_charger_rejects_a_duplicate_phase_given_as_a_list`, the fold and huge-integer tests | pass (206 cases) |
| T2 | `test_ev_codec.py`: `test_snapshot_from_dict_rejects_a_missing_required_field_with_its_path`, `..._a_wrongly_typed_field_with_its_path`, `..._an_unknown_key_with_its_path`, `..._prefixes_a_model_error_with_the_path`, `..._rejects_duplicate_and_unknown_phases`, `test_decision_from_dict_rejects_bad_fields_with_their_path`, `test_both_decoders_reject_a_non_object_with_the_field_data` | pass |
| T3 | `test_ev_decide.py`: `test_doc_scenarios_2_to_5_split_equally_and_cut_the_latest_ready_time`, `test_equal_ready_times_rank_the_larger_remaining_need_first`, `test_only_the_highest_occupied_tier_gets_current` | pass |
| T4 | `test_ev_allocation.py`: the `test_share_*` group (42 cases: zero-or-min order, later claim on another phase, floor and 1 A remainder, max, all-or-nothing, empty / zero / negative capacity, purity) | pass |
| T5 | `test_ev_decide.py`: the priority group — `test_charge_now_*`, `test_the_current_quarter_in_the_plan_is_priority_two`, `test_a_passed_ready_time_*`, `test_a_covered_need_below_full_charge_is_plan_completed`, `test_a_full_battery_is_target_reached_*`, `test_a_plan_for_later_quarters_is_priority_four_*`, `test_a_tier_is_occupied_only_by_chargers_that_can_run`, `test_a_session_on_a_fixed_charger_is_accepted_and_changes_nothing` | pass |
| T6 | `test_the_phase_guard_binds_before_the_budget` (doc scenario 7), `test_doc_scenario_12_a_shared_phase_is_held_to_the_budget`, `test_doc_scenario_20_an_on_off_charger_is_all_or_nothing` | pass |
| T7 | `test_ev_decide.py`: `test_a_fixed_charger_is_reserved_*`, `test_an_unavailable_*`, `test_smart_charging_off_*`; `test_ev_allocation.py`: `test_present_draw_*`, `test_reserved_current_*` | pass |
| T8 | `test_ev_planning.py`: `test_plan_scenario_14_twenty_kwh_at_eleven_kilowatts_in_eight_hours`, `test_plan_matches_the_hand_checked_cheapest_selection_with_runs_of_two` (brute-force optimum), `test_plan_without_a_cap_never_leaves_a_run_shorter_than_the_block_length`, `test_plan_an_odd_need_extends_a_block_*` | pass |
| T9 | `test_plan_unreachable_plans_every_usable_quarter`, `test_a_target_that_cannot_be_reached_raises_an_event_with_the_reachable_kwh` | pass |
| T10 | `test_plan_uses_unpriced_quarters_last_and_latest_first`, `test_plan_supplying_the_missing_prices_changes_the_plan`, `test_plan_a_priced_single_quarter_is_taken_before_an_unpriced_block`, `test_unpriced_quarters_in_the_plan_are_flagged_with_no_price` | pass |
| T11 | `test_ev_quarters.py`: `test_next_occurrence_returns_the_first_matching_moment_after`, `test_dst_days_give_92_and_100_quarters_between_two_seven_oclocks`, the fold tests; `test_ev_decide.py`: `test_the_dst_nights_reach_planning_with_the_right_number_of_quarters`, `test_the_current_quarter_is_found_in_utc_inside_the_fall_back_hour` | pass |
| T12 | `test_ev_planning.py`: the cap group (`..._price_equal_to_the_cap_*`, `..._cap_is_not_broken_*`, `..._cap_is_broken_only_for_the_missing_energy`, `..._isolated_quarters_under_the_cap_beat_a_block_over_it`), `test_plan_hourly_intervals_split_*`, the shared-capacity group, `test_plan_unreachable_with_unpriced_quarters_is_flagged` | pass |
| T13 | `test_ev_decide.py`: `test_without_phase_data_every_controllable_charger_is_capped_at_its_fallback` (10 rows), `test_measurement_age_boundary_*`, the missing-phase group, `test_in_fallback_*`, the stale-price group; `test_ev_allocation.py`: `test_measurements_fresh_*` | pass |
| T14 | `test_a_paused_charger_with_measured_power_adds_to_the_other_load`, `test_measured_draw_above_the_setting_does_not_raise_the_free_current`, `test_a_charger_without_a_car_counts_as_drawing_nothing`, `test_missing_charger_power_is_taken_as_drawing_the_setting`, `test_a_covered_need_below_full_charge_is_plan_completed` | pass |
| T15 | `test_every_decrease_and_pause_comes_before_every_increase_and_resume`, `test_the_action_is_chosen_by_comparing_the_target_with_the_present_state`, `test_pause_targets_zero_and_every_target_is_absolute`, `test_executing_the_actions_makes_the_next_decision_a_no_op` | pass |
| T16 | `test_reasons_carry_the_priority_allocation_and_need_source`, `test_capped_by_is_only_ever_one_of_the_named_limits`, `test_expected_finish_*`, `test_result_datetimes_are_in_the_settings_timezone`, `test_the_available_amps_are_reported_per_phase_after_reservations` | pass |
| T17 | `test_the_invariants_hold_for_generated_snapshots` (300 generated snapshots, `derandomize=True`; checks computed independently in `assert_invariants`); mutation-checked: dropping the phase guard, the reservation subtraction, the down-before-up order or the fallback cap each turns it red | pass |
| T18 | `test_the_same_snapshot_gives_equal_decisions`, `test_decide_leaves_the_snapshot_equal_to_a_deep_copy_taken_before`, `test_the_output_does_not_depend_on_the_hash_seed` (three `PYTHONHASHSEED`s in subprocesses), `test_the_package_imports_only_the_standard_library_and_itself` | pass |
| T19 | `test_ev_codec.py`: `test_snapshot_variants_round_trip_through_json`, `test_hand_built_decision_round_trips_through_json`, `test_a_real_decision_round_trips_through_json`, `test_decision_round_trip_keeps_param_types`, `test_generated_snapshots_survive_the_dict_round_trip_and_decide_the_same` | pass |
| T20 | `test_ten_chargers_and_a_48_hour_series_decide_in_under_100_ms` (cap below every price, two unreachable sessions, best of five) | pass |

Final run: `ruff check .`, `ruff format --check .`, `mypy` clean; `pytest` 1110 passed (803 in
the seven new `tests/test_ev_*.py` files, 307 existing). The new suite run against the step-4
source (commit `1fec06b`) fails 41 tests — the red run for the bugs in section 3's "Changed in
step 5" — and passes all of them on the fixed code.

### Reader reports: each finding judged

Four `test-designer` reports (input-space and contract, on model/quarters/codec and on
planning/allocation/decide) were merged; duplicates dropped, every contradiction decided.

**Applied (bug, settled by section 1 or section 2 — fixed, regression test added)**

| Finding | Settled by | Fix | Tests |
|---|---|---|---|
| `user_current_limit=0` raised `ZeroDivisionError` in `plan_sessions` (both reports) | guide 8.6: such a session "plans nothing, so it is unreachable" | a request with `max_current == 0` sorts as least slack and plans nothing | `test_a_user_limit_of_zero_*`, `test_plan_with_zero_max_current_*` |
| `PriceInterval` compared wall clocks for a shared `ZoneInfo` | guide 5, "never compare … convert to UTC first" | compare `.astimezone(UTC)` | `test_price_interval_*fold*` |
| `next_occurrence` inherited `ready_time.fold` | the row: gap shifts forward, fold is the first occurrence | candidate built from the fold-0 wall time | `test_next_occurrence_ignores_the_fold_attribute_*` |
| `next_occurrence` with `after` between the two fold occurrences skipped a day | the row's definition, "first moment strictly after `after` whose local time is `ready_time`", and A4's "next occurrence" | the second occurrence is returned once the first is at or before `after` | `test_next_occurrence_in_the_fold_goes_to_the_second_occurrence_*` |
| `OverflowError` instead of `ConfigError` for huge ints and durations | A9: wrongly typed or invalid fields are `ConfigError` naming the field | caught in `model._number`, `codec._num`, `codec._seconds` | `test_snapshot_from_dict_turns_a_huge_number_into_a_config_error`, model overflow cases |
| `PriceSeries(intervals=None)` raised `TypeError` | A1/T1: `ConfigError` naming the field | iterable check | `test_price_series_rejects_a_non_sequence_of_intervals` |
| Duplicate phases accepted on direct construction | A1 ("unknown or duplicate phases"), T1 ("for direct construction") | `Charger` rejects a duplicate in a list | `test_charger_rejects_a_duplicate_phase_*` |
| Float residue of the need not treated as 0 | A2: need 0 → priority 3 plan completed | `_classify` snaps `need <= 1e-9` to 0 | `test_a_float_residue_*` |
| `capped_by` reported for an unavailable charger | guide 8.12: only for an allocated charger below its own max | `_capped_by` is `None` for reserved chargers | `test_a_reserved_charger_is_never_capped_by_a_limit` |
| A fixed charger's reservation cancelled out of the phase-guard term | section 1: "reserved first, on their phases"; "safety always wins" | see section 3, "Changed in step 5" | `test_phase_capacity_does_not_credit_a_fixed_charger_*`, `test_a_fixed_charger_that_is_not_drawing_its_level_*`, `test_a_fixed_charger_drawing_its_level_is_held_back_once_not_twice` |
| `quarter_starts` / `prices_fresh` read a naive moment in the host timezone | A8: deterministic output | `ValueError` for a naive input, like the other two functions | `test_quarter_starts_rejects_a_naive_moment_*`, `test_prices_fresh_rejects_a_naive_now` |
| `next_occurrence`, `plan_sessions` lacked `Raises` entries (unknown zone, naive input) | conventions | docstrings completed; behaviour pinned | `test_next_occurrence_raises_for_an_unknown_zone` |

**Rebutted or pinned as built (a decision, with the reason)**

| Finding | Decision | Why |
|---|---|---|
| A partly used quarter (the one holding `now`, or the last one before an off-grid ready time) is credited as a whole quarter of energy; `reaches_target` can then read False for a car that finishes just in time | pinned as built, untested as a requirement | The plan chose whole-quarter granularity on purpose and says so three times: guide 6.1 (horizon = `quarter_starts`), 6.4 (energy per quarter), 8.9 ("the end of the first quarter"), and A4's own formula counts whole quarters. Every call re-plans, so the first quarter corrects itself; ready times are on the 15-minute grid in practice. The error is bounded by one quarter and, for `reaches_target`, in the conservative direction. Noted for step 8 |
| `null` for a field that only has a default (`available`, `delivered_kwh`, `charge_now`, `smart_charging`, `voltage`, …; `plan: null`) is rejected | pinned (tests added) and the docstring fixed | A1: "an error naming the offending field, never a best guess". Reading a null `available` as available, or a null `voltage` as 230, is a guess. Omitting the key takes the default; `| None` fields take null or omitted alike |
| An on/off charger's `present_draw` is the smaller of assumed and measured power (section 1 says "accounted at the assumed value") | pinned | The allocation and budget are accounted at the assumed value; only the phase-guard "other load" uses the reading, and a smaller draw makes the other load larger and the capacity smaller. The direction is conservative (plan critic finding 5) |
| `_plan_requests` applies the user limit to an on/off charger (guide 8.6 says `assumed_current` for both) | pinned | A limit below the assumed current means the charger cannot run, which is the guide's own "a limit below the minimum plans nothing" sentence applied to min = assumed; the other reading plans a charger that can never run |
| `PRICE_CAP_EXCEEDED` also fires for an unreachable plan | pinned | Guide 6.6: the flag is set "in the unreachable case of step 5 too" |
| `share` skips a higher-ranked claim whose minimum does not fit and admits a lower-ranked one | pinned (`test_share_an_unfit_higher_ranked_claim_does_not_block_a_lower_one`) | Guide 7 pass 1, "include it … if it fits"; the alternative leaves capacity unused |
| A carless charger with a session reports no need, source or deadline | pinned (`test_a_charger_without_a_car_gets_nothing_and_no_action`) | Section 1: a session exists "when a car is connected"; with no car the session is stale data and is ignored |
| `capped_by="max"` in guide 8.12 is never produced | pinned (`test_capped_by_is_only_ever_one_of_the_named_limits`) | An allocated charger at its max is not "below its own max"; the value was vacuous |
| The "(assumed need N kWh)" note appears only for smart sessions, not for smart-charging-off or reserved ones | left as built, untested | The default is *used* only where a plan or priority depends on it; elsewhere the need is only reported |
| `LIMITED_BY_PHASE` is chosen when any phase of a short charger has a guard below the budget, even if another phase is the binding one | left as built, untested | Low-risk attribution heuristic; the status is informational and section 1 only lists the status names |
| `ZoneInfo` datetimes inside the fall-back hour are not `==` to the same instant in a fixed offset (PEP 495), so a decoded value is not `==` to the original | pinned (`test_round_trip_keeps_the_instants_of_datetimes_inside_the_fall_back_hour`) | The instant and offset survive, which is all A9 can mean for a string that carries no zone name; the test compares instants and the encoded form |

**Considered and deliberately skipped**

- `plugged_in_at` later than `now` (clock skew) shifts the deadline by a day when the ready
  time falls in between. Section 1 does not decide it and it is not a plausible input; left
  unchanged and untested so no assertion buries a guess. For step 8's `DEVELOPMENT.md`.
- `ConfigError` is not picklable (two-argument `__init__`); nothing in the contract or its
  callers needs it.
- A lower-case timezone name (`europe/copenhagen`) loads or not depending on the platform's
  file system; not reproducible here.
- NFC / NFD variants of a charger id: different strings by design.
- Very long strings, idempotency of the validating constructors, and purity of the
  converters beyond the decoders: no length limit is promised and the dataclasses are frozen.
- Duplicate ids inside `share` / `plan_sessions`: `decide` cannot produce them.
- A mutation of tier exclusivity was not caught by the property because the mutant did not
  change behaviour; the tier rule is pinned by the explicit tier tests instead.

### Re-run after step 6 (A2 send-back; step-3 re-run commit `9e8b11c`)

The record above stands. This is step 5 on the re-run: the changed rule is `decide`'s
starved rule and the status it feeds. Two `test-designer` reports (input-space, contract;
13 cases each, six contradictions between them) were merged: duplicates dropped (the
boundary, smart-off, running-charger and stale-data cases appeared in both), every
contradiction decided below. 28 test cases (16 functions) were added to `tests/test_ev_decide.py` (1113 →
1141 passing); the existing tests needed no change.

| Intent | Test names (all `tests/test_ev_decide.py`) | Result |
|---|---|---|
| T3 / A2: "waiting for capacity" when less than min is left, in its boundaries | `test_planning_capacity_is_starved_only_below_the_adjustable_minimum` (6.0 A, 6.0 A + 5e-10, 5.5 A, 3 A), `test_planning_capacity_is_starved_below_the_assumed_current_of_an_on_off_charger`, `test_one_phase_below_the_minimum_starves_a_multi_phase_charger`, `test_an_on_off_charger_starves_while_an_adjustable_on_its_phase_plans` | pass |
| T3 / A2: all active and starved chargers at 0 | `test_an_active_tier_at_zero_and_a_starved_charger_wait_for_capacity`, `test_a_starved_charger_beside_a_charger_planned_for_later_waits_for_capacity`, `test_smart_charging_off_allocations_can_starve_a_smart_session` (limit 10 / 11 / none) | pass |
| T3 / A2 mixed case | `test_a_starved_charger_beside_a_served_one_limits_the_status` (served is smart, served is charge-now) | pass (red on the previous code) |
| A6 reason truthfulness | `test_an_empty_plan_left_by_another_sessions_plan_is_waiting_for_capacity` | pass (red on the previous code) |
| T5 / T13 precedence | `test_a_user_limit_below_the_minimum_is_not_reported_as_starved` (4 A and 6 A), `test_stale_phase_data_outranks_a_starved_charger_in_the_status` (global and own fallback), `test_stale_prices_never_mark_a_session_starved` | pass |
| T15 / T16 / T18 | `test_a_running_starved_charger_is_paused` (pause, down stage), `test_a_starved_session_reports_what_it_will_reach_and_is_not_on_target` (event params with delivered energy, no price-cap event), `test_the_starved_reason_has_a_stable_text_and_params` (energy / default / SOC need source, text and params), `test_a_starved_snapshot_is_pure_and_repeatable` | pass |

The new tests run against the previous `decide.py` (commit `b2e115e`): the three marked red
fail — the two mixed-case parameters and the other-session empty plan — and the other 25
pass, so they pin behaviour the re-run already had. Against the fixed code all 1141 pass.

**Decisions on the four open points**

| Point | Decision | Why it stays within section 1 |
|---|---|---|
| (a) An empty plan left by another session's plan read "charging is planned for later quarters" | **Fixed.** Starved = can run, planned, empty plan (see section 3, "Changed in step 5, re-run"). Reason `WAITING_FOR_CAPACITY` ("Priority 4 — waiting for capacity"), priority 4 and tiers untouched | A6 promises an explainable reason; a sentence that says charging is planned when nothing is planned is false. Reading the reason off the plan (empty, so capacity) needs no new priority and no new rule |
| (b) Status `waiting_for_capacity` while a smart-charging-off charger draws current | **Pinned as built** (`test_smart_charging_off_allocations_can_starve_a_smart_session`). Not changed | A2 is about smart chargers ("every connected smart charger ... everyone gets 0"); section 1's order of service serves smart-off chargers first and gives the smart ones what is left, so with less than a minimum left every smart charger gets 0 and the status is "waiting for capacity". The existing smart-off test already encoded the same reading |
| (c) Status `normal` for a starved charger beside a served one | **Fixed.** A starved charger counts as short in `_status`: the mixed case reads `limited_by_budget` or `limited_by_phase` | Section 1 names the statuses and A2 decides only the all-zero case; for the mixed case "normal" repeats the complaint of section 6 (status "normal" while nothing can ever be planned) and "limited by budget" is what the budget causes. No priority or allocation changes |
| (d) Guide 8.7 said priority 4 keeps `PLANNED_LATER` | **Fixed in the plan**: 8.7 and 8.11 now describe the code | Section 2 describes the code, as at step 4 |

Other contradictions the reports raised, none of which needs a decision from section 1:
a user limit below the minimum with too little capacity left gives `BELOW_MINIMUM` and
status `normal` — it predates the re-run, the cause is the limit, not capacity, and A2's
"less than min available" does not cover it (pinned by the 4 A parameter); the order
`PRICES_STALE` before `WAITING_FOR_CAPACITY` cannot meet a starved charger, since stale
prices send every session to priority 3 before planning (pinned by the stale-prices test).

**Considered and deliberately skipped (re-run)**

- A starved charger when `plan_cap` is an exact multiple-of-EPS boundary beyond 5e-10:
  float noise is a property of the planner's `floor(room + EPS)`; the 5e-10 row pins the
  edge the starved test shares with it.
- A charger whose plan is empty for a reason other than capacity: none exists (a future
  deadline gives at least one quarter, unpriced quarters are usable, and a user limit below
  the minimum means it cannot run, not starved).
- `capped_by` on a starved charger: it has allocation 0, so none is reported by design.

Final run: `ruff check .`, `ruff format --check .`, `mypy` clean; `pytest` 1141 passed.

## 6. Concept check

> Written in step 6, against section 1 — not against section 2. The question is whether
> the thing built is the thing agreed, not whether it matches the plan.

| # | Criterion | Met | Evidence |
|---|---|---|---|
| A1 | Rejects invalid configuration | yes | Driven by hand (19 inputs): min > max → `max_current`; min 0 / −1 → `min_current`; empty and unknown phase → `phases`; `None` budget / threshold / fallback → `phase_budget` / `phase_threshold` / `fallback_current` ("required"); budget 0 and −3 → `phase_budget`; unknown and `None` timezone → `timezone`; duplicate charger id → `chargers`; session for unknown charger → `sessions`; naive `now`, `plugged_in_at`, price bound and `measured_at` → the field. Each is a `ConfigError` whose `field` and message name the field and the value. Suite: `tests/test_ev_model.py` (206 cases for T1). |
| A2 | Priority 1–4, tier exclusivity, 0 or ≥ min, ranking, split, "waiting for capacity" | **partially** | Met: tier exclusivity and ranking (probe: charge-now 32 A, priority 2 and 3 get 0 with `waiting_for_higher_priority`); 15/15, 7/7/0, 6/0/0 and 16/16/16 splits (`test_doc_scenarios_2_to_5_*`, 5 rows); `share` floors and hands out the remainder 1 A at a time in ranking order (`tests/test_ev_allocation.py`, 42 `test_share_*` cases). **Unmet clause: "with less than min available everyone gets 0 and the status is 'waiting for capacity'".** The test builds scenario 4 only with charge-now sessions (`now_session`, priority 1). With ordinary smart sessions whose ready time is in the future, when the budget left after reservations is below the minimum — e.g. budget 32 A, a fixed charger reserved at 27 A on all phases, two adjustable 6–16 A chargers with `energy_need_kwh=10`, ready 07:00 / 09:00, flat 48 h prices — `decide` returns status `normal`, `available` 5.0 on every phase, both chargers allocation 0, priority 4 and reason `planned_later` ("charging is planned for later quarters"), plus two `target_unreachable` events with 0.0 kWh. The planning capacity (`plan_cap`, `decide.py:498-518`) is below the minimum in every quarter, so no quarter can be planned, the sessions fall to priority 4 and never reach the tier that `_status` (`decide.py:443`) checks. The same snapshot with `charge_now=True`, or with ready time passed, gives `waiting_for_capacity`. A user opening the status sees "normal" and the reason says charging is planned, while nothing can ever be planned. |
| A3 | Respects amp limits | yes | Probes: doc scenario 7 (29 A other load on L1, threshold 35, budget 32 → 6 A, `limited_by_phase`, `available.l1` = 6); scenario 12 (16 A three-phase + 10 A single-phase on one shared phase, budget 25 → 15 A / 10 A); `share` of a 3-phase and two single-phase claims on 25 A per phase → 13 / 12 / 12 (max-min fair, no phase over 25); scenario 20 (on/off 10 A + adjustable, budget 18 → 10 A and 8 A, on/off never partial); fixed charger reserved at its level (27 A) with no action; unavailable adjustable set 12 A drawing 14 A reserved at 14 A (`available.l1` 18 = 32 − 14) with no action; smart-off at its user limit 12 A served first, the charge-now smart charger gets the 8 A that is left. `test_the_invariants_hold_for_generated_snapshots` and the T6 / T7 tests pass. |
| A4 | Plans the cheapest quarters before the ready time | yes | Driven by hand: 07:00 → 07:00 on 2026-03-28/29 gives 92 quarters and on 2026-10-24/25 gives 100 (deadlines 05:00 UTC and 06:00 UTC); 20 kWh at 16 A × 3 phases over 28 quarters with random prices plans 8 quarters (ceil(20 / 2.76) = 8) in runs `[2, 2, 2, 2]`, finish 07:00, `reaches_target` True; 170 kWh with prices only to midnight plans 62 quarters of which 6 are unpriced (`price` None), the latest quarters, `reaches_target` True; a cap of 0.5 with 12 cheap quarters and a need of 15 quarters plans 15 and raises `price_cap_exceeded`, while a need of 11 quarters stays under the cap with no event; the unreachable case raises `target_unreachable` with `reachable_kwh` and `target_kwh`; hourly intervals are split (`test_plan_hourly_intervals_split_*`). Shared capacity: `test_plan_*shared*` group. |
| A5 | Falls back under doubt | yes | Probes: measurements 5 min old, `None` object and a reading dated 5 min ahead all give status `phase_data_stale`, a `phase_data_stale` event, charger `a` 6 A (global fallback), charger `b` 10 A (its own `fallback_current`), on/off 10 A and adjustable min 8 A both 0 with `below_minimum`; budget and tiers still apply. Stale prices (3 h old): the non-charge-now session is priority 3, the charge-now one stays priority 1, status `prices_stale`, event `prices_stale`, no plan. Paused charger with 2300 W measured adds to the other load (L1 cap 6 A = 16 − 10); missing power is taken as drawing the setting (cap 16 A). `test_ev_decide.py` T13 / T14 groups pass. |
| A6 | Explainable, ordered output | yes | Probe: `x` 16 → 8 A emitted as `set_current` stage `down` before `y` 6 → 16 A `set_current` stage `up`; resume / pause carry absolute targets; no action for fixed, unavailable, carless or unchanged chargers. Every `ChargerDecision` carries allocation, priority, `Reason(code, text, params)`, plan (with `price` None for unpriced), `expected_finish`, `reaches_target`; `Decision.available` and `status` set; the four event kinds named in section 1 are raised in the probes above (`target_unreachable`, `price_cap_exceeded`, `phase_data_stale`, `prices_stale`; `charge_now_need_covered` in `test_charge_now_*`). The A6 weakness found under A2 (reason "planned for later quarters" on a charger with an empty plan) is part of that one finding. |
| A7 | §12 invariants for any input | yes | `test_the_invariants_hold_for_generated_snapshots`: 300 generated snapshots, `derandomize=True`, checks written independently in `assert_invariants`; section 5 records that dropping the phase guard, the reservation, the down-before-up order or the fallback cap each turns it red. Passes in the full run (1110 passed). |
| A8 | Deterministic and dependency-free | yes | `decide(s) == decide(s)` in the probe; `test_the_output_does_not_depend_on_the_hash_seed` (three `PYTHONHASHSEED`s), `test_the_package_imports_only_the_standard_library_and_itself` (AST scan); `grep` over `src/` finds no import outside the standard library and no `datetime.now` / clock call. |
| A9 | Round-trips plain dicts | yes | Probe: `snapshot_from_dict(json.loads(json.dumps(snapshot_to_dict(s)))) == s` and the same for a 10-charger `Decision`, both True, with `l2: null` kept as `None`; ISO 8601 with offset, `ready_time` `"07:00:00"`, durations as seconds; a missing `settings.phase_budget`, a string `phase_budget` and a string `chargers[1].max_current` are rejected naming `settings.phase_budget` / `chargers[1].max_current`. Optional fields (`measured_power_w`, `soc_percent`, `user_current_limit`, `plugged_in_at`, `energy_need_kwh`, `fallback_current`, `price_cap`, `phase_currents`) accept null; defaulted fields (`available`, `delivered_kwh`, `voltage`) reject it and accept omission. |
| A10 | Fast | yes | Probe: 10 chargers (1- and 3-phase), 200 quarters of prices (50 h), with and without a price cap: best of five `decide` calls 2.7 ms. `test_ten_chargers_and_a_48_hour_series_decide_in_under_100_ms` passes. |

Drift found, and what was done about it:

1. **A2 — "waiting for capacity" missing when the budget left after reservations is below the
   minimum** (code wrong, concept clear). Section 1 states the outcome outright, so no
   amendment is needed; the work goes **back to step 3**. Scope for the fix, so step 3 need not
   guess: when no quarter can be planned for a session that can run and still has need because
   the planning capacity (budget minus reservations and smart-charging-off allocations) is
   below its minimum on one of its phases, the overall status is `waiting_for_capacity` and the
   charger's reason says it is waiting for capacity rather than "planned for later quarters".
   A priority-4 charger whose plan is non-empty but later, with only the *current* capacity
   (the phase guard) below the minimum, keeps `normal`: it is not waiting for capacity, it is
   waiting for its cheaper quarters. If the fix turns out to need a different *priority* for
   such a session (the tiers are fixed by section 1), that is a halt, not a code decision. Add a
   test with ordinary smart sessions (not charge-now) for doc scenario 4.
2. **Judgement calls recorded in sections 3 and 5 — all within section 1, none amends it:**
   - `next_occurrence` between the two fold occurrences returns the second occurrence: section 1
     says the horizon runs "to the next occurrence of the ready time in the settings'
     timezone", and the second occurrence is the next moment the local clock reads that time.
   - Partial quarters credited as whole quarters (the one holding `now`; the last one before an
     off-grid ready time): A4 states the quantity as "ceil(remaining need / (V × A × phases /
     4))", a count of whole quarters, and every call re-plans. For `reaches_target` the error is
     conservative (finish is the end of the quarter); for the quarter holding `now` it is up to
     one quarter of energy optimistic, corrected on the next call. Not drift; already noted for
     step 8.
   - `null` on a field that only has a default is rejected: A1 "never a best guess" and A9
     "wrongly typed … rejected". Section 1's "missing values allowed" is listed field by field
     (SOC, measured power, phase currents), and every one of those accepts null.
   - `present_draw` for a fixed or unavailable charger is its measured draw when known, else its
     reservation: A3 reserves it at "the higher of … and measured draw"; the change only makes
     the phase-guard term tighter and removes a double count. Within A3.
   - Also within section 1: on/off `present_draw` is min(assumed, measured) (section 1: "charges
     least"); user limit applied to an on/off charger (cannot run below its assumed current);
     `PRICE_CAP_EXCEEDED` on an unreachable plan; `TARGET_REACHED` for SOC ≥ 100 (priority none,
     section 1 gives priority 1–4 "or none").
3. **Out of scope, surface, connections:** nothing from the out-of-scope list was built (no
   memory between calls, hysteresis, deadband, ramp-up, pause timers; `grep` finds none); no
   I/O or HA code; `hello_world.py` and its test are gone; nothing outside `src/`, `tests/`,
   `pyproject.toml`, `STRUCTURE.md`, `DEVELOPMENT.md` and `development/` changed. Public API
   matches what the concept asks for (snapshot / decision types, `decide`, four codec
   functions); the helper modules are importable but not re-exported. All seven showcases
   exit 0 and read as worked examples.
4. **Structure:** the `structure-auditor` report ("STRUCTURE.md matches the code: nothing needs
   changing"; all `ev/` modules, ten test files and five hooks checked) was applied; I did not
   re-derive it by hand. `ruff check .`, `ruff format --check .` and `mypy` clean; `pytest`
   1110 passed on the tree as audited.

### Re-run audit after the A2 send-back (commits `9e8b11c`, `b2e115e`, `4cb6db3`, `711bdda`)

The audit above stands as the record of the first pass. This is the second pass, on the tree
the re-run left. Since the first audit (`348b2b0`) the only source file that changed is
`ev/decide.py` (`git diff 348b2b0 HEAD --stat`: `decide.py`, `tests/test_ev_decide.py`, this
file); `model`, `result`, `quarters`, `planning`, `allocation` and `codec` are untouched.
`ruff check .`, `ruff format --check .`, `mypy` clean; `pytest` 1141 passed.

**The section-6 failing input, re-probed.** Budget 32 A, a fixed charger reserved at 27 A on
all phases, two adjustable 6-16 A chargers with `energy_need_kwh=10`, ready 07:00 / 09:00,
flat 48 h prices, no charge-now. Before: status `normal`, both priority 4 `planned_later`.
Now: status `waiting_for_capacity`, `available` 5.0 on every phase, both allocation 0,
priority 4, reason `waiting_for_capacity` ("Priority 4 — waiting for capacity"), no action,
two `target_unreachable` events with 0 kWh. The same with measurements zero and with the
fixed charger measured at 27 A give the same result; the `charge_now=True` variant still gives
`waiting_for_capacity`; one smart charger instead of two gives the same. The boundary holds:
with exactly 6 A left (budget 33) charger `a` gets 6 A (priority 2, `limited_by_budget`) and
`b` stays priority 4 `planned_later` with a ten-quarter plan, so it is not starved. Stale
phase data on the same input reports `phase_data_stale`, as A5 requires.

**Differential against `348b2b0`'s `decide`** (3000 generated snapshots: 1-5 chargers of all
three kinds on mixed phases, fixed/stale/missing measurements, stale prices, charge-now,
smart-off, user limits, price cap; scratch driver, not committed). 174 snapshots differ, and
every difference is a reason code `planned_later` -> `waiting_for_capacity` (131) or a status
change (`normal` -> `waiting_for_capacity` 30, `limited_by_*` -> `waiting_for_capacity` 8,
`normal` -> `limited_by_*` 5). **No allocation, priority, plan, action, `available` or event
differs in any snapshot.** The new `decide` was also equal to itself on a second call and
every new decision survived `decision_from_dict(json.loads(json.dumps(decision_to_dict(d))))`
unchanged, for all 3000.

| # | Criterion | Met | Evidence |
|---|---|---|---|
| A1 | Rejects invalid configuration | yes | `model.py` and `codec.py` not touched since the first audit, whose 19-input probe stands; `tests/test_ev_model.py` and `tests/test_ev_codec.py` pass in the 1141. |
| A2 | Priority 1-4, tier exclusivity, 0 or >= min, ranking, split, "waiting for capacity" | yes | The unmet clause is met: the section-6 input above gives `waiting_for_capacity` with everyone at 0 (doc scenario 4 with ordinary smart sessions; `test_doc_scenario_4_with_ordinary_smart_sessions_waits_for_capacity`, red on `b2e115e`'s predecessor, green now). Tier exclusivity, ranking, 15/15, 7/7/0, 6/0 and floor-plus-remainder are unchanged: the differential shows no priority or allocation differs in 3000 snapshots, and `test_doc_scenarios_2_to_5_*` and the 42 `test_share_*` cases pass. A charger with a non-empty later plan held back only by the current capacity stays `normal` / `planned_later` (probe: phase guard leaves 4 A, plan of 2 quarters later -> `normal`, `planned_later`), as the doc's "active group" wording asks; a priority-2 charger squeezed below its minimum by the phase guard reads `waiting_for_capacity`. |
| A3 | Respects amp limits | yes | No allocation differs from `348b2b0` in 3000 generated snapshots; `test_the_invariants_hold_for_generated_snapshots` and the T6/T7 tests pass. |
| A4 | Plans the cheapest quarters before the ready time | yes | `planning.py` and `quarters.py` untouched; no plan differs in the differential; T8-T12 tests pass. |
| A5 | Falls back under doubt | yes | Probe: `phase_currents=None` on the section-6 input gives `phase_data_stale` with a `phase_data_stale` event (the status precedence puts it above `waiting_for_capacity`); `test_stale_phase_data_outranks_a_starved_charger_in_the_status` and `test_stale_prices_never_mark_a_session_starved` pass; no allocation differs in the differential, which includes stale and missing measurements and stale prices. |
| A6 | Explainable, ordered output | yes | The reason that was false ("planned for later quarters" on a charger with no plan) is gone: every `planned_later` left in the differential output has a non-empty plan, and a session with an empty plan reads `waiting_for_capacity`, including when another session's plan used the capacity (probe: two sessions, one quarter, budget 16 -> `a` 16 A `in_plan`, `b` 0 A "waiting for capacity", status `limited_by_budget`). No action or event differs from `348b2b0`; down-before-up unchanged (`test_every_decrease_and_pause_comes_before_every_increase_and_resume`); a running starved charger is paused (`test_a_running_starved_charger_is_paused`); the decision still round-trips through JSON for all 3000 snapshots. Reason text and params are pinned by `test_the_starved_reason_has_a_stable_text_and_params`. |
| A7 | Invariants for any input | yes | `test_the_invariants_hold_for_generated_snapshots` passes; the re-run changed no allocation, so the properties it checks are unaffected. |
| A8 | Deterministic and dependency-free | yes | `decide(s) == decide(s)` over the 3000 snapshots; `test_a_starved_snapshot_is_pure_and_repeatable`, the hash-seed test and the AST import scan pass; `decide.py` imports only `dataclasses`, `datetime`, `enum`, `zoneinfo` and its siblings, and calls no clock. |
| A9 | Round-trips plain dicts | yes | `codec.py` untouched; round trip of the new decisions (new reason code in the existing enum) verified for 3000 snapshots; `test_ev_codec.py` passes. |
| A10 | Fast | yes | `test_ten_chargers_and_a_48_hour_series_decide_in_under_100_ms` passes (0.02 s for the test, five calls); the re-run added one `any()` per planned session and a list scan in `_status`, no loop over quarters. |

**The three step-5 re-run decisions, each judged against section 1:**

| Decision | Within section 1? | Why |
|---|---|---|
| (a) An empty plan reads reason `waiting_for_capacity` at priority 4 | **Yes, no amendment.** | A2 gives every connected smart charger a priority 1-4 and the doc's priority 4 "always gets 0"; a session with nothing planned cannot be priority 2 (current quarter not in plan) or 3 (ready time not passed, plan not completed), so 4 is the only tier that does not invent current for it. The tiers and priorities are unchanged (the differential shows no priority differs); only the reason, which A6 requires to be true, changed. The first audit already scoped the fix as "reason and status, priority stays 4; a different priority is a halt" and the re-run kept to it. |
| (b) Status `waiting_for_capacity` while a smart-charging-off charger draws | **Yes, no amendment.** | A2 speaks of smart chargers ("every connected smart charger", "everyone gets 0"); section 1's order of service serves smart-off chargers first and gives the smart ones what is left, so with less than a minimum left every smart charger gets 0. The code already read it this way for an active tier at 0 beside a smart-off charger before the re-run (pinned by the existing smart-off test); the re-run extends the same reading to starved sessions. Probe: smart-off `a` at 16 A on a 16 A budget, smart `b` -> `b` 0 A `waiting_for_capacity`, status `waiting_for_capacity`. |
| (c) A starved charger beside a served one reads `limited_by_budget` / `limited_by_phase`, not `normal` | **Yes, no amendment.** | Section 1 names the statuses and A2 fixes only the all-zero case; for a mixed case no criterion says `normal`, and `normal` would repeat the first audit's complaint (a status that says nothing is limited while a session can never be planned). `limited_by_budget` is what the budget does. Probes: starved `c1` on L1 beside a served `c2` on L2 -> `limited_by_budget`; budget 16 with one quarter, `a` takes it, `b` starved -> `limited_by_budget`. This is an interpretation the concept leaves open, recorded here; not a "better than the concept" change. |

Drift in the re-run: none. Nothing was built from the out-of-scope list; the differential
shows the re-run changed reasons and statuses only. The `structure-auditor` report for the
re-run ("No edits needed": `decide(snapshot: Snapshot) -> Decision` and `main() -> None` are
still `decide.py`'s only public names and match; the purpose line and the `main()` row are
accurate; the changes are in private names; the new starved/waiting tests fall under the
`tests/test_ev_decide.py` entry; all 9 files under `src/` and 10 under `tests/` have entries
and none points to a missing file) was applied; I confirmed the public names with `ast` and
did not re-derive the rest. The showcase `python -m energy_optimization.ev.decide` exits 0
and reads as before. A2 is met, so this is not a second failure of the same criterion; step
7 opens.

### Earlier rounds still hold

> Later rounds only. Re-check every acceptance criterion from every earlier round in this
> folder: this round changed code they depend on, and their tests passing is necessary but
> not sufficient — a criterion can be satisfied by tests that no longer describe what the
> feature does.

| Round | # | Criterion | Still met | Evidence |
|---|---|---|---|---|

---

## 7. Ship log

| Field | Value |
|---|---|
| Commits | 22 commits on top of `main` (`aa646d8`, unmoved: `origin/main` fetched at ship). By step: 1 `37aad2a` Concept; 2 `a43bf51` Plan, `7a0467a` Plan (reviewed and accepted); 3 `11250c6`, `39f27dd`, `c6a78ff` WIP step 3, `d69c87a` Add EV charging decision core; 4 `4c15ecb` WIP step 4, `1fec06b` Verify; 5 `5615a09`, `8441bd6`, `52a9aa5`, `70a1a8e`, `9154d43`, `7688400`, `ef6aef6` WIP step 5, `6d18ef2` Test; 6 `348b2b0` Concept check; A2 send-back re-run: `9e8b11c` Report waiting for capacity when planning capacity is below the minimum, `b2e115e` Verify (re-run), `4cb6db3` WIP step 5, `711bdda` Test (re-run), `14c8abf` Concept check (re-run); 7 the `Ship: EV charging decision core` commit that closes this round |
| Pushed to | `origin/feat/ev-charging-core` |

Gates on the whole tree at ship: `ruff check .` all checks passed; `ruff format --check .`
52 files already formatted; `mypy` no issues in 24 source files; `pytest` 1141 passed.
Section 6's tables have no unmet row; sections 4 and 5 carry a test against every intent
T1-T20. Diff review (`git diff main...HEAD --stat`, 22 files): only `src/`, `tests/`,
`development/`, `STRUCTURE.md`, `DEVELOPMENT.md` and `pyproject.toml`; no scratch files, sample
data, debug leftovers or TODO markers. Every step from 1 to 6 left a commit naming the round;
none missing.

---

## 8. Recommendations

> Written in step 8. Only follow-ups that are critical and belong to this work, which most
> rounds do not have: replace the table with `None.` when there are none. Lesser ideas are
> one-line notes in `DEVELOPMENT.md`, not rows here. Not bugs in what this round built —
> those go back through `/build` before the pull request. A critical defect outside what
> section 1 promised, such as a class member it put out of scope, is a recommendation here,
> and its round opens through `/fix`.

| # | Recommendation | Why it is critical | Effort | Decision |
|---|---|---|---|---|
| R1 | **Round 2 — behaviour over time:** memory between calls (and a conservative start when it is missing or invalid), minimum on/off pause, selection hysteresis, deadband, ramp-up, under-use reduction, the resume probe, the phase guard's yellow/red/emergency levels over time, and the minimum time between actions on one charger (doc scenarios 6, 8–11, 16, 21). | Section 1 names it as the second half of this feature on this branch. Without it the core re-decides from scratch every call: a charger at the edge of capacity is switched on and off every quarter, a car that under-draws keeps its allocation forever, and a phase above the threshold only shrinks headroom instead of triggering the red/emergency reaction doc §13 requires — unfit for the HA installation section 1 targets. | large | **next round** — agreed by the user at step 1 ("Two rounds, one PR") |

Decisions: `deferred`, `rejected`, or `next round` — a new numbered file in this folder,
taken back through steps 1 to 7 on the same branch.

---

## 9. Pull request

> Written in step 9, in the commit that opens the pull request — so the URL is not known
> yet and the pull request is found from the branch. The review itself is recorded on the
> pull request thread, not here: this file is `done` from step 9 on.

| Field | Value |
|---|---|
| URL | opened by step 9 — see the branch's pull request |
| Opened as | ready for review |

---

## Halted

> Only if the build stopped. Written by `/build`: the step, the reason verbatim from the
> step that halted, the question for the user — and, once answered, the answer and what
> changed because of it. Never deleted; it is the record of where the plan was thinner
> than the code needed.
