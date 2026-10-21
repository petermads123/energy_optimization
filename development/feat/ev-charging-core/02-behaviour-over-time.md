# EV charging core: behaviour over time

<!-- claude-plan step=2 status=active -->

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
| A5 | **Runs phase-guard levels over time.** Each decision reports green / yellow / red / emergency. Yellow and red allow no increases; red cuts to fit and raises `PHASE_GUARD_INTERVENED`; emergency (when configured) pauses every controllable charger in that call and allows restarts only after the calm period. Any increase requires the phases to have been green continuously, across calls, for the green hold. |
| A6 | **Paces the actuators.** No increase or resume is sent to a charger within the minimum time between actions; decreases and pauses always go. A command identical to one still in flight is not repeated, and a decrease that has not taken effect yet is not compounded by a second decrease for the same overload. |
| A7 | **Says when to call next.** Each decision gives the earliest moment a new call could change something (a pause, hold, period, probe or pacing window ending), or none. |
| A8 | **Keeps round 1's guarantees.** Every round-1 criterion A1–A10 still holds. Under random multi-call sequences that feed each decision's memory into the next snapshot, every output still holds the doc §12 invariants plus: no increase in yellow, red or a conservative start; every controllable charger at 0 in emergency; down before up. Determinism, the stdlib-only rule and the 100 ms budget hold. |

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
