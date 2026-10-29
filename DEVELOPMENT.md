# Development notes

Development-side open questions and things to fix later: what the code does not do yet, a
question nobody can answer yet, an idea that fell short of a recommendation. Not a design
document and not a changelog.

**Adding an entry:** a heading, the date and the branch/round it came from, the item itself,
and its next step or where it is configured. **Resolving an entry:** the change that
resolves it deletes it — this file only ever shows what is still open. Step 8 of every round
ends by cleaning it: entries the branch resolved are removed, duplicates are merged, and
only open items are left. Before an entry is deleted or renamed, anything that points at it
by heading is updated in the same change.

## Example AppDaemon app and HA usage guide

2026-10-21, `feat/ev-charging-core` round 1. The decision core is generic; its first caller
is an AppDaemon app in Home Assistant. A worked example app (outside the package) and a
README "Using from Home Assistant" section — install by copying the package into the apps
folder, since the add-on image has no `git`, and list `numpy` and `scipy` under the add-on's
`python_packages` since the solver needs them — were deliberately left out of round 1. The core is
stateless, so the app calls it on its own schedule and owns timing, waiting between
commands, regulation to the setpoint and anti-flapping. Next step: its own feature, after
the branch merges.

## Age of a car's SOC reading

2026-10-21, `feat/ev-charging-core` round 1. Car integrations update SOC rarely while the
car sleeps, so a session's SOC can be hours old. The core takes SOC as given; judging its
age is the caller's job for now. Next step: decide whether a session should carry the SOC's
timestamp and the core should distrust an old one.

## `feat/ev-charging-core` round 1 notes

2026-10-21, from round 1's step 5 and step 6 records (`development/feat/ev-charging-core/01-decision-core.md`, section 5).

- A `plugged_in_at` later than `now` (clock skew) moves the deadline a full day when the ready time falls in between; section 1 does not decide it. Next step: reject or clamp a future `plugged_in_at` once a real caller shows whether it happens.
- Planning credits a part-used quarter (the one holding `now`, or the last one before an off-grid ready time) as a whole quarter; the error is at most one quarter and self-corrects on the next call. Since the solver round, a plan can then be `reachable` while `reaches_target` is False and no `TARGET_UNREACHABLE` is raised (pinned by `test_decide_a_ready_time_inside_a_quarter_credits_the_whole_quarter`). Next step: revisit if ready times off the 15-minute grid matter in practice.
- `LIMITED_BY_PHASE` is chosen when any phase of a short charger has its guard below the budget, even when another phase binds; the status is informational. Next step: attribute per binding phase if the status is ever acted on.

## `feat/stateless-milp-planner` round 1 notes

2026-10-22, from round 1's step 5 records (`development/feat/stateless-milp-planner/01-stateless-core.md`, section 5). Round-1 behaviour, unchanged; round 2 rewrites the planner and may settle some of them.

- `reaches_target` is `False` for a need already met once the deadline has passed (`_reaches` requires `deadline > now`), so a finished session reads "will not reach target" after its ready time. Next step: decide whether a covered need reports `True`.
- A fixed or unavailable charger with a session gets `expected_finish` from its reservation, an assumed level rather than measured delivery. Next step: decide whether such a finish should be `None`.
- Docstrings lag the code: `ChargerDecision` says the session fields are `None` "without a session", but a session with no car also gives `None`; `_expected_finish` does not say a trickle gives `None`. Next step: reword when the round touching them lands.

## `feat/stateless-milp-planner` round 2 notes

2026-10-22, from round 2's step 5 and step 6 records (`development/feat/stateless-milp-planner/02-milp-solver.md`, sections 5 and 6).

- A need smaller than one minimum-current quarter is planned in the last quarter before the ready time when `min_block_quarters > 1` (a run the deadline cuts short is not penalised), so the car reads `PLANNED_LATER` for a need it could meet now. Next step: decide whether a single short run should be exempt from the block rule.
- HiGHS sometimes prints `HighsMipSolverData::transformNewIntegerFeasibleSolution tmpSolver.run();` to stdout during a solve, which will land in the AppDaemon log. Next step: silence it if it is noisy in practice.
- Off the A5 grid a call can be slow: a tight grid (20 A for 7 chargers) took up to 176 s at one point, and planned-heavy snapshot #78 (4 chargers at 16 A) takes 26 s, 15 s of it the first attempt the presolve retry repeats. Next step: measure on the target hardware (Raspberry Pi) before relying on the call cadence.
- The exact price hold (whole ticks) is off whenever any on/off current is fractional; the fallback tolerance can then trade away a one-tick price gap worth under 1e-6 EUR. Next step: none unless a fractional on/off charger is ever used.
- The overfill bound (a plan may exceed the need by at most one quarter at the top current) changes round 1's plans at negative prices. Next step: confirm with a real negative-price day.
- Test debt: the leftover rule's effect on `status` has no dedicated test, and the brute-force property at 60 examples rarely reaches the minimum-block level (it does at 600). Next step: add both when the tests are next touched.

## Interruptible load scheduler: window budget past the horizon

2026-10-29, `feat/interruptible-load-scheduler` round 1. The off-budget of a window that reaches
past the last priced quarter is not reserved, so a plan can spend it at the end of the horizon
and leave the first quarters after it starved of off-time. Accepted because the core re-plans
every quarter and prices usually reach 10+ hours ahead. Next step: revisit if a real plan shows
the quarters after the horizon starved of off-time.

## Interruptible load scheduler: JSON codec

2026-10-29, `feat/interruptible-load-scheduler` round 1. The new core has no dict/JSON codec
like `ev.codec`; round 1 left it out. Next step: add one when a caller needs to log or replay
inputs.
