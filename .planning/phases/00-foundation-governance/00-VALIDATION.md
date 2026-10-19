---
phase: 0
slug: foundation-governance
status: complete
nyquist_compliant: true
wave_0_complete: true
created: 2026-04-21
---

# Phase 0 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x |
| **Config file** | `pyproject.toml` (`[tool.pytest.ini_options]`) |
| **Quick run command** | `uv run pytest` |
| **Full suite command** | `uv run pytest --cov=dbt_s_layer` |
| **Estimated runtime** | ~5 seconds (Phase 0 suite is minimal) |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest`
- **After every plan wave:** Run `uv run pytest --cov=dbt_s_layer`
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 10 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 0-01-01 | 01 | 1 | — | — | N/A | structural | `test -f pyproject.toml && uv run python -c "import dbt_s_layer"` | ✅ | ✅ green |
| 0-01-02 | 01 | 1 | — | — | N/A | structural | `uv run pytest` | ✅ | ✅ green |
| 0-02-01 | 02 | 1 | — | — | N/A | structural | `test -f .pre-commit-config.yaml && prek run --all-files` | ✅ | ✅ green |
| 0-03-01 | 03 | 2 | CLI-03 | — | N/A | integration | `uv run dbt-s-layer --help` | ✅ | ✅ green |
| 0-03-02 | 03 | 2 | CLI-03 | — | N/A | integration | `uv run dbt-s-layer doctor --help` | ✅ | ✅ green |
| 0-03-03 | 03 | 2 | CLI-03 | — | N/A | unit | `uv run pytest tests/test_doctor.py` | ✅ | ✅ green |
| 0-04-01 | 04 | 3 | — | — | N/A | structural | `test -f spike/verify_stack.py && uv run python spike/verify_stack.py` | ✅ | ✅ green |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/__init__.py` — test package marker
- [ ] `tests/conftest.py` — shared fixtures (empty for Phase 0)
- [ ] `tests/test_dbt_s_layer.py` — smoke test (`assert hasattr(dbt_s_layer, "__all__")`)
- [ ] `tests/test_doctor.py` — CliRunner tests for doctor command (stubs, filled when CLI is built)
- [ ] `src/dbt_s_layer/__init__.py` — package entry point with `__all__ = []`
- [ ] `src/dbt_s_layer/py.typed` — PEP 561 marker

*These files must exist (even as stubs) before any task verification commands can run.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| CI workflow green on push | — | Requires GitHub Actions runner | Push branch and confirm all jobs green in Actions tab |
| `pip install dbt-s-layer` from built wheel prints `--help` | — | Requires isolated venv + built wheel | `uv build && pip install dist/*.whl && dbt-s-layer --help` |
| `dbt-s-layer doctor` detects real `dbtf` binary | CLI-03 | Requires dbtf installed on system | Install dbtf, run `dbt-s-layer doctor`, confirm version shown |
| Spike script confirms Pydantic v1/v2 status | — | Requires human reading of output | `uv run python spike/verify_stack.py` and review output section on pydantic |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 10s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
