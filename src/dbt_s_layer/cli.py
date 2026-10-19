"""dbt-s-layer command-line interface."""

from __future__ import annotations

import typer

app = typer.Typer(
    name="dbt-s-layer",
    help="Code-generate dbt models from the dbt semantic layer.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def _main() -> None:  # pyright: ignore[reportUnusedFunction]
    """
    Code-generate dbt models from the dbt semantic layer.

    A Typer callback is registered so the root app remains a multi-command
    group even when only one subcommand is defined. Without it, Typer
    collapses single-command apps into their sole command, which would
    hide the `doctor` subcommand from `--help` output.
    """


@app.command()
def doctor() -> None:
    """Report environment diagnostics (dbt engine, metricflow version)."""
    from dbt_s_layer._doctor import run_doctor

    raise typer.Exit(code=run_doctor())


if __name__ == "__main__":
    app()
