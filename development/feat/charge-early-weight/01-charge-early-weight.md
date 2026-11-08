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
`price · (1 + w·k/4)` when the planner compares plans. `k` counts quarters from the
quarter containing `now`, on the UTC grid, so quarter 0 is never weighted and DST nights are
counted correctly. The premium is added to positive prices only: a zero or negative price is used as it is, so
the weight never makes a negative-priced quarter look worse than it is. The weight applies to the `PLANNED` tier only.

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
| A3 | The weight is added to positive prices only: a plan over quarters that are all zero or negative is the same at any weight; a later quarter at a more negative price wins at `w = 0.01` just as at `w = 0`; and between a negative or zero quarter and a positive one, the positive one is penalised by its `k`, the other not at all. |
| A4 | Only the planned tier is weighted: snapshots with no planned car give the same allocations and status for any weight; with planned cars, the smart-off and charge-now allocations do not change, the tiers' rules (as many planned cars on now as possible, the better-ranked first, filled evenly) do not change, and spare capacity takes whatever the planned cars leave in quarter 0. |
| A5 | The price cap, `price_cap_exceeded` and every `PlannedQuarter.price` still use the raw prices; a quarter above the cap counts as above it however the weight shifts the plan. |
| A6 | `Settings` refuses a negative, NaN, infinite, bool or above-1.0 weight, naming the field. The weight round-trips through the codec, and a snapshot dict without it decodes as 0. A decision with `w > 0` is identical across runs and hash seeds, and the solver is called with the same options as before. |

### Open questions

None.

---

## 2. Plan

### Approach

Obvious design, so no fork: the weight reaches exactly one place. `Settings` gains
`waiting_premium_per_hour` (float, default 0.0). `decide` passes it to `optimize` as a new
required trailing parameter (`optimize` has no defaults by design, and `decide` always passes it). In `_stage_plan` only level P5's cost objective
(`_minimise_price`) reads weighted prices: the price of the cell in horizon quarter `t` is
`p · (1 + w · t / 4)` for `p > 0` and `p` otherwise (`t` is the index in the horizon, whose index 0 is the quarter
containing `now`; `w · t / 4` is the weight times the hours since that quarter began). P2 (unpriced), P3 (price cap), P4 (blocks), the
quarter-0 tiers, `_session_plan` and `PlannedQuarter.price` keep raw prices, which covers A4
and A5. `_price_ticks` is called on the weighted values of the cells, unchanged: at `w = 0`
the weighted function is skipped (not merely multiplied by zero), so the values and the exact
tick hold are bit-identical to today (A1); at `w > 0` the tick detection finds a grid only when
the weighted values happen to share one, else the existing tolerance path runs (A6, no new
code path). `_minimise_price` is the only consumer of the weighted prices, so nothing else
sees them. Determinism holds because the weighted prices are a pure function of the inputs
and the objective is built in the same sorted column order.

Rejected: weighting inside `decide` by rewriting the price series handed to `optimize`. It
would leak weighted prices into `PlannedQuarter.price`, the price-cap check and the cap
event (breaking A5), and `decide` does not know the horizon index the planner uses. Also
rejected: penalising negative prices through `|p|`, which at large `w·t` makes a negative price look worse than a free later quarter (the user chose positive-only), and
rounding weighted prices back to the original tick grid, which changes the meaning of the
setting for a solver convenience.

### Modules

| Path | New or changed | Purpose |
|---|---|---|
| `src/energy_optimization/ev/model.py` | changed | `Settings.waiting_premium_per_hour` field and its validation |
| `src/energy_optimization/ev/optimize.py` | changed | `optimize` takes the premium; P5 minimises the weighted cost |
| `src/energy_optimization/ev/decide.py` | changed | passes `settings.waiting_premium_per_hour` to `optimize` |
| `src/energy_optimization/ev/codec.py` | changed | encodes and decodes the field (omitted decodes as 0.0) |
| `STRUCTURE.md` | changed | new field, new parameter, the tests that cover them |
| `tests/test_ev_model.py`, `tests/test_ev_codec.py`, `tests/test_ev_optimize.py`, `tests/test_ev_decide.py` | changed | see Test intents; the existing literal key-set, dataclass-field and signature tests move with the surface |

No new module: the change is one field, one parameter and one objective line.

### Public API

| Signature | Module | Purpose | Covers |
|---|---|---|---|
| `Settings(..., max_measurement_age: timedelta = timedelta(seconds=60), waiting_premium_per_hour: float = 0.0)` (new last field) | `ev/model.py` | Premium per hour of waiting, as a fraction of the price: a planned quarter `h` hours after the start of the current quarter costs `price · (1 + w·h)` when the price is positive and `price` otherwise. Float; ints accepted; refuses bools, NaN, inf, a negative value and a value above 1.0, naming `waiting_premium_per_hour`. | A1, A2, A3, A6 |
| `optimize(demands: Sequence[Demand], now: datetime, prices: Mapping[datetime, float], capacity: Mapping[Phase, float], now_capacity: Mapping[Phase, float], voltage: float, min_block_quarters: int, price_cap: float \| None, timezone: str, waiting_premium_per_hour: float) -> Outcome` | `ev/optimize.py` | The planner's lowest-price level minimises the weighted cost; `ValueError` naming `waiting_premium_per_hour` if it is not a finite number ≥ 0 (checked after the empty-demands return, with the other `ValueError`s); no upper bound here, that is `Settings`' | A1–A5 |
| `decide(snapshot: Snapshot) -> Decision` (signature unchanged) | `ev/decide.py` | Passes the setting on | A2, A4 |
| `snapshot_to_dict`, `snapshot_from_dict` (signatures unchanged) | `ev/codec.py` | `settings` dict gains the key `waiting_premium_per_hour`; the decoder accepts it omitted (0.0), refuses null, strings and out-of-range values with the path `settings.waiting_premium_per_hour` | A6 |

### Implementation guide

1. `model.py`: add the field after `max_measurement_age`; document it in the `Settings`
   docstring as the owner's price of waiting (a delay is taken only if it saves more than
   its premium); validate with `_number`, then refuse a value `< 0` or `> 1.0`; store the
   float with `_set`.
2. `optimize.py`: add the required trailing parameter and its docstring (`Args`, `Raises`); validate it
   after the empty-demands return; thread it through `_stage_plan` to `_minimise_price`.
3. `_minimise_price`: when the premium is 0 build the price list exactly as today. Otherwise
   replace each cell's price by `p · (1 + premium · t / 4)` when `p > 0`, else `p`, with `t` the horizon index
   (hours per quarter taken from `QUARTER`, not a literal), and use those values both for the
   tick detection and for the tolerance objective. Nothing else reads them.
4. `decide.py`: pass `settings.waiting_premium_per_hour` as the last argument to `optimize`.
5. `codec.py`: add the key to `_SETTINGS_FIELDS` (converter `_num`) and to `snapshot_to_dict`.
6. `STRUCTURE.md`: the `Settings` row, the `optimize` row and Description, the codec
   paragraph, and the test descriptions for the four test files.
7. Update the tests that pin the surface (dataclass fields, the `optimize` parameter list in
   `test_the_optimiser_and_capacity_signatures`, whose no-defaults assertion stays, the settings
   key set in `snapshot_to_dict` output) and every direct `optimize` call (the `run` helper in
   `tests/test_ev_optimize.py`, the direct calls, `main()`'s two calls), then add the new tests.

### Test intents

| # | Must prove | Covers |
|---|---|---|
| T1 | At premium 0 and omitted, `optimize` and `decide` equal today's results: the existing suite is unchanged apart from surface-pinning tests, and a one-off differential at step 5 (not a suite test, which would compare the code with itself after merge): `origin/main` in a temporary worktree and this branch both run 2 × 30 000 generated snapshots through `decide` with the field omitted and at 0.0, counts pasted into section 5 for step 6 to cite; the permanent guard is the unchanged existing suite | A1 |
| T2 | `optimize` with `min_block_quarters=1`, a one-quarter need and two priced quarters: at 0.01 per hour and a gap of 8 quarters, now wins at +1.5% and loses at +2.5%; with 96 quarters, now wins when the later price is 10% lower and loses when it is 30% lower; premium 0 picks the later quarter in all four; also through `decide` for a planned car (`min_block_quarters=1`, a price on every quarter of the horizon) | A2 |
| T3 | Negative and zero prices are unweighted: all-non-positive horizons plan identically at premiums 0, 0.01 and 1.0; a later, more negative quarter wins at every premium; mixed-sign horizons behave as the formula says (hand-checked: a positive quarter far out loses to a negative one nearer, and a negative one later still beats a positive one now when it did at premium 0) | A3 |
| T4 | Snapshots without a planned car (smart off, charge now, spare, fixed, unavailable) give identical allocations and status for premiums 0, 0.01 and 1.0; two planned cars of different deadlines are weighted by the same horizon index, with planned cars present the smart-off and charge-now allocations are identical across premiums and spare equals the quarter-0 room minus the planned cars' quarter-0 current, while the cars on now still follow the most-on-now, better-ranked-first and even-fill rules at premium > 0 | A4 |
| T5 | With a price cap and a premium: `price_cap_exceeded`, the `PRICE_CAP_EXCEEDED` event and the P3 level use raw prices (a quarter above the cap is still avoided though the weight would push its neighbour above the cap), and every `PlannedQuarter.price` equals the raw price of its quarter | A5 |
| T6 | `Settings` validation table: negative, NaN, ±inf, bools, strings, 1.0 accepted, 1.0000001 refused, 0 and an int accepted, each naming `waiting_premium_per_hour`; `optimize` raises `ValueError` for negative, NaN, inf and bool (not for 5.0) | A6 |
| T7 | Codec: round trip with a premium, omitted key decodes to 0.0, null/string/huge/out-of-range refused with path `settings.waiting_premium_per_hour`, and the dict output carries the key | A6 |
| T8 | Determinism: a snapshot with a premium decides identically on repeated runs and under several `PYTHONHASHSEED`s; the spied `milp` calls show one thread, the node budget, presolve as before; a premium on a price series that has no tick grid (the tolerance path) and one whose weighted values share a grid (the exact path) both solve | A6 |
| T10 | The brute-force oracle (`level_tuple` in `tests/test_ev_optimize.py`) takes a premium in {0, 0.01, 1.0} on its P5 level, so the weighted level is proved in general, including two cars with different deadlines sharing the horizon index | A1–A3 |
| T9 | DST: with the fall-back night in the horizon, the weight follows the UTC quarter index, so the repeated hour is weighted by elapsed quarters, not by wall-clock hour | A2, A6 |

### Risks

- **The weighted objective changes tie-breaking against P7 (earlier first).** None expected:
  the premium only raises a later positive price by more than an earlier one's (and leaves
  zero and negative prices alone), so it can never turn an earlier-preferred pair into a
  later-preferred one. If a test shows a plan
  moving later at a positive premium, the build halts: that contradicts A2.
- **Tolerance path accuracy.** With `w > 0` the cost hold is to a tolerance (the existing
  1e-6 behaviour), which can trade away a gap under 1e-6 EUR. Accepted in the concept; no
  action.
- **Speed.** The tolerance path may solve slower than the exact tick path; the 3/7/10 charger
  timing tests (A5 of the earlier round) run at premium 0 and are unaffected. If a premium run
  of the timing seeds is slower than 3x the premium-0 time, record it in `DEVELOPMENT.md`
  rather than halt.
- **Surface-pinning tests.** Four existing tests pin dataclass fields, `optimize` parameters
  and codec key sets; they must be updated, not weakened, to include the new field and
  parameter. Anything else failing at premium 0 is a bug in the change: the build fixes it,
  and halts if two fixes fail.
- **Fraction vs percent confusion.** The unit is a fraction per hour (0.01 = 1% per hour); a
  docstring example must say so.

### Critique

`plan-critic` verdict: accept with the changes below; findings 1 to 3 each carried a question for the user.

1. Negative prices weighted above zero at large `w·h` (waiting premium could push charging later). **Applied, by the user's decision:** the premium is added to positive prices only; A3, T3 and the formula changed accordingly, and Risk 1 now stands (the premium cannot turn an earlier-preferred pair later-preferred).
2. A3 not showable as "now vs later" under negative-price overfill. **Dissolved:** with negative prices unweighted, A3 no longer compares negative quarters by the weight, so the overfill reading is not needed.
3. A4 false for spare when a planned car moves into quarter 0. **Applied:** A4 and T4 reworded (smart-off and charge-now unchanged, the tiers' rules unchanged, spare takes what the planned cars leave); this is independent of the answer to finding 1. Awaiting the user's acceptance of the reworded A4.
4. `optimize` default contradicts the no-defaults surface test. **Applied:** the parameter is required; the no-defaults assertion stays.
5. A1 differential form open. **Applied:** a one-off differential at step 5, not a suite test.
Extras applied: T2 uses `min_block_quarters=1` and prices on every quarter; T10 extends the brute-force oracle.

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
