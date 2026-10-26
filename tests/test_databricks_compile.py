"""
End to end on a real dbt engine: generated metric views compile on a Databricks target.

Copies a Databricks fixture project (`databricks_spec` for Fusion, `databricks_legacy`
for dbt Core, as in test_snowflake_compile.py) and runs `dbt parse`,
`dbt-s-layer generate`, `dbt parse` again, `dbt-s-layer check` and `dbt compile`. No
workspace is needed: dbt Core compiles with `--no-populate-cache`, Fusion compiles
offline (06-RESEARCH §5). This proves the Jinja, the refs, the DAG edges and that each
compiled definition is YAML, not that Databricks accepts it.

Engines come from `DBT_S_LAYER_FUSION` / `DBT_S_LAYER_CORE_DATABRICKS` (paths), else
from PATH (`dbtf` or a Fusion `dbt`; a dbt Core `dbt` with dbt-databricks >= 1.12.0,
which needs dbt-core < 1.12.6). Each engine's test skips when it is not found. CI
installs neither engine, so there it skips.
"""

from __future__ import annotations

import json
from io import StringIO
from typing import TYPE_CHECKING, Any

import pytest
from ruamel.yaml import YAML
from typer.testing import CliRunner

from dbt_s_layer.cli import app
from dbt_s_layer.project import load_project
from dbt_s_layer.refs import build_ref_map
from dbt_s_layer.semantic_views import build_semantic_views
from tests.engines import copy_project, find_engine, ok, run

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("kind", "project"), [("Fusion", "databricks_spec"), ("dbt Core", "databricks_legacy")]
)
def test_generated_metric_views_compile(kind: str, project: str, tmp_path: Path) -> None:
    variable = "DBT_S_LAYER_FUSION" if kind == "Fusion" else "DBT_S_LAYER_CORE_DATABRICKS"
    binary = find_engine(kind, variable, "databricks")
    if binary is None:
        pytest.skip(f"no {kind} engine with Databricks support (set {variable})")
    project_dir = copy_project(project, tmp_path)
    ok(run(binary, "deps", cwd=project_dir))

    ok(run(binary, "parse", "--target", "databricks", cwd=project_dir))
    runner = CliRunner()
    result = runner.invoke(app, ["generate", "--project-dir", str(project_dir)])
    assert result.exit_code == 0, result.output
    assert "metric_view materialization" not in result.output
    semantic_dir = project_dir / "models" / "semantic"
    generated = sorted(p.name for p in semantic_dir.glob("sem__*.sql"))
    assert "sem__orders.sql" in generated

    ok(run(binary, "parse", "--target", "databricks", cwd=project_dir))
    result = runner.invoke(app, ["check", "--project-dir", str(project_dir)])
    assert result.exit_code == 0, result.output

    compile_args = ["compile", "--target", "databricks"]
    if kind == "dbt Core":
        # Core connects to fill its relation cache unless told not to.
        compile_args.append("--no-populate-cache")
    ok(run(binary, *compile_args, cwd=project_dir))

    loaded = load_project(project_dir)
    refs = build_ref_map(loaded)
    manifest: dict[str, Any] = json.loads((project_dir / "target" / "manifest.json").read_text())
    nodes = {n["name"]: n for n in manifest["nodes"].values() if n["name"].startswith("sem__")}
    assert sorted(f"{name}.sql" for name in nodes) == generated
    for view in build_semantic_views(loaded, adapter="databricks").views:
        node = nodes[f"sem__{view.name}"]
        assert node["config"]["materialized"] == "metric_view"
        if view.name == "orders":
            # Members with an s_layer block get their Unity Catalog column tags after the build.
            assert node["config"]["view_update_via_alter"] is False
            hooks = [hook["sql"] for hook in node["config"]["post-hook"]]
            assert any("`revenue` set tags ('S_LAYER_KIND' = 'currency'" in h for h in hooks)
        expected = sorted(refs[t.semantic_model].node_id for t in view.tables)
        assert sorted(node["depends_on"]["nodes"]) == expected
        compiled = next((project_dir / "target" / "compiled").rglob(f"sem__{view.name}.sql"))
        text = compiled.read_text()
        assert "{{" not in text
        assert "ref(" not in text
        definition = YAML(typ="safe", pure=True).load(StringIO(text))  # pyright: ignore[reportUnknownMemberType]
        assert definition["version"] == 1.1
        assert definition["source"].count(".") == 2
