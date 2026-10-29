# Interruptible load scheduler — off-time limits

<!-- claude-plan step=2 status=active -->

| Field | Value |
|---|---|
| Feature | `feat/interruptible-load-scheduler` (the folder) |
| Round | `1` |
| Branch | `feat/interruptible-load-scheduler` |
| Started | `2026-10-29` |

## Progress

| # | Step | Skill | Runs | Status |
|---|---|---|---|---|
| 1 | Conceptualize | `/conceptualize` | with the user | done |
| 2 | Plan | `/plan` | with the user | in progress |
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

Agreed with Peter on 2026-10-29. The letters X, Y, Z, N and M are shorthand for the concept
and the criteria only; the code names each limit descriptively, and step 2's plan maps each
letter to its field.

### What this is

A stateless decision core for **interruptible loads**: devices that are normally on and can be
switched off for a while without harm, as long as the off-time is bounded — a refrigerator or
freezer behind a smart plug, an electric water heater, a dehumidifier, a circulation or pool
pump. Given a price series in quarter-hours, the load's limits and what the load has done
recently, it returns an on/off plan for every quarter of the horizon and the decision for the
current quarter: off in the expensive quarters, on in the rest, never breaking a limit.

The limits, all in whole quarters:

- **Maximum consecutive off (X)** — the load is never off for more than X quarters in a row.
- **Maximum off per rolling window (Y of Z)** — in every window of Z consecutive quarters, the
  load is off in at most Y of them. The window **slides**: every run of Z consecutive quarters
  counts, wherever it starts — not fixed blocks such as calendar days. Equivalently: on in at
  least Z − Y of every Z quarters, which also describes loads that *must run* a certain share
  of the time. Y = Z means no window limit.
- **Minimum on after an off period (N)** — once switched back on, the load stays on for at
  least N quarters, so a compressor is not short-cycled and the device can recover. N = 0
  behaves as N = 1.
- **Minimum off block (M)** — an off period lasts at least M quarters, so a single
  cheap-looking quarter does not cost a relay switch.

Valid limits satisfy 1 ≤ M ≤ X and M ≤ Y ≤ Z; anything else is a `ConfigError`. A load that
must never be off is simply not passed to the core.

Like the EV core, it never reads the clock and does no I/O: the caller passes `now`, the
prices and the load's recent history, and owns timing, actuation and retries.

**History is a commitment.** What the load did before the current quarter constrains the plan:
an off run already in progress counts towards X and towards the windows, an on run after an
off period counts towards N, and an off run shorter than M **continues until it reaches M**.
History that already breaks a limit — someone switched the plug by hand — is accepted, not an
error: every plan quarter that limit still constrains is on. When rules conflict, they give
way in this order: the override first, then X and the window, then M and N, then the
end-of-horizon rule.

**Short history is accepted.** The core looks back max(Z − 1, X, N) quarters. History shorter
than that is padded with *on* before its start, the plan is made as usual, and the result says
the history was short. Older history than the look-back is ignored.

### Why it is worth building

A fridge or water heater uses energy all day, and the price spread inside a day is large. A
load that may be off for bounded periods can move its consumption out of the peak — but only
if the off-time is placed by looking at the whole horizon, under limits that keep the food
cold and the compressor healthy. A fixed daily schedule (off 17–21 every day) ignores the
day's actual prices and cannot express "at most X in a row and at most Y in any Z".

**The saving is a shift, not a cut.** A fridge that was off for an hour runs harder
afterwards to pull the temperature back down, so most of the energy it did not use while off
is used in the quarters right after. What is earned is the price difference between the off
quarters and the recovery quarters, not the price of the off quarters. The core models this,
or it would overstate savings and put an off period right before an even more expensive
quarter.

### Inputs and outputs

In:

- `now`, aware.
- **Prices** — a series of priced quarter-hour intervals (the same shape the EV core takes).
  The horizon is the contiguous run of priced quarters from the one holding `now`; it ends at
  the first unpriced quarter. With no price for the current quarter the decision is on, with
  reason "no prices". There is no check on how old the prices are.
- **The load** — its limits (X, Y, Z, N and M); its power while running in steady state (kW,
  **optional** — used only for the money figures); a **minimum saving in percent** (default
  0 %); and a **rebound factor** r in [0, 1]: the share of the energy not used while off that
  is used again in the on-quarters right after (1 = everything comes back, 0 = a true saving).
  The rebound energy is **spread evenly over the first max(N, 1) on-quarters** after the off
  period, on top of the steady-state power. Optional: an override saying the load may not be
  off right now (for instance a measured temperature above a limit).
- **History** — the load's on/off state for the quarters before the current one, newest last.
  The current off run, the on run since the last off period and the off-quarters already used
  in each window are derived from it.

Out:

- The decision for the current quarter: `on` or `off`.
- The plan: on/off for every quarter of the horizon.
- The saving of the plan against always-on **in percent** — independent of the load's power,
  so it is always reported, except when the always-on cost is 0 (then not reported). With the
  power given, also the expected cost of both and the difference in the price's currency.
  **Always-on** means switching on now and staying on, including the rebound of an off run in
  progress ending now.
- A reason/status: planned off, planned on, limit binding (which one), continuing an off run
  to its minimum, no prices, overridden on, no saving worth taking — and a flag when the
  history was short.

**Cost model.** A quarter's energy is the steady-state power for a quarter when on, plus any
rebound landing in it, and 0 when off; its cost is that energy times the quarter's price. The
plan minimises the horizon's total cost. Ties between equal-cost plans go to the plan with
fewer off quarters; step 2 fixes the rest of the tie-break so the result is deterministic.

**Minimum saving.** An off period that starts in the horizon is only planned if it saves at
least the given percentage of what its affected quarters would cost always on. Its affected
quarters are its off quarters and the max(N, 1) on-quarters carrying its rebound; the saving is
their always-on cost minus their planned cost, both at the same steady-state power, so the
power cancels out and the threshold works without knowing the real consumption. 0 % takes any
saving above zero. When the affected quarters' always-on cost is ≤ 0, the saving is measured
against its absolute value. An off run already in progress is judged by cost alone. A
percentage rather than an amount, because the amount needs the power (Peter).

**End of the horizon.** The last max(N, 1) quarters of the horizon are on, so every off period
ends with its full rebound inside the priced quarters — otherwise an off period at the end
would push its rebound past the last price and look free. The one exception is an off run in
progress that must still continue to M. The off-budget of a window that reaches past the
horizon is not reserved: the core re-plans every quarter, prices usually reach at least 10
hours ahead, and a caller can extend the series with a forecast (a forecast price is just a
price to the core).

### How it connects to the rest of the repo

A new subpackage beside `ev`, with the same conventions: a pure function, frozen dataclasses
that validate themselves, `ConfigError` naming the field, a `main()` showcase per module. It
solves with the HiGHS MILP that `ev` already depends on (`scipy`), or something simpler if the
plan shows that is enough. It does not call `ev` and `ev` does not call it. No timezone is
needed: there are no local ready times, and all quarter arithmetic is in UTC.

**Shared foundation.** What both cores need moves out of `ev` into a shared module at package
level: the price types (`PriceInterval`, `PriceSeries`), `ConfigError` and the validation
helpers behind it, and the quarter-hour grid helpers. `ev` imports them from there, and so
does the new subpackage. The package may be reshaped so it reads as one general library rather
than an EV library with a neighbour; `ev`'s public names stay importable from where they are
today, so existing callers are not broken. `STRUCTURE.md`, `README.md` and `CLAUDE.md` are
updated to the new layout.

### Explicitly out of scope

- **Several loads in one call**, and a shared power budget or phase limit across them. Each
  load is planned on its own; a caller with several loads calls once per load.
- **Thermal models.** No temperature, insulation or door-opening model; the off-time limits
  and the rebound factor are the model. A measured temperature can only force the load on
  through the caller's override.
- **Actuation and timing** — switching the plug, retries, re-planning cadence. The caller's.
- **Price forecasting.** The core plans on the prices it is given.
- **Continuous power levels.** On or off only.
- **A JSON codec** for the new core's input and output, like `ev.codec`. Noted in
  `DEVELOPMENT.md`.
- **Reserving window budget past the horizon.** Noted in `DEVELOPMENT.md`.

### Acceptance criteria

| # | The finished feature... |
|---|---|
| A1 | Returns a plan that adds no breach of any limit, counting history: no more than X consecutive off quarters, no more than Y off quarters in any window of Z, at least N quarters on after every off period, and every off period that starts in the horizon at least M quarters long; quarters that history already constrains are on. |
| A2 | Returns, among the plans that keep the limits and the minimum saving, one with the lowest expected cost under the rebound model; checked against brute force on small horizons. |
| A3 | With equal prices across the horizon and r = 1, returns an always-on plan. |
| A4 | When the history already uses the window's off-budget, or the current off run has reached X, decides `on` for the current quarter, and the reason names the binding limit. |
| A5 | With the override forcing the load on, decides `on` for the current quarter, and the rest of the plan still keeps the limits given that quarter. |
| A6 | Reports the plan's saving against always-on in percent, and, when the power is given, the expected cost of both in the price's currency; with r = 1 and a flat price, the saving is 0 %. |
| A7 | Raises `ConfigError` naming the field for invalid input — limits that cannot be met (M > X, M > Y, Y > Z, a limit below its minimum), r outside [0, 1], a negative minimum saving, prices not on quarter boundaries. |
| A8 | With history shorter than the look-back, pads it with *on* before its start, plans as usual, and says in the result that the history was short. |
| A9 | Gives the same output for the same input (no clock, no randomness), and does not mutate its inputs. |
| A10 | Puts the price types, `ConfigError` and the quarter helpers in one shared package-level module that both subpackages import; every name `ev` exports today is still importable from the same path, and the existing `ev` suite passes without changes to its assertions. |
| A11 | With a minimum saving of P %, plans no off period starting in the horizon that saves less than P % of its affected quarters' always-on cost; an input where no off period reaches P % gives always-on; the result is the same with or without the power given. |
| A12 | Keeps the last max(N, 1) quarters of the horizon on in every plan, except where an off run in progress must still continue to M. |
| A13 | Solves a 192-quarter horizon with Z = 96 within 10 s on the development machine. |

### Open questions

None — all closed 2026-10-29: sliding window; rebound spread over the first N on-quarters;
minimum off in round 1; a shared package-level module; a minimum saving in percent per off
period; the last max(N, 1) quarters on; short history padded as on; A3 holds for r = 1 only;
an off run in progress continues to M.

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
