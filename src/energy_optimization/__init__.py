"""Package entry point.

Household energy optimization as pure decision cores, each a subpackage with its own
public API:

    from energy_optimization.ev import decide
    from energy_optimization.interruptible import schedule

`energy_optimization.ev` decides smart EV charging, `energy_optimization.interruptible`
plans loads that may be switched off for bounded periods, and
`energy_optimization.common` holds what both share (the price types, `ConfigError`,
`SolverError` and the quarter-hour grid). Nothing is re-exported at the package root.
"""
