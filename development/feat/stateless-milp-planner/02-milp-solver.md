# MILP solver

<!-- claude-plan step=1 status=active -->


| Field | Value |
|---|---|
| Feature | `feat/stateless-milp-planner` |
| Round | `2` |
| Branch | `feat/stateless-milp-planner` |
| Started | `2026-10-22` |

## Progress

| # | Step | Skill | Runs | Status |
|---|---|---|---|---|
| 1 | Conceptualize | `/conceptualize` | with the user | pending |
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

> Written in step 1, agreed with the user before step 2 starts. Prose, not code. Steps 3
> to 7 run without the user, and the one thing that stops them is a finding that would
> change this section — so what is not decided here is decided by a halt.

### Defect

> Fix rounds only — a round 1 that `/fix` opened, or a later round opened on a bug report,
> whatever its folder is called. Delete this block on a feature round; its presence, filled,
> is the only thing that marks a round as a fix round to every step after this one. Its
> starting content is the `/fix` diagnosis, agreed with the user like the rest of section 1
> and written to disk with it. The root cause is on the halting line: a build that finds a
> different cause halts rather than fixing what it found.

| Field | Value |
|---|---|
| Observed | What happens, quoted from the reproduction. |
| Expected | What should happen, and what says so — a docstring, a test, an earlier round's criterion. |
| Reproduction | The exact command or call and its output. Step 3 turns this into the first test and runs it red before fixing. |
| Root cause | `file.py:NN`, and the decision on that line that is wrong. |
| Introduced by | The commit, or "older than the history here". |
| Class | Other inputs the same cause breaks, and the same shape elsewhere in the repo. |
| Blast radius | Callers of the cause, tests that will move, anything that depends on the current behaviour. |
| Scope | `this instance` or `the class` — the user's decision, with the reason. What the class holds that is not taken goes under Explicitly out of scope by name. |

Critique — the `diagnosis-critic`'s findings and what was done with each:

### What this is

### Why it is worth building

### Inputs and outputs

### How it connects to the rest of the repo

Which existing modules it calls, which call it, what it does not touch.

### Explicitly out of scope

### Acceptance criteria

> Numbered, observable, and phrased so that step 6 can mark each one met or not met.
> These are the contract. Step 2 plans against them, step 5 tests them, step 6 audits
> against them. If a criterion cannot be observed from outside the code, rewrite it.
>
> On a fix round the first criterion is the reproduction passing — "Given <the
> reproduction's input>, <expected> rather than <observed>" — and the last is that nothing
> else changed, phrased so step 6 can evidence it with more than a green suite. If the
> scope is `the class`, each input in the class gets its own row.

| # | The finished feature... |
|---|---|
| A1 | |
| A2 | |

### Open questions

> Must be empty before step 2 begins. An unanswered question here is a decision being
> made by accident later — and nobody is watching when it happens.

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
