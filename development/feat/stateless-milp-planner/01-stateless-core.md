# Stateless core

<!-- claude-plan step=2 status=active -->

| Field | Value |
|---|---|
| Feature | `feat/stateless-milp-planner` |
| Round | `1` |
| Branch | `feat/stateless-milp-planner` |
| Started | `2026-10-22` |

## Progress

| # | Step | Skill | Runs | Status |
|---|---|---|---|---|
| 1 | Conceptualize | `/conceptualize` | with the user | done |
| 2 | Plan | `/plan` | with the user | pending |
| 3 | Implement | `/implement` | in `/build` | pending |
| 4 | Verify | `/verify` | in `/build` | pending |
| 5 | Test | `/test` | in `/build` | pending |
| 6 | Concept check | `/concept-check` | in `/build` | pending |
| 7 | Ship | `/ship` | in `/build` | pending |
| 8 | Recommend | `/recommend` | with the user | pending |
| 9 | Pull request | `/create-pr` | with the user | pending |
| 10 | Review | `/watch-pr` | on the pull request | pending |

Statuses: `pending`, `in progress`, `done`.

## Builds on

Nothing — this is the first round.

The code it changes is `feat/ev-charging-core` (merged to `main` as pull request #2): round
1 (`development/feat/ev-charging-core/01-decision-core.md`) built the single-call decision
core; round 2 (`development/feat/ev-charging-core/02-behaviour-over-time.md`) added memory
and behaviour over time on top. This round removes round 2's behaviour and keeps round 1's.

---

## 1. Concept

### What this is

`decide(snapshot)` plans from the snapshot alone, as `feat/ev-charging-core` round 1 did.
Everything that branch's round 2 added that needs memory of earlier calls is removed:
memory and the conservative start, minimum pause and hysteresis, regulation to measured
draw (cut, ramp, resume probe), phase-guard levels over time (green hold, emergency latch),
pacing, in-flight command handling, settling after a decrease, and `next_check`. Round 1's
planning, priority tiers, sharing, per-phase budget and measured phase headroom, fallback
cap under stale measurements, reservations, reasons, actions, status, events and codec
stay — and so do the bug fixes round 2 made in code round 1 shipped (overflow guards,
`_expected_finish` on a trickle of current, and the like).

### Why it is worth building

The user's decision: the repo is a general planning core and should "simply create the
plan given the constraints and states". Timing and command handling belong to the caller —
in the user's case an AppDaemon app in Home Assistant that calls every 5 minutes and on
events (plug-in, a user change), checks itself whether chargers use their setpoints, waits
between instructions, and decreases chargers locally when a phase gets close to its limit
before calling again. Behaviour that depends on earlier calls duplicates what the caller
does and makes the core harder to reason about. It also clears the ground for round 2,
whose solver model shrinks once the memory-driven rules are gone.

### Inputs and outputs

Exactly `feat/ev-charging-core` round 1's: a `Snapshot` (time, settings, chargers, states,
sessions, prices, phase currents) in, a `Decision` (ordered actions with absolute targets,
per-charger decisions, available amps, status, events) out, both convertible to and from
plain JSON-compatible dicts. A snapshot dict that still carries `memory` or a round-2
setting is rejected with a `ConfigError` naming the field; there are no released callers.

### How it connects to the rest of the repo

Changes `src/energy_optimization/ev/` (`model`, `result`, `planning`, `allocation`,
`decide`, `codec`, `__init__`), deletes `guard.py` and `regulation.py` with their tests,
and updates the remaining `tests/test_ev_*.py`, `STRUCTURE.md` and `DEVELOPMENT.md`.
Nothing outside the package and its docs.

### Explicitly out of scope

- **Round 2 on this branch — the exact solver:** the HiGHS MILP (via
  `scipy.optimize.milp`) replaces the greedy planner and the current-quarter sharing, on
  the simplified core. Agreed with the user so far: scipy and numpy become runtime
  dependencies (round 1's stdlib-only rule is amended); the greedy code is removed with no
  fallback; on failure `decide` raises a `SolverError` and the caller handles it in HA;
  determinism via a node budget and a single thread; the 100 ms target becomes a stated
  solve budget. Its criteria are confirmed at its own step 1.
- Commands in flight, waiting between commands, and any other timing — the caller's.
- The example AppDaemon app.
- Any new behaviour.

### Acceptance criteria

| # | The finished change... |
|---|---|
| A1 | **Is stateless.** `decide` depends only on its snapshot; the public types and signatures are exactly `feat/ev-charging-core` round 1's (section 2 of `01-decision-core.md`, in its original round-1 form), and no memory type, round-2 setting or round-2 enum member remains. |
| A2 | **Removes round 2 cleanly.** `guard.py`, `regulation.py` and their tests are deleted; the round-2 parameters on `phase_capacity` (`settling`), `plan_sessions` (`first`) and `PlanRequest` (`skip_current`) are gone; `STRUCTURE.md` and the `ev` exports match the code. |
| A3 | **Behaves like round 1.** A differential against `feat/ev-charging-core` round 1's `decide` (commit `982a494`) over generated snapshots gives equal decisions, except where a round-2 bug fix in shared code applies — each such difference listed with the test that pins it. |
| A4 | **Keeps every round-1 guarantee.** `feat/ev-charging-core` round 1's criteria A1–A10 all hold; the round-1 suite passes with its warm-memory helper removed and no expected value changed. |
| A5 | **Has a round-1-shaped codec.** Snapshot and decision dicts carry exactly round 1's keys; a dict with `memory` or a round-2 setting is rejected with a `ConfigError` naming that field. |

### Open questions

None.

---

## 2. Plan

Accepted by the user in advance: at the concept gate they chose "Confirm" — write the plan,
have the critic review it, then build without stopping for the plan. Step 3 opens once the
`plan-critic` findings below are applied.

### Survey

`git diff 982a494 origin/main` (round 1's head of `feat/ev-charging-core` against `main`)
shows what round 2 did to code round 1 shipped:

- `model.py`, `result.py`, `allocation.py`, `__init__.py`: **additions only** — memory
  types, round-2 settings, enum members, `Decision` fields, `phase_capacity(settling)`,
  re-exports. No round-1 line removed or changed.
- `planning.py`: round 1's `_select` / `_plan_one` / `plan_sessions` body rewritten only to
  carry `first` and `skip_current` (round 2). No round-1 bug fix.
- `codec.py`: memory keys and round-2 settings; the `price_cap` nullable branch widened to
  `emergency_threshold`. No round-1 bug fix.
- `decide.py`: round 2 rewrote the pipeline around memory. **One change fixes a latent
  round-1 defect:** `_expected_finish` without a plan computed `ctx.now + timedelta(hours=…)`
  — wall-clock arithmetic on a zone-aware `now` (an hour off across DST) that also raises
  `OverflowError` for a trickle of current. Round 2 made it UTC arithmetic and returned
  `None` on overflow (`decide.py` lines 508–515 on `main`).
- `quarters.py`: unchanged.
- Tests: round 2 added memory/guard/regulation tests, gave round-1 `decide` tests a
  warm-memory helper, widened the exact enum-member sets in `tests/test_ev_result.py`, and
  added a plan-fit oracle (`assert_plans_fit`) to the round-1 invariants. The plan-fit
  oracle and the expected-finish regressions test round-1 behaviour and need no memory.
- `STRUCTURE.md`: 163 lines added, 39 changed by round 2; `README.md` and `CLAUDE.md` do not
  mention memory.

So round 1's files at `982a494` plus the one fix *are* the target. Nothing needs inventing.

### Approach

**Chosen: restore, then re-apply.** Restore every `src/energy_optimization/ev/*.py` and
every round-1 `tests/test_ev_*.py` from commit `982a494`; delete `guard.py`,
`regulation.py`, `tests/test_ev_guard.py`, `tests/test_ev_regulation.py`; then re-apply the
single latent fix (`_expected_finish`) and port the memory-free round-2 tests that pin
round-1 behaviour. A3's "equal to round 1 except listed fixes" then holds by construction
and the differential only has to confirm it.

**Rejected: strip round 2 out of the current code by hand.** `decide.py` interleaves round
2 through its whole pipeline (`_commanded`, `_holders`, `_held_off`, `_lower`, memory
building, `next_check`); removing it piece by piece risks leaving residue that the
differential would then have to find. Restoring is exact.

### Modules

| Path | New or changed | Purpose |
|---|---|---|
| `src/energy_optimization/ev/model.py` | restored to `982a494` | Round 1's input types; no memory, no round-2 settings (A1) |
| `src/energy_optimization/ev/result.py` | restored | Round 1's output types and enum members (A1) |
| `src/energy_optimization/ev/planning.py` | restored | Round 1's planner; no `first`, no `skip_current` (A2) |
| `src/energy_optimization/ev/allocation.py` | restored | Round 1's; no `settling` (A2) |
| `src/energy_optimization/ev/decide.py` | restored + one fix | Round 1's pipeline; `_expected_finish` in UTC and `None` on overflow (A3) |
| `src/energy_optimization/ev/codec.py` | restored | Round 1's dict shape; unknown keys (`memory`, round-2 settings) rejected (A5) |
| `src/energy_optimization/ev/__init__.py` | restored | Round 1's exports (A1, A2) |
| `src/energy_optimization/ev/quarters.py` | unchanged | — |
| `src/energy_optimization/ev/guard.py`, `regulation.py` | deleted | Round-2 only (A2) |
| `tests/test_ev_guard.py`, `tests/test_ev_regulation.py` | deleted | Their modules are gone (A2) |
| `tests/test_ev_*.py` (the other seven) | restored + ported tests | Round 1's suite, plus the memory-free round-2 tests listed in T3, T5 (A3, A4) |
| `STRUCTURE.md` | changed | `ev/` and test sections back to round 1's content, with the fix and the ported tests described (A2) |
| `pyproject.toml` | unchanged | `hypothesis` and `tzdata` stay dev-only |

### Public API

**No public name is added.** The public API is exactly `feat/ev-charging-core` round 1's,
as it stood at commit `982a494` — that is the reference step 4 checks against
(`git show 982a494:src/energy_optimization/ev/<module>.py`), not the table in
`01-decision-core.md`, which `feat/ev-charging-core`'s step 9 updated to round 2's
signatures and which stays as written (plan files are a historical record).

Removed, each with A1/A2 behind it:

| Removed | Module |
|---|---|
| `Reduction`, `GuardLevel`, `ChargerMemory`, `Memory`; `Snapshot.memory`; the 15 round-2 `Settings` fields (`emergency_threshold` … `max_memory_age`) | `model.py` |
| `ReasonCode.MINIMUM_PAUSE`, `.UNDER_USE`, `.EMERGENCY`; `EventKind.MEMORY_RESET`, `.PHASE_GUARD_INTERVENED`; `Status.EMERGENCY`; `Decision.memory`, `.guard_level`, `.next_check` | `result.py` |
| `PlanRequest.skip_current`; `plan_sessions(..., first=...)` | `planning.py` |
| `phase_capacity(..., settling=...)` | `allocation.py` |
| `memory_to_dict`, `memory_from_dict`; the `memory`, `guard_level`, `next_check` keys | `codec.py` |
| `instant_level`, `GuardState`, `advance_guard`, `main` | `guard.py` (module deleted) |
| `Regulation`, `regulate`, `main` | `regulation.py` (module deleted) |

### Implementation guide

1. Restore the package and the round-1 tests from round 1's head:
   `git checkout 982a494 -- src/energy_optimization/ev/ tests/test_ev_model.py
   tests/test_ev_result.py tests/test_ev_quarters.py tests/test_ev_planning.py
   tests/test_ev_allocation.py tests/test_ev_decide.py tests/test_ev_codec.py`. Then
   `git rm` `src/energy_optimization/ev/guard.py`, `src/energy_optimization/ev/regulation.py`,
   `tests/test_ev_guard.py`, `tests/test_ev_regulation.py` (the checkout does not delete
   files that did not exist at `982a494`).
2. Re-apply the fix in `decide.py`'s `_expected_finish`, unplanned branch: compute in UTC
   (`ctx.now.astimezone(UTC) + timedelta(hours=hours)`, then `.astimezone(ctx.zone)`) and
   return `None` on `OverflowError`, with a one-line comment, exactly as on `main`.
3. Run the four gates and every `python -m energy_optimization.ev.<module>` showcase; all
   green before the step closes (the restored suite was green at `982a494`).
4. `STRUCTURE.md`: replace the `ev/` package sections and the `tests/test_ev_*.py`
   sections with `982a494`'s text (`git show 982a494:STRUCTURE.md`), remove the
   `guard.py`, `regulation.py`, `test_ev_guard.py`, `test_ev_regulation.py` sections, and
   keep everything else in the file (hooks, `.claude/`, plans) as on `main`. Mention the
   expected-finish fix in the `decide.py` entry and any ported tests in their test-file
   entries.
5. `DEVELOPMENT.md` is step 8's to clean; leave it.

### Test intents

| # | Must prove | Covers |
|---|---|---|
| T1 | The public surface equals round 1's: `ev.__all__` is exactly round 1's set; `guard` and `regulation` are not importable from `energy_optimization.ev`; `Snapshot`, `Settings`, `Decision`, `PlanRequest` have exactly round 1's fields; `plan_sessions` and `phase_capacity` have round 1's parameters; the enum member sets are round 1's (the restored `tests/test_ev_result.py` already pins those) | A1, A2 |
| T2 | The restored round-1 suite passes with no expected value changed — the only edits to restored test files are additions | A4 |
| T3 | Expected finish without a plan: correct across both 2026 Copenhagen DST nights (the finish instant is `now + need/(V·A·n)` in UTC), and `None` — not an exception — for a trickle of current that no calendar can hold | A3 |
| T4 | `snapshot_from_dict` rejects a `memory` key, a top-level `guard_level`/`next_check` in a decision dict, and each of the 15 round-2 settings keys, with a `ConfigError` whose field is the dotted path (`memory`, `settings.min_pause`, …) | A5 |
| T5 | The round-1 `hypothesis` invariants still hold, now also checking (ported from round 2, memory-free) that no plan books more than a phase holds in any quarter | A4 |
| T6 | Determinism and the standard-library-only AST scan still pass (round 1's T18) | A1, A4 |

The differential against `982a494`'s `decide` (A3) is step 6's probe, run from a scratch
copy outside the repo, not a test: the old code cannot live in the tree.

**Coverage check.** A1 → removed-API table, T1, T6. A2 → modules table, T1. A3 → the fix,
T3, step 6 differential. A4 → T2, T5, T6. A5 → restored codec, T4. Nothing is added to the
public API, so nothing lacks a criterion.

### Risks

1. **The restore picks up a round-1 test that round 2 had corrected.** If a restored test
   fails on the restored code, the code at `982a494` was green, so the failure is in the
   one re-applied fix or the environment: fix that, do not edit the restored expectation. If
   a restored expectation conflicts with the fix itself (expected finish), the test changes
   to the fixed value and is named in section 3.
2. **`STRUCTURE.md` drift.** The stop gate's file check catches a deleted module still
   listed; the `structure-auditor` before steps 4 and 6 catches the rest.
3. **Round 2 fixed something in shared code that this survey missed.** The step-6
   differential shows it as an unexplained difference. If the difference is a round-1
   defect round 2 fixed, port that fix too and list it (A3 allows listed fixes); if it is
   round-2 behaviour, the restore is right as it is.
4. **The pinned old plan table.** `01-decision-core.md`'s Public API table shows round-2
   signatures (updated at the last branch's step 9). Step 4 compares against `982a494`'s
   code, not that table, and does not edit the old plan file.

---

## 3. Implementation notes

> Written in step 3. Only deviations from the plan above, each with its reason. "Built as
> planned" is a complete and good entry. On a fix round, also the reproduction test's red
> run, pasted here before the fix was written — step 6 cites it.

---

## 4. Verification log

> Written in step 4: the static half. Command output, not a summary of it.

| Check | Result |
|---|---|
| `ruff check .` | |
| `ruff format --check .` | |
| `mypy` | |
| Plan completeness | every signature in the Public API table exists as written |
| `STRUCTURE.md` | in sync |
| `python -m <package>.<module>` | |

---

## 5. Test log

> Written in step 5: the dynamic half.

| Intent | Test names | Result |
|---|---|---|

Edge cases considered and deliberately skipped, with reasons:

---

## 6. Concept check

> Written in step 6, against section 1 — not against section 2. The question is whether
> the thing built is the thing agreed, not whether it matches the plan.

| # | Criterion | Met | Evidence |
|---|---|---|---|
| A1 | | | |

Drift found, and what was done about it:

### Earlier rounds still hold

> Later rounds only. Re-check every acceptance criterion from every earlier round in this
> folder: this round changed code they depend on, and their tests passing is necessary but
> not sufficient — a criterion can be satisfied by tests that no longer describe what the
> feature does.

| Round | # | Criterion | Still met | Evidence |
|---|---|---|---|---|

---

## 7. Ship log

| Field | Value |
|---|---|
| Commits | |
| Pushed to | |

---

## 8. Recommendations

> Written in step 8. Only follow-ups that are critical and belong to this work, which most
> rounds do not have: replace the table with `None.` when there are none. Lesser ideas are
> one-line notes in `DEVELOPMENT.md`, not rows here. Not bugs in what this round built —
> those go back through `/build` before the pull request. A critical defect outside what
> section 1 promised, such as a class member it put out of scope, is a recommendation here,
> and its round opens through `/fix`.

| # | Recommendation | Why it is critical | Effort | Decision |
|---|---|---|---|---|
| R1 | | | | |

Decisions: `deferred`, `rejected`, or `next round` — a new numbered file in this folder,
taken back through steps 1 to 7 on the same branch.

---

## 9. Pull request

> Written in step 9, in the commit that opens the pull request — so the URL is not known
> yet and the pull request is found from the branch. The review itself is recorded on the
> pull request thread, not here: this file is `done` from step 9 on.

| Field | Value |
|---|---|
| URL | opened by step 9 — see the branch's pull request |
| Opened as | ready for review |

---

## Halted

> Only if the build stopped. Written by `/build`: the step, the reason verbatim from the
> step that halted, the question for the user — and, once answered, the answer and what
> changed because of it. Never deleted; it is the record of where the plan was thinner
> than the code needed.
