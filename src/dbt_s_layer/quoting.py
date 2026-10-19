"""
Quote column names that are reserved words, for each dialect metricflow renders.

metricflow 0.213.0 renders every column name and alias as a bare identifier
(upstream: https://github.com/dbt-labs/metricflow/issues/2058), so an entity or
dimension named like a reserved word (`order`, `group`, ...) yields SQL the
warehouse rejects: `orders_src_10000.order_id AS order`, `subq_10.order`.

`quoting_renderer` subclasses a metricflow plan renderer so that exactly those
names are quoted, and every other name is rendered byte for byte as before. It
hooks the renderer's own column visitors, not the rendered text:

- `visit_column_reference_expr`: `table.column` and bare `column`;
- `visit_column_alias_reference_expr`: a bare alias;
- `render_group_by_expr`: BigQuery groups by alias;
- `_render_select_columns_section`: the `AS alias` of each select column. It is
  private to metricflow, so `tests/test_stack_surface.py` pins its signature.

SQL the user wrote (a dimension's `expr`) reaches metricflow as a string
expression and is never touched: quote a reserved word there yourself.

A quoted name keeps the case the dialect folds an unquoted name to (upper case
on Snowflake, lower case on Postgres and Redshift, as written elsewhere, where
names are case-insensitive), so the column is named as it would have been.
"""

from __future__ import annotations

import dataclasses
from functools import cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from metricflow.sql.render.expr_renderer import (
        DefaultSqlExpressionRenderer,
        SqlExpressionRenderResult,
    )
    from metricflow.sql.render.sql_plan_renderer import DefaultSqlPlanRenderer, SqlPlanRenderResult
    from metricflow.sql.sql_plan import SqlSelectColumn
    from metricflow_semantics.sql.sql_exprs import (
        SqlColumnAliasReferenceExpression,
        SqlColumnReferenceExpression,
    )


def _words(text: str) -> frozenset[str]:
    return frozenset(text.lower().split())


# Reserved words that cannot be a bare column name or alias, from each vendor's
# documentation (2026-10). A word listed by mistake only costs a pair of quotes.

# https://docs.snowflake.com/en/sql-reference/reserved-keywords
_SNOWFLAKE = _words("""
    account all alter and any as between by case cast check column connect connection
    constraint create cross current current_date current_time current_timestamp current_user
    database delete distinct drop else exists false following for from full grant group
    gscluster having ilike in increment inner insert intersect into is issue join lateral left
    like localtime localtimestamp minus natural not null of on or order organization qualify
    regexp revoke right rlike row rows sample schema select set some start table tablesample
    then to trigger true try_cast union unique update using values view when whenever where
    window with
""")

# https://www.postgresql.org/docs/current/sql-keywords-appendix.html: "reserved" and
# "reserved (can be function or type)".
_POSTGRES = _words("""
    all analyse analyze and any array as asc asymmetric authorization binary both case cast
    check collate collation column concurrently constraint create cross current_catalog
    current_date current_role current_schema current_time current_timestamp current_user
    default deferrable desc distinct do else end except false fetch for foreign freeze from
    full grant group having ilike in initially inner intersect into is isnull join lateral
    leading left like limit localtime localtimestamp natural not notnull null offset on only
    or order outer overlaps placing primary references returning right select session_user
    similar some symmetric system_user table tablesample then to trailing true union unique
    user using variadic verbose when where window with
""")

# https://docs.aws.amazon.com/redshift/latest/dg/r_pg_keywords.html
_REDSHIFT = _words("""
    aes128 aes256 all allowoverwrite analyse analyze and any array as asc authorization az64
    backup between binary blanksasnull both bytedict bzip2 case cast check collate column
    constraint create credentials cross current_date current_time current_timestamp
    current_user current_user_id default deferrable deflate defrag delta delta32k desc
    disable distinct do else emptyasnull enable encode encrypt encryption end except explicit
    false for foreign freeze from full globaldict256 globaldict64k grant group gzip having
    identity ignore ilike in initially inner intersect interval into is isnull join leading
    left like limit localtime localtimestamp lun luns lzo lzop minus mostly16 mostly32 mostly8
    natural new not notnull null nulls off offline offset oid old on only open or order outer
    overlaps parallel partition percent permissions pivot placing primary raw readratio
    recover references rejectlog resort respect restore right select session_user similar
    snapshot some sysdate system table tag tdes text255 text32k then timestamp to top trailing
    true truncatecolumns type union unique unnest unpivot user using verbose wallet when where
    with without
""")

# https://duckdb.org/docs/sql/dialect/keywords_and_identifiers: duckdb_keywords()
# where keyword_category = 'reserved'.
_DUCKDB = _words("""
    all analyse analyze and any array as asc asymmetric both case cast check collate column
    constraint create current_catalog current_date current_role current_time
    current_timestamp current_user default deferrable desc describe distinct do else end
    except false fetch for foreign from grant group having in initially intersect into
    lateral leading limit localtime localtimestamp not null offset on only or order pivot
    pivot_longer pivot_wider placing primary qualify references returning select session_user
    show some summarize symmetric table then to trailing true union unique unpivot user using
    variadic when where window with
""")

# https://cloud.google.com/bigquery/docs/reference/standard-sql/lexical#reserved_keywords
_BIGQUERY = _words("""
    all and any array as asc assert_rows_modified at between by case cast collate contains
    create cross cube current default define desc distinct else end enum escape except
    exclude exists extract false fetch following for from full group grouping groups hash
    having if ignore in inner intersect interval into is join lateral left like limit lookup
    merge natural new no not null nulls of on or order outer over partition preceding proto
    qualify range recursive respect right rollup rows select set some struct tablesample then
    to treat true unbounded union unnest using when where window with within
""")

# https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-reserved-words: not
# enforced by default, but the special words are, and spark.sql.ansi.enforceReservedKeywords
# enforces the ANSI list.
_DATABRICKS = _words("""
    all and any as authorization both case cast check collate column constraint create cross
    current_date current_time current_timestamp current_user default distinct else end
    escape except false fetch filter for foreign from full grant group having in inner
    intersect into is join lateral leading left natural not null offset on only or order
    outer overlaps primary references right select session_user some table then time to
    trailing true union unique unknown user using when where with
""")

# https://trino.io/docs/current/language/reserved.html; Athena's DML engine is Trino.
_TRINO = _words("""
    alter and as auto between by case cast constraint create cross cube current_catalog
    current_date current_path current_role current_schema current_time current_timestamp
    current_user deallocate delete describe distinct drop else end escape except exists
    extract false for from full group grouping having in inner insert intersect into is join
    json_array json_exists json_object json_query json_serialize json_table json_value left
    like listagg localtime localtimestamp natural normalize not null on or order outer
    overlaps prepare recursive right rollup select skip table then trim true uescape union
    unnest using values when where with
""")


@dataclasses.dataclass(frozen=True)
class Dialect:
    """How a dialect quotes an identifier, and which names it must quote."""

    reserved: frozenset[str]
    open: str
    close: str
    fold: Callable[[str], str]

    def quote(self, name: str) -> str:
        """Return `name` quoted if it is a reserved word, else `name` unchanged."""
        if name.lower() not in self.reserved:
            return name
        escaped = self.fold(name).replace(self.close, self.close * 2)
        return f"{self.open}{escaped}{self.close}"


def _same(name: str) -> str:
    return name


DIALECTS: dict[str, Dialect] = {
    "athena": Dialect(_TRINO, '"', '"', str.lower),
    "bigquery": Dialect(_BIGQUERY, "`", "`", _same),
    "databricks": Dialect(_DATABRICKS, "`", "`", _same),
    "duckdb": Dialect(_DUCKDB, '"', '"', _same),
    "postgres": Dialect(_POSTGRES, '"', '"', str.lower),
    "redshift": Dialect(_REDSHIFT, '"', '"', str.lower),
    "snowflake": Dialect(_SNOWFLAKE, '"', '"', str.upper),
    "trino": Dialect(_TRINO, '"', '"', str.lower),
}


@cache
def quoting_renderer(
    adapter: str, base: type[DefaultSqlPlanRenderer]
) -> type[DefaultSqlPlanRenderer]:
    """Subclass `base` (and its expression renderer) to quote reserved column names."""
    dialect = DIALECTS[adapter]
    base_expr: type[DefaultSqlExpressionRenderer] = type(base.EXPR_RENDERER)

    class _Expr(base_expr):
        def visit_column_reference_expr(
            self, node: SqlColumnReferenceExpression
        ) -> SqlExpressionRenderResult:
            result = super().visit_column_reference_expr(node)
            column = node.col_ref.column_name
            quoted = dialect.quote(column)
            if quoted == column:
                return result
            # metricflow renders `table_alias.column`, or `column` alone.
            prefix = result.sql.removesuffix(column)
            assert prefix in ("", f"{node.col_ref.table_alias}."), result.sql
            return dataclasses.replace(result, sql=prefix + quoted)

        def visit_column_alias_reference_expr(
            self, node: SqlColumnAliasReferenceExpression
        ) -> SqlExpressionRenderResult:
            result = super().visit_column_alias_reference_expr(node)
            assert result.sql == node.column_alias, result.sql
            return dataclasses.replace(result, sql=dialect.quote(result.sql))

        def render_group_by_expr(
            self, group_by_column: SqlSelectColumn
        ) -> SqlExpressionRenderResult:
            result = super().render_group_by_expr(group_by_column)
            # BigQuery groups by the bare alias; every other dialect by the expression.
            if result.sql == group_by_column.column_alias:
                return dataclasses.replace(result, sql=dialect.quote(result.sql))
            return result

    class _Plan(base):
        EXPR_RENDERER = _Expr()

        def _render_select_columns_section(
            self,
            select_columns: Sequence[SqlSelectColumn],
            num_parents: int,
            distinct: bool,
        ) -> SqlPlanRenderResult:
            # A reserved alias equal to its column name renders as `t."ORDER" AS "ORDER"`,
            # not metricflow's shorter `t.order`: the two names no longer compare equal.
            columns = [
                c
                if dialect.quote(c.column_alias) == c.column_alias
                else c.copy_with_new_alias(dialect.quote(c.column_alias))
                for c in select_columns
            ]
            return super()._render_select_columns_section(columns, num_parents, distinct)

    _Plan.__name__ = _Plan.__qualname__ = f"Quoting{base.__name__}"
    return _Plan
