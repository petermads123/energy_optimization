# Devlog

One entry per version, newest first: what changed, then the files it touched. `/finish`
writes each entry in the commit that opens the pull request.

## 0.1.1 — 2026-11-09

Replaces the ten-step plan-file pipeline with the one from `petermads123/template`: features
are agreed in `/feature`, split into rounds that each run as the `round` workflow on their own
agents, and closed by `/finish`; bugs start with `/fix` and its `diagnose` workflow. State
moves from numbered plan files to `BRIEF.md`, `brief.json` and `round.json` per feature. A
round runs only the tests its change can affect (`select_tests.py`); CI runs the whole suite,
and `check_sync.py`, on every pull request. No package code changes.

Files:
- `.claude/skills/` — `feature`, `fix`, `round`, `finish` replace the fourteen old skills
- `.claude/agents/` — the template's seventeen pipeline agents replace the five old ones
- `.claude/workflows/` — `diagnose.js`, `round.js`, `final.js`
- `.claude/hooks/` — `dev_state.py`, `check_sync.py`, `select_tests.py` added, `session_brief.py`, `guard_git.py`, `lint_py.py` updated, `plan_state.py`, `stop_gate.py` removed
- `.claude/rules/python.md`, `.claude/settings.json`, `.gitignore`
- `tests/` — `test_dev_state.py`, `test_session_brief.py`, `test_check_sync.py`, `test_select_tests.py` added, `test_plan_state.py`, `test_stop_gate.py` removed
- `.github/workflows/check.yml`
- `development/README.md` added, `development/TEMPLATE.md` removed
- `CLAUDE.md`, `STRUCTURE.md`, `README.md`, `DEVLOG.md`, `DEVELOPMENT.md`, `pyproject.toml`

Versions up to 0.1.0 predate this log; their history is in git and under `development/`.
