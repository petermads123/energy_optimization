# EV charging core: behaviour over time

<!-- claude-plan step=3 status=active -->

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
