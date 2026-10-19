"""Tests for `dbt-s-layer pre-commit`, the pre-commit hook's entry point (HOOK-01)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from typer.testing import CliRunner

from dbt_s_layer._hook import is_semantic_yaml, select_projects
from dbt_s_layer.cli import app
from tests.conftest import ARTIFACTS

if TYPE_CHECKING:
    from click.testing import Result

    from tests.conftest import MakeProject

runner = CliRunner()
OUTPUT = Path("models") / "semantic"


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text))
    return path


# --- which files trigger a run --------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # new spec: semantic_model on a model, metrics nested under it
        """\
        models:
          - name: orders
            semantic_model:
              enabled: true
        """,
        """\
        models:
          - name: orders
            metrics:
              - name: revenue
        """,
        # legacy spec
        """\
        semantic_models:
          - name: orders
        """,
        "metrics:\n  - name: revenue\n",
        "saved_queries:\n  - name: q\n",
        # a list item that is itself the key
        "models:\n  - semantic_model: {enabled: true}\n    name: orders\n",
        # spacing before the colon
        "saved_queries :\n  - name: q\n",
        # a model that is only the time spine still feeds semantic_manifest.json
        """\
        models:
          - name: all_days
            time_spine:
              standard_granularity_column: date_day
        """,
    ],
)
def test_semantic_yaml_is_recognised(text: str, tmp_path: Path) -> None:
    assert is_semantic_yaml(_write(tmp_path / "models" / "x.yml", text))


@pytest.mark.parametrize(
    "text",
    [
        """\
        models:
          - name: orders
            description: "metrics: not a key"
            columns:
              - name: id
                data_tests: [unique]
        """,
        "on: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n",
        "# metrics: commented out\nmodels: []\n",
        "my_metrics:\n  - a\n",
        "",
    ],
)
def test_other_yaml_is_not(text: str, tmp_path: Path) -> None:
    assert not is_semantic_yaml(_write(tmp_path / "models" / "x.yml", text))


def test_yaml_extension_variants(tmp_path: Path) -> None:
    assert is_semantic_yaml(_write(tmp_path / "a.yaml", "metrics:\n  - name: m\n"))
    assert not is_semantic_yaml(_write(tmp_path / "a.sql", "metrics:\n"))
    assert not is_semantic_yaml(tmp_path / "missing.yml")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository with a dbt project at `analytics/` and one at the root of `other/`."""
    root = tmp_path / "repo"
    _write(root / "analytics" / "dbt_project.yml", "name: a\n")
    _write(root / "analytics" / "models" / "orders.yml", "models:\n  - name: o\n    metrics: []\n")
    _write(root / "analytics" / "models" / "plain.yml", "models:\n  - name: p\n")
    _write(root / "analytics" / "models" / "semantic" / "exp__q__e.sql", "select 1\n")
    _write(root / "analytics" / "models" / "plain.sql", "select 1\n")
    _write(root / "other" / "dbt_project.yml", "name: b\n")
    _write(root / "other" / "models" / "m.yaml", "saved_queries: []\n")
    _write(root / "docs" / "metrics.yml", "metrics: []\n")
    _write(root / ".github" / "workflows" / "ci.yml", "jobs: {}\n")
    return root


def _select(repo: Path, *files: str, project_dir: Path | None = None) -> list[Path]:
    return select_projects(
        [Path(f) for f in files], project_dir=project_dir, output_dir=OUTPUT, cwd=repo
    )


def test_a_semantic_file_selects_its_nearest_project(repo: Path) -> None:
    assert _select(repo, "analytics/models/orders.yml") == [repo / "analytics"]


def test_non_semantic_files_select_nothing(repo: Path) -> None:
    assert (
        _select(
            repo,
            "analytics/models/plain.yml",
            "analytics/models/plain.sql",
            ".github/workflows/ci.yml",
        )
        == []
    )


def test_a_semantic_file_under_no_project_is_ignored(repo: Path) -> None:
    assert _select(repo, "docs/metrics.yml") == []


def test_dbt_project_yml_and_generated_files_select_their_project(repo: Path) -> None:
    assert _select(repo, "analytics/dbt_project.yml") == [repo / "analytics"]
    assert _select(repo, "analytics/models/semantic/exp__q__e.sql") == [repo / "analytics"]


def test_a_local_package_selects_the_project_that_installs_it(tmp_path: Path) -> None:
    # A package's dbt_project.yml names no profile: it cannot be parsed on its own,
    # and its semantic models reach the root project's semantic_manifest.json.
    root = tmp_path / "repo"
    _write(root / "analytics" / "dbt_project.yml", "name: a\nprofile: a\n")
    _write(root / "analytics" / "packages" / "dep" / "dbt_project.yml", "name: dep\n")
    _write(root / "analytics" / "packages" / "dep" / "models" / "s.yml", "semantic_models: []\n")
    _write(root / "lib" / "dbt_project.yml", "name: lib\n")
    _write(root / "lib" / "models" / "s.yml", "semantic_models: []\n")
    assert _select(root, "analytics/packages/dep/models/s.yml") == [root / "analytics"]
    # With no project above it that names a profile, the nearest one is used.
    assert _select(root, "lib/models/s.yml") == [root / "lib"]


@pytest.mark.parametrize("dependencies_file", ["packages.yml", "dependencies.yml"])
def test_a_local_package_that_names_a_profile_selects_the_project_that_installs_it(
    dependencies_file: str, tmp_path: Path
) -> None:
    # `dbt init` writes a `profile:` into every project, packages included; what makes
    # `dep` a package is the root project listing it as a local package.
    root = tmp_path / "repo"
    _write(root / "analytics" / "dbt_project.yml", "name: a\nprofile: a\n")
    _write(root / "analytics" / dependencies_file, "packages:\n  - local: packages/dep/\n")
    _write(root / "analytics" / "packages" / "dep" / "dbt_project.yml", "name: dep\nprofile: d\n")
    _write(root / "analytics" / "packages" / "dep" / "models" / "s.yml", "semantic_models: []\n")
    assert _select(root, "analytics/packages/dep/models/s.yml") == [root / "analytics"]


def test_a_nested_project_that_installs_its_parent_is_its_own_project(tmp_path: Path) -> None:
    # The usual dbt package repository: the package at the root, and an
    # integration_tests project inside it that installs the package with `local: ../`.
    root = tmp_path / "repo"
    _write(root / "dbt_project.yml", "name: pkg\nprofile: pkg\n")
    _write(root / "models" / "s.yml", "semantic_models: []\n")
    tests_dir = root / "integration_tests"
    _write(tests_dir / "dbt_project.yml", "name: pkg_tests\nprofile: pkg_tests\n")
    _write(tests_dir / "packages.yml", "packages:\n  - local: ../\n")
    _write(tests_dir / "models" / "t.yml", "semantic_models: []\n")
    assert _select(root, "integration_tests/models/t.yml") == [tests_dir]
    assert _select(root, "models/s.yml") == [root]


def test_projects_are_sorted_and_unique(repo: Path) -> None:
    files = ("other/models/m.yaml", "analytics/models/orders.yml", "analytics/dbt_project.yml")
    assert _select(repo, *files) == [repo / "analytics", repo / "other"]


def test_project_dir_pins_one_project(repo: Path) -> None:
    pinned = repo / "analytics"
    assert _select(repo, "other/models/m.yaml", project_dir=pinned) == []
    assert _select(repo, "analytics/models/orders.yml", project_dir=pinned) == [pinned]
    assert _select(repo, "analytics/models/orders.yml", project_dir=Path("analytics")) == [pinned]


def test_no_files_selects_the_project_dir_or_the_working_directory(repo: Path) -> None:
    assert _select(repo) == [repo]
    assert _select(repo, project_dir=repo / "other") == [repo / "other"]


def test_a_custom_output_dir_is_honoured(repo: Path) -> None:
    _write(repo / "analytics" / "gen" / "sem__orders.sql", "x\n")
    files = [Path("analytics/gen/sem__orders.sql")]
    assert select_projects(files, project_dir=None, output_dir=Path("gen"), cwd=repo) == [
        repo / "analytics"
    ]
    assert select_projects(files, project_dir=None, output_dir=OUTPUT, cwd=repo) == []


# --- running dbt parse and generate ---------------------------------------------

FAKE_DBT = """\
#!{python}
# A stand-in for `dbt parse`: logs its name, arguments and directory, then writes
# FAKE_DBT_ARTIFACTS, or the artifact set named like the directory, into ./target.
import json, os, shutil, sys
from pathlib import Path
if sys.argv[1:] == ["--version"]:
    print({version!r})
    sys.exit(0)
with open(os.environ["FAKE_DBT_LOG"], "a") as log:
    log.write(json.dumps([Path(sys.argv[0]).name, sys.argv[1:], os.getcwd()]) + "\\n")
if os.environ.get("FAKE_DBT_FAIL"):
    print("Parsing failed: something is wrong in orders.yml")
    sys.exit(2)
root = Path(os.environ["FAKE_DBT_ROOT"])
source = Path(os.environ.get("FAKE_DBT_ARTIFACTS") or root / Path.cwd().name)
target = Path("target")
target.mkdir(exist_ok=True)
for name in ("semantic_manifest.json", "manifest.json"):
    shutil.copyfile(source / name, target / name)
"""


# What each engine prints for `--version`.
VERSIONS = {
    "Fusion": "dbt-fusion 2.0.8",
    "dbt Core": "Core:\n  - installed: 1.12.6\n  - latest:    1.12.6 - Up to date!\n",
}


class FakeDbt:
    def __init__(self, bin_dir: Path, log: Path) -> None:
        self.bin_dir = bin_dir
        self.log = log

    def install(self, name: str, kind: str | None = None) -> Path:
        """Install a fake engine; `dbtf` is Fusion and anything else dbt Core, unless `kind`."""
        kind = kind or ("Fusion" if name == "dbtf" else "dbt Core")
        version = VERSIONS[kind]
        path = self.bin_dir / name
        path.write_text(FAKE_DBT.format(python=sys.executable, version=version))
        path.chmod(0o755)
        return path

    def calls(self) -> list[tuple[str, list[str], str]]:
        if not self.log.exists():
            return []
        calls: list[tuple[str, list[str], str]] = []
        for line in self.log.read_text().splitlines():
            name, args, cwd = cast("tuple[str, list[str], str]", json.loads(line))
            calls.append((name, args, cwd))
        return calls


@pytest.fixture
def fake_dbt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeDbt:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = FakeDbt(bin_dir, tmp_path / "dbt-calls.log")
    monkeypatch.setenv("PATH", str(bin_dir))
    # No developer's ~/.dbt/profiles.yml or DBT_PROFILES_DIR reaches the parse arguments.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
    monkeypatch.setenv("FAKE_DBT_LOG", str(fake.log))
    monkeypatch.setenv("FAKE_DBT_ROOT", str(ARTIFACTS))
    monkeypatch.delenv("FAKE_DBT_ARTIFACTS", raising=False)
    monkeypatch.delenv("FAKE_DBT_FAIL", raising=False)
    return fake


@pytest.fixture
def project(make_project: MakeProject, monkeypatch: pytest.MonkeyPatch) -> Path:
    """core_export at `<repo>/core_export`, the working directory being the repo root."""
    project_dir = make_project("core_export")
    monkeypatch.chdir(project_dir.parent)
    return project_dir


SEMANTIC_FILE = "core_export/models/marts/orders.yml"


def _edit(project: Path) -> None:
    """Make the semantic YAML newer than the artifacts, as an edit before commit does."""
    now = time.time()
    for name in ("semantic_manifest.json", "manifest.json"):
        os.utime(project / "target" / name, (now - 60, now - 60))
    yaml = project / "models" / "marts" / "orders.yml"
    os.utime(yaml, (now - 30, now - 30))


def _hook(*args: str | Path) -> Result:
    return runner.invoke(app, ["pre-commit", *(str(a) for a in args)])


def test_first_run_writes_and_fails_second_run_passes(project: Path, fake_dbt: FakeDbt) -> None:
    fake_dbt.install("dbt")
    _edit(project)
    first = _hook(SEMANTIC_FILE)
    assert first.exit_code == 1, first.output
    out = project / OUTPUT
    assert (out / "exp__revenue_by_day__exp_revenue_by_day.sql").is_file()
    assert "new: core_export/models/semantic/exp__revenue_by_day__exp_revenue_by_day.sql" in (
        first.output
    )
    assert "git add" in first.output
    assert fake_dbt.calls() == [("dbt", ["parse"], str(project))]

    second = _hook(SEMANTIC_FILE)
    assert second.exit_code == 0, second.output
    assert "up to date" in second.output
    assert len(fake_dbt.calls()) == 2


def test_a_hand_edited_generated_file_is_restored(project: Path, fake_dbt: FakeDbt) -> None:
    fake_dbt.install("dbt")
    assert _hook(SEMANTIC_FILE).exit_code == 1
    generated = project / OUTPUT / "exp__revenue_by_day__exp_revenue_by_day.sql"
    original = generated.read_text()
    generated.write_text(original + "-- edited\n")
    result = _hook("core_export/models/semantic/exp__revenue_by_day__exp_revenue_by_day.sql")
    assert result.exit_code == 1, result.output
    assert "changed: core_export/models/semantic/exp__revenue_by_day__exp_revenue_by_day.sql" in (
        result.output
    )
    assert generated.read_text() == original


def test_a_removed_export_removes_its_file(
    project: Path, fake_dbt: FakeDbt, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_dbt.install("dbt")
    assert _hook(SEMANTIC_FILE).exit_code == 1
    edited = tmp_path / "edited"
    shutil.copytree(ARTIFACTS / "core_export", edited)
    path = edited / "semantic_manifest.json"
    raw = json.loads(path.read_text())
    for sq in raw["saved_queries"]:
        if sq["name"] == "cumulative_revenue":
            sq["exports"] = []
    path.write_text(json.dumps(raw))
    monkeypatch.setenv("FAKE_DBT_ARTIFACTS", str(edited))
    result = _hook(SEMANTIC_FILE)
    assert result.exit_code == 1, result.output
    assert "removed: core_export/models/semantic/exp__cumulative_revenue__exp_cumulative.sql" in (
        result.output
    )


def test_irrelevant_files_do_nothing(project: Path, fake_dbt: FakeDbt) -> None:
    fake_dbt.install("dbt")
    _write(project / "models" / "plain.yml", "models:\n  - name: p\n")
    result = _hook("core_export/models/plain.yml", "README.md")
    assert result.exit_code == 0, result.output
    assert fake_dbt.calls() == []
    assert not (project / OUTPUT).exists()


def test_irrelevant_files_do_not_load_metricflow(project: Path) -> None:
    _write(project / "models" / "plain.yml", "models:\n  - name: p\n")
    script = (
        "import sys\n"
        "for name in ('metricflow', 'metricflow_semantics', 'metricflow_semantic_interfaces'):\n"
        "    sys.modules[name] = None\n"
        "from dbt_s_layer.cli import app\n"
        "app(sys.argv[1:])\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, "pre-commit", "core_export/models/plain.yml"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


def test_target_and_profiles_dir_reach_dbt_parse(project: Path, fake_dbt: FakeDbt) -> None:
    fake_dbt.install("dbt")
    result = _hook("--target", "duckdb", "--profiles-dir", "core_export/profiles", SEMANTIC_FILE)
    assert result.exit_code == 1, result.output
    profiles = str(project / "profiles")
    assert fake_dbt.calls() == [
        ("dbt", ["parse", "--target", "duckdb", "--profiles-dir", profiles], str(project))
    ]


def test_dbtf_is_preferred_for_the_new_spec(
    make_project: MakeProject, fake_dbt: FakeDbt, monkeypatch: pytest.MonkeyPatch
) -> None:
    new_spec = make_project("fusion_export")
    monkeypatch.chdir(new_spec.parent)
    fake_dbt.install("dbt")
    fake_dbt.install("dbtf")
    assert _hook("fusion_export/models/marts/orders.yml").exit_code == 1
    assert [name for name, *_ in fake_dbt.calls()] == ["dbtf"]


def test_a_legacy_project_is_parsed_with_dbt_core(project: Path, fake_dbt: FakeDbt) -> None:
    # core_export declares top-level `semantic_models:`, which Fusion ignores.
    fake_dbt.install("dbtf")
    fake_dbt.install("dbt")
    result = _hook(SEMANTIC_FILE)
    assert result.exit_code == 1, result.output
    assert [name for name, *_ in fake_dbt.calls()] == ["dbt"]
    assert (project / OUTPUT / "exp__revenue_by_day__exp_revenue_by_day.sql").is_file()


@pytest.mark.parametrize(
    "engines", [[("dbtf", "Fusion")], [("dbt", "Fusion")], [("dbtf", "Fusion"), ("dbt", "Fusion")]]
)
def test_a_legacy_project_without_dbt_core_is_an_error(
    engines: list[tuple[str, str]], project: Path, fake_dbt: FakeDbt
) -> None:
    for name, kind in engines:
        fake_dbt.install(name, kind)
    result = _hook(SEMANTIC_FILE)
    assert result.exit_code == 1, result.output
    assert fake_dbt.calls() == []
    # Names a file that declares one (the first in path order), and the way out.
    assert "core_export/models/marts/customers.yml declares a legacy-spec" in result.output
    assert "dbt Core" in result.output
    assert "--dbt" in result.output
    assert not (project / OUTPUT).exists()


def test_a_legacy_local_package_needs_dbt_core(
    make_project: MakeProject, fake_dbt: FakeDbt, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The root project is new-spec, but a local package it installs is legacy.
    new_spec = make_project("fusion_export")
    monkeypatch.chdir(new_spec.parent)
    _write(new_spec / "packages.yml", "packages:\n  - local: vendored/legacy_dep\n")
    _write(new_spec / "vendored" / "legacy_dep" / "dbt_project.yml", "name: legacy_dep\n")
    _write(
        new_spec / "vendored" / "legacy_dep" / "models" / "s.yml",
        "semantic_models:\n  - name: dep_orders\n    model: ref('orders')\n",
    )
    fake_dbt.install("dbtf")
    result = _hook("fusion_export/models/marts/orders.yml")
    assert result.exit_code == 1, result.output
    assert fake_dbt.calls() == []
    assert "fusion_export/vendored/legacy_dep/models/s.yml" in result.output


def test_the_dbt_option_overrides_the_engine_choice(project: Path, fake_dbt: FakeDbt) -> None:
    fake_dbt.install("dbtf")
    fake_dbt.install("dbt")
    assert _hook("--dbt", "dbtf", SEMANTIC_FILE).exit_code == 1
    assert [name for name, *_ in fake_dbt.calls()] == ["dbtf"]


def test_a_declared_semantic_model_missing_after_parse_is_an_error(
    project: Path, fake_dbt: FakeDbt, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A parse that left out the declared `customers` semantic model (as Fusion does
    # with legacy-spec ones), and with it the saved queries grouped by its country.
    # The hook must not take that for removals and delete their exp__ files.
    fake_dbt.install("dbt")
    assert _hook(SEMANTIC_FILE).exit_code == 1
    before = sorted(p.name for p in (project / OUTPUT).iterdir())
    dropped = tmp_path / "dropped"
    shutil.copytree(ARTIFACTS / "core_export", dropped)
    path = dropped / "semantic_manifest.json"
    raw = json.loads(path.read_text())
    raw["semantic_models"] = [sm for sm in raw["semantic_models"] if sm["name"] != "customers"]
    raw["saved_queries"] = [
        sq for sq in raw["saved_queries"] if "customer__country" not in json.dumps(sq)
    ]
    path.write_text(json.dumps(raw))
    monkeypatch.setenv("FAKE_DBT_ARTIFACTS", str(dropped))
    result = _hook("--dbt", "dbt", SEMANTIC_FILE)
    assert result.exit_code == 1, result.output
    assert "ERROR" in result.output
    assert "missing from semantic_manifest.json" in result.output
    assert "removed:" not in result.output
    assert sorted(p.name for p in (project / OUTPUT).iterdir()) == before


def test_profiles_at_the_repo_root_reach_dbt_parse(project: Path, fake_dbt: FakeDbt) -> None:
    # dbt-s-layer looks up --target in the working directory's profiles.yml first;
    # `dbt parse` runs in the project, so it must be told to use the same file.
    fake_dbt.install("dbt")
    repo_root = project.parent
    shutil.copyfile(project / "profiles" / "profiles.yml", repo_root / "profiles.yml")
    result = _hook("--target", "duckdb", SEMANTIC_FILE)
    assert result.exit_code == 1, result.output
    assert fake_dbt.calls() == [
        ("dbt", ["parse", "--target", "duckdb", "--profiles-dir", str(repo_root)], str(project))
    ]


def test_dbt_option_names_the_executable(
    project: Path, fake_dbt: FakeDbt, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_dbt.install("dbtf")
    venv_bin = tmp_path / "repo-venv" / "bin"
    venv_bin.mkdir(parents=True)
    other = FakeDbt(venv_bin, fake_dbt.log)
    other.install("my-dbt")
    rel = os.path.relpath(venv_bin / "my-dbt", project.parent)
    assert _hook("--dbt", rel, SEMANTIC_FILE).exit_code == 1
    assert [name for name, *_ in fake_dbt.calls()] == ["my-dbt"]


def test_no_parse_skips_dbt(project: Path, fake_dbt: FakeDbt) -> None:
    fake_dbt.install("dbt")
    result = _hook("--no-parse", SEMANTIC_FILE)
    assert result.exit_code == 1, result.output
    assert fake_dbt.calls() == []
    assert (project / OUTPUT / "exp__revenue_by_day__exp_revenue_by_day.sql").is_file()


def test_no_parse_with_stale_artifacts_is_an_error(project: Path, fake_dbt: FakeDbt) -> None:
    _edit(project)
    result = _hook("--no-parse", SEMANTIC_FILE)
    assert result.exit_code == 1, result.output
    assert "dbt parse" in result.output
    assert not (project / OUTPUT).exists()


def test_a_failing_parse_shows_dbts_output(
    project: Path, fake_dbt: FakeDbt, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_dbt.install("dbt")
    monkeypatch.setenv("FAKE_DBT_FAIL", "1")
    result = _hook(SEMANTIC_FILE)
    assert result.exit_code == 1, result.output
    assert "Parsing failed: something is wrong in orders.yml" in result.output
    assert "exit code 2" in result.output
    assert "--no-parse" in result.output
    assert not (project / OUTPUT).exists()


def test_no_dbt_on_path(project: Path, fake_dbt: FakeDbt) -> None:
    result = _hook(SEMANTIC_FILE)
    assert result.exit_code == 1, result.output
    assert "--dbt" in result.output
    assert "--no-parse" in result.output


def test_each_project_is_parsed_and_generated(
    project: Path, fake_dbt: FakeDbt, make_project: MakeProject
) -> None:
    fake_dbt.install("dbt")
    second = make_project("fusion_export")
    result = _hook("fusion_export/models/marts/orders.yml", SEMANTIC_FILE)
    assert result.exit_code == 1, result.output
    assert [cwd for *_, cwd in fake_dbt.calls()] == [str(project), str(second)]
    assert (second / OUTPUT).is_dir()


def test_help_lists_the_hook_command() -> None:
    result = runner.invoke(app, ["--help"])
    assert "pre-commit" in result.output
    result = runner.invoke(app, ["pre-commit", "--help"])
    assert result.exit_code == 0
    for option in ("--dbt", "--no-parse", "--project-dir", "--output-dir", "--target"):
        assert option in result.output


# --- the hook definition --------------------------------------------------------

ROOT = Path(__file__).resolve().parents[1]


def _hook_definition() -> dict[str, object]:
    from dbt_s_layer._yaml import read_yaml

    hooks = read_yaml(ROOT / ".pre-commit-hooks.yaml")
    assert isinstance(hooks, list)
    assert len(cast("list[object]", hooks)) == 1
    hook = cast("list[dict[str, object]]", hooks)[0]
    return hook


def test_hook_definition() -> None:
    import tomllib

    hook = _hook_definition()
    assert hook["id"] == "dbt-s-layer"
    assert hook["language"] == "python"
    assert hook["entry"] == "dbt-s-layer pre-commit"
    # Without it pre-commit runs file partitions in parallel, two parses of one project.
    assert hook["require_serial"] is True
    assert hook.get("pass_filenames", True) is True
    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]
    assert scripts["dbt-s-layer"] == "dbt_s_layer.cli:app"


@pytest.mark.parametrize(
    ("path", "matches"),
    [
        ("models/marts/orders.yml", True),
        ("analytics/models/orders.yaml", True),
        ("dbt_project.yml", True),
        ("analytics/dbt_project.yml", True),
        ("models/semantic/sem__orders.sql", True),
        ("models/semantic/exp__q__e.sql", True),
        ("models/semantic/_semantic_views.yml", True),
        ("models/marts/orders.sql", False),
        ("models/marts/my_exp__x.sql", False),
        ("src/app.py", False),
        ("README.md", False),
    ],
)
def test_hook_files_pattern(path: str, matches: bool) -> None:
    import re

    pattern = _hook_definition()["files"]
    assert isinstance(pattern, str)
    assert (re.search(pattern, path) is not None) is matches
