"""
Regenerate the committed dbt parse artifacts under tests/fixtures/artifacts/.

Each artifact set is produced by a real `dbt parse` of a fixture project:

    fusion_new   tests/fixtures/projects/new_spec     dbt Fusion, snowflake target
    core_new     tests/fixtures/projects/new_spec     dbt Core,   duckdb target
    core_legacy  tests/fixtures/projects/legacy_spec  dbt Core,   duckdb target
    fusion_export tests/fixtures/projects/export_spec   dbt Fusion, snowflake target
    core_export  tests/fixtures/projects/export_legacy dbt Core,   duckdb target
    fusion_snowflake tests/fixtures/projects/snowflake_spec dbt Fusion, snowflake target
    core_snowflake   tests/fixtures/projects/snowflake_legacy dbt Core, snowflake target

Projects with a `packages.yml` get `dbt deps` first (the snowflake_* projects download the
dbt_semantic_view package from GitHub). The dbt Core used must have the duckdb and
snowflake adapters.

`manifest.json` is trimmed to what dbt-s-layer reads (metadata, model, seed and
snapshot nodes, semantic models and saved queries, and the ids of the
dbt_semantic_view materializations when that package is installed), and every machine- or
run-specific value is dropped, so that regenerating with the same engine
versions produces no diff.

Usage:
    uv run python scripts/regen_fixtures.py --fusion PATH --core PATH

Without `--fusion`, `dbtf` is tried, then a `dbt` on PATH that reports Fusion 2.x.
Without `--core`, a `dbt` on PATH that reports dbt Core is used.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Literal

from dbt_s_layer._doctor import Engine, parse_engine_version

ROOT = Path(__file__).resolve().parent.parent
PROJECTS = ROOT / "tests" / "fixtures" / "projects"
ARTIFACTS = ROOT / "tests" / "fixtures" / "artifacts"

EngineKind = Literal["Fusion", "dbt Core"]

# (artifact set, project, engine kind, profile target)
SETS: list[tuple[str, str, EngineKind, str]] = [
    ("fusion_new", "new_spec", "Fusion", "snowflake"),
    ("core_new", "new_spec", "dbt Core", "duckdb"),
    ("core_legacy", "legacy_spec", "dbt Core", "duckdb"),
    ("fusion_export", "export_spec", "Fusion", "snowflake"),
    ("core_export", "export_legacy", "dbt Core", "duckdb"),
    ("fusion_snowflake", "snowflake_spec", "Fusion", "snowflake"),
    ("core_snowflake", "snowflake_legacy", "dbt Core", "snowflake"),
]

# Only these metadata keys are stable across machines and runs.
_METADATA_KEYS = ("adapter_type", "dbt_schema_version", "dbt_version", "project_name", "quoting")
# Node types a semantic model can read from.
_RELATION_NODE_TYPES = ("model", "seed", "snapshot")
# Macros kept (ids only): they tell dbt-s-layer whether dbt_semantic_view is installed.
_KEPT_MACRO_PREFIX = "macro.dbt_semantic_view.materialization_"
# Node keys that vary per run or per machine.
_VOLATILE_NODE_KEYS = ("created_at", "root_path", "build_path", "compiled_path", "deferred")


class RegenError(Exception):
    """A fixture set could not be regenerated."""


def _probe(path: str) -> Engine | None:
    result = subprocess.run(
        [path, "--version"], capture_output=True, text=True, timeout=30, check=False
    )
    if result.returncode != 0:
        return None
    return parse_engine_version(result.stdout, path)


def find_engine(kind: EngineKind, explicit: str | None) -> Engine:
    """Return the engine of `kind`, from an explicit path or from PATH."""
    candidates = [explicit] if explicit else (["dbtf", "dbt"] if kind == "Fusion" else ["dbt"])
    for name in candidates:
        path = shutil.which(name) if name else None
        if path is None:
            continue
        engine = _probe(path)
        if engine is not None and engine.kind == kind:
            return engine
    flag = "--fusion" if kind == "Fusion" else "--core"
    looked = explicit or " or ".join(f"`{c}`" for c in candidates)
    raise RegenError(f"no {kind} found ({looked}); pass its path with {flag}")


def _strip_volatile(node: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in node.items() if k not in _VOLATILE_NODE_KEYS}


def trim_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Reduce manifest.json to the keys dbt-s-layer reads, minus run-specific values."""
    metadata = manifest["metadata"]
    macros = {
        macro_id: {"unique_id": macro_id, "package_name": macro["package_name"]}
        for macro_id, macro in manifest.get("macros", {}).items()
        if macro_id.startswith(_KEPT_MACRO_PREFIX)
    }
    trimmed = {
        "metadata": {k: metadata[k] for k in _METADATA_KEYS if k in metadata},
        "nodes": {
            node_id: _strip_volatile(node)
            for node_id, node in manifest["nodes"].items()
            if node.get("resource_type") in _RELATION_NODE_TYPES
        },
        "semantic_models": {
            sm_id: _strip_volatile(sm) for sm_id, sm in manifest["semantic_models"].items()
        },
        "saved_queries": {
            sq_id: _strip_volatile(sq) for sq_id, sq in manifest["saved_queries"].items()
        },
    }
    # Only when there are any, so sets without the package keep their old shape.
    if macros:
        trimmed["macros"] = macros
    return trimmed


def _dump(data: Any) -> str:
    return json.dumps(data, sort_keys=True, indent=2) + "\n"


def _check_no_paths(text: str, scratch: Path, name: str) -> None:
    for needle in (str(ROOT), str(scratch), str(Path.home())):
        if needle in text:
            raise RegenError(f"{name} still contains a machine path ({needle})")


def _run(set_name: str, cmd: list[str], env: dict[str, str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)
    if result.returncode != 0:
        raise RegenError(
            f"{set_name}: `{' '.join(cmd)}` exited {result.returncode}\n"
            f"{result.stdout}\n{result.stderr}"
        )


def regen_set(set_name: str, project: str, engine: Engine, target: str) -> None:
    """Run `dbt parse` for one artifact set and write its trimmed artifacts."""
    project_dir = PROJECTS / project
    env = {**os.environ, "DO_NOT_TRACK": "1", "DBT_SEND_ANONYMOUS_USAGE_STATS": "false"}
    with tempfile.TemporaryDirectory(prefix=f"regen-{set_name}-") as tmp:
        scratch = Path(tmp)
        if (project_dir / "packages.yml").exists():
            _run(
                set_name,
                [
                    engine.path,
                    "deps",
                    "--project-dir",
                    str(project_dir),
                    "--profiles-dir",
                    str(project_dir / "profiles"),
                ],
                env,
            )
        cmd = [
            engine.path,
            "parse",
            "--project-dir",
            str(project_dir),
            "--profiles-dir",
            str(project_dir / "profiles"),
            "--target",
            target,
            "--target-path",
            str(scratch / "target"),
            "--log-path",
            str(scratch / "logs"),
        ]
        _run(set_name, cmd, env)
        target_dir = scratch / "target"
        semantic = json.loads((target_dir / "semantic_manifest.json").read_text())
        manifest = trim_manifest(json.loads((target_dir / "manifest.json").read_text()))

        out = ARTIFACTS / set_name
        out.mkdir(parents=True, exist_ok=True)
        files = {
            "semantic_manifest.json": _dump(semantic),
            "manifest.json": _dump(manifest),
            "PRODUCER": f"{engine.label}\n",
        }
        for name, text in files.items():
            _check_no_paths(text, scratch, f"{set_name}/{name}")
        for name, text in files.items():
            (out / name).write_text(text)
    print(f"{set_name}: {engine.label}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--fusion", help="path to the dbt Fusion binary")
    parser.add_argument("--core", help="path to a dbt Core `dbt` executable")
    args = parser.parse_args(argv)
    try:
        engines = {
            "Fusion": find_engine("Fusion", args.fusion),
            "dbt Core": find_engine("dbt Core", args.core),
        }
        for set_name, project, kind, target in SETS:
            regen_set(set_name, project, engines[kind], target)
    except RegenError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
