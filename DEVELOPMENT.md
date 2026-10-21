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
folder, since the add-on image has no `git` — were deliberately left out of round 1. Next
step: a later round once the core's input/output shapes have settled after round 2.

## Age of a car's SOC reading

2026-10-21, `feat/ev-charging-core` round 1. Car integrations update SOC rarely while the
car sleeps, so a session's SOC can be hours old. The core takes SOC as given; judging its
age is the caller's job for now. Next step: decide whether a session should carry the SOC's
timestamp and the core should distrust an old one.
