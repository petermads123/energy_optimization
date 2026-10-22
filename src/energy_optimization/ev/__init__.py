"""EV charging decision core.

A pure function `decide` turns one complete `Snapshot` of an installation (time,
prices, chargers, sessions, measured phase currents, settings) into a `Decision`:
ordered actions, a plan and a reason per charger, the available amps per phase, a
status and events. Each decision also returns a `Memory` the caller hands back in the
next snapshot, which gives the core behaviour over time. The codec functions convert
snapshots, decisions and memory to and from plain dicts.
"""

from .codec import (
    decision_from_dict,
    decision_to_dict,
    memory_from_dict,
    memory_to_dict,
    snapshot_from_dict,
    snapshot_to_dict,
)
from .decide import decide
from .model import (
    Charger,
    ChargerKind,
    ChargerMemory,
    ChargerState,
    ConfigError,
    GuardLevel,
    Memory,
    Phase,
    PhaseCurrents,
    PriceInterval,
    PriceSeries,
    Reduction,
    Session,
    Settings,
    Snapshot,
)
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
    "ChargerMemory",
    "ChargerState",
    "ConfigError",
    "Decision",
    "Event",
    "EventKind",
    "GuardLevel",
    "Memory",
    "Phase",
    "PhaseAmps",
    "PhaseCurrents",
    "PlannedQuarter",
    "PriceInterval",
    "PriceSeries",
    "Reason",
    "ReasonCode",
    "Reduction",
    "Session",
    "Settings",
    "Snapshot",
    "Stage",
    "Status",
    "decide",
    "decision_from_dict",
    "decision_to_dict",
    "memory_from_dict",
    "memory_to_dict",
    "snapshot_from_dict",
    "snapshot_to_dict",
]
