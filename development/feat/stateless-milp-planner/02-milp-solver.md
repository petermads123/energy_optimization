# MILP solver

<!-- claude-plan step=2 status=active -->


| Field | Value |
|---|---|
| Feature | `feat/stateless-milp-planner` |
| Round | `2` |
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

| Round | File | What it delivered |
|---|---|---|
| 1 | `01-stateless-core.md` | The stateless decision core: `feat/ev-charging-core` round 1's public API and behaviour restored (memory, phase guard over time, regulation, anti-flapping, pacing and `next_check` removed with `guard.py` and `regulation.py`), every expected finish computed in UTC (A6), and a round-1-shaped codec that rejects `memory` and round-2 settings. 1221 tests. |

This round came from recommendation `R1` of round `1`, which read:

> Round 2: replace the greedy planner and current-quarter sharing with the HiGHS MILP
> (`scipy.optimize.milp`), as recorded under **Explicitly out of scope**. Not a defect: the
> branch's own second half, agreed with the user before this round opened ("One branch, two
> rounds"). Without it the branch does not deliver what it was opened for.

Round 1's out-of-scope entry recorded what was agreed for it so far: scipy and numpy become
runtime dependencies (round 1's stdlib-only rule is amended); the greedy code is removed
with no fallback; on failure `decide` raises a `SolverError` and the caller handles it in
HA; determinism via a node budget and a single thread; the 100 ms target becomes a stated
solve budget; the solver works in UTC throughout and converts every input to it. Its
criteria are confirmed at this round's step 1.

What is already on the branch that this round must not break:

- Round 1's A1 statelessness: `decide` depends only on its snapshot; no memory, no timing.
- Round 1's A5 codec shape: snapshot and decision dicts carry exactly the round-1 keys.
- Round 1's A6: every time calculation in UTC, outputs in the settings timezone.
- `feat/ev-charging-core` round 1's input validation (`ConfigError` naming the field), the
  priority tiers, reservations for fixed and unavailable chargers, the fallback cap under
  stale measurements, and the action ordering (decreases before increases).

---

## 1. Concept

Agreed with the user on 2026-10-22 ("go ahead"), after these four decisions: lower tiers
use what higher tiers leave; within a tier the current quarter is split evenly where cost
allows; a solve stopped by the node budget returns its best plan with an event; every call
returns within 10 s in total.

### What this is

One mixed-integer program, solved by HiGHS through `scipy.optimize.milp`, decides for every
quarter from now to each car's ready time which charger charges and at how many whole amps.
It replaces the greedy planner and the current-quarter sharing. `decide` acts on quarter 0;
the rest is the reported plan. Everything before the solve (classification, reservations,
the fallback cap and phase guard in quarter 0, deadlines, smart-off chargers served first)
and after it (actions with decreases first, reasons, status, events) stays as round 1
left it.

The model, strongest preference first: meet each need (a shortfall slack keeps the problem
always feasible), avoid unpriced quarters, avoid quarters above the price cap, avoid runs
shorter than the minimum block, lowest price, an even split in quarter 0, earlier quarters
first as the tie-break. Priority tiers sit above all of it as strict precedence on the
leftover: charge-now first, then cars planned to charge now, then spare-capacity charging
(plan completed, prices stale, ready time passed). All time arithmetic is in UTC.

### Why it is worth building

The greedy planner is a set of hand-written precedence rules that approximate the cheapest
plan. An exact optimiser finds the true optimum for a stated objective, and the next loads
the repo is meant for (a battery, a heat pump) enter as variables and constraints instead
of new heuristics.

### Inputs and outputs

Unchanged: a `Snapshot` in, a `Decision` out, the same codec keys, stateless. New: an event
saying a plan is not proven optimal, and a `SolverError` raised when the solver fails.

### How it connects to the rest of the repo

Replaces the greedy planning in `planning.py` and the sharing in `allocation.py` that
`decide.py` calls; `decide.py` builds the model's data from what it already computes and
reads the solution back. `model.py`, `quarters.py` and `codec.py` are untouched apart from
what the new event and error need. `pyproject.toml` gains scipy and numpy as runtime
dependencies. `STRUCTURE.md` and the tests follow.

### Explicitly out of scope

- Other loads (battery, heat pump).
- A greedy fallback when the solver fails or is slow.
- Multithreaded solving; warm starts across calls (the core is stateless).
- Tuning the model until 10 or more chargers prove optimal within the budget.
- `feat/ev-charging-core` round 1's criteria this round supersedes, re-checked at step 6 in
  their amended form: A2's top-tier-only rule and equal split (now this round's A3), A4's
  description of the greedy algorithm (now A2), A8's standard-library-only rule (now A7),
  A10's 100 ms target (now A5).

### Acceptance criteria

| # | The finished feature... |
|---|---|
| A1 | **Plans and allocates within every limit.** In every planned quarter each charger gets 0 or a whole-amp current between its minimum and the lower of its maximum and the user's limit (on/off chargers exactly their assumed current); each phase stays within its budget in every quarter, and quarter 0 also within the phase guard, or the fallback cap when measurements are stale; nothing is planned at or after a car's ready time; the quarter-0 allocation is the plan's first quarter. Shown by a property test over generated snapshots. |
| A2 | **Is optimal for the stated precedence** — need, unpriced, price cap, minimum block, price, even split, earlier-first. On small generated instances every proven-optimal plan has the objective of a brute-force enumeration; the doc scenarios for cheapest quarters, price cap, unreachable target and unpriced quarters give the hand-checked plans; a shortfall gives the "target unreachable" event with the reachable kWh. |
| A3 | **Serves the tiers in order on the leftover.** Charge-now first, then cars planned to charge now, then spare-capacity charging; each tier gets what the tiers above leave, and a lower tier never takes current a higher tier could use. Within a tier, quarter 0 is split as evenly as cost allows. Replaces `feat/ev-charging-core` A2's top-tier-only rule and equal split. |
| A4 | **Keeps the stale-price rule.** With stale prices there is no plan; smart sessions are served in quarter 0 at spare-capacity priority, with round 1's status and event. |
| A5 | **Is deterministic and bounded.** The same snapshot gives an equal decision across runs and hash seeds; the solve uses a single thread and a fixed node budget, never a wall-clock limit; every call with up to 10 chargers and a 48-hour price series returns within 10 s in total on the development machine; a solve stopped by the node budget returns its best plan with an event saying it is not proven optimal. |
| A6 | **Raises instead of guessing.** When HiGHS reports an error or finds no solution, `decide` raises `SolverError` naming the solver status, never a partial or greedy decision. |
| A7 | **Removes the greedy planner and declares its dependencies.** The greedy planning and sharing code is gone from the package and its public API; scipy and numpy are runtime dependencies, and nothing else outside the standard library is imported; `STRUCTURE.md` matches. |
| A8 | **Changes nothing else.** The snapshot and decision dict keys, statelessness, UTC arithmetic and input validation are unchanged. Round 1's scenarios still pass, except where optimality, the leftover rule or the even split gives a different answer; a differential against round 1 of this branch (commit `2646e7c`) over generated snapshots shows every difference is one of those, each kind pinned by a test. |

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
