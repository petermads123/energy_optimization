# EV charging core: behaviour over time

<!-- claude-plan step=6 status=active -->

| Field | Value |
|---|---|
| Feature | `feat/ev-charging-core` |
| Round | `2` |
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
| 6 | Concept check | `/concept-check` | in `/build` | pending |
| 7 | Ship | `/ship` | in `/build` | pending |
| 8 | Recommend | `/recommend` | with the user | pending |
| 9 | Pull request | `/create-pr` | with the user | pending |
| 10 | Review | `/watch-pr` | on the pull request | pending |

Statuses: `pending`, `in progress`, `done`.

## Builds on

| Round | File | What it delivered |
|---|---|---|
| 1 | `01-decision-core.md` | The single-call decision core `energy_optimization.ev`: validated input model (`ConfigError` naming the field), price quarters across DST, cheapest-block planning with shared capacity, price cap and unpriced quarters, priority tiers 1–4 with max-min-fair 0/min sharing, per-phase budget and phase-guard limits, fixed/unavailable reservations, smart-off served first, per-charger fallback current under stale measurements, ordered absolute actions, reason codes, status and events, plain-dict codec. A1–A10 met; 1141 tests. |

This round came from recommendation `R1` of round 1, which read:

> **Round 2 — behaviour over time:** memory between calls (and a conservative start when it is missing or invalid), minimum on/off pause, selection hysteresis, deadband, ramp-up, under-use reduction, the resume probe, the phase guard's yellow/red/emergency levels over time, and the minimum time between actions on one charger (doc scenarios 6, 8–11, 16, 21).

What is already on the branch that this round must not break: every round-1 acceptance
criterion A1–A10 — in particular the §12 invariants under random inputs (A7), determinism
and the stdlib-only rule (A8), the plain-dict round trip (A9, which a memory object must
join) and the 100 ms budget (A10). `decide`'s public signature may need a memory argument
and the `Decision` a new-memory field; any such change is decided in this round's concept.

---

## 1. Concept

### What this is

Round 1's decision core decides every call from scratch. This round gives it **memory**:
every `Decision` returns a small, plain-JSON memory object and the caller hands it back in
the next snapshot (persisting it — an HA entity, a file — is the caller's job). With it the
core stops flapping, regulates chargers to what the car actually draws, runs the
phase-guard levels over time, and paces commands to slow cloud chargers. It stays pure: no
clock, no I/O; every timer is measured from the snapshot's `now` against timestamps held in
memory. The call cadence is the caller's choice — every 5 minutes, every 15, and on events
all work; the price plan stays on 15-minute quarters.

### Why it is worth building

Without memory the core re-decides each call in isolation: a charger at the edge of
capacity is switched on and off every call, a car that tapers or stops drawing keeps its
allocation forever while others wait, a phase above the threshold only shrinks headroom
instead of triggering the red/emergency reactions doc §13 asks for, and a slow cloud
charger can be sent the same or a compounding command before the last one landed. That is
unfit for the HA installation round 1 targets.

### Inputs and outputs

**Inputs (additions to round 1's snapshot):**

- **Memory** — the object the previous decision returned, or nothing. Missing, malformed,
  out-of-date or future-dated memory is never an error (see A1).
- **Settings** gain (defaults in brackets): emergency threshold (optional, **off** when
  unset), emergency calm period (5 min), yellow margin (2 A), green hold (60 s), deadband
  (2 A), ramp step (2 A), ramp period (5 min), under-use period (15 min), reduction method
  (to measured draw + margin, or halving; default draw + margin), under-use margin (1 A),
  minimum on/off pause (15 min), selection hysteresis (30 min), resume probe (60 min),
  minimum time between actions on one charger (30 s).
- `Session.plugged_in_at` stays optional; when the caller omits it, the session start
  recorded in memory (the call that first saw the car connected) is used.

**Outputs (additions to round 1's decision):**

- **New memory** for the next call.
- **Phase-guard level**: green, yellow, red or emergency.
- **Next check**: the earliest moment a new call could change the decision (a pause, hold,
  period, probe or pacing window ending), or none — so the AppDaemon app can schedule a
  `run_at` for it.
- New events: `MEMORY_RESET`, `PHASE_GUARD_INTERVENED`; new reason detail where
  anti-flapping, regulation, the resume probe, a guard level or pacing held a charger back.

**What memory holds** (doc §14): per charger the session start, when it was last switched
on or off for capacity reasons, whether it is selected, the under-use and full-use timers
and its regulated cap, when it was cut for under-use (for the probe), and its last command
and when; for the installation, the phase-guard level and since when, and since when the
phases have been continuously green.

**Decisions taken with the user:**

- **Ramp-up only after a cut.** A charger starts at its full share; only after an
  under-use cut does it climb back, one ramp step per whole ramp period of full use, up to
  its share. The ramp period is its own setting (5 min) so a 5-minute cadence ramps
  promptly.
- **Red cuts to fit immediately** — round 1's free-current rule (threshold − other load)
  already reduces the chargers to what fits, lowest tier first then fairly; red adds "no
  increases" and a `PHASE_GUARD_INTERVENED` event. Doc §13's one-step-per-call reduction
  was considered and not chosen.
- **The emergency threshold is optional**; unset means no emergency level and red is the
  strongest reaction.
- **"Charge now"** skips the minimum pause and the hysteresis (doc §8) but never the guard
  levels, the fallback, the budget or the pacing.
- **Spike smoothing is the caller's job**: the package acts on the phase reading it gets.
- **Conservative start** = no increases on the call that has no usable memory; normal
  rules from the next call.

### How it connects to the rest of the repo

Extends round 1's `energy_optimization.ev` subpackage: the snapshot and decision types, the
settings, `decide` and the dict codec all grow; planning and sharing are reused. Nothing
outside `src/energy_optimization/ev/`, its tests, `STRUCTURE.md` and this plan folder
changes. The caller is still the user's AppDaemon app, which now also stores the memory
between calls.

### Explicitly out of scope

- Everything round 1 put out of scope "not at all" and "never part of the package".
- Smoothing short spikes in the phase readings (caller).
- Persisting the memory (caller).
- Soft start (ramping every start from the minimum) — considered and not chosen.
- Doc §13's one-step-at-a-time red reduction — considered and not chosen.
- The example AppDaemon app and README section — still a `DEVELOPMENT.md` note.

### Acceptance criteria

| # | The finished round 2... |
|---|---|
| A1 | **Carries memory and starts conservatively.** Every `Decision` returns memory that the next snapshot accepts, and it round-trips through plain JSON-compatible dicts like the rest of round 1's A9. Missing, malformed, out-of-date or future-dated memory is never an error: it is treated as a restart — no increases on that call and a `MEMORY_RESET` event. Entries for chargers no longer in the snapshot are dropped. Doc scenario 21. |
| A2 | **Does not flap.** A charger stopped for lack of capacity cannot restart before the minimum pause has passed; one started for capacity is not dropped for an equal-tier charger before the minimum pause either; a selected charger keeps its place unless a contender's ready time is more than the hysteresis earlier. None of this applies to priority 1, and none of it ever breaks the budget, the phase guard or the fallback cap. Doc scenario 6. |
| A3 | **Regulates adjustable chargers.** Within the deadband nothing changes; after a whole under-use period the charger is cut to its measured draw + the margin (or halved, per the setting), and the freed current goes to others in the same call; a cut below the minimum becomes 0. After a cut, each whole ramp period of full use raises it one ramp step, up to its share. On/off chargers and chargers without a power reading are never regulated. Doc scenarios 8–11. |
| A4 | **Probes to resume.** A charger set to 0 for under-use is offered current again when its car is re-plugged, its session changes, or the resume-probe time has passed. Doc scenario 16. |
| A5 | **Runs phase-guard levels over time.** Each decision reports green / yellow / red / emergency. Yellow and red allow no increases; red cuts to fit and raises `PHASE_GUARD_INTERVENED`; emergency (when configured) pauses every controllable charger in that call and allows restarts only after the calm period. Any increase requires the phases to have been green continuously, across calls, for the green hold; this applies to fresh measurements — under stale or missing measurements round 1's fallback rule governs increases (user decision at step 2). |
| A6 | **Paces the actuators.** No increase or resume is sent to a charger within the minimum time between actions; decreases and pauses always go. A command identical to one still in flight is not repeated, and a decrease that has not taken effect yet is not compounded by a second decrease for the same overload. |
| A7 | **Says when to call next.** Each decision gives the earliest moment a new call could change something (a pause, hold, period, probe or pacing window ending), or none. |
| A8 | **Keeps round 1's guarantees.** Every round-1 criterion A1–A10 still holds. Under random multi-call sequences that feed each decision's memory into the next snapshot, every output still holds the doc §12 invariants — with the phase-guard bound counting a charger at its measured draw, instead of its setting, for `min_action_interval` after a decrease sent to it (slow actuators, doc §13; user decision at step 2) — plus: no increase in yellow, red or a conservative start; every controllable charger at 0 in emergency; down before up. Determinism, the stdlib-only rule and the 100 ms budget hold. |

### Open questions

None.

**Amended at step 2** (the `plan-critic` found two criteria in conflict; the user decided):
A5 — the green hold applies to fresh measurements, and stale data keeps round 1's fallback
rule for increases; A8 — for `min_action_interval` after a decrease, the phase-guard
invariant counts that charger at its measured draw, so a car still ramping down is not cut
twice for the same overload.

---

## 2. Plan

Accepted by the user in advance: at the round-2 concept gate they chose "Confirm, same as
round 1" — plan, have it reviewed, then build without stopping for the plan. Step 3 opens
once the `plan-critic` findings below are applied.

### Survey

Round 1 left `src/energy_optimization/ev/` as: `model.py` (input types, `ConfigError`,
validation), `result.py` (output types), `quarters.py`, `planning.py`, `allocation.py`
(`share`, `phase_capacity`, `present_draw`, `reserved_current`, `measurements_fresh`),
`decide.py` (one public function, `decide(snapshot) -> Decision`, built around a private
per-charger `_Item` record: classify → limits → reservations → smart-off share → planning
→ tier share → reasons/actions/events/status) and `codec.py` (dict conversion with dotted
paths). Everything round 2 needs to *decide* is already computed there; what is missing is
state carried between calls. Reused as is: `share` (the hard limits stay where they are),
`plan_sessions`, `measurements_fresh`, `reserved_current`, the codec's `_Reader`
machinery. `phase_capacity` gains one optional parameter (A6). The round-1 test suites
build snapshots through one helper per file (`snap(...)` in `tests/test_ev_decide.py`),
which is where a "warm" memory goes (Risk 1).

### Approach

**Chosen: memory as an input type in `model.py`, two small pure modules for the two state
machines that have nothing to do with sharing (`guard.py` for the phase-guard level,
`regulation.py` for under-use / ramp / resume), and everything that interacts with
ranking, sharing and actions — anti-flapping, hysteresis, pacing, the conservative start,
the next-check time and building the new memory — inside `decide.py`.** The round-1
allocation stays the *upper bound*: round 2 only ever narrows a claim before `share`
(regulated cap, minimum pause) or lowers / holds a target after it (emergency, no-increase,
pacing). Nothing in round 2 raises a target above what round 1 would have given, so every
round-1 limit invariant holds by construction, and A8's new invariants are all of the form
"never above the present setting" or "zero".

**Rejected.** *A separate `Controller` class holding the memory* — stateful objects break
round 1's purity (A8) and make the HA app's restart story the package's problem. *Memory in
its own `memory.py`* — `Snapshot` must type it and `model.py` would import from a module
that imports `ConfigError` back from `model.py`; keeping the memory types beside the other
input types avoids the cycle at the cost of a longer `model.py`. *Doing all of round 2
inside `decide.py`* — the guard latch and the regulation timers are self-contained state
machines worth testing on their own; keeping them out of `decide.py` keeps it near its
current size.

### Modules

| Path | New or changed | Purpose |
|---|---|---|
| `src/energy_optimization/ev/model.py` | changed | `Reduction`, `GuardLevel`, `ChargerMemory`, `Memory`; new `Settings` fields; `Snapshot.memory` (A1, A3, A5, A6) |
| `src/energy_optimization/ev/result.py` | changed | New `ReasonCode`, `EventKind`, `Status` members; `Decision.memory`, `.guard_level`, `.next_check` (A1, A5, A7) |
| `src/energy_optimization/ev/guard.py` | new | Phase-guard level from one reading, and the level/green/emergency latch over time (A5) |
| `src/energy_optimization/ev/regulation.py` | new | Under-use cut, ramp back, resume probe for one adjustable charger (A3, A4) |
| `src/energy_optimization/ev/allocation.py` | changed | `phase_capacity` gains `settling` (A6) |
| `src/energy_optimization/ev/planning.py` | changed (step 3 re-run, step 5 re-run) | `plan_sessions` gains `first`: the sessions that keep the current quarter, in the order they contest it; `PlanRequest` gains `skip_current` (A2, round-1 A4) |
| `src/energy_optimization/ev/decide.py` | changed | Memory usability, anti-flapping, hysteresis, regulation and guard wiring, pacing, next check, new memory (A1–A8) |
| `src/energy_optimization/ev/codec.py` | changed | Memory in snapshot and decision dicts; `memory_to_dict`, `memory_from_dict` (A1) |
| `src/energy_optimization/ev/__init__.py` | changed | Re-export the new public names |
| `tests/test_ev_guard.py`, `tests/test_ev_regulation.py` | new | One test file per new module |
| `tests/test_ev_*.py` (round 1) | changed | Snapshot helpers gain a warm memory (Risk 1); new cases for changed modules |
| `STRUCTURE.md` | changed | New modules and test files by full path; changed signature rows |

### Public API

Additions and changes only; round 1's table in `01-decision-core.md` stays the reference
for everything not listed. Same conventions: frozen dataclasses validating in
`__post_init__` and raising `ConfigError` naming the field; `StrEnum`s with lower-case
values; tuple fields accept any sequence.

**`ev/model.py`**

| Signature | Purpose | Covers |
|---|---|---|
| `class Reduction(StrEnum)`: `TO_USE = "to_use"`, `HALVE = "halve"` | How an under-use cut is computed | A3 |
| `class GuardLevel(StrEnum)`: `GREEN = "green"`, `YELLOW = "yellow"`, `RED = "red"`, `EMERGENCY = "emergency"` | The phase guard's level | A5 |
| `ChargerMemory(charger_id: str, session_start: datetime \| None = None, car_connected: bool = False, session_key: str = "", running: bool = False, switched_at: datetime \| None = None, stopped_for_capacity: bool = False, selected: bool = False, regulated_cap: int \| None = None, under_use_since: datetime \| None = None, full_use_since: datetime \| None = None, under_use_stop_at: datetime \| None = None, last_target: float \| None = None, last_action_at: datetime \| None = None, decreased_at: datetime \| None = None)` | What one charger carries between calls. Datetimes aware; `regulated_cap ≥ 0`; `last_target ≥ 0`; `charger_id` non-empty | A1–A4, A6 |
| `Memory(saved_at: datetime, chargers: tuple[ChargerMemory, ...] = (), level: GuardLevel \| None = None, green_since: datetime \| None = None, emergency_at: datetime \| None = None)` | Everything carried between calls; `saved_at` is the `now` of the call that produced it. Datetimes aware; charger ids unique. Method `charger(self, charger_id: str) -> ChargerMemory \| None` | A1, A5 |
| `Settings(...)` gains, after round 1's fields and in this order: `emergency_threshold: float \| None = None`, `emergency_calm: timedelta = timedelta(minutes=5)`, `yellow_margin: float = 2.0`, `green_hold: timedelta = timedelta(seconds=60)`, `deadband: float = 2.0`, `ramp_step: int = 2`, `ramp_period: timedelta = timedelta(minutes=5)`, `under_use_period: timedelta = timedelta(minutes=15)`, `reduction: Reduction = Reduction.TO_USE`, `under_use_margin: float = 1.0`, `min_pause: timedelta = timedelta(minutes=15)`, `hysteresis: timedelta = timedelta(minutes=30)`, `resume_probe: timedelta = timedelta(minutes=60)`, `min_action_interval: timedelta = timedelta(seconds=30)`, `max_memory_age: timedelta = timedelta(minutes=30)` | Validation: `emergency_threshold > phase_threshold` when set; `yellow_margin`, `deadband`, `under_use_margin ≥ 0`; `ramp_step ≥ 1`; `emergency_calm`, `green_hold`, `min_pause`, `hysteresis`, `min_action_interval ≥ 0`; `ramp_period`, `under_use_period`, `resume_probe`, `max_memory_age > 0` | A2–A6 |
| `Snapshot(...)` gains `memory: Memory \| None = None` as its last field | The previous decision's memory | A1 |

`max_memory_age` is the one setting section 1 did not list: A1's "out-of-date" needs a
threshold, and 30 minutes is two missed 15-minute calls.

**`ev/result.py`**

| Signature | Purpose | Covers |
|---|---|---|
| `ReasonCode` gains `MINIMUM_PAUSE = "minimum_pause"`, `UNDER_USE = "under_use"`, `EMERGENCY = "emergency"` | Held off by anti-flapping; at 0 after an under-use cut, waiting for the probe; paused by the emergency level | A2–A5 |
| `EventKind` gains `MEMORY_RESET = "memory_reset"`, `PHASE_GUARD_INTERVENED = "phase_guard_intervened"` | Conservative start; red or emergency | A1, A5 |
| `Status` gains `EMERGENCY = "emergency"`, first in precedence | The emergency level is in force | A5 |
| `Decision(...)` gains, after round 1's fields: `memory: Memory \| None = None`, `guard_level: GuardLevel \| None = None`, `next_check: datetime \| None = None` | `decide` always sets `memory`; `guard_level` is the guard's level after the call (`advance_guard(...).level`) — None when the measurements are stale unless an emergency is latched; `next_check` in the settings timezone | A1, A5, A7 |

**`ev/guard.py`** (new)

| Signature | Purpose | Covers |
|---|---|---|
| `instant_level(currents: PhaseCurrents, phases: frozenset[Phase], settings: Settings) -> GuardLevel` | Level from one reading over `phases` (a `None` value is skipped): emergency if any reading `> emergency_threshold` (when set), else red if any `> phase_threshold`, else yellow if any `≥ phase_threshold − yellow_margin`, else green | A5 |
| `GuardState(level: GuardLevel \| None, green_since: datetime \| None, emergency_at: datetime \| None, increases_allowed: bool, intervened: bool)` | The guard after one call | A5 |
| `advance_guard(memory: Memory \| None, instant: GuardLevel \| None, now: datetime, settings: Settings) -> GuardState` | One step of the guard over time (guide 4) | A5 |

**`ev/regulation.py`** (new)

| Signature | Purpose | Covers |
|---|---|---|
| `Regulation(regulated_cap: int \| None, under_use_since: datetime \| None, full_use_since: datetime \| None, under_use_stop_at: datetime \| None, cut: bool)` | One adjustable charger's regulation after one call; `regulated_cap` 0 means stopped for under-use | A3, A4 |
| `regulate(previous: ChargerMemory \| None, charger: Charger, state: ChargerState, reset: bool, now: datetime, settings: Settings) -> Regulation` | One step of under-use / ramp / probe (guide 5) | A3, A4 |

**`ev/allocation.py`**

| Signature | Purpose | Covers |
|---|---|---|
| `phase_capacity(snapshot: Snapshot, fresh: bool, settling: frozenset[str] = frozenset()) -> dict[Phase, float]` | As round 1, except a charger whose id is in `settling` counts at its **measured** per-phase draw (when known) instead of the smaller of setting and measured: a car still ramping down after a decrease is not mistaken for other load | A6 |

**`ev/planning.py`** (added at the step 3 re-run after step 6)

| Signature | Purpose | Covers |
|---|---|---|
| `plan_sessions(requests: Sequence[PlanRequest], now: datetime, prices: Mapping[datetime, float], capacity: Mapping[Phase, float], voltage: float, min_block_quarters: int, price_cap: float \| None, timezone: str, first: Sequence[str] = ()) -> dict[str, SessionPlan]` | As round 1, except the sessions named in `first`, in the order they contest it, keep the quarter containing `now` when their own plan (made alone on what the earlier ones left of it) has it there; everything else, their later quarters included, is planned most urgent first on what remains, so the plans together never book more than `capacity`. A bare string is a `TypeError`. *(Changed at the step 5 re-run: it was `Collection[str]` and claimed every quarter.)* | A2, round-1 A4 |
| `PlanRequest(..., deadline: datetime, skip_current: bool = False)` | `skip_current` closes the quarter containing `now` to the session (it is held off by the minimum pause), so it neither plans nor books it. *(Added at the step 5 re-run.)* | A2 |

**`ev/codec.py`**

| Signature | Purpose | Covers |
|---|---|---|
| `memory_to_dict(memory: Memory) -> dict[str, object]` | Memory to a plain JSON-compatible dict | A1 |
| `memory_from_dict(data: object) -> Memory \| None` | Dict to Memory; **never raises** — anything malformed gives None | A1 |

`snapshot_to_dict`/`snapshot_from_dict` carry an optional `"memory"` key, decoded with
`memory_from_dict` (malformed memory becomes `None`, never a `ConfigError`).
`decision_to_dict`/`decision_from_dict` carry `"memory"`, `"guard_level"` and
`"next_check"`; inside a decision the memory is decoded strictly, with dotted paths, like
every other field. `decide(snapshot: Snapshot) -> Decision` is unchanged.

### Implementation guide

1. **`model.py`.** Add the two enums, `ChargerMemory`, `Memory` (with `charger()`), the
   `Settings` fields and their validation, and `Snapshot.memory` (validated as a `Memory`
   or `None`; its charger entries are **not** cross-checked against the snapshot's
   chargers — unknown ids are dropped by `decide`, guide 6.1). Showcase gains a memory.
2. **`result.py`.** The new members and `Decision` fields.
3. **`allocation.py`.** `phase_capacity`'s `settling`: for those ids (controllable,
   available chargers only) the draw term is `measured_power_w / (voltage × phases)` when
   measured, else `present_draw` as before.
4. **`guard.py`.** `instant_level` per its row. `advance_guard`:
   - **Stale** (`instant is None`): if the memory shows a latched emergency
     (`memory.level is EMERGENCY`), level stays `EMERGENCY`, `green_since = None`,
     `increases_allowed = False`, `intervened = True`. Otherwise `level = None`,
     `green_since = None`, `emergency_at` carried, `increases_allowed = True` (round 1's
     fallback rule governs stale data — its "increases up to the fallback are allowed" is
     a round-1 decision A8 keeps), `intervened = False`.
   - **Instant emergency**: level `EMERGENCY`, `emergency_at = now`, `green_since = None`.
   - **Latched emergency** (`memory.level is EMERGENCY`, instant not emergency): if the
     instant is green, `green_since` = the memory's `green_since` or `now`; the latch
     releases (level green, `emergency_at = None`) once `now − green_since ≥
     emergency_calm`, else level stays `EMERGENCY`. A non-green instant keeps
     `EMERGENCY` and sets `green_since = None`.
   - **Otherwise**: level = instant; `green_since` = (memory's `green_since` or `now`)
     when green, else `None`. "Continuously across calls" therefore means every call since
     `green_since` saw green.
   - `increases_allowed` (fresh cases) = level green and `now − green_since ≥
     green_hold`. With no memory `green_since = now`, so a memoryless call never increases
     unless `green_hold` is 0 (the conservative start blocks it anyway, guide 6.8).
   - `intervened` = level red or emergency.
   Showcase: a red reading, then three green calls 30 s apart, printing level and
   `increases_allowed`.
5. **`regulation.py`.** `regulate`:
   - Not `ADJUSTABLE` → `Regulation(None, None, None, None, False)`.
   - Start from `previous` (all None when absent); if `reset`, clear all four fields.
   - **Probe:** if `under_use_stop_at` is set and `now − under_use_stop_at ≥
     resume_probe`, clear `regulated_cap` and `under_use_stop_at`.
   - If paused, no car, or `measured_power_w is None`: clear both timers, keep
     `regulated_cap` and `under_use_stop_at`, `cut=False`.
   - `measured = measured_power_w / (voltage × len(phases))`, `setting =
     current_setting`.
   - Three zones, so a steady draw after a cut is neither ramped nor cut again:
   - **Under-use** (`measured < setting − deadband`): `under_use_since` = previous or
     `now`; `full_use_since = None`. When `now − under_use_since ≥ under_use_period`:
     `new = ceil(measured + under_use_margin)` (`TO_USE`; `ceil` keeps at least the margin
     of headroom) or `floor(setting / 2)` (`HALVE`); `new < min_current` →
     `regulated_cap = 0`, `under_use_stop_at = now`; else `regulated_cap = new`; clear
     `under_use_since`; `cut = True`.
   - **Full use** (`measured ≥ setting − under_use_margin / 2`, including a draw above the
     setting): `under_use_since = None`; `full_use_since` = previous or `now`. If
     `regulated_cap` is set and `> 0` and `now − full_use_since ≥ ramp_period`:
     `regulated_cap = min(regulated_cap, floor(setting)) + ramp_step` (so a cap never
     climbs while the setting is held below it — yellow, red, pacing), `full_use_since =
     now`; a cap `≥ max_current` is cleared (`None`).
   - **In between** (neither): clear both timers; nothing else changes.
   Doc scenarios: 16 A set, 7 A drawn for 15 min → cap 8 (`ceil(7 + 1)`); 10 set, 2 drawn
   → `ceil(3) = 3 < 6` → 0 with `under_use_stop_at`; 16 set, 14.5 drawn, deadband 2 → nothing; cap 10, 9.5 drawn for a
   ramp period → 12. Showcase: those four.
6. **`decide.py`.** In order, around round 1's pipeline:
   1. **Memory.** `usable` = memory given, `saved_at ≤ now` and `now − saved_at ≤
      max_memory_age`. Unusable → `memory = None`, `conservative = True`, and a
      `MEMORY_RESET` event (with a param `reason`: `"missing"`, `"invalid"`,
      `"out_of_date"` or `"future"`). `"invalid"` is memory present in the snapshot dict
      that the codec could not decode; how the codec hands that fact to `decide` (a
      private marker) is the implementer's choice, recorded in section 3. Memory is also
      `"future"` — unusable — when **any** timestamp inside it is later than its
      `saved_at`. Entries for charger ids not in the snapshot are ignored.
   2. **Session start and reset.** Per charger with a car: `session_start` = memory's
      when the memory says the car was already connected, else `now`. `session_key` = a
      deterministic string of the session's `ready_time`, `energy_need_kwh`,
      `target_soc_percent`, `charge_now`, `user_current_limit` and `plugged_in_at`. `reset`
      = the car was not connected in memory (re-plug or first sight) or the key changed — a
      changed `plugged_in_at` is therefore a re-plug even when both calls saw the car
      connected. When the caller gives `plugged_in_at` it is the session start; when it is
      None, the deadline is seen from `session_start` (round 1 used `now`). A reset clears
      the regulation fields (guide 5) and leaves the anti-flapping fields
      (`running`, `switched_at`, `stopped_for_capacity`, `selected`) as they were: a
      re-plug is not a reason to skip the minimum pause.
   3. **Guard.** `instant = instant_level(...)` over the controllable phases when fresh,
      else None; `guard = advance_guard(memory, instant, now, settings)`.
   4. **Regulation.** For every controllable adjustable charger in the SMART group with a
      car (smart-charging-off chargers are unmanaged, as in round 1, and never
      regulated), `regulate(...)`; a `regulated_cap` lowers its effective max before `can_run` is
      decided (`0` → cannot run, final code `UNDER_USE`). The plan request's max uses the
      cap only when it is at or above the charger's minimum.
   5. **Settling and capacity.** `settling` = ids whose memory `decreased_at` is within
      `min_action_interval` of `now`; `raw = phase_capacity(snapshot, fresh, settling)`.
   6. **Anti-flapping, before the tier share** (not for priority 1):
      - *Minimum pause (off):* a smart charger whose memory says `stopped_for_capacity`,
        not running, and `now − switched_at < min_pause` is left out of the claims —
        allocation 0, final code `MINIMUM_PAUSE` (param `until`, ISO string) — but still
        occupies its tier.
      - *Ranking:* round 1's key, with an effective deadline: a charger running and within
        `min_pause` of `switched_at` ranks first in its tier (minimum on-time); else a
        `selected` charger's deadline is taken `hysteresis` earlier, ties to the selected
        one (doc scenario 6: selected 08:45 beats newcomer 08:30; a newcomer at 08:14 wins).
      - Smart-off chargers follow round 1 unchanged.
   7. **Round 1's shares** run as before on these claims; call each result the *share*.
   8. **Lowering, after the shares**, per controllable charger, in this order (each step
      only lowers or holds; `present` as round 1's `_present`; `held(present)` for an
      adjustable charger = `floor(present)` when that is at least its minimum, else 0; for
      an on/off charger = present (0 or its assumed current)):
      1. guard level `EMERGENCY` → target 0, final code `EMERGENCY`;
      2. `conservative`, or `not guard.increases_allowed` → target = `min(share,
         held(present))`, param `held_by` = `"memory_reset"` or `"phase_guard"`;
      3. pacing: a target above `present` while `now − last_action_at <
         min_action_interval` → target = `held(present)`, param `held_by = "pacing"`.
   **The final target is the charger's `ChargerDecision.allocation`.** `held_by` is set
      only when a step actually lowered the share; when it is set, `capped_by` is omitted.
      A regulated cap that limits the share reports `capped_by = "regulation"` (and
      `regulated_cap`), never `"user_limit"`.
   9. **Actions** from the final targets as round 1, except an action whose target equals
      memory `last_target` while `now − last_action_at < min_action_interval` is not
      emitted (in flight). Down actions are never held back by pacing.
   10. **Status:** `EMERGENCY` first, then round 1's order. **Events:** round 1's, plus
       `MEMORY_RESET` and `PHASE_GUARD_INTERVENED` (when `guard.intervened`; params
       `level`). **Reasons:** a `regulated_cap` param when a cap applied; `held_by` per
       8.2–8.3.
   11. **New memory** (`saved_at = now`; level, `green_since`, `emergency_at` from
       `guard`; one `ChargerMemory` per charger in snapshot order). **Every field not set
       here is carried from the charger's previous entry.** `running` = final target > 0;
       "changed" compares that with `present > 0` from this snapshot's state (not with
       the memory default), and when it changed, `switched_at = now` and `stopped_for_capacity`
       = it stopped while it could run and its final code is `WAITING_FOR_CAPACITY`;
       `selected` = running and in the active tier; regulation fields from 4;
       `last_target`/`last_action_at` set when an action was emitted for it, and
       `decreased_at = now` when that action was a down action; a charger with no car
       keeps only `charger_id` and `car_connected=False`.
   12. **Next check:** the earliest of these that is after `now`, in the settings zone,
       or None: the next quarter boundary when any smart session has a plan;
       `green_since + green_hold` while increases are not yet allowed; `green_since +
       emergency_calm` while the emergency latch holds; each `switched_at + min_pause`
       that held a charger off or ranked it first; each `under_use_since +
       under_use_period`, `full_use_since + ramp_period` (with a cap) and
       `under_use_stop_at + resume_probe`; `last_action_at + min_action_interval` for every charger
       whose window is still open (pacing, in-flight suppression and settling all end
       there); each session's deadline while it is in the future (a ready time off the
       quarter grid changes priority at that minute); `prices.updated_at + max_price_age`
       while prices are fresh.
   Showcase: the round-1 showcase, then two follow-up calls feeding the memory back.
7. **`codec.py`.** Per the Public API notes; `timedelta` settings as seconds, enums by
   value. `memory_from_dict` catches every `ConfigError`, `TypeError`, `ValueError` and
   `OverflowError` and returns None.
8. **Round-1 test helpers** (do this in step 3, so the tree is green at step 4): give each
   round-1 test file's snapshot builder a warm memory per Risk 1, and update the exact-shape
   tests per Risk 2. Change no expected value.
9. **`__init__.py`** re-exports `Reduction`, `GuardLevel`, `ChargerMemory`, `Memory`,
   `memory_to_dict`, `memory_from_dict`.
10. **STRUCTURE.md.** New sections for `guard.py`, `regulation.py`, their test files; the
   changed rows.

### Test intents

| # | Must prove | Covers |
|---|---|---|
| T1 | Memory and the new settings validate (aware datetimes, unique ids, ranges, emergency above threshold) with `ConfigError` naming the field | A1, A5 |
| T2 | `memory_to_dict` / `memory_from_dict` round-trip through JSON, and inside snapshot and decision dicts; `memory_from_dict` returns None — never raises — for garbage, wrong types, huge numbers, naive datetimes | A1 |
| T3 | Conservative start: no memory, invalid memory, out-of-date memory, future-dated memory and a valid memory with one timestamp after its `saved_at` each give no increase or resume that call and a `MEMORY_RESET` event with the right reason; decreases still go; unknown charger entries are ignored; the next call with that decision's memory behaves normally | A1 |
| T4 | Minimum pause: a charger stopped for capacity is held off (`MINIMUM_PAUSE`) until `min_pause` has passed, then can start; charge-now ignores it; it never lets a held-off charger's share exceed the limits | A2 |
| T5 | Hysteresis and minimum on-time: doc scenario 6 (selected 08:45 keeps its place against 08:30), a contender more than 30 min earlier wins, a just-started charger is not dropped for an equal-tier one, and none of it overrides budget, phase guard or fallback | A2 |
| T6 | `regulate`: doc scenarios 8–11, the halving method, a steady draw after a cut neither ramped nor cut again over several calls (both methods), a held setting never letting the cap climb, under-use not yet a whole period, a full-use call resetting the under-use timer, ramp only with a cap, cap cleared at max, on/off and missing power never regulated, reset clears | A3 |
| T7 | Through `decide`: a cut frees current to another charger in the same call; a cap below the minimum gives 0 with `UNDER_USE`; the cap shows in params | A3 |
| T8 | Resume probe: doc scenario 16 (60 min), re-plug and a session change each clear the cut early | A4 |
| T9 | `instant_level` boundaries (exactly at threshold − margin, at threshold, at emergency; `None` phases; emergency unset) and `advance_guard` over call sequences: green hold, yellow/red blocking increases, emergency latch and calm release, stale data | A5 |
| T10 | Through `decide`: red cuts to fit and raises `PHASE_GUARD_INTERVENED`; emergency pauses every controllable charger including charge-now and smart-off, status `EMERGENCY`, restarts only after the calm period; `guard_level` reported — None when stale unless an emergency is latched | A5 |
| T11 | Pacing: an increase within `min_action_interval` is held; a decrease always goes; an identical command still in flight is not re-sent; a settling charger's measured draw is used so a second decrease is not compounded (`phase_capacity` with `settling`) | A6 |
| T12 | `next_check`: each source yields the expected instant, the earliest wins, None when nothing is pending | A7 |
| T13 | Session start from memory gives the deadline when `plugged_in_at` is absent; a re-plug restarts it | A1, A4 |
| T14 | Every round-1 test still passes with the warm-memory helper and unchanged expectations (Risk 1); a differential against round 1's `decide` (commit `348b2b0`) under warm memory is step 6's probe, not a test | A8 |
| T15 | Property (`hypothesis`): random sequences of 2–6 calls feeding each memory forward, with fractional `current_setting` values generated, hold the §12 invariants (phase-guard bound per the amended A8; in-flight-suppressed commands excluded from the action-set equality) plus whole amps, no increase in yellow/red/conservative start, all controllable at 0 in emergency, down before up; deterministic; the 10-charger 48-hour call with a full memory under 100 ms | A8 |

**Coverage check.** Every criterion has Public API: A1 memory types, `Snapshot.memory`,
`Decision.memory`, codec functions, `MEMORY_RESET`; A2 `MINIMUM_PAUSE`, settings, memory
fields; A3 `Reduction`, `regulate`, `UNDER_USE`; A4 `regulate`, memory fields; A5
`GuardLevel`, guard module, `EMERGENCY` members, `Decision.guard_level`; A6 settings,
`phase_capacity(settling)`, memory fields; A7 `Decision.next_check`; A8 `decide`. Every
criterion has a test intent: A1 T1–T3, T13; A2 T4–T5; A3 T6–T7; A4 T8, T13; A5 T1, T9–T10;
A6 T11; A7 T12; A8 T14–T15. Every new public name has a criterion.

### Risks

1. **Round-1 tests meet the conservative start.** Every round-1 `decide` test passes no
   memory, which now means "no increases" — their expected allocations would collapse.
   The build gives each round-1 test file's snapshot helper a *warm* memory (`saved_at =
   now − 1 min`, `green_since = now − 1 h`, no charger entries) so their scenarios run in
   steady state, and changes **no** expected value. A round-1 expectation that still
   changes is a regression against A8: fix the code, and if the code is right because two
   criteria disagree, halt.
2. **Exact-shape tests.** Round-1 tests asserting the exact key set of a snapshot,
   settings or decision dict, the exact members of `ReasonCode`, `EventKind`, `Status`
   (`tests/test_ev_result.py`), or the exact `ev.__all__` set must learn the new names;
   that is a deliberate API addition, not a weakened test.
3. **Interpretations decided here** (follow them, do not halt over them): (a) under stale
   measurements round 1's fallback governs increases (guide 4); (b) an emergency latch
   survives stale data; (c) a "switch for capacity reasons" is a stop whose final code is
   `WAITING_FOR_CAPACITY`, and any start; (d) a session change is a change in the five
   fields of guide 6.2; (e) the held value respects the 0/min rule (guide 6.8); (f)
   planning uses a regulated cap only when it is at or above the minimum; (g)
   `max_memory_age` = 30 min. Anything else undecided by section 1 or this plan is a halt.
4. **Phase-limited installations sit in yellow.** Round 1 fills a phase-limited
   installation up to the threshold, so the reading then sits at or above `threshold −
   yellow_margin` — yellow — and every increase waits for a decrease plus the green hold.
   That is the agreed A5 behaviour, not a defect: tests for A3's "freed current goes to
   others in the same call" (T7) are built in green.
5. **Datetime pitfalls** as in round 1: compare in UTC; memory timestamps may arrive with
   fixed offsets from the codec.
6. **A10 / A8 timing.** The added work is linear in chargers; if the 100 ms check fails,
   profile before changing algorithms; a second failure is a halt.

### Critique

The `plan-critic` read section 1, then this section, the source doc and round 1's code and
tests; verdict "back to step 1 for two one-line user decisions, then accept with changes".

1. *`settling` breaks round 1's A3 bound, so A6 and A8 conflict.* **Put to the user**, who
   chose to count a settling charger at its measured draw for `min_action_interval` after
   a decrease; A8 amended in section 1, and T15's bound follows it.
2. *"Full use" makes every cut full use, so the regulator cycles, and under `HALVE` it
   pauses a charging car.* **Applied:** three zones, `ceil` for the cut (guide 5); a T6
   multi-call intent.
3. *`held(present)` can be fractional.* **Applied:** `floor` for adjustable chargers;
   fractional settings in T15.
4. *The ramp climbs while increases are held.* **Applied:** `min(cap, floor(setting)) +
   step` (guide 5); a T6 intent.
5. *A5's green hold contradicts the stale-data rule.* **Put to the user**, who kept round
   1's fallback rule under stale data; A5 amended in section 1.
6. *Guard-level types inconsistent in the stale case.* **Applied:** `Memory.level` is
   optional; `Decision.guard_level` is the guard's level.
7. *Memory update rules underspecified.* **Applied:** fields carried over, "changed"
   compares with `present`, a reset leaves anti-flapping fields alone (guide 6.2, 6.11).
8. *Allocation after the holds undefined; reasons would misreport.* **Applied:** allocation
   is the final target; `capped_by = "regulation"`; `held_by` only when it lowered the
   share and then no `capped_by`; T15 excludes suppressed commands.
9. *T14's differential cannot be built.* **Applied:** moved to step 6 as a probe; warm
   helpers moved into the guide (8); Risk 2 widened.
10. *Bad timestamps inside valid memory hold a charger off forever.* **Applied:** any
    timestamp after `saved_at` makes the memory `"future"`; a malformed dict is
    `"invalid"`; both in T3.
11. *`next_check` misses sources.* **Applied:** every open action window; each future
    deadline.
12. *Smart-off regulation, `plugged_in_at`, yellow sit.* **Applied:** smart-off never
    regulated; `plugged_in_at` is the session start and a change is a re-plug; the yellow
    behaviour named in Risk 4.

The critic called `max_memory_age` a justified addition. Coverage re-checked after these
changes: every criterion still has a Public API entry and a test intent; the only new
public surface is a type change (`Memory.level` optional) and the `"regulation"` value of
an existing param.

---

## 3. Implementation notes

> Written in step 3. Only deviations from the plan above, each with its reason. "Built as
> planned" is a complete and good entry. On a fix round, also the reproduction test's red
> run, pasted here before the fix was written — step 6 cites it.

Built as planned: the Public API table is unchanged. Choices the plan left to the
implementer, and small additions, each recorded here:

- **Invalid-memory marker (guide 6.1).** `model.py` holds a private module constant
  `_INVALID_MEMORY` (a `Memory` dated year 1). `snapshot_from_dict` puts it in
  `Snapshot.memory` when the dict's `"memory"` cannot be decoded; `decide` recognises it
  by identity and reports `MEMORY_RESET` with reason `"invalid"`.
- **"Future" timestamps** are checked only on charger entries whose id is in the snapshot
  (plus the top-level `green_since` and `emergency_at`), so an entry for an unknown
  charger is ignored entirely, as guide 6.1 says, even when its timestamps are bad.
- **`regulate` while stopped.** A charger whose `regulated_cap` is 0 (and whose probe has
  not yet passed) is not re-evaluated even if it is not yet paused: both timers are
  cleared and the cap is kept, so a pause still on its way cannot restart the cycle.
- **A charger stopped for under-use is not planned.** An item with `regulated_cap == 0`
  is left out of `plan_sessions`, so it reserves no planning capacity for others while it
  waits for the probe; its `priority` is None and its reason code `UNDER_USE`.
- **Emergency scope.** The emergency step of the lowering pass applies to every acting
  charger (smart, smart-off, and idle chargers that act), setting the reason code
  `EMERGENCY` only when it changed something (share or present setting above 0).
- **`stopped_for_capacity`** is also set when the final code is `MINIMUM_PAUSE` (a held-off
  charger the caller started by hand and that is stopped again).
- **Memory `session_start`** stores the call that first saw the car, not `plugged_in_at`;
  the deadline still uses `plugged_in_at` when the caller gives it.
- **Codec.** `snapshot_to_dict`/`decision_to_dict` always write `"memory"` (and for a
  decision `"guard_level"`, `"next_check"`); on decoding these keys are optional.
  `Settings` decodes the new `reduction` by value and `emergency_threshold` as nullable.
- **Round-1 test changes (guide 8, Risk 2).** `tests/test_ev_decide.py`: a `warm(now)`
  helper (saved a minute ago, green for an hour, no charger entries) passed by `snap`; the
  exact-`Decision` equality in `test_an_empty_installation_decides_nothing` now compares
  the round-1 fields and checks the memory separately. `tests/test_ev_result.py`: the exact
  `ReasonCode`, `Status` and `EventKind` member sets gained the new names. No expected value
  changed; the full suite is green (1142 passed; the plan's 1141 was round 1's count).

### Changed at step 5 (bugs the tests found; no public signature changed)

Four test-designer reports (two briefs on guard/regulation/model/allocation/codec, two on
`decide`) converged on the items below; each was fixed in the code and pinned by a test.
Where the fix differs from the implementation guide, the guide's text is superseded:

- **Ramp (guide 5).** `min(cap, floor(setting)) + step` could lower a cap or leave one
  between 0 and the minimum with no probe; it is now `max(cap, min(cap, floor(setting)) +
  step)`. A cut only ever lowers: it is skipped when the new value is not below
  `min(max_current, existing cap, ceil(setting))`. A cap in `(0, min_current)` is read as a
  stop, and a cap of 0 with no `under_use_stop_at` is stopped from now (probe after the
  resume probe time).
- **Commanded value (guide 6.8, 6.9, 6.11).** A command sent within
  `min_action_interval` counts as where the charger is (`_commanded`): lowering, pacing,
  action choice and the `running`/`switched_at` update compare against it, so an emergency,
  a smaller share or a pacing hold also countermands an increase or resume still in
  flight, and a pacing hold no longer marks an in-flight resume as not running.
- **Unplug (guide 6.2 over 6.11).** A no-car entry keeps `switched_at` and
  `stopped_for_capacity` (the latter only if it was not running), so a re-plug does not skip
  the minimum pause.
- **Smart-off / unregulated (guide 6.4 with 6.11).** Entries of chargers that were not
  regulated this call clear `under_use_since` and `full_use_since`.
- **Tier-2 hysteresis (new `_keep_places`, after planning).** *Superseded in the re-run
  below:* `_keep_places` is gone; the retention decision now goes into the planner.
- **Next check (guide 6.12).** Never later than the new memory's expiry when anything is
  pending (None stays None); all additions in UTC (`_shifted`); the `until` parameter too.
- **Never an error.** A memory time no UTC date can hold (known entries and the top
  level) is an `"invalid"` restart; unknown entries are ignored before any arithmetic;
  durations and hysteresis so large they overflow a date are skipped (`_shifted`,
  `_earlier`); `_expected_finish` returns None for a trickle of current; a
  `regulated_cap` too large for a float is rejected by `ChargerMemory`.
- **Invalid-memory marker** is recognised by equality, so it survives a snapshot round trip.
- **Session key** normalises `plugged_in_at` to UTC.
- **Settling** counts a charger's measured draw only when it has a car (`allocation`).
- **Guard** ignores a `green_since` carried by a yellow or red memory.

### Changed in step 3, re-run after step 6 (R-A2 and R-A4)

Step 6 sent the round back for the planned-tier hysteresis (`_keep_places`, added at step
5): R-A2, the minimum on-time and the minimum pause do not hold in the planned tier, and
R-A4, a restored charger's stand-alone plan overbooks a phase (a regression of round-1 A4).
Both reproductions are written as tests in `tests/test_ev_decide.py` (section "Send-back
after step 6") and were run against the code as step 6 left it (commit `21c5acd`) before
anything was fixed. Red run:

```
$ pytest tests/test_ev_decide.py -k "started_planned or in_the_middle_of_a_newcomers or displaced_for_capacity or never_book" -q --tb=short
collected 298 items / 294 deselected / 4 selected
tests/test_ev_decide.py FFFF                                             [100%]
_ test_a_started_planned_charger_is_not_dropped_for_a_more_urgent_one_within_the_pause _
E   AssertionError: assert {'a': 0.0, 'b': 6.0} == {'a': 6.0, 'b': 0.0}          (22:05: a demoted after 5 min)
_ test_a_planned_charger_that_stops_in_the_middle_of_a_newcomers_stay_is_not_restarted _
E   AssertionError: assert (Action(charg...e.UP: 'up'>),) == ()                   (22:15: a restarts 10 min after it stopped)
_ test_a_planned_charger_displaced_for_capacity_cannot_restart_before_the_minimum_pause _
E   AssertionError: assert [] == [('a', <Actio...ge.UP: 'up'>)]                   (22:15: a was already dropped at 22:05)
_______ test_planned_charger_plans_never_book_more_than_the_phase_holds ________
E   AssertionError: assert 12.0 <= 6.0                                            (18 quarters booked at 12 A on a 6 A phase)
4 failed, 294 deselected in 0.61s
```

Fix (one source of truth; no public signature of `decide` changed). `_keep_places` and
its patching of `item.plan` are removed. In their place:

- **`planning.plan_sessions(..., first: Collection[str] = frozenset())`** (the one public
  signature change; recorded in section 2's Public API and in `STRUCTURE.md`). Sessions
  named in `first` claim capacity before all the others, each group still most urgent
  first, on the same per-quarter remaining capacity. Calling `plan_sessions` twice with
  "the remaining capacity" was not possible, because the remaining capacity differs per
  quarter and the function takes one capacity constant over the horizon.
- *(Superseded at the step 5 re-run below: `_keepers` is now `_holders`, and the keepers no longer claim every quarter.)* **`decide._keepers`** names the planned sessions that hold their place: a charger that
  can run and is `protected` (running, started less than `min_pause` ago) always; a
  `was_selected` one unless a contender that can run, shares a phase with it and has a
  deadline earlier than its own minus `hysteresis`. Their ids go to `plan_sessions` as
  `first`, so the plan is one plan: the keepers are planned on the full planning capacity
  and everyone else on what is left, the plans together never book more than the phase
  holds (R-A4), and a started charger keeps the current quarter against a newcomer within
  the minimum on-time (R-A2). A keeper whose own cheapest quarters do not include this one
  is still planned for later (price-driven), as before.
- **`decide._mark_displaced`** sets `_Item.displaced` on a running charger (commanded
  above 0) that the plan put at priority 4 although, planned alone on the same planning
  capacity, it would have had this quarter: the quarter was lost to another session, not to
  price. `_new_memory` then records the stop as `stopped_for_capacity` (alongside the
  `WAITING_FOR_CAPACITY` and `MINIMUM_PAUSE` final codes), so the minimum pause holds the
  charger off when it would restart (the planner then puts it at priority 2 and the
  existing hold-off gives `MINIMUM_PAUSE`). A price-driven stop is not a capacity stop
  (pinned by `test_a_selected_charger_stopped_because_later_is_cheaper_is_not_a_capacity_stop`).
  The reason code of a displaced charger stays `PLANNED_LATER`, which is true: it is
  planned for later quarters.
- **Choice:** keepers claim *all* their quarters first, not only the current one, so a
  keeper's plan is the one it was already executing and does not churn. The price is that
  a less urgent keeper can take cheap quarters a more urgent newcomer would have needed;
  the newcomer's plan then says unreachable if it cannot be met, honestly, instead of
  overbooking.

Round-1 and round-2 test expectations: none of round 1 changed. One round-2 test line
changed because it encoded the defect: `test_the_hysteresis_holds_in_the_planned_tier_too`
(both parametrizations) asserted that the loser `b` is `WAITING_FOR_CAPACITY`. That was
`b` being a second priority-2 session in a quarter `_keep_places` had already given to `a`
(the double booking itself); with one plan, `b` is priority 4, `PLANNED_LATER`, and the test
now says so. Its allocations (`a` 10 A, `b` 0 A), `a`'s priority and code and the absence of
actions are unchanged. Tests added: four reproductions (R-A2 x3, R-A4 x1), one price-driven
stop, one `plan_sessions(first=...)` test.

Green run: `ruff check .` clean, `ruff format --check .` clean, `mypy` clean (28 source
files), `pytest` 1643 passed (1637 + 4 reproductions + 1 price-driven stop + 1 planning
test; the two edited parametrizations are among the 1637). The four reproductions that
were red above are green. A scratch fuzz of 1 500 random multi-call sequences on 2-3
chargers over shared phases checks that no decision's plans book more than the budget per
phase in any quarter: 49 sequences fail on `21c5acd`, 0 on the fix.

### Changed at step 5, re-run after step 6 (reader reports on `5d96a5d`)

Two test-designer reports (`input-space`, `contract`) on `5d96a5d` converged on two
defects of the step 3 re-run and three smaller ones. All are fixed in the code and pinned
by tests; none touches section 1, because A2 and round-1 A4 both hold as written (below).

- **(a) Keepers claimed every quarter, starving a more urgent newcomer (A4 against A2).**
  Round-1 A4: "the most urgent session (least slack) claims shared capacity first".
  A2 protects a charger's *place*, which is the quarter being decided now: not dropped
  within `min_pause`, selection kept within the hysteresis. It asks nothing about later
  quarters, and every call re-decides the place anyway. So a keeper now claims only the
  current quarter; everything else, its own later quarters included, goes by least slack.
  `plan_sessions` does it in a pre-pass (`_holds`): each session in `first`, in order, is
  planned alone on a copy of the capacity less what the earlier ones hold of the current
  quarter; if that plan has the current quarter, the session *holds* the current it has
  there. The holds are then taken off the capacity of that quarter, and every session is
  planned in the round-1 slack order, a holder planned with its own hold given back and
  the quarter forced into its plan (`_select(..., forced)`, inside the cheapest
  minimum-length block that contains it when one is free). The plans together still never
  book more than `capacity` (R-A4), and a more urgent newcomer plans on everything but the
  holders' current quarter, so it is no longer reported unreachable for quarters the keeper
  never needed. The cost is a projection that can say a later quarter is the newcomer's
  while the keeper, still protected or selected at that call, holds that one too; every
  call re-decides, and the allocation, which is all that is executed, follows the rules.
- **(b) Hysteresis was an existence test; the ranked tier is pairwise.** `_keepers` voided
  a selected charger's retention when *any* phase-sharing contender was more than the
  hysteresis earlier, after which a contender *inside* the hysteresis could take the place
  on slack. A2 says the place goes unless "a contender's ready time is more than the
  hysteresis earlier", and the ranked tiers read it as the contender that takes the place.
  `decide._holders` (replaces `_keepers`) now returns the contest in the ranked tier's
  order (`_tier_key`: protected first, then deadline with a selected charger's taken the
  hysteresis earlier): every protected or selected charger, plus any session whose deadline
  is more than the hysteresis earlier than that of a selected charger it shares a phase
  with, since only such a session can take that charger's place. A challenger holds the
  quarter only if its own plan wants it; a session that does not (its cheap hours are
  later) leaves the place to the selected charger, and a session inside the hysteresis is
  not in the contest at all, so what the keeper leaves goes by slack.
- **(c) A held-off charger booked the current quarter.** In the ranked tiers a charger
  held off by the minimum pause is left out of the claims; in the planned tier the planner
  still booked the quarter for it, idling the capacity and possibly marking another
  charger `displaced`. `_held_off` is now computed before planning for every planned
  session and passed on as `PlanRequest.skip_current`, so the plan starts with the next
  quarter and books nothing now. It still occupies its tier when it would have had the
  quarter (`_plans_now`: planned alone on the same capacity, unchanged from
  `_mark_displaced`), so the reason stays `MINIMUM_PAUSE` and tier 3 does not run over it;
  `_mark_displaced` skips held-off chargers; `starved` excludes them.
- **(d) `first` as a bare string** substring-matched (`"b" in "ab"`). `first` is now
  `Sequence[str]` (the order is the contest order) and a bare `str` raises `TypeError`
  naming the value. `decide` passes a tuple.
- **(e) Docstrings and rows.** `plan_sessions`, `_holders` and the section 2 / `STRUCTURE.md`
  rows now say what the code does (a keeper keeps the quarter being decided, not "the full
  capacity"; the comparison is of deadlines, the next occurrence of the ready time). A
  smart-charging-off squeeze is not another session's claim on the current quarter:
  `plan_cap` already has it out, so a running charger squeezed to fewer quarters is a
  capacity stop only when it is starved of all of them (`WAITING_FOR_CAPACITY`) — recorded in
  `_mark_displaced`'s docstring.

**A2 and round-1 A4 can both hold**, and do: A2 is about who has the current quarter at each
call; A4 about who claims the shared capacity of the plan. The two split on exactly one
quarter, the current one, and the fix gives that quarter to the A2 order and the rest to
A4's. No halt.

Public signature changes (section 2 and `STRUCTURE.md` updated): `plan_sessions(..., first:
Sequence[str] = ())` (was `Collection[str] = frozenset()`), `PlanRequest(..., skip_current:
bool = False)`. `decide`'s signature is unchanged.

Test changes: **one** existing test encoded defect (a) and was replaced, with its name and
reason recorded in its comment: `test_plan_a_session_named_first_claims_before_the_more_urgent_one`
(pinned "a session named first claims every one of its quarters before the more urgent one")
became `test_plan_a_session_named_first_keeps_only_the_current_quarter`. The planning test
helper `plan(..., first=)` takes a sequence instead of a frozenset (the new type); two call
arguments changed from `frozenset(...)` to a tuple. `tier_two_pair` gained optional settings
changes. No decide test expectation changed: every round-1 test and every earlier round-2
test passes as it was written.

---

## 4. Verification log

> Written in step 4: the static half. Command output, not a summary of it.

| Check | Result |
|---|---|
| `ruff check .` | `All checks passed!` |
| `ruff format --check .` | `55 files already formatted` |
| `mypy` | `Success: no issues found in 26 source files` |
| `pytest` | `1142 passed in 10.31s` |
| Plan completeness | every signature in the Public API table exists as written (table below) |
| `STRUCTURE.md` | in sync (auditor findings below) |
| `python -m energy_optimization.ev.<module>` | all nine modules exit 0 with readable output (below) |

### Plan completeness

Checked by introspection (`dataclasses.fields`, `inspect.signature`) against the Public API
table of section 2, plus round 1's table for the names round 2 did not touch.

| Name | Result |
|---|---|
| `Reduction`, `GuardLevel` (members and lower-case values) | matches |
| `ChargerMemory` (15 fields, order and defaults) | matches |
| `Memory` (5 fields) and `Memory.charger(self, charger_id: str) -> ChargerMemory \| None` | matches |
| `Settings` (15 new fields, order and defaults) | matches |
| `Snapshot.memory: Memory \| None = None` (last field) | matches |
| `ReasonCode` (`MINIMUM_PAUSE`, `UNDER_USE`, `EMERGENCY`), `EventKind` (`MEMORY_RESET`, `PHASE_GUARD_INTERVENED`) | matches |
| `Status.EMERGENCY` first in precedence | matches |
| `Decision.memory`, `.guard_level`, `.next_check` (after round 1's fields, default None) | matches |
| `guard.instant_level`, `guard.GuardState`, `guard.advance_guard` | matches |
| `regulation.Regulation`, `regulation.regulate` | matches |
| `allocation.phase_capacity(snapshot, fresh, settling: frozenset[str] = frozenset())` | matches |
| `codec.memory_to_dict`, `codec.memory_from_dict` (never raises) | matches |
| `decide(snapshot: Snapshot) -> Decision` | unchanged, as planned |
| `ev.__all__` gains `Reduction`, `GuardLevel`, `ChargerMemory`, `Memory`, `memory_to_dict`, `memory_from_dict` | matches |

No Missing, no Deviation (section 3 records only implementer choices and the `_INVALID_MEMORY`
private marker), no Unplanned public surface. Round 1's table is unchanged for everything
else.

### `structure-auditor` findings (12 edits) and what was done

Step 3 had already applied most of them; each was checked against `STRUCTURE.md` as it
stood.

| # | Finding | Result |
|---|---|---|
| 1 | `Settings` row missing 15 fields | already present |
| 2 | `Snapshot` row missing `memory` | already present |
| 3 | `Decision` row missing three fields | already present |
| 4 | `phase_capacity` row missing `settling` | already present |
| 5 | `ReasonCode`, `Status`, `EventKind` members | already present |
| 6 | `model.py`: `Reduction`, `GuardLevel`, `ChargerMemory`, `Memory` rows | rows already present; applied now: intro sentence naming `Reduction` and `GuardLevel` as `StrEnum`s, and the `main` row (snapshot carrying a memory) |
| 7 | `codec.py`: memory functions, intro, `snapshot_from_dict`/`decision_from_dict`/`main` rows | functions already present; applied now: intro ("Snapshot, decision and memory"), `snapshot_from_dict` (malformed memory becomes the invalid-memory marker), `decision_from_dict` (including bad `memory`), `main` (malformed memory decoding to None) |
| 8 | Dependency-order paragraph | already present |
| 9 | `guard.py` and `regulation.py` sections | already present |
| 10 | Subpackage description | already present |
| 11 | `ev/__init__.py` description | already present |
| 12 | `decide.py` intro and `main` row | already present |

The test sections for `test_ev_guard.py` and `test_ev_regulation.py` are step 5's, as the
brief said.

### Changed here to get green

Nothing failed. One showcase-form fix: `regulation.main` passed the literal `False` as the
`reset` argument inline in four calls; it is now a named `reset = False` with a comment.

### Showcase output

`python -m energy_optimization.ev.<module>` for `model`, `result`, `quarters`, `planning`,
`allocation`, `guard`, `regulation`, `decide`, `codec`: exit 0 each (plus the expected
`RuntimeWarning`). Highlights:

```
guard:      +0 s 33.0 A -> red, increases allowed: False ... +90 s green, increases allowed: True; stale: level None, increases allowed: True
regulation: cap 8 A / cap 0 A (0 means stopped) / cap None / cap 12 A   (guide 5's four doc scenarios)
decide:     call 1 "held by memory reset"; after 0:05:00 the garage gets 16.0 A; next check 2026-10-21 22:05:30+02:00; stale call falls back
codec:      snapshot and memory round trips equal: True; malformed memory decodes to: None; rejected: chargers[0].max_current: must be a number, got 'sixteen'
```

### Re-run after the step 3 re-run (commits `8955853`, `5d96a5d`)

Step 6 sent the round back (R-A2, R-A4); step 3 was re-run, so step 4 ran again on the
tree at `5d96a5d`. The first record above is kept; this entry adds to it.

| Check | Result |
|---|---|
| `ruff check .` | `All checks passed!` |
| `ruff format --check .` | `57 files already formatted` |
| `mypy` | `Success: no issues found in 28 source files` |
| `pytest` | `1643 passed in 16.07s` (1637 + 4 reproductions + 1 price-driven stop + 1 `first` planning test) |
| Plan completeness | matches; the one changed row is below |
| `STRUCTURE.md` | in sync (auditor report below) |
| `python -m energy_optimization.ev.<module>` | all nine modules (`model`, `result`, `quarters`, `planning`, `allocation`, `guard`, `regulation`, `decide`, `codec`) exit 0 |

**Plan completeness, the changed row.** `plan_sessions` was checked with
`inspect.signature` against the Public API table in section 2 (row added at the step 3
re-run) and against `STRUCTURE.md`; all three are character-for-character the same
parameter list: `plan_sessions(requests: Sequence[PlanRequest], now: datetime, prices:
Mapping[datetime, float], capacity: Mapping[Phase, float], voltage: float,
min_block_quarters: int, price_cap: float | None, timezone: str, first: Collection[str] =
frozenset()) -> dict[str, SessionPlan]`. The module-level public names of `decide`
(`decide`, `main`), `planning`, `guard`, `regulation`, `allocation` and `codec` were listed
from the code: no name is outside the Public API tables, so no Unplanned surface; no
Missing; no Deviation (the re-run's signature change is recorded in section 3 and section
2). `_keepers`, `_mark_displaced` and `_Item.displaced` are private.

**`structure-auditor` (run by the orchestrator on the code as the re-run left it).** No
edits needed: the `plan_sessions` row matches the real signature including `first:
Collection[str] = frozenset()`; `decide.py`'s public names are still `decide` and `main`;
the module purpose covers the keepers / displaced behaviour; no private names are listed;
the `test_ev_planning.py` and `test_ev_decide.py` entries already describe the new tests.
Checked by hand against `git diff 21c5acd HEAD -- STRUCTURE.md` (the re-run's own edits to
the `plan_sessions` row, the `decide.py` intro and the two test entries): consistent with
the code.

**Changed here to get green.** Nothing failed; no source file was changed in this step.

**Showcases.** All nine run with exit 0. `planning` still prints two cars on a shared phase
and a default need; `decide` prints the conservative first call (`garage ... held by memory
reset`), the follow-up five minutes later (`garage: 16.0 A`, next check `22:05:30+02:00`)
and the stale call. `plan_sessions`'s new `first` parameter is not exercised in the
`planning` showcase (it keeps its default); its use is shown by the tests.

---

## 5. Test log

> Written in step 5: the dynamic half.

Final run: `ruff check .`, `ruff format --check .` and `mypy` clean; `pytest`: **1637
passed** (1142 before the round-2 tests). Every module still runs under `python -m`.
No round-1 test was weakened (the only edits to existing test lines are three stronger
showcase assertions and one import).

| Intent | Test names (files) | Result |
|---|---|---|
| T1 | `test_settings_*`, `test_charger_memory_*`, `test_memory_*`, `test_snapshot_memory_*`, `test_guard_level_and_reduction_have_exact_lower_case_values` (`test_ev_model.py`) | pass |
| T2 | `test_memory_round_trips_through_json`, `test_memory_from_dict_*` (non-dict, 29 malformed shapes, deep nesting), `test_snapshot_from_dict_*memory*`, `test_the_invalid_memory_marker_survives_a_snapshot_round_trip`, `test_decision_round_trips_*`, `test_decision_from_dict_reads_the_round_two_fields_strictly`, `test_settings_dict_*` (`test_ev_codec.py`) | pass |
| T3 | `test_a_call_without_memory_increases_nothing_but_still_decreases`, `test_the_next_call_with_that_memory_starts_the_charger`, `test_memory_usability_boundaries` (10 rows), `test_memory_written_with_another_offset_*`, `test_a_time_no_utc_date_can_hold_*` (8), `test_an_extreme_time_on_an_unknown_charger_is_ignored`, `test_a_held_charger_is_floored_*`, `test_a_huge_*_never_overflows` (`test_ev_decide.py`) | pass |
| T4 | `test_the_minimum_pause_*`, `test_a_capacity_stop_can_restart_*`, `test_charge_now_skips_the_minimum_pause`, `test_charge_now_skips_the_pause_but_not_the_pacing`, `test_an_observed_unplug_and_replug_*`, `test_a_held_off_charger_still_occupies_its_tier` | pass |
| T5 | `test_a_selected_charger_keeps_its_place_*`, `test_a_contender_more_than_the_hysteresis_earlier_*` (tier ranking and planned tier), `test_a_just_started_charger_is_not_dropped_*`, `test_an_in_flight_resume_still_counts_as_running_*`, `test_the_hysteresis_holds_in_the_planned_tier_too`, `test_a_huge_hysteresis_*` | pass |
| T6 | `test_ev_regulation.py` (55 tests: scenarios, boundaries, both methods, steady draw over calls, held setting, cut never raises, bad memory, reset, kinds, UTC) | pass |
| T7 | `test_an_under_use_cut_frees_current_*`, `test_a_cut_below_the_minimum_stops_*`, `test_a_cut_charger_alone_*`, `test_a_steady_draw_after_a_cut_*`, `test_whole_ramp_periods_*`, `test_chargers_that_cannot_be_regulated_are_never_cut` (3), `test_a_smart_charging_off_period_forgets_*` | pass |
| T8 | `test_a_cut_charger_is_offered_current_again_after_the_probe_time`, `test_a_session_change_clears_an_under_use_stop_early` (4), `test_a_replug_clears_*`, `test_the_same_plug_in_time_written_with_another_offset_*` | pass |
| T9 | `test_ev_guard.py` (45 tests) | pass |
| T10 | `test_red_cuts_to_fit_*`, `test_yellow_allows_no_increase_*`, `test_just_below_yellow_*`, `test_increases_wait_for_the_green_hold_*`, `test_stale_measurements_*`, `test_a_stale_call_in_the_middle_*`, `test_an_emergency_*` (pauses all, calm, yellow restart, stale, countermands a resume), `test_without_an_emergency_threshold_*`, `test_a_lost_memory_forgets_the_emergency_latch_*` | pass |
| T11 | `test_an_increase_inside_the_action_interval_*`, `test_a_command_still_in_flight_is_not_sent_again`, `test_a_decrease_in_flight_is_not_raised_*`, `test_an_increase_in_flight_is_cut_back_*`, `test_a_second_decrease_is_not_compounded_*`, `test_a_decrease_that_never_landed_*`, `test_once_the_window_closes_*`, `test_phase_capacity_settling_*` (14, `test_ev_allocation.py`) | pass |
| T12 | `test_next_check_*` (14: none, hold, price, clamp, quarter, deadline, under-use, action window, probe, ramp, earliest, expiry round trip, fall-back night, UTC addition) | pass |
| T13 | `test_the_deadline_is_seen_from_the_session_start_*`, `test_without_memory_the_deadline_*`, `test_a_replug_restarts_the_deadline`, `test_a_given_plugged_in_time_*` | pass |
| T14 | the 1142 round-1 tests, unchanged expectations, under the warm helper | pass |
| T15 | `test_the_promises_hold_across_sequences_of_calls_feeding_memory_forward` (150 sequences, arbitrary installations), `test_the_promises_hold_across_plain_sequences_where_every_timer_can_fire` (120 sequences), `test_ten_chargers_and_a_48_hour_series_decide_in_under_100_ms_with_a_full_memory`. The oracle recomputes freshness, the amended phase-guard bound, the command in flight, action existence/stage/order, increases blocked (restart, yellow/red/emergency, green hold, pacing), minimum pause, emergency, legal currents, memory round trip and next check without the module's helpers. Run offline at 4 000 + 2 000 sequences: no failure. | pass |

Mutation check: 17 single-line reversions of the fixes below (and of guide behaviour) were
each run against the suite; every one is caught by a named test.

### Reader reports: every case and contradiction, and its disposition

*Applied* = fixed in the code and pinned by a test. *Pinned* = behaviour kept as built,
asserted by a test. *Rebutted* = the expectation was wrong.

| # | Finding (reader) | Disposition |
|---|---|---|
| 1 | `OverflowError` from extreme memory timestamps in `_usable_memory` (A in, A con, B con) | **Applied**: `"invalid"` restart; unknown entries ignored before arithmetic |
| 2 | `OverflowError` from huge durations in `_next_check` / `until` / `hysteresis` (A in) | **Applied** (`_shifted`, `_earlier`); incl. round-1 `max_price_age` |
| 3 | Ramp lowers the cap / leaves 0 < cap < min with no probe (all four) | **Applied** (`max(cap, …)`, such a cap read as a stop) |
| 4 | Cut raises the cap above setting/max (A in, A con) | **Applied** (cut only if below `min(max, cap, ceil(setting))`) |
| 5 | `regulated_cap=0` without `under_use_stop_at` never probed (A in, A con) | **Applied** (stopped from now) |
| 6 | Unplug wipes anti-flapping fields; 6.2 vs 6.11 (B in, B con) | **Applied** in favour of 6.2 and A2: no-car entry keeps `switched_at`, `stopped_for_capacity`. Not a halt: A2 is worded per charger and says re-plug is no reason |
| 7 | `next_check` later than memory expiry (B in, B con) | **Applied** (clamp when anything is pending; `None` stays `None`, T12) |
| 8 | Pacing hold marks an in-flight resume as not running (B in) | **Applied** (`running`/`switched_at` judged against the commanded value) |
| 9 | Smart-off periods carry stale under-use timers (B in) | **Applied** |
| 10 | `session_key` sensitive to the UTC offset (A in, B in, B con) | **Applied** |
| 11 | Settling counts power with no car (A in) | **Applied** |
| 12 | `next_check` / `until` wall-clock arithmetic (A in) | **Applied** (UTC) |
| 13 | Invalid-memory marker lost in a round trip (A in, A con, B con) | **Applied** (equality) |
| (i) | In-flight increase/resume never countermanded (B con 1-2, I14; B con 6 "down" that is an increase) | **Applied, no halt**: an open `last_target` is the present value when lowering and pacing (`_commanded`). Emergency now pauses an in-flight resume, a smaller share cuts an in-flight increase back (a SET_CURRENT DOWN even on a paused-looking charger), and nothing is raised inside the window. This changes no criterion: A5 "pauses every controllable charger" and A6 "no increase within the interval, decreases always go" are met more fully, and A8's budget invariants now also hold for the command in flight (I14 asserted by T15) |
| (ii) | Hysteresis only within the active tier; planner can displace a selected charger in tier 2 (B in 2) | **Applied, no halt**: A2 as written has no tier qualifier ("a selected charger keeps its place unless a contender's ready time is more than the hysteresis earlier"), so it covers tier 2; `_keep_places` implements it |
| 14 | `advance_guard` trusts `green_since` of a yellow/red memory (A con 5, B in 9) | **Applied** for yellow/red (level `None` stays trusted: hand-built and warm memories) |
| 15 | Lost memory drops the emergency latch (B con 7) | **Pinned**: the calm lives in the memory; the restart is conservative and A1 says treat as a restart. Test documents it. Noted for step 8 as a `DEVELOPMENT.md` idea, not a defect against section 1 |
| 16 | Charger preempted by charge-now gets no minimum pause (B in 11) | **Pinned** (guide Risk 3c decided it) |
| 17 | Held-off charger still occupies its tier (B con 10) | **Pinned** by `test_a_held_off_charger_still_occupies_its_tier` |
| 18 | A6 not compounded only with a power reading (B in 10) | **Pinned** (plan: "when known") |
| 19 | `yellow_margin` has no upper bound (A in 6) | **Pinned**: a configuration footgun, not decided by section 1; test documents it |
| 20 | `switched_at` drifts while a command is in flight (B in 5) | **Applied** with #8 |
| 21 | `-20 A` reading, "negative export" (A in) | **Rebutted**: `PhaseCurrents` rejects negative readings (round 1) |
| 22 | `regulated_cap` of 10**400 accepted by `ChargerMemory` (found while testing) | **Applied** (finite-float check) |
| 23 | `_expected_finish` overflow for a trickle of reserved current (found by T15) | **Applied** (returns None); latent in round 1 |
| 24 | Docstring drift (`GuardState.emergency_at`, `phase_capacity`, `memory_from_dict`) | **Applied** |

Edge cases considered and deliberately skipped, with reasons:

- Text/non-ASCII ids in memory: ids are only compared exactly (one `charger("A")` case).
- Purity of `advance_guard` / `regulate`: frozen types, covered by one idempotency test each.
- A `now` within an offset of `datetime.min`/`max` in the snapshot itself: round 1's domain.
- Soft start and one-step red reduction: out of scope in section 1.
- A wall-clock `now` that jumps backwards (negative age): becomes a `"future"` memory; covered by the one-microsecond-in-the-future row.


### Re-run after step 6 (R-A2, R-A4): reader reports on `5d96a5d`

The earlier record above is kept. Step 3 was re-run (`8955853`, `5d96a5d`) and step 4
passed again (`047f434`); this step ran again on that tree, with two new `test-designer`
reports (saved verbatim at `.../scratchpad/designers3/input-space.md` and `contract.md`).
This is the first re-run after step 6: R-A2 and R-A4 are both met at the end of it (the
four reproductions stay green; A2 and round-1 A4 are pinned together by the new tests), so
no halt. Final run: `ruff check .`, `ruff format --check .`, `mypy` clean (28 source files);
`pytest`: **1686 passed** (1643 before this entry: 22 new `decide` test functions and 15
new `planning` ones, one of which replaces an old one; parametrized rows counted).

| Intent | Test names | Result |
|---|---|---|
| T4/T5 (A2, planned tier) | `test_a_planned_pair_does_not_flap_over_an_hour_with_memory_fed_forward`, `test_the_minimum_on_time_and_pause_end_exactly_on_their_boundaries_in_the_planned_tier` (22:14:59 / 22:15, 22:29:59 / 22:30), `test_a_zero_minimum_pause_disables_both_planned_tier_rules`, `test_a_huge_hysteresis_in_the_planned_tier_keeps_the_selected_charger`, `test_the_planned_tier_hysteresis_is_strict_at_tiny_values` (4 rows), `test_two_protected_chargers_on_a_shrinking_budget_lose_exactly_one_as_a_capacity_stop`, `test_a_nearer_contender_never_takes_the_place_of_a_selected_charger`, `test_a_charger_still_waiting_on_an_in_flight_resume_holds_its_place` | pass |
| T5, finding (b): the contender that takes the place | `test_a_contender_that_will_not_take_the_quarter_does_not_free_it_for_a_nearer_one`, `test_the_contender_that_takes_the_place_must_be_the_one_more_than_the_hysteresis_earlier`, `test_a_contender_inside_the_hysteresis_gets_no_priority_for_what_the_keeper_leaves`, `test_a_contender_on_another_phase_neither_moves_the_keeper_nor_waits` | pass |
| T4, finding (c): held-off claims nothing | `test_a_held_off_planned_charger_does_not_idle_the_capacity_it_cannot_use`, `test_a_running_charger_keeps_the_quarter_a_held_off_one_cannot_use`, `test_a_replugged_planned_charger_is_still_held_off_inside_the_pause`, `test_charge_now_skips_the_pause_a_planned_displacement_started`, `test_a_price_driven_stop_beside_a_contender_is_not_a_capacity_stop` | pass |
| round-1 A4 / A8, finding (a) | `test_a_protected_charger_does_not_starve_a_more_urgent_newcomer_of_later_quarters`, `test_plans_never_overbook_any_phase_when_the_protected_charger_is_three_phase` (2 rows), `test_the_planned_tier_counts_smart_charging_off_draw_before_a_keeper_plans`, `test_plans_never_book_beyond_the_budget_across_memory_fed_sequences` (property, 250 sequences of 3-7 calls, memory fed forward), and the plan-fit oracle `assert_plans_fit` now inside `assert_invariants` (round-1 snapshots) and `assert_sequence_invariants` (T15): no plan books more than the budget less the fixed, unavailable and smart-off allocations on any phase in any quarter (keyed in UTC), and `reaches_target` implies the plan's energy covers the need | pass |
| `plan_sessions(first=, skip_current=)` | `test_plan_a_session_named_first_keeps_only_the_current_quarter`, `..._keeps_a_current_quarter_in_a_full_block`, `test_plan_the_sessions_named_first_contest_the_quarter_in_the_order_given` (2 rows), `test_plan_the_order_of_first_beats_urgency_for_the_current_quarter`, `test_plan_a_later_session_named_first_holds_what_the_earlier_ones_left`, `test_plan_a_first_that_holds_nothing_changes_no_plan` (3 rows), `test_plan_a_session_whose_own_plan_skips_the_current_quarter_holds_nothing`, `test_plan_first_names_the_same_session_twice_as_once`, `test_plan_first_given_as_a_bare_string_is_refused_not_read_as_characters`, `test_plan_first_accepts_a_list_and_a_tuple_alike`, `test_plan_first_does_not_mutate_its_arguments`, the three `skip_current` tests, and the property `test_plan_sessions_that_hold_the_current_quarter_never_book_beyond_the_capacity` (150 examples: booking per phase and quarter, `skip_current` never plans the current quarter, result independent of request order) | pass |
| T15 (100 ms) | `test_ten_running_chargers_at_priority_four_still_decide_in_under_100_ms` (about 12 ms: one extra alone plan per running charger the plan put later) | pass |

Offline, beyond the suite: 20 000 round-1 style snapshots through the plan-fit oracle, and
7 500 sequences (2 500 each of `random_snapshot`, `friendly_snapshot`, crowded installations)
through `assert_sequence_invariants`: zero violations. Mutation checks against the new tests
(each reverted): the old step 3 re-run source fails 5 of the new `decide` tests (starvation,
both contender cases, the held-off idle capacity, the running charger); ignoring
`skip_current`, not sorting the contest, no block around the held quarter, not reserving the
hold, no string guard, and treating every earlier session as a challenger are each caught by
a named test.

**Reader reports, every case and contradiction.** *Applied* = fixed in code and pinned.
*Written* = test added, behaviour already right. *Rebutted* = the expectation was wrong.

| # | Finding (reader) | Disposition |
|---|---|---|
| I1 | Planned pair over an hour with memory fed forward | **Written** (`..._does_not_flap_over_an_hour_...`) |
| I2 | Far-earlier contender voids retention, nearer one takes the place | **Applied** (b). Part **rebutted**: with flat prices and a need that wants the quarter, the 07:00 contender legitimately takes `a`'s place (it is more than 30 min earlier); the nearer 08:30 car does not. Both variants are tests |
| I3 | Two keepers on a shrinking budget | **Written**; the winner is the earlier ready time (`b`, 08:30), as in the ranked tiers, not `a` as the report guessed |
| I4 | Order inside the first group | **Applied**: the order is now the caller's contest order, not slack (needed by (b)); written as two tests |
| I5 | Contender on another phase | **Written**; no mutation separates it since another phase never conflicts, the behaviour (keeper stays, the other charges) is pinned |
| I6 | Hysteresis boundary at 0 and 1 minute | **Written** (4 rows) |
| I7 | A keeper claiming every quarter leaves a newcomer unreachable | **Rebutted**: that is finding (a) itself. The opposite is pinned (`..._does_not_starve_...`) |
| I8 | Zero minimum pause in the planned tier | **Written** |
| I9 | `first` that changes no claim | **Written** (unknown id, empty list, a session whose plan skips the quarter, a repeated id) |
| I10 | Huge hysteresis in the planned tier | **Written** |
| I11 | Three-phase keeper, per-phase booking | **Written** (2 rows) |
| I12 | In-flight resume holds its place while the state reads paused | **Written** |
| I13 | Smart-off draw shrinks the keeper's planning capacity | **Written** |
| I14 | `first` as a bare string | **Applied** (d) |
| I-C1 | `_keepers` existence test against A2 | **Applied** (b), not a halt: A2 is satisfied by the pairwise reading, which is also the ranked tier's |
| I-C2 | Keepers claim everything against round-1 A4 | **Applied** (a), not a halt: A2 and A4 split on one quarter and both hold |
| I-C3 | Bare string | **Applied** (d) |
| I-C4 | "ready time" against deadline wording | **Applied** (e) |
| C1 | Keeper starves a more urgent newcomer | **Applied** (a) |
| C2 | Overbooking fuzz as a test | **Written** (property and oracle) |
| C3 | Bystander contender lets a near newcomer displace | **Applied** (b). The report's cars all want the quarter under flat cheap prices, in which case the far-earlier one does take the place; the test uses prices where it does not |
| C4 | Two keepers no longer fit | **Written** (as I3) |
| C5 | Price-driven stop beside a contender | **Written** |
| C6 | Held-off charger idles capacity | **Applied** (c). Caveat found while writing it: with the default minimum block of two quarters, the newcomer cannot use an isolated current quarter when the held-off charger's own plan takes the next one, so the test sets `min_block_quarters=1`; that is the planner's block rule, not the hold |
| C7 | `first` that changes no order | **Written** (as I9) |
| C8 | Hypothesis over the planner | **Written**, without the clause "the first sessions' plans equal their plans alone", which no longer holds by design (only the current quarter is held) |
| C9 | Order inside the first group | **Applied** (as I4) |
| C10 | Other phase | **Written** (as I5) |
| C11 | Pause boundaries 1 s either side | **Written** |
| C12 | Re-plug inside the pause | **Written** |
| C13 | Charge now after a displacement | **Written** |
| C14 | 100 ms with displacement checks | **Written** |
| C15 | Bare string | **Applied** (d) |
| C-C1 | Round-1 A4 against the "claims every quarter" choice | **Applied** (a); the section 3 "Choice" of the step 3 re-run is reversed |
| C-C2 | Hysteresis differs between tiers | **Applied** (b) |
| C-C3 | A held-off charger occupies the plan | **Applied** (c) |
| C-C4 | Docstring and row overstate `first` | **Applied** (e) |
| C-C5 | Smart-off squeeze is not a displacement | **Pinned**: documented in `_mark_displaced`; section 3 |

Edge cases considered and deliberately skipped in this re-run: the `plan_sessions([])`
case (round 1's, and `decide` never plans an empty set); a `first` naming a session with
need 0 or a passed deadline through `decide` (those sessions are in the ranked tiers, never
planned, so never holders; covered at planner level by the "holds nothing" tests); text
ids (only compared for equality); the `_holders` phase filter, whose removal changes no
outcome (a contender on another phase never conflicts), kept as a statement of who can
take a place.

---

## 6. Concept check

> Written in step 6, against section 1 — not against section 2. The question is whether
> the thing built is the thing agreed, not whether it matches the plan.

| # | Criterion | Met | Evidence |
|---|---|---|---|
| A1 | Carries memory, starts conservatively | yes | `Decision.memory` round-trips through `json.dumps`/`memory_from_dict` and inside snapshot and decision dicts (probe: all `True`). No / garbage-dict / 31-minute-old / future-dated memory each give `MEMORY_RESET` with reason `missing` / `invalid` / `out_of_date` / `future`, no increase, decreases still go; a conservative call holds a running 10 A charger at 10 A (`held_by: memory_reset`) and the next call with that memory sends `set_current 16`. A `ghost` entry is dropped from the next memory. Tests: `test_a_call_without_memory_increases_nothing_but_still_decreases`, `test_memory_usability_boundaries`. |
| A2 | Does not flap | **partially: unmet in the planned tier** | Met in the ranked tiers: a charger started at 22:00 and a far-earlier newcomer at 22:05 (tier 3, 5-min steps) keeps its place until 22:15, then is stopped for capacity and shown `minimum_pause` at 22:20; a stopped charger cannot restart inside the pause; charge-now skips it; invariants hold. Doc scenario 6 holds in the planned tier (a ready 08:45 keeps its place against b 08:30, also at exactly 30 min earlier; b at 08:14 wins). **Unmet in the planned tier** (priority 2 contention resolved by the planner): neither the minimum on-time nor the minimum pause applies once the planner has demoted the started charger to priority 4. See *Unmet* below, reproduction R-A2. |
| A3 | Regulates adjustable chargers | yes | Doc scenarios by simulation (5-min calls, executed actions): 16 A set / car takes 7 A -> cap 8 at +15 min (`('a', 'set_current', 8.0)`); 10 A / 2 A -> `under_use`, `pause`, current handed to the other charger in the same call; 16 A / 14.5 A -> no change over 25 min; after a cut, ramp 8 -> 10 -> 12 -> 14 -> 16 one step per 5 min, cap cleared at max; `HALVE` gives 8 A; an on/off charger drawing 1 A and an adjustable charger with no power reading are never regulated. Tests: `tests/test_ev_regulation.py` (55), T7 group in `test_ev_decide.py`. |
| A4 | Probes to resume | yes | Cut to 0 at 22:15, `resume 16.0` at 23:15 (60 min) and not before; unplug at 22:30 and re-plug at 22:35 resumes at 22:35; a changed ready time at 22:30 resumes at 22:30. Tests: `test_a_cut_charger_is_offered_current_again_after_the_probe_time`, `test_a_session_change_clears_an_under_use_stop_early`, `test_a_replug_clears_*`. |
| A5 | Runs phase-guard levels over time | yes (stale level is `None`, see drift) | Probe with charge-now, smart-off and on/off chargers: 33.5 A on L1 -> `red`, cuts to fit, `phase_guard_intervened`; a 30.5 A reading -> `yellow`, nothing raised; 39 A with emergency 38 -> `emergency` pauses the charge-now and smart-off chargers, status `emergency`; the next green calls stay `emergency` for the 5-minute calm (release at +360 s, resume 12 A at +360 s, 16 A at +390 s); a stale call inside the latch keeps `emergency`; increases wait for the 60 s green hold (`red -> green -> green -> green -> allowed` in the `guard` showcase). Tests: `tests/test_ev_guard.py` (45), T10 group. |
| A6 | Paces the actuators | yes | Slow-actuator probe (state lags 25 s): red cut to 12 A at +0 s, the same command is not repeated at +10 s / +20 s, and the car still drawing 16 A is not cut a second time (`capped_by: phase`, target stays 12); an increase to 16 A sent at +0 s is not re-sent at +10 s; an in-flight resume is countermanded by a pause (emergency) or a smaller `set_current` (see judgement call (i)). Tests: T11 group, `test_phase_capacity_settling_*` (14). |
| A7 | Says when to call next | yes | `next_check` observed at each probe step: 22:15:00 (under-use period), 22:15:30 (pacing window after a command), 22:50:00 (memory expiry clamp while the probe at 23:15 is pending), 22:05:30 in the showcase; `None` when nothing is pending. Tests: `test_next_check_*` (14). Minor: the clamp can return a moment before the earliest change (documented at step 5, T12). |
| A8 | Keeps round 1's guarantees | **no: round-1 A4 regressed** | The §12 invariants hold: independent oracle over 3 x 4000 random 6-call sequences (72 000 calls, memory fed forward, fresh/stale/partial measurements, emergency on/off, 4 step sizes): legal currents, per-phase budget, phase-guard bound, fallback cap, down before up, no increase in yellow/red/conservative start, emergency at 0, determinism: zero violations (a mutation that disables the lowering pass produces 293 + 410, so the oracle is live). Determinism, stdlib-only scan (all `rglob` files), 100 ms (7.0 ms measured) hold. **But** the hysteresis step `_keep_places` overbooks the plan: see R-A4. |

### Unmet, with reproductions

**R-A2 (round 2 A2, planned tier).** `decide.py` applies the minimum on-time (`protected`) and the minimum pause (`held_off`, `stopped_for_capacity`) only in `_tier_key` / the active-tier hold-off, i.e. among chargers that are *already in the same tier*. When two sessions both want the current quarter, `plan_sessions` gives it to the more urgent one and demotes the other to priority 4 / `planned_later` first, so the demoted charger is neither protected (it was started) nor marked `stopped_for_capacity` (its final code is `PLANNED_LATER`, not `WAITING_FOR_CAPACITY`). `_keep_places` (the planned-tier hysteresis) only restores it when the contender's deadline is not more than `hysteresis` earlier. A2 has no tier qualifier, and step 5 itself read it that way for the hysteresis (judgement call (ii)); the same reading puts both pause rules in the planned tier.

Reproduction (a and b on L1, budget 6 A, min 6, cheap hours first, 6 kWh each; calls every 5 minutes, actions executed):

| Time | Event | a (alloc, tier, code) | b |
|---|---|---|---|
| 22:00 | only a present, ready 08:45 | 6.0, 2, `in_plan` (started) | no car |
| 22:05 | b arrives, ready 07:00 | **0.0, 4, `planned_later`** (stopped after 5 min, `min_pause` is 15) | 6.0, 2 |
| 22:10 | | 0.0, 4 | 6.0, 2 |
| 22:15 | b unplugs | **6.0, 2, `in_plan` (restarts 10 min after it stopped, `min_pause` is 15)** | no car |

Expected by A2: at 22:05 a "started for capacity is not dropped for an equal-tier charger before the minimum pause" (stays until 22:15); at 22:15 a "stopped for lack of capacity cannot restart before the minimum pause" (held to 22:20 as `minimum_pause`). The same two-charger situation one tier down (need 0, tier 3) behaves as A2 says (`minimum_pause` at 22:20), which is the inconsistency. Script: `/tmp/claude-0/-home-user-energy-optimization/6ceb099d-e40c-56d7-8ca2-50b25e6eb191/scratchpad/drv/probe5.py` and `probe4.py` (scratch, not in the tree).

**R-A4 (round 1 A4, regressed by round 2's `_keep_places`).** Round 1 A4: "the most urgent session claims shared capacity first and no quarter is planned beyond the per-phase budget". `_keep_places` replaces a restored charger's `SessionPlan` with the plan it would have **alone** (`plan_sessions([item], ..., plan_cap)`), so every future quarter of that plan ignores the other session's claim. Reproduction: same two chargers, a running (selected, ready 08:45), b arriving with ready 08:30 at 22:15, budget 6 A:

- round 1 (`982a494`): `a` 0 A priority 4, `b` 6 A priority 2, **0** quarters planned above 6 A;
- round 2: `a` 6 A and `b` 0 A (hysteresis, intended), both priority 2, each listing 6 A in the same 18 quarters, **18 quarters planned at 12 A on a 6 A phase**; `expected_finish` and `reaches_target` of the restored charger are computed from that overbooked plan, so they promise more than the phase can deliver. The allocation itself is safe (`share` still bounds it by the real capacity); the reported plan, the finish estimate and `reaches_target` are what break A4 (and A6 "plan with prices ... whether the ready time is met"). Script: `.../drv/probe12.py`, `probe13.py`.

**For step 3.** Both come from the planned-tier hysteresis living outside the planner. A fix that keeps one source of truth is to feed the planner the retention decision (plan the keepers first with the capacity they hold, then the rest on what is left, and treat a capacity-driven demotion as a capacity stop: `stopped_for_capacity`, `MINIMUM_PAUSE` hold-off), instead of patching `item.plan` afterwards. Whatever the fix, R-A2 and R-A4 reproductions become tests, and no expected value of a round-1 test may change.

### Judgement calls recorded at step 5 (section 5), against section 1

| Call | Verdict | Reasoning |
|---|---|---|
| (i) An open in-flight `last_target` counts as the charger's commanded value (`_commanded`): an emergency pauses an in-flight resume, a smaller share cuts an in-flight increase | **Within section 1** | A5 says emergency "pauses every controllable charger in that call"; A6 says "decreases and pauses always go" and only increases are paced; A8 states the budget/guard invariants for every output. Probe: resume sent at +0 s, state still paused at +10 s: emergency -> `pause` (down); share 8 A -> `set_current 8` (down); unchanged -> nothing. Leaving the command to land would break A5 and A8. Caller contract worth stating: a `SET_CURRENT` in the down stage may reach a charger whose state still reads paused, because a resume to a higher value is in flight. |
| (ii) Hysteresis extended into the planned tier by `_keep_places` | **Within section 1 as to intent; the implementation breaks round 1 A4 (R-A4)** | A2 has no tier qualifier and doc scenario 6 (two priority-2 chargers) is exactly the planned case; without it scenario 6 fails. The method, not the intent, is at fault. |
| Pinned: lost memory drops the emergency latch | **Within section 1** | A1 and the user decision "conservative start = no increases on the call that has no usable memory; normal rules from the next call" decide it; the core cannot know an emergency happened. Worth a `DEVELOPMENT.md` note (caller should persist the memory), not a defect. |
| Pinned: charge-now preemption gives no minimum pause | **Within section 1** | The stopped charger's final code is `WAITING_FOR_HIGHER_PRIORITY`, not a capacity stop, and A2's on-time rule is worded for an *equal-tier* charger; the user's "charge now skips the minimum pause" points the same way. |
| Pinned: a held-off charger occupies its tier | **Within section 1** | "Only the highest occupied tier gets current" (round 1) plus "cannot restart before the minimum pause": letting a lower tier use the capacity would be dropped again at the restart. Cost: capacity idles during the pause. |
| Pinned: `yellow_margin` unbounded | **Within section 1** | Section 1 gives a default of 2 A and no bound; a margin above the threshold keeps the guard yellow for ever. Configuration footgun, `DEVELOPMENT.md` note. |

### Probes

**T14 differential** (round-1 `decide` at `982a494`, copied to a scratch package outside the repo, against the current `decide` with a blank warm memory; scratch `drv/diff1.py` beside the auditor report): 2 x 30 000 generated snapshots (1-4 chargers of all kinds, mixed phases, fresh/stale/partial/no measurements, price cap, charge now, smart off, user limits, 0-40 hourly prices, optional emergency threshold). **Identical** whenever the guard level is green or the measurements are stale: 13 529 + 6 319 and 13 589 + 6 238 snapshots, zero differences. **Different only at yellow, red or emergency**, each intended by A5/A8: yellow, allocation lower (306 / 395: no increase while yellow); red, event only (5 883 / 5 924: the added `PHASE_GUARD_INTERVENED`) or allocation lower (707 / 695: no increase while red); emergency, reason code `EMERGENCY` and event only (1 302 / 1 265) or allocation lower (194 / 165: pause). **Zero** cases where round 2 allocates more than round 1, zero plan, priority, deadline or need differences at any other level. Every differing charger differs in allocation, reason code or reason params (`held_by`, `capped_by`, `regulated_cap`) only, and `expected_finish` / `reaches_target` change only with the allocation.

**Multi-call differential** (memory fed forward, round-1 `decide` on the same state each call, explicit `plugged_in_at` so the session-start rule does not differ; 3 x 4 000 sequences of 6 calls): every allocation difference is explained by a round-2 mechanism (`held_by` phase guard / memory reset, emergency, `under_use` cap, `minimum_pause`, a guard level, or another charger held by one of these), except 6 / 2 / 2 residuals, all selected-first ranking in a shared tier (`_keep_places` and the hysteresis-adjusted remainder order). Without `_keep_places` four of the six disappear. They are the intended hysteresis (and the source of R-A4), not drift. The one other source of difference without explicit `plugged_in_at` is the deadline seen from the remembered session start (A1, T13), intended.

**Showcases**: all nine modules run (`model`, `result`, `quarters`, `planning`, `allocation`, `guard`, `regulation`, `decide`, `codec`), exit 0; `guard`, `regulation` and `decide` read as worked examples of the concept (conservative first call, then the follow-up call that starts the chargers, then the stale call).

### Reader report: `structure-auditor`

Saved verbatim at `/tmp/claude-0/-home-user-energy-optimization/6ceb099d-e40c-56d7-8ca2-50b25e6eb191/scratchpad/auditor-r2-step6.md`: everything matches. Both optional wording edits applied to `STRUCTURE.md` here (`result.py` intro now mentions the tuple normalisation in `ChargerDecision` and `Decision`; the `guard.main` row mentions the final stale call). The re-run of step 3 will touch `decide.py`; steps 4 and 6 re-check its STRUCTURE.md rows.

Drift found, and what was done about it:

1. **Unmet: R-A2** (code wrong, A2 stands): marker back to step 3.
2. **Regression: R-A4** (round 1 A4, code wrong): marker back to step 3.
3. **Accepted at step 2, recorded so the user can veto:** `Decision.guard_level` is `None` under stale measurements (A5 says "each decision reports" a level; there is no reading to classify, and the stale status and event carry the fact); `max_memory_age` (30 min) is a setting section 1 did not list (A1 "out-of-date" needs a threshold; the plan-critic called it justified); `Status.EMERGENCY` is a status member section 1 did not name. None changes a criterion; none halts.
4. **Observations for step 8 (`DEVELOPMENT.md`, not defects):** under a conservative start the status reads `waiting_for_capacity` although capacity exists and the hold is the memory reset (the reason says `held by memory reset`); `next_check` is clamped to the memory expiry, which can be earlier than the earliest change; the lost-memory/emergency-latch interaction and unbounded `yellow_margin` above.

### Earlier rounds still hold

> Later rounds only. Re-check every acceptance criterion from every earlier round in this
> folder: this round changed code they depend on, and their tests passing is necessary but
> not sufficient — a criterion can be satisfied by tests that no longer describe what the
> feature does.

| Round | # | Criterion | Still met | Evidence |
|---|---|---|---|---|
| 1 | A1 | Rejects invalid configuration naming the field | yes | Spot probes raise `ConfigError` with the field: `max_current: ... must be >= min_current`, `phase_budget: required`, `timezone: unknown timezone`, naive `saved_at`, `emergency_threshold` not above the phase threshold, negative `deadband`, `ramp_step` 0. Round-1 model tests unchanged and green. |
| 1 | A2 | Priority 1-4, equal split, max-min remainder, waiting for capacity | yes | T14 differential: identical to round 1 at green and stale on 2 x 30 000 snapshots; doc scenarios 1-5 tests unchanged under the warm helper. |
| 1 | A3 | Respects amp limits, reservations, smart-off | yes | Independent oracle over 72 000 sequence calls (budget, phase guard, legal currents, fixed/unavailable reservations); differential identical at green. |
| 1 | A4 | Cheapest-quarters plan, shared capacity, no quarter planned beyond the per-phase budget | **no** | R-A4: `_keep_places` gives a restored charger a plan made alone, 18 quarters at 12 A on a 6 A phase (round 1 gives 0). Everything else in A4 (DST horizons, blocks, price cap, unpriced quarters, unreachable event) is unchanged: `planning.py` and `quarters.py` have no diff against `982a494`. |
| 1 | A5 | Falls back under stale measurements / stale prices | yes | Differential: zero differences at stale levels (including stale-price snapshots, priority 3 unchanged). One round-2 interaction by design: a conservative start also holds the first stale call's increases (A1 round 2). |
| 1 | A6 | Explainable, ordered output | yes | Down before up asserted by the oracle on every output; actions absolute; in-flight suppression only removes an action, never reorders. Plan/finish truthfulness is the R-A4 defect, tracked there. |
| 1 | A7 | Holds the doc §12 invariants for any input | yes | Round-1 hypothesis property still green and the independent oracle above, zero violations. |
| 1 | A8 | Deterministic, stdlib only | yes | Hash-seed determinism test and the `rglob` AST import scan (covers `guard.py` and `regulation.py`) green; oracle re-decide equality. |
| 1 | A9 | Plain-dict round trip, missing/wrong field rejected | yes | Snapshot, decision and memory round-trip equal after `json.dumps`; decision dicts decode memory strictly with dotted paths; a malformed `memory` inside a *snapshot* dict is deliberately not an error (round 2 A1). |
| 1 | A10 | 100 ms for 10 chargers and 48 h | yes | 7.0 ms minimum, 7.3 ms median with a full memory (`drv/perf.py`); the test asserts `< 0.1`. |

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
