"""The interruptible-load scheduler: a shortest path with window side constraints.

Nodes are the quarter boundaries 0..T of the priced horizon. A plan is a path from node
0 to node T made of two kinds of arc. An *on arc* is one quarter on. An *off arc*
`a -> b + K` (K = max(N, 1)) is off in `[a, b)` followed by the K on-quarters that carry
its rebound. The maximum off run, the minimum off run, the minimum on time after an off
period and the end-of-horizon rule are built into which arcs exist; the minimum saving
filters arcs; the sliding windows are the only rows that couple arcs, so they are linear
rows over the off quarters. The program is solved by HiGHS through
`scipy.optimize.milp` in two levels: the cost, then the number of off quarters (earlier
quarters first) with the cost held.

Costs are worked out in units of one quarter at steady-state power, so the power cancels
out of every decision and only scales the money figures. All time arithmetic is in UTC.
The solver is deterministic: one thread, a fixed node budget per solve (`NODE_LIMIT`)
and never a wall-clock limit. A solve HiGHS reports infeasible, or that stops without a
point, is retried once with presolve off; only a second failure raises `SolverError`.
"""

import math
import warnings
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import numpy as np
from numpy.typing import NDArray
from scipy.optimize import Bounds, LinearConstraint, OptimizeResult, milp
from scipy.sparse import csr_array

from energy_optimization.common import (
    PriceInterval,
    PriceSeries,
    SolverError,
    floor_to_quarter,
    quarter_prices,
)
from energy_optimization.interruptible.model import (
    Limits,
    Load,
    LoadSnapshot,
    LoadState,
)
from energy_optimization.interruptible.result import (
    LoadSchedule,
    Reason,
    ReasonCode,
    ScheduledQuarter,
)

NODE_LIMIT = 8000
"""Branch-and-bound nodes allowed per level solve."""

_ON = "on"
_OFF = "off"
_RUN = "run"


@dataclass(frozen=True)
class _Arc:
    """One arc of the path model."""

    kind: str
    src: int
    dst: int
    cost: float
    off_lo: int = 0
    off_hi: int = 0
    extra: int = 0  # for a run arc: the quarters it adds to the run in progress


@dataclass(frozen=True)
class _Facts:
    """What the history says, derived from the look-back only."""

    run: int  # off quarters ending at the last history quarter
    on_run: int  # on quarters since the last off quarter (0 while off)
    after_off: bool  # an off quarter precedes the trailing on run
    previous_off: int  # the off run before the trailing on run
    off_prefix: list[int]  # off_prefix[i] = off quarters in the first i history ones
    short: bool


def _facts(history: tuple[LoadState, ...], limits: Limits) -> _Facts:
    """Derive the history facts from its last `history_needed` quarters."""
    needed = limits.history_needed
    seen = [state is LoadState.OFF for state in history[-needed:]]
    prefix = [0]
    for off in seen:
        prefix.append(prefix[-1] + off)
    count = len(seen)
    run = 0
    while run < count and seen[count - 1 - run]:
        run += 1
    on_run = 0
    if run == 0:
        while on_run < count and not seen[count - 1 - on_run]:
            on_run += 1
    before = count - run - on_run
    previous = 0
    if run == 0 and before > 0:
        while previous < before and seen[before - 1 - previous]:
            previous += 1
    return _Facts(
        run=run,
        on_run=on_run,
        after_off=run == 0 and before > 0,
        previous_off=previous,
        off_prefix=prefix,
        short=len(history) < needed,
    )


def _windows(facts: _Facts, limits: Limits, horizon: int) -> list[tuple[int, int, int]]:
    """Return the window rows `(lo, hi, bound)` that can bind, sorted.

    A window starting at `s` covers history quarters `s..-1` and horizon quarters
    `max(s, 0)..min(s + Z, T) - 1`. Windows are grouped by their horizon range and keep
    the largest history count. Windows starting before the visible history are implied
    by the one that starts at its first quarter, so the work is O(T + history).
    """
    length = limits.window_length
    visible = len(facts.off_prefix) - 1
    best: dict[tuple[int, int], int] = {}
    for start in range(max(-visible, -(length - 1)), horizon):
        behind = max(-start, 0)
        count = facts.off_prefix[visible] - facts.off_prefix[visible - behind]
        key = (max(start, 0), min(start + length, horizon))
        if count > best.get(key, -1):
            best[key] = count
    rows: list[tuple[int, int, int]] = []
    for (low, high), count in sorted(best.items()):
        bound = max(0, limits.window_off_budget - count)
        if bound < high - low:
            rows.append((low, high, bound))
    return rows


def _guard(
    snapshot: LoadSnapshot,
    facts: _Facts,
    horizon: int,
    rows: list[tuple[int, int, int]],
) -> ReasonCode | None:
    """Return the reason that decides the current quarter before any solve."""
    limits = snapshot.load.limits
    if snapshot.force_on:
        return ReasonCode.FORCED_ON
    if facts.run >= limits.max_off_run:
        return ReasonCode.MAX_OFF_RUN
    if facts.run > 0:
        forced = max(1, limits.min_off_run - facts.run)
    else:
        forced = limits.min_off_run
    for low, high, bound in rows:
        if low < forced and min(high, forced) - low > bound:
            return ReasonCode.WINDOW_BUDGET
    if facts.after_off and facts.on_run < limits.min_on_after_off:
        return ReasonCode.MIN_ON_AFTER_OFF
    tail = max(limits.min_on_after_off, 1)
    if facts.run == 0 and limits.min_off_run + tail > horizon:
        return ReasonCode.END_OF_HORIZON
    if facts.run >= limits.min_off_run and 1 + tail > horizon:
        return ReasonCode.END_OF_HORIZON
    return None


def _arcs(
    snapshot: LoadSnapshot,
    facts: _Facts,
    prices: list[float],
    guard: ReasonCode | None,
    tol: float,
) -> list[_Arc]:
    """Build every arc the model may use."""
    load = snapshot.load
    limits = load.limits
    horizon = len(prices)
    tail = max(limits.min_on_after_off, 1)
    prefix = [0.0]
    for price in prices:
        prefix.append(prefix[-1] + price)
    arcs: list[_Arc] = []
    run = facts.run
    if run == 0:
        arcs.extend(_Arc(_ON, t, t + 1, prices[t]) for t in range(horizon))
    else:
        arcs.extend(_Arc(_ON, t, t + 1, prices[t]) for t in range(1, horizon))
    first = 0
    if run > 0 or guard in (
        ReasonCode.FORCED_ON,
        ReasonCode.WINDOW_BUDGET,
        ReasonCode.MIN_ON_AFTER_OFF,
    ):
        first = 1
    if facts.after_off:
        first = max(first, limits.min_on_after_off - facts.on_run)
    factor_unit = load.rebound / tail
    threshold = load.min_saving_percent / 100.0
    for start in range(first, horizon):
        for length in range(limits.min_off_run, limits.max_off_run + 1):
            end = start + length
            if end + tail > horizon:
                break
            span = prefix[end + tail] - prefix[start]
            cost = (prefix[end + tail] - prefix[end]) * (1 + factor_unit * length)
            saving = span - cost
            if saving > tol and saving >= threshold * abs(span):
                arcs.append(_Arc(_OFF, start, end + tail, cost, start, end))
    if run > 0:
        arcs.extend(_run_arcs(snapshot, facts, prefix, guard, horizon))
    return arcs


def _run_arc(
    load: Load, run: int, extra: int, prefix: list[float], horizon: int
) -> _Arc:
    """Build the arc of an off run of `run` quarters that lasts `extra` more."""
    tail = max(load.limits.min_on_after_off, 1)
    low = min(extra, horizon)
    high = min(extra + tail, horizon)
    cost = (prefix[high] - prefix[low]) * (1 + load.rebound * (run + extra) / tail)
    return _Arc(_RUN, 0, high, cost, 0, low, extra)


def _run_arcs(
    snapshot: LoadSnapshot,
    facts: _Facts,
    prefix: list[float],
    guard: ReasonCode | None,
    horizon: int,
) -> list[_Arc]:
    """Build the arcs that continue an off run in progress."""
    load = snapshot.load
    limits = load.limits
    tail = max(limits.min_on_after_off, 1)
    shortest = max(0, limits.min_off_run - facts.run)
    longest = limits.max_off_run - facts.run
    if guard in (
        ReasonCode.FORCED_ON,
        ReasonCode.MAX_OFF_RUN,
        ReasonCode.WINDOW_BUDGET,
    ) or (longest < shortest):
        extras = [0]
    else:
        extras = [shortest] + [
            extra
            for extra in range(shortest + 1, longest + 1)
            if extra + tail <= horizon
        ]
    return [_run_arc(load, facts.run, extra, prefix, horizon) for extra in extras]


def _past_horizon(
    load: Load, run: int, extra: int, last_price: float, horizon: int
) -> float:
    """Charge the rebound of a clipped run arc that falls past the horizon."""
    tail = max(load.limits.min_on_after_off, 1)
    inside = min(extra + tail, horizon) - min(extra, horizon)
    return load.rebound * (run + extra) / tail * (tail - inside) * last_price


def _failed(result: OptimizeResult) -> bool:
    """Whether a solve is worth one retry: infeasible, or without a point."""
    return int(result.status) == 2 or result.x is None


class _Program:
    """The mixed-integer program: columns are the arcs, then one off column per quarter."""

    def __init__(
        self, arcs: list[_Arc], horizon: int, window_rows: list[tuple[int, int, int]]
    ) -> None:
        """Build the columns, bounds and rows.

        Args:
            arcs: Every arc of the model.
            horizon: The number of priced quarters.
            window_rows: The `(lo, hi, bound)` window rows.
        """
        count = len(arcs)
        self.arcs = arcs
        self.horizon = horizon
        self.columns = count + horizon
        self.integer = np.array(
            [arc.kind != _ON for arc in arcs] + [False] * horizon, dtype=np.int64
        )
        self.proven = True
        rows: list[int] = []
        cols: list[int] = []
        vals: list[float] = []
        low: list[float] = []
        high: list[float] = []
        # Flow conservation: one unit leaves node 0 and arrives at node T.
        for node in range(horizon + 1):
            low.append(1.0 if node == 0 else -1.0 if node == horizon else 0.0)
            high.append(low[-1])
        for col, arc in enumerate(arcs):
            rows.extend((arc.src, arc.dst))
            cols.extend((col, col))
            vals.extend((1.0, -1.0))
        # off_t equals the sum of the arcs that are off in quarter t.
        base = horizon + 1
        for t in range(horizon):
            rows.append(base + t)
            cols.append(count + t)
            vals.append(1.0)
            low.append(0.0)
            high.append(0.0)
        for col, arc in enumerate(arcs):
            for t in range(arc.off_lo, arc.off_hi):
                rows.append(base + t)
                cols.append(col)
                vals.append(-1.0)
        row = base + horizon
        for start, end, bound in window_rows:
            for t in range(start, end):
                rows.append(row)
                cols.append(count + t)
                vals.append(1.0)
            low.append(-math.inf)
            high.append(float(bound))
            row += 1
        self.rows = rows
        self.cols = cols
        self.vals = vals
        self.low = low
        self.high = high
        self.costs = np.array([arc.cost for arc in arcs] + [0.0] * horizon)

    def solve(
        self, objective: NDArray[np.float64], cost_limit: float | None = None
    ) -> NDArray[np.float64]:
        """Solve, retry once with presolve off if the attempt failed.

        Args:
            objective: The objective vector to minimise.
            cost_limit: When given, a row holding the cost at or below it.

        Returns:
            The solution.

        Raises:
            SolverError: If the retry fails too, or a status other than 0, 1 or a
                node-budget stop comes back with a point.
        """
        result = self._milp(objective, cost_limit, presolve=True)
        if _failed(result):
            # HiGHS sometimes reports a feasible model infeasible, or stops without a
            # point, under one presolve setting and solves it under the other.
            result = self._milp(objective, cost_limit, presolve=False)
        status = int(result.status)
        message = str(result.message)
        solution = result.x
        nodes = getattr(result, "mip_node_count", 0) or 0
        at_budget = status == 1 or (status == 4 and nodes >= NODE_LIMIT)
        if solution is None:
            raise SolverError(status, message)
        if status != 0:
            if not at_budget:
                raise SolverError(status, message)
            self.proven = False
        return np.asarray(solution, dtype=float)

    def _milp(
        self,
        objective: NDArray[np.float64],
        cost_limit: float | None,
        presolve: bool,
    ) -> OptimizeResult:
        """Call HiGHS once on the model as it stands."""
        rows, cols, vals = list(self.rows), list(self.cols), list(self.vals)
        low, high = list(self.low), list(self.high)
        if cost_limit is not None:
            row = len(low)
            for col, arc in enumerate(self.arcs):
                if arc.cost != 0.0:
                    rows.append(row)
                    cols.append(col)
                    vals.append(arc.cost)
            low.append(-math.inf)
            high.append(cost_limit)
        matrix = csr_array((vals, (rows, cols)), shape=(len(low), self.columns))
        options = {
            "node_limit": NODE_LIMIT,
            "mip_rel_gap": 0.0,
            "presolve": presolve,
            "threads": 1,
        }
        with warnings.catch_warnings():
            # `threads` is not a documented milp option, but HiGHS honours it.
            warnings.filterwarnings(
                "ignore",
                message="Unrecognized options detected",
                category=RuntimeWarning,
            )
            return milp(
                c=objective,
                constraints=LinearConstraint(matrix, low, high),
                integrality=self.integer,
                bounds=Bounds(np.zeros(self.columns), np.ones(self.columns)),
                options=options,
            )


def _reason(
    code: ReasonCode,
    limits: Limits,
    facts: _Facts,
    horizon: int,
    off_quarters: int,
) -> Reason:
    """Write the sentence for a reason code, naming the numbers."""
    tail = max(limits.min_on_after_off, 1)
    texts = {
        ReasonCode.NO_PRICES: "no price for the current quarter, so the load stays on",
        ReasonCode.FORCED_ON: "the caller's override keeps the load on",
        ReasonCode.MAX_OFF_RUN: (
            f"already off for {facts.run} quarters, the maximum is {limits.max_off_run}"
        ),
        ReasonCode.WINDOW_BUDGET: (
            f"another off period would exceed {limits.window_off_budget} off "
            f"quarters in a window of {limits.window_length}"
        ),
        ReasonCode.MIN_ON_AFTER_OFF: (
            f"on for {facts.on_run} of the {limits.min_on_after_off} quarters "
            "required after an off period"
        ),
        ReasonCode.END_OF_HORIZON: (
            f"only {horizon} priced quarters ahead, too few for an off period of "
            f"{limits.min_off_run} quarters followed by {tail} on"
        ),
        ReasonCode.MIN_OFF_RUN: (
            f"off for {facts.run} quarters, continuing to the minimum of "
            f"{limits.min_off_run}"
        ),
        ReasonCode.PLANNED_OFF: (
            f"planned off; the plan has {off_quarters} off quarters in all"
        ),
        ReasonCode.PLANNED_ON: (
            f"planned on; the plan has {off_quarters} off quarters later"
        ),
        ReasonCode.NO_SAVING: "no off period saves enough, so the load stays on",
    }
    return Reason(code=code, text=texts[code])


def schedule(snapshot: LoadSnapshot) -> LoadSchedule:
    """Plan an interruptible load.

    Args:
        snapshot: The time, the load, the prices, its recent history and the
            override.

    Returns:
        The decision for the current quarter, the plan for the whole horizon, its
        saving against always-on and why the current quarter is what it is.

    Raises:
        SolverError: If a solve has no point (or status 2) after its one retry with
            presolve off, or at once on a status other than 0, 1 or a node-budget 4
            when it has a point.
    """
    load = snapshot.load
    limits = load.limits
    facts = _facts(snapshot.history, limits)
    start = floor_to_quarter(snapshot.now)
    quarters = quarter_prices(snapshot.prices)
    starts: list[datetime] = []
    while start + timedelta(minutes=15 * len(starts)) in quarters:
        starts.append(start + timedelta(minutes=15 * len(starts)))
    prices = [quarters[moment] for moment in starts]
    horizon = len(prices)
    if horizon == 0:
        return LoadSchedule(
            state=LoadState.ON,
            reason=_reason(ReasonCode.NO_PRICES, limits, facts, 0, 0),
            plan=(),
            saving_percent=None,
            history_short=facts.short,
        )

    tol = 1e-9 * max(1.0, sum(abs(price) for price in prices))
    rows = _windows(facts, limits, horizon)
    guard = _guard(snapshot, facts, horizon, rows)
    arcs = _arcs(snapshot, facts, prices, guard, tol)
    program = _Program(arcs, horizon, rows)

    first = program.solve(program.costs)
    best = float(program.costs @ first)
    weights = np.zeros(program.columns)
    weights[len(arcs) :] = [
        1 + (t + 1) / (horizon * (horizon + 1)) for t in range(horizon)
    ]
    second = program.solve(weights, cost_limit=best + tol)
    chosen = [arc for arc, value in zip(arcs, second, strict=False) if value > 0.5]

    off = [False] * horizon
    for arc in chosen:
        for t in range(arc.off_lo, arc.off_hi):
            off[t] = True
    plan = tuple(
        ScheduledQuarter(
            start=starts[t],
            price=prices[t],
            state=LoadState.OFF if off[t] else LoadState.ON,
        )
        for t in range(horizon)
    )

    if guard is not None:
        code = guard
    elif off[0]:
        code = (
            ReasonCode.MIN_OFF_RUN
            if 0 < facts.run < limits.min_off_run
            else ReasonCode.PLANNED_OFF
        )
    else:
        code = ReasonCode.PLANNED_ON if any(off) else ReasonCode.NO_SAVING
    reason = _reason(code, limits, facts, horizon, sum(off))

    planned, always = _figures(load, facts, prices, chosen)
    difference = always - planned
    if abs(difference) <= tol:
        difference = 0.0
    percent = None if abs(always) <= tol else 100.0 * difference / abs(always)
    always_cost = planned_cost = saving = None
    if load.power_kw is not None:
        scale = load.power_kw / 4.0
        always_cost = always * scale
        planned_cost = planned * scale
        saving = always_cost - planned_cost
        if difference == 0.0:
            saving = 0.0
    return LoadSchedule(
        state=plan[0].state,
        reason=reason,
        plan=plan,
        saving_percent=percent,
        always_on_cost=always_cost,
        planned_cost=planned_cost,
        saving=saving,
        history_short=facts.short,
        proven=program.proven,
    )


def _figures(
    load: Load, facts: _Facts, prices: list[float], chosen: list[_Arc]
) -> tuple[float, float]:
    """Return the plan's cost and the always-on cost, in quarter units."""
    limits = load.limits
    horizon = len(prices)
    tail = max(limits.min_on_after_off, 1)
    last = prices[-1]
    prefix = [0.0]
    for price in prices:
        prefix.append(prefix[-1] + price)
    constant = 0.0
    if facts.after_off and facts.on_run < limits.min_on_after_off:
        remaining = min(tail - facts.on_run, horizon)
        constant = load.rebound * facts.previous_off / tail * prefix[remaining]
    planned = constant + sum(arc.cost for arc in chosen)
    for arc in chosen:
        if arc.kind == _RUN:
            planned += _past_horizon(load, facts.run, arc.extra, last, horizon)
    if facts.run == 0:
        always = constant + prefix[horizon]
    else:
        arc = _run_arc(load, facts.run, 0, prefix, horizon)
        always = (
            arc.cost
            + _past_horizon(load, facts.run, 0, last, horizon)
            + prefix[horizon]
            - prefix[arc.dst]
        )
    return planned, always


def main() -> None:
    """Showcase this module's functionality."""
    now = datetime(2026, 10, 21, 14, 0, tzinfo=UTC)
    quarters_ahead = 32
    cheap = 1.0
    dear = 3.0
    dear_from = 12  # the dear quarters start 3 hours ahead
    dear_to = 20
    max_off_run = 4
    window_off_budget = 8
    window_length = 24
    min_on_after_off = 2
    min_off_run = 2
    rebound = 0.9
    power_kw = 0.15
    prices = PriceSeries(
        intervals=tuple(
            PriceInterval(
                start=now + timedelta(minutes=15 * t),
                end=now + timedelta(minutes=15 * (t + 1)),
                price=dear if dear_from <= t < dear_to else cheap,
            )
            for t in range(quarters_ahead)
        ),
        updated_at=now,
    )
    limits = Limits(
        max_off_run=max_off_run,
        window_off_budget=window_off_budget,
        window_length=window_length,
        min_on_after_off=min_on_after_off,
        min_off_run=min_off_run,
    )
    load = Load(limits=limits, rebound=rebound, power_kw=power_kw)
    snapshot = LoadSnapshot(now=now, load=load, prices=prices)

    result = schedule(snapshot)

    plan = "".join("#" if q.state is LoadState.ON else "." for q in result.plan)
    print(f"now: {result.state} ({result.reason.text})")
    print(f"plan (# on, . off): {plan}")
    print(f"saving: {result.saving_percent:.1f} % ({result.saving:.4f})")

    # The override keeps the load on now; the rest of the plan still keeps the limits.
    force_on = True

    snapshot = LoadSnapshot(now=now, load=load, prices=prices, force_on=force_on)
    result = schedule(snapshot)

    print(f"forced: {result.state} ({result.reason.code})")


if __name__ == "__main__":
    main()
