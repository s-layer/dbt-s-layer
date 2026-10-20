# Phase 0: Foundation & Governance - Research

**Researched:** 2026-04-21
**Domain:** Python package scaffolding, quality-gate CI, stack verification for dbt semantic-layer tooling
**Confidence:** HIGH for tooling stack, MEDIUM for metricflow family pins (dev-version pin inside 0.209.0 needs spike confirmation)

## Summary

Phase 0 is almost entirely mechanical: apply the cookiecutter-python-uv-library template as-is, add a `doctor` Typer subcommand, and run a small stack-verification spike to pin the metricflow family. The template's pyproject.toml, pre-commit config, and GitHub Actions workflows already encode every CONTEXT.md decision (uv_build, basedpyright strict, prek, ruff ruleset, py.typed, 3.11+3.14 matrix, weekly compat check, OIDC release). The template's `cookiecutter.minimum_python_version` slot maps directly to `"3.11"`.

The research uncovered one **material risk** to the 3.11+3.14 CI matrix: **metricflow 0.209.0 declares `Requires-Python <3.13, >=3.9`**. The current `main` branch loosens this to `>=3.10,<3.14`, but no released wheel yet claims 3.14 compatibility — and pydantic v1's compatibility shim is unreliable on 3.14 (upstream pydantic note). This means either (a) Phase 0 pins metricflow at a version that excludes 3.14 from CI until a new release lands, (b) the 3.14 CI leg excludes `metricflow` from the runtime dep set and tests only scaffolding, or (c) the matrix drops 3.14 entirely for now.

**Primary recommendation:** Clone the cookiecutter template into the repo root, set `minimum_python_version=3.11`, add metricflow stack via the spike, and adopt approach (b) above — a "scaffold-only" CI leg on 3.14 using an `environment: minimal` extra that omits metricflow, while the `environment: full` leg with metricflow runs on 3.11 and 3.12. Revisit when metricflow publishes a wheel declaring 3.14 support.

## User Constraints (from CONTEXT.md)

### Locked Decisions

**Build & Packaging:**
- Build backend: `uv_build` (NOT hatchling) — `requires = ["uv_build>=0.9.18,<1.0.0"]`
- Package manager: `uv`
- Layout: `src/dbt_s_layer/` with `py.typed` marker (PEP 561)
- Python minimum: 3.11; dev/CI target: 3.14
- `__init__.py` exports `__all__ = []` initially

**Type Checking:**
- `basedpyright` in strict mode (NOT mypy)
- `reportPrivateUsage = false`
- Runs as a local pre-commit hook (system language)

**Linting & Formatting:**
- `ruff` with rules: E, F, W, I, UP, B, SIM, TCH, D
- Docstring style: D213
- Ignores: D1, D202, D203, D212, D401, D413
- Line length: 100
- isort integrated via ruff

**Pre-commit & Quality Gate:**
- Standard hooks: trailing-whitespace, end-of-file-fixer, check-json, check-toml, check-yaml
- ruff (--fix) + ruff-format + uv-lock + basedpyright (local, system)
- Quality gate runner: `prek run --all-files` (NOT `pre-commit run`)

**CI (GitHub Actions):**
- Reusable `quality.yml` called from `ci.yml`
- Steps: checkout → setup uv (cached) → `uv sync --locked` → `prek run --all-files` → pytest
- Python matrix: 3.11 + 3.14
- Weekly compat check workflow (unlocked deps, Copilot PR if failing)
- Release pipeline: trusted OIDC publishing to PyPI (no long-lived tokens)
- **No license scan step** (ELv2 risk is architectural)

**Doctor Command (Phase 0 scope only):**
- `shutil.which("dbtf")` for binary detection
- `importlib.metadata.version("metricflow")` for version report
- Clear error message with install link if `dbtf` not found
- Output directory writability check deferred to Phase 3

**Stack Verification Spike:**
- Lives in `spike/` directory, committed as evidence
- Must probe: Pydantic v1 vs v2 in metricflow, `SqlTable.from_string` module path, `PydanticSemanticManifest` import path
- Output: pinned versions committed to `pyproject.toml`

**Testing:**
- `pytest` ≥ 8, `pytest-cov` ≥ 6
- `tests/` with `__init__.py`, `conftest.py`, `test_dbt_s_layer.py`
- Basic smoke test: `assert hasattr(dbt_s_layer, "__all__")`
- Empty test suite must pass `uv run pytest` in a fresh clone

**CLI Framework:**
- `typer` for CLI; `rich` comes transitively
- Entrypoint: `dbt-s-layer` registered via `[project.scripts]` in pyproject.toml
- Subcommand: `doctor`

### Claude's Discretion

- Exact GitHub Actions runner versions (template currently uses `actions/setup-python@v5`, `astral-sh/setup-uv@v7`, `actions/checkout@v6`)
- Whether to include `dependabot.yml` in Phase 0 (low priority, can be added)
- `DEVELOP.md` and `AGENTS.md` scaffold docs (nice to have, not required for success criteria)

### Deferred Ideas (OUT OF SCOPE)

- Output directory writability check in `doctor` → Phase 3 (when `--output-dir` flag exists)
- `--project-dir`, `--output-dir`, `--target`, `--adapter` CLI flags → Phase 3
- Coverage reporting PR comments → can be added in Phase 8 hardening
- `DEVELOP.md`, `AGENTS.md` docs scaffold → Phase 8
- Changelog automation (git-cliff) → Phase 8

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| CLI-03 | `dbt-s-layer doctor` validates the environment: `semantic_manifest.json` presence/freshness, supported metricflow version, output directory writability | Phase 0 covers the **doctor scaffolding** only — `shutil.which("dbtf")` + `importlib.metadata.version("metricflow")`. Manifest-freshness and output-dir-writability checks defer to Phases 1 and 3 respectively. See doctor pseudocode in Code Examples below. |

## Project Constraints (from CLAUDE.md)

The following CLAUDE.md directives apply to Phase 0 and must be respected by the planner:

| Directive | Source | How Phase 0 honours it |
|-----------|--------|------------------------|
| Must not embed ELv2-licensed code (Fusion crates) | "Constraints" | Doctor uses subprocess detection (`shutil.which`) — no Rust binding, no vendored source |
| Python runtime | "Constraints" | pyproject.toml sets `requires-python = ">=3.11"` — see metricflow caveat below |
| Build backend is uv_build | Stack § Core Runtime | `[build-system] requires = ["uv_build>=0.9.18,<1.0.0"]`, `build-backend = "uv_build"` |
| Use Typer for CLI | Stack § CLI Framework | `dbt-s-layer` entrypoint via `[project.scripts]`, `doctor` subcommand |
| Use pydantic v2 | Stack § Data Models | Applies to our own code — metricflow family sets their own pin (see findings) |
| Ship `.pre-commit-hooks.yaml` as public surface | Stack § Pre-commit Hook Contract | **Deferred to Phase 5** — not in Phase 0 scope |
| GSD workflow enforcement | CLAUDE.md | All changes go through GSD commands; direct edits outside workflow are disallowed |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard | Source |
|---------|---------|---------|--------------|--------|
| Python | 3.11, 3.12 (full), 3.14 (scaffold-only) | Package runtime | 3.11 is the floor; metricflow 0.209.0 blocks 3.13/3.14 from the full runtime leg (see Common Pitfalls) | [VERIFIED: PyPI metricflow 0.209.0 Requires-Python <3.13,>=3.9] |
| `uv` | ≥ 0.5 (install time); CI uses `astral-sh/setup-uv@v7` | Dep resolver + venv + build frontend | Template already uses this — 2025/2026 ecosystem consensus | [VERIFIED: cookiecutter template quality.yml] |
| `uv_build` | `>=0.9.18,<1.0.0` (per CONTEXT.md); upstream docs now recommend `>=0.11.7,<0.12` | PEP 517 build backend | uv's own backend; stable in 2026 | [CITED: docs.astral.sh/uv/concepts/build-backend] |
| `pyproject.toml` | PEP 621 | Single source of metadata | Standard | [VERIFIED: template] |

**Note on uv_build pin:** CONTEXT.md locks `>=0.9.18,<1.0.0`. Current upstream docs recommend `>=0.11.7,<0.12` as of Apr 2026 ([CITED: docs.astral.sh/uv]). The looser lower bound in CONTEXT is fine — honour it. Do **not** silently upgrade the pin without user approval.

### CLI & Output
| Library | Version | Purpose | Source |
|---------|---------|---------|--------|
| `typer` | `>=0.24.1` (latest Feb 2026; Python >=3.10) | CLI framework with type-hint-driven subcommands | [VERIFIED: pypi.org/project/typer] |
| `rich` | transitive via typer | Terminal output | [ASSUMED] based on typer's dep tree |

### Type / Lint / Format / Test (locked in CONTEXT.md)
| Library | Version | Purpose | Source |
|---------|---------|---------|--------|
| `basedpyright` | `>=1.38.0` per template; 1.21.0+ is "recent" per docs | Strict type checking | [VERIFIED: template; CITED: docs.basedpyright.com] |
| `ruff` | `>=0.15.1` per template | Lint + format | [VERIFIED: template] |
| `pytest` | `>=8.0.0` | Test runner | [VERIFIED: template] |
| `pytest-cov` | `>=6.0.0` | Coverage | [VERIFIED: template] |
| `prek` | `0.3.10` (latest, Apr 21 2026) — install via `uv tool install prek` in CI | Rust-based drop-in replacement for `pre-commit`; reads same `.pre-commit-config.yaml`, supports `repo: local` + `language: system` hooks | [VERIFIED: pypi.org/project/prek; CITED: prek.j178.dev] |

### dbt Semantic-Layer Runtime Deps (runtime deps in `[project.dependencies]`, pinned from spike)
| Library | Candidate pin | Purpose | Source |
|---------|--------------|---------|--------|
| `metricflow` | `==0.209.0` (latest GA, Oct 14 2025; Apache-2.0 from this version onward) | SQL rendering via `MetricFlowEngine.explain()` (used in Phase 2+) | [VERIFIED: pypi.org/project/metricflow] |
| `metricflow-semantics` | Transitive pin; let metricflow resolve it. Split out from main metricflow at v0.206.0. | Holds `SqlTable`, `SqlTableNode` etc. | [CITED: metricflow GitHub release notes] |
| `dbt-semantic-interfaces` | `metricflow==0.209.0` pins `dbt-semantic-interfaces==0.9.4.dev0` exactly. **Spike must confirm this dev-version pin.** PyPI shows 0.10.5 (Feb 10 2026) as latest stable but it is incompatible with metricflow 0.209.0. | `PydanticSemanticManifest` | [VERIFIED: pypi.org/project/metricflow JSON metadata for 0.209.0; VERIFIED: pypi.org/project/dbt-semantic-interfaces] |
| `pydantic` | `>=2.7,<3` (our own code); metricflow accepts `>=1.10.0,<3.0` so v2 co-exists | Our config models | [VERIFIED: metricflow pyproject.toml on main] |
| `dbt-semantic-interfaces` pydantic | Also accepts `>=1.10,<3` | Our dep uses v2-compatible shape | [VERIFIED: dbt-semantic-interfaces pyproject.toml] |

**Key finding:** Both `metricflow` and `dbt-semantic-interfaces` accept pydantic v2. The CLAUDE.md notes a worry about pydantic v1 compat shims — that worry is **mostly discharged** by the PyPI metadata. The spike must still verify at runtime: does `PydanticSemanticManifest` import as `from dbt_semantic_interfaces.implementations.semantic_manifest import PydanticSemanticManifest`, and does its `parse_file(...)` / `model_validate_json(...)` actually succeed against a Fusion-produced manifest? If the model classes are declared with pydantic-v1 semantics (`@validator` etc.) the `pydantic.v1` shim is still in play.

### Not Yet Added in Phase 0 (future phases)
- `jinja2`, `ruamel.yaml`, `sqlglot` — codegen concerns (Phase 2/3)
- `pre-commit-hooks` public surface — Phase 5

**Installation (for local dev):**
```bash
uv sync --locked --dev
```

**Version verification performed during research:**
| Package | Latest version | Release date | Python support |
|---------|----------------|--------------|----------------|
| `metricflow` | 0.209.0 | 2025-10-14 | `<3.13, >=3.9` |
| `dbt-semantic-interfaces` | 0.10.5 | 2026-02-10 | `>=3.8` |
| `metricflow-semantics` | (bundled in metricflow 0.206+) | tracks metricflow | tracks metricflow |
| `pydantic` | 2.12.5+ (v2 current) | — | v1 shim unreliable on 3.14 |
| `uv_build` | 0.11.7+ | — | — |
| `prek` | 0.3.10 | 2026-04-21 | Rust binary; installable via pip/uv |
| `typer` | 0.24.1 | 2026-02-21 | `>=3.10` |
| `basedpyright` | 1.21.0+ | — | — |

The spike **must** re-verify the metricflow-family pins by invoking `uv pip install metricflow==0.209.0` and printing resolved versions of its transitive deps.

### Alternatives Considered (already rejected in CONTEXT.md)
| Instead of | Could Use | Why Not |
|------------|-----------|---------|
| `uv_build` | `hatchling` | Locked decision in CONTEXT.md |
| `basedpyright` | `mypy` | Locked decision in CONTEXT.md |
| `prek` | `pre-commit` | Locked decision in CONTEXT.md |

## Architecture Patterns

### Recommended Project Structure (Phase 0 scaffold)
```
dbt-s-layer/
├── .github/
│   └── workflows/
│       ├── ci.yml              # triggers on push, calls quality.yml
│       ├── quality.yml          # reusable — ruff, basedpyright, pytest on 3.11 + (3.14 minimal)
│       ├── release.yml          # tag-triggered OIDC publish
│       └── weekly.yml           # scheduled compat check
├── .pre-commit-config.yaml      # template-matching hook set
├── .python-version              # "3.11"
├── pyproject.toml               # uv_build backend, deps, tool configs
├── src/
│   └── dbt_s_layer/
│       ├── __init__.py          # __all__ = []
│       ├── py.typed              # empty file (PEP 561)
│       ├── cli.py               # Typer app + doctor subcommand
│       └── __main__.py          # enables `python -m dbt_s_layer`
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   └── test_dbt_s_layer.py     # smoke test
├── spike/
│   ├── __init__.py
│   ├── verify_stack.py          # spike script (see below)
│   └── README.md                # documents what was verified
├── LICENSE                       # Apache-2.0
├── README.md
└── CLAUDE.md                    # already exists — do not touch
```

### Pattern 1: Typer app with subcommand (doctor)
**What:** Single root Typer app, subcommand registered as a callback on a sub-app.
**When:** Whenever there are ≥ 2 subcommands (Phase 3 adds `generate`/`check`).
**Example:**
```python
# src/dbt_s_layer/cli.py
# Source: verified against typer 0.24.x quickstart pattern
import typer

app = typer.Typer(
    name="dbt-s-layer",
    help="Code-generate dbt models from the dbt semantic layer.",
    no_args_is_help=True,
)


@app.command()
def doctor() -> None:
    """Report environment diagnostics (Fusion binary, metricflow version)."""
    from dbt_s_layer._doctor import run_doctor
    raise typer.Exit(code=run_doctor())


if __name__ == "__main__":
    app()
```

```toml
# pyproject.toml
[project.scripts]
dbt-s-layer = "dbt_s_layer.cli:app"
```

### Pattern 2: Doctor check module
**What:** Pure-function environment probe, returns an exit code.
**Example:**
```python
# src/dbt_s_layer/_doctor.py
import shutil
from importlib.metadata import PackageNotFoundError, version

FUSION_INSTALL_URL = "https://docs.getdbt.com/docs/fusion/install-fusion"


def run_doctor() -> int:
    """Return 0 if everything is ready, else a non-zero code.

    Exit codes:
        0 — dbtf binary present AND metricflow importable
        1 — dbtf missing (blocking)
        2 — metricflow version unreadable (blocking — package install broken)
    """
    exit_code = 0

    dbtf_path = shutil.which("dbtf")
    if dbtf_path is None:
        print(f"ERROR: `dbtf` (dbt Fusion) not found on PATH.")
        print(f"Install Fusion: {FUSION_INSTALL_URL}")
        exit_code = 1
    else:
        print(f"OK  dbtf found at: {dbtf_path}")

    try:
        mf_version = version("metricflow")
        print(f"OK  metricflow version: {mf_version}")
    except PackageNotFoundError:
        print("ERROR: metricflow is not installed (dbt-s-layer install is broken).")
        exit_code = 2

    return exit_code
```

### Pattern 3: Stack verification spike script
**What:** Committed script that documents version pinning evidence.
**Example:**
```python
# spike/verify_stack.py
"""Stack verification spike for dbt-s-layer Phase 0.

Run: uv run python spike/verify_stack.py
Commits a snapshot of what Phase 0 found so later phases can trust pins.
"""
from __future__ import annotations

import importlib.metadata as meta


def probe() -> None:
    """Report resolved versions and internal import paths we depend on."""
    for pkg in ("metricflow", "metricflow-semantics", "dbt-semantic-interfaces", "pydantic"):
        try:
            print(f"{pkg}: {meta.version(pkg)}")
        except meta.PackageNotFoundError:
            print(f"{pkg}: NOT INSTALLED")

    # Verify the surfaces we rely on in later phases:
    from dbt_semantic_interfaces.implementations.semantic_manifest import (
        PydanticSemanticManifest,
    )
    print(f"PydanticSemanticManifest import OK → {PydanticSemanticManifest!r}")

    from metricflow_semantics.sql.sql_table import SqlTable
    print(f"SqlTable import OK → {SqlTable!r}")
    print(f"SqlTable.from_string OK → {SqlTable.from_string!r}")

    # Pydantic v1 shim check
    import pydantic
    print(f"pydantic.VERSION = {pydantic.VERSION}")
    # PydanticSemanticManifest may be declared with pydantic-v1 semantics even when
    # pydantic v2 is installed — model_fields vs __fields__ tells us which.
    has_v2_fields = hasattr(PydanticSemanticManifest, "model_fields")
    has_v1_fields = hasattr(PydanticSemanticManifest, "__fields__")
    print(f"manifest uses pydantic-v2 API: {has_v2_fields}")
    print(f"manifest uses pydantic-v1 API: {has_v1_fields}")


if __name__ == "__main__":
    probe()
```
The spike's stdout should be captured in `spike/results-<date>.txt` and committed — that's the evidence for the pinned versions.

### Anti-Patterns to Avoid

- **Don't** put metricflow in a `dev` dependency group. It's a **runtime** dep (CLAUDE.md spec, CONTEXT.md specifics). Runtime users need it to generate.
- **Don't** call `dbtf` during `doctor` (e.g., `dbtf --version`). `shutil.which` is enough and avoids error-path noise. Running the binary is deferred to Phase 1.
- **Don't** scan dependency licenses in CI. CONTEXT.md is explicit: ELv2 risk is architectural (no PyO3 bindings, no vendored Rust) — dep-tree scanning would false-positive and mask real risk. ROADMAP.md's success criterion #2 mentions a license scan; that criterion is **superseded by CONTEXT.md**.
- **Don't** use `pre-commit run` in CI. Use `prek run --all-files`. The local `.pre-commit-config.yaml` is read by both — but the runner is `prek`.
- **Don't** match the template's `minimum_python_version` to 3.10. CONTEXT.md locks 3.11 minimum.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Package build plumbing | setuptools setup.py | `uv_build` | Locked in CONTEXT |
| Lock-file generation | pip-compile | `uv.lock` via `uv sync` | uv native |
| Pre-commit hook runner | shell script | `prek` | Faster + spec-compatible |
| CI workflow from scratch | ad-hoc YAML | Cookiecutter template's `quality.yml` + `ci.yml` | Already solves caching, matrix, timeouts, cache pruning |
| Publish pipeline | manual `twine upload` + API tokens | `pypa/gh-action-pypi-publish@release/v1` + OIDC | Trusted publishing, no secrets rotation |
| Binary detection | `os.system("which dbtf")` | `shutil.which("dbtf")` | Cross-platform, no shell spawn |
| Version reporting | parsing `pip show metricflow` stdout | `importlib.metadata.version(...)` | Stdlib, fast, typed |
| Dev tool ownership | committed `requirements-dev.txt` | `[dependency-groups] dev` in pyproject.toml | PEP 735, uv native |

**Key insight:** The cookiecutter template is a direct match for CONTEXT.md. Hand-rolling any of these is rework.

## Common Pitfalls

### Pitfall 1: metricflow 0.209.0 blocks Python 3.13 and 3.14
**What goes wrong:** `uv sync` on Python 3.14 fails with `No matching distribution found for metricflow==0.209.0 (from dbt-s-layer)` because metricflow 0.209.0's `Requires-Python` is `<3.13,>=3.9`.
**Why it happens:** metricflow's `main` branch is at `>=3.10,<3.14` but the 0.209.0 release pre-dates full 3.14 testing. pydantic-v1's compatibility shim relies on Python internals removed in 3.14, further blocking the chain.
**How to avoid:**
- Drop the 3.14 matrix leg for the full environment. Keep 3.14 **only** for a "scaffold-only" matrix leg that installs `dbt-s-layer[scaffold]` without the metricflow family. This preserves the "dev target 3.14" requirement in CONTEXT.md without forcing an install failure.
- Document the trade-off in the pyproject.toml and revisit when metricflow publishes a 3.14-compatible release.
- Alternative: target 3.11 + 3.12 only and revisit 3.14 after a phase-milestone review.
**Warning signs:** `uv sync --locked` on the 3.14 job fails with `requires-python` error.

### Pitfall 2: dbt-semantic-interfaces dev-version pin
**What goes wrong:** `metricflow==0.209.0` pins `dbt-semantic-interfaces==0.9.4.dev0`. uv resolver may require `--prerelease=allow` or a `[tool.uv] allow-insecure-host`-style setting to accept a `.dev` version.
**Why it happens:** dbt-labs commonly ships dev-tagged transitive deps; strict resolvers will refuse them.
**How to avoid:**
- Spike confirms the resolution behaves. If it fails, add to pyproject.toml:
  ```toml
  [tool.uv]
  prerelease = "allow"   # for metricflow's dev transitive
  ```
- Or pin `dbt-semantic-interfaces` explicitly to `==0.9.4.dev0` in `[project.dependencies]` — matches upstream exactly.
**Warning signs:** `uv sync` errors with "prerelease is not allowed" for dbt-semantic-interfaces.

### Pitfall 3: py.typed marker missing from wheel
**What goes wrong:** `pip install dbt-s-layer` succeeds but downstream consumers get "no type information" because the wheel excluded the `py.typed` zero-byte marker.
**Why it happens:** Some build backends need explicit `package-data` config. `uv_build` auto-includes files in the package dir, but it's a known Phase-0 gotcha.
**How to avoid:** Template's `release.yml` already has a validation step: `unzip -l dist/*.whl | grep "dbt_s_layer/py.typed"`. Keep it.
**Warning signs:** basedpyright in a downstream project says "Stub file not found for 'dbt_s_layer'".

### Pitfall 4: prek and the template's template-literal mismatch
**What goes wrong:** Template renders `{%- raw %}${{ matrix.python-version }}{%- endraw %}`. When you copy this file into the real repo, the `{%- raw %}` / `{%- endraw %}` markers stay and break GitHub Actions parsing (`${{ ... }}` gets double-escaped).
**Why it happens:** Cookiecutter uses jinja2 with raw-block fencing around GHA expressions. The rendered output (what Cookiecutter produces) is already clean; but if you **manually** copy the template file instead of running Cookiecutter, you keep the fences.
**How to avoid:** Either (a) run `cookiecutter /Users/paul/.../cookiecutter-python-uv-library` once, capture the output, and seed the repo from the rendered output; or (b) copy files manually but grep for `{%- raw %}` / `{%- endraw %}` / `{{ cookiecutter.` and delete the fences.
**Warning signs:** CI fails to parse workflow YAML on first push.

### Pitfall 5: basedpyright local hook runs without uv context
**What goes wrong:** `prek run basedpyright --all-files` works locally but fails in CI because `uv run basedpyright` needs the synced venv.
**Why it happens:** The pre-commit-config has `entry: uv run basedpyright` — this requires `uv sync` to have run first. The template's `quality.yml` sequences these correctly (sync then prek).
**How to avoid:** Keep the quality.yml step order exactly as the template has it: `uv sync --locked --dev` → `uv tool install prek` → `prek run --all-files`.
**Warning signs:** CI error "command not found: basedpyright" even though hook is defined.

### Pitfall 6: `uv sync --locked` vs `uv sync --locked --dev`
**What goes wrong:** CI installs only runtime deps, basedpyright/ruff/pytest not installed, hooks fail with "command not found".
**Why it happens:** `uv sync --locked` without `--dev` omits the `[dependency-groups] dev` group (uv follows PEP 735).
**How to avoid:** Template uses `uv sync --locked --dev`. Do not drop `--dev` in CI.
**Warning signs:** "uv run basedpyright: command not found" in CI.

## Code Examples

### Example 1: Minimal pyproject.toml shape
```toml
# pyproject.toml
[project]
name = "dbt-s-layer"
version = "0.1.0"
description = "dbt Cloud semantic-layer features for dbt Core / Fusion users"
readme = "README.md"
license = "Apache-2.0"
authors = [{ name = "Anentropic", email = "ego@anentropic.com" }]
requires-python = ">=3.11"
dependencies = [
    # Runtime deps added by spike commit — placeholders below, replace with
    # spike-verified pins (see spike/results-<date>.txt).
    # "metricflow==0.209.0",
    # "dbt-semantic-interfaces==0.9.4.dev0",
    # "typer>=0.24.1",
    # "pydantic>=2.7,<3",
]

[project.scripts]
dbt-s-layer = "dbt_s_layer.cli:app"

[build-system]
requires = ["uv_build>=0.9.18,<1.0.0"]
build-backend = "uv_build"

[dependency-groups]
dev = [
    "basedpyright>=1.38.0",
    "pytest>=8.0.0",
    "pytest-cov>=6.0.0",
    "ruff>=0.15.1",
]

[tool.pyright]
pythonVersion = "3.11"
typeCheckingMode = "strict"
include = ["src", "tests"]
reportPrivateUsage = false

[tool.ruff]
target-version = "py311"
line-length = 100
src = ["src"]

[tool.ruff.lint]
select = ["E", "F", "W", "I", "UP", "B", "SIM", "TCH", "D"]
ignore = ["D1", "D202", "D203", "D212", "D401", "D413"]

[tool.pytest.ini_options]
addopts = "-v"
```

### Example 2: GitHub Actions quality.yml (adapted from template)
```yaml
# .github/workflows/quality.yml
name: Quality

on:
  workflow_call:

permissions:
  contents: read

jobs:
  quality:
    name: Quality gates
    runs-on: ubuntu-latest
    timeout-minutes: 15
    strategy:
      matrix:
        python-version: ["3.11", "3.12"]   # 3.14 excluded until metricflow support lands
      fail-fast: false

    steps:
      - name: Checkout
        uses: actions/checkout@v6

      - name: Set up uv
        uses: astral-sh/setup-uv@v7
        with:
          enable-cache: true
          cache-dependency-glob: "uv.lock"
          python-version: ${{ matrix.python-version }}

      - name: Sync dependencies
        run: uv sync --locked --dev

      - name: Install prek
        run: uv tool install prek

      - name: Run quality gates
        run: prek run --all-files

      - name: Tests
        run: uv run pytest

      - name: Cache pruning
        if: always()
        run: uv cache prune --ci
```

### Example 3: .pre-commit-config.yaml (adapted from template)
```yaml
# .pre-commit-config.yaml
exclude: ^\.planning/
repos:
  - repo: builtin
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-json
      - id: check-toml
      - id: check-yaml

  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.15.1
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format

  - repo: https://github.com/astral-sh/uv-pre-commit
    rev: 0.10.3
    hooks:
      - id: uv-lock

  - repo: local
    hooks:
      - id: basedpyright
        name: Type checking (basedpyright)
        entry: uv run basedpyright
        language: system
        types: [python]
        pass_filenames: false
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `pre-commit` (Python) | `prek` (Rust) | 2025-2026 | Faster, single binary, same config; CONTEXT.md locks it in |
| `hatchling` / `setuptools` build backend | `uv_build` | 2025 stable | One tool for deps + build; CONTEXT.md locks it in |
| `mypy` strict | `basedpyright` strict | 2024-2025 | Faster, more rules, same config shape; CONTEXT.md locks it in |
| API-token PyPI publish | OIDC trusted publishing | 2024 | No long-lived tokens; template already uses it |
| pydantic v1 | pydantic v2 | 2023-2024 | metricflow 0.207.1+ accepts v2 (our spike confirms at runtime) |

**Deprecated/outdated:**
- `requirements.txt` / `setup.py` — replaced by pyproject.toml + uv.lock
- `pip install -e .` for dev — replaced by `uv sync`
- License scan as ELv2 guard — unreliable; architectural choice (no Rust binding) is the real guard

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `rich` is auto-installed as a typer transitive dep | Standard Stack | LOW — if not, add explicit `rich` dep; CLI still works without it |
| A2 | metricflow 0.209.0's resolved `metricflow-semantics` version is the one we want (vs an older or newer) | Standard Stack | MEDIUM — spike must print the resolved version and pin it; if spike shows something different, pins must change |
| A3 | `dbt-semantic-interfaces==0.9.4.dev0` resolves under default uv prerelease policy | Common Pitfalls | MEDIUM — if uv refuses, we'll need `[tool.uv] prerelease = "allow"` |
| A4 | The Phase 0 spike can be written without a real Fusion binary on hand (it only imports, doesn't parse) | Code Examples | LOW — current spike is import-only |
| A5 | Cookiecutter template files are copy-pasteable after rendering (fence removal described in Pitfall 4) | Architecture Patterns | LOW — planner should explicitly call out whether to run cookiecutter or hand-port |
| A6 | OIDC trusted publishing on PyPI is already configured for the project, or is a trivial "configure once" on PyPI admin UI | State of the Art | LOW — deferral-safe; v1 tag doesn't exist until Phase 8 |

## Open Questions (RESOLVED)

1. **Do we want the 3.14 CI leg as "scaffold-only" or drop it entirely?**
   - **RESOLVED: Full matrix 3.11-3.14 per CONTEXT.md D-05. Override bound widened to <3.15.** (A `python_version < '3.14'` bound evaluates False on Python 3.14 itself, so the uv override must use `< '3.15'` to include 3.14.)
   - What we know: metricflow 0.209.0 blocks 3.14; CONTEXT.md wants 3.14 as dev target.
   - What's unclear: is "dev target 3.14" a runtime target or just a local-dev target?
   - Original recommendation: default to Python matrix `[3.11, 3.12]`; add a separate "syntax-only" 3.14 job that installs dbt-s-layer with `--no-deps` and runs basedpyright only. Flag for user discussion if they disagree.

2. **Do we pin `dbt-semantic-interfaces==0.9.4.dev0` explicitly, or rely on metricflow's transitive pin?**
   - **RESOLVED: Spike in Plan 03 Task 1 will confirm; Plan 03 Task 2 adds explicit pin if uv resolution wobbles.**
   - What we know: metricflow 0.209.0 pins it exactly.
   - What's unclear: whether uv's lockfile will capture the `.dev0` version or try to upgrade.
   - Original recommendation: spike must print the resolved graph; if the exact dev version is captured in uv.lock, no explicit pin needed in pyproject.toml. If uv wobbles, pin explicitly.

3. **Does the doctor command need a `--json` output mode for future CI-consumption use?**
   - **RESOLVED: Deferred to Phase 3 per CONTEXT.md deferred items.**
   - What we know: CONTEXT.md scope is text-only.
   - Original recommendation: defer to Phase 3 when `generate --check` arrives; not blocking Phase 0.

## Environment Availability

Phase 0 scaffolding is pure code/config — no external runtime tool is *required* to finish Phase 0 itself. However the produced `doctor` command probes for one tool:

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| `uv` | Phase 0 local dev + CI | Assumed present | — | n/a (hard prereq for template) |
| `prek` | CI + local hook runner | Installed by `uv tool install prek` at CI step | — | falls back to `pre-commit` (not locked — do not do this) |
| `dbtf` | Referenced by `doctor`, **not** required to complete Phase 0 | Unknown on CI runner | — | doctor reports "missing" with install link — that IS the desired behaviour |

**Missing dependencies with no fallback:** None.
**Missing dependencies with fallback:** `dbtf` deliberately not required; doctor must gracefully report its absence.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest >=8.0.0 + pytest-cov >=6.0.0 |
| Config file | `[tool.pytest.ini_options]` in `pyproject.toml` |
| Quick run command | `uv run pytest -x tests/test_dbt_s_layer.py` |
| Full suite command | `uv run pytest` |
| Quality gate | `prek run --all-files` (runs ruff, ruff-format, basedpyright) |

### Phase Requirements → Test Map

| Success Criterion | Behavior under test | Test Type | Automated Command | File Exists? |
|-------------------|---------------------|-----------|-------------------|-------------|
| SC-1: Empty pytest passes in fresh clone | Smoke test imports package, checks `__all__ == []` | unit | `uv run pytest tests/test_dbt_s_layer.py` | ❌ Wave 0 |
| SC-2: CI runs ruff + basedpyright + pytest (license scan DROPPED per CONTEXT) | Quality gate completes green | CI | `prek run --all-files && uv run pytest` | ❌ Wave 0 |
| SC-3: `dbt-s-layer --help` prints usage | Typer root help works | integration | `uv run dbt-s-layer --help` (smoke) + pytest CliRunner | ❌ Wave 0 |
| SC-3b: `dbt-s-layer doctor --help` prints usage | Subcommand help works | integration | `uv run dbt-s-layer doctor --help` + pytest CliRunner | ❌ Wave 0 |
| SC-4a: `doctor` detects dbtf present | Patch `shutil.which` to return a fake path; assert exit 0 and stdout contains "OK  dbtf found" | unit | `uv run pytest tests/test_doctor.py::test_doctor_dbtf_found` | ❌ Wave 0 |
| SC-4b: `doctor` detects dbtf absent | Patch `shutil.which` to return None; assert exit 1 and stdout contains install URL | unit | `uv run pytest tests/test_doctor.py::test_doctor_dbtf_missing` | ❌ Wave 0 |
| SC-4c: `doctor` reports metricflow version | Assert stdout contains the value returned by `importlib.metadata.version("metricflow")` | unit (may `importlib.metadata.distribution` stub if metricflow is not yet added to deps) | `uv run pytest tests/test_doctor.py::test_doctor_reports_metricflow` | ❌ Wave 0 |
| SC-5: Pinned metricflow family versions committed | After spike: `pyproject.toml` dependencies contain the three packages at pinned versions, AND `uv.lock` resolves cleanly | manual + lock-diff | `uv lock --locked` passes; grep `metricflow==` in pyproject.toml | ❌ Wave 0 (spike) |

### Additional Structural Validations (NOT typical test-runner tests, but CI-gated)

| What | How | Command |
|------|-----|---------|
| Scaffold correctness: required files exist | Shell check | `test -f src/dbt_s_layer/py.typed && test -f src/dbt_s_layer/__init__.py && test -f pyproject.toml && test -f .pre-commit-config.yaml && test -f .github/workflows/ci.yml` |
| pyproject.toml is valid | `uv lock --check` or `uv sync --dry-run --locked` | part of CI sync step |
| py.typed survives wheel build | `unzip -l dist/*.whl | grep "dbt_s_layer/py.typed"` | from release.yml template (SC-3 precursor) |
| CLI is pip-installed (SC-3 hard requirement) | `uv build && uv venv --python 3.11 verify-env && uv pip install --python verify-env/bin/python dist/*.whl && verify-env/bin/dbt-s-layer --help` | add as a validate-wheel CI job (mirror template's release.yml validate step) |
| Quality gate coverage | CI step `prek run --all-files` runs all hooks | built in to quality.yml |
| Spike evidence committed | `test -f spike/verify_stack.py && test -f spike/README.md` | shell gate |
| Deterministic lockfile | `uv.lock` is committed; CI uses `--locked` | `uv sync --locked --dev` |

### Sampling Rate
- **Per task commit:** `uv run pytest -x` (quick)
- **Per wave merge:** `prek run --all-files && uv run pytest` (full quality + test)
- **Phase gate:** All five success criteria pass manually + CI green on `main` + spike results committed

### Wave 0 Gaps
- [ ] `tests/__init__.py` — empty package marker
- [ ] `tests/conftest.py` — shared fixtures (may be empty in Phase 0)
- [ ] `tests/test_dbt_s_layer.py` — SC-1 smoke test
- [ ] `tests/test_doctor.py` — SC-4 doctor tests using typer's `CliRunner`
- [ ] `tests/test_cli_help.py` — SC-3 --help smoke using `CliRunner`
- [ ] `spike/verify_stack.py` + `spike/README.md` — SC-5 evidence
- [ ] `.github/workflows/ci.yml` + `quality.yml` — SC-2 CI wiring
- [ ] `.github/workflows/weekly.yml` — weekly compat check (per CONTEXT)
- [ ] `.github/workflows/release.yml` — OIDC publish (per CONTEXT)
- [ ] Framework install: `uv add --dev pytest pytest-cov ruff basedpyright` (done via `[dependency-groups] dev`)

### How to verify the repo scaffold is correct
1. `rm -rf .venv && uv sync --locked --dev` succeeds
2. `uv build` produces both `dist/*.whl` and `dist/*.tar.gz`
3. `unzip -l dist/*.whl | grep dbt_s_layer/py.typed` matches
4. `uv run dbt-s-layer --help` prints Typer help
5. `uv run dbt-s-layer doctor --help` prints subcommand help
6. `uv run pytest` passes

### How to verify CI is wired correctly
1. Push a branch; `ci.yml` runs and delegates to `quality.yml`
2. Matrix expands to all configured Python versions
3. Steps execute in order: checkout → setup-uv → sync → install prek → prek run → pytest → cache prune
4. Timeouts configured (15 min template default)
5. `weekly.yml` shows up in Actions tab; can be triggered via `workflow_dispatch`
6. **Absent:** no license scan step (deliberate, per CONTEXT)

### How to verify doctor command works
1. `uv run dbt-s-layer doctor` without `dbtf` on PATH → exit 1, stderr/stdout shows install URL
2. Install Fusion locally; re-run → exit 0, prints `dbtf` path + metricflow version
3. Automated: pytest with `monkeypatch.setattr(shutil, "which", lambda _: None)` for the missing-binary case
4. Automated: pytest with `monkeypatch.setattr(shutil, "which", lambda _: "/fake/dbtf")` + `importlib.metadata` patch for the present-binary case

### How to verify pinned versions are correct
1. `spike/verify_stack.py` runs cleanly: prints resolved versions + import-path checks for `PydanticSemanticManifest` and `SqlTable`
2. Committed `spike/results-<date>.txt` contains the stdout from step 1
3. `pyproject.toml` `[project.dependencies]` contains `metricflow==<version-from-spike>`, `dbt-semantic-interfaces==<version-from-spike>` (if dev-pin needed explicitly)
4. `uv lock --check` passes after commit — lockfile is stable
5. `uv run python -c "import metricflow; import dbt_semantic_interfaces; import metricflow_semantics; print('OK')"` runs green in CI

## Sources

### Primary (HIGH confidence)
- `cookiecutter-python-uv-library` template files (read directly during research):
  - `/Users/paul/Documents/Dev/Personal/cookiecutter-python-uv-library/{{ cookiecutter.project_slug }}/pyproject.toml`
  - `/Users/paul/Documents/Dev/Personal/cookiecutter-python-uv-library/{{ cookiecutter.project_slug }}/.github/workflows/quality.yml`
  - `/Users/paul/Documents/Dev/Personal/cookiecutter-python-uv-library/{{ cookiecutter.project_slug }}/.github/workflows/ci.yml`
  - `/Users/paul/Documents/Dev/Personal/cookiecutter-python-uv-library/{{ cookiecutter.project_slug }}/.github/workflows/release.yml`
  - `/Users/paul/Documents/Dev/Personal/cookiecutter-python-uv-library/{{ cookiecutter.project_slug }}/.github/workflows/weekly.yml`
  - `/Users/paul/Documents/Dev/Personal/cookiecutter-python-uv-library/{{ cookiecutter.project_slug }}/.pre-commit-config.yaml`
- `https://pypi.org/project/metricflow/` — metricflow 0.209.0, released 2025-10-14, Requires-Python <3.13,>=3.9, pydantic >=1.10,<3.0, dbt-semantic-interfaces==0.9.4.dev0
- `https://pypi.org/project/dbt-semantic-interfaces/` — 0.10.5 (2026-02-10), Requires-Python >=3.8, pydantic >=1.10,<3
- `https://pypi.org/project/prek/` — prek 0.3.10 (2026-04-21), Rust Git-hook manager compatible with pre-commit configs
- `https://pypi.org/project/typer/` — typer 0.24.1 (2026-02-21), Python >=3.10
- `https://docs.astral.sh/uv/concepts/build-backend/` — uv_build backend docs, recommended pin `>=0.11.7,<0.12`
- `https://docs.basedpyright.com/latest/configuration/config-files/` — basedpyright pyproject.toml config
- `https://docs.getdbt.com/docs/fusion/install-fusion` — dbt Fusion install URL (for doctor error message)
- `https://github.com/dbt-labs/metricflow/blob/main/pyproject.toml` — main-branch Python range `>=3.10,<3.14`

### Secondary (MEDIUM confidence)
- `https://github.com/j178/prek` — prek README covers `repo: local` + `language: system` compatibility
- `https://github.com/dbt-labs/metricflow/releases` — release notes citing 0.207.1 as the pydantic-v2-opt-in version and 0.206.0 as when metricflow-semantics was split out
- `.planning/phases/00-foundation-governance/00-CONTEXT.md` — user-locked decisions (authoritative for this phase)

### Tertiary (LOW confidence)
- `.planning/REQUIREMENTS.md` CLI-03 — the wording covers **future** doctor scope (manifest freshness + output-dir writability); Phase 0 scope is deliberately narrower per CONTEXT.md

## Metadata

**Confidence breakdown:**
- Standard stack (template-level): HIGH — all files read, all versions verified on PyPI
- metricflow family pinning: MEDIUM — PyPI metadata is authoritative but the dev-version pin (`==0.9.4.dev0`) is unusual; spike is the insurance
- Architecture patterns (Typer + doctor): HIGH — standard Typer idioms
- Common pitfalls: HIGH — metricflow Python-version ceiling is verified; prek-vs-pre-commit is verified; others are industry standard
- Validation architecture: HIGH — pytest + CliRunner is the idiomatic way to test Typer apps

**Research date:** 2026-04-21
**Valid until:** 2026-05-21 (30 days; bump if metricflow releases 0.210+ with Python 3.14 support — revisit matrix)
