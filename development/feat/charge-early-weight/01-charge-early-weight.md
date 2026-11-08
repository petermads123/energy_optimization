# Charge-early weight

<!-- claude-plan step=2 status=active -->


| Field | Value |
|---|---|
| Feature | `feat/charge-early-weight` (the folder) |
| Round | `1` |
| Branch | `feat/charge-early-weight` |
| Version | `minor` — a small addition to existing planning behaviour; step 9 adds `0.1.0 → 0.2.0` (from main today) |
| Started | `2026-11-08` |

## Progress

| # | Step | Skill | Runs | Status |
|---|---|---|---|---|
| 1 | Conceptualize | `/conceptualize` | with the user | done |
| 2 | Plan | `/plan` | with the user, unless the critique is clean | pending |
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

---

## 1. Concept

### What this is

`Settings` gets an optional weight per hour, default 0, that adds a premium to the price of
every planned quarter the further it is from now. A quarter `k` quarters ahead is priced at
`price + (w/4)·k·|price|` when the planner compares plans. `k` counts quarters from the
quarter containing `now`, on the UTC grid, so quarter 0 is never weighted and DST nights are
counted correctly. Using `|price|` means a negative price is also less attractive the longer
the wait. The weight applies to the `PLANNED` tier only.

### Why it is worth building

Prices are mostly known a day or more ahead, so the issue is not forecast risk. A pure cost
minimiser will wait a full day to save a few percent, or even 10%, and the car is unavailable
or only partly charged for the whole wait. The weight is the owner's price of waiting: a
delay is taken only if it saves more than its premium. At `w = 0.01` per hour a full day is a
24% premium and two hours is 2%.

### Inputs and outputs

One new optional float field on `Settings`, validated like the others, carried by the codec
(a snapshot dict without it decodes as 0). The output is the same `Decision` type; only which
quarters get planned changes. Assumptions: the field and parameter names are chosen in
step 2; values above 1.0 per hour are refused to keep objective coefficients inside the
solver's range.

### How it connects to the rest of the repo

`ev.model.Settings`, `ev.codec`, `ev.decide` (passes the weight on) and `ev.optimize` (weights
the planned-stage price objective). `Demand` and the quarter-0 tiers are untouched. The
whole-tick cost hold (`_price_ticks`) runs on the weighted prices: with `w = 0` they are the
raw prices and today's exact hold applies unchanged; with `w > 0` they are rarely on a tick
grid, so the existing tolerance path normally takes over, and this round does not widen it.
`interruptible` and `common` are untouched.

### Explicitly out of scope

The interruptible scheduler; the charge-now, smart-off and spare-capacity tiers; per-charger
weights; compounding or non-linear discounts; weighting unpriced quarters; changing the
tolerance fallback or the tick detection.

### Acceptance criteria

| # | The finished feature... |
|---|---|
| A1 | With the weight 0 or omitted, `decide` returns exactly today's decision for every snapshot, shown by the existing suite and by a differential of generated snapshots against `main`. |
| A2 | With `w = 0.01` per hour, a car whose need fits one quarter charges now when the price now is 1.5% above the price 8 quarters later, and later when it is 2.5% above; it also charges now when the price 96 quarters out is 10% lower and later when it is 30% lower. With `w = 0` it charges later in every one of those cases. |
| A3 | The weight penalises waiting at negative prices too: equal negative prices now and 2 hours later give "now"; a price 1% more negative 2 hours later still loses at `w = 0.01`, while the same price wins at `w = 0`. |
| A4 | Only the planned tier is affected: snapshots with no planned car give the same allocations for any weight, and the quarter-0 tiers' results do not change. |
| A5 | The price cap, `price_cap_exceeded` and every `PlannedQuarter.price` still use the raw prices; a quarter above the cap counts as above it however the weight shifts the plan. |
| A6 | `Settings` refuses a negative, NaN, infinite, bool or above-1.0 weight, naming the field. The weight round-trips through the codec, and a snapshot dict without it decodes as 0. A decision with `w > 0` is identical across runs and hash seeds, and the solver is called with the same options as before. |

### Open questions

None.

---

## 2. Plan

> Written in step 2, accepted before step 3 starts — by the user, or by a clean critique
> (see `/plan`). Concrete enough that step 3 is transcription, not invention.

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

### Critique

> The `plan-critic`'s findings, each with what was done — applied, or rebutted in one
> sentence — then its verdict, and one line saying how the plan was accepted:
> `Accepted: clean critique, <date>` or `Accepted: by the user, <date>`.

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
