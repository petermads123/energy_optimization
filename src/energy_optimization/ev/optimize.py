"""The charging optimiser: one mixed-integer program, solved level by level.

`optimize` decides, for every quarter from now to each car's ready time, which charger
charges and at how many whole amps, with HiGHS through `scipy.optimize.milp`. The
preferences are strictly ordered, so the program is solved lexicographically: each
level is minimised with every earlier level held at its optimum. Chargers that are only
decided for the current quarter (smart charging off, charge now, spare capacity) are
solved on small quarter-0 models before and after the planning model, which is exact
because their variables meet the later quarters only through capacity.

All time arithmetic is in UTC. The solver is deterministic: one thread, a fixed node
budget per solve (`NODE_LIMIT`) and never a wall-clock limit.
"""

import math
import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import IntEnum
from zoneinfo import ZoneInfo

import numpy as np
from numpy.typing import NDArray
from scipy.optimize import Bounds, LinearConstraint, OptimizeResult, milp
from scipy.sparse import csr_array

from .model import Phase
from .planning import SessionPlan, energy_per_quarter_kwh
from .quarters import floor_to_quarter, quarter_starts
from .result import PlannedQuarter

NODE_LIMIT = 8000
"""Branch-and-bound nodes allowed per level solve."""

_TOLERANCE = 1e-6
_EPS = 1e-9
_MAX_TICKS = 10_000_000


class SolverError(RuntimeError):
    """The solver failed.

    Attributes:
        status: The status `scipy.optimize.milp` reported.
        message: The message it reported.
    """

    def __init__(self, status: int, message: str) -> None:
        """Build the error.

        Args:
            status: The `milp` status code.
            message: The `milp` message.
        """
        super().__init__(f"HiGHS status {status}: {message}")
        self.status = status
        self.message = message


class Tier(IntEnum):
    """Which precedence stage a demand belongs to."""

    SMART_OFF = 0
    CHARGE_NOW = 1
    PLANNED = 2
    SPARE = 3


def _bad(field: str, message: str) -> ValueError:
    """Build the error for an invalid `Demand` field."""
    return ValueError(f"{field}: {message}")


def _check_current(field: str, value: float) -> None:
    """Require a finite current of at least 0."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _bad(field, f"must be a number, got {value!r}")
    if not math.isfinite(value) or value < 0:
        raise _bad(field, f"must be finite and >= 0, got {value}")


@dataclass(frozen=True)
class Demand:
    """One charger asking for current, given in ranking order.

    Attributes:
        charger_id: The charger.
        phases: The phases it is counted on.
        tier: Which precedence stage it belongs to.
        min_current: Lowest non-zero current in A; equal to `max_current` means
            all-or-nothing (on/off) at exactly this current, in every tier, even when
            `now_max_current` is higher. Otherwise the charger is adjustable in whole
            amps, so a minimum it can actually run at must be a whole number (a
            fractional minimum is allowed when both maxima are below it, which means
            off in every quarter).
        max_current: Highest current in A in quarters after the first. Below
            `min_current` means off in those quarters.
        now_max_current: Highest current in A in the first quarter (user limit,
            fallback). Below `min_current` means off now.
        need_kwh: Energy to deliver, `PLANNED` only.
        deadline: The moment the energy is needed by, aware, `PLANNED` only.

    Raises:
        ValueError: If a field is invalid (including a `tier` that is not a `Tier`,
            and a fractional `min_current` on a charger that differs from on/off and can run
            at it); the message starts with the field name.
    """

    charger_id: str
    phases: frozenset[Phase]
    tier: Tier
    min_current: float
    max_current: float
    now_max_current: float
    need_kwh: float = 0.0
    deadline: datetime | None = None

    def __post_init__(self) -> None:
        """Validate the fields."""
        if not isinstance(self.tier, Tier):
            raise _bad("tier", f"must be a Tier, got {self.tier!r}")
        if not self.phases:
            raise _bad("phases", "must not be empty")
        for name in ("min_current", "max_current", "now_max_current", "need_kwh"):
            _check_current(name, getattr(self, name))
        runs_adjustably = self.min_current != self.max_current and (
            max(self.max_current, self.now_max_current) >= self.min_current
        )
        if runs_adjustably and not float(self.min_current).is_integer():
            raise _bad(
                "min_current",
                "must be a whole number of amps when the charger can run below its "
                f"limits in whole amps (not on/off, not always off), got {self.min_current}",
            )
        if self.deadline is not None and self.deadline.utcoffset() is None:
            raise _bad("deadline", f"must be timezone-aware, got {self.deadline}")
        if self.tier is Tier.PLANNED and self.deadline is None:
            raise _bad("deadline", "required for a PLANNED demand")


@dataclass(frozen=True)
class Outcome:
    """What `optimize` decided.

    Attributes:
        allocations: The quarter-0 current of every demand, by charger id.
        plans: A plan for every `PLANNED` demand, by charger id.
        proven: False when any solve (a level, or a probe that decides a level) stopped
            at the node budget.
    """

    allocations: dict[str, float]
    plans: dict[str, SessionPlan]
    proven: bool


@dataclass
class _Run:
    """What the solves of one call have found out together."""

    proven: bool = True
    status: int = 0
    message: str = ""


class _Problem:
    """A growing mixed-integer program that is minimised level by level."""

    def __init__(self, run: _Run) -> None:
        self._run = run
        self._lb: list[float] = []
        self._ub: list[float] = []
        self._integer: list[int] = []
        self._ri: list[int] = []
        self._ci: list[int] = []
        self._vals: list[float] = []
        self._lo: list[float] = []
        self._hi: list[float] = []
        self.x: NDArray[np.float64] | None = None

    def var(self, lb: float, ub: float, integer: bool) -> int:
        """Add a variable and return its column."""
        self._lb.append(float(lb))
        self._ub.append(float(ub))
        self._integer.append(1 if integer else 0)
        return len(self._lb) - 1

    def row(self, terms: Sequence[tuple[int, float]], lo: float, hi: float) -> None:
        """Add the constraint `lo <= sum(coefficient * variable) <= hi`."""
        index = len(self._lo)
        for col, value in terms:
            self._ri.append(index)
            self._ci.append(col)
            self._vals.append(float(value))
        self._lo.append(float(lo))
        self._hi.append(float(hi))

    def fix(self, col: int, value: float) -> None:
        """Pin a variable to a value."""
        self._lb[col] = self._ub[col] = float(value)

    def _solve(
        self, objective: NDArray[np.float64], keep: bool = True
    ) -> NDArray[np.float64]:
        """Solve once, update the run's proof status and return the solution.

        Args:
            objective: The objective vector to minimise.
            keep: Whether the solution becomes the problem's last solution, the
                witness that lets a later level be held without a solve. A probe that
                is not followed by a hold must not keep it.
        """
        result = self._milp(objective, presolve=True)
        if _failed(result):
            # HiGHS sometimes reports a feasible model infeasible, or stops without a
            # point, under one presolve setting and solves it under the other. The retry
            # is deterministic, and only a second failure raises.
            result = self._milp(objective, presolve=False)
        status = int(result.status)
        message = str(result.message)
        solution = result.x
        nodes = getattr(result, "mip_node_count", 0) or 0
        # HiGHS reports a node-budget stop as status 4 with the node count at the limit
        # (checked against the real solver for limits 1 to 50); any other status 4 is
        # a failure.
        stopped = status == 4 and nodes >= NODE_LIMIT
        at_budget = status == 1 or stopped
        if solution is None:
            raise SolverError(status, message)
        if status != 0:
            if not at_budget:
                raise SolverError(status, message)
            self._run.proven = False
        self._run.status = status
        self._run.message = message
        found: NDArray[np.float64] = np.asarray(solution, dtype=float)
        if keep:
            self.x = found
        return found

    def _milp(self, objective: NDArray[np.float64], presolve: bool) -> OptimizeResult:
        """Call HiGHS once on the problem as it stands."""
        count = len(self._lb)
        constraints = None
        if self._lo:
            matrix = csr_array(
                (self._vals, (self._ri, self._ci)), shape=(len(self._lo), count)
            )
            constraints = LinearConstraint(matrix, self._lo, self._hi)
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
                constraints=constraints,
                integrality=np.array(self._integer),
                bounds=Bounds(self._lb, self._ub),
                options=options,
            )

    def probe(self, objective: Mapping[int, float]) -> float:
        """Minimise a level without holding it and without keeping the solution.

        Args:
            objective: Coefficient per column.

        Returns:
            The optimum with every row added so far.
        """
        vector = np.zeros(len(self._lb))
        for col, value in objective.items():
            vector[col] = value
        return float(vector @ self._solve(vector, keep=False))

    def raise_to(self, col: int, level: float) -> bool:
        """Hold a column at `level` or more if it can reach that with every row so far.

        The solution found stays the problem's last solution: it satisfies the new row
        when the column can be raised, and the old rows when it cannot.

        Args:
            col: The column.
            level: The value to hold it at or above.

        Returns:
            Whether it can reach `level`; the row is added only then.
        """
        vector = np.zeros(len(self._lb))
        vector[col] = -1.0
        if round(-float(vector @ self._solve(vector))) < level:
            return False
        self.row([(col, 1.0)], level, math.inf)
        return True

    def set_known(self, col: int, value: float) -> None:
        """Record the value of a column the last solution did not have yet.

        Used for a variable that was just fixed by reasoning rather than by a solve, so
        the last solution stays a valid point of the grown problem.
        """
        assert self.x is not None
        grown = np.zeros(len(self._lb))
        grown[: len(self.x)] = self.x
        grown[col] = value
        self.x = grown

    def hold(
        self, objective: Mapping[int, float], value: float, integral: bool
    ) -> None:
        """Hold a level at its optimum for every level after it.

        Args:
            objective: Coefficient per column.
            value: The optimum.
            integral: Whether the objective can only take whole values, which allows
                a half-unit tolerance instead of a numerical one.
        """
        terms = [(col, v) for col, v in sorted(objective.items()) if v]
        if integral:
            # The optimum is a whole number; the solver's value may be off by its
            # integrality tolerance times the coefficients.
            self.row(terms, -math.inf, round(value) + 0.5)
        else:
            self.row(terms, -math.inf, value + _TOLERANCE + 1e-9 * abs(value))

    def minimise(
        self,
        objective: Mapping[int, float],
        *,
        integral: bool = False,
        floor: float | None = None,
    ) -> bool:
        """Minimise one level, then hold it at its optimum.

        Args:
            objective: Coefficient per column; zero coefficients are ignored.
            integral: Whether the objective can only take whole values.
            floor: A lower bound on the objective. When the last solution already
                reaches it, the level is held without a solve.

        Returns:
            False when the level has no non-zero coefficient and was skipped.
        """
        terms = [(col, value) for col, value in sorted(objective.items()) if value]
        if not terms:
            return False
        vector = np.zeros(len(self._lb))
        for col, value in terms:
            vector[col] = value
        if floor is not None and self.x is not None:
            known = float(vector[: len(self.x)] @ self.x)
            if known <= floor + _EPS:
                self.hold(objective, known, integral)
                return True
        solution = self._solve(vector)
        self.hold(objective, float(vector @ solution), integral)
        return True

    def solution(self) -> NDArray[np.float64]:
        """Return the last solution, solving for any feasible point if there is none."""
        if self.x is None:
            self._solve(np.zeros(len(self._lb)))
        assert self.x is not None
        return self.x


def _failed(result: OptimizeResult) -> bool:
    """Whether a solve is worth one retry: infeasible, or stopped without a point."""
    return int(result.status) == 2 or result.x is None


def _hi(demand: Demand, first: bool) -> float:
    """Return the highest current of a demand in a quarter, 0 when it is off."""
    top = demand.now_max_current if first else demand.max_current
    if top < demand.min_current:
        return 0.0
    if demand.min_current == demand.max_current:
        return demand.min_current
    return top


def _clip(capacity: Mapping[Phase, float]) -> dict[Phase, float]:
    """Return the capacity per phase with absent and negative values as 0."""
    return {phase: max(0.0, capacity.get(phase, 0.0)) for phase in Phase}


def _take(room: dict[Phase, float], demand: Demand, amount: float) -> None:
    """Take a current off the room of every phase of a demand."""
    for phase in demand.phases:
        room[phase] = max(0.0, room[phase] - amount)


def _stage_now(
    demands: Sequence[Demand], room: Mapping[Phase, float], run: _Run
) -> dict[str, float]:
    """Decide the first quarter of demands that exist only in the first quarter.

    Inclusion at the minimum in ranking order, then max-min-fair 1 A steps, then the
    remainder in ranking order (levels S1 to S3).
    """
    if not demands:
        return {}
    # S1: each demand in ranking order is in if it fits after those ranked above.
    first = _Problem(run)
    chosen = {
        d.charger_id: first.var(0, 1 if _hi(d, True) > 0 else 0, True) for d in demands
    }
    for phase in Phase:
        terms = [
            (chosen[d.charger_id], d.min_current) for d in demands if phase in d.phases
        ]
        if terms:
            first.row(terms, -math.inf, room[phase])
    for demand in demands:
        first.minimise({chosen[demand.charger_id]: -1.0}, integral=True, floor=-1.0)
    solved = first.solution()
    included = [d for d in demands if solved[chosen[d.charger_id]] > 0.5]
    if not included:
        return {d.charger_id: 0.0 for d in demands}
    # S2: progressive filling, then the leftover one amp at a time (round 1's `share`).
    top = {d.charger_id: _hi(d, True) for d in included}
    value = {d.charger_id: d.min_current for d in included}
    frozen = {d.charger_id for d in included if top[d.charger_id] <= d.min_current}
    while len(frozen) < len(included):
        fill = _Problem(run)
        cols = {}
        for d in included:
            key = d.charger_id
            lower = value[key]
            upper = value[key] if key in frozen else top[key]
            cols[key] = fill.var(lower, upper, lower < upper)
        level = fill.var(0, max(top.values()), True)
        for phase in Phase:
            terms = [(cols[d.charger_id], 1.0) for d in included if phase in d.phases]
            if terms:
                fill.row(terms, -math.inf, room[phase])
        for d in included:
            if d.charger_id not in frozen:
                fill.row([(cols[d.charger_id], 1.0), (level, -1.0)], 0.0, math.inf)
        fill.minimise({level: -1.0}, integral=True)
        reached = round(float(fill.solution()[level]))
        for d in included:
            if d.charger_id not in frozen:
                value[d.charger_id] = max(float(reached), value[d.charger_id])
        left = dict(room)
        for d in included:
            for phase in d.phases:
                left[phase] -= value[d.charger_id]
        # The cars at the level cannot all rise together. As in `share`, each in ranking
        # order takes one more amp if it still fits, and one that does not never will
        # (the room only shrinks), so it is frozen; the rest go on from the next level.
        moved = False
        for d in included:
            key = d.charger_id
            if key in frozen or value[key] > reached:
                continue
            if value[key] + 1 > top[key] + _EPS or any(
                left[p] < 1 - _EPS for p in d.phases
            ):
                frozen.add(key)
                moved = True
                continue
            value[key] += 1.0
            moved = True
            for phase in d.phases:
                left[phase] -= 1.0
        if not moved:
            frozen |= {d.charger_id for d in included}
    return {d.charger_id: value.get(d.charger_id, 0.0) for d in demands}


@dataclass(frozen=True)
class _Vars:
    """The columns of one planned demand."""

    x: list[int]
    i: list[int]
    shortfall: int
    energy: float


def _short_runs(
    solved: NDArray[np.float64], columns: Sequence[_Vars], length: int
) -> int:
    """Count the runs shorter than the minimum block in a solution.

    A run counts when it starts and a quarter of the following `length - 1` quarters
    inside the window is off; a run the deadline cuts short does not.
    """
    count = 0
    for c in columns:
        on = [solved[x] > 0.5 for x in c.x]
        for t, is_on in enumerate(on):
            starts_run = is_on and (t == 0 or not on[t - 1])
            if starts_run and not all(on[t + 1 : t + length]):
                count += 1
    return count


def _add_blocks(problem: _Problem, columns: Sequence[_Vars], length: int) -> list[int]:
    """Add the minimum-block penalties and return their columns.

    For every quarter `t` with at least one quarter after it in the window, a binary
    `w` is forced to 1 when a run starts at `t` and one of the next `length - 1`
    quarters of the window is off.
    """
    waste: list[int] = []
    for c in columns:
        count = len(c.x)
        for t in range(count):
            m = min(length - 1, count - 1 - t)
            if m <= 0:
                continue
            w = problem.var(0, 1, True)
            waste.append(w)
            terms = [(c.x[t + k], 1.0) for k in range(1, m + 1)]
            terms.append((w, float(m)))
            terms.append((c.x[t], -float(m)))
            if t > 0:
                terms.append((c.x[t - 1], float(m)))
            problem.row(terms, 0.0, math.inf)
    return waste


def _price_ticks(values: Sequence[float]) -> list[int] | None:
    """Return the prices as whole multiples of their largest common tick, if coarse.

    Prices are quoted on a grid (a Nord Pool price in EUR per MWh to two decimals is a
    multiple of 1e-5 EUR per kWh). When every price is a whole multiple of a tick of at
    least 1e-7, the cost of a plan with whole-amp currents is a whole number of ticks
    (times the energy of one amp-quarter), so it can be held exactly rather than to a
    tolerance that a price step can slip under.

    Args:
        values: The prices.

    Returns:
        Each price in ticks, or None when the grid is finer than 1e-7, all prices are
        zero or a price is more than `_MAX_TICKS` ticks.
    """
    scaled = [round(v * 1e9) for v in values]
    step = math.gcd(*scaled) if scaled else 0
    if step < 100:
        return None
    ticks = [v // step for v in scaled]
    # Keep the objective's coefficients small enough for the solver's tolerances.
    return ticks if max(abs(t) for t in ticks) <= _MAX_TICKS else None


def _minimise_price(
    problem: _Problem,
    demands: Sequence[Demand],
    columns: Sequence[_Vars],
    horizon: Sequence[datetime],
    prices: Mapping[datetime, float],
    whole: bool,
) -> None:
    """Minimise and hold the cost of the plan (level P5, first part).

    The cost is minimised in whole ticks of the price grid when the prices have one and
    every current is whole, so the hold is exact: a plan that is dearer by one tick is
    never let through by the solver's tolerance. Otherwise it is held to a tolerance.
    """
    cells = [
        (n, t, i)
        for n, c in enumerate(columns)
        for t, i in enumerate(c.i)
        if horizon[t] in prices
    ]
    ticks = _price_ticks([prices[horizon[t]] for _, t, _ in cells]) if whole else None
    if ticks is None:
        problem.minimise(
            {i: prices[horizon[t]] * columns[n].energy for n, t, i in cells}
        )
        return
    problem.minimise(
        {
            i: float(tick * len(demands[n].phases))
            for (n, _, i), tick in zip(cells, ticks, strict=True)
        },
        integral=True,
    )


def _fit_now(demands: Sequence[Demand], room: Mapping[Phase, float], run: _Run) -> int:
    """Return the most demands that fit in the first quarter at their minimum.

    It looks at the first quarter's capacity alone, so it bounds what the full model
    can have on now: a solution that reaches it is optimal for that level.
    """
    small = _Problem(run)
    cols = [small.var(0, 1 if _hi(d, True) > 0 else 0, True) for d in demands]
    for phase in Phase:
        terms = [
            (c, d.min_current)
            for c, d in zip(cols, demands, strict=True)
            if phase in d.phases
        ]
        if terms:
            small.row(terms, -math.inf, room[phase])
    small.minimise({c: -1.0 for c in cols}, integral=True)
    return round(sum(small.solution()[c] for c in cols))


def _fill_now(
    problem: _Problem,
    demands: Sequence[Demand],
    columns: Sequence[_Vars],
    on_now: Sequence[int],
) -> None:
    """Share the first quarter evenly among the cars that are on now (level P6).

    Progressive filling as in the first-quarter tiers: repeatedly raise the lowest
    first-quarter current of the cars not yet saturated as far as it goes, then
    saturate every car that is at its maximum or cannot exceed that level, holding
    everything found. When no car is saturated by a level (they could each exceed it,
    just not all at once) the cars in ranking order take one more amp each while they
    can, as in the first-quarter tiers (round 1's `share`).
    """
    top = {n: _hi(demands[n], True) for n in on_now}
    value = {n: demands[n].min_current for n in on_now}
    live = [n for n in on_now if top[n] > demands[n].min_current + _EPS]
    while live:
        ceiling = min(math.floor(top[n] + _EPS) for n in live)
        level = problem.var(0, ceiling, True)
        for n in live:
            problem.row([(columns[n].i[0], 1.0), (level, -1.0)], 0.0, math.inf)
        solved = problem.solution()
        # Integer columns come back within the solver's integrality tolerance of a
        # whole number (6.9999995 for 7), so they are rounded before they are compared.
        known = min(round(solved[columns[n].i[0]]) for n in live)
        if known >= ceiling:
            problem.fix(level, float(ceiling))
            problem.set_known(level, float(ceiling))
            reached = ceiling
        else:
            problem.minimise({level: -1.0}, integral=True)
            reached = round(float(problem.solution()[level]))
            problem.fix(level, float(reached))
        for n in live:
            value[n] = max(value[n], float(reached))
        # A car is saturated at its maximum, or when it cannot be raised above the
        # level with every other live car held at it.
        held = problem.solution()
        stuck = []
        for n in live:
            col = columns[n].i[0]
            at_top = value[n] >= top[n] - _EPS
            free = round(held[col]) >= reached + 1
            if not (at_top or free):
                free = round(-problem.probe({col: -1.0})) >= reached + 1
            if at_top or not free:
                stuck.append(n)
        if not stuck:
            # Each car could rise alone but not all together. As in `share`, each in
            # ranking order takes one more amp if it still can with everything held, and
            # one that cannot never will (the room only shrinks), so it drops out.
            rising = []
            for n in live:
                if problem.raise_to(columns[n].i[0], reached + 1):
                    value[n] = float(reached + 1)
                    rising.append(n)
            live = rising
            continue
        live = [n for n in live if n not in stuck]


def _on_now_and_even(
    problem: _Problem,
    demands: Sequence[Demand],
    columns: Sequence[_Vars],
    now_room: Mapping[Phase, float],
    run: _Run,
    priced_now: bool,
    whole: bool,
) -> None:
    """Run the second part of P5 and P6: cars on now, then an even first quarter.

    With an unpriced first quarter its total current is minimised and held first, so
    only what the need forces is charged there. The cars on now are then as many as
    possible, the better-ranked ones among them, and they share the first quarter.
    """
    possible = [n for n, c in enumerate(columns) if c.x and _hi(demands[n], True) > 0]
    if not priced_now:
        # Energy, not amps: a three-phase car delivers three times the kWh per amp.
        problem.minimise(
            {columns[n].i[0]: float(len(demands[n].phases)) for n in possible},
            integral=whole,
            floor=0.0,
        )
    fits = _fit_now([demands[n] for n in possible], now_room, run) if possible else 0
    problem.minimise(
        {columns[n].x[0]: -1.0 for n in possible}, integral=True, floor=-float(fits)
    )
    # Among the same number of cars on now, the better-ranked ones are on: in ranking
    # order, each car is on if that is still possible after those ranked above it.
    for n in possible:
        problem.minimise({columns[n].x[0]: -1.0}, integral=True, floor=-1.0)
    solved = problem.solution()
    on_now = [n for n, c in enumerate(columns) if c.x and solved[c.x[0]] > 0.5]
    for n, c in enumerate(columns):
        if c.x:
            problem.fix(c.x[0], 1.0 if n in on_now else 0.0)
    if on_now:
        _fill_now(problem, demands, columns, on_now)


def _stage_plan(
    demands: Sequence[Demand],
    horizon: Sequence[datetime],
    prices: Mapping[datetime, float],
    now_room: Mapping[Phase, float],
    later_room: Mapping[Phase, float],
    voltage: float,
    min_block_quarters: int,
    price_cap: float | None,
    run: _Run,
) -> dict[str, list[float]]:
    """Plan the `PLANNED` demands over their windows (levels P1 to P7).

    Returns:
        The current of every demand in every quarter of its window.
    """
    problem = _Problem(run)
    total = max(1, len(horizon))
    columns: list[_Vars] = []
    for d in demands:
        count = len(quarter_starts(horizon[0], d.deadline)) if d.deadline else 0
        energy = energy_per_quarter_kwh(1.0, len(d.phases), voltage)
        xs: list[int] = []
        currents: list[int] = []
        for t in range(count):
            top = _hi(d, t == 0)
            x = problem.var(0, 1 if top > 0 else 0, True)
            # An on/off demand is fixed to `min_current * x`, which need not be whole.
            i = problem.var(0, top, d.min_current != d.max_current)
            problem.row([(i, 1.0), (x, -d.min_current)], 0.0, math.inf)
            problem.row([(i, 1.0), (x, -top)], -math.inf, 0.0)
            xs.append(x)
            currents.append(i)
        shortfall = problem.var(0, math.inf, False)
        if currents:
            terms = [(i, energy) for i in currents]
            problem.row([*terms, (shortfall, 1.0)], d.need_kwh, math.inf)
            allowed = d.need_kwh + energy * max(_hi(d, False), _hi(d, True))
            problem.row(terms, -math.inf, allowed)
        else:
            problem.row([(shortfall, 1.0)], d.need_kwh, math.inf)
        columns.append(_Vars(xs, currents, shortfall, energy))
    for t in range(total):
        for phase in Phase:
            terms = [
                (c.i[t], 1.0)
                for d, c in zip(demands, columns, strict=True)
                if phase in d.phases and t < len(c.i)
            ]
            if terms:
                room = now_room[phase] if t == 0 else later_room[phase]
                problem.row(terms, -math.inf, room)

    def price_at(t: int) -> float | None:
        return prices.get(horizon[t])

    # P1: meet each need.
    problem.minimise({c.shortfall: 1.0 for c in columns}, floor=0.0)
    # P2: avoid unpriced quarters.
    problem.minimise(
        {
            i: c.energy
            for c in columns
            for t, i in enumerate(c.i)
            if price_at(t) is None
        },
        floor=0.0,
    )
    # P3: use quarters above the price cap only to meet the need.
    if price_cap is not None:
        problem.minimise(
            {
                i: c.energy
                for c in columns
                for t, i in enumerate(c.i)
                if (p := price_at(t)) is not None and p > price_cap
            },
            floor=0.0,
        )
    # P4: avoid runs shorter than the minimum block. The blocks are added only now:
    # until here they cannot change the optimum.
    if min_block_quarters > 1:
        waste = _add_blocks(problem, columns, min_block_quarters)
        penalty = {w: 1.0 for w in waste}
        if _short_runs(problem.solution(), columns, min_block_quarters) == 0:
            problem.hold(penalty, 0.0, integral=True)
        else:
            problem.minimise(penalty, integral=True)
    # P5: lowest price; then as many cars as possible on now.
    whole = all(
        d.min_current != d.max_current or float(d.min_current).is_integer()
        for d in demands
    )
    _minimise_price(problem, demands, columns, horizon, prices, whole)
    # P5, second part, and P6. An unpriced first quarter keeps only what the need
    # forces (its total is minimised and held); the cars on now share it as a priced one.
    _on_now_and_even(
        problem, demands, columns, now_room, run, price_at(0) is not None, whole
    )
    # P7: earlier priced quarters first, unpriced quarters latest first, and the
    # better-ranked car in the earlier quarter of either kind. A product weight is
    # minimised by pairing its large factors with its small ones: for priced quarters
    # the better-ranked car carries the larger rank factor (count - n) and so gets the
    # smaller (t + 1); for unpriced ones the worse-ranked car carries the larger rank
    # factor (n + 1) and so gets the smaller (total - t), the later quarter.
    count = len(demands)
    weight: dict[int, float] = {}
    for n, c in enumerate(columns):
        for t, i in enumerate(c.i):
            if price_at(t) is None:
                weight[i] = float((total - t) * (n + 1))
            else:
                weight[i] = float((t + 1) * (count - n))
    problem.minimise(weight, integral=whole)
    solved = problem.solution()
    return {
        d.charger_id: [
            _read(d, t == 0, solved[x], solved[i])
            for t, (x, i) in enumerate(zip(c.x, c.i, strict=True))
        ]
        for d, c in zip(demands, columns, strict=True)
    }


def _read(demand: Demand, first: bool, on: float, current: float) -> float:
    """Read a solved current: whole amps, or exactly the on/off current when it is on."""
    if demand.min_current != demand.max_current:
        return float(round(current))
    return _hi(demand, first) if on > 0.5 else 0.0


def _current(demand: Demand, first: bool, value: float) -> float:
    """Return a rounded current, 0 when it is off, checked against the limits."""
    if value < 0.5:
        return 0.0
    top = _hi(demand, first)
    if value < demand.min_current - _TOLERANCE or value > top + _TOLERANCE:
        raise SolverError(
            0, f"{demand.charger_id} solved at {value} A, outside its limits"
        )
    return value


def optimize(
    demands: Sequence[Demand],
    now: datetime,
    prices: Mapping[datetime, float],
    capacity: Mapping[Phase, float],
    now_capacity: Mapping[Phase, float],
    voltage: float,
    min_block_quarters: int,
    price_cap: float | None,
    timezone: str,
) -> Outcome:
    """Solve the lexicographic model.

    The stages run in this order: smart charging off, charge now, the planned cars over
    all their quarters, then spare-capacity charging. Each takes what the ones before
    leave.

    Args:
        demands: The demands in ranking order; the order within a tier breaks ties.
        now: The time now, aware.
        prices: The price of each known quarter, keyed by UTC quarter start; a missing
            quarter is unpriced.
        capacity: What the chargers may use per phase in the quarters after the first,
            before the `SMART_OFF` demands, which keep their first-quarter current
            there (so they are held to the smaller of `capacity` and `now_capacity`).
            Negative values and absent phases count as 0.
        now_capacity: What they may use per phase in the first quarter; negative
            values and absent phases count as 0.
        voltage: Phase voltage in V.
        min_block_quarters: Minimum length of a run of quarters; shorter runs are
            avoided.
        price_cap: Price above which quarters are avoided, None for off.
        timezone: IANA name; only sets the zone of `PlannedQuarter.start`.

    Returns:
        The first-quarter current of every demand, a plan for every `PLANNED` demand
        and whether every solve (levels and probes) was proven optimal.

    With no demands it returns `Outcome({}, {}, True)` at once, before any of the
    checks below.

    Raises:
        SolverError: If HiGHS reports an error or finds no solution in any level solve
            (carrying its status and message), or if rounding leaves a current outside
            its limits or a phase over its capacity (carrying status 0, the solver's
            "optimal", with a message naming the charger or phase).
        ValueError: If `now` is naive or a charger id is repeated.
        ZoneInfoNotFoundError: If `timezone` is not a known zone.
    """
    if not demands:
        return Outcome({}, {}, True)
    ids = [d.charger_id for d in demands]
    if len(set(ids)) != len(ids):
        raise ValueError(f"charger ids must be unique, got {ids}")
    zone = ZoneInfo(timezone)
    start = floor_to_quarter(now)
    run = _Run()
    later = _clip(capacity)
    room = _clip(now_capacity)
    limits = (dict(later), dict(room))
    by_tier = {tier: [d for d in demands if d.tier is tier] for tier in Tier}
    allocation: dict[str, float] = {}

    for tier in (Tier.SMART_OFF, Tier.CHARGE_NOW):
        # A smart-off charger keeps its first-quarter current in the later quarters, so
        # it is held to the smaller of the two rooms.
        stage = _stage_now(
            by_tier[tier],
            {p: min(room[p], later[p]) for p in Phase}
            if tier is Tier.SMART_OFF
            else room,
            run,
        )
        allocation.update(stage)
        for d in by_tier[tier]:
            _take(room, d, stage[d.charger_id])
            if tier is Tier.SMART_OFF:
                _take(later, d, stage[d.charger_id])

    plans: dict[str, SessionPlan] = {}
    planned = by_tier[Tier.PLANNED]
    if planned:
        latest = max(d.deadline for d in planned if d.deadline is not None)
        horizon = quarter_starts(start, latest) or [start]
        grid = _stage_plan(
            planned,
            horizon,
            prices,
            room,
            later,
            voltage,
            min_block_quarters,
            price_cap,
            run,
        )
        for d in planned:
            currents = [
                _current(d, t == 0, v) for t, v in enumerate(grid[d.charger_id])
            ]
            allocation[d.charger_id] = currents[0] if currents else 0.0
            _take(room, d, allocation[d.charger_id])
            plans[d.charger_id] = _session_plan(
                d, currents, horizon, prices, voltage, price_cap, zone
            )

    spare = _stage_now(by_tier[Tier.SPARE], room, run)
    allocation.update(spare)
    _verify(demands, allocation, plans, start, *limits)
    return Outcome(
        {d.charger_id: allocation[d.charger_id] for d in demands}, plans, run.proven
    )


def _verify(
    demands: Sequence[Demand],
    allocation: Mapping[str, float],
    plans: Mapping[str, SessionPlan],
    start: datetime,
    later: Mapping[Phase, float],
    first: Mapping[Phase, float],
) -> None:
    """Raise `SolverError` if rounding left a phase over its capacity."""
    drawn: dict[datetime, dict[Phase, float]] = {}
    now_draw = {phase: 0.0 for phase in Phase}
    for d in demands:
        for phase in d.phases:
            now_draw[phase] += allocation[d.charger_id]
        plan = plans.get(d.charger_id)
        for q in plan.quarters if plan else ():
            slot = drawn.setdefault(q.start.astimezone(UTC), {p: 0.0 for p in Phase})
            for phase in d.phases:
                slot[phase] += q.current
    for phase in Phase:
        if now_draw[phase] > first[phase] + _TOLERANCE:
            raise SolverError(
                0, f"{phase} solved at {now_draw[phase]} A, over {first[phase]} A"
            )
    for moment, slot in drawn.items():
        for phase in Phase:
            extra = sum(
                allocation[d.charger_id]
                for d in demands
                if d.tier is Tier.SMART_OFF and phase in d.phases
            )
            if moment != start and slot[phase] + extra > later[phase] + _TOLERANCE:
                raise SolverError(
                    0, f"{phase} solved at {slot[phase]} A, over {later[phase]} A"
                )


def _session_plan(
    demand: Demand,
    currents: Sequence[float],
    horizon: Sequence[datetime],
    prices: Mapping[datetime, float],
    voltage: float,
    price_cap: float | None,
    zone: ZoneInfo,
) -> SessionPlan:
    """Build a demand's plan from its solved currents."""
    quarters: list[PlannedQuarter] = []
    delivered = 0.0
    for moment, current in zip(horizon, currents, strict=False):
        if current <= 0:
            continue
        price = prices.get(moment)
        quarters.append(PlannedQuarter(moment.astimezone(zone), price, current))
        delivered += energy_per_quarter_kwh(current, len(demand.phases), voltage)
    return SessionPlan(
        charger_id=demand.charger_id,
        quarters=tuple(quarters),
        planned_kwh=delivered,
        reachable=delivered >= demand.need_kwh - _EPS,
        price_cap_exceeded=price_cap is not None
        and any(q.price is not None and q.price > price_cap for q in quarters),
        uses_unpriced=any(q.price is None for q in quarters),
    )


def main() -> None:
    """Showcase this module's functionality."""
    now = datetime(2026, 10, 21, 22, 0, tzinfo=UTC)
    deadline = now + timedelta(hours=8)
    cheap_hours = {0.30: [0, 1, 2], 0.10: [3, 4], 0.20: [5, 6, 7]}
    prices: dict[datetime, float] = {}
    for price, hours in cheap_hours.items():
        for hour in hours:
            for quarter in range(4):
                prices[now + timedelta(hours=hour, minutes=15 * quarter)] = price
    l1 = frozenset({Phase.L1})
    # Tier: SMART_OFF, CHARGE_NOW, PLANNED, SPARE
    demands = [
        Demand("boost", l1, Tier.CHARGE_NOW, 6, 16, 16),
        Demand("a", l1, Tier.PLANNED, 6, 16, 16, need_kwh=12.0, deadline=deadline),
        Demand("b", l1, Tier.PLANNED, 6, 16, 16, need_kwh=12.0, deadline=deadline),
        Demand("spare", l1, Tier.SPARE, 6, 16, 16),
    ]
    capacity = {Phase.L1: 20.0, Phase.L2: 20.0, Phase.L3: 20.0}
    now_capacity = {Phase.L1: 20.0, Phase.L2: 20.0, Phase.L3: 20.0}
    voltage = 230.0
    min_block_quarters = 2
    price_cap = None  # None, or a price such as 0.25
    timezone = "Europe/Copenhagen"

    outcome = optimize(
        demands,
        now,
        prices,
        capacity,
        now_capacity,
        voltage,
        min_block_quarters,
        price_cap,
        timezone,
    )

    print(f"proven optimal: {outcome.proven}")
    for charger_id, current in outcome.allocations.items():
        print(f"{charger_id}: {current} A now")
    for charger_id, plan in outcome.plans.items():
        first = plan.quarters[0].start.strftime("%H:%M") if plan.quarters else "-"
        count = len(plan.quarters)
        print(
            f"{charger_id}: {plan.planned_kwh:.1f} kWh in {count} quarters from {first}"
        )

    # A phase with no room at all: the cars are planned nothing and fall short.
    capacity = {Phase.L1: 0.0, Phase.L2: 0.0, Phase.L3: 0.0}
    now_capacity = {Phase.L1: 0.0, Phase.L2: 0.0, Phase.L3: 0.0}

    outcome = optimize(
        demands,
        now,
        prices,
        capacity,
        now_capacity,
        voltage,
        min_block_quarters,
        price_cap,
        timezone,
    )

    for charger_id, plan in outcome.plans.items():
        print(f"no room, {charger_id}: reachable {plan.reachable}")


if __name__ == "__main__":
    main()
