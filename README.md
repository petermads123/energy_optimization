# energy_optimization

Energy optimization for a household: pure-Python decision cores that take one complete
snapshot and return what to do now. Built to be called from a Home Assistant / AppDaemon
app, which gathers the input and executes the actions.

**Version:** 0.1.2

## What is in the package

- `energy_optimization.ev` decides when, who and at how many amps the EV chargers behind
  one shared grid connection may charge, within per-phase safety limits.
- `energy_optimization.interruptible` plans interruptible loads (a fridge, a water heater,
  a pump) that may be switched off for bounded periods: off in the expensive quarters, on
  in the rest, within a maximum off run, a share of off time per rolling window, a
  minimum on time after an off period and a minimum off block.
- `energy_optimization.common` is what both share: the price types, `ConfigError`,
  `SolverError` and the quarter-hour grid helpers.

## Install as a dependency

```powershell
pip install git+https://github.com/petermads123/energy_optimization.git@main
```

Or in `dependencies` in `pyproject.toml`:

```toml
"energy_optimization @ git+https://github.com/petermads123/energy_optimization.git@main"
```

## Layout

### Why `src/`

Everything installable lives under `src/`, so nothing else in the repo — `tests/`, `docs/`,
a stray script — can be picked up as a package by accident, and an import in a test resolves
against the installed package rather than against whatever happens to sit in the working
directory.

The trade-off: the repo root is not on `sys.path`, so `python -m <package>.<module>` only
works once you have run `pip install -e ".[dev]"`. `pytest` is unaffected — `pythonpath`
in `pyproject.toml` points it at `src`.

Avoid naming the package folder `lib`, `build`, `dist` or `sdist`: the `.gitignore`
inherited from GitHub's Python template ignores those, so the folder would be silently
untracked even under `src/`.

## Development

### Prerequisites

All installable from PowerShell — no website visits needed:

```powershell
winget install --id Git.Git --source winget
winget install --id Python.Python.3.13 --source winget
code --install-extension ms-python.python
code --install-extension charliermarsh.ruff
```

The Python version must satisfy `requires-python` in `pyproject.toml`. To install a
different one, replace the version in the package id, e.g. `Python.Python.3.14`.

`winget` does not update the PATH of the shell it ran in. Open a new terminal before
continuing, then check with `python --version`.

### Create the environment

From the repo root:

```powershell
python -m venv .venv
```

```powershell
.\.venv\Scripts\Activate.ps1
```

```powershell
python -m pip install --upgrade pip
```

```powershell
pip install -e ".[dev]"
```

The `[dev]` part installs Ruff, mypy and pytest. Without it you get the package only.

In VS Code the environment activates automatically in new terminals once the interpreter is
selected. If it is not picked up, use `CTRL + Shift + P` -> `Python: Select Interpreter` and
choose the one in `.venv`.

### Verify

All four must pass on a fresh clone:

```powershell
ruff check .
```

```powershell
ruff format --check .
```

```powershell
mypy
```

```powershell
pytest
```

### Editor

`.vscode/settings.json` is checked in, so format-on-save, import sorting and Ruff as the
Python formatter are already configured for this repo. Installing the Ruff extension (see
Prerequisites) is all that is required.

## Working with Claude Code

This repo ships a Claude Code configuration under `.claude/`, plus `CLAUDE.md` (the routing
map, loaded every session) and `STRUCTURE.md` (every module and public signature, imported by
`CLAUDE.md`). The pipeline is the one from `petermads123/template`; `CLAUDE.md` explains it.

Ask for a feature in plain words, or `/feature <what to build>`; report a bug the same way,
or `/fix <symptom>`. Claude routes it and says which route in one line.

| Stage | Skill | What happens | Waits for you |
|---|---|---|---|
| Concept | `/feature` | The concept, the branch name and the version bump are agreed; repo scouts investigate in the background; `BRIEF.md` splits the work into rounds | the concept and the rounds |
| Diagnosis (a bug) | `/fix` | The symptom reproduced as a failing test, causes hunted from three angles, one root cause proven and criticised | the cause, the scope and the bump |
| Each round | `/round` | Conceptualize, plan, implement, verify with parallel reviewers, four test designers, write and run the tests, three concept checkers, merge into the feature branch | only a question the round cannot answer |
| Finish | `/finish` | Whole-feature check, extras, the version, `STRUCTURE.md`, `README.md` and `DEVLOG.md` written, the pull request opened and its CI seen through | only a critical extra |

State lives under `development/<branch>/` (`BRIEF.md`, `brief.json`, one `r<n>-<slug>/`
folder per round) and is committed after every step, so a session can be closed and resumed.
`development/README.md` documents the JSON files; the older numbered `NN-*.md` plan files
there are the history of the previous pipeline.

Claude never merges on its own judgment or on an approval alone: a pull request reaches
`main` because you pressed the button or told Claude to merge it. Every version is recorded
in `DEVLOG.md`.

### Hooks and CI

- **Session start**: reports a feature in flight and the skill that resumes it.
- **Before any shell command**: refuses a `git commit` or `git push` that would land on `main`.
- **After every `.py` write**: Ruff formats and auto-fixes the file.
- **CI** (`.github/workflows/check.yml`): on every pull request and push to `main`, runs the
  four checks above and `python .claude/hooks/check_sync.py`, which checks that the version,
  `README.md`, `DEVLOG.md` and `STRUCTURE.md` agree with each other and the code.

Hooks are read at session start, so a change under `.claude/hooks/` or to
`.claude/settings.json` needs a Claude Code restart, and the first session asks for
workspace trust. The hooks run the first of `python3` or `python` that actually starts, so
the Windows Store alias stub is skipped.

### Troubleshooting

`Activate.ps1 cannot be loaded because running scripts is disabled` — allow local scripts
for your user:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

`python` not found right after `winget install` — open a new terminal so PATH is reloaded.
