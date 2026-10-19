# 04-02 Summary: Snowflake renderer

- `snowflake.py`: `render_snowflake(spec)` returns
  `{{ config(materialized='semantic_view', static_analysis='off') }}`, a blank line, and the
  clauses TABLES (with `PRIMARY KEY`, one `UNIQUE` per key, `COMMENT`), RELATIONSHIPS (explicit
  parent column), DIMENSIONS, METRICS (aggregations, `PERCENTILE_CONT/DISC ... WITHIN GROUP`,
  `APPROX_PERCENTILE`, `PRIVATE`, `NON ADDITIVE BY (<table>.<dim> ASC|DESC)`, view-level derived
  metrics) and the view `COMMENT`. Empty clauses are left out; a spec with no dimension or metric
  is an error.
- `quote_identifier`: Snowflake reserved words (from the docs page) and the semantic view DDL
  words become upper-case quoted identifiers. `sql_string` escapes `\`, `'` and newlines. Exprs
  and literals go through `sentinels.escape_jinja`; refs stay live; `postflight` runs on the body.
- `check_name_shadowing`: within a table, an element whose name is a bare identifier in another
  element's expr raises `SemanticViewError` naming both and the expr, unless it is a dimension
  whose expr is exactly that column. Literals, quoted identifiers, function names, comments,
  other tables' qualified names and keywords (including `END`, which Snowflake does not reserve)
  are not references. A metric over a bare column still shadows: its semantic expression is the
  aggregate.
- Snapshots: tests/snapshots/semantic_views/<set>/sem__*.sql for every artifact set
  (`UPDATE_SNAPSHOTS=1` rewrites them; a missing snapshot fails).
- Tests committed red first: tests/test_snowflake.py (renderer, quoting, literals, shadowing,
  snapshots).

## Review round 1

- Trailing line comments: exprs are pasted inline, so an expr ending in `-- comment` swallowed
  the `,`, the aggregate's `)` or the `COMMENT` after it. `_expr` (used for dimensions, every
  aggregate including the percentiles, and the text parts of derived metrics) scans the expr
  with `scan_sql` and appends a newline when its last token is a `--` comment. Other exprs are
  byte-identical, so no snapshot changed; a `/* */` comment, `--` in a string literal or quoted
  identifier, and an expr already ending in a newline are left alone.
- Name shadowing no longer takes type names and date parts for columns. `_references` skips,
  structurally: the identifier after `::` or after `AS` (the type of `CAST`/`TRY_CAST`), the
  second word of `double precision`/`character varying`, the first argument of `DATE_TRUNC`,
  `DATEADD`, `DATEDIFF`, `DATE_PART`, `TIMEADD`, `TIMEDIFF`, `TIMESTAMPADD`, `TIMESTAMPDIFF`, the
  part in `EXTRACT(part FROM ...)` and the second argument of `LAST_DAY`. Date parts are not an
  ignore list of words: a column named `year` used as `MAX(year)` or as `DATEADD`'s amount still
  shadows. The reserved-word quoting set is unchanged.
