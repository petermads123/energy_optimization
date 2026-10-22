"""EV charging decision core.

A pure function `decide` turns one complete `Snapshot` of an installation (time,
prices, chargers, sessions, measured phase currents, settings) into a `Decision`:
ordered actions, a plan and a reason per charger, the available amps per phase, a
status and events. The codec functions convert both to and from plain dicts. The
plan comes from a mixed-integer program (`optimize`); `SolverError` is raised when the
solver fails.
"""

from .codec import (
    decision_from_dict,
    decision_to_dict,
    snapshot_from_dict,
    snapshot_to_dict,
)
from .decide import decide
from .model import (
    Charger,
    ChargerKind,
    ChargerState,
    ConfigError,
    Phase,
    PhaseCurrents,
    PriceInterval,
    PriceSeries,
    Session,
    Settings,
    Snapshot,
)
from .optimize import SolverError
from .result import (
    Action,
    ActionKind,
    ChargerDecision,
    Decision,
    Event,
    EventKind,
    PhaseAmps,
    PlannedQuarter,
    Reason,
    ReasonCode,
    Stage,
    Status,
)

__all__ = [
    "Action",
    "ActionKind",
    "Charger",
    "ChargerDecision",
    "ChargerKind",
    "ChargerState",
    "ConfigError",
    "Decision",
    "Event",
    "EventKind",
    "Phase",
    "PhaseAmps",
    "PhaseCurrents",
    "PlannedQuarter",
    "PriceInterval",
    "PriceSeries",
    "Reason",
    "ReasonCode",
    "Session",
    "Settings",
    "Snapshot",
    "SolverError",
    "Stage",
    "Status",
    "decide",
    "decision_from_dict",
    "decision_to_dict",
    "snapshot_from_dict",
    "snapshot_to_dict",
]
