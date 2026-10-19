"""
Swap physical relations for tokens before metricflow renders SQL, and tokens for `ref()` after.

metricflow writes each semantic model's `node_relation.relation_name` verbatim
into `FROM` and `JOIN` clauses. Replacing it with a two-part token such as
`__REF__.n0_orders` (two parts satisfy `SqlTable.from_string`) lets the
rendered SQL be rewritten into dbt `{{ ref() }}` calls by a table lookup,
never by guessing from table names (EXPRT-02).

Prefixes name the ref form: `__REF__` for the root package, `__XREF__` for
another package or project, `__REFV__` for a versioned model. `__SOURCE__` is
reserved but never produced: dbt does not let a semantic model read a source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, cast

from dbt_s_layer.errors import ExportRenderError, SentinelCollisionError
from dbt_s_layer.refs import build_ref_map

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from metricflow_semantic_interfaces.implementations.semantic_manifest import (
        PydanticSemanticManifest,
    )

    from dbt_s_layer.project import SemanticProject

REF = "__REF__"
REFV = "__REFV__"
XREF = "__XREF__"
SOURCE = "__SOURCE__"
RESERVED_PREFIXES = (REF, REFV, XREF, SOURCE)

# Any reserved prefix, in any case: what pre- and post-flight look for.
_RESERVED_RE = re.compile(r"__(?:REFV|XREF|SOURCE|REF)__", re.IGNORECASE)
# A token as this module writes it.
_TOKEN_RE = re.compile(r"(?:__REFV__|__XREF__|__SOURCE__|__REF__)\.[A-Za-z0-9_]+")
# Jinja openers, plus a lone `{` at the end of text, which would merge with a
# following `{{ ref(...) }}`.
_JINJA_OPEN_RE = re.compile(r"\{[{%#]|\{\Z")


@dataclass(frozen=True)
class SentinelRelation:
    """What one token stands for."""

    token: str
    # The physical relation the engine wrote, for error messages.
    relation_name: str
    # The node it maps to, or None for a time spine that matches no node.
    node_id: str | None
    # e.g. "{{ ref('orders') }}"; None when the relation cannot be referenced.
    jinja: str | None


@dataclass(frozen=True)
class SentinelTable:
    """A copy of the semantic manifest with tokens for relations, and what each token means."""

    manifest: PydanticSemanticManifest
    relations: Mapping[str, SentinelRelation]


# --- refs ---------------------------------------------------------------------


def _jinja_str(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def render_ref(
    package: str, name: str, version: int | float | str | None, *, root_package: str
) -> str:
    """Return the `{{ ref(...) }}` call for a node; the package is named unless it is the root."""
    args = [_jinja_str(name)]
    if package != root_package:
        args.insert(0, _jinja_str(package))
    if version is not None:
        literal = _jinja_str(version) if isinstance(version, str) else repr(version)
        args.append(f"v={literal}")
    return "{{ ref(" + ", ".join(args) + ") }}"


def _prefix(package: str, version: int | float | str | None, root_package: str) -> str:
    if version is not None:
        return REFV
    return REF if package == root_package else XREF


def _token(prefix: str, index: int, name: str) -> str:
    return f"{prefix}.n{index}_{re.sub(r'[^A-Za-z0-9_]', '_', name)}"


# --- pre-flight (EXPRT-04) ----------------------------------------------------


def _label(item: object, index: int) -> str:
    """Name list items by their `name` where they have one: `dimensions[status]`."""
    name = cast("dict[object, object]", item).get("name") if isinstance(item, dict) else None
    return name if isinstance(name, str) else str(index)


def _strings(value: object, path: str) -> Iterator[tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, item in cast("dict[object, object]", value).items():
            yield from _strings(item, f"{path}.{key}" if path else str(key))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(cast("list[object]", value)):
            yield from _strings(item, f"{path}[{_label(item, index)}]")


def _collision(where: str, text: str) -> SentinelCollisionError:
    match = _RESERVED_RE.search(text)
    found = match.group(0).upper() if match else "?"
    reserved = ", ".join(RESERVED_PREFIXES)
    return SentinelCollisionError(
        f"{where} contains {found!r}, a prefix dbt-s-layer reserves for its own table "
        f"placeholders ({reserved}). Rename it so generated SQL cannot be confused."
    )


def preflight(project: SemanticProject) -> None:
    """
    Refuse a project whose own text already contains a reserved prefix.

    Checks every string in the semantic manifest (names, `expr`s, filters,
    relations) and the name and alias of every node a semantic model or time
    spine reads:
    the only project text that can reach generated SQL.
    """
    for path, text in _strings(cast("object", project.semantic_manifest.dict()), ""):
        if _RESERVED_RE.search(text):
            raise _collision(f"semantic_manifest.json `{path}`", text)
    config = project.semantic_manifest.project_configuration
    spines = [ts.node_relation.relation_name for ts in config.time_spines]
    spines += [legacy.location for legacy in config.time_spine_table_configurations]
    node_ids = {target.node_id for target in build_ref_map(project).values()}
    node_ids |= {n for rel in spines if (n := _time_spine_node(project, rel)) is not None}
    for node_id in sorted(node_ids):
        node = project.dbt_manifest.nodes[node_id]
        for field, text in (("name", node.name), ("alias", node.alias)):
            if _RESERVED_RE.search(text):
                raise _collision(f"manifest.json node {node.unique_id} {field} {text!r}", text)


# --- table --------------------------------------------------------------------


def _time_spine_node(project: SemanticProject, relation_name: str) -> str | None:
    matches = [
        node.unique_id
        for node in project.dbt_manifest.nodes.values()
        if node.relation_name == relation_name
    ]
    return matches[0] if len(matches) == 1 else None


def build_sentinel_table(project: SemanticProject) -> SentinelTable:
    """
    Run the pre-flight, then tokenise a copy of the project's semantic manifest.

    Semantic models map through `build_ref_map`. Time spines have no ref of
    their own, so they map to the node whose `relation_name` equals theirs; one
    that matches no node gets a token that fails only if an export uses it.
    """
    preflight(project)
    root = project.dbt_manifest.metadata.project_name
    manifest = project.semantic_manifest.copy(deep=True)
    config = manifest.project_configuration

    sm_nodes = {name: target.node_id for name, target in build_ref_map(project).items()}
    spine_relations = [ts.node_relation.relation_name for ts in config.time_spines] + [
        legacy.location for legacy in config.time_spine_table_configurations
    ]
    spine_nodes = {rel: _time_spine_node(project, rel) for rel in spine_relations}

    node_ids = sorted({*sm_nodes.values(), *(n for n in spine_nodes.values() if n is not None)})
    by_node: dict[str, SentinelRelation] = {}
    for index, node_id in enumerate(node_ids):
        node = project.dbt_manifest.nodes[node_id]
        by_node[node_id] = SentinelRelation(
            token=_token(_prefix(node.package_name, node.version, root), index, node.name),
            relation_name=node.relation_name or node_id,
            node_id=node_id,
            jinja=render_ref(node.package_name, node.name, node.version, root_package=root),
        )
    unmapped: dict[str, SentinelRelation] = {}
    for index, rel in enumerate(sorted(r for r, n in spine_nodes.items() if n is None)):
        unmapped[rel] = SentinelRelation(
            token=_token(REF, len(node_ids) + index, "unmapped_time_spine"),
            relation_name=rel,
            node_id=None,
            jinja=None,
        )

    def token_for_spine(relation_name: str) -> str:
        node_id = spine_nodes[relation_name]
        return (by_node[node_id] if node_id else unmapped[relation_name]).token

    for semantic_model in manifest.semantic_models:
        relation = by_node[sm_nodes[semantic_model.name.lower()]]
        semantic_model.node_relation.relation_name = relation.token
    for time_spine in config.time_spines:
        time_spine.node_relation.relation_name = token_for_spine(
            time_spine.node_relation.relation_name
        )
    for legacy in config.time_spine_table_configurations:
        legacy.location = token_for_spine(legacy.location)

    relations = {r.token: r for r in [*by_node.values(), *unmapped.values()]}
    return SentinelTable(manifest=manifest, relations=MappingProxyType(relations))


# --- rewrite and post-flight (EXPRT-05) ---------------------------------------


def escape_jinja(text: str) -> str:
    """Escape Jinja openers so dbt renders `text` back to exactly itself."""
    return _JINJA_OPEN_RE.sub(lambda m: "{{ " + _jinja_str(m.group(0)) + " }}", text)


def rewrite(sql: str, table: SentinelTable, *, context: str) -> str:
    """Turn metricflow SQL with tokens into dbt model SQL: refs for tokens, the rest escaped."""
    parts: list[str] = []
    end = 0
    for match in _TOKEN_RE.finditer(sql):
        relation = table.relations.get(match.group(0))
        if relation is None:
            raise ExportRenderError(
                f"{context}: refusing to emit SQL with an unresolved sentinel "
                f"{match.group(0)!r}; metricflow produced a table reference dbt-s-layer "
                "did not place."
            )
        if relation.jinja is None:
            raise ExportRenderError(
                f"{context}: uses the time spine {relation.relation_name}, which matches "
                "no node in manifest.json, so it cannot be written as a ref(). "
                "Define the time spine as a dbt model and re-run `dbt parse`."
            )
        parts.append(escape_jinja(sql[end : match.start()]))
        parts.append(relation.jinja)
        end = match.end()
    parts.append(escape_jinja(sql[end:]))
    return "".join(parts)


def postflight(text: str, *, context: str) -> None:
    """Refuse generated text that still contains a reserved prefix in any case."""
    found = sorted({m.group(0) for m in _RESERVED_RE.finditer(text)})
    if found:
        raise ExportRenderError(
            f"{context}: refusing to emit SQL with an unresolved sentinel ({', '.join(found)})."
        )
