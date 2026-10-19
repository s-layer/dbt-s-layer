"""Tests for picking the SQL dialect from a dbt target (CLI-04 `--target`)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from dbt_s_layer.errors import ProfileError
from dbt_s_layer.profiles import adapter_for_target

if TYPE_CHECKING:
    from pathlib import Path

PROFILES = """\
fx:
  target: dev
  outputs:
    dev:
      type: duckdb
      path: dev.duckdb
    prod:
      type: snowflake
      account: x
other:
  outputs:
    dev:
      type: bigquery
"""


@pytest.fixture
def dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """A project, an unrelated working directory and a home, with no profiles.yml anywhere."""
    paths = {name: tmp_path / name for name in ("project", "cwd", "home", "flag", "env")}
    for path in paths.values():
        path.mkdir()
    (paths["project"] / "dbt_project.yml").write_text("name: fx\nprofile: fx\n")
    monkeypatch.chdir(paths["cwd"])
    monkeypatch.setenv("HOME", str(paths["home"]))
    monkeypatch.delenv("DBT_PROFILES_DIR", raising=False)
    return paths


def _profiles(directory: Path, adapter: str) -> None:
    (directory / "profiles.yml").write_text(PROFILES.replace("snowflake", adapter))


def test_named_target(dirs: dict[str, Path]) -> None:
    _profiles(dirs["project"], "snowflake")
    assert adapter_for_target(dirs["project"], "prod") == "snowflake"
    assert adapter_for_target(dirs["project"], "dev") == "duckdb"


def test_lookup_order(dirs: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    # --profiles-dir, then DBT_PROFILES_DIR, the working directory, the project, ~/.dbt.
    (dirs["home"] / ".dbt").mkdir()
    _profiles(dirs["home"] / ".dbt", "redshift")
    assert adapter_for_target(dirs["project"], "prod") == "redshift"
    _profiles(dirs["project"], "postgres")
    assert adapter_for_target(dirs["project"], "prod") == "postgres"
    _profiles(dirs["cwd"], "trino")
    assert adapter_for_target(dirs["project"], "prod") == "trino"
    _profiles(dirs["env"], "athena")
    monkeypatch.setenv("DBT_PROFILES_DIR", str(dirs["env"]))
    assert adapter_for_target(dirs["project"], "prod") == "athena"
    _profiles(dirs["flag"], "databricks")
    assert adapter_for_target(dirs["project"], "prod", profiles_dir=dirs["flag"]) == "databricks"


def test_explicit_profiles_dir_without_profiles(dirs: dict[str, Path]) -> None:
    # An explicit directory is not silently skipped.
    _profiles(dirs["project"], "snowflake")
    with pytest.raises(ProfileError, match=rf"{dirs['flag'] / 'profiles.yml'} does not exist"):
        adapter_for_target(dirs["project"], "prod", profiles_dir=dirs["flag"])


def test_no_profiles_anywhere(dirs: dict[str, Path]) -> None:
    with pytest.raises(ProfileError, match=r"no profiles\.yml.*--adapter") as info:
        adapter_for_target(dirs["project"], "prod")
    assert str(dirs["project"]) in str(info.value)
    assert ".dbt" in str(info.value)


def test_unknown_target_lists_the_targets(dirs: dict[str, Path]) -> None:
    _profiles(dirs["project"], "snowflake")
    with pytest.raises(ProfileError, match=r"target 'staging'.*'dev', 'prod'"):
        adapter_for_target(dirs["project"], "staging")


def test_unknown_profile(dirs: dict[str, Path]) -> None:
    (dirs["project"] / "dbt_project.yml").write_text("name: fx\nprofile: missing\n")
    _profiles(dirs["project"], "snowflake")
    with pytest.raises(ProfileError, match=r"profile 'missing'"):
        adapter_for_target(dirs["project"], "prod")


def test_project_without_profile(dirs: dict[str, Path]) -> None:
    (dirs["project"] / "dbt_project.yml").write_text("name: fx\n")
    _profiles(dirs["project"], "snowflake")
    with pytest.raises(ProfileError, match=r"does not set `profile`"):
        adapter_for_target(dirs["project"], "prod")


@pytest.mark.parametrize(
    "type_line",
    ["type: \"{{ env_var('DBT_TYPE') }}\"", "type: 3", "account: x"],
)
def test_unusable_type_asks_for_adapter(type_line: str, dirs: dict[str, Path]) -> None:
    (dirs["project"] / "profiles.yml").write_text(
        f"fx:\n  outputs:\n    prod:\n      {type_line}\n"
    )
    with pytest.raises(ProfileError, match=r"target 'prod'.*--adapter"):
        adapter_for_target(dirs["project"], "prod")


def test_unreadable_profiles(dirs: dict[str, Path]) -> None:
    (dirs["project"] / "profiles.yml").write_text("fx: [unclosed\n")
    with pytest.raises(ProfileError, match=r"profiles\.yml could not be read"):
        adapter_for_target(dirs["project"], "prod")
