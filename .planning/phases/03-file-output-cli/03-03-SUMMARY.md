# 03-03 Summary: doctor, README, end to end

- `doctor --project-dir/--output-dir` reports whether the output directory (default
  `models/semantic`) and the parent `generate` stages files in can be written, or which existing
  ancestor it would be created in. Completes CLI-03. Tests committed red first; dropping the
  parent check fails one.
- README: "Generating models" section (commands, options table, ownership, atomicity, clashes),
  and the Exports section no longer says the command is unreleased.
- End-to-end run recorded in 03-VERIFICATION.md.
