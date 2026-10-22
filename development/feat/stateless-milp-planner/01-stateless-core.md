# Stateless core

<!-- claude-plan step=2 status=active -->

| Field | Value |
|---|---|
| Feature | `feat/stateless-milp-planner` |
| Round | `1` |
| Branch | `feat/stateless-milp-planner` |
| Started | `2026-10-22` |

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

The code it changes is `feat/ev-charging-core` (merged to `main` as pull request #2): round
1 (`development/feat/ev-charging-core/01-decision-core.md`) built the single-call decision
core; round 2 (`development/feat/ev-charging-core/02-behaviour-over-time.md`) added memory
and behaviour over time on top. This round removes round 2's behaviour and keeps round 1's.

---

## 1. Concept

### What this is

`decide(snapshot)` plans from the snapshot alone, as `feat/ev-charging-core` round 1 did.
Everything that branch's round 2 added that needs memory of earlier calls is removed:
memory and the conservative start, minimum pause and hysteresis, regulation to measured
draw (cut, ramp, resume probe), phase-guard levels over time (green hold, emergency latch),
pacing, in-flight command handling, settling after a decrease, and `next_check`. Round 1's
planning, priority tiers, sharing, per-phase budget and measured phase headroom, fallback
cap under stale measurements, reservations, reasons, actions, status, events and codec
stay — and so do the bug fixes round 2 made in code round 1 shipped (overflow guards,
`_expected_finish` on a trickle of current, and the like).

### Why it is worth building

The user's decision: the repo is a general planning core and should "simply create the
plan given the constraints and states". Timing and command handling belong to the caller —
in the user's case an AppDaemon app in Home Assistant that calls every 5 minutes and on
events (plug-in, a user change), checks itself whether chargers use their setpoints, waits
between instructions, and decreases chargers locally when a phase gets close to its limit
before calling again. Behaviour that depends on earlier calls duplicates what the caller
does and makes the core harder to reason about. It also clears the ground for round 2,
whose solver model shrinks once the memory-driven rules are gone.

### Inputs and outputs

Exactly `feat/ev-charging-core` round 1's: a `Snapshot` (time, settings, chargers, states,
sessions, prices, phase currents) in, a `Decision` (ordered actions with absolute targets,
per-charger decisions, available amps, status, events) out, both convertible to and from
plain JSON-compatible dicts. A snapshot dict that still carries `memory` or a round-2
setting is rejected with a `ConfigError` naming the field; there are no released callers.

### How it connects to the rest of the repo

Changes `src/energy_optimization/ev/` (`model`, `result`, `planning`, `allocation`,
`decide`, `codec`, `__init__`), deletes `guard.py` and `regulation.py` with their tests,
and updates the remaining `tests/test_ev_*.py`, `STRUCTURE.md` and `DEVELOPMENT.md`.
Nothing outside the package and its docs.

### Explicitly out of scope

- **Round 2 on this branch — the exact solver:** the HiGHS MILP (via
  `scipy.optimize.milp`) replaces the greedy planner and the current-quarter sharing, on
  the simplified core. Agreed with the user so far: scipy and numpy become runtime
  dependencies (round 1's stdlib-only rule is amended); the greedy code is removed with no
  fallback; on failure `decide` raises a `SolverError` and the caller handles it in HA;
  determinism via a node budget and a single thread; the 100 ms target becomes a stated
  solve budget. Its criteria are confirmed at its own step 1.
- Commands in flight, waiting between commands, and any other timing — the caller's.
- The example AppDaemon app.
- Any new behaviour.

### Acceptance criteria

| # | The finished change... |
|---|---|
| A1 | **Is stateless.** `decide` depends only on its snapshot; the public types and signatures are exactly `feat/ev-charging-core` round 1's (section 2 of `01-decision-core.md`, in its original round-1 form), and no memory type, round-2 setting or round-2 enum member remains. |
| A2 | **Removes round 2 cleanly.** `guard.py`, `regulation.py` and their tests are deleted; the round-2 parameters on `phase_capacity` (`settling`), `plan_sessions` (`first`) and `PlanRequest` (`skip_current`) are gone; `STRUCTURE.md` and the `ev` exports match the code. |
| A3 | **Behaves like round 1.** A differential against `feat/ev-charging-core` round 1's `decide` (commit `982a494`) over generated snapshots gives equal decisions, except where a round-2 bug fix in shared code applies — each such difference listed with the test that pins it. |
| A4 | **Keeps every round-1 guarantee.** `feat/ev-charging-core` round 1's criteria A1–A10 all hold; the round-1 suite passes with its warm-memory helper removed and no expected value changed. |
| A5 | **Has a round-1-shaped codec.** Snapshot and decision dicts carry exactly round 1's keys; a dict with `memory` or a round-2 setting is rejected with a `ConfigError` naming that field. |

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
