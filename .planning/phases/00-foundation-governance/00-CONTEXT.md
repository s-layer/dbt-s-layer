# Phase 0: Foundation & Governance - Context

**Gathered:** 2026-04-21
**Status:** Ready for planning
**Source:** Assumptions discussion + cookiecutter-python-uv-library template review

<domain>
## Phase Boundary

Repo scaffold, license-clean CI, stack verification spike, and a minimal `doctor` CLI stub. A freshly cloned repo must pass `uv run pytest`, CI must run all quality gates, and `dbt-s-layer doctor` must detect the `dbtf` binary and report the installed metricflow version.

**In scope:**
- `pyproject.toml` + `src/dbt_s_layer/` scaffold with `py.typed` marker
- GitHub Actions CI (quality gates + pytest)
- `dbt-s-layer doctor` — binary detection + metricflow version report only
- Stack verification spike (committed `spike/` script as evidence)
- Pinned dep versions for metricflow, metricflow-semantics, dbt-semantic-interfaces committed after spike

**Out of scope:**
- Parsing logic (Phase 1)
- `generate` or `check` commands (Phase 3)
- Output directory writability check in `doctor` (deferred to Phase 3 when `--output-dir` exists)
- Pre-commit hook for users (Phase 5)
- License scan CI (not needed — ELv2 risk is architectural, not a dep-tree scanning problem)

</domain>

<decisions>
## Implementation Decisions

### Build & Packaging
- Build backend: `uv_build` (NOT hatchling) — `requires = ["uv_build>=0.9.18,<1.0.0"]`
- Package manager: `uv`
- Layout: `src/dbt_s_layer/` with `py.typed` marker (PEP 561)
- Python minimum: 3.11; dev/CI target: 3.14
- `__init__.py` exports `__all__ = []` initially

### Type Checking
- `basedpyright` in strict mode (NOT mypy)
- `reportPrivateUsage = false` (tests can access private members)
- Runs as a local pre-commit hook (system language)

### Linting & Formatting
- `ruff` with rules: E, F, W, I, UP, B, SIM, TCH, D
- Docstring style: D213 (summary on second line after opening quotes)
- Ignores: D1, D202, D203, D212, D401, D413
- Line length: 100
- isort integrated via ruff

### Pre-commit & Quality Gate
- Standard hooks: trailing-whitespace, end-of-file-fixer, check-json, check-toml, check-yaml
- ruff (--fix) + ruff-format + uv-lock + basedpyright (local, system)
- Quality gate runner: `prek run --all-files` (NOT `pre-commit run`)

### CI (GitHub Actions)
- Reusable `quality.yml` called from `ci.yml`
- Steps: checkout → setup uv (cached) → `uv sync --locked` → `prek run --all-files` → pytest
- Python matrix: 3.11 + 3.14
- Weekly compat check workflow (unlocked deps, Copilot PR if failing)
- Release pipeline: trusted OIDC publishing to PyPI (no long-lived tokens)
- No license scan step

### Doctor Command (Phase 0 scope only)
- `shutil.which("dbtf")` for binary detection
- `importlib.metadata.version("metricflow")` for version report
- Clear error message with install link if `dbtf` not found
- Output directory writability check deferred to Phase 3

### Stack Verification Spike
- Lives in `spike/` directory, committed as evidence of version pinning decisions
- Must probe: Pydantic v1 vs v2 in metricflow, `SqlTable.from_string` module path, `PydanticSemanticManifest` import path
- Output: pinned versions committed to `pyproject.toml`

### Testing
- `pytest` ≥ 8, `pytest-cov` ≥ 6
- `tests/` with `__init__.py`, `conftest.py`, `test_dbt_s_layer.py`
- Basic smoke test: `assert hasattr(dbt_s_layer, "__all__")`
- Empty test suite must pass `uv run pytest` in a fresh clone

### CLI Framework
- `typer` for CLI; `rich` comes transitively
- Entrypoint: `dbt-s-layer` registered via `[project.scripts]` in pyproject.toml
- Subcommand: `doctor`

### Claude's Discretion
- Exact GitHub Actions runner versions (use `actions/setup-python@v5`, `astral-sh/setup-uv@v3`)
- Whether to include `dependabot.yml` in Phase 0 (low priority, can be added)
- `DEVELOP.md` and `AGENTS.md` scaffold docs (nice to have, not required for success criteria)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project Requirements & Roadmap
- `.planning/ROADMAP.md` — Phase 0 success criteria (all 5 must be met)
- `.planning/REQUIREMENTS.md` — CLI-03 requirement definition

### Template Reference (do not copy blindly — adapt to this project)
- `/Users/paul/Documents/Dev/Personal/cookiecutter-python-uv-library/{{ cookiecutter.project_slug }}/pyproject.toml` — canonical pyproject.toml shape
- `/Users/paul/Documents/Dev/Personal/cookiecutter-python-uv-library/{{ cookiecutter.project_slug }}/.github/workflows/quality.yml` — CI workflow pattern
- `/Users/paul/Documents/Dev/Personal/cookiecutter-python-uv-library/{{ cookiecutter.project_slug }}/.pre-commit-config.yaml` — pre-commit hook list

</canonical_refs>

<specifics>
## Specific Ideas

- The spike script should confirm `from pydantic.v1 import BaseModel` vs `from pydantic import BaseModel` in metricflow — this decision cascades to all future phases
- `dbt-s-layer doctor` exit codes: 0 = all good, 1 = dbtf missing, 2 = metricflow version unsupported (future)
- Pinned versions for the three metricflow-family packages should be locked in `pyproject.toml` under `[project.dependencies]`, not just dev deps — they are runtime deps

</specifics>

<deferred>
## Deferred Ideas

- Output directory writability check in `doctor` → Phase 3 (when `--output-dir` flag exists)
- `--project-dir`, `--output-dir`, `--target`, `--adapter` CLI flags → Phase 3
- Coverage reporting PR comments → can be added in Phase 8 hardening
- `DEVELOP.md`, `AGENTS.md` docs scaffold → Phase 8
- Changelog automation (git-cliff) → Phase 8

</deferred>

---

*Phase: 00-foundation-governance*
*Context gathered: 2026-04-21 via assumptions discussion + cookiecutter template review*
