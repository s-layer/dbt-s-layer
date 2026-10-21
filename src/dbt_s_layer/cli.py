"""dbt-s-layer command-line interface."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from dbt_s_layer import _paths

if TYPE_CHECKING:
    from dbt_s_layer._commands import Options

app = typer.Typer(
    name="dbt-s-layer",
    help="Code-generate dbt models from the dbt semantic layer.",
    no_args_is_help=True,
    add_completion=False,
)


def _print_version(value: bool) -> None:
    if value:
        from importlib.metadata import version

        typer.echo(f"dbt-s-layer {version('dbt-s-layer')}")
        raise typer.Exit


@app.callback()
def _main(  # pyright: ignore[reportUnusedFunction]
    _version: Annotated[
        bool,
        typer.Option(
            "--version",
            help="Print the installed dbt-s-layer version and exit.",
            callback=_print_version,
            is_eager=True,
        ),
    ] = False,
) -> None:
    """
    Code-generate dbt models from the dbt semantic layer.

    A Typer callback is registered so the root app remains a multi-command
    group even when only one subcommand is defined. Without it, Typer
    collapses single-command apps into their sole command, which would
    hide the `doctor` subcommand from `--help` output.
    """


_PROJECT_DIR = typer.Option(
    "--project-dir",
    help="The dbt project directory (default: the current directory).",
    file_okay=False,
)
_OUTPUT_DIR = typer.Option(
    "--output-dir",
    help="Where generated models go; a relative path is relative to --project-dir.",
)
_TARGET = typer.Option(
    "--target",
    help="dbt target whose adapter type picks the SQL dialect (read from profiles.yml).",
)
_ADAPTER = typer.Option(
    "--adapter",
    help="SQL dialect (dbt adapter type, e.g. snowflake); overrides --target and the "
    "adapter recorded by `dbt parse`.",
)
_PROFILES_DIR = typer.Option(
    "--profiles-dir",
    help="Directory holding profiles.yml, for --target.",
    file_okay=False,
)


def _options(
    project_dir: Path | None,
    output_dir: Path | None,
    target: str | None,
    adapter: str | None,
    profiles_dir: Path | None,
) -> Options:
    # Imported here, like `_doctor`: it loads metricflow, which `doctor` must not need.
    from dbt_s_layer._commands import Options

    return Options(
        project_dir=project_dir or Path.cwd(),
        output_dir=output_dir or _paths.DEFAULT_OUTPUT_DIR,
        target=target,
        adapter=adapter,
        profiles_dir=profiles_dir,
    )


@app.command()
def generate(
    project_dir: Annotated[Path | None, _PROJECT_DIR] = None,
    output_dir: Annotated[Path | None, _OUTPUT_DIR] = None,
    target: Annotated[str | None, _TARGET] = None,
    adapter: Annotated[str | None, _ADAPTER] = None,
    profiles_dir: Annotated[Path | None, _PROFILES_DIR] = None,
) -> None:
    """Generate dbt models from the project's semantic layer into --output-dir."""
    from dbt_s_layer import _commands

    options = _options(project_dir, output_dir, target, adapter, profiles_dir)
    raise typer.Exit(code=_commands.run_generate(options))


@app.command()
def check(
    project_dir: Annotated[Path | None, _PROJECT_DIR] = None,
    output_dir: Annotated[Path | None, _OUTPUT_DIR] = None,
    target: Annotated[str | None, _TARGET] = None,
    adapter: Annotated[str | None, _ADAPTER] = None,
    profiles_dir: Annotated[Path | None, _PROFILES_DIR] = None,
) -> None:
    """Exit 1 if the generated models in --output-dir are out of date (for CI)."""
    from dbt_s_layer import _commands

    options = _options(project_dir, output_dir, target, adapter, profiles_dir)
    raise typer.Exit(code=_commands.run_check(options))


@app.command("pre-commit")
def pre_commit(
    files: Annotated[
        list[Path] | None,
        typer.Argument(
            help="Changed files (pre-commit passes the staged ones). Only semantic YAML, "
            "dbt_project.yml and generated files trigger a run.",
            show_default=False,
        ),
    ] = None,
    project_dir: Annotated[
        Path | None,
        typer.Option(
            "--project-dir",
            help="The dbt project (default: the nearest dbt_project.yml above each file).",
            file_okay=False,
        ),
    ] = None,
    output_dir: Annotated[Path | None, _OUTPUT_DIR] = None,
    target: Annotated[str | None, _TARGET] = None,
    adapter: Annotated[str | None, _ADAPTER] = None,
    profiles_dir: Annotated[Path | None, _PROFILES_DIR] = None,
    dbt: Annotated[
        str | None,
        typer.Option(
            "--dbt",
            help=(
                "dbt executable that parses the project (default: dbt Core for a project "
                "with legacy-spec semantic_models:, else dbtf if on PATH, else dbt)."
            ),
        ),
    ] = None,
    parse: Annotated[
        bool,
        typer.Option(
            "--parse/--no-parse",
            help="Run `dbt parse` before generating (--no-parse: something else parses).",
        ),
    ] = True,
) -> None:
    """Parse and regenerate when semantic YAML changed; exit 1 if files changed (for pre-commit)."""
    from dbt_s_layer import _hook

    options = _hook.HookOptions(
        output_dir=output_dir or _paths.DEFAULT_OUTPUT_DIR,
        project_dir=project_dir,
        target=target,
        adapter=adapter,
        profiles_dir=profiles_dir,
        dbt=dbt,
        parse=parse,
    )
    raise typer.Exit(code=_hook.run_hook(files or [], options))


@app.command()
def doctor(
    project_dir: Annotated[
        Path | None,
        typer.Option(
            "--project-dir",
            help="Also check this dbt project's parse artifacts and output directory.",
            file_okay=False,
        ),
    ] = None,
    output_dir: Annotated[
        Path | None,
        typer.Option(
            "--output-dir",
            help="Output directory to check (default: models/semantic, relative to --project-dir).",
        ),
    ] = None,
) -> None:
    """Report environment diagnostics (dbt engine, metricflow, project artifacts, output dir)."""
    from dbt_s_layer import _doctor

    raise typer.Exit(code=_doctor.run_doctor(project_dir, output_dir))


if __name__ == "__main__":
    app()
