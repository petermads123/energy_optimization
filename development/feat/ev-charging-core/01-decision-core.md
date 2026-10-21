# EV charging decision core

<!-- claude-plan step=2 status=active -->

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
| 2 | Plan | `/plan` | with the user | pending |
| 3 | Implement | `/implement` | in `/build` | pending |
| 4 | Verify | `/verify` | in `/build` | pending |
| 5 | Test | `/test` | in `/build` | pending |
| 6 | Concept check | `/concept-check` | in `/build` | pending |
| 7 | Ship | `/ship` | in `/build` | pending |
| 8 | Recommend | `/recommend` | with the user | pending |
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
| `STRUCTURE.md` | changed | Tree and one line per `ev/` file (full paths, so the stop gate sees them); hello_world entries removed |
| `.claude/rules/structure-ev.md` | new | Per-module signature tables for `ev/`, `paths: ["src/energy_optimization/ev/**", "tests/test_ev_*.py"]` — the STRUCTURE.md growth rule for a subpackage |

### Public API

Every type below is a `@dataclass(frozen=True)` unless it is an enum (`enum.StrEnum`) or
an exception. Tuple-typed fields also accept any sequence and are converted to a tuple in
`__post_init__` (via `object.__setattr__`), so equality and repr are stable.

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
| `Settings(phase_budget: float, phase_threshold: float, fallback_current: int, timezone: str, voltage: float = 230.0, min_block_quarters: int = 2, price_cap: float \| None = None, default_energy_need_kwh: float = 30.0, max_price_age: timedelta = timedelta(minutes=60), max_measurement_age: timedelta = timedelta(seconds=60))` | Installation settings. Budget, threshold, voltage `> 0`; fallback `≥ 0`; `timezone` must load with `zoneinfo.ZoneInfo`; `min_block_quarters ≥ 1`; default need `≥ 0`; both ages `> 0`; `price_cap` finite when set. | A1, A3, A4, A5 |
| `Snapshot(now: datetime, settings: Settings, chargers: tuple[Charger, ...], states: tuple[ChargerState, ...], sessions: tuple[Session, ...], prices: PriceSeries, phase_currents: PhaseCurrents \| None = None)` | One call's input. `now` aware; charger ids unique; exactly one state per charger and none for an unknown id; at most one session per charger, none for an unknown id, none on a fixed charger. `phase_currents=None` = missing. | A1 |

Numeric fields reject `bool` and non-finite floats. `ConfigError.field` is the dataclass
field name (`"max_current"`), and the message names the offending value and, inside a
charger, its id: `ConfigError("max_current", "charger 'carport': must be ≥ min_current (6), got 4")`.

**`ev/result.py`**

| Signature | Purpose | Covers |
|---|---|---|
| `class Stage(StrEnum)`: `DOWN = "down"`, `UP = "up"` | Which phase of execution an action belongs to | A6 |
| `class ActionKind(StrEnum)`: `SET_CURRENT = "set_current"`, `PAUSE = "pause"`, `RESUME = "resume"` | What to do to a charger | A6 |
| `Action(charger_id: str, kind: ActionKind, target_current: float, stage: Stage)` | One change with an absolute target: `PAUSE` has `target_current=0.0`; `RESUME` carries the current to resume at | A6 |
| `class ReasonCode(StrEnum)`: `CHARGE_NOW`, `IN_PLAN`, `READY_TIME_PASSED`, `PRICES_STALE`, `PLANNED_LATER`, `WAITING_FOR_HIGHER_PRIORITY`, `WAITING_FOR_CAPACITY`, `TARGET_REACHED`, `BELOW_MINIMUM`, `SMART_CHARGING_OFF`, `FIXED`, `UNAVAILABLE`, `NO_CAR`, `NO_SESSION` — values are the lower-case names | Stable reason codes | A6 |
| `Reason(code: ReasonCode, text: str, params: dict[str, str \| float \| int \| bool] = field(default_factory=dict))` | Why a charger got what it got, as a code, its parameters and an English sentence | A6 |
| `PlannedQuarter(start: datetime, price: float \| None, current: float)` | One planned quarter: its start (in the settings timezone), its price (`None` = unpriced) and the planned amps | A4, A6 |
| `ChargerDecision(charger_id: str, allocation: float, priority: int \| None, reason: Reason, plan: tuple[PlannedQuarter, ...] = (), remaining_need_kwh: float \| None = None, deadline: datetime \| None = None, expected_finish: datetime \| None = None, reaches_target: bool \| None = None)` | Everything decided for one charger. For a fixed or unavailable charger `allocation` is the current reserved for it | A6 |
| `class Status(StrEnum)`: `NORMAL`, `LIMITED_BY_BUDGET`, `LIMITED_BY_PHASE`, `WAITING_FOR_CAPACITY`, `PHASE_DATA_STALE`, `PRICES_STALE` — values are the lower-case names | Overall status | A2, A5, A6 |
| `class EventKind(StrEnum)`: `TARGET_UNREACHABLE`, `PRICE_CAP_EXCEEDED`, `CHARGE_NOW_NEED_COVERED`, `PHASE_DATA_STALE`, `PRICES_STALE` — values are the lower-case names | Notification-worthy events | A4, A5, A6 |
| `Event(kind: EventKind, text: str, charger_id: str \| None = None, params: dict[str, str \| float \| int \| bool] = field(default_factory=dict))` | One event | A6 |
| `PhaseAmps(l1: float, l2: float, l3: float)` with `get(self, phase: Phase) -> float` | Amps per phase | A6 |
| `Decision(actions: tuple[Action, ...], chargers: tuple[ChargerDecision, ...], available: PhaseAmps, status: Status, events: tuple[Event, ...])` | One call's answer; `chargers` in snapshot charger order | A6 |

**`ev/quarters.py`**

| Signature | Purpose | Covers |
|---|---|---|
| `QUARTER: timedelta = timedelta(minutes=15)` | The planning step | A4 |
| `floor_to_quarter(moment: datetime) -> datetime` | Start of the quarter containing an aware moment, in UTC; `ValueError` if naive | A4 |
| `next_occurrence(ready_time: time, after: datetime, timezone: str) -> datetime` | First moment strictly after `after` whose local time in `timezone` is `ready_time`, in UTC. A ready time inside a spring-forward gap resolves to the first valid moment after it; inside a fall-back fold, to the first occurrence. `ValueError` if `after` is naive | A4 |
| `quarter_starts(start: datetime, end: datetime) -> list[datetime]` | UTC quarter starts from `floor_to_quarter(start)` while `< end`; empty when `end <= start` | A4 |
| `quarter_prices(series: PriceSeries) -> dict[datetime, float]` | Every interval split into quarters, keyed by UTC quarter start | A4 |
| `prices_fresh(series: PriceSeries, now: datetime, max_age: timedelta) -> bool` | `now - series.updated_at <= max_age` | A5 |

**`ev/planning.py`**

| Signature | Purpose | Covers |
|---|---|---|
| `class NeedSource(StrEnum)`: `ENERGY = "energy"`, `SOC = "soc"`, `DEFAULT = "default"` | Where a remaining need came from | A4, A6 |
| `remaining_need_kwh(session: Session, default_need_kwh: float) -> tuple[float, NeedSource]` | `energy_need_kwh − delivered` if a need is given; else `(target − soc) / 100 × capacity` if all three SOC fields are given; else `default_need_kwh − delivered`. Clipped at 0 | A4 |
| `energy_per_quarter_kwh(current: float, phase_count: int, voltage: float) -> float` | `voltage × current × phase_count / 4 / 1000` | A4 |
| `PlanRequest(charger_id: str, phases: frozenset[Phase], min_current: float, max_current: float, need_kwh: float, deadline: datetime)` | One session to plan; `min_current == max_current` means all-or-nothing | A4 |
| `SessionPlan(charger_id: str, quarters: tuple[PlannedQuarter, ...], planned_kwh: float, reachable: bool, price_cap_exceeded: bool, uses_unpriced: bool)` | One session's plan, quarters in time order | A4 |
| `plan_sessions(requests: Sequence[PlanRequest], now: datetime, prices: Mapping[datetime, float], capacity: Mapping[Phase, float], voltage: float, min_block_quarters: int, price_cap: float \| None, timezone: str) -> dict[str, SessionPlan]` | Plan every request against shared per-phase capacity (constant over the horizon), most urgent first; keyed by charger id. `timezone` only sets the zone of `PlannedQuarter.start` | A4 |

**`ev/allocation.py`**

| Signature | Purpose | Covers |
|---|---|---|
| `Claim(charger_id: str, phases: frozenset[Phase], min_current: float, max_current: float)` | One charger asking for current; `min_current == max_current` means all-or-nothing | A2, A3 |
| `share(claims: Sequence[Claim], capacity: Mapping[Phase, float]) -> dict[str, float]` | Share capacity among claims given in ranking order: 0/min selection in order, then max-min-fair 1 A increments; every claim's id is in the result | A2, A3 |
| `measurements_fresh(currents: PhaseCurrents \| None, now: datetime, max_age: timedelta, phases: frozenset[Phase]) -> bool` | Not None, `now − measured_at <= max_age`, and every phase in `phases` has a value | A5 |
| `present_draw(charger: Charger, state: ChargerState, voltage: float) -> float` | What a charger is taken to draw now on each of its phases, for the phase-guard calculation | A3 |
| `reserved_current(charger: Charger, state: ChargerState, voltage: float) -> float` | What a fixed or unavailable charger is reserved at on each of its phases | A3 |
| `phase_capacity(snapshot: Snapshot, fresh: bool) -> dict[Phase, float]` | Per phase, what all chargers together may use: `min(budget, threshold − (measured − Σ present_draw))` when `fresh`, else `budget`. May be negative | A3, A5 |

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
   6. Otherwise select in passes until the selected energy reaches the need: pass 1 uses
      usable priced quarters at or below `price_cap` (all priced quarters when there is no
      cap); pass 2 adds priced quarters above the cap (`price_cap_exceeded=True` if any is
      chosen); pass 3 adds unpriced quarters (`uses_unpriced=True`). In passes 1 and 2 the
      unit of choice is a **block**: a run of `L = min_block_quarters` consecutive usable
      quarters (consecutive = starts `QUARTER` apart) all allowed in that pass, which may
      overlap quarters already selected. A block's score is the mean price of its *new*
      quarters; blocks with no new quarter are skipped. Each step picks the block with the
      lowest score, ties to the earliest start — but when some block's new quarters would
      reach the remaining need without overshooting by a whole quarter, only such blocks
      are eligible (this ends a plan with an extension of an existing run rather than a
      fresh pair). Pass 3 uses the same blocks but scores by latest start first. When a
      pass has no complete block left (a run shorter than `L`, e.g. a horizon of one
      quarter), single quarters of that pass are allowed, cheapest first (latest first in
      pass 3).
   7. Subtract the chosen currents from `remaining` on the request's phases, then plan the
      next request.
   8. `PlannedQuarter.start` is converted to `timezone`; quarters are returned in time
      order; `planned_kwh` is the sum of their energy.
   Showcase: two cars on a shared phase, the second planned around the first.
7. **`ev/allocation.py`.**
   - `present_draw`: nominal = 0 if `state.paused`; else `current_setting` (adjustable,
     unavailable of any kind), `assumed_current` (on/off, fixed). If `measured_power_w` is
     known, measured per-phase draw = `measured_power_w / (voltage × len(phases))`, and the
     result is `min(nominal, measured)`; else nominal. (The doc §13 formula subtracts the
     *setting*; taking the smaller of setting and measured draw estimates the other load as
     at least as large, so the free current is never larger than A3's bound — a car drawing
     less than it is set to no longer inflates the free current.)
   - `reserved_current`: `max(nominal, measured per-phase draw or 0)` with the same nominal,
     for a fixed charger or any charger whose `available` is False.
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
      unavailable → reserved (`UNAVAILABLE`); fixed → reserved (`FIXED`); no car →
      0 (`NO_CAR`); no session → 0 (`NO_SESSION`); `smart_charging` False → smart-off group
      (`SMART_CHARGING_OFF`; its need is reported but not acted on — an unmanaged charger
      charges until the car stops it); remaining need 0 → 0 (`TARGET_REACHED`, plus a
      `CHARGE_NOW_NEED_COVERED` event if `charge_now`); `charge_now` → priority 1 (`CHARGE_NOW`);
      prices not fresh → priority 3 (`PRICES_STALE`); deadline ≤ now → priority 3
      (`READY_TIME_PASSED`); otherwise planned, then priority 2 if the quarter containing
      `now` is in its plan (`IN_PLAN`), else priority 4 (`PLANNED_LATER`).
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
      clipped at 0 (the phase guard is a now-quantity and is not projected); requests for
      every session that reached the "otherwise planned" rule; `plan_sessions`. Charge-now
      sessions are not planned and do not consume planning capacity.
   7. **Active tier:** the lowest of priorities 1, 2, 3 that has at least one charger that
      can run; nobody in priority 4 ever gets current. Active claims ranked by deadline
      (earliest first; for priority 3 the most overdue first), then larger remaining need,
      then id; `share` against the remaining `cap`. Chargers in lower tiers get 0 with
      `WAITING_FOR_HIGHER_PRIORITY` (priority 4 keeps `PLANNED_LATER`); an active-tier
      charger that got 0 gets `WAITING_FOR_CAPACITY`.
   8. **Actions**, compared with the present state (present = 0 if paused, else
      `current_setting`, for on/off `assumed_current` when not paused), never for reserved
      or carless chargers: target 0 and present > 0 → `PAUSE` (down); target > 0 and
      paused → `RESUME` at target (up); not paused and target ≠ present → `SET_CURRENT`,
      down if lower, up if higher; equal → none. Down actions, then up actions, each sorted
      by charger id.
   9. **Expected finish and reaches_target:** with a plan, the end of the first quarter at
      which cumulative planned energy reaches the need, else None; without a plan and with
      allocation > 0, `now + need / (voltage × allocation × phases)` hours; else None.
      `reaches_target` = finish is not None and finish ≤ deadline (False once the deadline
      has passed); None when there is no session. Datetimes in the result are in `zone`.
   10. **Events:** `TARGET_UNREACHABLE` per unreachable plan (params `reachable_kwh` =
       delivered + planned, `target_kwh` = delivered + need, `deadline`);
       `PRICE_CAP_EXCEEDED` per plan that broke the cap; `CHARGE_NOW_NEED_COVERED`;
       `PHASE_DATA_STALE` when `fallback`; `PRICES_STALE` when not `prices_ok`.
   11. **Status**, first that applies: `PHASE_DATA_STALE` if fallback; `PRICES_STALE` if
       prices stale; `WAITING_FOR_CAPACITY` if the active tier has a charger that can run
       and every active-tier charger got 0; `LIMITED_BY_PHASE` if an active-tier or
       smart-off charger got less than its effective max and one of its phases has
       `threshold − other load < budget`; `LIMITED_BY_BUDGET` if one got less than its
       effective max otherwise; `NORMAL`.
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
   naive result; `time` as `"HH:MM"` or `"HH:MM:SS"`; `timedelta` as seconds (number);
   `frozenset[Phase]` as a sorted list, where a duplicate is rejected; tuples as lists.
   Optional fields may be omitted. Unknown keys are rejected. Int fields accept an
   integral float; bool is never a number. Any `ConfigError` raised while building an
   object is re-raised with the dotted path prefixed (`"chargers[1].max_current"`).
   Showcase: snapshot → dict → `json.dumps` → back, printing the JSON and the equality.
10. **`ev/__init__.py`.** Relative re-exports of every public name above except the
    planning/allocation/quarters helpers (importable from their modules), with `__all__`.
11. **STRUCTURE.md and `.claude/rules/structure-ev.md`.** Tree gains `ev/`; one line per
    `ev/` file and per test file, by full path; the per-module signature tables go in the
    rules file.

### Test intents

| # | Must prove | Covers |
|---|---|---|
| T1 | Every invalid case listed in A1 raises `ConfigError` whose `field` names the field, for direct construction — including fields that do not belong to a kind, an unloadable timezone, overlapping or misaligned price intervals, non-finite numbers and bools as numbers | A1 |
| T2 | `snapshot_from_dict` and `decision_from_dict` reject missing, unknown, wrongly typed and invalid fields with a dotted path, and a duplicated phase in a list | A1, A9 |
| T3 | Doc scenarios 1–5 through `decide`: tier exclusivity, 15/15 split, 7/7/0 with the latest ready time cut, all-0 with "waiting for capacity", earliest ready time wins at 6 A | A2 |
| T4 | `share`: 0/min selection in ranking order, a later claim on another phase still fits, floor and 1 A remainder in order, max and all-or-nothing respected, empty claims, zero or negative capacity | A2, A3 |
| T5 | Priority assignment: charge-now → 1; current quarter in plan → 2; deadline passed (from `plugged_in_at`) → 3; plan later → 4; need 0 → target reached with the charge-now event; a priority-1 charger below its minimum does not occupy the tier | A2 |
| T6 | Doc scenarios 7 (the phase binds at 6 A), 12 (mixed phases share max-min fairly; the shared phase never exceeds the budget) and 20 (on/off accounted at its assumed current, never partially) | A3 |
| T7 | Fixed and unavailable chargers are reserved at the higher of level/setting and measured draw, receive no actions, and shrink everyone else's capacity; smart-off chargers are served first and cut only when every smart charger is at 0; `present_draw` takes the smaller of setting and measured draw, paused counts 0 | A3, A5 |
| T8 | Doc scenario 14: 20 kWh at 11 kW in an 8-hour horizon plans 8 quarters with no run shorter than 2; the plan is the cheapest such selection on a hand-checked series | A4 |
| T9 | Doc scenario 15: an unreachable target plans every usable quarter and raises `TARGET_UNREACHABLE` with reachable and target kWh | A4 |
| T10 | Doc scenario 17 (known quarters only): published quarters are used first, unpriced quarters only when needed and latest first, flagged; supplying the prices changes the plan | A4 |
| T11 | Doc scenario 18: 92 and 100 quarters on the 2026 Copenhagen DST days, a correct deadline across midnight and across both transitions, a ready time inside the gap and the fold | A4 |
| T12 | Price cap skipped when the need fits under it, broken with `PRICE_CAP_EXCEEDED` when it does not; hourly intervals split into four equal quarters; shared capacity plans the least-slack session first and never plans a phase beyond its capacity | A4 |
| T13 | Fallback: stale, missing (None object, a missing used phase, a future-dated reading counts as fresh) → every controllable charger ≤ its own fallback or the global one; below-min and on/off-above-fallback → 0; budget and tiers still apply; status and event. Stale prices → no plans, priority 3, status and event | A5 |
| T14 | Contradictory input charges least: a paused charger with measured power adds to the other load; measured draw above the setting does not raise free current; a connected car with SOC above target needs 0 | A5 |
| T15 | Doc scenario 19: every down action precedes every up action; targets are absolute; no action when nothing changes, for reserved chargers or for chargers without a car; pause/resume/set-current chosen per the guide | A6 |
| T16 | Every charger carries the right reason code and params; `expected_finish` and `reaches_target` for planned, unplanned and passed-deadline sessions; `available` per phase; result datetimes are in the settings timezone | A6 |
| T17 | Property-based (`hypothesis`): for generated snapshots, every output holds 0 or min…effective max (on/off: 0 or assumed), Σ on each phase ≤ budget − reserved, ≤ the phase-guard limit when fresh, ≤ fallback when not, and down before up — computed independently in the test, not via the module's helpers | A7 |
| T18 | `decide` called twice on the same snapshot gives equal decisions and leaves the snapshot equal to a deep copy taken before; every module under `src/energy_optimization/` imports only the standard library and itself (AST scan against `sys.stdlib_module_names`) | A8 |
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
  extension fits; (g) a future-dated measurement counts as fresh. Anything **else** the
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
- **The stop gate's file check** needs every `ev/` path written in full in STRUCTURE.md;
  the rules file alone does not satisfy it.

---

## 3. Implementation notes

> Written in step 3. Only deviations from the plan above, each with its reason. "Built as
> planned" is a complete and good entry. On a fix round, also the reproduction test's red
> run, pasted here before the fix was written — step 6 cites it.

---

## 4. Verification log

> Written in step 4: the static half. Command output, not a summary of it.

| Check | Result |
|---|---|
| `ruff check .` | |
| `ruff format --check .` | |
| `mypy` | |
| Plan completeness | every signature in the Public API table exists as written |
| `STRUCTURE.md` | in sync |
| `python -m <package>.<module>` | |

---

## 5. Test log

> Written in step 5: the dynamic half.

| Intent | Test names | Result |
|---|---|---|

Edge cases considered and deliberately skipped, with reasons:

---

## 6. Concept check

> Written in step 6, against section 1 — not against section 2. The question is whether
> the thing built is the thing agreed, not whether it matches the plan.

| # | Criterion | Met | Evidence |
|---|---|---|---|
| A1 | | | |

Drift found, and what was done about it:

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
| Commits | |
| Pushed to | |

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
| R1 | | | | |

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
