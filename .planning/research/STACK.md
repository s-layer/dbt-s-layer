# Technology Stack

**Project:** dbt-s-layer
**Researched:** 2026-04-18
**Mode:** Ecosystem (Stack dimension)

---

## Research Methodology Note

**IMPORTANT — this research was conducted with external verification tools blocked in the sandbox.**

- Context7 MCP: not available in this agent's tool set
- WebSearch, WebFetch, Brave, Exa, Firecrawl: all returned permission-denied
- No local checkouts of `metricflow`, `dbt-semantic-interfaces`, or `dbt-fusion` were present in the project tree (the design notes reference `_metricflow/...` paths that do not exist on disk)

All version numbers, API surface claims, and package relationships below are therefore derived from Claude's training data (cutoff January 2026) and the information already in the user's design notes. Every version claim is flagged with an explicit confidence level, and the `Verification Required Before Locking Versions` section at the bottom lists the exact queries an engineer (or a later research pass with network access) must run before the roadmap commits to pins.

The recommendations on **shape of the stack** (which libraries to use, which to avoid, how they fit together) are higher confidence than specific version numbers — the design notes plus training data give a coherent picture of the ecosystem, and the shape doesn't churn as fast as versions.

---

## Recommended Stack

### Core Runtime & Packaging

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| Python | 3.10 – 3.12 (require ≥3.10, test up to 3.13) | Package runtime | 3.10 is the floor set by modern metricflow releases; 3.13 is current stable (Oct 2024 GA) and CI should cover it. 3.9 EOL was Oct 2025. |
| `uv` | ≥ 0.5 | Dependency resolver, virtualenv manager, build frontend | Astral's uv is the de-facto 2025/2026 Python project tool — replaces pip, pip-tools, virtualenv, and pyenv with one 10-100x faster Rust binary. Native `uv.lock` gives reproducible builds. |
| `hatchling` | ≥ 1.25 | PEP 517 build backend | Zero-config, officially endorsed by PyPA, plays well with `uv build` and `pyproject.toml`. Avoids setuptools legacy quirks. |
| `pyproject.toml` | PEP 621 | Single source of metadata | All config (deps, build, tool settings) in one file. No `setup.py`, no `setup.cfg`. |

**Confidence:** HIGH for uv and hatchling (ecosystem-wide consolidation in 2024–2025); HIGH for Python range.

---

### CLI Framework

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| `typer` | ~0.15 (verify current) | CLI entrypoint (`dbt-s-layer generate`) | Type-hint-first API, Rich-powered help, auto-completion support, subcommand composition. Built on Click under the hood, so we inherit Click's maturity. Minimal boilerplate for the small command surface this tool needs. |
| `rich` | ~13 | Terminal output, progress, tables | Comes transitively with Typer anyway; gives us colored diagnostics, progress bars for codegen over large projects, and pretty tracebacks during development. |

**Alternatives rejected:**

- **`click`** directly — more verbose for the same result. Choose it only if we want zero type-hint magic. For a tool where we're already all-in on Pydantic/typing, Typer is the natural fit.
- **`argparse`** — no help rendering, no colors, verbose subcommand wiring. Fine for one-off scripts, wrong for a packaged CLI.
- **`fire`** — convenient for exploration, poor for a shipped product (signature becomes the contract in bad ways).

**Confidence:** HIGH on choice of Typer; MEDIUM on exact version (verify latest 0.x).

---

### Data Models & Validation

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| `pydantic` | v2 (≥ 2.7) | Our own config models, sentinel mapping structures, any user-facing data types | Pydantic v2 is the default everywhere in 2026. 5-50x faster than v1. Full type-hint inference. `model_validate_json()` replaces `parse_file`. |
| (transitive) `pydantic` v1 compat | via `pydantic.v1` shim | Required *only* because MetricFlow's `PydanticSemanticManifest` historically used Pydantic v1 models | See "Pydantic version risk" note below. |

**Pydantic version risk — CRITICAL to resolve early:**

The design note (`semantic-manifest-loading.md`) leaves this open: "Pydantic v1 or v2 for semantic manifest parsing." Based on training data, `metricflow` and `dbt-semantic-interfaces` historically pinned Pydantic v1 and used `PydanticSemanticManifest.parse_file(...)`. If they are **still on v1** as of 2026, we have three paths:

1. **Ship with Pydantic v2 for our own code, use `pydantic.v1` namespace for semantic-manifest objects.** Works cleanly since Pydantic 2.x exposes v1 as a subpackage. Our public config surface stays modern, the manifest parsing lives in a wrapper module. *Recommended path*.
2. **Ship with Pydantic v1 project-wide.** Simplest if metricflow is v1-only — but locks us out of modern tooling (FastAPI, modern starlette ecosystems) and is a poor signal to downstream users.
3. **Wait for metricflow to migrate.** Blocks us indefinitely.

If metricflow has already migrated to v2 by the time we start, this collapses to "use v2" and we skip the wrapper.

**Action:** First task of Phase 1 must be a 30-minute spike that installs the latest `metricflow` + `dbt-semantic-interfaces` and checks `pip show`/`pyproject.toml` for the Pydantic pin. The answer picks the path.

**Confidence:** HIGH on Pydantic v2 being the right target for our code; MEDIUM on metricflow's current Pydantic version (training data says v1; **must verify**).

---

### dbt Semantic Layer Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `metricflow` | latest 0.x (verify) | SQL rendering via `MetricFlowEngine.explain()` for saved-query exports | Required for export model codegen. Invoked in-process after manifest is loaded. |
| `metricflow-semantics` | matched to `metricflow` | `SqlTable`, `SqlTableNode`, semantic-layer primitives that metricflow builds on | Transitive dep of `metricflow`; we import `SqlTable.from_string` and related directly for the sentinel-rewrite logic. |
| `dbt-semantic-interfaces` | latest (verify) | `PydanticSemanticManifest` — the typed shape of `semantic_manifest.json` | Required to load the Fusion-emitted manifest into Python. Transitive via metricflow but we should pin it explicitly since we depend on its types in our own code. |
| (legacy fast-path only) `metricflow_semantic_interfaces.parsing.dir_to_model` | part of metricflow-semantics | Pure-Python YAML parser, *legacy spec only* | Optional fast-path for projects known to use legacy YAML spec (skip Fusion binary). Default path uses Fusion. |

**Critical notes from design docs:**

- The three packages (`metricflow`, `metricflow-semantics`, `dbt-semantic-interfaces`) evolve together. **Pin all three to a tested matrix.** Do not accept transitive resolution alone — a surprise `metricflow-semantics` minor bump has historically broken `SqlTable.from_string` internals in other dbt ecosystem tools.
- The design already identifies the narrow surfaces we depend on: `node_relation.relation_name` (string field), `SqlTable.from_string` (2 or 3 parts), `visit_table_node` (verbatim emission). Pin aggressively and review changelogs before bumping.
- **No pure-Python alternative for new-spec parsing exists.** `dir_to_model` is legacy-spec-only. Fusion binary is mandatory for new-spec projects.

**Confidence:** HIGH on the *shape* of the dependency (these three packages, pinned together); LOW on specific version numbers — **must verify current stable releases against PyPI before pinning**.

---

### Fusion Binary (Runtime, Not Python Dep)

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| `dbtf` (dbt Fusion binary) | Latest GA | Parse dbt projects with both legacy and new YAML specs → `target/semantic_manifest.json` | Only way to normalize both YAML specs without ELv2 contamination. Invoked via subprocess — no Python bindings exist. |

**Integration approach:**
- At runtime, our CLI checks `which dbtf` (or a configurable path) before running.
- If missing, emit a clear error message with install instructions (link to `https://docs.getdbt.com/docs/fusion/install-fusion`).
- Pin a **minimum Fusion version** once we've tested against one; track the artifact schema carefully since Fusion's `SemanticManifest` and metricflow's `PydanticSemanticManifest` are independent codebases (see PITFALLS.md).
- **We do not install, vendor, or wrap the binary.** Users install it themselves. We document the dependency prominently.

**Confidence:** HIGH on the approach (already decided in design notes); MEDIUM on "latest GA" — Fusion was in preview in early 2025 and moved toward GA through the year; verify GA status and exact minimum we need.

---

### Code Generation & Templating

| Library | Version | Purpose | Why |
|---------|---------|---------|-----|
| `jinja2` | ≥ 3.1 | Template rendering for generated `.sql` and `.yml` files | Stdlib of dbt tooling. Users already have it via dbt. No reason to use anything else. We use it for *our* templates, not to execute dbt Jinja. |
| `ruamel.yaml` | ≥ 0.18 | Round-trip-safe YAML writing for `_semantic_views.yml` | Preserves comments, ordering, and style when writing the generated schema file. `PyYAML` does not. Matters when users diff generated files. |
| `sqlglot` (optional, deferred) | — | Dialect-aware SQL formatting | Nice-to-have for making generated SQL pretty. Not strictly required — metricflow's output is already executable. Defer unless we see ugly diffs that hurt review. |

**Alternatives rejected:**

- **Plain string templating** — painful to escape `{{` and `}}` in SQL output that contains dbt Jinja. Jinja2 with `variable_start_string='<<'` or similar sidesteps the problem cleanly. Or use Python f-strings for simple wrappers and Jinja2 only for the yml.
- **`PyYAML`** — loses comments and field ordering on round-trip. Unacceptable for a tool where the output is committed and reviewed.

**Confidence:** HIGH.

---

### Subprocess & Process Control

| Library | Version | Purpose | Why |
|---------|---------|---------|-----|
| `subprocess` (stdlib) | — | Invoke `dbtf parse` | Stdlib. No need for `sh` or `plumbum`. Use `subprocess.run(..., check=True, capture_output=True)` with a small wrapper that surfaces Fusion's stderr on failure. |
| `shutil.which` (stdlib) | — | Detect Fusion binary on PATH | Standard. |

**Confidence:** HIGH.

---

### Testing

| Library | Version | Purpose | Why |
|---------|---------|---------|-----|
| `pytest` | ≥ 8 | Test runner | Standard. Avoid `unittest`. |
| `pytest-xdist` | ≥ 3 | Parallel tests | Codegen tests are IO-bound; parallelism helps. |
| `syrupy` | ≥ 4 | **Snapshot testing of generated SQL and YAML** | Critical for this project. Codegen output is inherently snapshot-shaped — we want to see exact diffs when metricflow or our templates change. `pytest-snapshot` is an alternative but syrupy has better UX for nested structures and supports single-file snapshots. |
| `pytest-golden` | — | Alternative to syrupy if we want `.expected.sql` files on disk rather than `__snapshots__/`-style | Consider if reviewers prefer seeing snapshot files alongside the test fixtures. |
| `dirty-equals` | ≥ 0.7 | Approximate matching for manifest objects | For "this field contains a timestamp, don't care which" style assertions without writing regex. |
| `pytest-cov` | ≥ 5 | Coverage | Standard CI integration. |

**Snapshot-testing strategy (central for this project):**

Because our output is code that's committed and reviewed, test cases should take the form:
```
tests/
  fixtures/
    projects/
      minimal_legacy/      # dbt project with legacy-spec YAML
      minimal_new/         # dbt project with new-spec YAML
      multi_export/        # one saved_query, multiple exports
      cross_project_ref/   # uses __XREF__
    expected/
      minimal_legacy/
        sem__orders.sql
        exp__orders_by_month.sql
        _semantic_views.yml
```

Tests run the generator against each fixture project and assert byte-equality (or syrupy-snapshot-equality) against the `expected/` tree. A `--update-snapshots` flag regenerates them during development.

For the Fusion-binary step, **mock the binary in CI** by pre-generating `semantic_manifest.json` files and committing them under `fixtures/projects/<name>/target/` — that way CI doesn't need Fusion installed. A separate **integration** test suite (run nightly, not on every PR) invokes real Fusion against the same fixtures and asserts the manifest is unchanged.

**Confidence:** HIGH on pytest + snapshot testing as the approach; MEDIUM on syrupy specifically vs pytest-golden (either works).

---

### Linting, Formatting, Type Checking

| Tool | Version | Purpose | Why |
|------|---------|---------|-----|
| `ruff` | ≥ 0.8 | Linting + formatting (replaces black, isort, flake8, pylint) | Standard 2025/2026 choice. One tool, one config block, sub-second runs. |
| `mypy` | ≥ 1.13 | Type checking | Still the default. `pyright` is an alternative (faster, stricter default rules) — pick mypy unless team preference says otherwise. |
| `pre-commit` | ≥ 3.8 | Hook runner for our own hooks AND for users of this tool | **Double role:** we use it for our own CI checks; we also ship a pre-commit hook that users install in their dbt project. Our repo's `.pre-commit-hooks.yaml` is the public interface for the latter. |

**Confidence:** HIGH. Ruff + mypy + pre-commit is the 2026 default baseline.

---

### Pre-commit Hook Contract (Public Surface)

Not a library choice, but a stack decision:

- Ship a `.pre-commit-hooks.yaml` at the repo root defining a `dbt-s-layer-generate` hook.
- Users add to their `.pre-commit-config.yaml`:
  ```yaml
  - repo: https://github.com/anentropic/dbt-s-layer
    rev: v0.1.0
    hooks:
      - id: dbt-s-layer-generate
        files: '(semantic_models|metrics|saved_queries).*\.ya?ml$'
  ```
- The hook invokes our CLI in the project dir, which regenerates files under `models/semantic/`.
- **Hook must be idempotent** — running it twice in a row produces zero diff. This is a testable invariant and goes in the snapshot test suite.
- **Hook must fail fast if Fusion is missing** with a clear remediation message.

**Confidence:** HIGH (standard pre-commit pattern).

---

### CI / Release

| Tool | Version | Purpose | Why |
|------|---------|---------|-----|
| GitHub Actions | — | CI | Ubiquitous. Free for OSS. |
| `actions/setup-python` | v5 | Python matrix | Standard. |
| `astral-sh/setup-uv` | v3 | Install uv in CI | Faster than pip for install steps. |
| `pypa/gh-action-pypi-publish` | v1 | Trusted publishing to PyPI | OIDC-based, no long-lived tokens. Current 2026 best practice. |
| `release-please` or `commitizen` | — | Changelog + version bumps | Either works; pick `release-please` if the team uses Conventional Commits, `commitizen` if not. |

**Confidence:** HIGH.

---

## Alternatives Considered

| Category | Recommended | Alternative | Why Not |
|----------|-------------|-------------|---------|
| Package manager | `uv` | Poetry | Poetry is fine but slower, less uniform, and the ecosystem is consolidating around uv through 2025. New projects should default to uv. |
| Package manager | `uv` | pip + pip-tools | Works but is now the legacy path for new projects. |
| Build backend | `hatchling` | `setuptools` | Setuptools works but has legacy baggage (src-layout config verbosity, `setup.py` expectations). Hatchling is simpler. |
| Build backend | `hatchling` | `pdm-backend`, `flit` | Both fine; hatchling has the widest PyPA endorsement. |
| CLI | `typer` | `click` | Typer gives us auto-help from type hints; Click needs manual decorators. For our surface Typer wins. |
| CLI | `typer` | `argparse` | No colors, poor help rendering, painful subcommands. |
| Data models | Pydantic v2 | `attrs` + `cattrs` | Great alternative, but we *must* integrate with Pydantic-v1-based metricflow types — sticking with Pydantic across our own code simplifies the boundary. |
| Data models | Pydantic v2 | `dataclasses` + manual validation | Fine for tiny configs; wrong for user-facing YAML/JSON parsing where we want error messages. |
| YAML writing | `ruamel.yaml` | `PyYAML` | PyYAML loses comments and field order on round-trip. |
| Snapshot testing | `syrupy` | `pytest-snapshot` | Both fine; syrupy's UX is better for nested data. |
| Snapshot testing | `syrupy` | Hand-written `assert_equals` on file contents | Works but noisy — snapshot libraries automate diff display and update. |
| Manifest parsing (legacy) | Fusion binary (default) | `dir_to_model` Python-only | Design already decided: fast-path, not default. New-spec projects force Fusion. |
| Manifest parsing (new) | Fusion binary | PyO3 Fusion bindings | Design already rejected: ELv2 license contamination, no upstream bindings exist, maintenance burden. |
| SQL rendering | `metricflow.MetricFlowEngine.explain()` | Hand-rolling the SQL compiler | Reinventing metricflow = years of work and missing dialect coverage. Non-starter. |
| DAG integration | Codegen to disk | `dbt.plugins.get_nodes()` hook | Design already rejected: undocumented, Fusion-incompatible. |

---

## Installation (target `pyproject.toml` shape)

```toml
[project]
name = "dbt-s-layer"
description = "Warehouse-native semantic views and exports for dbt Core and Fusion"
license = "Apache-2.0"
requires-python = ">=3.10"
dynamic = ["version"]

dependencies = [
    # Pin all three metricflow-family packages together; verify exact versions
    "metricflow>=0.207,<0.300",               # VERIFY current stable
    "metricflow-semantics>=0.7,<0.10",        # VERIFY current stable
    "dbt-semantic-interfaces>=0.7,<0.10",     # VERIFY current stable
    # CLI + core
    "typer[all]>=0.15",
    "pydantic>=2.7",
    "ruamel.yaml>=0.18",
    "jinja2>=3.1",
]

[project.optional-dependencies]
dev = [
    "pytest>=8",
    "pytest-xdist>=3",
    "pytest-cov>=5",
    "syrupy>=4",
    "dirty-equals>=0.7",
    "ruff>=0.8",
    "mypy>=1.13",
    "pre-commit>=3.8",
]

[project.scripts]
dbt-s-layer = "dbt_s_layer.cli:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.ruff]
target-version = "py310"
line-length = 100

[tool.mypy]
strict = true
python_version = "3.10"
```

Installation for developers:
```bash
# Install uv if not already present
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone + install
git clone https://github.com/anentropic/dbt-s-layer
cd dbt-s-layer
uv sync --all-extras
uv run pre-commit install

# Run tests
uv run pytest
```

Installation for end users:
```bash
# 1. Install Fusion binary (one-time)
curl -fsSL https://public.cdn.getdbt.com/fs/install/install.sh | sh -s --

# 2. Install our package
uv add --dev dbt-s-layer        # or: pip install dbt-s-layer

# 3. Wire up pre-commit
# (users add the hook stanza to their .pre-commit-config.yaml)
pre-commit install
```

---

## Verification Required Before Locking Versions

Before the roadmap phase that creates `pyproject.toml`, run these checks (in a shell with network or via Context7 when available):

| Package | What to verify | Why it matters |
|---------|----------------|----------------|
| `metricflow` | Latest version on PyPI; Pydantic v1 vs v2; Python version support | Core dependency; Pydantic choice cascades to everything else |
| `metricflow-semantics` | Version that matches the chosen `metricflow`; `SqlTable.from_string` still at same module path | Sentinel-rewrite approach depends on this exact symbol |
| `dbt-semantic-interfaces` | Latest; whether it's still a separate package or merged into metricflow-semantics | Our models depend on `PydanticSemanticManifest` |
| dbt Fusion (`dbtf`) | Current release stream; whether GA is declared; minimum version we should require; install script URL | Runtime dependency and docs link |
| `typer` | Latest; any 0.x → 1.0 transition pending | Affects API we commit to |
| `pydantic` v1 availability inside v2 | Confirm `from pydantic.v1 import BaseModel` still ships in latest v2 | Fallback path if metricflow is still v1 |
| `ruamel.yaml` | Latest | Comment-preserving YAML still at 0.18+ |
| `syrupy` vs `pytest-golden` vs `pytest-snapshot` | Current activity, Python 3.13 support, serializer options for SQL | Test DX depends on this choice |
| Python 3.13 | Compatibility of all the above | CI matrix |

**A 1-hour spike in Phase 1 should answer all of these.** The answers go back into this file as a revision before any version is pinned in `pyproject.toml`.

---

## Sources

- `/Users/paul/Documents/Dev/Personal/dbt-s-layer/.planning/PROJECT.md` — project goals and constraints (read during research)
- `/Users/paul/Documents/Dev/Personal/dbt-s-layer/_notes/semantic-manifest-loading.md` — detailed architecture for metricflow integration, sentinel rewrite approach, source-level references into metricflow internals
- `/Users/paul/Documents/Dev/Personal/dbt-s-layer/_notes/dbt-semantic-views-codegen.md` — codegen design and dbt integration mechanics
- Claude training data (cutoff Jan 2026) — for general Python ecosystem trends (uv, ruff, Pydantic v2, Typer, syrupy). **Specific version numbers from training data are marked LOW confidence and flagged in the verification table above.**

External sources (Context7, PyPI, official docs) were **not available** during this research pass. See "Research Methodology Note" at top.

---

## Confidence Summary

| Recommendation | Confidence | Notes |
|----------------|------------|-------|
| Use `uv` + `hatchling` + `pyproject.toml` | HIGH | Ecosystem consensus |
| Use `typer` for CLI | HIGH | Best fit for type-hinted small-surface CLI |
| Use Pydantic v2 for our own code | HIGH | Modern default |
| metricflow uses Pydantic v1 (needing compat shim) | LOW | **Must verify** — if v2 already migrated, simplifies stack |
| Specific versions of metricflow / metricflow-semantics / dbt-semantic-interfaces | LOW | **Must verify against PyPI** |
| Fusion binary is mandatory for new-spec parsing | HIGH | Established in design notes |
| Snapshot-test generated SQL with `syrupy` | MEDIUM-HIGH | Approach is right; specific library can flex |
| Mock Fusion in unit tests, real Fusion in nightly integration | HIGH | Only sensible CI shape |
| Ship `.pre-commit-hooks.yaml` as public surface | HIGH | Standard pattern |
| Ruff + mypy + pre-commit toolchain | HIGH | 2026 default |
| `ruamel.yaml` over PyYAML for generated `.yml` | HIGH | Comment/order preservation matters for committed files |
