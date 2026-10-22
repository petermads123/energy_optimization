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
folder, since the add-on image has no `git` — were deliberately left out of round 1. The core is
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
- Planning credits a part-used quarter (the one holding `now`, or the last one before an off-grid ready time) as a whole quarter; the error is at most one quarter and self-corrects on the next call. Next step: revisit if ready times off the 15-minute grid matter in practice.
- `LIMITED_BY_PHASE` is chosen when any phase of a short charger has its guard below the budget, even when another phase binds; the status is informational. Next step: attribute per binding phase if the status is ever acted on.
