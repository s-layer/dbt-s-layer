---
phase: 00-foundation-governance
plan: 01
subsystem: infra
tags: [python, uv, uv_build, hatchling-alternative, pep621, pep561, typer, basedpyright, ruff, pytest, apache-2.0]

# Dependency graph
requires:
  - phase: 00-foundation-governance
    provides: "Phase 0 CONTEXT locked decisions (uv_build backend, basedpyright strict, ruff ruleset, 3.11 floor)"
provides:
  - "Buildable, importable, testable Python package skeleton"
  - "pyproject.toml with uv_build backend and typer>=0.24.1 runtime dep"
  - "src/dbt_s_layer/ PEP 561 package (py.typed marker present)"
  - "project.scripts entrypoint 'dbt-s-layer = dbt_s_layer.cli:app' declared for Plan 02 consumption"
  - "uv.lock committed — enables --locked in CI for all subsequent plans"
  - "Passing smoke test (uv run pytest green on fresh clone)"
  - "Canonical Apache-2.0 LICENSE with project copyright"
affects: [00-02-cli-doctor, 00-03-stack-verification-spike, 00-04-ci-quality-gates, all-future-plans]

# Tech tracking
tech-stack:
  added: [uv, uv_build, typer, basedpyright, pytest, pytest-cov, ruff, rich (transitive)]
  patterns:
    - "src-layout with PEP 561 py.typed marker"
    - "basedpyright strict mode with reportPrivateUsage=false"
    - "ruff ruleset E,F,W,I,UP,B,SIM,TCH,D with D1/D202/D203/D212/D401/D413 ignores"
    - "Console entrypoint declared upfront, implementation deferred to Plan 02"

key-files:
  created:
    - "pyproject.toml"
    - "src/dbt_s_layer/__init__.py"
    - "src/dbt_s_layer/py.typed"
    - "tests/__init__.py"
    - "tests/conftest.py"
    - "tests/test_dbt_s_layer.py"
    - ".python-version"
    - ".gitignore"
    - "LICENSE"
    - "README.md"
    - "uv.lock"
  modified: []

key-decisions:
  - "uv_build build backend (not hatchling) — per CONTEXT.md locked decision"
  - "basedpyright strict mode (not mypy) — per CONTEXT.md locked decision"
  - "Python 3.11 floor — per CONTEXT.md locked decision"
  - "typer>=0.24.1 declared in runtime deps now so Plan 02 CLI doesn't need to touch pyproject.toml"
  - "metricflow family pins deliberately omitted — deferred to Plan 03 after stack spike"
  - "Console entrypoint 'dbt-s-layer = dbt_s_layer.cli:app' declared upfront; Plan 02 creates cli.py with module-level app: typer.Typer"
  - "LICENSE sourced from dbt_utils package (canonical Apache-2.0 text, 11402 bytes)"

patterns-established:
  - "src-layout: package under src/<name>/ not repo-root/<name>/"
  - "PEP 561: empty py.typed marker in package root advertises type information"
  - "Strict type checking enforced by basedpyright from day 0 (e.g. __all__: list[str] = [], -> None on tests)"
  - "uv.lock is committed; CI uses --locked for reproducibility"

requirements-completed: []

# Metrics
duration: ~5min
completed: 2026-04-21
---

# Phase 00 Plan 01: Package Scaffold Summary

**Buildable, importable Python package with uv_build backend, typer entrypoint declared for Plan 02, and passing pytest smoke test on Python 3.11.**

## Performance

- **Duration:** ~5 min
- **Started:** 2026-04-21T18:19:00Z (approx)
- **Completed:** 2026-04-21T18:23:31Z
- **Tasks:** 2 (both committed atomically)
- **Files created:** 11 (8 in Task 1, 3 in Task 2)
- **Files modified:** 0

## Accomplishments

- Package scaffold complete: `pyproject.toml`, `src/dbt_s_layer/` with `py.typed`, `tests/` with passing smoke test, `.python-version`, `.gitignore`, canonical Apache-2.0 `LICENSE`, `README.md`, committed `uv.lock`
- `uv sync --locked --dev` succeeds → reproducible installs guaranteed for Plans 02/03/04
- `import dbt_s_layer` works; `dbt_s_layer.__all__ == []` asserted by smoke test
- `uv build` produces wheel including `dbt_s_layer/py.typed` (verified locally)
- Phase 0 ROADMAP SC-1 (`uv run pytest` passes on fresh clone) **MET**
- `dbt-s-layer = "dbt_s_layer.cli:app"` console entrypoint declared — Plan 02 must create `src/dbt_s_layer/cli.py` exporting a module-level `app: typer.Typer`

## Task Commits

1. **Task 1: Create pyproject.toml, src package, and supporting files** — `3fa5c87` (feat)
2. **Task 2: Create tests/ scaffold with smoke test** — `d2cd8b3` (test)

## Files Created/Modified

### Task 1
- `pyproject.toml` — project metadata, uv_build backend, typer runtime dep, dev deps (basedpyright/pytest/pytest-cov/ruff), `[project.scripts]`, `[tool.pyright]` strict mode, `[tool.ruff]` with D213 docstring rules, `[tool.pytest.ini_options]`
- `src/dbt_s_layer/__init__.py` — one-line docstring and `__all__: list[str] = []` (explicit annotation required for strict mode)
- `src/dbt_s_layer/py.typed` — empty (PEP 561 marker)
- `.python-version` — `3.11`
- `.gitignore` — Python + uv + build artifacts; `.venv/`, `__pycache__/`, `dist/`, `build/`, coverage outputs, IDE, OS noise
- `LICENSE` — canonical Apache-2.0 (202 lines, 11402 bytes; sourced from dbt_utils package; copyright updated to `paul <ego@anentropic.com>` / `The dbt-s-layer Contributors`)
- `README.md` — project overview, status, requirements, license reference
- `uv.lock` — 20 packages resolved; typer 0.24.1 landed

### Task 2
- `tests/__init__.py` — empty (makes `tests` a package)
- `tests/conftest.py` — fixture placeholder
- `tests/test_dbt_s_layer.py` — `test_import() -> None` asserts `hasattr(dbt_s_layer, "__all__")` and `dbt_s_layer.__all__ == []`

## uv.lock Resolution Snapshot

Key versions that landed after `uv sync --dev`:

| Package       | Resolved Version | Notes                                                        |
| ------------- | ---------------- | ------------------------------------------------------------ |
| typer         | 0.24.1           | Runtime dep (spec `>=0.24.1`); satisfies Plan 02 CLI needs   |
| rich          | 15.0.0           | Transitive via typer                                         |
| click         | 8.3.2            | Transitive via typer                                         |
| basedpyright  | 1.39.3           | Dev; spec `>=1.38.0`; strict mode type checker               |
| pytest        | 9.0.3            | Dev; spec `>=8.0.0`                                          |
| pytest-cov    | 7.1.0            | Dev; spec `>=6.0.0` (CONTEXT.md specified ≥6)                |
| ruff          | 0.15.11          | Dev; spec `>=0.15.1`                                         |
| coverage      | 7.13.5           | Transitive via pytest-cov                                    |
| nodejs-wheel-binaries | 24.15.0  | Transitive via basedpyright (bundles Node runtime for pyright) |

Total resolved: 20 packages (18 installed into `.venv`; `dbt-s-layer` itself + `annotated-doc` round out the list).

## Decisions Made

- **LICENSE sourced from dbt_utils package** rather than fetching from apache.org (WebFetch/WebSearch permissions unavailable). The dbt_utils LICENSE matched the canonical Apache-2.0 text exactly (202 lines, starts with `                                 Apache License`, contains full Appendix). Copyright line replaced with dbt-s-layer attribution. 11402-byte size comfortably exceeds the plan's ≥10000-byte acceptance criterion.
- **`typer>=0.24.1` added to runtime deps now** (not deferred to Plan 02) so Plan 02 can land `cli.py` without needing to touch `pyproject.toml`. Matches plan-level interface contract.
- **metricflow family pins NOT added** here — the three packages (metricflow, metricflow-semantics, dbt-semantic-interfaces) are explicitly deferred to Plan 03 (stack verification spike). Only `typer` was added to `[project.dependencies]`.
- **`[tool.uv] override-dependencies`** for the metricflow Python-version cap override is NOT added here — deferred to Plan 03 (alongside the metricflow pins it constrains). This keeps Plan 01 free of any metricflow-related config.

## Deviations from Plan

None — plan executed exactly as written.

All acceptance criteria met:
- `build-backend = "uv_build"` ✓
- `requires-python = ">=3.11"` ✓
- `typer>=0.24.1` in `[project]` deps ✓
- `basedpyright>=1.38.0` in `[dependency-groups] dev` ✓
- `dbt-s-layer = "dbt_s_layer.cli:app"` under `[project.scripts]` ✓
- `typeCheckingMode = "strict"` under `[tool.pyright]` ✓
- Ruff `select` matches exactly ✓
- `__all__: list[str] = []` in `__init__.py` ✓
- `py.typed` is 0 bytes ✓
- `.python-version` contains `3.11` ✓
- `.gitignore` contains `__pycache__/` and `.venv/` ✓
- `LICENSE` is ≥ 10000 bytes (11402) and starts with `                                 Apache License` ✓
- `README.md` contains `# dbt-s-layer` ✓
- `uv sync --dev` exits 0 and creates `uv.lock` ✓
- `uv run python -c "import dbt_s_layer"` exits 0 ✓
- `uv run python -c "import dbt_s_layer; assert dbt_s_layer.__all__ == []"` exits 0 ✓
- `uv run pytest` exits 0 with 1 passing test ✓
- `test_dbt_s_layer.py` defines `def test_import() -> None:` ✓
- Bonus: `uv build` produces wheel containing `dbt_s_layer/py.typed` ✓

## Issues Encountered

None.

## Confirmation: Plan 02 Interface

The `pyproject.toml` declares:

```toml
[project.scripts]
dbt-s-layer = "dbt_s_layer.cli:app"
```

Plan 02 (00-02) MUST create `src/dbt_s_layer/cli.py` with a module-level `app: typer.Typer` symbol to satisfy this entrypoint. The runtime dependency `typer>=0.24.1` is already locked in `uv.lock`.

## User Setup Required

None - no external service configuration required for this plan.

## Next Plan Readiness

- **Plan 00-02 (CLI + doctor):** Ready. `typer` is installed; `pyproject.toml` declares the entrypoint; package is importable.
- **Plan 00-03 (stack verification spike):** Ready. `uv` environment available; `pyproject.toml` has free `[project.dependencies]` block (no metricflow yet) waiting for spike results.
- **Plan 00-04 (CI quality gates):** Ready. `uv.lock` is committed so CI can use `uv sync --locked`; `pyproject.toml` has ruff + basedpyright + pytest configured.

No blockers.

## Self-Check: PASSED

Files verified on disk:
- FOUND: pyproject.toml
- FOUND: src/dbt_s_layer/__init__.py
- FOUND: src/dbt_s_layer/py.typed (0 bytes)
- FOUND: tests/__init__.py (0 bytes)
- FOUND: tests/conftest.py
- FOUND: tests/test_dbt_s_layer.py
- FOUND: .python-version
- FOUND: .gitignore
- FOUND: LICENSE (11402 bytes)
- FOUND: README.md
- FOUND: uv.lock

Commits verified in git log:
- FOUND: 3fa5c87 (Task 1: scaffold package with uv_build and supporting files)
- FOUND: d2cd8b3 (Task 2: tests/ scaffold with smoke test)

Functional verification:
- FOUND: `uv sync --locked --dev` exits 0
- FOUND: `uv run python -c "import dbt_s_layer"` exits 0
- FOUND: `uv run pytest` exits 0 (1 passing test)

---
*Phase: 00-foundation-governance*
*Plan: 01 — Package scaffold*
*Completed: 2026-04-21*
