"""
Read a dbt target's adapter type from profiles.yml (CLI-04 `--target`).

Only `type` is read: it picks the SQL dialect. dbt-s-layer never connects.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import cast

from dbt_s_layer._project_yaml import read_dbt_project
from dbt_s_layer._yaml import YamlReadError, read_yaml
from dbt_s_layer.errors import ProfileError

PROFILES_FILE = "profiles.yml"


def _candidates(project_dir: Path) -> list[Path]:
    """Where dbt looks for profiles.yml, in order, after `--profiles-dir`."""
    dirs: list[Path] = []
    env = os.environ.get("DBT_PROFILES_DIR")
    if env:
        dirs.append(Path(env))
    dirs += [Path.cwd(), project_dir, Path.home() / ".dbt"]
    return [d / PROFILES_FILE for d in dirs]


def find_profiles(project_dir: Path, profiles_dir: Path | None = None) -> Path:
    """Return the profiles.yml dbt would use for `project_dir`."""
    if profiles_dir is not None:
        path = profiles_dir / PROFILES_FILE
        if not path.is_file():
            raise ProfileError(f"{path} does not exist.")
        return path
    candidates = _candidates(project_dir)
    for path in candidates:
        if path.is_file():
            return path
    looked = ", ".join(str(p.parent) for p in candidates)
    raise ProfileError(
        f"no profiles.yml found (looked in {looked}); pass --profiles-dir, or --adapter "
        "to name the SQL dialect directly."
    )


def _mapping(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return {str(k): v for k, v in cast("dict[object, object]", value).items()}


def adapter_for_target(project_dir: Path, target: str, *, profiles_dir: Path | None = None) -> str:
    """Return the adapter `type` of `target` in the project's dbt profile."""
    profile = read_dbt_project(project_dir).get("profile")
    if not isinstance(profile, str) or not profile:
        raise ProfileError(
            f"{project_dir / 'dbt_project.yml'} does not set `profile`, so --target "
            "cannot be looked up; pass --adapter instead."
        )
    path = find_profiles(project_dir, profiles_dir)
    try:
        data = read_yaml(path)
    except YamlReadError as exc:
        raise ProfileError(f"{path} could not be read: {exc}") from exc

    profiles = _mapping(data) or {}
    entry = _mapping(profiles.get(profile))
    if entry is None:
        raise ProfileError(f"{path} has no profile {profile!r} (named in dbt_project.yml).")
    outputs = _mapping(entry.get("outputs")) or {}
    output = _mapping(outputs.get(target))
    if output is None:
        known = ", ".join(repr(name) for name in sorted(outputs)) or "none"
        raise ProfileError(
            f"profile {profile!r} in {path} has no target {target!r}; its targets: {known}."
        )
    adapter = output.get("type")
    if not isinstance(adapter, str) or not adapter or "{{" in adapter or "{%" in adapter:
        raise ProfileError(
            f"target {target!r} of profile {profile!r} in {path} has no plain `type` "
            f"(found {adapter!r}); pass --adapter to name the SQL dialect."
        )
    return adapter
