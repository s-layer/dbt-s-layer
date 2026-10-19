"""dbt-s-layer command-line interface."""

from __future__ import annotations

from pathlib import Path  # noqa: TC003 - Typer resolves annotations at runtime
from typing import Annotated

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
def doctor(
    project_dir: Annotated[
        Path | None,
        typer.Option(
            "--project-dir",
            help="Also check this dbt project's parse artifacts exist and are up to date.",
            file_okay=False,
        ),
    ] = None,
) -> None:
    """Report environment diagnostics (dbt engine, metricflow version, project artifacts)."""
    from dbt_s_layer import _doctor

    raise typer.Exit(code=_doctor.run_doctor(project_dir))


if __name__ == "__main__":
    app()
