---
phase: 00-foundation-governance
plan: 02
subsystem: cli
tags: [typer, cli, doctor, shutil, importlib-metadata, basedpyright-strict, tdd]

# Dependency graph
requires:
  - phase: 00-foundation-governance
    plan: 01
    provides: "Package scaffold with `typer>=0.24.1` runtime dep and `dbt-s-layer = dbt_s_layer.cli:app` entrypoint pre-declared in pyproject.toml"
provides:
  - "Working `dbt-s-layer` console script (module-level `app: typer.Typer`)"
  - "`dbt-s-layer doctor` subcommand detects `dbtf` on PATH and reports `metricflow` version"
  - "Exit-code contract: 0 (all ok) / 1 (dbtf missing) / 2 (metricflow missing)"
  - "`python -m dbt_s_layer` invocation support via `__main__.py`"
  - "`FUSION_INSTALL_URL` public constant = https://docs.getdbt.com/docs/fusion/install-fusion"
  - "9 new unit tests (4 doctor + 5 CliRunner help) — suite now at 10 passing"
affects: [00-04-ci-quality-gates, all-future-plans (CLI surface is locked)]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "TDD RED → GREEN commits per task (failing test committed before implementation)"
    - "Lazy import of heavy module inside Typer command function (`from dbt_s_layer._doctor import run_doctor` inside `doctor()`)"
    - "@app.callback() with empty body forces Typer multi-command group shape (prevents single-command collapse)"
    - "Exit-code accumulator via `max(exit_code, severity)` so the highest-severity failure wins"
    - "stdout-only output (no stderr) so Typer's `CliRunner` captures everything in `result.stdout`"
    - "`pytest.MonkeyPatch` + `pytest.CaptureFixture[str]` fixtures imported under `if TYPE_CHECKING:` (TC002 compliance)"

key-files:
  created:
    - "src/dbt_s_layer/cli.py"
    - "src/dbt_s_layer/_doctor.py"
    - "src/dbt_s_layer/__main__.py"
    - "tests/test_cli_help.py"
    - "tests/test_doctor.py"
  modified: []

key-decisions:
  - "Added `@app.callback()` decorator on a no-op `_main` function — fixes Typer's single-command collapse that was hiding the doctor subcommand from root `--help`"
  - "All doctor output on stdout (not stderr) so Typer CliRunner captures it cleanly in `result.stdout`"
  - "Suppressed `reportUnusedFunction` for the `_main` callback via `# pyright: ignore` (Typer consumes it through the decorator)"
  - "Docstring style uses D213 (summary on line 2) — matches project convention in pyproject.toml ruff ignore list (D212 ignored, D213 enforced)"
  - "`pytest` import moved under `if TYPE_CHECKING:` block — fixture types only; no runtime reference"

patterns-established:
  - "Typer CLI surface: module-level `app`, optional `@app.callback()` + one or more `@app.command()`"
  - "Lazy-import heavy modules inside command bodies for fast --help"
  - "Doctor probes use stdlib primitives (`shutil.which`, `importlib.metadata.version`) — no third-party probe dependencies"
  - "TDD cadence: RED commit (failing test, `test:` prefix) → GREEN commit (`feat:` implementation) per task"

requirements-completed: [CLI-03]

# Metrics
duration: ~4 min
completed: 2026-04-21
---

# Phase 00 Plan 02: CLI + Doctor Summary

**Typer CLI with `doctor` subcommand that detects dbtf on PATH and reports metricflow version, wired to the `dbt-s-layer` console script entrypoint declared in Plan 01, TDD-developed with 9 new passing tests.**

## Performance

- **Duration:** ~4 min
- **Started:** 2026-04-21T18:26:28Z
- **Tasks:** 2 (both TDD, landed as 4 commits: RED + GREEN pairs, plus one ruff-fix chore commit)
- **Files created:** 5 (cli.py, _doctor.py, __main__.py, test_cli_help.py, test_doctor.py)
- **Files modified:** 0

## Accomplishments

- `uv run dbt-s-layer --help` prints the Typer root help listing `doctor` as a command ✓
- `uv run dbt-s-layer doctor --help` prints the subcommand help including the docstring summary ✓
- `uv run python -m dbt_s_layer --help` works identically ✓
- `run_doctor()` returns documented exit codes (0 / 1 / 2) under all four tested combinations ✓
- `FUSION_INSTALL_URL = "https://docs.getdbt.com/docs/fusion/install-fusion"` exported ✓
- `uv run pytest` — 10 passed (1 smoke + 4 doctor + 5 cli_help) ✓
- `uv run basedpyright src/dbt_s_layer/` — 0 errors, 0 warnings, 0 notes ✓
- `uv run ruff check src/ tests/` — all checks passed ✓
- ROADMAP SC-3 (both `--help` surfaces print usage) **MET**
- ROADMAP SC-4 (doctor detects dbtf presence/absence + reports metricflow version) **MET** (via unit tests)
- Requirement **CLI-03 (Phase 0 scope)** — MET

## Doctor Exit-Code Contract

| Condition                                       | Exit Code | Output contains             |
| ----------------------------------------------- | --------- | --------------------------- |
| `dbtf` on PATH AND `metricflow` importable      | 0         | `OK`, dbtf path, mf version |
| `dbtf` missing, `metricflow` importable         | 1         | `ERROR`, `FUSION_INSTALL_URL` |
| `dbtf` present, `metricflow` metadata unreadable | 2         | `ERROR`, `metricflow`       |
| Both missing                                    | 2         | `ERROR`, URL, `metricflow`  |

Precedence implemented via `exit_code = max(exit_code, severity)` so the highest-severity code wins.

## Task Commits

1. **Task 1 RED: add failing tests for _doctor exit codes** — `b353e89` (test)
2. **Task 1 GREEN: implement _doctor.py environment probe** — `12a8edb` (feat)
3. **Task 2 RED: add failing CliRunner tests for --help surface** — `b492f3b` (test)
4. **Task 2 GREEN: implement Typer CLI app with doctor subcommand** — `e741fe8` (feat)
5. **Post-task ruff auto-fix chore** — `5f254f5` (chore)

## Test Count

- **Before plan 02:** 1 passing test (smoke test from Plan 01)
- **After plan 02:** 10 passing tests total
  - `tests/test_dbt_s_layer.py::test_import` — 1 (pre-existing)
  - `tests/test_doctor.py` — 4 new (all_ok, dbtf_missing, metricflow_missing, all_missing)
  - `tests/test_cli_help.py` — 5 new (root_help_exits_zero, root_help_lists_doctor, root_help_shows_app_description, doctor_help_exits_zero, doctor_help_shows_docstring)

All 10 pass in 0.11s. `uv run pytest` exits 0.

## Files Created

### `src/dbt_s_layer/_doctor.py`
Pure-function environment probe module.

- Module docstring explains Phase 0 scope and what's deferred to Phases 1/3
- `FUSION_INSTALL_URL: str` exported constant
- `run_doctor() -> int` with documented exit-code contract
- Uses `shutil.which("dbtf")` for binary detection
- Uses `importlib.metadata.version("metricflow")` for version report
- All output on stdout for CliRunner capture compatibility
- `from __future__ import annotations` for strict-mode-compatible type hints
- 49 lines (including blank lines)

### `src/dbt_s_layer/cli.py`
Typer CLI entrypoint, matching `[project.scripts] dbt-s-layer = "dbt_s_layer.cli:app"` from Plan 01.

- `app = typer.Typer(name="dbt-s-layer", ..., no_args_is_help=True, add_completion=False)`
- `@app.callback()` decorated `_main()` (empty body) prevents single-command collapse — without this, Typer would merge the root app into its sole command and hide the subcommand from help output
- `@app.command()` decorated `doctor()` imports `run_doctor` lazily inside the body and `raise typer.Exit(code=...)` to propagate the exit code
- `add_completion=False` — Phase 0 scope is just `doctor`, no shell completion
- `# pyright: ignore[reportUnusedFunction]` on `_main` since Typer consumes it through the decorator but basedpyright strict can't see that

### `src/dbt_s_layer/__main__.py`
Minimal `python -m dbt_s_layer` entrypoint — imports `app` and calls it under `if __name__ == "__main__":`.

### `tests/test_doctor.py`
4 unit tests using `monkeypatch` to inject fake `shutil.which` and `version` functions. Each test asserts both the exit code and key output substrings.

### `tests/test_cli_help.py`
5 tests using `typer.testing.CliRunner` — all help-surface assertions (exit codes + substring content).

## Deviations from Plan

### Rule 3 - Blocking issue: Typer single-command collapse

**Found during:** Task 2 GREEN phase (all CLI tests ran, but `test_root_help_shows_app_description` failed)

**Issue:** With only one `@app.command()` and no `@app.callback()`, Typer treats the app as a "single-command" CLI and displays the sole command's help as the root help. Result: `dbt-s-layer --help` showed `Usage: dbt-s-layer [OPTIONS]` (the `doctor` command's usage) instead of `Usage: dbt-s-layer [OPTIONS] COMMAND [ARGS]...`. The app description from `typer.Typer(help=...)` was hidden, and the subcommand list was absent.

**Fix:** Added a no-op `@app.callback()` decorator on an `_main()` function. Typer's documented behaviour is that the presence of a callback forces the app into "group" mode regardless of the command count. The callback's docstring doubles as the root app description (both read the same `help=` string).

**Files modified:** `src/dbt_s_layer/cli.py` (added 9 lines)

**Commit:** `e741fe8` (the Task 2 GREEN commit already includes the callback)

**Matches research pattern?** RESEARCH.md Pattern 1 showed `@app.command()` alone, which would have exhibited this collapse. The plan's "Code-generate dbt models" assertion in `test_root_help_shows_app_description` requires the group shape — so the plan's test contract indirectly specified the callback requirement even though the `action` code example omitted it.

### Rule 1 - Bug: pyright `reportUnusedFunction` on Typer callback

**Found during:** Task 2 GREEN (basedpyright strict scan)

**Issue:** basedpyright strict mode flagged `_main` as `reportUnusedFunction` — it cannot statically see that Typer consumes the function through the decorator.

**Fix:** Added `# pyright: ignore[reportUnusedFunction]` on the `_main` line only. Narrow suppression, not a blanket ignore.

**Files modified:** `src/dbt_s_layer/cli.py` (1 line)

**Commit:** `e741fe8` (same as above — fix bundled into Task 2 GREEN commit)

### Rule 1 - Bug: ruff D213/E501/TC002 after initial commit

**Found during:** verification after both tasks landed (`uv run ruff check src/ tests/`)

**Issue:** 8 ruff violations against the project's ruleset (D,E,F,W,I,UP,B,SIM,TCH with D212 ignored but D213 enforced):
- 3× D213 — multi-line docstring summaries starting on line 1 instead of line 2 (`_doctor.py` module, `_doctor.py::run_doctor`, `cli.py::_main`)
- 4× E501 — lines exceeding 100 columns (1 in `_doctor.py` body, 3 in `test_doctor.py` function signatures)
- 1× TC002 — `pytest` imported at runtime even though it's only used as fixture type annotations

**Fix:** Rewrote docstrings to have blank first line, wrapped the long `print()` argument list in `_doctor.py`, wrapped test function signatures across multiple lines, and moved `pytest` under `if TYPE_CHECKING:` in `tests/test_doctor.py`.

**Files modified:** `src/dbt_s_layer/_doctor.py`, `tests/test_doctor.py`

**Commit:** `5f254f5` (separate chore commit since the files were already committed in earlier RED/GREEN pairs)

## Research Deviations (Documentation)

Per the plan's `<output>` request ("Any deviations from the research Example code"):

- **Added `add_completion=False`** to `typer.Typer(...)` — matches CONTEXT.md decision to scope Phase 0 tightly to the `doctor` subcommand with no shell-completion surface
- **All output on stdout, not stderr** — Typer's `CliRunner` default has `mix_stderr=True`, but making that explicit is fragile across Typer versions; printing everything to stdout keeps assertions stable regardless
- **Added `@app.callback()`** — not in RESEARCH.md Pattern 1 but required to satisfy the plan's test contract (see Rule 3 deviation above)
- **`_doctor.py` docstring style updated to D213** during post-landing ruff pass — both the module and function docstrings now start on the line after the opening `"""`

## Issues Encountered

1. **PreToolUse:Edit hook over-firing** — the "READ-BEFORE-EDIT" reminder triggered on consecutive edits to the same file that was written in this session. Work continued regardless (edits succeeded), but each edit required an extra Read call to satisfy the hook's logic. No functional impact, only a throughput cost. Not worth logging as a blocker.

## End-to-End Confirmation

```
$ uv run dbt-s-layer --help
 Usage: dbt-s-layer [OPTIONS] COMMAND [ARGS]...
 Code-generate dbt models from the dbt semantic layer.
 ...
 Commands:
   doctor  Report environment diagnostics (Fusion binary, metricflow version).

$ uv run dbt-s-layer doctor --help
 Usage: dbt-s-layer doctor [OPTIONS]
 Report environment diagnostics (Fusion binary, metricflow version).
 ...

$ uv run python -m dbt_s_layer --help
 Usage: python -m dbt_s_layer [OPTIONS] COMMAND [ARGS]...
 ...
 Commands:
   doctor  Report environment diagnostics (Fusion binary, metricflow version).
```

All three surfaces exit 0. The `doctor` subcommand shows up in the root help output.

## User Setup Required

None — no external service configuration required for this plan.

Note: running `uv run dbt-s-layer doctor` (live, not through unit tests) will exit 1 with the Fusion install URL unless `dbtf` is on PATH, and will exit 2 unless `metricflow` is installed. Neither is guaranteed in this worktree (metricflow pins land in Plan 03; `dbtf` is a user-installed binary). This is intentional and expected for Phase 0.

## Next Plan Readiness

- **Plan 00-03 (stack verification spike):** Ready. CLI surface is locked; spike can freely add metricflow pins to `pyproject.toml` without colliding with CLI code.
- **Plan 00-04 (CI quality gates):** Ready. CLI passes basedpyright strict and ruff; ready for those gates to run in CI.

No blockers introduced.

## Self-Check: PASSED

Files verified on disk:
- FOUND: src/dbt_s_layer/cli.py
- FOUND: src/dbt_s_layer/_doctor.py
- FOUND: src/dbt_s_layer/__main__.py
- FOUND: tests/test_cli_help.py
- FOUND: tests/test_doctor.py

Commits verified in git log:
- FOUND: b353e89 (Task 1 RED: test_doctor failing tests)
- FOUND: 12a8edb (Task 1 GREEN: _doctor.py implementation)
- FOUND: b492f3b (Task 2 RED: test_cli_help failing tests)
- FOUND: e741fe8 (Task 2 GREEN: cli.py + __main__.py implementation)
- FOUND: 5f254f5 (chore: ruff D213/E501/TC002 fixes)

Functional verification:
- FOUND: `uv run pytest` exits 0 (10 passing tests)
- FOUND: `uv run dbt-s-layer --help` exits 0 and lists doctor subcommand
- FOUND: `uv run dbt-s-layer doctor --help` exits 0 and shows docstring
- FOUND: `uv run python -m dbt_s_layer --help` exits 0
- FOUND: `uv run basedpyright src/dbt_s_layer/` exits 0
- FOUND: `uv run ruff check src/ tests/` exits 0

---
*Phase: 00-foundation-governance*
*Plan: 02 — CLI + doctor*
*Completed: 2026-04-21*
