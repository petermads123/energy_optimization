# Interruptible load scheduler — off-time limits

<!-- claude-plan step=9 status=active -->

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
| 2 | Plan | `/plan` | with the user | done |
| 3 | Implement | `/implement` | in `/build` | done |
| 4 | Verify | `/verify` | in `/build` | done |
| 5 | Test | `/test` | in `/build` | done |
| 6 | Concept check | `/concept-check` | in `/build` | done |
| 7 | Ship | `/ship` | in `/build` | done |
| 8 | Recommend | `/recommend` | with the user | done |
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

### Approach

**A shortest path on the quarter grid, with the sliding windows as side constraints,
solved as one MILP by HiGHS.** Nodes are the quarter boundaries 0..T of the horizon (T
priced quarters from the one holding `now`). A path from node 0 to node T is a plan, built
from two kinds of arc:

- an **on arc** `t → t+1`: one quarter on, costing that quarter's price;
- an **off arc** `a → b + K` (K = max(N, 1)): off in `[a, b)`, then the K tail quarters
  `[b, b + K)` on, carrying the rebound. It exists only for M ≤ b − a ≤ X, b + K ≤ T, and when
  the off period passes the minimum saving. It costs
  `Σ_{t∈[b,b+K)} p_t · (1 + r·(b − a)/K)`.

Most limits are built into the arcs, so they hold by construction. X and M are the allowed
arc lengths, and N is the tail: the next off arc can only start after K on quarters. The
end-of-horizon rule (A12) is `b + K ≤ T`, and the minimum saving (A11) is a filter on arcs,
because each off period's saving depends on that arc alone. The windows are the only
constraint that couples arcs. Each one is a linear row over `off_t`, the sum of the off arcs
covering quarter t. Without a window limit (Y = Z) the model is a pure shortest path, and
its LP relaxation is integral. Costs are worked out in units of one quarter at steady-state
power, so the power cancels out; money figures are those units × `power_kw` / 4.

History enters in five places:
- **Off run in progress.** An off run of length h > 0 that is still going makes every path
  start with an *in-progress arc* `0 → min(b + K, T)`, with b ∈ [max(0, M − h), X − h]; when h ≥ X, or any of guards 2–4 fires, the set is {0}, so a hand-switched run longer than X still has an arc. Its
  cost carries the rebound of all h + b quarters.
- **On run after an off run.** An on run of length j < N after an off run means no off arc
  starts before N − j.
- **Rebound still running.** The tail of an off run that has already ended is a constant in
  both costs.
- **Window counts.** The off quarters in the history count towards every window that
  reaches into it.
- **Padding.** History shorter than `Limits.history_needed` counts as on. The padding is
  counted, never built as a list.

**Two levels of solve:**
1. Minimise the cost.
2. With the cost held within a tolerance of level 1's, minimise the total number of off
   quarters (the concept's tie-break).

Level 2's objective is `Σ_t off_t · (1 + (t + 1)/(T·(T + 1)))`. The weights add up to less than 1, so the off count stays primary, and among equal counts the earlier off quarters win. That fixes the rest of the tie-break with no extra solve. Beyond that, HiGHS is deterministic, as in `ev`: one thread, a fixed node budget, no time limit.

**What was rejected.**
- **A compact MILP with one binary per quarter plus start/end indicators.** Its rebound term
  is bilinear (the off-run length times the tail's prices), and linearising it costs more
  variables than the arcs do. Its minimum-saving rule would need big-M rows, where the arc
  model just leaves arcs out.
- **Dynamic programming.** With a sliding window, the DP state has to remember the last
  Z − 1 quarters, which is 2^95 states for a day-long window.

**Only `SolverError` moves from the solver side.** `interruptible` keeps its own `milp` call, retry and node-budget logic, modelled on `ev.optimize._solve`. `ev.optimize` keeps calling the `milp` it imports itself, because about 20 `ev` tests patch `optimize_module.milp`.

**`SolverError` moves to the shared module too.** It is the one other name both cores need,
so it moves under the concept's "what both cores need moves". `ev.optimize.SolverError`
remains the same class, re-exported.

### Letter → field

| Concept | Field | Constraint |
|---|---|---|
| X | `Limits.max_off_run` | ≥ 1 |
| Y | `Limits.window_off_budget` | ≥ 1 (own field); then ≤ Z (names Y), ≥ M (names M) |
| Z | `Limits.window_length` | ≥ 1 |
| N | `Limits.min_on_after_off` | ≥ 0; 0 behaves as 1 |
| M | `Limits.min_off_run` | 1 ≤ M ≤ X, M ≤ Y |
| r | `Load.rebound` | [0, 1] |
| P | `Load.min_saving_percent` | ≥ 0 |

### Modules

| Path | New or changed | Purpose |
|---|---|---|
| `src/energy_optimization/common.py` | new | The shared foundation: `ConfigError`, `SolverError`, the validation helpers (now public), `PriceInterval`, `PriceSeries`, and the generic quarter-grid helpers. |
| `src/energy_optimization/ev/model.py` | changed | Imports `ConfigError`, the price types and the helpers from `common` and re-exports the names it exported before; the moved code is deleted. |
| `src/energy_optimization/ev/quarters.py` | changed | Re-exports `QUARTER`, `floor_to_quarter`, `quarter_starts`, `quarter_prices` and `prices_fresh` from `common`; keeps `next_occurrence`, which is EV-only (local ready times). |
| `src/energy_optimization/ev/optimize.py` | changed | Re-exports `SolverError` from `common`. |
| `src/energy_optimization/interruptible/__init__.py` | new | Re-exports the new core's public API with `__all__`. |
| `src/energy_optimization/interruptible/model.py` | new | Input types: `LoadState`, `Limits`, `Load`, `LoadSnapshot`, each validating itself. |
| `src/energy_optimization/interruptible/result.py` | new | Output types: `ReasonCode`, `Reason`, `ScheduledQuarter`, `LoadSchedule`. |
| `src/energy_optimization/interruptible/scheduler.py` | new | `schedule()`: horizon, history, arcs, the two-level MILP, the reason, the money figures. |
| `src/energy_optimization/__init__.py` | changed | The docstring names both subpackages and `common`. |
| `STRUCTURE.md`, `README.md`, `CLAUDE.md`, `pyproject.toml` | changed | The new layout. `README.md` gets a short "what is in the package" section. `CLAUDE.md` only if it names the layout (today it does not). `pyproject.toml` gets its `description` updated. |

Dependency order: `common` is a leaf. `ev.*` and `interruptible.*` import from `common`, and
neither imports the other. Every new module ends in a `main()` showcase and uses only the
standard library, `numpy` and `scipy` (the existing AST test in `test_ev_decide.py` scans the
whole package).

### Public API

`common.py`. The moved names keep their exact signatures, error types and messages:

| Signature | Purpose | Covers |
|---|---|---|
| `class ConfigError(ValueError)`; `__init__(self, field: str, message: str) -> None` | Moved from `ev.model`, unchanged. | A7, A10 |
| `class SolverError(RuntimeError)`; `__init__(self, status: int, message: str) -> None` | Moved from `ev.optimize`, unchanged. | A10, A2 |
| `fail(field: str, message: str, ctx: str = "") -> ConfigError` | Was `ev.model._fail`. | A7, A10 |
| `check_number(field: str, value: object, ctx: str = "") -> float` | Was `_number`. | A7, A10 |
| `check_whole(field: str, value: object, ctx: str = "") -> int` | Was `_whole`. | A7, A10 |
| `check_aware(field: str, value: object, ctx: str = "") -> datetime` | Was `_aware`. | A7, A10 |
| `check_bool(field: str, value: object, ctx: str = "") -> bool` | Was `_bool`. | A7, A10 |
| `check_text(field: str, value: object, ctx: str = "") -> str` | Was `_text`. | A10 |
| `set_field(obj: object, field: str, value: object) -> None` | Was `_set`. | A10 |
| `to_utc(moment: datetime, name: str) -> datetime` | Was `ev.quarters._utc`; `ValueError` naming `name` if naive. | A10 |
| `PriceInterval(start: datetime, end: datetime, price: float)` | Moved, unchanged. | A7, A10 |
| `PriceSeries(intervals: tuple[PriceInterval, ...], updated_at: datetime)` | Moved, unchanged. | A10 |
| `QUARTER: timedelta` | Moved. | A10 |
| `floor_to_quarter(moment: datetime) -> datetime` | Moved. | A10 |
| `quarter_starts(start: datetime, end: datetime) -> list[datetime]` | Moved. | A10 |
| `quarter_prices(series: PriceSeries) -> dict[datetime, float]` | Moved. | A10 |
| `prices_fresh(series: PriceSeries, now: datetime, max_age: timedelta) -> bool` | Moved. | A10 |
| `main() -> None` | Showcase: a price series split into quarters, a rejected field. | — |

`ev.model` imports the helpers under their old private names (`from energy_optimization.common import check_number as _number`, …), so its class bodies are untouched. Imports are **absolute**, because Ruff's TID252 bans parent-relative imports. The re-exports use the redundant-alias form (`from energy_optimization.common import ConfigError as ConfigError`), so Ruff and mypy treat them as deliberate.

`interruptible/model.py`. All dataclasses are frozen and validate in `__post_init__`,
raising `ConfigError` with the field name:

| Signature | Purpose | Covers |
|---|---|---|
| `class LoadState(StrEnum)`: `ON = "on"`, `OFF = "off"` | A quarter's state, in history, plan and decision. | A1 |
| `Limits(max_off_run: int, window_off_budget: int, window_length: int, min_on_after_off: int = 0, min_off_run: int = 1)`; property `history_needed -> int` = max(Z − 1, X, N) | X, Y, Z, N and M. Integral floats are accepted, as in `ev`. Per-field ranges are checked first (X ≥ 1, Y ≥ 1, Z ≥ 1, N ≥ 0, M ≥ 1, each naming its own field), then the cross-checks: Y > Z names `window_off_budget`; M > X and M > Y name `min_off_run`. | A1, A7, A8 |
| `Load(limits: Limits, rebound: float, min_saving_percent: float = 0.0, power_kw: float \| None = None)` | `rebound` in [0, 1]; `min_saving_percent` ≥ 0 and finite; `power_kw` > 0 or None. | A6, A7, A11 |
| `LoadSnapshot(now: datetime, load: Load, prices: PriceSeries, history: tuple[LoadState, ...] = (), force_on: bool = False)` | One call's input. `history` is oldest first, its last entry the quarter before the one holding `now`. Any sequence is accepted and becomes a tuple; a non-`LoadState` entry is refused, and the message names its index. | A5, A7, A8 |
| `main() -> None` | Showcase: a fridge's limits and a snapshot; then `ConfigError` for M > X. | — |

`interruptible/result.py`:

| Signature | Purpose | Covers |
|---|---|---|
| `class ReasonCode(StrEnum)`: `NO_PRICES`, `FORCED_ON`, `MAX_OFF_RUN`, `WINDOW_BUDGET`, `MIN_ON_AFTER_OFF`, `END_OF_HORIZON`, `MIN_OFF_RUN`, `PLANNED_OFF`, `PLANNED_ON`, `NO_SAVING` | Why the current quarter is what it is. Values are the lower-case names. | A4, A5 |
| `Reason(code: ReasonCode, text: str)` | The code plus a sentence naming the numbers. | A4 |
| `ScheduledQuarter(start: datetime, price: float, state: LoadState)` | One planned quarter; `start` in UTC. | A1 |
| `LoadSchedule(state: LoadState, reason: Reason, plan: tuple[ScheduledQuarter, ...], saving_percent: float \| None, always_on_cost: float \| None = None, planned_cost: float \| None = None, saving: float \| None = None, history_short: bool = False, proven: bool = True)` | The answer. `state` is `plan[0].state`, or `ON` when the plan is empty. `saving_percent` is None only when the always-on cost is 0 or there are no prices. The three money fields are set only when `power_kw` is given. `proven` is False when a level stopped at the node budget. | A6, A8 |
| `main() -> None` | Showcase: a small schedule built by hand. | — |

`interruptible/scheduler.py` (the module is not named `schedule`: `__init__` re-exports the function `schedule`, which would shadow a module of that name and make the tests' `milp` spies patch nothing):

| Signature | Purpose | Covers |
|---|---|---|
| `NODE_LIMIT: int` | Branch-and-bound nodes per level solve: 8000, as in `ev`. | A9, A13 |
| `schedule(snapshot: LoadSnapshot) -> LoadSchedule` | Plan the load. Raises `SolverError` when a solve has no point (or status 2) after its one retry with presolve off, or at once on a status other than 0, 1 or a node-budget 4 when it has a point. | A1–A6, A8, A9, A11–A13 |
| `main() -> None` | Showcase: a fridge across an evening price peak; then the same load with the override on. | — |

`interruptible/__init__.py` re-exports `LoadState`, `Limits`, `Load`, `LoadSnapshot`,
`ReasonCode`, `Reason`, `ScheduledQuarter`, `LoadSchedule`, `schedule`, plus `ConfigError`,
`SolverError`, `PriceInterval` and `PriceSeries` from `common`, so a caller needs only one
import.

### Implementation guide

1. **`common.py`.** Move `ConfigError`, the helpers (renamed public), `PriceInterval`,
   `PriceSeries`, `QUARTER`, `floor_to_quarter`, `quarter_starts`, `quarter_prices`,
   `prices_fresh` and `SolverError` here, with docstrings and messages kept character for
   character. Add `to_utc` and `main()`.
2. **Repoint `ev`.**
   - `ev/model.py` imports from `energy_optimization.common` (absolute): the helpers under their old underscore names,
     and the three public names re-exported. The moved definitions are deleted.
   - `ev/quarters.py` re-exports the five grid names, and `next_occurrence` uses `to_utc`.
   - `ev/optimize.py` re-exports `SolverError`.
   - Run the whole `ev` suite. **It must pass with `tests/test_ev_*.py` untouched** before
     anything else is written.
3. **`interruptible/model.py`**, in the order of the table.
4. **`interruptible/result.py`.**
5. **`interruptible/scheduler.py`**, as private steps inside `schedule()`:
   1. **Horizon.**
      - `start = floor_to_quarter(now)`.
      - The prices come from `quarter_prices(prices)`.
      - T is the count of contiguous priced quarters from `start`.
   2. **History facts.** With the padding counted, not built:
      - **h:** the off run ending at the last history quarter.
      - **j:** the on run ending there.
      - **Previous run:** the length of the off run before that on run, as far as the
        history shows it.
      - **Window counts:** for each window reaching into the horizon, the number of history
        off quarters it covers.
      - **`history_short`:** whether the history is shorter than `history_needed`.

      Every fact is derived from `history[-history_needed:]` with on-padding before it, so older history changes nothing, not even the money figures. Windows are grouped by their horizon range `[max(s, 0), min(s + Z, T))`. Each range
      keeps the largest history count, so the work is O(T + len(history)), not O(Z).
   3. **Current-quarter guards** that decide the reason before any solve, in this
      precedence:
      1. T = 0 → `NO_PRICES`, with an empty plan, all figures None and state `ON`.
      2. `force_on` → `FORCED_ON`.
      3. h ≥ X → `MAX_OFF_RUN`.
      4. Being off now would push a window over its budget, counting history. "Off now" is
         the in-progress run continuing to max(1, M − h) quarters when h > 0, or M quarters
         when h = 0. → `WINDOW_BUDGET`.
      5. h = 0 and j < N after an off run → `MIN_ON_AFTER_OFF`.
      6. No off arc or in-progress continuation can start at 0 inside the horizon (h = 0 and
         M + K > T; or h ≥ M and 1 + K > T) → `END_OF_HORIZON`.

      In cases 2–6 the current quarter is held on in the model.
   4. **Arcs.**
      - **On arcs.**
      - **Off arcs**, under the rules in Approach. A new off arc may not start at 0 when
        h > 0, when `force_on` is set, or before N − j. It is kept only if `saving > tol`
        and `saving ≥ P/100 · |base|`, where `base` is the always-on cost of `[a, b + K)`
        and `saving = base − arc cost`.
      - **In-progress arcs** (h > 0), for the allowed b. The shortest allowed one may clip
        its tail at T; the longer ones must fit (A12's exception). When guard 2 or 4 holds,
        only b = 0 is allowed.
      - A constant for the rebound tail of an off run that has already ended (clipped at
        T).
   5. **MILP.**
      - **Variables:** one binary per arc, and `off_t` continuous in [0, 1].
      - **Rows:** flow conservation (source 0, sink T); `off_t` = the sum of off and
        in-progress arcs covering t; one window row per distinct horizon range,
        `Σ off_t ≤ max(0, Y − history count)`.
      - **Level 1** minimises the cost.
      - **Level 2** minimises `Σ off_t` with `cost ≤ c1 + tol`.
      - **Options:** `{"node_limit": NODE_LIMIT, "mip_rel_gap": 0.0, "presolve": ..., "threads": 1}` (as `ev` passes them, with the same warning filter); no `time_limit`.
      - **Failures**, mirroring `ev.optimize._solve`: a result with `status == 2` or no point is retried once with presolve off, and the retry is the result judged. A retry still without a point, or still status 2, raises `SolverError(status, message)` with the retry's status and message. With a point, status 0 is proven, and status 1, or status 4 with `mip_node_count >= NODE_LIMIT`, is kept with `proven=False`. Any other status raises `SolverError` at once, without a retry.
   6. **Plan.** Walk the chosen path into `ScheduledQuarter`s. The reason is the guard's if
      one fired. Otherwise:
      - off now: `MIN_OFF_RUN` while 0 < h < M, else `PLANNED_OFF`;
      - on now: `PLANNED_ON` if the plan has an off quarter, else `NO_SAVING`.
   7. **Figures.**
      - `planned` is the path's cost.
      - `always_on` is the cost of the in-progress arc with b = 0 (clipped) followed by on
        arcs, i.e. switching on now. Both include the constant history tail.
      - **A clipped in-progress arc** (its tail cut at T) has the rebound energy that falls past T charged at the last horizon price, on both sides, in the figures only. This keeps the "looks free" effect A12 guards against out of the reported saving. The optimisation is unaffected, because a clipped arc is always the only one allowed.
      - `saving_percent` is `100 · (always_on − planned) / |always_on|`, or None when
        `always_on == 0`.
      - With power given, `planned_cost` and `always_on_cost` are the units × `power_kw` /
        4, and `saving` is their difference.
   8. **Tolerance.** `tol = 1e-9 · max(1, Σ_t |p_t|)` is used both for the level-2 cost hold
      and for "saving > 0" on an arc, so r = 1 at a flat price leaves no off arcs despite
      rounding.
6. **`interruptible/__init__.py`.** Each module's `main()`.
7. **Docs.** `STRUCTURE.md` gets the tree, `common`, the `interruptible` tables, the new
   test files and the dependency order. Also update `README.md`, the package docstring, the
   `pyproject.toml` description, and `CLAUDE.md` if needed.

### Test intents

New test files: `tests/test_common.py`, `tests/test_interruptible_model.py`,
`tests/test_interruptible_result.py`, `tests/test_interruptible_schedule.py`. The
`tests/test_ev_*.py` files are not edited.

| # | Must prove | Covers |
|---|---|---|
| T1 | A `hypothesis` property over generated limits, histories and prices: every plan keeps X, Y-of-Z (windows reaching into the history), N after every off period (counting the history's), and M for every off period that starts in the horizon. Quarters that the history already constrains are on. Checked by an oracle written independently of the module. | A1 |
| T2 | On horizons of up to ~10 quarters, with history, rebound and minimum saving, the plan's cost equals the minimum found by enumerating every on/off sequence that keeps the limits and the minimum saving. Among equal-cost plans, the plan has the fewest off quarters. | A2 |
| T3 | A flat price with r = 1 starts no off period in the horizon, with and without history, for any limits: always-on, except that an off run in progress below M continues exactly to M and is on afterwards. A flat price with r < 1 does plan off quarters. | A3 |
| T4 | A history whose off run has reached X gives `on` and `MAX_OFF_RUN`. A history that fills a window's budget gives `on` and `WINDOW_BUDGET`. History that breaks a limit (a hand-switched plug) is accepted, and the constrained quarters are on. | A4, A1 |
| T5 | `force_on` gives `on` and `FORCED_ON`, including mid-run below M. The rest of the plan keeps the limits from quarter 1 on. | A5 |
| T6 | `saving_percent` matches a hand calculation; the money fields appear only with `power_kw` and scale with it; r = 1 at a flat price gives 0 %, including with a forced, clipped continuation; a zero always-on cost gives None; negative prices give a well-defined percentage. | A6 |
| T7 | `ConfigError` names the field for every invalid input: each limit below its range, M > X, M > Y, Y > Z, r outside [0, 1], a negative or non-finite minimum saving, `power_kw` ≤ 0, bools and non-finite numbers, a naive `now`, a non-`LoadState` history entry, and prices off the quarter grid. | A7 |
| T8 | A short or empty history is padded as on and flags `history_short`. A history longer than `history_needed` gives the same `LoadSchedule` (plan, reason and every figure) as its tail. A huge `window_length` with a short history neither materialises the padding nor slows down. | A8 |
| T9 | Repeated calls are equal; inputs are unchanged; the solver call is spied for one thread, `NODE_LIMIT`, no `time_limit`, and `mip_rel_gap` 0, and presolve off only on the retry. The spies patch the `scheduler` module. | A9 |
| T10 | `ev.model.ConfigError is common.ConfigError` (and likewise for the price types, the grid helpers and `SolverError`); every name in `ev.__all__` still imports; the `ev` suite is green. That `tests/test_ev_*.py` is unchanged against `main` is checked at steps 4 and 6 and recorded in the plan file, not asserted in pytest. | A10 |
| T11 | At P %, every off period that starts in the horizon saves ≥ P % of its affected quarters' always-on cost. Inputs where no period reaches P % give always-on and `NO_SAVING`. The plan is identical with and without `power_kw`. | A11 |
| T12 | The last max(N, 1) quarters are on in every plan. A run in progress below M continues to M even when that pushes its tail past T. A horizon too short to fit M + K gives `END_OF_HORIZON`. | A12 |
| T13 | 192 quarters, Z = 96, Y = 32, X = 8, N = 4, M = 2, r = 0.8, on five price seeds: each within 10 s. | A13 |
| T14 | The reason table: `NO_PRICES` (no price now, and an unpriced gap ending the horizon), `MIN_ON_AFTER_OFF`, `MIN_OFF_RUN`, `PLANNED_OFF`, `PLANNED_ON`, `NO_SAVING`, and the precedence when several hold. | A4, A5 |
| T15 | `common`: the moved helpers' behaviour and messages; `to_utc`. All new `main()` showcases run. | A10 |
| T16 | A stubbed `milp` with no point, or with status 2, is retried with presolve off, then raises `SolverError` with the retry's status. A node-budget stop with a point (status 1, or 4 at the node limit) gives `proven=False`. Status 3, or 4 below the limit, raises at once. | A2, A9 |

### Coverage

- Every criterion A1–A13 has a Public API entry: A1 `LoadState`/`Limits`/`schedule`, A2
  `schedule`, A3 `schedule`, A4 `ReasonCode`, A5 `LoadSnapshot.force_on`, A6 `LoadSchedule`
  fields, A7 the validators, A8 `history_needed` and `history_short`, A9 `schedule`, A10
  `common`, A11 `Load.min_saving_percent`, A12 and A13 `schedule`/`NODE_LIMIT`.
- Every criterion has a test intent: A1 T1/T4, A2 T2/T16, A3 T3, A4 T4/T14, A5 T5/T14, A6
  T6, A7 T7, A8 T8, A9 T9, A10 T10/T15, A11 T11, A12 T12, A13 T13.
- Every Public API entry traces to a criterion. `main()` is the repo convention, and
  `SolverError`'s move is A10.

### Risks

- **Performance at large X.** There are about T × (X − M + 1) off arcs. The A13 case
  (X = 8) has ~1.3k arcs, which is small. A window-only load with X = Y = 32 has ~6k arcs
  and ~100k nonzeros, and should still be well under 10 s. If the A13 case is over 10 s, the
  build may tighten the formulation (for example, aggregate the window rows), but it does not
  relax A13: a second failure halts. A slow case outside A13 goes into `DEVELOPMENT.md`.
- **HiGHS calling a feasible model infeasible**, as `ev` saw. Always-on is always feasible
  here, so the retry with presolve off covers it. A real infeasibility would be a bug in the
  arc rules, and the build fixes it, never by relaxing a limit.
- **Float noise** in the minimum-saving filter and the level-2 hold. `tol` covers both, and
  the T2 brute force compares costs with `pytest.approx`.
- **The move breaks an `ev` assertion** (a message, a module path, an identity check). Keep
  messages character for character. If an `ev` test assertion would have to change, **halt**:
  that amends A10.
- **The reason precedence disagrees with the solved plan**, e.g. a guard says on but the
  model would choose off. The guards also fix quarter 0 on in the model, so this cannot
  happen silently. A test finding a case the precedence does not decide is a **halt** (it
  amends A4/A5).
- **How A3 is read.** A3's "always-on" means no off period *starts* in the horizon. An off run in progress below M is a history commitment, and the concept ranks it above cost, so it continues to M even at a flat price with r = 1. T3 tests that reading. A test that finds a different one is a **halt**.
- **A history the concept does not decide.** One example is an off run truncated by the
  start of the history: its rebound uses the visible length, which is stated here. Anything
  else of that kind is a **halt**.

---

### Critique

From the `plan-critic`, which gave the verdict "accept with changes". Each finding is listed with what was done about it.

1. **An in-progress run with h > X had no arcs, so it would raise `SolverError`.** Applied: the allowed set is {0} when h ≥ X or any of guards 2–4 fires.
2. **A module named `schedule.py` is shadowed by the re-exported function, so the `milp` spies would patch nothing.** Applied: the module is renamed `scheduler.py`.
3. **Solver options and statuses.** Applied: `mip_rel_gap` 0, a retry on status 2 or no point, node-budget detection, an immediate raise on other statuses, all mirroring `ev.optimize._solve`. T9 and T16 are extended.
4. **A clipped forced continuation made A6 report a false saving, and A3 can't hold with a run in progress below M.** Applied: the figures charge the past-T rebound at the last price. A3 is read as "no off period starts in the horizon" (Risks and T3), and the user is told when the plan is presented.
5. **Ruff's TID252 bans `from ..common`.** Applied: absolute imports.
6. **History facts must come from the look-back only.** Applied: everything is derived from `history[-history_needed:]`, and T8 compares the whole `LoadSchedule`.
7. **Don't share the solve wrapper, since the `ev` tests patch `optimize.milp`.** Applied: stated in Approach.
8. **`window_off_budget` had no range of its own.** Applied: Y ≥ 1 names `window_off_budget`.
9. **A `git diff` doesn't belong in pytest.** Applied: it moves to the step 4 and step 6 records.
10. **The tie-break beyond the off count.** Applied: weighted level-2 objective, with earlier off quarters winning among equal counts.


---

## 3. Implementation notes

> Written in step 3. Only deviations from the plan above, each with its reason. "Built as
> planned" is a complete and good entry. On a fix round, also the reproduction test's red
> run, pasted here before the fix was written — step 6 cites it.

Built as planned; the Public API table matches the code. Points the plan left open, decided
while writing, none of which touches section 1:

- `tests/test_ev_*.py` is untouched (`git diff main -- tests/test_ev_*.py` is empty) and the
  whole suite was green after guide step 2, before the new subpackage was written.
- `ev.model` keeps the old private names (`_fail`, `_number`, ...) as import aliases of the
  new public `common` names, so its class bodies are unchanged.
- `LoadSchedule.history_short` is set on the `NO_PRICES` result too; nothing else is reported there.
- Window rows: windows starting before the visible history are dropped as implied by the one
  starting at its first quarter, and a row whose budget is at least its length is dropped as
  trivial. This keeps the work O(T + len(history)).
- The on arcs are continuous in [0, 1]; only off and run arcs are integer. Flow conservation
  makes the on arcs integral once those are.
- A clipped run arc's rebound past T is charged at the last horizon price in the figures
  only. The constant tail of an off run that has already ended is not charged past T.
- `saving_percent` is None when `|always_on| <= tol`, and a difference within `tol` is
  reported as exactly 0 (so r = 1 at a flat price gives 0 %).
- Sanity check outside the suite (scratchpad, not committed): 400 random small horizons
  against a brute-force enumeration agreed on cost and off count except where the
  enumeration was looser than the plan's rules (a run clipped at T, or a history that
  already breaks N); A13's case (192 quarters, Z = 96, X = 8, N = 4, M = 2, r = 0.8)
  solved in 1 to 3 s on five seeds. Step 5 builds the real oracle.
- Step 5 fixes (code changed after step 4, no signature changed): `_past_horizon` charges the
  rebound of a clipped run for the off quarters seen (`run + min(extra, T)`), not for quarters
  past the horizon that are never credited, so a forced continuation longer than the horizon
  reports 0 % at a flat price with r = 1 (T6); `LoadSnapshot.history` refuses a set, frozenset
  or mapping (unordered), still accepting any ordered iterable; the `LoadSchedule.saving_percent`
  docstring says "0 (within a rounding tolerance)".
- `README.md`, the package docstring and the `pyproject.toml` description are updated;
  `CLAUDE.md` does not name the layout and is unchanged. `STRUCTURE.md` gained `common`,
  the `interruptible` subpackage and the re-export notes for `ev`; the new test files are
  added to it in step 5.

---

## 4. Verification log

> Written in step 4: the static half. Command output, not a summary of it.

| Check | Result |
|---|---|
| `ruff check .` | `All checks passed!` |
| `ruff format --check .` | `63 files already formatted` |
| `mypy` | `Success: no issues found in 31 source files` |
| `pytest` | `1525 passed in 122.46s (0:02:02)` |
| Plan completeness | every signature in the Public API table exists as written; no Missing, Deviation or Unplanned rows |
| `STRUCTURE.md` | in sync (structure-auditor: no edits needed; the code was not changed in this step, so the report still holds) |
| `tests/test_ev_*.py` against `main` (T10) | `git fetch origin main`, then `git diff --stat origin/main -- 'tests/test_ev_*.py'`: empty output, exit 0 (unchanged) |
| `python -m energy_optimization.<module>` | `common`, `ev.model`, `ev.quarters`, `ev.optimize`, `interruptible.model`, `interruptible.result`, `interruptible.scheduler` all exit 0; the `RuntimeWarning` on the re-exported modules is the expected one |

Showcase output (new modules):

```
common:
4 quarters, each at 1.25:
  2026-10-21T18:00:00+00:00 ... 18:45
rejected: end: must be on a quarter-hour boundary, got 2026-10-21T19:10:00+00:00 (field=end)
interruptible.model:
look-back: 15 quarters
snapshot: Limits(max_off_run=4, window_off_budget=8, window_length=16, min_on_after_off=2, min_off_run=2) at 2026-10-21T17:00:00+00:00
rejected: min_off_run: must be ≤ max_off_run (4), got 6 (field=min_off_run)
interruptible.result:
now: off (planned_off: off in the dearest quarter)
plan: ['off', 'on']
saving: 12.5 %, proven=True
interruptible.scheduler:
now: off (planned off; the plan has 10 off quarters in all)
plan (# on, . off): ..##########..##....####..######
saving: 19.6 % (0.3525)
forced: on (forced_on)
```

Showcases are in the required form (named inputs, call on its own line, named result, printed).

---

## 5. Test log

> Written in step 5: the dynamic half.

| Intent | Test names | Result |
|---|---|---|
| T1 | `test_t1_every_plan_keeps_every_limit_counting_history` (hypothesis, 150 examples; also run at 1500 off-suite) | pass |
| T2 | `test_t2_plan_is_the_cheapest_then_fewest_off_then_earliest` (hypothesis brute force, 120 examples; 3000 off-suite), `test_schedule_hand_checked_plans` | pass |
| T3 | `test_t3_flat_price_with_full_rebound_starts_no_off_period` (15), `test_t3_flat_price_full_rebound_*`, `test_t3_run_in_progress_*`, `test_t3_negative_flat_prices_*` | pass |
| T4 | `test_t4_max_off_run_boundary`, `test_t4_window_budget_*`, `test_t4_history_that_breaks_*` | pass |
| T5 | `test_t5_*` (force_on, mid-run, past X, N after the forced end, later off quarters) | pass |
| T6 | `test_t6_*` (hand percentages, money scaling, zero and negative always-on, the regression below) | pass |
| T7 | `test_interruptible_model.py` (every field, `Limits` order of checks, naive `now`, bad history, bad `load`/`prices`), `test_t7_naive_now_*` | pass |
| T8 | `test_t8_*` (flag at the look-back, padding, older history equal as a whole `LoadSchedule`, huge window under 1 s) | pass |
| T9 | `test_t9_*` (repeat, purity, UTC starts, spied options: one thread, node 8000, gap 0, no `time_limit`) | pass |
| T10 | `test_common.py` identity tests, `__all__` imports; the `ev` suite untouched and green | pass |
| T11 | `test_t11_*` (inclusive boundary, rebound spread, zero span, no period reaches P, with/without power, hypothesis) | pass |
| T12 | `test_t12_*` (END_OF_HORIZON boundaries, last N on, clipped continuation) | pass |
| T13 | `test_t13_a_192_quarter_horizon_*` (five seeds) | pass; one seed takes 7.4 s |
| T14 | `test_t14_*` (every code, 8-row precedence table, N boundary) | pass |
| T15 | `test_common.py` helpers, `to_utc`, grid, price types; every `main()` showcase test | pass |
| T16 | `test_t16_*` (retry on no point and status 2, second failure's status/message, status 1 and 4 at the limit proven False, status 3 and 4 below the limit at once, second-level failure) | pass |

Full suite: `1843 passed in 135.67s`; `ruff check`, `ruff format --check` and `mypy` clean; `git diff --stat origin/main -- tests/test_ev_*.py` empty.

**Bugs the tests found**

1. `_figures`/`_past_horizon` (input-space Contradiction 1, confirmed): history `-`, `lim(4,4,4,0,4)`, flat `[1, 1]`, r = 1 gave -33.33 % instead of 0 %. The forced run is longer than the horizon, and rebound was charged for off quarters past the horizon that the always-on figure never credits. Red run before the fix: `test_t6_clipped_continuation_longer_than_the_horizon_reports_zero_at_flat_r1` and `...[prices0]` failed with `-33.333333333333336`. Fix: charge for `run + min(extra, horizon)` quarters. The plan itself was never affected. Green after.
2. `LoadSnapshot.history` accepted a set or mapping (contract Contradiction 2). "Any sequence" in the plan and "oldest first" in the concept need an order, so these are refused with `ConfigError` naming `history`; generators and other ordered iterables still work.

**Designer contradictions, applied or rebutted**

- Input-space 1: applied (bug 1).
- Input-space 2 (a zero affected cost passes any P): rebutted. The concept measures against the absolute value, so P % of 0 is 0 and any saving above tolerance is enough; pinned by `test_t11_zero_span_passes_any_threshold`.
- Input-space 3 (`MAX_OFF_RUN` text names the capped visible run): rebutted. The core only sees the look-back, which is at least X, and the text names the numbers it used; noted for step 6, no test pins the capped figure.
- Input-space 4 / contract 1 (`saving_percent` None within `tol`, not only at exactly 0): rebutted as the plan's rule (section 3 note on `tol`); the `LoadSchedule` docstring now says "within a rounding tolerance". Prices of 1e-10 are out of range for any currency unit.
- Input-space 5 (per-arc saving can exceed 100 % with negative tail prices): not a defect; `test_t11_percent_above_one_hundred_can_still_allow_a_negative_price_tail` pins it.
- Contract 2 (history as a set): applied (bug 2).
- Contract 3 (`NO_SAVING` for a run that ends now): rebutted as guide step 5.6's rule; pinned as "not endorsed" by `test_t14_run_ending_now_without_later_off_reports_no_saving`, for step 6 to judge.
- Contract 4 (history rebound constant clipped at T): not a contradiction; section 3 states it, and no test changes it.
- Contract 5: no disagreement.

Oracle notes (test-side only): the T1/T2 oracle reads the concept's rules, not the module. Three oracle mistakes were found and fixed while writing it (a forced continuation cut by the horizon, the M rule for a run that ends at T, and the rebound of a run ending exactly now); after that 3000 random cases of up to 9 quarters with history, rebound and minimum saving agreed with the module on cost, off count and earliest placement.

Edge cases considered and deliberately skipped, with reasons:

- `Decimal`, `numpy` bools and text inputs for the limits: `check_number`/`check_whole` are shared with `ev` and covered in `test_common.py`; the limits have no free-text field.
- A 48-hour horizon or larger than 192 quarters: A13 fixes the size; a slow larger case is for `DEVELOPMENT.md`, not a test that would flake.
- Asserting the exact `MAX_OFF_RUN` figure for a run longer than the look-back: see the rebuttal above.
- Time-zone named inputs: the core has no timezone; odd UTC offsets are covered (`test_t9_now_inside_a_quarter_*`, `test_schedule_horizon_runs_*`).
- Concurrency: the core is stateless and pure; repeat and purity tests cover it.
- T13 wall-clock margin: seed 3 takes about 7.4 s (level 2 is one LP of 5.5 s, one node), under the 10 s limit but not by a wide margin on a slower machine. Not relaxed; flagged for step 6/8.

---

## 6. Concept check

> Written in step 6, against section 1 — not against section 2. The question is whether
> the thing built is the thing agreed, not whether it matches the plan.

| # | Criterion | Met | Evidence |
|---|---|---|---|
| A1 | No breach of any limit counting history | yes | `test_t1_every_plan_keeps_every_limit_counting_history` (hypothesis, oracle independent of the module); arcs fix X/M/N/A12 by construction (`scheduler.py` `_arcs`), window rows from `_windows`; history-breaking cases `test_t4_history_that_breaks_*` |
| A2 | Lowest cost among valid plans, brute-force checked | yes | `test_t2_plan_is_the_cheapest_then_fewest_off_then_earliest` (3000 off-suite examples agreed per step 5); two-level solve `scheduler.py:490-496` |
| A3 | Flat price, r = 1 gives always-on | yes | `test_t3_*`; arcs need `saving > tol` (`scheduler.py:207`); the run-in-progress-below-M reading is the one section 1 ranks (history commitment above cost) |
| A4 | History at budget or run at X gives on, reason names the limit | yes | `test_t4_max_off_run_boundary`, `test_t4_window_budget_*`; `_guard` `scheduler.py:148-156` |
| A5 | Override forces on, rest keeps limits | yes | `test_t5_*`; `FORCED_ON` guard and first=1 in `_arcs` |
| A6 | Saving percent, money with power, 0 % at flat r = 1 | yes | `test_t6_*` incl. clipped-continuation regression; `scheduler.py:524-536` |
| A7 | `ConfigError` naming the field | yes | `tests/test_interruptible_model.py`, `test_t7_*`; M > X, M > Y, Y > Z, r, P, off-grid prices |
| A8 | Short history padded on, flagged | yes | `test_t8_*` (flag at look-back, older history equal as whole `LoadSchedule`, huge window under 1 s); `_facts` uses `history[-needed:]` |
| A9 | Deterministic, no mutation | yes | `test_t9_*` (repeat, purity, solver options spied) |
| A10 | Shared module; `ev` names importable; ev suite unchanged | yes | `git fetch origin main` then `git diff --stat origin/main -- 'tests/test_ev_*.py'`: empty, exit 0. Ev suite + `test_common.py` run now: 1290 passed. Run: every name in `ev.__all__` importable, `ev.ConfigError is common.ConfigError`, `ev.PriceSeries is common.PriceSeries`; no `interruptible` module imports `ev` and `ev` does not mention `interruptible` |
| A11 | Minimum saving per off period, same with or without power | yes | `test_t11_*`; arc filter `scheduler.py:207`; scaling by power only applied to figures |
| A12 | Last max(N,1) quarters on except run continuing to M | yes | `test_t12_*`; `end + tail > horizon` break `scheduler.py:202`; clipped run arc only for the shortest allowed |
| A13 | 192 quarters, Z = 96 within 10 s | yes, thin margin | T13 five seeds pass; slowest 7.51 s (seed 3), others 0.45 to 1.4 s, measured this step |

Drift found, and what was done about it:

- **Out of scope:** nothing built from the exclusion list (no codec, no multi-load, no thermal model, no window reservation past the horizon, no price-age check).
- **Connections:** `ev` imports from `common`; `interruptible` imports from `common` only; neither imports the other. Showcases run (`python -m energy_optimization.interruptible.scheduler` reads as a fridge across an evening peak, with the override as the second case).
- **Step 5 flag (a), `NO_SAVING` for an off run that the plan ends now with no later off quarter:** judged acceptable, no drift. Section 1's reason list says "no saving worth taking" without defining it further, and a run in progress is judged by cost alone, so ending it now is the cost-optimal outcome with nothing later worth taking. No criterion decides the code (A4/A5 name only the binding-limit and override cases). The wording "no off period saves enough" is slightly loose for that case; cosmetic, left as is. Not a concept change.
- **Step 5 flag (b), T13 margin:** A13 is met (max 7.51 s of 10 s on this machine; 0.45 to 1.4 s on the other four seeds). Thin on one seed, not a criterion failure. Worth a `DEVELOPMENT.md` note at step 8 (level 2 as one 5.5 s LP).
- **Structure:** structure-auditor report (in sync) taken as given.
- No unmet criterion; marker advanced to step 7.

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
| Commits | `bf48d99` Concept; `b8c6cc9` Plan (draft); `96f5a8d` Plan; `95c5cd1` Plan accepted; `7c16b8a` WIP step 3 (common, ev repointed); `d990cdc` Add interruptible-load scheduler core; `114d7a4` Verify; `11ad479` WIP step 5; `a7e3d10` Test; `f9f659f` Concept check; plus this commit, `Ship: Interruptible load scheduler` |
| Pushed to | `origin/feat/interruptible-load-scheduler` |

Gates on the whole tree: `ruff check .` clean, `ruff format --check .` 67 files formatted, `mypy` no issues in 35 source files, `pytest` 1843 passed in 135.58s. `git diff --stat origin/main -- 'tests/test_ev_*.py'` is empty. Steps 1 to 6 are done, section 6 has no unmet rows, section 5 has a test against every intent. No stray files in `git diff main...HEAD --stat`; `.venv` is ignored and untracked. Every step from 1 to 6 left a commit.

---

## 8. Recommendations

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
