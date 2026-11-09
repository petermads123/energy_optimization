# Charge-early weight

<!-- claude-plan step=9 status=done -->


| Field | Value |
|---|---|
| Feature | `feat/charge-early-weight` (the folder) |
| Round | `1` |
| Branch | `feat/charge-early-weight` |
| Version | `minor` — a small addition to existing planning behaviour; 0.1.0 → 0.2.0 |
| Started | `2026-11-08` |

## Progress

| # | Step | Skill | Runs | Status |
|---|---|---|---|---|
| 1 | Conceptualize | `/conceptualize` | with the user | done |
| 2 | Plan | `/plan` | with the user, unless the critique is clean | done |
| 3 | Implement | `/implement` | in `/build` | done |
| 4 | Verify | `/verify` | in `/build` | done |
| 5 | Test | `/test` | in `/build` | done |
| 6 | Concept check | `/concept-check` | in `/build` | done |
| 7 | Ship | `/ship` | in `/build` | done |
| 8 | Recommend | `/recommend` | with the user | done |
| 9 | Pull request | `/create-pr` | with the user | done |
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

Accepted: by the user, 2026-11-08

---

## 3. Implementation notes

> Written in step 3. Only deviations from the plan above, each with its reason. "Built as
> planned" is a complete and good entry. On a fix round, also the reproduction test's red
> run, pasted here before the fix was written — step 6 cites it.

Built as planned. Notes: `main()` of `optimize.py` gained a third case with a premium of 0.5; `STRUCTURE.md`'s test descriptions and the existing tests' `optimize` calls (6 call sites in `tests/test_ev_optimize.py`, mypy reports them) are left for step 5, as the plan's step 7 of the guide is test work.

---

## 4. Verification log

> Written in step 4: the static half. Command output, not a summary of it.

| Check | Result |
|---|---|
| `ruff check .` | All checks passed! |
| `ruff format --check .` | 68 files already formatted |
| `mypy` | Success: no issues found in 35 source files (after threading `0.0` through the 6 direct `optimize` calls in `tests/test_ev_optimize.py`: `_share`, `run`, the empty-demands call, the naive-now/unknown-zone call, and the two generated-snapshot calls; no assertion changed) |
| `pytest` | 1840 passed, 3 failed: `test_snapshot_to_dict_has_exactly_round_1_keys`, `test_the_dataclass_fields_are_round_ones_and_the_demands[Settings-fields1]`, `test_the_optimiser_and_capacity_signatures[optimize-names0]`. All three pin the old surface (the new field/parameter is the extra item); left for step 5 per the brief |
| Plan completeness | every signature in the Public API table exists as written: `Settings.waiting_premium_per_hour: float = 0.0` (last field), `optimize(..., timezone: str, waiting_premium_per_hour: float)` required, `decide` unchanged and passes it on, codec key in `_SETTINGS_FIELDS` and `snapshot_to_dict`. No missing, deviating or unplanned surface |
| `STRUCTURE.md` | in sync; structure-auditor's three source drifts applied (Settings row, optimize row, cost-level sentence, `main` row); the four test-file descriptions are step 5's |
| `python -m <package>.<module>` | the eight `ev` modules run with exit 0; `optimize`'s showcase prints the third case (premium 0.5), in the required form (named inputs, call, print). `RuntimeWarning` on re-exported modules is expected |

---

## 5. Test log

> Written in step 5: the dynamic half.

| Intent | Test names | Result |
|---|---|---|
| T1 | existing suite unchanged apart from the three surface-pinning tests (`test_snapshot_to_dict_has_exactly_round_1_keys`, `test_the_dataclass_fields_are_round_ones_and_the_demands[Settings]`, `test_the_optimiser_and_capacity_signatures[optimize]`), each updated to include the new field/parameter, the no-defaults assertion kept; `test_decide_a_zero_premium_decides_exactly_as_one_left_out`; `test_optimize_a_premium_of_minus_zero_is_the_raw_path`; one-off differential below | pass |
| T2 | `test_optimize_a_premium_flips_now_against_later_at_the_a2_boundaries` (10 cases: 8 and 96 quarters at +1.5%/+2.5% and 10%/30%, premium 0 later in all four, two break-even ties keep now); `test_optimize_the_first_quarter_is_never_weighted`; `test_optimize_a_premium_counts_quarters_from_the_one_containing_now`; through `decide`: `test_decide_a_premium_moves_a_planned_car_between_now_and_later_by_the_a2_numbers` (8 cases, `min_block_quarters=1`, a price on every quarter; the 96-quarter cases run on the 25-hour fall-back day because a ready time is under 24 h away) | pass |
| T3 | `test_optimize_non_positive_prices_are_not_weighted_and_decide_alike_at_every_premium` (2 gaps x 3 premiums x 5 sign cases); `test_optimize_a_plan_over_non_positive_quarters_is_the_same_at_any_premium`; `test_optimize_a_later_negative_price_is_not_pushed_down_by_the_weight` (fails if negatives are weighted); `test_optimize_a_tiny_positive_premium_leaves_the_exact_path_with_the_same_plan` | pass |
| T4 | `test_optimize_without_a_planned_demand_ignores_the_premium`; `test_optimize_with_planned_cars_only_they_and_the_spare_car_move_with_the_premium` (smart-off and charge-now fixed, spare = room minus the planned car's quarter-0 current, the planned car moving 0 to 16 A); `test_optimize_planned_cars_still_share_the_first_quarter_evenly_at_a_premium`; `test_optimize_two_cars_with_different_deadlines_weigh_the_same_horizon_index`; `test_decide_a_snapshot_without_a_planned_car_decides_the_same_at_any_premium`; `test_decide_with_planned_cars_the_other_tiers_keep_their_allocations` | pass |
| T5 | `test_optimize_the_price_cap_is_judged_on_raw_prices_whatever_the_premium`; `test_optimize_a_quarter_above_the_cap_still_counts_as_above_it_when_forced`; `test_optimize_a_weight_that_pushes_a_price_over_the_cap_does_not_exceed_it`; `test_decide_the_cap_event_and_planned_prices_use_raw_prices_at_a_premium`; `test_decide_the_cap_event_still_fires_on_a_raw_price_above_it_at_a_premium` | pass |
| T6 | `test_settings_refuse_a_waiting_premium_outside_zero_to_one_naming_it` (16 values); `test_settings_accept_a_waiting_premium_from_zero_to_one_stored_as_float`; `test_settings_waiting_premium_defaults_to_zero_and_is_the_last_field`; `test_settings_waiting_premium_is_frozen`; `test_settings_name_an_earlier_bad_field_before_a_bad_premium`; `test_settings_waiting_premium_error_names_the_value`; `test_optimize_rejects_a_bad_premium_naming_it` (9 values, `ValueError` with `match=`); `test_optimize_accepts_any_finite_non_negative_premium_even_above_one`; `test_optimize_checks_the_premium_after_the_empty_and_repeated_id_checks` | pass |
| T7 | `test_the_waiting_premium_round_trips_through_dicts_and_json_text`; `test_the_waiting_premium_is_in_the_dict_as_zero_when_unset`; `test_an_omitted_waiting_premium_decodes_as_zero`; `test_an_integer_waiting_premium_decodes_as_a_float`; `test_a_bad_waiting_premium_is_refused_with_its_dotted_path` (12 values, null included: `reader.given` does refuse null, as planned); `test_a_misspelt_waiting_premium_key_is_still_an_unknown_field`; `test_the_decision_dict_gains_no_key_from_the_waiting_premium` | pass |
| T8 | `test_optimize_a_premium_decision_is_deterministic_and_does_not_mutate_inputs`; `test_optimize_a_premium_leaves_the_solver_options_as_before`; `test_optimize_a_premium_solves_on_the_exact_path_when_the_weighted_prices_share_a_grid` and `..._tolerance_path_without_a_tick_grid` (a spy on `_price_ticks` shows each path taken); `test_decide_passes_the_waiting_premium_on_to_optimize` (spy); `test_decide_a_premium_decision_does_not_depend_on_the_hash_seed` (seeds 0, 1, 12345); `test_decide_a_premium_decision_is_the_same_on_repeated_runs`; `test_decide_a_premium_calls_the_solver_with_the_same_options`; `test_decide_a_premium_decision_survives_the_codec_round_trip`; `test_optimize_a_premium_keeps_the_earlier_quarter_on_flat_prices`; `test_optimize_a_premium_survives_a_48_hour_horizon_of_large_prices` | pass |
| T9 | `test_optimize_a_premium_weights_the_utc_index_across_the_fall_back_night`; `test_decide_a_premium_weights_the_utc_quarter_across_the_fall_back_night` (the same +1.5% case that wins now by the UTC index, 8 quarters, and would lose by wall-clock hours, 4) | pass |
| T10 | `level_tuple` takes `premium` and weights P5's cost by `p * (1 + premium * t / 4)` for `p > 0`; `test_optimize_matches_a_brute_force_enumeration_level_by_level` now draws the premium from {0, 0.01, 1.0} and prices from {0.1, 0.1002, 0.11, 0.2, 0.5, 0.0, -0.1}; the general oracle rarely meets a plan the premium moves (mutating `optimize` to ignore the premium passed it), so `test_optimize_weighted_plan_equals_the_enumeration_where_the_premium_flips_plans` (small needs, a price on every quarter, ample room, one or two cars of different deadlines) was added and fails under that mutation | pass |

Mutation checks run by hand against the source, each caught: the premium ignored by `_stage_plan`, by `decide`, quarter 0 weighted as `t + 1`, the hours per quarter taken as 1, the premium path entered only above 0.5, negatives weighted.

**T1 differential (one-off, not in the suite).** `origin/main` (`2c188c5`) in a temporary git worktree under `/tmp`, this branch in the working tree; 60 000 snapshots from the seeded generator `random_snapshot(random.Random(seed))` of `tests/test_ev_decide.py`, seeds 0 to 59 999, each decided on the branch and, from the same dict, on `main`; decisions compared as `decision_to_dict` JSON (sorted keys):

| Group | Seeds | Premium on the branch | Same | Different |
|---|---|---|---|---|
| omitted (key deleted from the dict) | 0 to 29 999 | omitted (decodes as 0.0) | 30 000 | 0 |
| 0.0 (set explicitly on `Settings`) | 30 000 to 59 999 | 0.0 | 30 000 | 0 |

Total 60 000 of 60 000 identical (3 of them were a `SolverError` on both sides, with the same message). The worktree was removed afterwards. Also, 600 further snapshots at a premium of 1e-12 against 0 gave identical decisions (no continuity break observed, so nothing for `DEVELOPMENT.md`).

Result: `ruff check .`, `ruff format --check .`, `mypy` clean; `pytest` 1998 passed.

Bugs found: none. The code and the plan agree that `reader.given` refuses a null premium (tested), and `optimize` raises a bare `ValueError` (tested with `match=`).

Edge cases considered and deliberately skipped, with reasons:

- A zero price being "strictly unweighted" as separate from the `p > 0` test: `0 * (1 + x)` is 0, so `>` against `>=` cannot be told apart by any plan; zero is in the non-positive cases anyway.
- `numpy.int64` and `Decimal` as an `optimize` premium (refused by the `int | float` check) against `Settings`' `_number`: the two layers' type tolerance is not part of the concept; `np.float64` is tested as accepted.
- An unpriced middle quarter keeping its horizon index: no discriminating hand case was found (the index comes from the cell tuple); covered by the oracle, whose prices are missing on 30% of quarters.
- A run of the 3/7/10 charger timing seeds at a premium (plan Risk "Speed"): not run; the differential's 60 000 premium-0 decides took about 15 minutes per 15 000 on a shared core and the premium-0 timing tests are unchanged. Left for step 8 if the owner cares.
- The deadline-relative reading of the horizon index for two cars: identical by construction (every window starts at now), so the hand test and the oracle's two-car cases stand in for it.

---

## 6. Concept check

> Written in step 6, against section 1 — not against section 2. The question is whether
> the thing built is the thing agreed, not whether it matches the plan.

| # | Criterion | Met | Evidence |
|---|---|---|---|
| A1 | yes | Full suite on the branch: 1998 passed (`python -m pytest`), ruff and mypy clean. Section 5's differential: 60 000 of 60 000 decisions identical to `main` (30 000 field omitted, 30 000 at 0.0). Re-checked here: 150 seeds (0 to 149) through `decide` on a `main` worktree and on the branch with the key deleted, 150 of 150 identical. Code: at premium 0 `_minimise_price` builds the raw list exactly as before (`optimize.py` `if premium > 0 ... else`). |
| A2 | yes | Direct probe through `optimize` (one-quarter need, `min_block_quarters=1`, price on every quarter, w = 0.01): 8 quarters, later +1.5% gives now, +2.5% gives later; 96 quarters, later 10% lower gives now, 30% lower gives later; at w = 0 all four give later. Tests: `test_optimize_a_premium_flips_now_against_later_at_the_a2_boundaries`, `test_decide_a_premium_moves_a_planned_car_between_now_and_later_by_the_a2_numbers`. |
| A3 | yes | Only `p > 0` is weighted (`optimize.py` `_minimise_price`). Probe: later more negative wins at w = 0.01 as at 0 (and the less negative later quarter loses at both); zero now beats positive later at both. Tests: `test_optimize_non_positive_prices_are_not_weighted_and_decide_alike_at_every_premium`, `test_optimize_a_later_negative_price_is_not_pushed_down_by_the_weight`; step 5 mutation "negatives weighted" caught. |
| A4 | yes | Weight reaches only `_minimise_price` via `_stage_plan`; `Demand` and the quarter-0 tier code untouched in the diff. Tests: `test_optimize_without_a_planned_demand_ignores_the_premium`, `test_optimize_with_planned_cars_only_they_and_the_spare_car_move_with_the_premium`, `test_optimize_planned_cars_still_share_the_first_quarter_evenly_at_a_premium`, `test_decide_a_snapshot_without_a_planned_car_decides_the_same_at_any_premium`. |
| A5 | yes | Weighted values live only inside `_minimise_price`; P3 cap, `_session_plan` and `PlannedQuarter.price` read raw prices (diff shows no other consumer). Tests: `test_optimize_the_price_cap_is_judged_on_raw_prices_whatever_the_premium`, `test_decide_the_cap_event_and_planned_prices_use_raw_prices_at_a_premium`. |
| A6 | yes | `Settings` refuses negative, NaN, inf, bool, above-1.0 naming the field (`test_settings_refuse_a_waiting_premium_outside_zero_to_one_naming_it`, 16 values); codec round trip and omitted key decoding as 0.0 (`test_the_waiting_premium_round_trips_through_dicts_and_json_text`, `test_an_omitted_waiting_premium_decodes_as_zero`); determinism across hash seeds and repeats and unchanged solver options (`test_decide_a_premium_decision_does_not_depend_on_the_hash_seed`, `test_decide_a_premium_calls_the_solver_with_the_same_options`). All in the 1998 passing. |

Drift found, and what was done about it: none. Out of scope respected (interruptible, `common`, other tiers, `Demand`, tick detection and tolerance path untouched; no per-charger weight). Connections: `Settings` to `decide` to `optimize` to P5, plus codec. Surface: exactly one new field and one new required `optimize` parameter. Showcase: `python -m energy_optimization.ev.optimize` runs, prints the new premium 0.5 case in the named-inputs form. STRUCTURE.md: structure-auditor reported it matching; spot-checked Settings, optimize, codec and test descriptions.

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
| Commits | `70193e1` Concept: charge-early weight; `6753dde` Plan: charge-early weight (draft); `4eda09a` Plan: apply critic findings 4 and 5 (draft); `7b8636f` Plan: apply critic findings; premium on positive prices only; `ef67081` Plan accepted: charge-early weight; `006c7b4` Add waiting premium to planned-tier pricing; `cd74f2a` WIP step 4: verify, optimize call sites and STRUCTURE.md; `bdd5c39` WIP step 5: waiting-premium tests; `43564c9` Test: charge-early weight; `7d764df` Concept check: charge-early weight; plus the final `Ship: Charge-early weight` commit. Steps 1 to 6 each left a commit; none missing. Diff review: 10 files (4 source, 4 test, STRUCTURE.md, plan file), no scratch files or data. Gates on the whole tree: ruff check, ruff format --check, mypy clean; `python -m pytest` 1998 passed. |
| Pushed to | `origin/feat/charge-early-weight` |

---

## 8. Recommendations

> Written in step 8. Only follow-ups that are critical and belong to this work, which most
> rounds do not have: replace the table with `None.` when there are none. Lesser ideas are
> one-line notes in `DEVELOPMENT.md`, not rows here. Not bugs in what this round built —
> those go back through `/build` before the pull request. A critical defect outside what
> section 1 promised, such as a class member it put out of scope, is a recommendation here,
> and its round opens through `/fix`.

None.

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
