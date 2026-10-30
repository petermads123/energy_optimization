"""Interruptible-load scheduler.

A pure function `schedule` turns one `LoadSnapshot` (the time, a load with its off-time
limits, the prices and the load's recent history) into a `LoadSchedule`: on or off for
the current quarter, a plan for every priced quarter, the saving against always-on and
a reason. The plan comes from a mixed-integer program solved by HiGHS; `SolverError`
is raised when the solver fails. `ConfigError`, `PriceInterval` and `PriceSeries` are
re-exported from `energy_optimization.common`, so one import is enough.
"""

from energy_optimization.common import (
    ConfigError,
    PriceInterval,
    PriceSeries,
    SolverError,
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
from energy_optimization.interruptible.scheduler import schedule

__all__ = [
    "ConfigError",
    "Limits",
    "Load",
    "LoadSchedule",
    "LoadSnapshot",
    "LoadState",
    "PriceInterval",
    "PriceSeries",
    "Reason",
    "ReasonCode",
    "ScheduledQuarter",
    "SolverError",
    "schedule",
]
