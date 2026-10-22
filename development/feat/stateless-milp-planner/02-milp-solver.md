# MILP solver

<!-- claude-plan step=3 status=active -->


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
| 2 | Plan | `/plan` | with the user | done |
| 3 | Implement | `/implement` | in `/build` | in progress (halted again at guide item 8, tight-budget tier) |
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
returns within 10 s in total (amended at the step-3 halt: 10 s up to 3 chargers, 60 s up to 7, 120 s up to 10).

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
| A5 | **Is deterministic and bounded.** The same snapshot gives an equal decision across runs and hash seeds; the solve uses a single thread and a fixed node budget, never a wall-clock limit; every call with a 48-hour price series returns, in total on the development machine, within 10 s for up to 3 chargers, 60 s for up to 7 and 120 s for up to 10 (amended at the step-3 halt, 2026-10-22); a solve stopped by the node budget returns its best plan with an event saying it is not proven optimal. |
| A6 | **Raises instead of guessing.** When HiGHS reports an error or finds no solution, `decide` raises `SolverError` naming the solver status, never a partial or greedy decision. |
| A7 | **Removes the greedy planner and declares its dependencies.** The greedy planning and sharing code is gone from the package and its public API; scipy and numpy are runtime dependencies, and nothing else outside the standard library is imported; `STRUCTURE.md` matches. |
| A8 | **Changes nothing else.** The snapshot and decision dict keys, statelessness, UTC arithmetic and input validation are unchanged. Round 1's scenarios still pass, except where optimality, the leftover rule or the even split gives a different answer; a differential against round 1 of this branch (commit `2646e7c`) over generated snapshots shows every difference is one of those, each kind pinned by a test. |

### Open questions

None.

---

## 2. Plan

Accepted by the user in advance, 2026-10-22: "when the plan and plan critique is done just
start the implementation straight away". Step 3 opens once the `plan-critic` findings below
are applied.

### Survey

- `decide.py` classifies every charger and serves them in this order:
  - reservations (`allocation.reserved_current`);
  - smart-off chargers through `allocation.share`;
  - tier-2/4 candidates planned by `planning.plan_sessions` against `plan_cap` (the budget
    minus reservations and smart-off, the same in every quarter);
  - then **only the highest occupied tier** shares quarter 0 through `share` against `cap`
    (the budget ∩ the phase guard or fallback, minus reservations and smart-off).
- In round 1 a tier-2 charger's quarter-0 allocation is its `share`, up to `eff_max`. It is
  not the plan's quarter-0 current.
- Charge-now (tier 1) and spare-capacity (tier 3) sessions have no plan.
- `planning.py`:
  - `remaining_need_kwh`, `energy_per_quarter_kwh`, `NeedSource` and `SessionPlan` are not
    greedy and stay.
  - `PlanRequest`, `plan_sessions` and the private block selection are greedy and go.
- `allocation.py`: `Claim` and `share` are greedy and go. `measurements_fresh`,
  `reserved_current`, `present_draw` and `phase_capacity` stay.
- `quarters.py`: everything is reused. `quarter_starts` gives the horizon and
  `quarter_prices` the UTC-keyed prices.
- `codec.py`: untouched. A new `EventKind` member encodes by value.
- `pyproject.toml`: `dependencies = []`. Mypy already has `ignore_missing_imports = true`,
  so scipy needs no stubs.
- Spike (scratch venv, scipy 1.18.1):
  - `scipy.optimize.milp` accepts `node_limit`, `mip_rel_gap`, `presolve` and
    `time_limit`.
  - Any other key, `threads` included, is passed to HiGHS verbatim with a `RuntimeWarning`
    ("Unrecognized options detected"). So `threads=1` is reachable through the public API.
  - There is no `random_seed` key; HiGHS's default seed is fixed.
  - A stop at the node limit comes back as `status == 4` with the message
    "The HiGHS status code was not recognized. (HiGHS Status 16: Solution limit reached)".
    `x` is present and `mip_node_count == node_limit`. The test was an 80-variable,
    8-row knapsack with node limits of 1, 3 and 10. On easy instances the solve is proven at
    the root whatever the limit.

### Approach

**Chosen: one new module, `optimize.py`, holding one model with a lexicographic objective.**
`decide` keeps everything before the solve and after it. In between it hands one list of
demands to `optimize`, one per charger the solver may give current to, and reads back
allocations and plans.

The concept's precedence is a strict order of eleven preferences:
1. smart-off amps;
2. smart-off even split;
3. charge-now amps;
4. charge-now even split;
5. shortfall;
6. unpriced energy;
7. energy above the cap;
8. short runs;
9. price;
10. planned-tier even split;
11. tie-break.

The spare-capacity tier's amps, even split and ranking come last.

That order is solved **lexicographically**: each level is minimised with every earlier
level held at its optimum. A single weighted sum would have to span the whole order with
weight gaps large enough that each level dominates everything after it. That needs
coefficients around 10²⁰ apart, which HiGHS's 10⁻⁷ tolerances and 10⁻⁴ default gap cannot
honour.

The quarter-0-only tiers (smart-off, charge-now, spare) touch only their own quarter-0
variables. So their levels are solved on a small quarter-0 model before and after the
planning model, with the results fixed between stages. This is the same lexicographic
optimum, found more cheaply.

It is one program with a lexicographic objective, solved level by level. The quarter-0
sub-models are exact because their variables appear in later rows only through
capacities. Clipping both capacities at 0 makes those rows slack: `now_capacity ≤
capacity` holds per phase, because `now_capacity` is the budget ∩ the guard, so leaving
constraint 2 out of the quarter-0 stages loses nothing.

Within each quarter-0 tier, the split reproduces round 1's `share` rule as solver levels.
First comes inclusion in ranking order: each demand at its minimum if it fits after the
ones ranked above it. Then max-min-fair progressive filling. Then the remainder in ranking
order. So smart-off and charge-now stay as round 1 left them, and spare capacity follows
the same rule on the leftover.

**Rejected:**
- *Weighted single solve.* Numerically unsound, for the reason above. It would also make
  A2's "optimal for the stated precedence" unprovable.
- *Keeping `share` for quarter 0 and using the MILP only for planning.* The user chose
  "planning plus current-quarter allocation" and the fair-then-cost split, and A7 removes
  the greedy sharing.
- *Using `highspy` directly for `threads` and `random_seed`.* That adds a third dependency
  the concept did not agree, when the public `milp` already reaches both.

### Modules

| Path | New or changed | Purpose |
|---|---|---|
| `src/energy_optimization/ev/optimize.py` | new | The MILP: demands in, allocations and plans out; lexicographic levels; node budget; `SolverError` (A1–A3, A5, A6) |
| `src/energy_optimization/ev/decide.py` | changed | Builds demands from the classification, calls `optimize`, maps the outcome to priorities, reasons, status and events; leftover rule (A3, A4, A8) |
| `src/energy_optimization/ev/planning.py` | changed | Greedy planner removed; keeps `NeedSource`, `remaining_need_kwh`, `energy_per_quarter_kwh`, `SessionPlan` (A7) |
| `src/energy_optimization/ev/allocation.py` | changed | `Claim` and `share` removed; the rest unchanged (A7) |
| `src/energy_optimization/ev/result.py` | changed | `EventKind.PLAN_NOT_PROVEN_OPTIMAL` (A5) |
| `src/energy_optimization/ev/__init__.py` | changed | Re-exports `SolverError` (A6) |
| `pyproject.toml` | changed | `dependencies = ["numpy>=2.2,<3", "scipy>=1.15,<2"]` (A7) |
| `tests/test_ev_optimize.py` | new | The model against brute force, every limit, levels, budget, failure (A1, A2, A5, A6) |
| `tests/test_ev_decide.py`, `test_ev_planning.py`, `test_ev_allocation.py`, `test_ev_result.py` | changed | Greedy and `share` tests removed with their code; round-1 scenarios kept, with every changed expectation listed in section 5 with its A8 kind; the stdlib AST scan and 100 ms test amended (A7, A5, A8) |
| `STRUCTURE.md`, `README.md` | changed | New module, removed names, the dependency note ("standard library only" becomes "standard library plus numpy and scipy"); README install line if it says stdlib only (A7) |

### Public API

New, in `optimize.py`:

| Signature | Module | Purpose | Covers |
|---|---|---|---|
| `class SolverError(RuntimeError)`; `__init__(self, status: int, message: str) -> None`; attributes `status`, `message`; `str(err) == f"HiGHS status {status}: {message}"` | `optimize` | The solver failed; carries scipy's `milp` status and message | A6 |
| `NODE_LIMIT: int` | `optimize` | Branch-and-bound nodes allowed per level solve. Fixed by step 3's benchmark as the largest power-of-two multiple of 1000 that keeps every A5 tier (3 chargers ≤ 10 s, 7 ≤ 60 s, 10 ≤ 120 s) inside its bound, with the measurement recorded in section 3 | A5 |
| `class Tier(IntEnum)`: `SMART_OFF = 0`, `CHARGE_NOW = 1`, `PLANNED = 2`, `SPARE = 3` | `optimize` | Which precedence stage a demand belongs to | A3 |
| `Demand(charger_id: str, phases: frozenset[Phase], tier: Tier, min_current: float, max_current: float, now_max_current: float, need_kwh: float = 0.0, deadline: datetime \| None = None)` | `optimize` | One charger asking for current, given in ranking order. `min_current == max_current` is all-or-nothing (on/off). `now_max_current` caps quarter 0 (user limit, fallback), `max_current` the later quarters. Quarter-0-only tiers ignore `need_kwh`, `deadline` and `max_current`. `PLANNED` requires an aware `deadline`. Validates itself in `__post_init__`, raising `ValueError` naming the field. Currents must be finite and ≥ 0. `max_current` or `now_max_current` below `min_current` is allowed and means off in those quarters, which keeps the `user_current_limit=0` regression and its `TARGET_UNREACHABLE`. `need_kwh` must be finite and ≥ 0. `PLANNED` requires an aware `deadline` | A1, A3 |
| `Outcome(allocations: dict[str, float], plans: dict[str, SessionPlan], proven: bool)` | `optimize` | Quarter-0 current for every demand; a plan for every `PLANNED` demand; `proven` False when any level stopped at the node budget | A1, A5 |
| `optimize(demands: Sequence[Demand], now: datetime, prices: Mapping[datetime, float], capacity: Mapping[Phase, float], now_capacity: Mapping[Phase, float], voltage: float, min_block_quarters: int, price_cap: float \| None, timezone: str) -> Outcome` | `optimize` | Solve the lexicographic model below. `capacity` holds per phase in quarters 1 onward, before `SMART_OFF` demands, which keep their quarter-0 current there. `now_capacity` holds in quarter 0. Prices are keyed by UTC quarter start, and a missing quarter is unpriced. Negative capacities count as 0. With no demands it returns `Outcome({}, {}, True)` without solving. Raises `SolverError` (A6), and `ValueError` for a naive `now` or a repeated charger id | A1–A3, A5, A6 |
| `main() -> None` | `optimize` | Showcase: two planned cars on a shared phase, a charge-now car taking quarter 0 first, then a spare-capacity car on the leftover | — |

Added to `result.py`: `EventKind.PLAN_NOT_PROVEN_OPTIMAL = "plan_not_proven_optimal"`
(A5). Added to `ev.__all__`: `SolverError`.

Removed: `PlanRequest` and `plan_sessions` from `planning.py`, and `Claim` and `share` from
`allocation.py` (A7).

#### The model (what `optimize` solves; step 3 transcribes it)

Notation:
- Let `Q` be the UTC quarter starts from `floor_to_quarter(now)`.
- For a `PLANNED` demand `d`, the window `W_d` is the quarters before its `deadline`
  (`quarter_starts(now, deadline)`). For every other tier, `W_d = {0}`.
- `e_d = energy_per_quarter_kwh(1, |phases_d|, voltage)` is kWh per amp-quarter.
- `T = |Q|` is the union horizon, up to the latest deadline, and at least quarter 0.
- Capacities are clipped first: `max(0, ·)` per phase, for both `capacity` and
  `now_capacity`.
- `hi_{d,0} = now_max_current` and `hi_{d,t≥1} = max_current`. Where `hi_{d,t} < min_current`
  the charger is off in that quarter.

Variables for every `d` and every `t ∈ W_d`:
- `x_{d,t} ∈ {0,1}`.
- `i_{d,t}`: an integer when `min_current < max_current`, otherwise fixed to
  `min_current · x_{d,t}`.
- `PLANNED` demands also get a shortfall `s_d ≥ 0` and a start `y_{d,t} ∈ [0,1]`.
- The quarter-0 stages' fill variables are described with their levels below.

Constraints:
1. `min_current · x ≤ i ≤ hi · x`.
2. Per phase `p` and quarter `t ≥ 1`: the sum of `i_{d,t}` over `PLANNED` demands on `p`,
   plus the sum of `i_{d,0}` over `SMART_OFF` demands on `p`, is at most `capacity[p]`.
3. Per phase `p` in quarter 0: the sum of `i_{d,0}` over every demand on `p` is at most
   `now_capacity[p]`.
4. For each `PLANNED` demand: `Σ_t e_d·i_{d,t} + s_d ≥ need_kwh`, and
   `Σ_t e_d·i_{d,t} ≤ need_kwh + e_d·max_current`, so negative prices never overfill a car
   by more than one quarter.
5. Starts and minimum block: `y_{d,t} ≥ x_{d,t} − x_{d,t−1}` (with `x_{d,−1} = 0`), and
   `x_{d,t+k} + w_{d,t} ≥ y_{d,t}` for `k = 1..L−1` while `t+k ∈ W_d`, with `w_{d,t} ≥ 0`.
   The block is soft. A run that the deadline cuts short is not penalised.
6. Planned even split (level P6 only): once level P5 has fixed `x_{·,0}`, add
   `z ≤ i_{d,0}` for every `PLANNED` demand with `x_{d,0} = 1`. A car that is off adds no
   bound, so switching a car off can never raise `z`.

Levels run in this order. Each is minimised with every earlier level held at its optimum.
A level with no non-zero coefficient, or no demand of its tier, is skipped.

**Quarter-0 stage**, run for `SMART_OFF`, then `CHARGE_NOW`, before planning, and for
`SPARE` after it. Each run uses the room left in quarter 0, and `SMART_OFF` also the room in
the later quarters. `r_d` is the demand's position within its tier, and `m` the tier's
size.

| # | Solve | Meaning |
|---|---|---|
| S1 | Maximise `Σ 2^(m−1−r_d)·x_{d,0}` with `i_{d,0} = min_current·x_{d,0}`, then fix `x_{·,0}` | Ranking-order inclusion at the minimum: each demand is in if it fits after those ranked above. This is round 1's 0/min selection |
| S2 | Progressive filling over the included demands. Repeatedly maximise an integer `f` with `i_{d,0} ≥ min(f, hi_{d,0})` for the unsaturated ones, holding every earlier result. After each round, mark saturated every demand that is at its `hi` or on a phase with no room left at `f + 1`. Stop when all are saturated | Max-min-fair 1 A steps, as round 1's `share` |
| S3 | Maximise `Σ (m − r_d)·i_{d,0}` with everything above held | The remainder goes in ranking order |

**Planning stage**, for the `PLANNED` demands, on the room both stages above left:

| # | Minimise | Meaning |
|---|---|---|
| P1 | `Σ s_d` | Meet each need |
| P2 | `Σ e_d·i_{d,t}` over unpriced `t` | Avoid unpriced quarters |
| P3 | `Σ e_d·i_{d,t}` over `t` priced above `price_cap` | Use the cap only to meet the need |
| P4 | `Σ w_{d,t}` | Avoid runs shorter than the minimum block |
| P5 | `Σ π_t·e_d·i_{d,t}` over priced `t`; then maximise `Σ x_{d,0}` and fix `x_{·,0}` | Lowest price; among equally cheap plans, as many cars as possible on now |
| P6 | Maximise `z` (constraint 6) | Even split among the planned cars on now: a single max-min, accepted at this tier because iterating it inside the big model is costly |
| P7 | `Σ τ_{d,t}·i_{d,t}`, with `τ = (t+1)·(r_d+1)` for priced `t` and `τ = (T−t)·(r_d+1)` for unpriced `t`; `r_d` is the position among the `PLANNED` demands | Earlier priced quarters first; unpriced quarters latest first, as in round 1; the better-ranked car gets the earlier quarter. `τ` is not separable, so swapping two cars between quarters changes it |

The full run is S1–S3 for `SMART_OFF`, S1–S3 for `CHARGE_NOW`, P1–P7, then S1–S3 for
`SPARE`. Stages that have no demands are skipped.

Holding a level: after level `k` returns a value `v`, its row becomes `objective ≤ v + tol`
with `tol = 1e-6·max(1, |v|)`.

Every solve:
- Options are `{"node_limit": NODE_LIMIT, "mip_rel_gap": 0.0, "presolve": True, "threads": 1}`.
- The `threads` warning is suppressed in a `warnings.catch_warnings()` filtered to that
  message only. There is never a `time_limit`.
- `res.status == 0` is proven.
- `res.x` present, with either `res.status == 1` or (`res.status == 4` and
  `res.mip_node_count >= NODE_LIMIT`), is the best found at the node budget, so
  `proven = False`.
- Any other status, or `x` missing, raises `SolverError(res.status, res.message)`.
- Reading back, currents are rounded to the nearest whole amp for integer variables and
  taken as exact for fixed ones.
- `SessionPlan` is built from the solution:
  - quarters with `i > 0`, starts converted to `timezone`, each with its price (None when
    unpriced) and its current;
  - `planned_kwh = Σ e·i`;
  - `reachable = planned_kwh >= need_kwh − 1e-9`, computed from the rounded plan with the
    same epsilon as `decide`;
  - `price_cap_exceeded` = any planned quarter priced above the cap;
  - `uses_unpriced` = any planned quarter unpriced.

### Implementation guide

1. `pyproject.toml`: add the runtime dependencies, then
   `.venv/bin/pip install -e ".[dev]"`. Check `python -c "import scipy"`.
2. `result.py`: add `EventKind.PLAN_NOT_PROVEN_OPTIMAL` and update its docstring.
3. `optimize.py`:
   - First, re-run the survey's node-limit spike inside the project venv, and record
     `status`, `message` and `mip_node_count` in section 3. If it differs from the survey,
     adjust the not-proven rule to what HiGHS reports and record why. The rule's intent,
     best found at the budget = not proven, is fixed.
   - `SolverError`, `NODE_LIMIT` (provisional 4000, fixed at item 8), `Tier`, `Demand`
     with validation, `Outcome`.
   - A private model builder, one per stage, using `scipy.sparse` for the constraint
     matrix.
   - A private lexicographic driver: a list of objective vectors, held rows appended as it
     goes.
   - The read-back into `Outcome`, then `optimize`, then `main`.
   - Phases are iterated in `Phase` order and demands in the order given, so the matrix is
     built identically every call (A5).
4. `decide.py`, in place of the `share` / `plan_sessions` / tier block:
   - Build demands in ranking order (`_rank_key`):
     - `SMART_OFF` from smart-off chargers that can run;
     - `CHARGE_NOW` from priority-1 items that can run;
     - `PLANNED` from priority-None smart items with need > 0 and a deadline after now,
       even when they cannot run in quarter 0 (`now_max_current = eff_max`, which may be
       below the minimum, so quarter 0 is off);
     - `SPARE` from priority-3 items that can run.
   - `capacity` is `plan_cap` after reservations only, clipped at 0 (smart-off is now inside
     the model). `now_capacity` is `cap` after reservations, clipped at 0.
   - Call `optimize` with `quarter_prices(snapshot.prices)` when prices are fresh, and with
     `prices={}` when they are stale. With stale prices there are no `PLANNED` demands,
     because classification already makes those items priority 3 (A4).
   - Allocation is the outcome's quarter-0 current. A `PLANNED` item is priority 2 when its
     plan's first quarter is the current one with current > 0, otherwise priority 4.
     `starved` is as in round 1.
   - Final codes:
     - `BELOW_MINIMUM` as now;
     - `WAITING_FOR_CAPACITY` for starved items;
     - `PLANNED_LATER` for priority 4;
     - for a tier-1 or tier-3 item at 0 A, `WAITING_FOR_HIGHER_PRIORITY` if a
       higher-precedence item (a smart-off charger or a lower tier number) on one of its
       phases got current, otherwise `WAITING_FOR_CAPACITY`.
   - `_status`:
     - `active` is every can-run item of tiers 1–3;
     - "short" means the allocation is below `eff_max` **and** quarter-0 room left on one of
       its phases is under `max(1, min_current)` A. The room is the clipped `cap` after
       reservations, minus every quarter-0 allocation. A planned car the plan deliberately
       runs below its maximum is not "limited".
     - The rest is unchanged.
   - `_capped_by` uses the same "short" test, written once as a private helper shared with
     `_status`. A charger that is not short gets no `"phase"`/`"budget"` cap. The
     `"user_limit"` and `"fallback"` branches are unchanged.
   - `_events`: add `PLAN_NOT_PROVEN_OPTIMAL` (no charger id) when `outcome.proven` is
     False, after the existing events.
   - Delete `_claim`, and `_plan_requests` if nothing still uses it.
   - Update the module docstring.
5. `planning.py` and `allocation.py`: delete the greedy code, its private helpers and its
   showcase parts. Rewrite both `main()`s for what remains.
6. `__init__.py`: export `SolverError`. Update its docstring.
7. `tests/`:
   - Delete the tests of the removed names.
   - Make the restored suite pass.
   - Where a round-1 expectation changes, change it and list it in section 3 with its kind.
     The kinds are fixed here:
     - **optimality**: any change that follows from the MILP plan, including a planned car's
       quarter-0 allocation becoming the plan's current (A1);
     - **leftover**: a lower tier now receiving current, and the reason-code, `_status` and
       `capped_by` changes that follow from that or from the line above. The
       `assert len(served) <= 1` check in `assert_invariants` becomes a tier-precedence
       invariant;
     - **even split**: a different split within a tier.
   - The surface tests A5–A7 require to change are updated, not classified:
     - `test_the_package_exports_exactly_round_ones_names` (+`SolverError`);
     - `test_the_enums_carry_no_round_two_members` and the exact enum sets in
       `test_ev_result.py` (+`PLAN_NOT_PROVEN_OPTIMAL`);
     - `test_the_dataclass_fields_are_round_ones` and
       `test_the_planner_and_capacity_signatures_are_round_ones` (`PlanRequest` and
       `plan_sessions` removed).
   - The stdlib AST scan allows exactly `numpy` and `scipy` besides the standard library.
   - The 100 ms test becomes the A5 tier tests: 3 chargers within 10 s, 7 within 60 s, 10 within 120 s (the 7- and 10-charger cases may carry a `slow` marker if the repo has one; they are not skipped).
8. Benchmark the A5 worst cases on a fixed set of seeds (at least 5) at 3, 7 and 10 chargers, each against its tier (10 s, 60 s, 120 s): chargers on mixed
   phases, a 48-hour series with an unpriced tail, `min_block_quarters = 4`, a price cap
   and a tight budget. Record the maximum time over the seeds. Fix `NODE_LIMIT` and record the runs in section 3.
9. `STRUCTURE.md` and `README.md`. Run the four gates and every showcase.

### Test intents

| # | Must prove | Covers |
|---|---|---|
| T1 | `optimize` never breaks a limit. The property test over generated demands and capacities checks every quarter: 0 or min..hi in whole amps, on/off exact, per-phase `capacity` (with smart-off) and `now_capacity`, nothing at or after a deadline, the allocation equal to the plan's first-quarter current. The same invariants hold through `decide` on generated snapshots, computed independently of the module | A1 |
| T2 | Brute force: on small instances (≤ 2 demands, ≤ 6 quarters, ≤ 3 current levels) the lexicographic tuple of every level equals an exhaustive enumeration's best tuple. Tuples only: equal tuples with different plans are not a finding | A2 |
| T3 | Hand-checked plans through `decide`: the cheapest quarters, the cap broken only for the ready time (with its event), an unreachable target (event with reachable kWh), unpriced quarters used only when needed and latest first, minimum blocks honoured unless the deadline cuts them | A2 |
| T4 | The tiers on the leftover: smart-off, then charge-now, then planned, then spare. A lower tier gets current only from what is left, and never at a higher tier's cost (a case per pair of tiers). Within a tier the split is even where cost is indifferent, with the remainder going in ranking order. Reason codes `WAITING_FOR_HIGHER_PRIORITY` and `WAITING_FOR_CAPACITY` behave as defined above. Named cases:
- two 6–16 A smart-off chargers on 16 A give 8 and 8;
- on/off 10 A (ranked first) with a 6–16 A charger on 16 A gives 10 and 6;
- A on L1+L2, B on L1 and C on L2, each phase at 16 A, give 8, 8 and 8;
- A limited to 8 A with B and C at 6–16 A on 30 A gives 8, 11 and 11;
- two planned cars at equal prices both run now, evenly;
- a planned car the plan runs below its maximum carries no `capped_by` and leaves the status `NORMAL`.

The quarter-0 tiers give `share`'s round-1 results on round 1's `share` test inputs | A3 |
| T5 | Stale prices: no plans, smart sessions served at priority 3 on quarter 0, round-1 status and event | A4 |
| T6 | Determinism: equal decisions across repeated calls and `PYTHONHASHSEED` values, and when `snapshot.chargers`, `states` and `sessions` are permuted through `decide` (`_rank_key` is a total order). Every one of the benchmark seeds (guide item 8) returns within its A5 tier: 3 chargers 10 s, 7 chargers 60 s, 10 chargers 120 s. The not-proven path works two ways: a monkeypatched `milp` returning a node-limit result (status 4, `x` present, `mip_node_count == NODE_LIMIT`), and a real hard instance with `NODE_LIMIT` monkeypatched to 1. Each gives a plan within every limit, `proven = False` and the `PLAN_NOT_PROVEN_OPTIMAL` event. No `time_limit` is ever passed (the solver call is spied) | A5 |
| T7 | `SolverError`: a monkeypatched `milp` returning each failure status raises `SolverError` with that status and message from `decide`. The statuses are 2, 3, 4 below the node limit, 4 without `x`, and 1 without `x`. Negative capacities (a reservation above the budget) never raise; they give a decision with 0 A. The `str` form, and the re-export from `energy_optimization.ev` | A6 |
| T8 | The removed names are gone (`plan_sessions`, `PlanRequest`, `share`, `Claim`). The AST scan finds only the standard library, numpy and scipy. `pyproject.toml` declares both | A7 |
| T9 | Unchanged surface: the codec key sets, statelessness, UTC (the DST finish tests) and validation tests all still pass. The differential against `2646e7c` over generated snapshots puts every difference into optimality, leftover or even split, and each kind has a named pinning test (step 6 runs the differential; step 5 writes the pins) | A8 |

**Coverage check.**
- A1: `optimize`, `Demand`, `Outcome`; T1.
- A2: `optimize`; T2 and T3.
- A3: `Tier`, `Demand`, `optimize`; T4.
- A4: `decide` (no new API); T5.
- A5: `NODE_LIMIT`, `Outcome.proven`, `PLAN_NOT_PROVEN_OPTIMAL`; T6.
- A6: `SolverError`; T7.
- A7: the removals and dependencies; T8.
- A8: the unchanged surface; T9.

Every public entry has a criterion behind it, and every criterion has an entry and a test.

### Risks

1. **An A5 tier bound (3 chargers 10 s, 7 chargers 60 s, 10 chargers 120 s) is not reachable with any node budget that still gives sensible
   plans.** The cause would be the planning model, at 10 chargers × 192 quarters, with up
   to 7 levels each spending its node budget. The build may then:
   - lower `NODE_LIMIT`;
   - skip empty levels;
   - tighten the formulation, for example with the aggregated block constraint
     `(L−1)·y ≤ Σ_k x + (L−1)·w`.

   If a tier's worst case still exceeds its bound, **halt**: A5 would need amending. Do not add a
   wall-clock limit (A5 forbids it).
2. **Determinism.** If repeated runs differ even with `threads=1`, **halt**. That would be a
   HiGHS property A5 cannot meet as written. Do not paper over it with a time limit or a
   sort.
3. **Rounding.** HiGHS returns integers as floats such as `15.9999999`. Round integer
   variables, then re-check the limits in the read-back. A violated limit after rounding is
   a bug to fix, not to clip.
4. **Ties.** P7 narrows ties but does not make the optimum unique: two plans can have
   equal level tuples. Equal tuples with different plans are expected and are not a
   finding. T2 compares tuples. A5's determinism rests on HiGHS being deterministic with one
   thread, not on a unique optimum.
5. **Round-1 tests whose values change.** The categories are fixed by A8 and defined in
   guide item 7, and so is the list of surface tests that change by A5–A7. A changed
   expectation that fits none of these is a halt, not an edit.
6. **Hypothesis property tests get slow with a solver in the loop.** Lower `max_examples`
   on the `decide`-level properties to keep the suite under a minute, and record the count
   in section 5. Do not drop a property.
8. **Mypy follows scipy's untyped source** (`follow_untyped_imports = true`). If step 4's
   mypy slows down or reports errors from inside scipy, add
   `[[tool.mypy.overrides]] module = ["scipy", "scipy.*", "numpy.*"]` with
   `follow_untyped_imports = false` to `pyproject.toml`, and record it in section 4.
7. **Charge-now cars are not reserved in later quarters.** This is round-1 behaviour, and
   the model keeps it: tier 1 lives only in quarter 0. It is listed in section 3 as kept,
   not changed.

### Critique

The `plan-critic` verdict was "accept with the changes". Every finding is applied:

1. **The even split rewarded switching chargers off, and a single `z` was not even.**
   Applied: the quarter-0 tiers are now S1 inclusion in ranking order, S2 progressive
   filling and S3 the ranking remainder, which is round 1's `share` rule. The planned tier
   gets P5's maximise-cars-on with `x` fixed, then `z ≤ i` over the cars that are on (a
   single max-min, accepted as stated). T4 names every counter-example.
2. **A node-limit stop is status 4, not 1.** Confirmed by a spike, now in the survey.
   Applied to the not-proven rule, to T6 and T7, and as guide item 3's re-check.
3. **Negative capacities made the model infeasible.** Applied: both capacities are clipped
   at 0 in `decide` and in `optimize`. T7 has the case.
4. **Risk 5 would have halted on surface tests that A5–A7 change.** Applied: guide item 7
   lists them as updated, not classified, and defines the three A8 kinds, including the
   plan's quarter-0 current (optimality) and the reason, status and `capped_by` changes
   (leftover).
5. **`_capped_by` was not updated with `_status`.** Applied: one shared "short" test, with a
   T4 case.
6. **The tie-break was separable and Risk 4 was false.** Applied: `τ = (t+1)·(r+1)`, and
   Risk 4 is reworded.
7. **Decisions the build would invent.** Applied:
   - (a) `Demand` validation rules are stated;
   - (b) stale prices pass `prices={}`, and no demands returns without solving;
   - (c) the wording is now "a repeated charger id";
   - (d) `T = |Q|`;
   - (e) `e_d` reuses `energy_per_quarter_kwh`.
8. **T6 was partly untestable.** Applied: a monkeypatched node-limit result alongside a real
   hard instance, permutations of the snapshot's sequences, and a fixed set of seeds for
   the benchmark.
9. **"One mixed-integer program" needed its argument.** Applied: the levels are listed in
   the order that runs, and the Approach says why the staged sub-models are exact.
10. **The `reachable` tolerance did not match `decide`.** Applied: it is computed from the
    rounded plan with `decide`'s `1e-9`.
11. **Mypy following scipy's source.** Applied as Risk 8, with its override.

---

## 3. Implementation notes

> Written in step 3. Only deviations from the plan above, each with its reason. "Built as
> planned" is a complete and good entry. On a fix round, also the reproduction test's red
> run, pasted here before the fix was written — step 6 cites it.

**State at the halt.** Guide items 1 to 6 are done in `src/`: dependencies installed,
`EventKind.PLAN_NOT_PROVEN_OPTIMAL`, `optimize.py`, `decide.py` wired to it, the greedy
code removed from `planning.py` and `allocation.py`, `SolverError` exported. Items 7 (tests)
and 9 (`STRUCTURE.md`, `README.md`) are **not** done, because item 8's benchmark halted the
build first (see **Halted**). `ruff`, `ruff format` and `mypy` on `src/` are clean; the old
tests still import removed names and fail to collect.

**Node-limit spike, re-run in the project venv** (scipy 1.18.1, HiGHS 1.12.0, 80-variable
knapsack, node limits 1, 3, 10): `status == 4`, `message == "The HiGHS status code was not
recognized. (HiGHS Status 16: Solution limit reached)"`, `mip_node_count == node_limit`,
`x` present, and the `threads` `RuntimeWarning`. It equals the survey, so the not-proven rule
is built as planned.

**Deviations from the plan** (the Public API table is unchanged by all of them):

1. *S3 is sequential, not one weighted solve.* The plan's `max Σ (m − r_d)·i_{d,0}` ties when
   one multi-phase car outweighs two single-phase ones (weights 3 against 2 + 1), so the
   "remainder in ranking order" was not strict. S3 now maximises each demand in ranking order,
   holding the earlier ones. The same for S1, which the plan already described as inclusion in
   ranking order (built as one level per demand rather than a `2^(m-1-r)` weighted sum).
2. *Minimum-block penalty is aggregated, with no `y` variables.* Per quarter `t` with
   `m = min(L-1, quarters left)` after it: `Σ_{k=1..m} x_{t+k} + m·w_t + m·x_{t-1} − m·x_t ≥ 0`
   with `w_t` binary. Same meaning as the plan's `y`/`w` rows (a run starting at `t` with a
   later quarter of its block off costs 1; a run the deadline cuts short costs nothing), but one
   row per quarter instead of `L` rows plus `y`. Measured on seed 1 of the benchmark: 16 s
   against 9 s in total. The block rows and `w` are added only after P3, because they cannot
   change P1 to P3.
3. *Levels are skipped when the last solution already reaches the level's lower bound*
   (shortfall, unpriced and above-cap energy at 0; S1 inclusion at 1; S3 at the demand's
   maximum; P4 when the last solution has no short run). The level is then held without a
   solve. Same optimum, fewer solves.
4. *Hold tolerance.* Integer-valued levels hold at `v + 0.5`; continuous ones at
   `v + 1e-6 + 1e-9·|v|`, not `v + 1e-6·max(1, |v|)`. A price level of about 110 allowed a
   slack of 1.1e-4, which is two amp-quarters at a price step of 0.001.
5. *`presolve` is False, not True.* On the captured planning levels of benchmark seed 1 the
   total fell from 13.9 s to 8.6 s with presolve off; `mip_heuristic_effort`,
   `mip_detect_symmetry`, `mip_pool_soft_limit` and the RINS/RENS switches did not help (the
   last two made it far slower).
6. *Two small bounds, `_fit_now` and `_even_now`,* let P5's second part and P6 be held without
   a solve when the last solution reaches what the first quarter's capacity alone allows. They
   rarely fire on the benchmark (the other levels already bind first); kept as a cheap shortcut.

**Benchmark (guide item 8), and why the build halted.** `bench.py` in the scratchpad: `decide`
on 10 adjustable 6 to 16 A chargers on mixed phases (L1, L2, L3, all three, L1 + L2), each a
session with a need of 8 to 45 kWh and a ready time between 06:00 and 18:30, a 40-hour hourly
price series, `min_block_quarters = 4`, `price_cap = 0.45`, per-phase budget 32 A, five seeds,
this machine (4 cores, HiGHS on one thread):

| NODE_LIMIT | seed 1 | seed 2 | seed 3 | seed 4 | seed 5 | worst |
|---|---|---|---|---|---|---|
| 4000 | 10.1 s | 5.7 s | 20.2 s | 19.0 s | 15.9 s | **20.2 s** |
| 1000 | 9.8 s | 5.7 s | 18.2 s | 17.7 s | 15.4 s | **18.2 s** |
| 1 (diagnostic, not a valid value) | 8.1 s | 5.5 s | 10.9 s | 10.0 s | n/a | the root work alone is 8 to 11 s |

With 5 chargers the worst of the five seeds is 8.6 s, with 7 chargers 8.8 s. Every run was
proven optimal (no `PLAN_NOT_PROVEN_OPTIMAL`), and two repeated calls on the same snapshot gave
equal decisions. The cost is per level, not per node: the seven planning solves (P1, P3, P4,
P5, P5b, P6, P7) take 0.5 to 5 s each on a 1300 to 1900 column model, and most finish at the
root or within a few dozen nodes, so a node budget does not bound them. Seed 3, per solve:
P1 0.7 s, P3 2.0 s, P4 2.2 s, P5 4.4 s, P5b 1.8 s, P6 2.4 s, P7 5.2 s. Risk 1's levers are
spent: skipping empty levels (item 3 above), the aggregated block constraint (item 2), and a
lower `NODE_LIMIT` (above).

**Re-run after the A5 amendment (guide item 8, second run).** Benchmark as in `bench.py`
(10, 7 or 3 adjustable 6 to 16 A chargers on mixed phases, 40-hour hourly series with an
unpriced tail, `min_block_quarters = 4`, `price_cap = 0.45`, 5 fixed seeds 1 to 5; 4 cores,
HiGHS on one thread; whole `decide` call). Two budgets: 32 A per phase (the one the first halt
measured) and 20 A per phase ("tight budget" in the plan's worst case).

| Budget, NODE_LIMIT | 3 chargers (10 s) | 7 chargers (60 s) | 10 chargers (120 s) |
|---|---|---|---|
| 32 A, 4000 | 0.61 0.20 0.47 0.89 1.51 s | 5.07 9.92 3.96 5.15 8.27 s | 10.3 6.7 18.9 19.7 16.4 s |
| 32 A, 64000 (10 only, 8 seeds) | | | 10.2 5.8 20.0 18.6 16.8 12.5 12.9 19.7 s, worst 20.0 s |
| 32 A, 1024000 (10 only, 8 seeds) | | | 10.4 6.3 20.9 20.0 16.2 12.0 11.9 21.0 s, worst 21.0 s |
| 20 A, 4000 | | | seed 1 108.3 s, seed 2 32.3 s, seed 3 **151.3 s** (SolverError before the fix below) |
| 20 A, 2000 | 1.11 0.26 0.96 3.94 1.98 s | 66.1 25.5 **>100** 8.6 32.9 s | 88.6 31.3 106.9 37.8 32.9 s |
| 20 A, 1000 | | 62.5 23.6 **176.1** 9.1 22.2 s | 74.0 26.8 84.7 36.3 26.3 s |

At 32 A every tier passes with a wide margin at any node limit (the node budget never binds:
every solve is proven at the root or within a few nodes). At 20 A it does bind, and
`NODE_LIMIT = 1000`, the smallest multiple the rule allows, is still too slow for the
7-charger tier: seed 1 takes 62.5 s and seed 3 176.1 s against 60 s. The 10-charger tier
passes at 1000 (worst 84.7 s) and at 2000 (worst 106.9 s) but fails at 4000. So under the
plan's rule `NODE_LIMIT` would be 2000 for the 10-charger tier and no value for the 7-charger
tier at 20 A. `NODE_LIMIT` is left at 1000 in the code, provisional. The 3-charger tier passes
everywhere. At 26 A the 7-charger seeds 1 and 3 take 13.5 s and 4.7 s.

**Deviation 7: a node-limit stop with no point of its own is not an error.** At 20 A, HiGHS
sometimes stops at the node budget in a later level without any solution at all
(`status 4`, `x = None`, `mip_node_count = None`, message "Solution limit reached"), which
raised `SolverError`. The last solution already satisfies every row held so far, so the
level now keeps it: `_Problem.minimise` holds the level at that solution's objective, marks
the run not proven (the `PLAN_NOT_PROVEN_OPTIMAL` event) and goes on. The first solve of a
run, which has no earlier solution, still raises `SolverError` (A6). `status 4` is read as a
node-budget stop when `mip_node_count >= NODE_LIMIT` or the message says "Solution limit".
This is a reading of A5 ("a solve stopped by the node budget returns its best plan") against
A6 ("finds no solution") that the concept does not spell out; flagged for step 6.

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

### Step 3, 2026-10-22

Halted by the implement step at guide item 8 (the A5 benchmark), under the plan's Risk 1:
the worst case for 10 chargers does not return within 10 s.

Reason: on the specified worst case (10 chargers on mixed phases, a price cap, minimum block
of 4, a tight budget) `decide` takes 5.7 to 20.2 s on this machine over five seeds, with every
level proven optimal. The time is the root work of seven exact planning solves (0.5 to 5 s
each), not branch-and-bound, so no `NODE_LIMIT` helps: with the limit at 1 the same instances
still take 8 to 11 s, and with 1000 the worst case is 18.2 s. Risk 1's three levers were
used (empty levels skipped, the aggregated block constraint, a lower node limit), plus level
shortcuts and `presolve = False`. A5 as written cannot be met by this model, and no
wall-clock limit is allowed. Fewer chargers fit: 5 chargers worst 8.6 s, 7 chargers worst
8.8 s. Determinism (Risk 2) held on repeated calls.

Question for the user: how should A5 change? The options, with what each costs:

1. **Raise the bound** to what the model needs, for example 30 s for 10 chargers (measured
   worst 20.2 s). Nothing else in the plan changes. Calls become slow for a household
   controller that calls every 5 minutes, but still fit that cadence.
2. **Lower the charger count** the bound is promised for, for example "up to 6 chargers in
   10 s" (5 chargers: 8.6 s, 7 chargers: 8.8 s on this machine; the margin is thin). The
   plan stays as built.
3. **Drop the exact lexicographic order from the last three levels** (P5 second part, P6, P7:
   most cars on now, the even split, the earlier-first tie-break), which are about half of
   the time on seed 3 (9.4 of 18.7 s), replacing them with a cheaper rule or a single merged
   solve. This amends A2's "optimal for the stated precedence" and A3's even split for the
   planned tier, and needs a new plan for the replacement.
4. **Split the call**: keep the model and the bound but let the caller pass a smaller set of
   chargers (only those that can charge in the next hours). That changes the public contract
   and is a new concept.

What exists is committed: `optimize.py`, `decide.py` wired to it, the greedy code removed.
Tests (guide item 7), `STRUCTURE.md` and `README.md` (item 9) are not done and wait for the
answer, because options 3 and 4 change what they test. On the answer, `/build` resumes from
step 3 at item 7.

**Answer (user, 2026-10-22):** "10 s for up to 3 chargers, 60 s for up to 7, 120 s for up to
10". A5 amended in section 1 to those three tiers; section 2's `NODE_LIMIT` row, guide items
7 and 8, T6 and Risk 1 follow (Risk 1's halt now applies to a tier bound). The model and the
lexicographic order stay as built. `/build` resumes step 3 at guide item 7, after re-running
the benchmark at 3, 7 and 10 chargers to fix `NODE_LIMIT`.

### Step 3, second run, 2026-10-22

Halted at guide item 8 under Risk 1, now for a tier: with the tight budget of the plan's worst
case (20 A per phase on all three phases, 7 chargers) `decide` takes 62.5 s on seed 1 and
176.1 s on seed 3 against the 60 s bound, at the smallest allowed `NODE_LIMIT` (1000); larger
limits are slower (66 s, over 100 s at 2000). The 3-charger tier (worst 3.9 s) and the
10-charger tier (worst 84.7 s at 1000) pass, and at 32 A, the budget the first halt measured
and the user's bounds were set from, all three tiers pass by a wide margin (1.5 s, 9.9 s,
21 s at any node limit). The cost at 20 A is the same as before: root work of seven exact
planning solves, now with the budget cutting some of them off. The wall-clock limit that would
bound it is forbidden by A5.

Question for the user: which budget defines "tight" in A5's worst case?

1. **32 A per phase** (the benchmark the bounds were chosen from). All tiers pass with any
   `NODE_LIMIT`; the build would set it to 1000 and note that tighter budgets than this are
   not covered by the bound.
2. **20 A per phase, with the 7-charger bound raised** to about 180 s (176.1 s measured).
3. **20 A, with a stricter model for many chargers** (a new plan: drop the exact order for the
   last planning levels, as option 3 of the first halt).

What exists is committed. Tests (item 7), `STRUCTURE.md` and `README.md` (item 9) are not done.
