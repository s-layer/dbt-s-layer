---
phase: 00-foundation-governance
verified: 2026-10-19
status: passed
---

# Phase 0 — Verification

Verified on 2026-10-19 against the ROADMAP success criteria, with a close-out review of everything Phase 0 shipped.

## Success criteria

| # | Criterion | Evidence | Result |
|---|-----------|----------|--------|
| 1 | `uv run pytest` passes in a fresh clone | `uv sync --locked --dev && uv run pytest`: 18 passed on 3.11, 3.12, 3.13 and 3.14 | ✅ |
| 2 | CI runs ruff, basedpyright and pytest (no license scan) | `quality.yml` runs `prek run --all-files` (ruff, ruff-format, basedpyright strict, uv-lock) then pytest on the 3.11–3.14 matrix; CI runs 2–4 green. No license step anywhere (D-04) | ✅ |
| 3 | `--help` and `doctor --help` work from a pip-installed build | Built the wheel with `uv build`, installed it into a clean venv: both print usage; wheel ships `py.typed` and the console-script entry point | ✅ |
| 4 | `doctor` detects `dbtf` and reports the metricflow version | Run against real binaries: Fusion 2.0.8 (installed as `dbt`) and dbt Core 1.12.6 both report OK and exit 0; with neither on PATH it prints both install links and exits 1. Unit tests cover detection order, version parsing, the Core 1.7 floor and exit codes 0/1/2; a CLI test proves the command exits with `run_doctor()`'s code | ✅ (widened, see 7) |
| 5 | metricflow-family pins committed after the spike settles Pydantic v1/v2 | `metricflow==0.213.0` pinned; `spike/results-2026-10-19.txt` exits 0; `PydanticSemanticManifest` is a `pydantic.v1` model (asserted in `tests/test_stack_surface.py`) | ✅ (re-pinned, see below) |

CLI-03 is only partly delivered by Phase 0, as planned: manifest freshness lands in Phase 1 and output-directory writability in Phase 3.

## Close-out review findings (fixed in this change)

1. **The metricflow pin was not enforced under uv.** `[tool.uv] override-dependencies` replaced the `metricflow==0.209.0` requirement with an unpinned one, so any `uv lock --upgrade` (and the weekly job) silently moved to 0.213.0 without the spike being re-run. pip honoured the pin, so pip users on Python 3.13/3.14 could not install the package at all (0.209.0 declares `Requires-Python <3.13`); CI missed this because uv ignores `Requires-Python` upper bounds.
   **Fix:** re-pinned to `metricflow==0.213.0`, which supports `<3.15` natively, and removed the `[tool.uv]` override and `prerelease = "allow"` blocks.
2. **metricflow 0.213.0 no longer uses `dbt-semantic-interfaces`.** It bundles its own fork as `metricflow_semantic_interfaces` and dropped the dependency. Pinning `dbt-semantic-interfaces` would load manifests into types the engine does not use.
   **Fix:** dropped the `dbt-semantic-interfaces` pin; the spike and tests now target `metricflow_semantic_interfaces`.
3. **The spike always exited 1**, including in its committed April evidence, because it counted the bundled `metricflow-semantics` namespace (which has no distribution metadata) as missing. A gate that always fails is no gate.
   **Fix:** the spike checks bundled namespaces by import only, adds `MetricFlowEngine.explain`, and exits 0 on the pinned stack.
4. **Nothing in CI guarded the spike's import surface** (the spike README claimed it ran in CI; it did not).
   **Fix:** `tests/test_stack_surface.py` asserts the installed metricflow equals the pin, the manifest model is pydantic v1, `SqlTable.from_string` parses 2- and 3-part names and round-trips `.sql`, and `MetricFlowEngine.explain` exists.
5. **No test covered the `doctor` command's exit status** (only its `--help`).
   **Fix:** parametrised CliRunner test over exit codes 0/1/2.
6. **`doctor` missed a standard Fusion install.** The Fusion installer names the binary `dbt` and only adds `dbtf` as a shell alias, which `shutil.which` cannot see.
7. **Fusion is no longer the only engine that works.** dbt Core 1.12.6 writes `semantic_manifest.json` for both the legacy and the new YAML spec; Fusion 2.0.8 ignores legacy-spec semantic models entirely (warning dbt1157, no semantic manifest). All three manifests load through `metricflow_semantics.model.dbt_manifest_parser.parse_manifest_from_dbt_generated_manifest` and render export SQL with `explain()`.
   **Fix (6 and 7, Anentropic chose "either engine" on 2026-10-19):** `doctor` now accepts Fusion (as `dbtf`, or `dbt` reporting 2.x) or dbt Core >= 1.7, and fails only when neither is found.
8. **Bookkeeping:** 00-04 had no SUMMARY and ROADMAP/STATE still read 0/4. Written and updated.

## Carried into Phase 1

- Confirm that a Fusion-emitted `semantic_manifest.json` (new YAML spec) loads into `metricflow_semantic_interfaces.PydanticSemanticManifest`. The Phase 0 spike is import-only.
