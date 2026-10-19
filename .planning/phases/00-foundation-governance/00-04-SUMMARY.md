---
phase: 00-foundation-governance
plan: 04
subsystem: infra
tags: [ci, github-actions, prek, pre-commit, basedpyright, ruff]

requires:
  - phase: 00-foundation-governance
    plan: 03
    provides: "Pinned metricflow family and uv lock the CI matrix resolves against"
provides:
  - "`.pre-commit-config.yaml` consumed by `prek` locally and in CI (builtin hygiene hooks, ruff + ruff-format, uv-lock, basedpyright strict)"
  - "`ci.yml` push orchestrator delegating to reusable `quality.yml` on Python 3.11/3.12/3.13/3.14"
  - "`release.yml` tag-triggered OIDC trusted publishing to PyPI + GitHub Release"
  - "`weekly.yml` Monday 07:00 UTC compat check (`uv sync --upgrade`) that opens a PR on failure"
affects: [all-future-phases (quality gate every PR runs through)]

key-files:
  created:
    - ".pre-commit-config.yaml"
    - ".github/workflows/ci.yml"
    - ".github/workflows/quality.yml"
    - ".github/workflows/release.yml"
    - ".github/workflows/weekly.yml"
  modified:
    - "src/dbt_s_layer/cli.py (strict-mode pyright fix surfaced by the new hook)"

requirements-completed: []

completed: 2026-04-21
---

# Phase 00 Plan 04: ci-precommit Summary

**prek hooks and four GitHub Actions workflows adapted from the cookiecutter template, with no license-scan step (CONTEXT.md D-04).**

## Accomplishments

- `.pre-commit-config.yaml` runs trailing-whitespace, end-of-file, json/toml/yaml checks, ruff (`--fix`), ruff-format, `uv-lock` and `uv run basedpyright` (strict). `.planning/` is excluded.
- `quality.yml` runs `uv sync --locked --dev` → `prek run --all-files` → `uv run pytest` on the full 3.11–3.14 matrix; `ci.yml` calls it on every push.
- `release.yml` publishes on `v*.*.*` tags via OIDC (no long-lived tokens) after checking the `py.typed` marker ships.
- `weekly.yml` re-resolves to the latest compatible deps every Monday and opens a PR if gates or tests fail.

## Checkpoint (human verify)

The plan's final checkpoint was a green CI run on push. This was satisfied after the fact: CI runs 2–4 on `gsd/v1.0-milestone` (2026-10-14) and every weekly compat run since are green. Later commits (2026-10-14) added the `claude-review.yml` workflow and GitHub App tokens for bot PRs; both are outside this plan's scope.

## Deviations

- The SUMMARY was not written at execution time; it was reconstructed on 2026-10-19 from the commits (`29485ad`, `473874d`) during the Phase 0 close-out review.
