"""Safe YAML reading for dbt project files."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

if TYPE_CHECKING:
    from pathlib import Path


class YamlReadError(Exception):
    """A YAML file could not be read or parsed."""


def read_yaml(path: Path) -> object:
    """Parse `path` with ruamel.yaml's safe loader; raise YamlReadError on any failure."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise YamlReadError(str(exc)) from exc
    try:
        data: object = YAML(typ="safe", pure=True).load(text)  # pyright: ignore[reportUnknownMemberType]
    except YAMLError as exc:
        raise YamlReadError(str(exc).splitlines()[0] if str(exc) else type(exc).__name__) from exc
    return data
