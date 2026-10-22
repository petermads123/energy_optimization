# MILP solver

<!-- claude-plan step=6 status=active -->


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
| 3 | Implement | `/implement` | in `/build` | done (re-run after the second step-6 halt) |
| 4 | Verify | `/verify` | in `/build` | done (re-run after the second step-6 halt) |
| 5 | Test | `/test` | in `/build` | done (re-run after the second step-6 halt) |
| 6 | Concept check | `/concept-check` | in `/build` | pending (re-run) |
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
| A5 | **Is deterministic and bounded.** The same snapshot gives an equal decision across runs and hash seeds; the solve uses a single thread and a fixed node budget, never a wall-clock limit; every call with a 48-hour price series returns, in total on the development machine, within 10 s for up to 3 chargers, 60 s for up to 7 and 120 s for up to 10 (amended at the step-3 halt, 2026-10-22), measured with a per-phase budget of 35 A for 1–3 chargers, 50 A for 2–5 and 70 A for 3–10 (the worst case defined at the second step-3 halt); a tighter grid still works but carries no time promise; a solve stopped by the node budget returns its best plan with an event saying it is not proven optimal. |
| A6 | **Raises instead of guessing.** When HiGHS reports an error or finds no solution — in any level solve, including a later level the node budget stops before it finds a point — `decide` raises `SolverError` naming the solver status, never a partial or greedy decision (decided at the second step-3 halt). A solve that reports infeasible, or stops without a point, is retried once with presolve toggled, and only a second failure raises (decided at the second step-6 halt). |
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
| `Demand(charger_id: str, phases: frozenset[Phase], tier: Tier, min_current: float, max_current: float, now_max_current: float, need_kwh: float = 0.0, deadline: datetime \| None = None)` | `optimize` | One charger asking for current, given in ranking order. `min_current == max_current` is all-or-nothing (on/off). `now_max_current` caps quarter 0 (user limit, fallback), `max_current` the later quarters. Quarter-0-only tiers ignore `need_kwh`, `deadline` and `max_current`. `PLANNED` requires an aware `deadline`. Validates itself in `__post_init__`, raising `ValueError` naming the field (`tier` must be a `Tier`, step 5). Currents must be finite and ≥ 0. A fractional `min_current` is refused with a `ValueError` naming `min_current` when the demand is neither on/off (`min_current == max_current`) nor always off (both `max_current` and `now_max_current` below it): an adjustable charger runs in whole amps (step 5, deviation 20). `max_current` or `now_max_current` below `min_current` is allowed and means off in those quarters, which keeps the `user_current_limit=0` regression and its `TARGET_UNREACHABLE`. `need_kwh` must be finite and ≥ 0. `PLANNED` requires an aware `deadline` | A1, A3 |
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
6. Planned even split (level P6 only): once level P5 has fixed `x_{·,0}`, the planned cars
   that are on now share the first quarter by progressive filling (the P6 row), not by a
   single bound `z ≤ i_{d,0}`: each round adds an integer level `f` with `i_{d,0} ≥ f` for the
   cars still live, and holds it. A car that is off adds no bound, so switching a car off
   can never raise the level. (Amended at the step-6 halt and again in step 5; the single
   max-min this row used to describe is gone.)

Levels run in this order. Each is minimised with every earlier level held at its optimum.
A level with no non-zero coefficient, or no demand of its tier, is skipped.

**Quarter-0 stage**, run for `SMART_OFF`, then `CHARGE_NOW`, before planning, and for
`SPARE` after it. Each run uses the room left in quarter 0, and `SMART_OFF` also the room in
the later quarters. `r_d` is the demand's position within its tier, and `m` the tier's
size.

| # | Solve | Meaning |
|---|---|---|
| S1 | Maximise `Σ 2^(m−1−r_d)·x_{d,0}` with `i_{d,0} = min_current·x_{d,0}`, then fix `x_{·,0}` | Ranking-order inclusion at the minimum: each demand is in if it fits after those ranked above. This is round 1's 0/min selection |
| S2 | Progressive filling over the included demands. Repeatedly maximise an integer `f` with `i_{d,0} ≥ min(f, hi_{d,0})` for the unsaturated ones, holding every earlier result. The demands at the level cannot all rise together, so (deviation 23) each, in ranking order, takes one more amp if it fits on all its phases in what is left, and one that does not fit is saturated for good, as is every demand at its `hi`. Stop when all are saturated | Max-min-fair 1 A steps, the leftover one amp at a time to the lowest with ties in ranking order: round 1's `share` exactly (7 / 7 / 6 for three 6–16 A cars on 20 A; 2 / 2 / 1 for minimums 2, 1, 1 on 5 A) |
| S3 | Removed (deviation 23): once S2 has saturated every demand there is no remainder left to give. It used to hand the leftover to the best-ranked car, which is what produced 8 / 6 / 6 | — |

**Planning stage**, for the `PLANNED` demands, on the room both stages above left:

| # | Minimise | Meaning |
|---|---|---|
| P1 | `Σ s_d` | Meet each need |
| P2 | `Σ e_d·i_{d,t}` over unpriced `t` | Avoid unpriced quarters |
| P3 | `Σ e_d·i_{d,t}` over `t` priced above `price_cap` | Use the cap only to meet the need |
| P4 | `Σ w_{d,t}` | Avoid runs shorter than the minimum block |
| P5 | `Σ π_t·e_d·i_{d,t}` over priced `t`, held **exactly** when the prices lie on a grid and every current is whole (below); then, **when quarter 0 is unpriced**, minimise the first-quarter **energy** `Σ_d e_d·i_{d,0}` (kWh, not amps) and hold it (the latest-first rule fixes how much charging quarter 0 gets; amended at the step-6 halt, kWh in step 5, deviation 18); then maximise `Σ x_{d,0}`, then in ranking order each `x_{d,0}` (each car is on if that is still possible after those ranked above it), and fix `x_{·,0}` | Lowest price; among equally cheap plans, as many cars as possible on now, the better-ranked ones first (the ranking part is deviation 11). With quarter 0 unpriced its energy is held first, so the cars the need forces into quarter 0 are switched on and share it, without pulling unpriced charging earlier (deviation 9 superseded at the step-6 halt). Measured in kWh because A2 promises unpriced quarters "only when needed": amps would let a three-phase car put three times the energy into an unpriced quarter 0 that a single-phase one would. **Exact price hold** (deviation 19): when every price is a whole multiple of a tick of at least 1e-7 and every current is whole, the objective is written in whole ticks (`price/tick × |phases|` per amp-quarter) and held at its rounded optimum plus 0.5, so a quarter dearer by one tick is never bought; otherwise it is held to the numerical tolerance |
| P6 | Progressive filling over the planned cars that are on now **and can still rise** (adjustable, with a first-quarter limit above the minimum; an on/off car, or one limited to its minimum, takes its current and is not part of the fill): repeatedly maximise an integer `f` with `i_{d,0} ≥ f` for the live cars, holding every earlier result; after each round mark saturated every car at its `hi`, or whose `i_{d,0}` cannot exceed `f` with everything held; stop when no car is live. **If a round saturates no car** (each could rise alone but not all together, e.g. three cars on 20 A) the cars, in ranking order, each take one more amp if they still can with everything held (a solve that maximises the car and holds the row `i_{d,0} ≥ f + 1`), and a car that cannot drops out for good; the loop goes on with the cars that rose, as S2 does (deviation 23, which replaces deviation 16's exit). Runs whether quarter 0 is priced or not. Integer columns are rounded before they are compared, because HiGHS returns 7 as 6.9999995 (deviation 21) | Even split among the planned cars on now, as far as cost allows (8/11/11 for caps 8, 16, 16 on 30 A at equal prices; 8/8 for two forced cars in an unpriced quarter 0). Amended at the step-6 halt from a single max-min, and in step 5 to say what the code does |
| P7 | `Σ τ_{d,t}·i_{d,t}`, with `τ = (t+1)·(n−r_d)` for priced `t` and `τ = (T−t)·(r_d+1)` for unpriced `t`; `r_d` is the position among the `PLANNED` demands and `n` their number | Earlier priced quarters first; unpriced quarters latest first, as in round 1; the better-ranked car gets the earlier quarter of either kind. `τ` is not separable, so swapping two cars between quarters changes it. A product is minimised by pairing its large factors with its small ones, so the rank factor is `n−r` (large for the better-ranked car) beside `t+1`, and `r+1` (large for the worse-ranked car) beside `T−t`. Corrected in step 5, deviation 10 |

The full run is S1–S2 for `SMART_OFF`, S1–S2 for `CHARGE_NOW`, P1–P7, then S1–S2 for
`SPARE`. Stages that have no demands are skipped.

Holding a level: after level `k` returns a value `v`, its row becomes `objective ≤ v + tol`.
`tol` is `0.5` after rounding `v` for a level that can only take whole values (amps, counts,
price ticks), and `1e-6 + 1e-9·|v|` otherwise (deviation 4; the first draft's `1e-6·max(1, |v|)`
allowed two amp-quarters at a price step of 0.001).

Every solve:
- Options are `{"node_limit": NODE_LIMIT, "mip_rel_gap": 0.0, "presolve": True, "threads": 1}`; the retry uses `"presolve": False`.
- The `threads` warning is suppressed in a `warnings.catch_warnings()` filtered to that
  message only. There is never a `time_limit`.
- A solve whose result is status 2 (infeasible, with or without a point) or has no `x` (whatever the status: 0, 2, 3 or 4 without a point) is retried once with the same model and `presolve` toggled (deviation 24, aligned in step 5); the retry's result is the one judged below, and only its failure raises. A result that has a point and any other status (a node-budget stop, or a status 3 or 4 below the limit) is judged as it is and never retried.
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

1. *S3 is sequential, not one weighted solve.* (S3 itself is gone: deviation 23.) The plan's `max Σ (m − r_d)·i_{d,0}` ties when
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
5. *`presolve` is False, not True.* (Reversed at the step-6 halt: deviation 17.) On the captured planning levels of benchmark seed 1 the
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

**Deviation 7: reverted (second halt, 2026-10-22).** A node-limit stop with no point of its
own briefly kept the last solution and went on (`_NoIncumbentError`). The user answered
"Raise SolverError" and A6 now says so: every level solve that ends without a point raises
`SolverError(status, message)`. The catch-and-hold, its `known` parameter and the
`_NoIncumbentError` class are removed from `optimize.py`. `PLAN_NOT_PROVEN_OPTIMAL` remains for
a node-budget stop that returns a point (status 1, or status 4 with `x`).

**Deviation 8: fractional all-or-nothing currents.** `_stage_now` made the progressive-fill and
remainder variables integer even when pinned at an on/off charger's fractional assumed current
(7.5 A), which made the model infeasible, and rounded the result to 8 A. Pinned variables are
now continuous and read back exactly. Found by the ported `share` tests.

**Deviation 9: P5's "cars on now" and P6 apply only when the first quarter is priced.** (Superseded at the step-6 halt: deviation 16.) As
planned, an all-unpriced horizon put the car on in quarter 0 (and gave it extra quarters),
contradicting A2's "unpriced quarters used only when needed and latest first". With an
unpriced first quarter the two levels are skipped, so a car runs now only if the need forces
it and P7 orders the unpriced quarters latest first. The concept decided this case (A2), so
no change to section 1; the plan's P5 and P6 rows are narrowed to priced first quarters.

**Benchmark (guide item 8, third run), final.** `bench.py`: adjustable 6 to 16 A chargers on
mixed phases (L1, L2, L3, all three, L1 + L2), each a session with a need of 8 to 45 kWh and
a ready time between 06:00 and 18:30, a 40-hour hourly series (unpriced tail),
`min_block_quarters = 4`, `price_cap = 0.45`, whole `decide` call, 4 cores, HiGHS on one
thread, `NODE_LIMIT = 8000`. Seconds, seeds 1 to 5 (the 3-, 7- and 10-charger cells at 70 A also
seeds 6 to 8). Every run proven optimal (no `PLAN_NOT_PROVEN_OPTIMAL`).

| Cell (chargers @ budget per phase) | Tier | Worst of the seeds |
|---|---|---|
| 1 @ 35 A | 10 s | 0.17 |
| 2 @ 35 A | 10 s | 0.44 |
| 3 @ 35 A | 10 s | 0.66 |
| 2 @ 50 A | 10 s | 0.33 |
| 3 @ 50 A | 10 s | 0.51 |
| 5 @ 50 A | 60 s | 2.27 |
| 3 @ 70 A | 10 s | 0.59 |
| 5 @ 70 A | 60 s | 1.46 |
| 7 @ 70 A | 60 s | 3.06 |
| 10 @ 70 A | 120 s | **39.54** (seed 5; the others 1.5 to 9.5) |

Every tier passes, the 10-charger worst case with a threefold margin. The same grid at
`NODE_LIMIT = 1000` (5 seeds) and `1024000` (3 to 10 chargers, 8 seeds) gave the same times
within noise (10 chargers: worst 40.05 s and 39.59 s): the node budget never binds on the
grid, so it does not discriminate.

**`NODE_LIMIT = 8000`.** The plan's rule (largest power-of-two multiple of 1000 that keeps
every tier inside) has no upper end on this grid. A degenerate small case does bind: one
adjustable charger with flat prices and 6 A left by a reserved charger
(`test_planning_capacity_is_starved_only_below_the_adjustable_minimum`) takes 0.8 s at 1000,
4.7 s at 8000, and 158 s at 1024000. 8000 is the largest value in the series that keeps that
1-charger case inside the 10 s tier, so it is the value. Budgets tighter than the grid's are
not timed (the second Halted answer).

**Tests (guide item 7).** The greedy planner's tests and `Claim`/`share`'s are removed from
`test_ev_planning.py` and `test_ev_allocation.py`; `share`'s tests are kept in the new
`tests/test_ev_optimize.py`, driven through `optimize` with `CHARGE_NOW` demands (they pass
unchanged). Changed expectations in `test_ev_decide.py`, each with its A8 kind:

| Test | Change | Kind |
|---|---|---|
| `test_two_unreachable_planned_cars_split_a_shared_phase_evenly_now` (was `test_an_empty_plan_left_by_another_sessions_plan_is_waiting_for_capacity`) | 16 A / 0 A becomes 8 A / 8 A, both priority 2, both `IN_PLAN` | even split |
| `test_a_planned_charger_without_room_now_is_planned_later_and_a_starved_one_waits` (was `..._an_active_tier_at_zero_and_a_starved_charger_wait_for_capacity`) | planned car with 5 A on its phase now: priority 2 and `WAITING_FOR_CAPACITY` become 4 and `PLANNED_LATER` | optimality (plan's quarter-0 current), reason code leftover |
| `test_a_lower_tier_gets_what_a_higher_tier_leaves` (was `..._only_the_highest_occupied_tier_gets_current`) | spare car on another phase gets 16 A, not 0 | leftover |
| `test_smart_charging_off_is_served_before_smart_chargers` | `WAITING_FOR_CAPACITY` becomes `WAITING_FOR_HIGHER_PRIORITY` for charge-now behind a smart-off charger | leftover (reason code) |
| `test_charge_now_takes_the_first_quarter_and_the_plan_starts_after_it` (was `..._do_not_consume_planning_capacity`) | plan `[16, 16]` from now becomes `[16, 11]` from the next quarter; priority 2 / `WAITING_FOR_HIGHER_PRIORITY` becomes 4 / `PLANNED_LATER` | optimality, reason code leftover |
| `test_smart_off_allocations_shrink_the_capacity_planned_for_smart_sessions`, `test_reserved_chargers_shrink_the_capacity_planned_for_smart_sessions` | plan currents `{10}` / `{15}` become a set topping out at 10 / 15 whose energy covers the need (the plan delivers the need, last quarters step down) | optimality |
| `assert_invariants`: `assert len(served) <= 1` | replaced by a tier-precedence invariant (a lower tier never holds current a runnable higher-tier charger on a shared phase could use) | leftover |

Added: `test_a_lower_tier_never_takes_current_a_higher_tier_could_use`, the tier timing tests
(`test_the_a5_tier_bound_holds_for_every_benchmark_seed` for 3 chargers @ 35 A, 7 and 10 @
70 A, seeds 1 to 5), a repeat-call equality test, and
`test_the_package_uses_numpy_and_scipy_and_pyproject_declares_them`. Surface tests updated,
not classified: the export set (+`SolverError`), the removed names (+`plan_sessions`,
`PlanRequest`, `share`, `Claim`), the dataclass fields (`PlanRequest` replaced by `Demand`),
the signatures (`plan_sessions` replaced by `optimize`), `EventKind`'s exact members, the AST
scan (+`numpy`, `scipy`) and the allocation showcase's assertion. No hypothesis
`max_examples` was lowered: the two `decide` properties (300 and 60 examples) take about 16
and 5 s. The suite takes 130 s, of which the 10-charger tier test is about 55 s and the 7-charger
one 11 s; no `slow` marker exists in the repo, so none was added.

**Gates at the end of step 3.** `ruff check .`, `ruff format --check .`, `mypy`: clean.
`pytest`: 1166 passed. Every module showcase exits 0. `STRUCTURE.md` gains `optimize.py`
and `tests/test_ev_optimize.py` and loses `Claim`, `share`, `PlanRequest` and `plan_sessions`;
`README.md` needed no change (it never claimed the package is stdlib-only).

**Changes made in step 5 (tests).** Each was found by a test that was red first; the evidence
is in section 5. The Public API table is unchanged by all of them.

10. *P7's priced weight was the wrong way round.* `τ = (t+1)·(r+1)` gives the earlier quarter
    to the worse-ranked car (rearrangement inequality), against the stated meaning. Priced
    quarters now use `(t+1)·(n−r)`. Unpriced quarters keep `(T−t)·(r+1)`: there the large
    factor `r+1` meets the small `T−t`, the later quarter, so the worse-ranked car takes the
    latest unpriced quarter and the better-ranked one the earlier. The orchestrator's
    suggested `(T−t)·(n−r)` for unpriced quarters would give the better-ranked car the
    latest, opposite to "the better-ranked car gets the earlier quarter"; a test fails on it.
11. *Which of equally cheap cars is on now was HiGHS's tie, not the ranking.* P5 fixed
    `x_{·,0}` after the count level, so with equal prices the worse-ranked car could take
    quarter 0 (the same defect as 10, in the first quarter). After the count, P5 now
    maximises each `x_{d,0}` in ranking order, held, skipped by the floor shortcut when the
    last solution already has it on.
12. *A planned on/off demand with a fractional current (7.5 A) could never be planned.*
    `_stage_plan` made `i` an integer bounded by `7.5·x`, so `x = 1` was infeasible (deviation
    8 fixed only the quarter-0 stage). Now `i` is continuous when `min_current ==
    max_current`, read back exactly (`_read`), `_even_now` treats a pinned column the same
    way and compares with `floor`, and P7's hold uses the half-unit tolerance only when every
    on/off current is whole (otherwise the continuous tolerance).
13. *Smart-off is held to the smaller of `now_capacity` and `capacity`.* Constraint 2 counts a
    smart-off charger's current against `capacity` in later quarters, but the stage only saw
    `now_capacity`. The stage's room is now `min(room, later)` per phase. Through `decide`
    nothing changes (`cap <= plan_cap`).
14. *Not-proven rule.* Status 4 is not proven only when `mip_node_count >= NODE_LIMIT`. The
    extra `"Solution limit" in message` clause is removed: the real HiGHS (scipy 1.18.1) returned
    status 4 with `mip_node_count == node_limit` for every limit tried (1, 2, 3, 10, 50, on three
    80-variable knapsacks), so the message is not needed, and a status 4 below the limit with
    the same message is a failure (T7). Section 2's text was already this rule.
15. *Validation and documentation.* `Demand` raises `ValueError` naming `tier` for a non-`Tier`
    tier. `optimize`'s docstring now says what it raises with no demands (nothing: it returns
    first), that absent phases and negative values count as 0 in both capacities, and that a
    limit broken after rounding raises `SolverError` with status 0 (the solver's "optimal").
    `decide` gains a `Raises: SolverError`. `min_current == max_current` is on/off in every
    tier even when `now_max_current` is higher (documented and pinned; the plan's "quarter-0
    tiers ignore `max_current`" is not reachable through `decide`). The overfill bound keeps
    `e·max(first-quarter, later)` current (documented and pinned; equal to the plan's
    `e·max_current` through `decide`).

Timing after these changes (whole `decide`, 4 cores, one thread, `NODE_LIMIT = 8000`, seconds):
3 chargers @ 35 A, seeds 1 to 5: worst 1.3; 7 @ 70 A, seeds 1 to 5: worst 4.6; 10 @ 70 A,
seeds 1 to 8: 7.4, 4.7, 10.3, 7.0, 42.7, 3.0, 12.9, 14.5, worst 42.7 (the P5 ranking level adds
solves; every tier still passes with a wide margin). The 10-charger tier test takes about 73 s
in the suite.

### Step-3 re-run after the step-6 halt (2026-10-22)

Both changes are in `src/energy_optimization/ev/optimize.py`; the Public API table is
unchanged. Section 2's P5 and P6 rows already carry the amended text.

**Red runs, before the fix** (reproductions added to `tests/test_ev_optimize.py` and
`tests/test_ev_decide.py`, committed red as `WIP step 3: reproductions ...`):

- `test_optimize_three_planned_cars_fill_the_first_quarter_evenly_up_to_each_limit`
  (A capped at 8 A, B and C 6 to 16 A, 30 A, equal prices, need 1.84 kWh each):
  `AssertionError ... Differing items: {'b': 14.0} != {'b': 11.0}, {'c': 8.0} != {'c': 11.0}`.
- `test_optimize_two_planned_cars_forced_into_an_unpriced_first_quarter_share_it`
  (16 A, no prices, need 1.84 kWh each): `assert {'a': 16.0, 'b': 0.0} == {'a': 8.0, 'b': 8.0}`.
- `test_decide_solves_snapshots_highs_called_infeasible_without_presolve[two chargers at 10 A]`
  and `[four chargers at 10 A]` (step 6's #40 and #246, written out as literal snapshot dicts):
  `SolverError: HiGHS status 2: The problem is infeasible. (HiGHS Status 8: model_status is Infeasible; primal_status is None)`.

All four are green after the fix. Added beside them as pins:
`test_optimize_an_unpriced_first_quarter_is_not_filled_beyond_what_the_need_forces` (latest-first
kept: one car with a small need and no prices gets 0 A now),
`test_optimize_planned_cars_that_cannot_all_rise_share_the_remainder_in_ranking_order` (20 A,
three cars: 8 / 6 / 6), `test_optimize_planned_cars_on_mixed_phases_fill_evenly_by_phase`
(8 / 8 / 8), and `presolve is True` in the solver-call spy test.

16. *P5 and P6 as amended (deviation 9 superseded).* `_on_now_and_even` now runs in every
    case. With an unpriced first quarter it first minimises and holds the total first-quarter
    current of the planned cars (only what the need forces), then maximises the cars on now
    and ranks them as when priced. P6 is `_fill_now`, a full progressive fill in the S2 style:
    each round maximises an integer level `f` with `i_{d,0} >= f` for the live cars, holds it,
    saturates every car at its maximum or that cannot be raised above `f` with every other
    live car held (a probe solve per candidate, skipped when the last solution already has the
    car above `f`), and goes on with the rest. One addition to the plan's wording, so the loop
    ends: if a round saturates no car (each could rise alone but not all together, e.g. three
    cars on 20 A) the remainder goes in ranking order, as S3 does. `_even_now` is removed
    (private; its shortcut is now the `known >= ceiling` test in `_fill_now`). `_Problem`
    gained `probe` (a solve that holds nothing and keeps no witness) and `set_known`, and
    `_solve` a `keep` flag. Results: 8 / 11 / 11 and 8 / 8.
17. *`presolve = True` (deviation 5 reversed).* With `presolve = False` HiGHS reported
    "infeasible" for feasible models. Step 6's planned-heavy set (500 snapshots) and its
    `random_snapshot` set (600): 0 `SolverError`s now (was 3: #40 and #246 status 2, #111
    status 4 after 40.7 s); #111 returns in 23 s while another 500-snapshot run shared the
    machine (17 s in step 6's run). Nothing else needed changing to make presolve work.
    #111 is not added as a regression test: at 16 A and 3 chargers it is off the A5 grid
    and takes about 17 s, which no test should spend; #40 and #246 take 1 s.

*Tests changed because of these two changes.* No test pinned deviation 9's skip or a single
max-min, so none was removed. One test depended on presolve being off:
`test_a_real_node_budget_stop_returns_a_plan_within_every_limit` used 2 chargers at 16 A,
seeds 1 to 6, to reach a real node-budget stop; with presolve those solve at the root (no
stop, no `SolverError`), so it now uses 4 chargers at 20 A, seeds 4 to 6 (one stop with a
point, two status 4 without), which still hits both paths with `NODE_LIMIT = 1` (18 s).

**Benchmark, re-timed (guide item 8, fourth run).** Whole `decide`, `benchmark_snapshot`
(mixed phases, 40 h series with an unpriced tail, `min_block_quarters = 4`, price cap), 4
cores, one thread, `NODE_LIMIT = 8000`, seconds, nothing else running; seeds 1 to 8 on the four
named cells, 1 to 5 on the others. No `PLAN_NOT_PROVEN_OPTIMAL` anywhere.

| Cell | Tier | Times | Worst |
|---|---|---|---|
| 1 @ 35 A | 10 s | 0.09 0.07 0.11 0.04 0.06 | 0.11 |
| 2 @ 35 A | 10 s | 0.25 0.15 0.29 0.11 0.13 | 0.29 |
| 3 @ 35 A | 10 s | 0.92 0.21 0.51 0.35 0.97 0.36 0.37 0.28 | 0.97 |
| 2 @ 50 A | 10 s | 0.19 0.12 0.25 0.09 0.12 | 0.25 |
| 3 @ 50 A | 10 s | 0.39 0.21 0.35 0.29 0.26 | 0.39 |
| 5 @ 50 A | 60 s | 1.36 0.89 2.51 2.15 1.79 0.73 0.79 1.11 | 2.51 |
| 3 @ 70 A | 10 s | 0.43 0.23 0.37 0.34 0.31 | 0.43 |
| 5 @ 70 A | 60 s | 1.02 0.70 1.05 0.75 0.55 | 1.05 |
| 7 @ 70 A | 60 s | 3.88 7.83 2.43 2.00 2.09 1.52 2.47 2.67 | 7.83 |
| 10 @ 70 A | 120 s | 8.84 6.79 12.98 2.31 59.54 2.05 6.41 20.83 | **59.54** (seed 5) |

Every tier holds; the 10-charger worst case is 59.5 s of 120 s (42.7 s before this pass: the
progressive fill and the minimised unpriced total add solves, presolve takes some back).
Risk 1 does not trigger.

**`NODE_LIMIT` stays 8000.** The plan's rule (largest power-of-two multiple of 1000 that keeps
every tier inside) again has no upper end on the grid: 10 @ 70 A seed 5 takes 55.7, 57.2 and
58.5 s at 8000, 16000 and 32000. The case that fixed 8000 before, one adjustable car with flat
prices and 6 A left by a reserved charger, no longer binds with presolve: 0.0 to 0.1 s at
8000, 16000, 64000, 128000, 256000 and 1,024,000. So the value is no longer chosen by a
measurement; it is kept (the node-budget tests and the not-proven path are written around it),
and no value up to 1,024,000 changes a time on the grid.


### Step-5 re-run changes (2026-10-22)

Found by tests written in step 5's re-run, each red first and then green (section 5). They
change `optimize.py` only; the Public API table is unchanged except the `Demand` row's
validation sentence (deviation 20). Section 2's P5 and P6 rows, constraint 6 and the
holding paragraph were rewritten to say what the code does.

18. *The unpriced first quarter's forced total is held in kWh, not amps.* P5's second part
    minimised `Σ i_{d,0}` in amps, so a three-phase car could put three times the energy
    into an unpriced quarter 0 that a single-phase one would, against A2's "unpriced
    quarters used only when needed and latest first" (a plan-level correction toward A2's
    wording; it breaks no other criterion). Now `Σ |phases_d|·i_{d,0}` (energy up to the
    constant `e` of one amp on one phase), held as a whole number when all currents are
    whole. Red: `test_optimize_an_unpriced_first_quarter_holds_the_forced_energy_not_the_forced_amps`
    (a three-phase and a single-phase car, 12 A-quarters each, no prices, 16 A):
    `{'c': 6.0, 's': 6.0}` against `{'c': 0.0, 's': 12.0}`, which puts 0.69 kWh into the
    unpriced quarter instead of 1.38 kWh. Green after the fix.
19. *The price level is held exactly, in whole price ticks.* Reproduced: a car needing 20
    A-quarters over two quarters at prices 0.20001 then 0.20 EUR/kWh (a one-tick, 10 EUR/MWh
    resolution, difference) with 14 A in the cheap second quarter, so 6 A now is the cheapest
    plan: `{'a': 9.0}` instead of `{'a': 6.0}`. Three amps of the dearer quarter were bought
    because the hold slack (`1e-6`, and HiGHS's own `1e-7` feasibility tolerance on top) is
    larger than one amp-quarter's price step (`1e-5 × 0.0575 = 5.75e-7`), and the cars-on-now
    maximise, the fill and P7 all spend whatever slack the hold leaves. Now, when every
    priced quarter's price is a whole multiple of a tick of at least `1e-7` (their gcd in
    units of `1e-9`, at most `10^7` ticks) and every current is whole, P5's first part is
    minimised in whole ticks (`price/tick × |phases|` per amp-quarter) and held at its
    rounded optimum plus 0.5. A strictly cheaper quarter is then never given up, equal
    prices still tie freely (`test_optimize_equal_prices_still_leave_the_split_free_after_the_tick_fix`),
    and a hold with whole-number steps cannot make the model infeasible any more than the
    other integral levels can. `_Problem.hold` now rounds the value of an integral level
    before adding 0.5 (the solver's value can be off by its integrality tolerance times
    large coefficients). Not covered, still held to the numerical tolerance: prices on a grid
    finer than `1e-7`, a fractional on/off current anywhere in the problem, and prices of
    more than `10^7` ticks. The first draft of the equal-price test expected 16 A for `a`; that
    was a wrong expectation (a 4 A second quarter is below the 6 A minimum), the plan has 14 A
    now and 6 A later.
20. *`Demand` refuses a fractional `min_current` on a charger that can run adjustably.* The
    current of an adjustable demand is an integer variable with `i ≥ min·x`, so a minimum
    of 7.5 A really means 8 A while the fit tests count 7.5 A, and the quarter-0 stage could
    ask for an infeasible model (`optimize` with a 7.5 to 16 A charge-now demand and 7.9 A of
    room). The refusal is a `ValueError` naming `min_current`. The first version refused every
    fractional minimum that differs from the maximum; a 600-snapshot stress run over
    `random_snapshot` then showed `decide` reaching it: an on/off pump at 7.5 A under a user
    limit of 5 A gives `min 7.5, max 5, now_max 5`, which is off in every quarter. Red:
    `test_decide_a_fractional_on_off_charger_under_a_lower_user_limit_stays_off[planned]` and
    `[limit-zero]` raised `ValueError: min_current: must be a whole number ...` from
    `decide`. Final rule: refused when `min_current != max_current`, the minimum is
    fractional and `max(max_current, now_max_current) >= min_current`. (The brief said
    `decide` cannot reach a fractional minimum; it can, through an on/off charger under a
    lower limit, and the final rule lets that through.) `_random_demands` in the limits property now draws a fractional minimum only for on/off
    demands.
21. *The fill compares rounded integer columns.* HiGHS returns 7 as 6.9999995 within its
    `1e-6` integrality tolerance, and `_fill_now` tested `held >= reached + 1 - 1e-9`, so a
    car already at the next amp was judged stuck and the ranking remainder skipped. Red:
    `test_optimize_the_fill_is_robust_to_solver_integrality_noise` (every returned entry
    above 0.5 lowered by `5e-7`, two cars on 15 A): `{'a': 7.0, 'b': 8.0}` against
    `{'a': 8.0, 'b': 7.0}`. The three comparisons (`known`, `held`, the probe) now round.
22. *Probes.* No code change. A failing probe already raises `SolverError` and a probe
    stopped at the node budget already makes `proven` False (both pinned in step 5);
    `Outcome` and `optimize` now say "every solve" instead of "every level".

*Timing after these changes* (whole `decide`, `benchmark_snapshot` as before with 3-decimal
prices, 4 cores, one thread, `NODE_LIMIT = 8000`, seconds, nothing else running; no
`PLAN_NOT_PROVEN_OPTIMAL` anywhere):

| Cell | Tier | Times (seeds in order) | Worst |
|---|---|---|---|
| 1 @ 35 A | 10 s | 0.09 0.06 0.10 0.04 0.06 | 0.10 |
| 2 @ 35 A | 10 s | 0.18 0.14 0.27 0.10 0.12 | 0.27 |
| 3 @ 35 A | 10 s | 0.62 0.17 0.53 0.32 0.89 0.35 0.40 0.28 | 0.89 |
| 2 @ 50 A | 10 s | 0.25 0.13 0.28 0.14 0.16 | 0.28 |
| 3 @ 50 A | 10 s | 0.45 0.17 0.32 0.29 0.27 | 0.45 |
| 5 @ 50 A | 60 s | 1.53 1.13 2.50 1.29 2.38 0.60 1.07 0.93 | 2.50 |
| 3 @ 70 A | 10 s | 0.36 0.17 0.35 0.25 0.30 | 0.36 |
| 5 @ 70 A | 60 s | 0.82 0.54 1.21 0.79 0.60 | 1.21 |
| 7 @ 70 A | 60 s | 3.65 4.95 2.33 1.89 1.64 1.67 2.34 2.29 | 4.95 |
| 10 @ 70 A | 120 s | 6.52 5.92 11.08 2.16 46.10 1.89 6.40 23.66 | **46.10** (seed 5) |

Every tier holds (the 10-charger worst case 46.1 s of 120 s; 59.5 s before this pass). The
same three named cells with 5-decimal prices, which give the exact hold larger tick numbers
(seeds 1 to 5, 10 @ 70 A seeds 1 to 8): 3 @ 35 A worst 0.42, 7 @ 70 A worst 14.48, 10 @ 70 A
worst 15.62. Risk 1 does not trigger. A stress run beside the grid: 600 `random_snapshot`
snapshots with prices rounded to 5, 3, 5 and 2 decimals in turn, and 500 planned-heavy ones
(2 to 4 adjustable chargers on shared phases, flat and varying prices, a priced or unpriced
first quarter): 0 `SolverError`s and 0 invariant failures in both; the planned-heavy set had 6
node-budget stops (a not-proven event, every limit kept), as the step-4 code did on the same
500 snapshots (6; worst 19.9 s then, 18.6 s now).

### Step-3 re-run after the second step-6 halt (2026-10-22)

Both changes are in `src/energy_optimization/ev/optimize.py`; the Public API table is
unchanged. Section 2's S2/S3 and P6 rows and its "Every solve" status rules are amended.

**Red runs, before the fix** (committed as `WIP step 3: reproductions, red`):

- `test_optimize_the_leftover_goes_one_amp_at_a_time_to_the_lowest_in_every_tier`
  (three 6 to 16 A cars on 20 A, minimums 2 / 1 / 1 on 5 A, 19 A, four 1 A cars on 7 A, in
  each of the four tiers): `{'a': 8.0, 'b': 6.0, 'c': 6.0} == {'a': 7.0, 'b': 7.0, 'c': 6.0}`
  for 20 A, and 3 / 1 / 1 against 2 / 2 / 1 for minimums 2 / 1 / 1 (16 cases, 12 red; the
  four 19 A ones were already `share`'s answer, since one spare amp has only one taker).
- `test_optimize_the_quarter_zero_tiers_equal_round_ones_share_on_generated_claims`
  (a restatement of round 1's `share` as the reference, 60 generated claim sets per tier):
  red for `SMART_OFF`, `CHARGE_NOW` and `SPARE`.
- `test_optimize_planned_cars_that_cannot_all_rise_share_the_remainder_one_amp_at_a_time`
  (the old 8 / 6 / 6 test, expectation changed to 7 / 7 / 6): `{'a': 8.0, 'b': 6.0, 'c': 6.0}`.
- `test_optimize_a_probe_stopped_at_the_node_budget_is_not_proven`: its expectation, 8 / 6 / 6,
  changed with the rule to 7 / 7 / 6.
- `test_optimize_tiny_instances_highs_calls_infeasible_are_solved_by_the_retry[1931]` and
  `[5662]` (step 6's tiny-set seeds, written out as literals): `SolverError: HiGHS status 2:
  The problem is infeasible. (HiGHS Status 8: model_status is Infeasible; primal_status is
  None)`.

All green after the fix.

23. *The leftover goes one amp at a time to the lowest, ties in ranking order (round 1's
    `share`), in every tier.* Answer of the user at the second step-6 halt. Deviation 16's
    exit ("the remainder goes in ranking order") is replaced, and S3 is removed.
    - *S2 (smart-off, charge-now, spare).* The progressive fill still finds the highest
      level `f` that every unsaturated demand can reach together, with a solve. The old
      freeze rule (`left < max(1, cars at the level)`) and S3 then handed the rest to the
      best-ranked car. Now, at that level, each unsaturated demand in ranking order takes
      one more amp if it fits on all its phases in what is left, and one that does not fit
      is saturated for good (the room only shrinks), as is one at its maximum. The loop goes
      on from the demands that rose. This is `share` level by level: a pass at one level is
      `share`'s rule, because every demand at the lowest value is a candidate in ranking
      order. S3 has nothing left to give and is deleted (one fewer solve per tier).
      `_stage_now` no longer builds its last problem.
    - *P6 (`_fill_now`).* When a round saturates no car, each live car in ranking order is
      asked, with everything held, whether it can reach `f + 1` (a new `_Problem.raise_to`:
      one solve that maximises the car, keeps its solution as the problem's witness and
      adds the row `i >= f + 1` when it can). Cars that can stay live; the others drop out.
      The old exit maximised each car in turn and so gave everything to the first.
    - *Evidence.* Round 1's `share` against the quarter-0 tiers, step 6's
      `sharediff.py` (3,000 generated claim sets, 1 to 8 A minimums, mixed phases): 0
      differences for `CHARGE_NOW` (it was 29), and 0 for `SMART_OFF` and `SPARE` (the same
      script with the tier changed). Step 6's planned-tier brute force over 6,000 instances
      each, `leximin.py` and `leximin_lv.py`: 0 mismatches and 0 errors (it was 5 and 2).
      The wider range, seeds 6,000 to 16,000, also 0 mismatches and 0 errors in both scripts
      (so 0 of 16,000; step 6 had 22).
    - *Tests changed because of it.* `test_optimize_planned_cars_that_cannot_all_rise_share_the_remainder_in_ranking_order`
      (8 / 6 / 6) is replaced by `..._one_amp_at_a_time` (7 / 7 / 6), and the expectation in
      `test_optimize_a_probe_stopped_at_the_node_budget_is_not_proven` changed the same way.
      No other test pinned 8 / 6 / 6 or 3 / 1 / 1. Added: the four-tier table above, and the
      generated comparison against the restated `share`.
24. *A solve HiGHS reports infeasible, or that stops without a point, is retried once with
    presolve toggled (A6 as amended).* `_Problem._solve` calls HiGHS (now in `_Problem._milp`)
    with `presolve = True` as before; when the result is status 2 or has no `x`, it calls
    again, same model, same node budget, `presolve = False`, and judges only that result: it
    is proven on status 0, not proven on a node-budget stop with a point, and a second failure
    raises `SolverError` with the retry's status and message. *Which results are retried
    (aligned in step 5 with what the code does, `_failed`):* status 2 whether or not it has a
    point, and any result without a point (status 0, 3 or 4 without `x` as well). A status 3
    or 4 below the node limit that has a point is not retried, nor is a node-budget stop with
    a point. (The first wording said status 3 is never retried; it never carries a point in
    scipy, so it is. This is A6's "reports infeasible, or stops without a point", read as "no
    usable point"; the code is unchanged.) Deterministic (the same two calls every run), no
    wall-clock limit. The retry applies to every solve, probes included.
    - *Evidence.* Seeds 1931 and 5662: `SolverError` status 2 before, solved by the retry now
      (the retry's solve is the one that succeeds; `trace1931.py` had shown the previous
      solution satisfies every row). Tests added: the retry for each of four failures (status
      2, status 2 with a point, status 4 and status 1 without a point), a retry that stops at
      the node budget being not proven, the second failure raising for each status with the
      number of calls, and the two seeds.
    - *Existing tests.* `test_decide_raises_solver_error_for_every_failure_status`,
      `test_optimize_a_probe_failure_raises_solver_error` and the rest of the failure
      stubs return the same failure on every call, so they still raise, after two calls.
      None had to change.

**Planned-heavy #78 (the status 4 since `d7c8e77`): root cause.** Traced with
`trace78.py` and a wrapper on `_Problem._milp` (columns, rows, presolve, status, nodes,
seconds) on the snapshot in step 6's set. Of nine solves, eight finish; the seventh, the
ranking level of P5 (maximise `x` of one car, 594 columns and 842 rows, one objective
column) with `presolve = True`, stops at 8,000 nodes with status 4 and no point after
15.4 s. The same model with `presolve = False` is optimal in 201 nodes and 1.4 s. The level
asks whether one more car can be on now at the same cost; the answer is no, and HiGHS has to
prove it. What changed in step 5 is the row it has to prove it against: deviation 19's exact
price hold, a single dense integer row with coefficients up to 2,667 ticks held at `round(v) + 0.5`,
is a knapsack that presolved branch-and-bound does not close within the budget. With the tick
hold switched off (`_price_ticks` returning `None`) the same level is optimal with presolve
after 1,913 nodes and 3.9 s, and the level before it takes 3,620 nodes instead of 2,368. So it
is not a defect in the tick hold's logic (it holds a level exactly, and A2 needs that) but a
search that stalls on one model under one setting, which is the thing the retry is for.
With the retry `decide` returns on #78 (27 s whole, 15 s of it the failed first attempt; the
snapshot is four chargers at 16 A, off the A5 grid). No code beyond the retry is changed
for it, and #78 is not added as a regression test (27 s); the retry's mechanism is pinned
by the stubbed tests above.

**Benchmark, re-timed (guide item 8, fifth run).** `r2b_bench.py`: whole `decide`,
`benchmark_snapshot` (mixed phases, 40 h series with an unpriced tail, `min_block_quarters
= 4`, price cap 0.45, 3-decimal prices), 4 cores, one thread, `NODE_LIMIT = 8000`, seconds,
nothing else running; seeds 1 to 8 on the four named cells (3 @ 35, 5 @ 50, 7 @ 70, 10 @ 70),
1 to 5 on the others. No `PLAN_NOT_PROVEN_OPTIMAL` anywhere.

| Cell | Tier | Times (seeds in order) | Worst |
|---|---|---|---|
| 1 @ 35 A | 10 s | 0.12 0.10 0.14 0.04 0.06 | 0.14 |
| 2 @ 35 A | 10 s | 0.23 0.14 0.29 0.13 0.14 | 0.29 |
| 3 @ 35 A | 10 s | 0.65 0.18 0.55 0.40 1.09 0.39 0.40 0.34 | 1.09 |
| 2 @ 50 A | 10 s | 0.24 0.14 0.33 0.12 0.13 | 0.33 |
| 3 @ 50 A | 10 s | 0.37 0.19 0.39 0.33 0.32 | 0.39 |
| 5 @ 50 A | 60 s | 1.83 1.26 2.48 1.38 2.94 0.77 1.21 0.91 | 2.94 |
| 3 @ 70 A | 10 s | 0.40 0.20 0.45 0.33 0.32 | 0.45 |
| 5 @ 70 A | 60 s | 0.89 0.56 1.34 0.86 0.63 | 1.34 |
| 7 @ 70 A | 60 s | 4.19 7.82 2.47 1.96 1.66 1.75 2.50 2.64 | 7.82 |
| 10 @ 70 A | 120 s | 7.19 6.03 11.30 2.30 46.70 1.91 6.32 32.28 | **46.70** (seed 5) |

Every tier holds; the 10-charger worst case is 46.7 s of 120 s (46.1 s before this pass:
the one-amp passes add no measurable time; seed 8 went from 23.7 s to 32.3 s, seed 7 and
the rest within noise). The same three named cells with 5-decimal prices (seeds 1 to 5, 10 @
70 A seeds 1 to 8): 3 @ 35 A worst 0.48, 7 @ 70 A worst 15.86, 10 @ 70 A worst 23.38. Risk 1
does not trigger. `NODE_LIMIT` stays 8000.

**Gates at the end of this re-run.** `ruff check .`, `ruff format --check .`, `mypy`: clean.
`pytest`: 1407 passed in 144.5 s (1377 at step 6's second run; the difference is the tests added
above). Every module showcase exits 0. `STRUCTURE.md`'s `optimize.py` purpose paragraph and
row, and the `test_ev_optimize.py` entry, are updated for the leftover rule and the retry.


### Step-5 re-run after the second step-6 halt (2026-10-22)

25. *`_fill_now` keeps a higher value.* In the raise pass `value[n] = reached + 1` lowered the
    recorded value of a car already above the level (unequal minimums). Only the `at_top`
    shortcut reads `value`, and the next round resets it from `reached`, so no result changed
    and no test can see it; it is now `max(value[n], reached + 1)`. Pinned only by the
    unequal-minimums results (right before and after). Public API unchanged.

*Retry scope, aligned with the code (no behaviour change).* `_failed` retries status 2 with or
without a point and any result without a point (status 0, 3 or 4 without `x`); a result with a
point and any other status is judged as it is. Section 2's "Every solve" bullet, deviation 24,
the `_failed` and `_solve` docstrings, `Outcome.proven`, `optimize`'s `Raises:` and
`STRUCTURE.md` now say so. A6's "reports infeasible, or stops without a point" is read as "no
usable point"; no change to section 1.

**A5 with every solve retried (forced failure; not in the suite).** `r2c_forced.py`: whole
`decide` on `benchmark_snapshot`; every first attempt runs the real solver and is then
reported as a status 4 without a point, so each solve pays for both attempts (the retry is the
real presolve-off result). 4 cores, one thread, `NODE_LIMIT = 8000`, seconds, seeds 1 to 8
(a few quick test runs shared the machine during the 10-charger cells). No
`PLAN_NOT_PROVEN_OPTIMAL`, no `SolverError`, first and retry call counts equal in every run.

| Cell | Tier | Times (seeds 1 to 8) | Worst | Unforced worst (fifth run) |
|---|---|---|---|---|
| 3 @ 35 A | 10 s | 1.58 0.35 1.47 1.09 2.44 0.91 1.07 0.79 | 2.44 | 1.09 |
| 7 @ 70 A | 60 s | 8.69 17.28 5.37 5.34 4.77 4.42 5.46 5.70 | 17.28 | 7.82 |
| 10 @ 70 A | 120 s | 14.86 11.99 22.98 5.61 89.94 5.48 19.82 73.61 | 89.94 (seed 5) | 46.70 |

Every tier still holds with every solve doubled; the 10-charger worst case is 90 s of 120 s
(seed 5; 46.7 s unforced). Nothing to record for step 8 on the promised grid. A real retry
repeats only the solve that failed, so this is an upper bound; a tighter grid carries no
time promise.


## 4. Verification log

> Written in step 4: the static half. Command output, not a summary of it.

| Check | Result |
|---|---|
| `ruff check .` | `All checks passed!` |
| `ruff format --check .` | `57 files already formatted` |
| `mypy` | `Success: no issues found in 26 source files` (no scipy override needed) |
| `pytest` | `1166 passed in 127.98s` |
| Plan completeness | every signature in the Public API table exists as written: `SolverError(status, message)` with `str` form, `NODE_LIMIT = 8000`, `Tier`, `Demand` (fields, defaults, validation), `Outcome`, `optimize(...)`, `main`, `EventKind.PLAN_NOT_PROVEN_OPTIMAL`, `SolverError` in `ev.__all__`, dependencies in `pyproject.toml`; `PlanRequest`, `plan_sessions`, `Claim`, `share` removed. No Missing, no Unplanned; deviations 1 to 9 are recorded in section 3 (`NODE_LIMIT` value fixed by the benchmark, as planned) |
| `STRUCTURE.md` | in sync. Auditor: no drift. Applied its note 1 (`optimize` row now lists `ZoneInfoNotFoundError`) and note 2 (stale first line of `allocation.py` reworded; `test_ev_optimize.py`'s docstring left for step 5, which fills the file) |
| `python -m energy_optimization.ev.<module>` | model, result, quarters, planning, allocation, decide, codec, optimize all exit 0 and print readable output (the expected `RuntimeWarning` only). Showcase form checked; `optimize.main` gained a same-line-style comment listing the `Tier` members |

### Re-run after the step-6 halt (step 3 fourth run)

| Check | Result |
|---|---|
| `ruff check .` | `All checks passed!` |
| `ruff format --check .` | `57 files already formatted` |
| `mypy` | `Success: no issues found in 26 source files` |
| `pytest` | `1305 passed in 161.55s` (1298 at the end of step 5; the difference is the tests step 3's re-run added, section 3) |
| Plan completeness | Public API table re-checked by introspection: `SolverError(status, message)`, `NODE_LIMIT = 8000`, `Tier`, `Demand` (fields, defaults), `Outcome`, `optimize(...)` (nine parameters as planned), `main`, `EventKind.PLAN_NOT_PROVEN_OPTIMAL`, `SolverError` in `ev.__all__`. The public names of `optimize.py` are exactly the planned ones; `PlanRequest`, `plan_sessions`, `Claim`, `share` still absent. No Missing, no Unplanned. |
| P5/P6 against the amended rows | `_on_now_and_even` runs in every case: with an unpriced first quarter it minimises and holds the planned cars' total first-quarter current, then maximises cars on now, then each car in ranking order, fixes `x`; `_fill_now` is a full progressive fill (integer level `f`, saturate at `hi` or when the car cannot rise above `f`, probe solves, ranking-order remainder when no car saturates). Matches section 2's P5 and P6 rows, including the one addition recorded in deviation 16. `_even_now` is gone. |
| Deviations 16 and 17 | Both recorded in section 3 (step-3 re-run subsection) with reason and evidence; deviations 5 and 9 carry "reversed" / "superseded" pointers. The Public API table is unchanged by both. `presolve` is `True` in `optimize.py` (line 209) and the spy test asserts `mip_rel_gap == 0.0` and `presolve is True`. |
| `STRUCTURE.md` | Auditor report applied or rebutted: `optimize.py` entry, no change (agreed). Item 1 (`test_ev_optimize.py`): the first half (the planned tier's full progressive fill) was already written by step 3; applied the two parts it lacked, "a zero gap and presolve on" in the solver-spy clause, and the unpriced-not-filled and 8 / 8 / 8 mixed-phase pins. Item 2 (`test_ev_decide.py`): already present as "the two step-6 snapshots HiGHS called infeasible without presolve (explicit literals)"; rebutted as done, wording kept. `decide`'s own spy test checks only the node budget, threads and gap, as the auditor noted; no drift. |
| Showcases | model, result, quarters, planning, allocation, optimize, decide, codec all exit 0 with readable output (the expected `RuntimeWarning` on `optimize` only). `optimize.main` unchanged and still in the required form. |

### Re-run after the second step-6 halt (step 3 fifth run)

| Check | Result |
|---|---|
| `ruff check .` | `All checks passed!` |
| `ruff format --check .` | `57 files already formatted` |
| `mypy` | `Success: no issues found in 26 source files` |
| `pytest` | `1407 passed in 144.33s` (matches step 3's count) |
| Plan completeness | Public API table re-checked by introspection: `optimize.py` public names are exactly `Demand`, `Outcome`, `SolverError`, `Tier`, `main`, `optimize` (+ `NODE_LIMIT = 8000`); `optimize(...)` has the nine planned parameters and return type; `Demand` fields and defaults (`need_kwh=0.0`, `deadline=None`) as planned. No Missing, no Unplanned. |
| S2 against the amended row | `_stage_now`: S1 inclusion, then rounds of a solved level `f`; at that level each unsaturated demand in ranking order takes one amp if it fits on all its phases in what is left, one that does not fit (or is at its `hi`) is frozen; loop ends when all are frozen. No S3 solve remains. Matches section 2's S2 and the removed S3 row (deviation 23). |
| P6 against the amended row | `_fill_now`: when a round saturates no car, each live car in ranking order calls `_Problem.raise_to(col, f + 1)`; cars that rose stay live, the rest drop out; integer columns rounded before comparison (deviation 21). Matches section 2's P6 row. |
| Retry rule | `_Problem._solve` calls `_milp(presolve=True)`; `_failed` (status 2 or `x is None`) triggers one `_milp(presolve=False)`; only the retry's result is judged (status 0 proven; status 1 or status 4 with `mip_node_count >= NODE_LIMIT` and a point is not proven; anything else raises with the retry's status and message). Statuses 3 and 4 below the limit with a point are not retried. Options `node_limit`, `mip_rel_gap=0.0`, `threads=1`, no `time_limit`. Matches "Every solve" and deviation 24. |
| Deviations 23 and 24 | Both recorded in section 3 with reason and evidence; deviation 1 and the S3 row carry "gone, deviation 23" pointers. The Public API table is unchanged by both. |
| `STRUCTURE.md` | Auditor report (third pass) checked against the code as it stands. Edits 1 and 2 (purpose paragraph: leftover rule, retry) were already in the file as step 3 wrote them; rebutted as done, wording kept. Edit 3 (`optimize` row): step 3's wording "fails twice" applied in the auditor's clearer form, "fails and its one retry with presolve toggled fails too (carrying the retry's status and message)". Edit 4 (`test_ev_optimize.py`): (b) the leftover wording and the retry clause were already present; applied the one missing part, "failing without a retry" for a status 4 below the limit (true: `_failed` does not fire for it). No signature drift. |
| Showcases | `python -m energy_optimization.ev.<module>` for model, result, quarters, planning, allocation, optimize, decide, codec: all exit 0 with readable output (the expected `RuntimeWarning` only). `optimize.main` unchanged, still in the required form (named inputs, call, result, print, `Tier` members listed in a comment). |

---

## 5. Test log

> Written in step 5: the dynamic half.

Two `test-designer` reports (`input-space`, `contract`) were merged: 15 + 14 `optimize`
cases, 7 + 14 `decide` cases, 5 + 13 contradictions. Every contradiction is settled below.

| Intent | Test names | Result |
|---|---|---|
| T1 limits | `test_optimize_never_breaks_a_limit` (80 generated calls: mixed tiers, phases, fractional and inverted currents, negative and absent capacities, gaps in prices, mid-quarter now; allocation equals the plan's first quarter, per-phase `now_capacity` and `capacity` with smart-off, nothing at or after a deadline); `test_the_invariants_hold_for_generated_snapshots` (through `decide`, independent of the module); `test_demand_rejects_each_invalid_field_naming_it` (13), `test_demand_accepts_zero_and_a_maximum_below_the_minimum` (5), `test_demand_is_frozen`, `test_outcome_is_frozen_and_holds_its_three_fields` | pass |
| T2 brute force | `test_optimize_matches_a_brute_force_enumeration_level_by_level` (60 generated instances: 1 or 2 planned demands, 2 to 6 quarters, at most 3 current levels, P1 to P7 tuple compared to an exhaustive enumeration; checked to bite: with `τ`'s priced rank factor put back to `r+1`, or the unpriced one to `n−r`, it fails) | pass |
| T3 hand-checked plans | `test_optimize_a_minimum_block_trades_price_for_a_contiguous_run` (2), `..._a_block_longer_than_the_window_is_cut_short_by_the_deadline` (2), `..._a_tiny_need_goes_to_the_deadline_quarter_under_a_block` (2), `..._a_partly_unpriced_horizon_uses_the_priced_quarters_first`, `..._an_all_unpriced_horizon_charges_only_the_latest_quarter`, `..._negative_prices_overfill_by_at_most_one_quarter`, `..._a_price_at_the_cap_is_not_above_it` (2), `..._uses_the_cap_only_to_meet_the_need`, `..._a_shortfall_is_reported_with_the_reachable_energy`, `..._now_capacity_limits_only_the_first_quarter`, `..._plans_nothing_for_a_deadline_at_or_before_now` (2), `..._a_deadline_inside_the_first_quarter_plans_that_quarter`, `..._a_zero_need_plans_nothing`, `..._plans_the_repeated_hour_of_the_fall_back_night_in_utc`, `..._a_48_hour_horizon_of_192_quarters_plans_the_cheapest_first`, `..._the_overfill_bound_uses_the_highest_current_of_either_quarter`; through `decide`: `test_decide_plans_an_on_off_charger_with_a_fractional_current`, `..._a_long_fractional_on_off_need_is_planned_in_full`, `..._fallback_caps_only_the_first_planned_quarter`, `..._a_ready_time_inside_the_current_quarter_plans_that_quarter`, `..._an_unpriced_current_quarter_waits_for_the_priced_hour`, `..._a_fresh_but_empty_price_series_plans_the_latest_quarter`, `..._the_price_cap_event_starts_strictly_above_the_cap` (2), `..._a_tiny_need_is_planned_in_the_ready_time_quarter`, `..._a_tiny_need_runs_now_when_the_minimum_block_is_one`; plus the existing doc scenarios | pass |
| T4 tiers | `test_optimize_every_quarter_zero_tier_reproduces_the_named_share_results` (4 named cases x 3 tiers), `test_optimize_a_lower_tier_takes_only_what_a_higher_tier_leaves` (all 6 tier pairs, lower tier listed first), `..._a_lower_tier_gets_the_leftover_on_another_phase`, `..._smart_off_current_holds_in_the_later_quarters`, `..._two_planned_cars_at_equal_prices_run_now_evenly`, `..._the_quarter_zero_stages_ignore_a_deadline_and_a_need`, `..._equal_minimum_and_maximum_is_on_off_even_below_the_first_quarter_limit`, `test_optimize_holds_smart_off_to_capacity_in_the_later_quarters`, `..._gives_the_better_ranked_car_the_earlier_cheap_quarter`, `..._ranking_follows_the_list_order_not_the_id`, `..._unpriced_quarters_go_to_the_better_ranked_car_earlier`, `..._equal_prices_put_the_better_ranked_car_on_now`; `decide`: `test_a_spare_car_behind_a_planned_car_waits_for_higher_priority`, `..._gets_current_when_the_planned_car_is_planned_later`, `..._behind_a_reserved_charger_waits_for_capacity`, `test_decide_a_planned_car_ranked_first_charges_first_on_equal_prices`, `..._a_planned_car_run_below_its_max_by_the_plan_is_normal`; the existing `test_a_lower_tier_gets_what_a_higher_tier_leaves` and `..._never_takes_current_a_higher_tier_could_use` | pass |
| T5 stale prices | `test_stale_prices_share_spare_capacity_evenly_with_no_plans`; the existing `test_stale_prices_make_a_smart_session_priority_three`, `..._do_not_touch_charge_now`, `test_stale_prices_never_mark_a_session_starved` | pass |
| T6 determinism and the node budget | `test_decide_is_equal_when_chargers_states_and_sessions_are_permuted` (6 shuffles; the comparison is by charger id and sorted events, because `Decision.chargers` and per-charger events follow snapshot order by design); the existing hash-seed, repeated-call and A5 tier tests; `test_decide_never_passes_a_time_limit_and_runs_single_threaded` and `test_optimize_never_passes_a_time_limit_and_runs_single_threaded` (the `milp` call is spied); `test_a_node_budget_stop_with_a_point_adds_the_not_proven_event_last` (3 stubs: real message, other message, status 1; chargers, actions, status equal the baseline and the event is last), `test_optimize_a_node_budget_stop_with_a_point_is_not_proven` (2), `test_a_not_proven_decision_round_trips_through_the_codec`, `test_a_real_node_budget_stop_returns_a_plan_within_every_limit` (real HiGHS with `NODE_LIMIT = 1` over 6 instances: some give a not-proven plan within every limit, some `SolverError` status 4) | pass |
| T7 `SolverError` | `test_decide_raises_solver_error_for_every_failure_status` (2, 2 with a point, 3, 4 below the limit, 4 without a point, 1 without a point), `test_a_status_four_below_the_node_limit_raises_whatever_its_message`, `test_optimize_a_status_four_below_the_node_limit_is_a_failure`, `test_decide_raises_when_a_later_level_stops_without_a_point`, `test_a_current_outside_its_limits_after_rounding_raises_with_status_zero`, `test_a_phase_over_its_capacity_after_rounding_raises_with_status_zero`, `test_solver_error_carries_status_and_message_and_is_reexported`, `test_optimize_treats_negative_and_absent_capacity_as_zero` (2: never raises, 0 A), `test_optimize_rejects_a_naive_now_and_an_unknown_zone` (2), `test_optimize_rejects_a_repeated_charger_id` (2), `test_optimize_of_no_demands_returns_before_any_check`, `test_node_limit_is_a_power_of_two_multiple_of_a_thousand` | pass |
| T8 removals and dependencies | the existing `test_the_round_two_names_are_gone` (`plan_sessions`, `PlanRequest`, `share`, `Claim`), `test_the_package_imports_only_the_standard_library_numpy_scipy_and_itself`, `test_the_package_uses_numpy_and_scipy_and_pyproject_declares_them` | pass |
| T9 unchanged surface | the existing codec key-set, statelessness, DST finish and validation tests; the differential's pinning tests, one per A8 kind: even split `test_two_unreachable_planned_cars_split_a_shared_phase_evenly_now`; optimality `test_a_planned_charger_without_room_now_is_planned_later_and_a_starved_one_waits`, `test_charge_now_takes_the_first_quarter_and_the_plan_starts_after_it`, `test_smart_off_allocations_shrink_the_capacity_planned_for_smart_sessions`, `test_reserved_chargers_shrink_the_capacity_planned_for_smart_sessions`; leftover `test_a_lower_tier_gets_what_a_higher_tier_leaves`, `test_smart_charging_off_is_served_before_smart_chargers`; new this step: `test_a_not_proven_decision_round_trips_through_the_codec` (decision keys equal round 1's) | pass |

**Bugs the tests found, each red first and then green** (full output not pasted: the
failures were assertion diffs):

| # | Bug | Red | Fix (section 3) |
|---|---|---|---|
| 1 | A planned on/off demand with a fractional current is never planned | `test_optimize_plans_an_on_off_demand_at_its_exact_current[fractional]`: `reachable` False, `quarters=()` (the `[whole]` control passed); `test_optimize_plans_a_fractional_on_off_demand_beside_an_adjustable_one` red too | deviation 12; green |
| 2 | P7's priced weight gave the earlier cheap quarter to the worse-ranked car | `test_optimize_gives_the_better_ranked_car_the_earlier_cheap_quarter` and `..._ranking_follows_the_list_order_not_the_id`: `a` planned at 20:30 and `b` at 20:15, not the reverse | deviation 10; green |
| 3 | Which of two equally cheap cars is on now was HiGHS's tie, not the ranking | found when the `decide` ranking test was first written with equal prices: `b` (second) planned at 22:00, `a` at 22:15 (`test_decide_a_planned_car_ranked_first_charges_first_on_equal_prices`, `test_optimize_equal_prices_put_the_better_ranked_car_on_now`) | deviation 11; green |
| 4 | Smart-off was not held to `capacity` in later quarters | `test_optimize_holds_smart_off_to_capacity_in_the_later_quarters`: `{'s': 16.0} != {'s': 10.0}` | deviation 13; green |
| 5 | `Demand(tier=2)` was accepted and failed later with a `KeyError` | `test_demand_rejects_a_tier_that_is_not_a_tier`: DID NOT RAISE | deviation 15; green |
| 6 | (introduced and caught inside this step) the first version of fix 1 treated `min_current > max_current` as on/off and planned 16 A | `test_optimize_the_overfill_bound_uses_the_highest_current_of_either_quarter` (plan `{q0: 16}` not `6`) | on/off is `min_current == max_current` only; green |

**Settled contradictions.** Input-space 1 = contract C1/C11/O1/D1: bug 1. Input-space 2 / O5:
bug 4. Input-space 3 / C6: bug 5. Contract C2/O2/D12: bugs 2 and 3. C3: the message clause is
removed (deviation 14). C4: documented and tested through the private `_current` and
`_verify`. C5, C8, C10: docstrings aligned (deviation 15). C7: documented and pinned. C12:
documented and pinned. C13: the permutation test compares by id. Input-space 4 and C9: see
below.

**Pinned for step 8, not changed** (they follow the model as written, and the concept decided
them):

- *A tiny need is pushed to the deadline under a minimum block.* With `min_block_quarters > 1`
  a need below one minimum-current quarter is planned in the last quarter, because P4 outranks
  P5/P7 and a run the deadline cuts short is free (`test_optimize_a_tiny_need_goes_to_the_
  deadline_quarter_under_a_block`, `test_decide_a_tiny_need_is_planned_in_the_ready_time_quarter`).
  Through `decide` the car reads `PLANNED_LATER` for a need that could be met now.
- *A mid-quarter ready time credits the whole last quarter.* `reachable` is True while the
  expected finish (the end of that quarter) is after the ready time, so `reaches_target` is
  False and no `TARGET_UNREACHABLE` is raised (`test_decide_a_ready_time_inside_a_quarter_
  credits_the_whole_quarter`). The same holds for a mid-quarter `now`. Inherited from round 1.

**Suite.** `ruff check .`, `ruff format --check .`, `mypy`: clean. `pytest`: 1298 passed in
169 s (1166 before this step; 132 added). Every module showcase exits 0.

Edge cases considered and deliberately skipped, with reasons:

- *Text cases (non-ASCII, whitespace, long ids)*: charger ids are opaque dictionary keys in
  `optimize`; `decide` and the codec tests already cover ids.
- *`Tier` values and `IntEnum` arithmetic*: one ordering test is enough.
- *A 192-quarter horizon through `decide`*: unreachable, `ready_time` is a `time` so the
  horizon is at most about 96 quarters; `optimize` itself is tested at 192.
- *Spring-forward in `optimize`*: covered through `decide` in `test_ev_decide.py`; the
  fall-back night, the harder one, is tested in `optimize` directly.
- *Warm-start or thread-count variations*: out of scope (A5, single thread, stateless).
- *Timing beyond the A5 grid*: the plan says a tighter grid carries no time promise.
- *A per-level `hypothesis` brute force with 3 or more demands*: the enumeration grows as
  levels^(quarters x demands); two demands and at most 30000 joint states per instance keep the
  property in 4 s, and 3 demands add no new level interaction.
- *`mip_node_count` missing from the result*: `getattr(..., 0)` is a defence for scipy
  versions that omit it; the real result always carries it, so there is nothing to simulate
  beyond the stubs that already set it.

### Re-run after the step-6 halt (step 5, 2026-10-22)

Two `test-designer` reports (`input-space` 14 cases and 3 contradictions, `contract` 12
cases and 7 contradictions) were merged. Every case was written (one oracle change, the
rest as tests), and every contradiction is settled below. The designers' hand-worked
expectations held on the first run except the two that exposed real behaviour (the
three-phase unpriced total and the price tick, bugs 1 and 2 below); the only expectation of
mine that was wrong was an equal-price control (deviation 19).

| Intent | Test names | Result |
|---|---|---|
| T2 oracle, amended P5/P6 | `level_tuple` now emits the forced first-quarter energy when it is unpriced, the cars-on count and flags whether or not the first quarter is priced, and the fill levels (minimum, then each car in ranking order) over the live cars only (adjustable, first-quarter limit above the minimum); `test_optimize_matches_a_brute_force_enumeration_level_by_level` also draws three-phase demands. It passed on the unchanged model at 60 and 600 examples (so the amps-versus-kWh question needed a hand-made case, below); it passes on the final code at 300 examples (the suite keeps 60) | pass |
| A3 planned tier, even first quarter | `test_optimize_an_unpriced_first_quarter_forced_to_one_car_switches_on_the_better_ranked` (2), `..._a_forced_expensive_first_quarter_is_split_evenly`, `..._the_even_split_never_pulls_a_car_into_an_expensive_first_quarter`, `..._planned_cars_tied_at_a_cap_leave_the_rest_to_the_uncapped_car`, `..._a_capped_first_car_passes_the_ranking_remainder_on`, `..._where_one_phase_binds_raise_the_free_phase_car_to_its_top`, `..._an_on_off_car_ranked_first_keeps_its_slot_and_the_adjustable_takes_the_rest`, `..._a_fractional_on_off_car_lowers_the_even_level_to_an_exact_fit`, `..._an_unpriced_first_quarter_takes_the_smaller_fractional_on_off_car`, `..._planned_cars_all_capped_below_the_room_get_their_caps`, `..._planned_cars_fill_to_their_own_limits_at_equal_prices` (6: unequal minimums, a car at its first-quarter limit, one limited to its minimum, an on/off car, a fractional limit of 10.5 A, a car off now), `..._a_need_limited_car_saturates_below_its_max_and_the_other_takes_the_rest`, `..._the_fill_never_moves_current_from_a_cheaper_quarter_into_the_first`, `..._an_unpriced_first_quarter_with_an_odd_forced_total_gives_the_extra_amp_by_rank`, `..._the_fill_leaves_every_plan_reachable_with_the_same_total_energy`; through `decide`: `test_decide_planned_cars_tied_at_a_user_limit_leave_the_rest_to_the_other`, `..._two_cars_forced_into_an_unpriced_current_quarter_share_it_in_plan`, `test_the_output_does_not_depend_on_the_hash_seed_through_the_planned_fill` | pass, first run |
| A2 unpriced only as needed, in energy | `test_optimize_an_unpriced_first_quarter_holds_the_forced_energy_not_the_forced_amps` | red, then green (bug 1) |
| A2 cheapest quarter first, to the price resolution | `test_optimize_a_first_quarter_dearer_by_one_price_tick_gets_no_extra_amp` (red, then green: bug 2), `test_optimize_equal_prices_still_leave_the_split_free_after_the_tick_fix`, `test_price_ticks_finds_the_coarse_grid_of_the_prices` (6), `test_price_ticks_gives_up_when_the_grid_is_too_fine_or_too_wide` (5) | pass |
| A3 stable under solver noise | `test_optimize_the_fill_is_robust_to_solver_integrality_noise` | red, then green (bug 3) |
| A6 and A5 for probes | `test_optimize_a_probe_failure_raises_solver_error` (status 2 from a probe, the wrapper saw `keep=False`), `test_optimize_a_probe_stopped_at_the_node_budget_is_not_proven` | pass, no code change needed |
| A5 same solver calls every run | `test_optimize_the_fill_issues_the_same_solver_calls_every_run` (the `c` vector of every `milp` call, twice, for two fill instances), `test_the_output_does_not_depend_on_the_hash_seed_through_the_planned_fill` | pass |
| A1 `Demand` validation | `test_demand_rejects_a_fractional_minimum_on_a_charger_that_can_run_adjustably` (5 shapes x 4 tiers), `test_demand_accepts_a_fractional_minimum_only_when_it_can_never_run_adjustably` (5), through `decide`: `test_decide_a_fractional_on_off_charger_under_a_lower_user_limit_stays_off` (3), `..._under_the_fallback_cap_stays_off` | red, then green (bug 4) |
| T4 oracle for the tier invariant | `test_the_tier_invariant_does_not_count_a_charger_the_fallback_cap_keeps_off` | red on the old oracle, green (bug 5, a wrong expectation) |

**Bugs found, each red first and then green:**

| # | Bug | Red | Fix (section 3) |
|---|---|---|---|
| 1 | The unpriced first quarter's forced total was measured in amps, so a three-phase car could put three times the kWh there | `{'c': 6.0, 's': 6.0}` against `{'c': 0.0, 's': 12.0}` | deviation 18; green |
| 2 | A strictly cheaper quarter was given up for a dearer one at a one-tick price difference (hold slack larger than a price step) | `{'a': 9.0}` against `{'a': 6.0}` (prices 0.20001 then 0.2) | deviation 19; green |
| 3 | The fill judged a car at 6.9999995 A stuck and skipped the ranking remainder | `{'a': 7.0, 'b': 8.0}` against `{'a': 8.0, 'b': 7.0}` with `5e-7` of noise | deviation 21; green |
| 4 | (introduced and caught inside this step) the first `Demand` refusal of a fractional `min_current` made `decide` raise for an on/off pump at 7.5 A under a 5 A user limit | `ValueError: min_current: must be a whole number ...` from `decide` (`[planned]`, `[limit-zero]`), found by the 600-snapshot stress run | deviation 20; green |
| 5 | The test oracle `assert_invariants` called a spare car holding current a violation although the smart-off charger beside it could not run under the 4 A fallback cap (a wrong expectation in the oracle; the code was right). Present at the step-4 commit too | `y holds current that x could use` | the oracle's `can_run` now respects the fallback cap |

Not bugs, no code change: probes (A6/A5 already held), the hold tolerance text in section 2
(text only), `whole` being computed over every planned demand (input-space contradiction
3; with a fractional on/off car anywhere the half-unit holds and the exact price hold fall
back to their tolerances, which is safe and documented here, and no case needs it narrower).

**Settled contradictions.** Input-space 1 and contract 3, 4, 5: section 2's constraint 6, P6 row
(loop exit, live set) rewritten. Input-space 2: deviation 20. Input-space 3: above. Contract 1:
deviation 19. Contract 2: deviation 18. Contract 6: `Outcome` and `optimize` docstrings say
"every solve", probes tested. Contract 7: the oracle, first row.

**Tests that changed.** `test_demand_accepts_zero_and_a_maximum_below_the_minimum`: its
`fractional-min` case (`Demand(.., 6.5, 16.0, 16.0)`) is now refused by design and moved to the
rejection test; an on/off fractional case replaced it. `_random_demands` (the limits property)
draws a fractional minimum only for on/off demands. `test_a_real_node_budget_stop_returns_a_plan_within_every_limit`:
the instances changed to 3 chargers at 16 A seeds 4 and 6 and 4 chargers at 20 A seed 6,
because the exact price hold changed which solves branch; with `NODE_LIMIT = 1` they still give
two not-proven plans with every limit kept and one `SolverError` status 4 (9 s). The `can_run`
oracle fix above. Nothing was weakened or deleted otherwise.

**Gates.** `ruff check .`, `ruff format --check .`, `mypy`: clean. `pytest`: 1377 passed in
130 s (1305 before this step; 72 added). Every module showcase exits 0. `STRUCTURE.md`:
the `Demand` row and the `test_ev_optimize.py` and `test_ev_decide.py` entries are updated.
Timing: section 3, "Step-5 re-run changes" (all ten cells of the A5 grid, worst 46.1 s for 10
chargers at 70 A).

Edge cases considered and deliberately skipped, with reasons:

- *Fractional on/off in the exact price hold*: it falls back to the tolerance (documented
  in deviation 19); a tick-aware hold for non-integral currents would need a rational model
  of the cost and no case needs it.
- *Prices on a grid finer than 1e-7*: the same fallback; a 1e-9 grid is not a market price.
- *T2 at more than 2 demands*: unchanged from the first run (the enumeration grows with
  levels^(quarters x demands)).
- *Pinning `_fit_now`'s call count*: it is a shortcut, covered by the results it must not change.
- *The 192-quarter horizon through `decide`*: unchanged from the first run.

### Re-run after the second step-6 halt (step 5, third run, 2026-10-22)

Two `test-designer` reports (`input-space`: 10 cases and 4 contradictions; `contract`: 10
cases and 6 contradictions) were merged with the orchestrator's guidance. Every case was
written except the slow one (contract 10, measured instead: section 3). No bug was found:
every new test was green on its first run, so there is no red evidence to record. To check
the tests bite, three mutations of `optimize.py` were run, each turned the intended test red,
and the file was restored: `_failed` widened to `status != 0`
(`..._a_budget_stop_with_a_point_is_not_retried[status-one]`), the fractional guard
`value + 1 > top` removed from `_stage_now`
(`..._a_fractional_first_quarter_limit_never_takes_the_remainder_amp[SMART_OFF]`), and the
retry's failure reported with the first attempt's status
(`..._a_failed_retry_raises_with_the_retrys_status_and_message[three-without-a-point]`).

| Intent | Test names | Result |
|---|---|---|
| A6 the retry reports its own failure | `test_optimize_a_failed_retry_raises_with_the_retrys_status_and_message` (5: status 3, 4 below the limit with a point, 2 with a point, 0 and 4 at the limit without a point; status, message, `str` and the presolve flags `[True, False]`), `test_decide_reports_the_retrys_failure_not_the_first_attempts` (3, through `decide`) | pass |
| A5/A6 the retry's options | `test_optimize_the_retry_changes_nothing_but_presolve_and_never_adds_a_time_limit` (every first attempt fails: key set, threads, node budget, gap, and each retry equals its first attempt apart from `presolve`); `test_optimize_never_passes_a_time_limit_and_runs_single_threaded` now asserts the retry contract (below) | pass |
| A6 a budget stop with a point is not retried | `test_optimize_a_budget_stop_with_a_point_is_not_retried` (status 1 and status 4 at the limit: no `presolve=False` call, not proven, allocations equal a clean run's) | pass |
| A6 a retry that stops with a point | `test_optimize_a_retry_that_stops_at_status_one_with_a_point_is_kept_and_not_proven` | pass |
| A6 the planned remainder through a retry | `test_optimize_the_planned_remainder_survives_a_retry_of_every_solve` (2: proven retry, and a retry that stops at the budget; 7 / 7 / 6, `proven` follows the retry; the raise pass, probes and levels all retry) | pass |
| A5 retried runs are deterministic | `test_optimize_a_retried_run_issues_the_same_solver_calls_every_run` (the call sequence alternates True, False and is equal across two runs), `test_optimize_the_tiny_seed_still_exercises_the_real_retry` (seed 1931 equal across runs, with a message if HiGHS stops needing the retry) | pass |
| A3 fractional limits and rooms | `test_optimize_a_fractional_first_quarter_limit_never_takes_the_remainder_amp` (4 tiers: a 6.5 A limit leaves 6 / 7, 10.5 A gives 10), `test_optimize_the_remainder_at_zero_above_the_minimum_and_on_a_fractional_room` (4 tiers x 4 rooms: 18, 21, 22, 20.5 A) | pass |
| A3 binding versus free phases | `test_optimize_a_car_on_the_binding_phase_drops_out_while_one_on_a_free_phase_keeps_rising` (4 tiers x 2 rankings: 7 / 6 / 16) | pass |
| A3 on/off cars in the raise pass | `test_optimize_an_on_off_car_inside_the_ranking_takes_its_current_and_the_remainder_skips_it` (4 tiers x whole 10 A and fractional 7.5 A) | pass |
| A3 unequal minimums | `test_optimize_unequal_minimums_send_the_remainder_to_the_lowest_not_the_higher_minimum` (4 tiers x 3 rows, one with the higher minimum between the others) | pass |
| A3 smart-off's two rooms | `test_optimize_smart_off_shares_the_smaller_of_its_two_rooms_one_amp_at_a_time` (later binds, now binds, charge-now ignores the later room) | pass |
| A3/A5 `raise_to` stopped at the budget | `test_optimize_a_raise_to_solve_stopped_at_the_budget_is_not_proven` | pass |
| A3 odd first quarter forced by prices | `test_optimize_a_cost_forced_odd_first_quarter_gives_the_extra_amp_to_the_first_ranked` (both rankings) | pass |
| A3 planned tier against round 1's `share` | `test_optimize_the_planned_tier_equals_round_ones_share_on_generated_claims` (40 seeds: every car fits at its minimum and the needs leave only the even split to decide) | pass |
| A1 slack | `test_optimize_limits_hold_to_the_solver_tolerance_and_the_documented_slack` (3 tiers x 3 rooms) | pass |

**Settled contradictions.**

- *Retry scope* (both reports' contradiction 1, input-space 1, contract 1 to 4): deviation 24,
  section 2's "Every solve", the `_failed` and `_solve` docstrings, `Outcome`, `optimize`'s
  `Raises:` and `STRUCTURE.md` aligned with what the code does; behaviour unchanged (section 3).
- *`Outcome.proven` docstring* (input-space 2): now says the retry's result counts.
- *Solver-tolerance slack* (input-space 4): accepted. A room is honoured to HiGHS's 1e-7 plus
  `_verify`'s 1e-6 A, below the resolution of any current measurement; a room 5e-10 short of a
  whole amp counts as that amp (pinned: 21 - 5e-10 A gives 7 / 7 / 7, 19.99 A does not). No
  halt, no code change.
- *`_fill_now`'s `value`* (input-space 3, contract 6): fixed (deviation 25), harmless today.
- *STRUCTURE.md against section 3* (contract 5): the purpose paragraph, the `optimize` row and
  the `test_ev_optimize.py` entry now describe the retry and the remainder tests.
- *The `presolve is True` test* (contract 6): `test_optimize_never_passes_a_time_limit_and_runs_single_threaded`
  asserted `presolve is True` on every call, which held only because its input never triggers a
  retry. It now asserts the retry contract: the key set, no `time_limit`, threads and node
  budget on every call, and a `presolve=False` call only straight after a `presolve=True` one
  with every other option equal. Changed in place, not weakened.
- *A5 with every solve retried* (contract case 10): measured, not added to the suite (section
  3): worst 2.44 s, 17.28 s and 89.94 s for the 3-, 7- and 10-charger cells against 10, 60 and
  120 s. No tier breaks; nothing for step 8.

**Bugs found:** none, so no red/green pairs this run.

**Suite.** `ruff check .`, `ruff format --check .`, `mypy`: clean. `pytest`: 1525 passed in
148.6 s (1407 before this run; 118 added, 115 in `test_ev_optimize.py` and 3 in
`test_ev_decide.py`, plus one existing test changed in place). Every module showcase exits 0.

Edge cases considered and deliberately skipped, with reasons:

- *The forced-retry A5 cells as a test* (contract 10): minutes of runtime, and the forced case
  is not the promised grid; measured once and recorded.
- *Remainder equal to the number of cars* and *a single car*: unreachable by the fill (a level
  maximised for all cars leaves at least one stuck; a lone car is always stuck), as the
  input-space report said.
- *Empty demands, naive `now`, text, purity of `_stage_now`*: the changed code does not touch
  them and the existing tests cover them.
- *A direct test of `_fill_now`'s `value` after the raise pass*: private state with no
  observable effect, so it is pinned only through the results.

## 6. Concept check

> Written in step 6, against section 1 — not against section 2. The question is whether
> the thing built is the thing agreed, not whether it matches the plan.

**Second run (after the step-6 halt of 2026-10-22). Result: A3 and A6 are unmet again and
the build halts (see Halted, "Step 6, second run").** A1, A2, A4, A5 and A7 are met.

Method. Round 1 of this branch (`2646e7c`) was extracted with `git archive` into the session
scratchpad and run beside the working tree over the same two sets as the first run (checked
byte-equal to the first run's files): 600 snapshots from the suite's `random_snapshot` and
500 planned-heavy ones. Two new independent probes: (1) a brute force written for the
audit (`leximin.py`) over 6,000 generated instances of 2 to 3 planned cars x 2 to 4
quarters (mixed phases, equal and unequal prices, an unpriced first quarter, a cap, blocks,
on/off cars, limits) that evaluates the levels above the even split from scratch in integer
units, takes the plans tied on all of them and asks whether the first quarter returned by
`optimize` is the most even of the tied plans (sorted allocation vector, at equal total);
it was checked to bite by running it on the pre-fix code (`79d1586`): 9 mismatches in 300
instances where the current code has 0 in 300; (2) a direct differential of the
quarter-0 stage against round 1's `share` on 3,000 generated claim sets (1 to 8 A minimums,
mixed phases, charge-now tier).

| # | Criterion | Met | Evidence |
|---|---|---|---|
| A1 | Plans and allocates within every limit | yes | `test_optimize_never_breaks_a_limit` and `test_the_invariants_hold_for_generated_snapshots` pass (suite: 1377 passed in 138 s). Independent script (no module helpers) over the 600 + 499 decisions that returned: 0 violations of 0-or-min..max, whole amps, on/off exactness, the per-phase budget less reservations, `available`, the window `[now quarter, deadline)`, allocation equal to the plan's first quarter and every later quarter within the budget. The same limits hold in all 6,000 brute-force instances (the brute force enumerates only feasible plans, and the returned plan was always one of them). |
| A2 | Optimal for the stated precedence | yes, one limit noted | The 6,000-instance brute force: the returned plan has the lexicographically optimal tuple (shortfall, unpriced, above cap, short runs, price, forced unpriced first-quarter energy, cars on now, each car in ranking order) in every instance that returned: 0 `LEVELS NOT OPTIMAL` (2 instances raised, see A6). `test_optimize_matches_a_brute_force_enumeration_level_by_level` and the T3 scenarios pass. In the 429 differing decisions of the differential no old plan that is feasible in the new model beats the new plan on any level (`tuples.py`). Limit (deviation 19): the exact price hold covers whole-amp problems only; with a fractional on/off current anywhere in the planned set it falls back to a 1e-6 EUR tolerance, and a one-tick price gap is again traded away (probe `tick.py`: 14 A cheap quarter available, `a` gets 6 A now, the correct answer, when alone, and 9 A when a 7.5 A on/off demand is in the problem). The error is at most the hold tolerance, 1e-6 EUR. |
| A3 | Serves the tiers in order on the leftover; quarter 0 split as evenly as cost allows | **partially: tier order yes, evenness unmet** | Tier order and leftover: `test_a_lower_tier_never_takes_current_a_higher_tier_could_use`, the six-pair optimize test, and in the differential 61 spare-tier gains with no higher tier losing current. The two cases of the first halt are fixed and re-run: 30 A, A limited to 8 A, B and C 6 to 16 A, equal prices: 8 / 11 / 11 (`probe3.py`); two forced cars with the first quarter unpriced: 8 / 8, the same as priced (`probe_unpriced.py`, `probe2.py` through `decide`). **But the split is still not as even as cost allows when the amps left over are fewer than the cars at the lowest level.** The fill (P6 `_fill_now`, and S2/S3 in the quarter-0 tiers) stops when no car can rise together with the others and hands the rest to the best-ranked car, which may already be above the others. Reproduced three ways. (a) Direct: three 6 to 16 A cars on 20 A in any tier (smart-off, charge-now, planned at equal prices, spare) give 8 / 6 / 6 (pinned by `test_optimize_planned_cars_that_cannot_all_rise_share_the_remainder_in_ranking_order`); (2, 1, 1) minimums on 5 A give 3 / 1 / 1. Round 1's `share` (raise the lowest, ties in ranking order) gave 7 / 7 / 6 and 2 / 2 / 1 on the same inputs. (b) Differential against `share`: of 3,000 generated claim sets, 2,969 are identical and 29 are strictly less even at an equal total (e.g. 7 / 7 / 6 becomes 8 / 6 / 6; 3 / 3 / 2 becomes 4 / 2 / 2; 5 / 5 / 5 / 4 becomes 7 / 4 / 4 / 4). (c) Planned tier, independent brute force: of 6,000 instances 5 return a first quarter that a more even split at equal total and equal cost on every level above would beat (seeds 647, 3118, 4103 and two more; 647 is 3 / 1 / 1 against a tied 2 / 1 / 2 on one 5 A phase with minimums 2, 1, 1), and 16,000 instances in a wider run give 22. The generated snapshots (A8) do not reach it because they rarely put three cars at the same level with a smaller remainder. |
| A4 | Keeps the stale-price rule | yes | `test_stale_prices_share_spare_capacity_evenly_with_no_plans`, `test_stale_prices_make_a_smart_session_priority_three`, `test_stale_prices_never_mark_a_session_starved` pass; 0 plans in any `prices_stale` decision of the two sets; `decide.py:581` passes `prices={}` for a stale series. |
| A5 | Deterministic and bounded | yes on the promised grid | Whole `decide`, 4 cores, seeds 1 to 5, `benchmark_snapshot`: 3 chargers @ 35 A `[0.59, 0.18, 0.55, 0.37, 0.94]` s (tier 10 s); 7 @ 70 A `[3.91, 5.41, 2.50, 2.36, 1.90]` (tier 60 s); 10 @ 70 A `[6.81, 6.00, 10.83, 2.50, 47.16]` (tier 120 s). No `PLAN_NOT_PROVEN_OPTIMAL` in any run (the node budget does not bind on the grid). Determinism: 8 decisions hashed under `PYTHONHASHSEED` 0, 1 and 12345 give one SHA-256 (`d7c0c632...b3fd5`); two repeated calls equal. Options in `optimize.py:221-226`: `node_limit = NODE_LIMIT = 8000`, `mip_rel_gap = 0.0`, `presolve = True`, `threads = 1`; no `time_limit` anywhere in `src/` (grep) and two tests spy the `milp` call. A stop at the node budget with a point gives the event (stubbed and real tests). |
| A6 | Raises instead of guessing | **no: HiGHS still reports infeasible for feasible models** | As worded (a HiGHS failure raises `SolverError` naming the status, no partial decision) it holds: `test_decide_raises_solver_error_for_every_failure_status` and the T7 tests. But the first run's defect (a status 2 on a model that is feasible, which the concept's shortfall slack rules out) is not gone; `presolve = True` moved it. (1) 2 of 6,000 tiny brute-force instances (seeds 1931 and 5662; 3 cars, 2 to 4 quarters) raise `HiGHS status 2: infeasible` in level P2; for 1931 the previous solution satisfies every row (max violation 0.0) and the identical model solves with `presolve = False` (status 0) and fails with `presolve = True` (`trace1931.py`). Neither setting is safe, which is what a retry would have to cover. (2) Planned-heavy set #78 (4 chargers, 16 A) raises `HiGHS status 4` (node budget, no point) in the ranking level of P5 after 14 s; the same snapshot returns at `e53aee4` (step 3's final, 31 s) and raises at `d7c8e77` (step 5's first change, 22 s), so step 5's exact price hold or kWh level introduced it. A6 allows the raise as worded, but it is a regression on a set the first run used as its yardstick. The first run's #40, #111 and #246 now pass; the random set (600) has 0 errors. |
| A7 | Removes the greedy planner and declares its dependencies | yes | `PlanRequest`, `plan_sessions`, `Claim`, `share` absent from the package and `__all__`; imports across `src/` are the standard library, `numpy`, `scipy.optimize`, `scipy.sparse` and the package; `pyproject.toml` `dependencies = ["numpy>=2.2,<3", "scipy>=1.15,<2"]`; tests `test_the_round_two_names_are_gone`, `test_the_package_imports_only_the_standard_library_numpy_scipy_and_itself`, `test_the_package_uses_numpy_and_scipy_and_pyproject_declares_them` pass; `STRUCTURE.md` reconciled (below). |
| A8 | Changes nothing else | yes on the generated snapshots; the quarter-0 remainder is recorded under A3 | `model.py`, `quarters.py` and `codec.py` are byte-identical to `2646e7c` (`git diff --stat`); the top-level and per-charger decision keys equal round 1's in all 1,099 decisions; `result.py` only gains `PLAN_NOT_PROVEN_OPTIMAL`. Differential below. |

**The A8 differential (round 1 `2646e7c` against the working tree).**

| Set | Snapshots | Identical | Differ | Fit no kind | Errors old / new |
|---|---|---|---|---|---|
| `random_snapshot`, seeds `7919 k + 13` | 600 | 527 | 73 | 0 | 0 / 0 |
| planned-heavy, seeds `104729 k + 7` | 500 | 143 | 356 | 0 | 0 / 1 (#78, A6) |

`classify.py` assigns every difference its kinds from the decisions alone (a plan change is
re-scored on the model's levels and the old plan is tested for feasibility in the new model;
a first-quarter change is compared by evenness; a lower-tier gain or a reason, status or
event change without a plan change is leftover). Counts, with the two sets together; one
difference can carry two kinds:

| Kind | Count and what was seen | Pinning test |
|---|---|---|
| Optimality | 81 old plans the new model forbids (first quarter above the fallback or user limit or the room left by guard, charge-now or smart-off; overfill beyond one quarter at the top current), 260 strictly better levels, 3 equal levels broken by ranking or earlier-first, and the consequences (priority 2 and 4 swapping, `IN_PLAN` and `PLANNED_LATER`, expected finish, `reaches_target`, events, status). In none does the old plan beat the new one on a level. | `test_a_planned_charger_without_room_now_is_planned_later_and_a_starved_one_waits`, `test_charge_now_takes_the_first_quarter_and_the_plan_starts_after_it`, `test_smart_off_allocations_shrink_the_capacity_planned_for_smart_sessions`, `test_reserved_chargers_shrink_the_capacity_planned_for_smart_sessions`, `test_decide_a_planned_car_ranked_first_charges_first_on_equal_prices`, `test_optimize_gives_the_better_ranked_car_the_earlier_cheap_quarter` |
| Leftover | 61 spare-tier cars gaining current, 11 planned cars gaining what a charge-now car leaves with an unchanged plan, 48 changes of reason code, status or events with no allocation change. | `test_a_lower_tier_gets_what_a_higher_tier_leaves`, `test_a_lower_tier_never_takes_current_a_higher_tier_could_use`, `test_smart_charging_off_is_served_before_smart_chargers`, `test_a_spare_car_behind_a_planned_car_waits_for_higher_priority`; the status-only consequence has no dedicated test (step 8 note) |
| Even split | 1 decision classified on its own (16 / 0 becomes 8 / 8); most planned-tier first-quarter changes also carry an optimality change and are counted there. None is less even than round 1. | `test_two_unreachable_planned_cars_split_a_shared_phase_evenly_now`, `test_optimize_two_planned_cars_at_equal_prices_run_now_evenly`, `test_optimize_three_planned_cars_fill_the_first_quarter_evenly_up_to_each_limit` |

No difference fitted no kind. Not in these sets, and found by the direct `share`
differential only: the quarter-0 stages are less even than round 1 in about 1 % of claim
sets (A3 row). That difference is "a different split within a tier", but in the direction
the concept does not allow, and no test pins it as intended.

**Deviations 16 to 22 (section 3) against section 1.** Deviations 1 to 15 stand as in the
first run (5 and 9 were superseded by 17 and 16).

| # | Verdict |
|---|---|
| 16 P5/P6 as amended; fill exit | **fail**: the full progressive fill and the unpriced case answer the first halt (8 / 11 / 11 and 8 / 8 reproduce). The addition that ends the loop when no car saturates ("the remainder goes in ranking order, as S3 does") is what leaves 8 / 6 / 6 where 7 / 7 / 6 is free; it narrows A3's "as evenly as cost allows" and, since S3 is round 1's `share` described wrongly, also the quarter-0 tiers. |
| 17 `presolve = True` | **fail as a fix**: removes the first run's three errors, but HiGHS still reports infeasible on feasible models (A6 row); the option moved the problem. |
| 18 forced unpriced first quarter in kWh | pass: A2 says unpriced quarters are used only when needed; kWh is the unit that statement is about, amps would let a three-phase car put three times the energy there. It changes no criterion's promise; it makes A2 literal. `test_optimize_an_unpriced_first_quarter_holds_the_forced_energy_not_the_forced_amps` red then green. The level sits between price and even split, so an unpriced first quarter is the later of A2's preferences and the even split still shares what the need forces. |
| 19 exact price hold | pass with a stated limit: it makes A2's price level exact for whole-amp problems with prices on a 1e-7 or coarser grid (all market prices), and keeps the earlier tolerance behaviour elsewhere. It changes no promise; the fallback is wider than the step-5 text suggests, because `whole` is taken over every planned demand, so one fractional on/off pump beside an adjustable car switches the exact hold off for both (A2 row). Worth a line in section 8, not a halt. |
| 20 `Demand` refuses a fractional minimum that can run adjustably | pass: A1 requires whole amps; `Charger.min_current` is an int, so only an on/off or always-off demand reaches `decide` with a fractional minimum, and the final rule lets both through. |
| 21 integrality noise in the fill | pass: a robustness fix with a red test, no promise changed. |
| 22 probes | pass: no code change; a failing probe raises, one stopped at the node budget makes `proven` False. |

**Out of scope.** Nothing from the exclusion list was built (no other loads, no greedy
fallback, no threads, no warm start). Connections: `decide.py` calls `optimize` once and
reads allocations and plans back. Showcases: all eight modules exit 0 and `optimize.main`
is a worked example in the required form. Gates: `ruff check .`, `ruff format --check .` and
`mypy` clean, `pytest` `1377 passed in 138.25s`.

**Structure.** The structure-auditor's five edits (report `r2-audit-before-6b.md`), each
checked against the code:

| # | Edit | Decision |
|---|---|---|
| 1 | `Demand` row | applied, in the auditor's words (on/off in every tier, whole amps otherwise, maximum below minimum means off, validation of `tier` and `deadline`); the row already carried the fractional-minimum rule. |
| 2 | `Outcome` row | applied (`proven` covers probes). |
| 3 | `optimize.py` purpose paragraph | applied, with one change: "share the first quarter by progressive filling" now says in brackets that the remainder when no car can be saturated goes in ranking order, because that is what the code does today and the A3 finding concerns exactly that sentence. |
| 4 | `tests/test_ev_optimize.py` entry | applied in part: step 5 had already added the fill, kWh, tick, noise and probe groups to the entry; added the accepted-case of the fractional minimum and the tick helper's grid detection, give-ups and the one-tick pin. The rest rebutted as already present. |
| 5 | `tests/test_ev_decide.py` entry | applied in part: added the fractional on/off charger under a lower limit, a zero limit and the fallback cap, and the tier invariant not counting a charger the cap keeps off; the tied-at-a-user-limit, forced-unpriced, hash-seed and presolve groups were already present. "Sessions that can never be planned waiting for capacity" rebutted: the tests that carry it predate this round and are covered by "the tiers' waiting reason codes". |

Drift found, and what was done about it:

1. **A3, the remainder of the planned tier's fill and of S2/S3 (halt).** Same criterion as
   the first halt, a different mechanism: then P6 was a single max-min and deviation 9
   skipped it; now the fill ends one step early (deviation 16's exit) and the quarter-0
   stages do the same (S2's freeze rule, then S3). A fix is not a guess at the code but it
   changes pinned 8 / 6 / 6 tests and the plan's S2/S3 rows, and this is the second run:
   halt, not a send-back.
2. **A6, HiGHS reports infeasible on feasible models, now with `presolve = True`.** Same
   defect as the first run's drift item 2. Halt for the same reason.
3. **Notes for step 8, not defects:** (a) the overfill bound changes round 1's behaviour at
   negative prices (12 charger plans in the first run's sets); (b) the brute force at 60
   examples does not bite the minimum-block level; (c) the status consequence of the
   leftover rule has no dedicated test; (d) HiGHS writes
   `HighsMipSolverData::transformNewIntegerFeasibleSolution tmpSolver.run();` lines to the
   console during some solves; (e) deviation 19's fallback (A2 row).

### Earlier rounds still hold

> Later rounds only. Re-check every acceptance criterion from every earlier round in this
> folder: this round changed code they depend on, and their tests passing is necessary but
> not sufficient — a criterion can be satisfied by tests that no longer describe what the
> feature does.

| Round | # | Criterion | Still met | Evidence |
|---|---|---|---|---|
| 1 (`01-stateless-core.md`) | A1 | Stateless | yes | `decide` takes only the snapshot; module-level values are constants; equal decisions on repeated calls and under three hash seeds (A5 row); `SolverError`, `PLAN_NOT_PROVEN_OPTIMAL` and `optimize` are this round's agreed additions, pinned beside round 1's names. |
| 1 | A2 | Round 2 removed cleanly | yes | `ls ev/`: no `guard.py`, no `regulation.py`; grep finds no `settling`, `skip_current`, `next_check` or `memory` outside the codec's rejection. |
| 1 | A3 | Behaves like round 1 | yes, in the amended form | Superseded by this round's A8: every difference in the sets is one of the three kinds; `quarters.py` untouched. |
| 1 | A4 | Keeps every round-1 guarantee | see the next group | |
| 1 | A5 | Round-1-shaped codec | yes | `codec.py` and `model.py` byte-identical to `2646e7c`; the same key sets in 1,099 decisions; the `memory` and round-2 setting rejections pass; a not-proven decision round-trips. |
| 1 | A6 | UTC arithmetic | yes | `optimize.py` converts every input with `astimezone(UTC)` before use and presents `PlannedQuarter.start` in the zone last; `_expected_finish` unchanged; the DST finish tests and the fall-back test in `optimize` pass. |
| ev-charging-core 1 | A1 | Rejects invalid configuration | yes | `model.py` unchanged; its suite passes. |
| ev-charging-core 1 | A2 (amended: tiers 1 to 4, ranking, 0 or min, user limit; the top-tier-only rule and equal split are now this round's A3) | partially | Classification, ranking, 0-or-min and the user limit hold in the invariant checks. Round 1's equal split ended with "the remainder 1 A at a time in ranking order" (7 / 7 / 6); the equal split now rides on A3 and gives 8 / 6 / 6 (A3 row), so this group is partial until A3 is. |
| ev-charging-core 1 | A3 | Respects amp limits | yes | Budget, guard in the first quarter, reservations, on/off exactness: 0 violations in 1,099 decisions. |
| ev-charging-core 1 | A4 (amended: now this round's A2) | Plans the cheapest quarters | yes | A2 row: 92 and 100 quarter days (`quarters.py` untouched), minimum block, cap only to meet the need with its event, reachable kWh, unpriced quarters latest first and flagged; limit noted under deviation 19. |
| ev-charging-core 1 | A5 | Falls back under doubt | yes | `_set_limits` and `phase_capacity` unchanged; the fallback caps only the first planned quarter (test); stale prices give no plan. |
| ev-charging-core 1 | A6 | Explainable, ordered output | yes | `_actions` unchanged (down before up); `actions` change only with allocations. |
| ev-charging-core 1 | A7 | Doc section 12 invariants | yes | Property test and the independent check, 0 violations. |
| ev-charging-core 1 | A8 (amended: now this round's A7) | Deterministic; dependencies | yes | A7 and A5 rows. |
| ev-charging-core 1 | A9 | Round-trips plain dicts | yes | `codec.py` unchanged; codec suite passes. |
| ev-charging-core 1 | A10 (amended: now this round's A5) | Fast | yes | A5 row. |

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

**Answer (user, 2026-10-22):**
1. Worst case: "For the purpose of testing assume 35 A per phase for 1-3 chargers and 50 A
   for 2-5 chargers and 70 A for 3-10 chargers". A5 now names those budgets. The benchmark
   grid is every listed charger count at every listed budget, each against its time tier
   (≤3 chargers 10 s, ≤7 60 s, ≤10 120 s). For example, 3 chargers at 35, 50 and 70 A
   within 10 s, 5 chargers at 50 and 70 A within 60 s, and 10 chargers at 70 A within 120 s.
   At least 5 seeds per cell. A grid tighter than those budgets is not timed.
2. A later level stopped by the node budget with no point: "Raise SolverError". A6 is
   amended to say so. Deviation 7's catch-and-hold (`_NoIncumbentError` held at the earlier
   objective) is reverted: every level solve that ends without a point raises `SolverError`.
   `PLAN_NOT_PROVEN_OPTIMAL` remains for a node-budget stop that returns a point.
`/build` resumes step 3: revert deviation 7, re-run the grid, fix `NODE_LIMIT`, then guide
items 7 and 9.

**Resolved (2026-10-22, step 3 third run):** deviation 7 reverted, the grid run and
`NODE_LIMIT` fixed at 8000 (section 3), tests and `STRUCTURE.md` done.

### Step 6, 2026-10-22

Halted by the concept check on A3 (rule 1: a deviation that narrows what a criterion promises,
and a case the concept never decided). Section 6 has the evidence.

A3 promises that within a tier quarter 0 is "split as evenly as cost allows". For the planned
tier the code does less than that, in two ways that I reproduced:

1. **Only the smallest allocation is made as large as possible.** Level P6 is a single
   max-min (section 2, critique 1: "accepted as stated"). Three planned cars on one phase, 30 A
   per phase, car A limited to 8 A, B and C 6 to 16 A, equal prices, equal needs, nothing
   forcing an uneven first quarter: `optimize` gives 8 / 14 / 8 A where 8 / 8 / 8 (or round 1's
   8 / 11 / 11) costs the same on every level above P6. The rest of the first quarter then
   follows the earlier-first tie-break.
2. **Nothing is split when the first quarter is unpriced** (deviation 9). Two 6 to 16 A cars on
   16 A, empty fresh price series, 1 h to the ready time, each needing 1.84 kWh, so both must
   run in quarter 0: `a` 16 A and `b` 0 A, where 8 / 8 is free. With a priced series the same
   snapshot gives 8 / 8. Deviation 9 says A2 decided this; A2 only names the doc's unpriced
   scenario (latest first) and does not say whether the even split gives way to latest-first
   when a car is forced into an unpriced first quarter.

Question for the user: what does A3 promise for the planned tier?

1. **Keep A3 as written and fix the model.** Make P6 a full progressive fill (several levels,
   as S2 does for the other tiers) and, for an unpriced first quarter, apply the split among the
   cars the need forces on. This costs solves in the planning stage, so the A5 cells must be
   re-timed (10 chargers at 70 A is at 42.7 s of its 120 s tier, 7 at 70 A 4.0 s of 60 s).
2. **Amend A3 to what is built:** for the planned tier, "the smallest first-quarter allocation
   is as large as possible, and only when the first quarter is priced"; later quarters and the
   rest follow the earlier-first tie-break. The two cases above then become documented behaviour.
3. **Middle:** full evenness when the first quarter is priced (keep deviation 9 for the unpriced
   one, where latest-first wins and the amended A3 says so).

Also found, and not a question: HiGHS reports "infeasible" for feasible models with
`presolve = False` (2 of 500 planned-heavy snapshots raised `SolverError` status 2; both solve
in under a second with `presolve = True`; section 6, drift item 2). This is a code defect and
goes back to step 3 together with whatever the answer above changes; the A5 cells must be
re-timed either way.

What exists is committed. On the answer `/build` resumes at step 3.

**Answer (user, 2026-10-22):** "Full even split" — option 1. A3 stays as written. Section 2's
P5 and P6 rows are amended: P6 becomes a full progressive fill over the planned cars on now,
in every case; with quarter 0 unpriced, P5 first holds quarter 0's total at its minimum
(latest-first) and then switches on and ranks the cars as when it is priced, so the cars the
need forces into quarter 0 share it. Deviation 9 is superseded. The `presolve = False` defect
(deviation 5) is fixed in the same pass: `presolve = True`, with the two infeasible snapshots
(#40, #246 in step 6's scratch set) and #111 as regression tests. `/build` resumes at step 3
with both changes; steps 5 and 6 then run again, and the A5 grid is re-timed.

**Resolved (2026-10-22, step 3 fourth run):** P5 and P6 as amended (deviation 16) and
`presolve = True` (deviation 17), the A5 grid re-timed (section 3); steps 4 to 6 run again.

### Step 6, second run, 2026-10-22

Halted by the concept check on A3 and A6, both unmet again (section 6). Not sent back to
step 3: this is the second step-6 run, and both criteria failed on the same ground as the
first (a split that is not as even as cost allows; HiGHS reporting infeasible on a feasible
model), and a third attempt without a decision would be another guess.

1. **A3, the integer remainder.** The first halt's two cases are fixed (8 / 11 / 11 and
   8 / 8 reproduce). What is left: when the amps left over are fewer than the cars at the
   lowest level, the fill stops and the best-ranked car takes the rest even if it is already
   the highest. Three 6 to 16 A cars on 20 A give **8 / 6 / 6** in all four tiers; round 1's
   `share` gave **7 / 7 / 6**. Minimums (2, 1, 1) on 5 A give 3 / 1 / 1; round 1 gave
   2 / 2 / 1. Of 3,000 generated claim sets, 29 are strictly less even than `share` at an
   equal total; of 6,000 generated planned-tier instances, 5 have a more even tied split
   (22 of 16,000). The cause is deviation 16's exit in the planned fill and the freeze rule
   of S2 followed by S3 in the quarter-0 tiers, which the plan described as reproducing
   `share` and does not. `test_optimize_planned_cars_that_cannot_all_rise_share_the_remainder_in_ranking_order`
   pins 8 / 6 / 6.
2. **A6, spurious "infeasible".** With `presolve = True`, 2 of 6,000 tiny instances raise
   `HiGHS status 2` on a feasible model (seed 1931: the last solution satisfies every row, and
   the same model solves with `presolve = False`). Planned-heavy #78 raises `HiGHS status 4`
   (node budget, no point) in a level that returned at `e53aee4`; step 5's changes introduced
   it. The first run's #40, #111 and #246 pass.

Question for the user.

For A3: what does "as evenly as cost allows" mean for the amps that cannot be shared
equally?
1. **As evenly as possible, one amp at a time to the lowest, ties in ranking order**
   (round 1's `share`: 7 / 7 / 6 and 2 / 2 / 1). A3 stays as written. The fill in all four
   tiers changes (S2/S3 and `_fill_now`), the pinned 8 / 6 / 6 test and deviation 16's exit go,
   and the A5 grid is re-timed (the 10-charger worst case is 47 s of 120 s).
2. **Amend A3 to what is built:** equal steps while all can rise, then the whole remainder to
   the best-ranked car (8 / 6 / 6 and 3 / 1 / 1), documented as a change from round 1.

For A6: what should happen when HiGHS reports infeasible on a model the concept says is always
feasible?
1. **Retry once with `presolve` toggled, raise only if both fail.** Deterministic. A6's
   "raises" then covers a failure HiGHS reproduces under both settings; no partial decision.
2. **Keep raising and amend A6** to say a status 2 can occur (2 in 6,000 tiny instances, 0 in
   1,100 snapshots), leaving the caller to retry.

My recommendation is 1 and 1; the decision is yours. On the answer `/build` resumes at step 3,
then 4 to 6 again. Also found, for step 8 whatever is chosen: the exact price hold switches
off for the whole planned set when one on/off demand has a fractional current (section 6, A2
row, 1e-6 EUR).

**Answer (user, 2026-10-22):**
1. A3 remainder: "1 A at a time to lowest" — A3 stays as written. In every quarter-0 split
   (S2/S3 for smart-off, charge-now and spare, and P6 for the planned tier), when no car can
   rise with the others the leftover goes one amp at a time to the lowest car, ties in ranking
   order, exactly round 1's `share` rule (three 6–16 A cars on 20 A give 7/7/6; minimums
   2/1/1 on 5 A give 2/2/1). Deviation 16's exit is replaced.
2. A6: "Retry with presolve off" — a solve that returns status 2, or stops without a point,
   is retried once with presolve toggled; `SolverError` is raised only if the retry fails too.
   Deterministic, no wall-clock limit. A6 amended to say so. #78's node-budget miss since step
   5's first change (d7c8e77) is to be investigated in the same pass.
`/build` resumes at step 3, then steps 4 to 6 run again and the A5 grid is re-timed.

**Resolved (2026-10-22, step 3 fifth run):** the leftover goes one amp at a time to the lowest in
every tier (deviation 23), a failed solve is retried once with presolve toggled (deviation 24),
#78's cause is recorded in section 3, and the A5 grid is re-timed; steps 4 to 6 run again.
