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
