"""
Write a synthetic dbt project for timing the pre-commit hook (HOOK-02).

The project has `--models` semantic models (default 100: one `customers` model
plus N-1 fact models that join to it) and `--exports` saved queries with one
export each (default 50), in the new YAML spec, which dbt Core >= 1.12 and dbt
Fusion both parse. Each fact model has its own YAML file, as a real project would.

Usage:
    uv run python scripts/make_scale_project.py DEST [--models 100] [--exports 50]

DEST must not exist. The profile has a `snowflake` target (the default: Fusion
parses it, and dbt-s-layer then also writes `sem__` semantic views) and a `duckdb`
one. Parsing never connects, so the credentials are dummies.
"""

from __future__ import annotations

import argparse
from pathlib import Path

PROFILES = """\
scale:
  target: snowflake
  outputs:
    snowflake:
      type: snowflake
      account: dummy
      user: dummy
      password: dummy
      role: dummy
      database: ANALYTICS
      warehouse: WH
      schema: MARTS
    duckdb:
      type: duckdb
      path: ":memory:"
      schema: marts
"""

TIME_SPINE_YML = """\
models:
  - name: metricflow_time_spine
    time_spine:
      standard_granularity_column: date_day
    columns:
      - name: date_day
        granularity: day
"""

CUSTOMERS_YML = """\
models:
  - name: customers
    semantic_model:
      enabled: true
    agg_time_dimension: signed_up_at
    columns:
      - name: customer_id
        entity:
          type: primary
          name: customer
      - name: country
        dimension:
          type: categorical
      - name: signed_up_at
        granularity: day
        dimension:
          type: time
    metrics:
      - name: customer_count
        type: simple
        agg: count_distinct
        expr: customer_id
"""

FACT_SQL = (
    "select 1 as id, 1 as customer_id, 'paid' as status, "
    "cast('2024-01-01' as date) as ordered_at, 10.0 as amount\n"
)

FACT_YML = """\
models:
  - name: fact_{n}
    semantic_model:
      enabled: true
    agg_time_dimension: ordered_at
    columns:
      - name: id
        entity:
          type: primary
          name: fact_{n}
      - name: customer_id
        entity:
          type: foreign
          name: customer
      - name: status
        dimension:
          type: categorical
      - name: ordered_at
        granularity: day
        dimension:
          type: time
    metrics:
      - name: revenue_{n}
        type: simple
        agg: sum
        expr: amount
      - name: orders_{n}
        type: simple
        agg: count
        expr: id
"""

SAVED_QUERY = """\
  - name: sq_{n}
    query_params:
      metrics: [revenue_{n}, orders_{n}]
      group_by: ["TimeDimension('metric_time', 'day')", "Dimension('customer__country')"]
    exports:
      - name: exp_{n}
        config:
          export_as: table
"""


def write_project(dest: Path, *, models: int = 100, exports: int = 50) -> None:
    """Write the project into `dest`, which must not exist."""
    facts = models - 1
    if facts < 1 or not 0 <= exports <= facts:
        raise ValueError("need models >= 2 and 0 <= exports < models")
    marts = dest / "models" / "marts"
    marts.mkdir(parents=True)
    (dest / "dbt_project.yml").write_text(
        'name: scale\nversion: "1.0.0"\nprofile: scale\nmodel-paths: ["models"]\n'
    )
    (dest / "profiles").mkdir()
    (dest / "profiles" / "profiles.yml").write_text(PROFILES)
    (marts / "metricflow_time_spine.sql").write_text(
        "select cast('2024-01-01' as date) as date_day\n"
    )
    (marts / "metricflow_time_spine.yml").write_text(TIME_SPINE_YML)
    (marts / "customers.sql").write_text(
        "select 1 as customer_id, 'GB' as country, cast('2024-01-01' as date) as signed_up_at\n"
    )
    (marts / "customers.yml").write_text(CUSTOMERS_YML)
    for i in range(facts):
        n = f"{i:03d}"
        (marts / f"fact_{n}.sql").write_text(FACT_SQL)
        (marts / f"fact_{n}.yml").write_text(FACT_YML.format(n=n))
    if exports:
        body = "".join(SAVED_QUERY.format(n=f"{i:03d}") for i in range(exports))
        (marts / "saved_queries.yml").write_text("saved_queries:\n" + body)


def main() -> None:
    parser = argparse.ArgumentParser(description="Write the 100/50 timing project.")
    parser.add_argument("dest", type=Path)
    parser.add_argument("--models", type=int, default=100)
    parser.add_argument("--exports", type=int, default=50)
    args = parser.parse_args()
    write_project(args.dest, models=args.models, exports=args.exports)


if __name__ == "__main__":
    main()
