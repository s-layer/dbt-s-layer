# 03-03 Summary: doctor, README, end to end

- `doctor --project-dir/--output-dir` reports whether the output directory (default
  `models/semantic`) and the parent `generate` stages files in can be written, or which existing
  ancestor it would be created in. Completes CLI-03. Tests committed red first; dropping the
  parent check fails one.
- README: "Generating models" section (commands, options table, ownership, atomicity, clashes),
  and the Exports section no longer says the command is unreleased.
- End-to-end run recorded in 03-VERIFICATION.md.

## Review round 1 (PR #5)

- The clash check skipped every node whose file is in the output directory. It now skips only
  dbt-s-layer's own file names there, so a user's model in that directory is still checked.
- A failure while moving files into place left a mix of old and new files. Old files are now
  moved aside into the staging directory, and every move is undone if any step fails, so a
  failed run really does leave the directory as it was. The new test also caught a bookkeeping
  bug in the first version of the undo.

## Review round 2 (PR #5)

- Blocking: `cli.py` imported `_commands` (and so metricflow) at load, and `doctor` imported
  it for two path helpers, so a missing metricflow crashed every command instead of `doctor`
  exiting 2. The helpers moved to the metricflow-free `_paths.py`, and `generate`/`check`
  import `_commands` lazily. A subprocess test runs `--help` and `doctor` with metricflow
  blocked.
- An export without its own schema now takes the output directory's folder `+schema` from
  dbt_project.yml in the clash check (deepest setting wins; a Jinja value skips that export
  rather than guessing). Ephemeral models are no longer compared, since they build no relation.
