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

> Written in step 2, accepted by the user before step 3 starts. Concrete enough that
> step 3 is transcription, not invention.

### Approach

One paragraph on the chosen approach, and one on what was rejected and why.

### Modules

| Path | New or changed | Purpose |
|---|---|---|

### Public API

> Every public class and function, with its full signature as it will be written.
> `Covers` links back to the acceptance criteria above.

| Signature | Module | Purpose | Covers |
|---|---|---|---|

### Implementation guide

Ordered. Each entry small enough to finish and check.

1.
2.

### Test intents

> High-level: what a test must prove, not how it is written. Step 5 turns each of these
> into concrete cases, including the edge cases.

| # | Must prove | Covers |
|---|---|---|
| T1 | | |

### Risks

What could make this harder than it looks, and what the build should do if it does —
including whether it should halt.

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
