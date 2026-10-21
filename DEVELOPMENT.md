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
folder, since the add-on image has no `git` — were deliberately left out of round 1. Both rounds
are now on the branch and the shapes have settled; the example app must also persist the
returned `Memory` between calls (an HA entity or a file) and schedule a call at
`next_check`. Next step: its own feature, after the branch merges.

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

## `feat/ev-charging-core` round 2 notes

2026-10-21, from round 2's step 5 and step 6 records (`development/feat/ev-charging-core/02-behaviour-over-time.md`, sections 5 and 6).

- A lost, invalid or out-of-date memory drops a latched emergency: the restart is conservative and the guard still reacts at once to a reading above the emergency threshold, but a reading back under it may resume after the green hold rather than the emergency calm. Next step: decide whether the caller should persist memory more often, or the core should treat a reset just after high readings more strictly.
- `yellow_margin` has no upper bound relative to `phase_threshold`; a margin at or above the threshold keeps an idle installation yellow and blocks every increase. Next step: validate it in `Settings` if a real configuration ever trips it.
- On a conservative start the overall status can read `waiting_for_capacity` while capacity exists (the reasons say `held_by: memory_reset`). Next step: a dedicated status if the dashboard shows it confusingly.
- `next_check` is clamped to the memory's expiry, so it can be earlier than the next real change. Harmless (one extra call); revisit only if call volume matters.
- While a charger waits out its minimum pause, the default two-quarter minimum block can leave the current quarter idle when the held-off charger's own plan takes the next one. Next step: let a newcomer use a single held-free quarter if idle capacity shows up in practice.
- A session more than the hysteresis earlier than a selected charger can take the current quarter ahead of a third session with less slack, even when it displaces nobody (once in 48 000 generated calls). Consistent with the ranked tiers; a small `_holders` change would reverse it.
- The `planning` showcase does not exercise `first` or `skip_current`; the tests do.
