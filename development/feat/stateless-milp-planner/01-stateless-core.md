# Stateless core

<!-- claude-plan step=7 status=active -->

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
| 2 | Plan | `/plan` | with the user | done |
| 3 | Implement | `/implement` | in `/build` | done |
| 4 | Verify | `/verify` | in `/build` | done |
| 5 | Test | `/test` | in `/build` | done |
| 6 | Concept check | `/concept-check` | in `/build` | done |
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
  solve budget; the solver works in UTC throughout and converts every input to it (A6's
  rule, stated by the user at this round's step-5 halt). Its criteria are confirmed at its own step 1.
- Commands in flight, waiting between commands, and any other timing — the caller's.
- The example AppDaemon app.
- Any new behaviour.

### Acceptance criteria

| # | The finished change... |
|---|---|
| A1 | **Is stateless.** `decide` depends only on its snapshot; the public types and signatures are exactly `feat/ev-charging-core` round 1's (section 2 of `01-decision-core.md`, in its original round-1 form), and no memory type, round-2 setting or round-2 enum member remains. |
| A2 | **Removes round 2 cleanly.** `guard.py`, `regulation.py` and their tests are deleted; the round-2 parameters on `phase_capacity` (`settling`), `plan_sessions` (`first`) and `PlanRequest` (`skip_current`) are gone; `STRUCTURE.md` and the `ev` exports match the code. |
| A3 | **Behaves like round 1.** A differential against `feat/ev-charging-core` round 1's `decide` (commit `982a494`) over generated snapshots gives equal decisions, except where a round-2 bug fix in shared code applies, or where A6 corrects time arithmetic round 1 did on a zone-aware datetime — each such difference listed with the test that pins it. |
| A4 | **Keeps every round-1 guarantee.** `feat/ev-charging-core` round 1's criteria A1–A10 all hold; the round-1 suite passes with its warm-memory helper removed and no expected value changed. |
| A5 | **Has a round-1-shaped codec.** Snapshot and decision dicts carry exactly round 1's keys; a dict with `memory` or a round-2 setting is rejected with a `ConfigError` naming that field. |
| A6 | **Does its time arithmetic in UTC.** Every aware datetime the core receives is converted to UTC before it is added to, subtracted from or compared; no arithmetic is done on a datetime carrying a `ZoneInfo`. Output datetimes are still presented in the settings timezone, as in round 1. Proven across both 2026 Copenhagen DST nights, including an expected finish from a plan ending in or at the repeated hour (added at the step-5 halt, 2026-10-22). |

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
   **Mechanical check, recorded in section 3:** `git diff origin/main --
   src/energy_optimization/ev/{model,result,allocation,quarters,__init__}.py` must contain
   only `-` lines (round-2 additions removed). Any `+` line is a round-2 edit to round-1
   code: classify it in section 3 as a round-1 fix to port (A3 allows listed fixes) or as
   round-2 behaviour. For `codec.py` the only expected `+` lines are the `price_cap`
   nullable branch (round 1's form) and the memory/settings decoders; anything else is
   classified the same way.
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
5. `DEVELOPMENT.md` (section 1 lists it as changed): delete the whole
   "`feat/ev-charging-core` round 2 notes" section — every bullet is about a mechanism this
   round removes — and rewrite the last two sentences of "Example AppDaemon app and HA usage
   guide": the core is stateless, so the app calls it on its own schedule and owns timing,
   waiting between commands, regulation to the setpoint and anti-flapping.

### Test intents

| # | Must prove | Covers |
|---|---|---|
| T1 | The public surface equals round 1's: `ev.__all__` is exactly round 1's set; `guard` and `regulation` are not importable from `energy_optimization.ev`; `Snapshot`, `Settings`, `Decision`, `PlanRequest` have exactly round 1's fields; `plan_sessions` and `phase_capacity` have round 1's parameters; the enum member sets are round 1's (the restored `tests/test_ev_result.py` already pins those) | A1, A2 |
| T2 | The restored round-1 suite passes with no expected value changed — the only edits to restored test files are additions. Step 5 also checks `git diff 982a494 origin/main -- tests/test_ev_{model,result,quarters,planning,allocation,codec}.py` for round-2 strengthenings of round-1 tests that need no memory (e.g. the stronger showcase assertions) and ports those that still apply, as additions | A4 |
| T3 | **New tests** (none exists on `main`): expected finish without a plan, with `now` built as `datetime(..., tzinfo=ZoneInfo("Europe/Copenhagen"))` either side of both 2026 transitions and stale prices so the allocation is the finish source — expected `(now.astimezone(UTC) + timedelta(hours=need·1000/(V·A·n))).astimezone(zone)`; and `None`, not an exception, for a trickle no calendar can hold (a huge `energy_need_kwh`, or a reserved charger with a tiny measured draw). Both must be red on `982a494` (shown in step 6's probe) | A3 |
| T4 | `snapshot_from_dict` rejects `memory` (including `"memory": null`) with field `memory`, and each of the 15 round-2 settings keys with `settings.<name>`; `decision_from_dict` rejects `memory`, `guard_level` and `next_check` with that key as the field; and the key sets of `snapshot_to_dict(...)`, its `settings` and `decision_to_dict(...)` equal literal round-1 sets | A5 |
| T5 | The round-1 `hypothesis` invariants still hold, now also checking (ported from round 2, memory-free) that no plan books more than a phase holds in any quarter | A4 |
| T6 | Determinism and the standard-library-only AST scan still pass (round 1's T18) | A1, A4 |

Two differentials are step 6's probes, run from scratch copies outside the repo, not tests:
(1) the restored `decide` against `982a494`'s — equal except the expected-finish fix
(where `982a494` raises `OverflowError` and the new code returns `None`, that is the
listed difference, not a crashed probe); (2) the restored `decide` against `origin/main`'s,
run where round 2's rules do nothing (blank warm memory, no emergency threshold, readings
below `phase_threshold − yellow_margin` or stale, no charger memory entries) — every
difference other than round-2 output fields is classified, which is what catches a round-1
fix the survey missed.

**Coverage check.** A1 → removed-API table, T1, T6. A2 → modules table, T1. A3 → the fix,
T3, step 6 differential. A4 → T2, T5, T6. A5 → restored codec, T4. Nothing is added to the
public API, so nothing lacks a criterion.

### Risks

1. **The restore picks up a round-1 test that round 2 had corrected.** If a restored test
   fails on the restored code, the code at `982a494` was green, so the failure is in the
   one re-applied fix or the environment: fix that, do not edit the restored expectation. If
   a restored expectation conflicts with the fix itself (expected finish), the build halts
   and asks; it does not edit the restored expectation (A4). Unlikely: the round-1 no-plan
   finish tests use a UTC `now` away from DST.
2. **`STRUCTURE.md` drift.** The stop gate's file check catches a deleted module still
   listed; the `structure-auditor` before steps 4 and 6 catches the rest.
3. **Round 2 fixed something in shared code that this survey missed.** Step 3's
   mechanical diff check and step 6's second differential (against `origin/main`) show it. If the difference is a round-1
   defect round 2 fixed, port that fix too and list it (A3 allows listed fixes); if it is
   round-2 behaviour, the restore is right as it is.
4. **The pinned old plan table.** `01-decision-core.md`'s Public API table shows round-2
   signatures (updated at the last branch's step 9). Step 4 compares against `982a494`'s
   code, not that table, and does not edit the old plan file.

### Critique

The `plan-critic` read section 1, then this section, round 2's records and the code on
`main` (it had no shell, so it checked the survey against round 2's own step-5 findings,
its T14 differential and the reflog); verdict "accept with changes". All six applied:

1. *Risk 3's net can't catch a missed fix — the step-6 differential only compares round 1
   with round 1.* **Applied:** a mechanical `-`-lines-only diff check at guide 1, and a
   second differential against `origin/main` where round 2's rules do nothing.
2. *`DEVELOPMENT.md` drifts from section 1.* **Applied:** guide 5 deletes the round-2
   notes and rewrites the AppDaemon entry.
3. *Risk 1's fallback (edit the expectation) amends A4.* **Applied:** halt instead.
4. *T3 can't fail on `982a494` unless `now` is ZoneInfo-aware; no such regression tests
   exist to port.* **Applied:** T3 now specifies new tests with a Copenhagen `now` and stale
   prices, red on `982a494`.
5. *T4 misplaces the decision keys, misses `memory: null` and A5's first half.*
   **Applied.**
6. *Round 2's stronger showcase assertions would be lost.* **Applied:** T2 ports memory-free
   strengthenings.

Coverage unchanged: no public API added; every criterion still has a test intent.

---

## 3. Implementation notes

> Written in step 3. Only deviations from the plan above, each with its reason. "Built as
> planned" is a complete and good entry. On a fix round, also the reproduction test's red
> run, pasted here before the fix was written — step 6 cites it.

Built as planned. Record of the mechanical check (guide 1), `git diff origin/main --
src/energy_optimization/ev/{model,quarters}.py` has no `+` line at all; the others have only
round-1 forms of round-2 edits, none a round-1 bug fix:

| File | `+` lines | Classification |
|---|---|---|
| `result.py` | `from .model import Phase` | round-1 import (round 2 widened it to `GuardLevel, Memory, Phase`) |
| `allocation.py` | `phase_capacity(snapshot, fresh)` signature and docstring lines; `present_draw(...)` in the draw sum | round-1 form of the `settling` branch |
| `__init__.py` | one module-docstring line | round-1 wording |
| `codec.py` | `if key == "price_cap":`; the `_Reader` key tuple without `memory`, `guard_level`, `next_check` | round-1 form of the nullable-key and decoder-key edits |

The only code added on top of the restore is the `_expected_finish` fix in `decide.py`
(UTC arithmetic, `None` on `OverflowError`), as in guide 2. `guard.py`, `regulation.py`,
`tests/test_ev_guard.py` and `tests/test_ev_regulation.py` removed. Gates after the restore:
ruff, ruff format, mypy, pytest (1141 passed) and all seven `ev` showcases green.
`STRUCTURE.md`: `ev/` and `tests/test_ev_*` sections replaced with `982a494`'s text, guard
and regulation sections removed, expected-finish fix noted in the `decide.py` entry; the
hooks, `.claude/` and plans sections kept as on `main`. `DEVELOPMENT.md`: round 2 notes
section deleted, AppDaemon entry rewritten. No test-file additions yet (T2-T5 are step 5's).

---

## 4. Verification log

> Written in step 4: the static half. Command output, not a summary of it.

| Check | Result |
|---|---|
| `ruff check .` | `All checks passed!` |
| `ruff format --check .` | `54 files already formatted` |
| `mypy` | `Success: no issues found in 24 source files` |
| `pytest` | `1141 passed in 11.12s` |
| Plan completeness | Checked against `982a494`, not `01-decision-core.md`. `git diff 982a494 -- src/energy_optimization/ev/` is empty for `__init__`, `model`, `result`, `quarters`, `planning`, `allocation`, `codec`; `decide.py` differs only in `_expected_finish` (UTC arithmetic, `None` on `OverflowError`), so every signature matches. Missing: none. Deviation: none. Unplanned: none (no public name added) |
| `STRUCTURE.md` | `structure-auditor` found 3 items. (1) `test_ev_decide.py` entry now names both `hypothesis` properties: applied. (2) `result.py` entry now mentions the tuple normalisation in `ChargerDecision` and `Decision`: applied. (3) `STEP_NAMES` / `STEP_SKILLS` undocumented in the hooks sections: rebutted, not applied; the hook sections are kept as on `main`, hooks are outside this round, and the gap pre-dates it |
| `python -m energy_optimization.ev.<module>` | `model`, `result`, `quarters`, `planning`, `allocation`, `decide`, `codec` all exit 0 and print a readable showcase; the `RuntimeWarning` about `sys.modules` is the expected re-export one |

---|---|
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
| T1 | `test_the_package_exports_exactly_round_ones_names`, `test_the_round_two_modules_are_gone`, `test_the_round_two_names_are_gone`, `test_the_dataclass_fields_are_round_ones`, `test_the_planner_and_capacity_signatures_are_round_ones`, `test_the_enums_carry_no_round_two_members` (all in `test_ev_decide.py`; the enum member sets are also pinned by the restored `test_ev_result.py`) | green |
| T2 | the restored round-1 suite, unchanged expectations; `git diff 982a494 origin/main` on the other test files shows only memory-specific showcase assertions and memory/guard tests, nothing memory-free to port | green (1221 passed after the A3 send-back round) |
| T3 (and A6) | `test_a_planned_finish_adds_its_quarter_in_utc_on_the_fall_back_night` (2 rows, red before the fix), `test_a_planned_finish_at_the_spring_forward_gap_is_rendered_as_the_real_local_time` (1, added at the step-6 send-back), `test_an_unplanned_expected_finish_adds_the_hours_in_utc_across_dst` (6 rows, the sixth added at the step-6 send-back), `test_the_unplanned_finish_off_a_wall_clock_now_matches_its_utc_instant`, `test_the_unplanned_finish_is_the_same_instant_whatever_zone_now_is_in` (3), `test_reaches_target_compares_instants_across_the_fold` (2), `test_a_trickle_of_current_gives_no_finish_instead_of_overflowing` (5), `test_a_reserved_charger_with_a_tiny_measured_draw_gives_no_finish` (2), `test_a_trickle_decision_survives_the_codec_round_trip` (2) | green; 21 of 216 tests in the file fail on a scratch copy of `982a494`, all of them these (19 of 214 before the send-back, as step 6 counted, plus the 2 new rows) |
| T4 | `test_snapshot_from_dict_rejects_memory_naming_the_field` (3), `test_snapshot_from_dict_rejects_each_round_2_setting` (16 rows, 15 keys), `test_snapshot_from_dict_names_the_top_level_key_before_a_nested_one`, `test_snapshot_from_dict_names_an_unknown_key_before_a_missing_one`, `test_decision_from_dict_rejects_round_2_keys` (6), `test_snapshot_to_dict_has_exactly_round_1_keys`, `test_decision_to_dict_has_exactly_round_1_keys`, `test_a_real_decisions_keys_are_round_1_too` (all in `test_ev_codec.py`) | green |
| T5 | `assert_plans_fit` in the `decide` invariant oracle (UTC-keyed), plus the `reaches_target is None` iff `deadline is None` invariant; `test_plan_never_books_more_than_a_phase_holds_on_mixed_phases` in `test_ev_planning.py`; `test_plan_rejects_a_naive_deadline`, `test_plan_rejects_an_unknown_timezone`, `test_plan_with_no_requests_never_looks_at_a_naive_now` | green |
| T6 | the restored determinism-across-hash-seeds and AST-scan tests, plus `test_a_decision_does_not_depend_on_the_snapshots_decided_before_it` (interleaved snapshots) | green |

**Finding, fixed after the halt (A3 widened, A6 added):** the planned branch of
`_expected_finish` did wall-clock arithmetic, `(quarter.start + QUARTER).astimezone(ctx.zone)`,
on a quarter start stamped in the settings zone. Now `10-24T22:00Z`, one 16 A adjustable
charger, 32 quarters at 0.30 with two cheap ones, `energy_need_kwh=1.84`.

Tests: `test_a_planned_finish_adds_its_quarter_in_utc_on_the_fall_back_night` (2 rows: D1,
cheap at 00:45Z+01:00Z, finish 01:15Z = `2026-10-25T02:15:00+01:00`; D2, cheap at
00:30Z+00:45Z, finish 01:00Z = `2026-10-25T02:00:00+01:00`), asserted through
`astimezone(UTC)`/`isoformat()`, also that the finish is after the first planned quarter and
`reaches_target` is true. Red run before the fix:

```
FAILED ...[d1-ends-in-the-repeated-hour]
E  AssertionError: assert '2026-10-25T00:15:00+00:00' == '2026-10-25T01:15:00+00:00'
FAILED ...[d2-ends-at-the-repeated-hour]
E  AssertionError: assert '2026-10-25T02:00:00+00:00' == '2026-10-25T01:00:00+00:00'
2 failed, 212 deselected
```

Fix: `(quarter.start.astimezone(UTC) + QUARTER).astimezone(ctx.zone)`; green: `2 passed`.
Invariant restored (contract D6): in `assert_invariants`, a non-empty plan with a
non-`None` `expected_finish` has `expected_finish.astimezone(UTC) > plan[0].start.astimezone(UTC)`.
The generated-snapshot property did not fail on the old code (hypothesis does not reach the
fold with a plan ending there), so D1/D2 are what pin the defect; the invariant guards the
rest.

**Step-6 send-back (A3 pin gap), 2026-10-22.** Two rows added so every difference shape in
the differential is pinned; production code untouched:

- `test_a_planned_finish_at_the_spring_forward_gap_is_rendered_as_the_real_local_time`:
  `now` 2026-03-28T22:00Z, 32 quarters at 0.30 with 00:30Z and 00:45Z at 0.01, need 1.84 kWh;
  plan ends at the 01:00Z transition, expected finish `2026-03-29T03:00:00+02:00` via
  `isoformat()` (old: the nonexistent `02:00:00+01:00`).
- `test_an_unplanned_expected_finish_adds_the_hours_in_utc_across_dst`, new row
  `now=2026-03-29T01:30 Europe/Copenhagen`, 0.5 h: finish is the transition,
  `2026-03-29T03:00:00+02:00` (same instant as the old sum, which renders the nonexistent
  `02:00:00+01:00`). The `hours` parameter is now typed `float`.

Red on `982a494` (the repo's current `tests/test_ev_decide.py` against `982a494`'s `src`, scratch copy `redrun`):

```
FAILED tests/test_ev_decide.py::test_an_unplanned_expected_finish_adds_the_hours_in_utc_across_dst[now5-0.5-2026-03-29T03:00:00+02:00]
E  AssertionError: assert '2026-03-29T02:00:00+01:00' == '2026-03-29T03:00:00+02:00'
FAILED tests/test_ev_decide.py::test_a_planned_finish_at_the_spring_forward_gap_is_rendered_as_the_real_local_time
E  AssertionError: assert '2026-03-29T02:00:00+01:00' == '2026-03-29T03:00:00+02:00'
2 failed, 214 deselected        (whole file on 982a494: 21 failed, 195 passed)
```

Green on the current tree: `7 passed, 209 deselected` for the same `-k` selection (both new
rows included); whole suite `1221 passed`, ruff, ruff format, mypy clean. The two earlier
differential rows marked "not pinned" in section 6 are now pinned by these two tests.

**A6 sweep** of `src/energy_optimization/ev/*.py` (every arithmetic, comparison, sort key
and dict key on a datetime):

| Site | Verdict |
|---|---|
| `decide.py` `_classify`: `next_occurrence(..., plugged_in_at or ctx.now, ...)` | ok: `next_occurrence` converts `after` to UTC first |
| `decide.py` `_classify`: `item.deadline <= ctx.now.astimezone(UTC)` | ok: UTC both sides |
| `decide.py` `_rank_key`: deadline (UTC from `next_occurrence`), `datetime.max` in UTC | ok |
| `decide.py` `_plan_requests`: passes UTC deadline | ok |
| `decide.py` `_expected_finish` planned branch | violation, fixed above (D1/D2) |
| `decide.py` `_expected_finish` unplanned branch, `need <= _EPS` | ok: `now.astimezone(zone)` is output only |
| `decide.py` `_expected_finish` unplanned, `now.astimezone(UTC) + timedelta` | ok: UTC, then presented |
| `decide.py` `decide`: `floor_to_quarter(now)` vs `q.start.astimezone(UTC)` | ok: UTC both sides |
| `decide.py` `decide`: `deadline=item.deadline.astimezone(ctx.zone)`, event `deadline` text | ok: output presentation only |
| `decide.py` `_reaches`: `item.deadline > now` | ok: UTC both sides |
| `decide.py` `_reaches`: `finish <= item.deadline` | hardened: `finish` was a settings-zone value compared with a UTC one (different tzinfo, so correct); now `finish.astimezone(UTC) <= item.deadline` so no comparison involves a `ZoneInfo` value. No behaviour change, so no red test (the fold cases are covered by `test_reaches_target_compares_instants_across_the_fold`, green before and after) |
| `planning.py` `plan_sessions`: `quarter_starts(now, deadline)`; `remaining` keyed by UTC starts; urgency key `deadline.astimezone(UTC)` | ok |
| `planning.py` `_blocks`: `slots[a+1].start - slots[a].start == QUARTER` | ok: slot starts are UTC horizon values |
| `planning.py` `_select`, `_best_block`, `_cheapest_key`, `_latest_key`: `min`/`max`/sort on `slot.start`, `.timestamp()` | ok: UTC values (instants) |
| `planning.py` `_plan_one`: `sorted(indices)`; `slot.start.astimezone(zone)` into `PlannedQuarter` | ok: ordering by index, conversion is output |
| `allocation.py` `measurements_fresh`: `now.astimezone(UTC) - measured_at.astimezone(UTC)` | ok |
| `quarters.py` `floor_to_quarter`, `_utc`, `quarter_starts`, `quarter_prices`, `prices_fresh` | ok: convert to UTC before any arithmetic or comparison |
| `quarters.py` `next_occurrence`: candidates, `candidate > after_utc`, `second != candidates[0]` | ok: UTC; the one wall-time compare, `second.astimezone(zone).replace(tzinfo=None) == naive`, is the gap/fold detector and needs the local wall clock by design, not instant arithmetic |
| `model.py` `PriceInterval`, `PriceSeries`: `end <= start`, quarter-boundary check, overlap sort and compare | ok: all via `.astimezone(UTC)` |
| `model.py` `Snapshot`, `PhaseCurrents`; `codec.py` | ok: no datetime arithmetic or comparison; codec only parses and `isoformat()`s |
| `planning.py`/`decide.py`/`quarters.py`/`allocation.py` `main()` showcases | ok: built in UTC; presentation only |

No other violation found.

Contradictions and suspicions from the designers, pinned or noted for step 8 (behaviour
unchanged):

- C2 `ChargerDecision` docstring says `remaining_need_kwh`, `deadline`, `reaches_target`
  are `None` "without a session", but a session on a charger with no car also gives
  `None` (`NO_CAR` returns before they are computed). Docstring is incomplete.
- C3 `reaches_target` is `False` for a need already met once the deadline has passed
  (`_reaches` requires `deadline > now`), so a finished session reads "will not reach
  target" after its ready time. Not pinned by a test; for step 8.
- C4 `plan_sessions` documents `ValueError` for a naive `now`, but with no requests it
  returns `{}` (pinned by `test_plan_with_no_requests_never_looks_at_a_naive_now`).
- Fixed or unavailable chargers with a session get `expected_finish` from their
  reservation, an assumed level rather than delivery (round-1 behaviour).
- `_expected_finish` docstring does not say it returns `None` for a trickle (only
  `STRUCTURE.md` does).

Edge cases considered and deliberately skipped, with reasons:

- The `need <= 1e-9` boundary for the finish (input-space case 6): low value, covered by
  the existing covered-need tests.
- Contract D7 (finish exactly at the deadline) and D8 (covered need after the ready
  time): the first needs a long hand-built price series for a `<=` already covered by the
  restored suite; the second is C3 above and is left for step 8.
- Text, very long strings, purity of the codec and the planner: covered by the restored
  suite.

---

## 6. Concept check

> Written in step 6, against section 1 — not against section 2. The question is whether
> the thing built is the thing agreed, not whether it matches the plan.

| # | Criterion | Met | Evidence |
|---|---|---|---|
| A1 | Stateless; public surface equals round 1's | yes | `git diff 982a494 -- src` is `decide.py` only (13+/3-, `_expected_finish`, `_reaches`); `model`/`result`/`planning`/`allocation`/`codec`/`__init__`/`quarters` are byte-identical to round 1, so no memory type, round-2 setting or enum member exists; `grep -rnE "settling\|skip_current\|memory" src` matches only stale `__pycache__` binaries. Pinned by `test_the_package_exports_exactly_round_ones_names`, `test_the_dataclass_fields_are_round_ones`, `test_the_planner_and_capacity_signatures_are_round_ones`, `test_the_enums_carry_no_round_two_members`, `test_a_decision_does_not_depend_on_the_snapshots_decided_before_it`; differential 2 (below) shows `decide` agrees with the round-2 code wherever its memory rules do nothing |
| A2 | Round 2 removed cleanly | yes | `git ls-files` has no `guard.py`, `regulation.py`, `test_ev_guard.py`, `test_ev_regulation.py` under `src/` or `tests/`; no `settling`, `first`, `skip_current` (signature test above); `STRUCTURE.md` reconciled by the `structure-auditor` (24 `.py` files, every entry has a file and every file an entry, signatures match); `test_the_round_two_modules_are_gone`, `test_the_round_two_names_are_gone` |
| A3 | Behaves like round 1, except listed fixes, each pinned | yes | Differential 1 over 6000 generated snapshots: every difference is `expected_finish` (and `reaches_target` where it follows), none elsewhere (0 non-finish diffs), new code equals an independent UTC oracle in all of them. Every difference shape is now pinned by a named test that is red on `982a494` and green now (table below), including seed 4135's planned spring-forward shape and the 65 same-instant unplanned gap cases. Production code unchanged since the first step-6 run (`git diff 785e1ae -- src` empty); only tests were added |
| A4 | Every round-1 guarantee holds; round-1 suite passes unchanged | yes | `git diff 982a494 -- tests` removes exactly three lines, all import lines rewritten to carry extra names (`from datetime import ...`, `from ...planning import energy_per_quarter_kwh`, `from zoneinfo import ZoneInfo`); everything else is additions (+799/-6 across `test_ev_decide.py`, `test_ev_planning.py`, `test_ev_codec.py`); no warm-memory helper in the tree. Re-run now: `pytest` 1221 passed, ruff, ruff format (54 files) and mypy (24 files) clean; all seven `python -m energy_optimization.ev.<module>` showcases exit 0 |
| A5 | Round-1-shaped codec | yes | `codec.py` byte-identical to `982a494`; `test_snapshot_to_dict_has_exactly_round_1_keys`, `test_decision_to_dict_has_exactly_round_1_keys`, `test_a_real_decisions_keys_are_round_1_too`, `test_snapshot_from_dict_rejects_memory_naming_the_field` (3), `test_snapshot_from_dict_rejects_each_round_2_setting` (16), `test_decision_from_dict_rejects_round_2_keys` (6), all green |
| A6 | All time arithmetic in UTC; outputs presented in the settings zone | yes | Sweep re-read against the code (`decide.py` `_expected_finish` planned and unplanned, `_reaches`, `decide`, `_classify`; `planning.py`; `allocation.py`; `quarters.py`; `model.py`): every addition, subtraction, comparison and dict key is on `.astimezone(UTC)` values; the one wall-time comparison is `next_occurrence`'s gap/fold detector, by design. Proven across both 2026 Copenhagen nights by the tests in the table below, including finishes in the repeated hour (D1, D2) and at the spring-forward transition |

**Differential 1, against `982a494`** (scratch copy via `git archive` under `.../scratchpad/old1`, never in the tree; driver `drv/gen.py` builds 6000 snapshots from the suite's own `random_snapshot`, a third with `now` re-expressed as `ZoneInfo("Europe/Copenhagen")`; compared as `decision_to_dict` JSON). Two runs: (a) pickled snapshots with ZoneInfo `now`: 1276 of 6000 decisions differ; (b) the same snapshots through the dict codec (fixed-offset `now`): 11 differ. No run raised in either version. Re-run and re-counted at this second step-6 run from the saved results; every difference is `chargers[].expected_finish`, plus `reaches_target` on 52 in (a) (19 true to false, 33 false to true) which follows from the finish. `drv/oracle.py` (`now.astimezone(UTC) + hours` for unplanned, last planned quarter start in UTC plus 15 min for planned): new code equals the oracle in all 1632 instant-differing cases (1570 + 52 with `reaches_target` changed + 10 planned), `bad []`. Per charger, with the test that pins each shape (the pinning tests are red on `982a494`, green now, see the run below):

| Shape | (a) | (b) | Pinned by |
|---|---|---|---|
| unplanned, instant differs, fall-back night | 1099 | 0 | `test_an_unplanned_expected_finish_adds_the_hours_in_utc_across_dst` (rows 1 to 3), `test_the_unplanned_finish_is_the_same_instant_whatever_zone_now_is_in`, `test_reaches_target_compares_instants_across_the_fold` (2) |
| unplanned, instant differs, spring-forward night | 421 | 0 | same test, rows 4 and 5 (`now` before and inside the gap) |
| unplanned, instant differs, finish on another day (the sum crosses a transition earlier) | 102 | 0 | same tests: the mechanism is the same wall-clock sum, rows 1, 4 and `test_the_unplanned_finish_off_a_wall_clock_now_matches_its_utc_instant` |
| unplanned, same instant, old renders a nonexistent gap time (`02:xx+01:00`, new `03:xx+02:00`) | 65 | 0 | `test_an_unplanned_expected_finish_adds_the_hours_in_utc_across_dst` row 6 (`now` 01:30 local, 0.5 h, finish is the transition: old `02:00:00+01:00`, new `03:00:00+02:00`). All 65 are on 2026-03-29 with the same `+01:00` to `+02:00` rendering change |
| planned, fall-back night (instant differs) | 10 | 10 | `test_a_planned_finish_adds_its_quarter_in_utc_on_the_fall_back_night` (D1 and D2) |
| planned, same instant, old renders a nonexistent gap time (seed 4135) | 1 | 1 | `test_a_planned_finish_at_the_spring_forward_gap_is_rendered_as_the_real_local_time` (plan ends at the 01:00Z transition: old `02:00:00+01:00`, new `03:00:00+02:00`, identical to seed 4135's 02:00 to 03:00) |

The `OverflowError` to `None` fix did not occur in the generated snapshots; it is pinned by `test_a_trickle_of_current_gives_no_finish_instead_of_overflowing` (5), `test_a_reserved_charger_with_a_tiny_measured_draw_gives_no_finish` (2), `test_a_trickle_decision_survives_the_codec_round_trip` (2).

**Red on `982a494`, green now (re-run at this step):** the repo's current `tests/test_ev_decide.py` copied into scratch `.../scratchpad/redrun` beside `982a494`'s `src` (verified byte-identical `decide.py`): `21 failed, 195 passed` (216 tests). The 21 are exactly: unplanned across DST (5 rows, including the new gap row), wall-clock `now` (1), same-instant whatever zone (1), `reaches_target` fold (2), planned fall-back D1 and D2 (2), planned spring-forward gap (1, new), trickle (5), tiny measured draw (2), codec round trip of a trickle (2). On the current tree the same file: `216 passed`; whole suite `1221 passed`. Section 5's 21 of 216 matches.

Drift found, and what was done about it:

- **Out of scope**: nothing built: no solver, scipy or numpy (`test_the_package_imports_only_the_standard_library_and_itself` green), no timing or command-handling code.
- **Surface**: no public name added; `STRUCTURE.md` matches the code.
- **Showcase**: all seven `ev` showcases run and read as round 1's.
- **Structure**: the `structure-auditor` found `STRUCTURE.md` in sync, with one optional wording edit: name the planned-finish tests in the `test_ev_decide.py` entry. Rebutted, not applied: the entry (lines 268 to 272) already says "a planned expected finish ending in or at the repeated hour of the fall-back night and at the spring-forward transition", and "an unplanned finish landing exactly on the spring-forward transition", added by step 5 at the send-back; the auditor read an older wording.
- **Gap (A3), closed**: the first step-6 run sent A3 back to step 5 for two unpinned shapes (planned and unplanned spring-forward gap rendering). Step 5 added a row and a test (commit `785e1ae`), tests only. Both are red on `982a494` and green now, so the same criterion does not come back unmet and the build does not halt.

### Earlier rounds still hold

> Later rounds only. Re-check every acceptance criterion from every earlier round in this
> folder: this round changed code they depend on, and their tests passing is necessary but
> not sufficient — a criterion can be satisfied by tests that no longer describe what the
> feature does.

The earlier round is `feat/ev-charging-core` round 1 (`development/feat/ev-charging-core/01-decision-core.md`, section 1, read only). All of `src/` except `decide.py` is byte-identical to its head `982a494`, and `decide.py` differs only in the finish arithmetic, so each criterion holds by identity plus the restored suite, which passes unchanged.

| Round | # | Criterion | Still met | Evidence |
|---|---|---|---|---|
| ev-charging-core 1 | A1 | Rejects invalid configuration naming the field | yes | `model.py`, `codec.py` identical to `982a494`; `tests/test_ev_model.py` identical, green; snapshot cross-links and naive timestamps covered there |
| ev-charging-core 1 | A2 | Priorities 1 to 4, tiers, ranking, split, waiting for capacity | yes | `decide.py` identical outside the finish; `test_ev_decide.py` doc-scenario tests green, unchanged; both differentials show equal allocations, reasons and priorities |
| ev-charging-core 1 | A3 | Respects amp limits (budget, phase headroom, mixed phases, on/off, reserved, smart-off) | yes | `allocation.py` identical; `test_ev_allocation.py` identical, green; `assert_invariants` in the generated-snapshot property checks budget and headroom independently; differentials equal in allocation |
| ev-charging-core 1 | A4 | Plans the cheapest quarters before the ready time (92/100-quarter days, blocks, cap, unreachable, unpriced) | yes | `planning.py`, `quarters.py` identical; `test_ev_planning.py`, `test_ev_quarters.py` green with additions only; plan-fit oracle `assert_plans_fit` added and green |
| ev-charging-core 1 | A5 | Falls back under doubt (stale measurements, stale prices) | yes | the fallback table tests in `test_ev_decide.py` green; both differentials equal on 6000 snapshots including stale ones (guard `None` cases) |
| ev-charging-core 1 | A6 | Explainable, ordered output (down before up, absolute targets, reasons, plan, expected finish, `reaches_target`) | yes | `test_ev_decide.py` actions-order and reason tests; `assert_invariants` checks the action set and stages; the expected finish is the intentionally changed field (A3/A6 here) |
| ev-charging-core 1 | A7 | Doc section 12 invariants under generated input | yes | `test_the_invariants_hold_for_generated_snapshots` (300 examples) green; the round-trip property (60) green |
| ev-charging-core 1 | A8 | Deterministic and standard-library only | yes | `test_the_output_does_not_depend_on_the_hash_seed`, `test_the_package_imports_only_the_standard_library_and_itself` green |
| ev-charging-core 1 | A9 | Round-trips plain dicts, rejects bad fields naming them | yes | `codec.py` identical; `test_ev_codec.py` green; the 6000-snapshot differentials went through the dict codec on both sides without error |
| ev-charging-core 1 | A10 | Under 100 ms for 10 chargers and 48 h | yes | `test_ten_chargers_and_a_48_hour_series_decide_in_under_100_ms` green in the full run (11.3 s total suite) |

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

### Step 5, 2026-10-22

Halted by the test step at the end of step 5, with the suite written and green (1217
passed, ruff, ruff format and mypy clean) and committed. Reason: the planned branch of
`_expected_finish` (D1/D2 above) is a round-1 defect in shared code that A3 does not list.
Fixing it would change behaviour against `982a494`, which amends section 1.

Question for the user: widen A3 to also allow this one-line fix,
`(quarter.start.astimezone(UTC) + QUARTER).astimezone(ctx.zone)`, with the two reproduction
cases added as tests (red on the current code, green after), or leave it as it is and note
it in `DEVELOPMENT.md` for a later `/fix`?

**Answer (user, 2026-10-22):** "yes, in general the solver should use UTC, and convert any
input to this". Section 1 amended: A3 now also allows the A6 corrections, and a new A6
requires all time arithmetic in UTC with every aware input converted first (outputs still in
the settings timezone). Round 2's out-of-scope entry carries the same rule into the solver.
Step 5 resumes: apply the planned-branch fix, add D1/D2 as tests red before and green after,
restore the skipped `expected_finish > plan[0].start` invariant, and sweep the package for
any other arithmetic or comparison on a zone-aware datetime.

Resumed 2026-10-22: the planned-branch fix, D1/D2 tests, restored finish-after-plan-start
invariant and A6 sweep are done (section 5); `STRUCTURE.md` updated; gates green (1219
passed). Step 5 closed.
