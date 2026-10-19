# 03-01 Summary: file writer

- `output.render_output(project, adapter=)` returns name -> text for every generated file:
  each `exp__*.sql` with the header line, and `_semantic_views.yml` (header comment,
  `version: 2`, models sorted, saved-query description or a default). Nothing when no exports.
- `apply_output(files, output_dir)` stages new and changed files in `.dbt-s-layer-*` beside the
  output directory, `os.replace`s each into place, deletes stale owned files, and leaves
  unchanged files untouched. `diff_output` reports changed/missing/stale and writes nothing.
- Owned names whose first line is not the header raise `OutputConflictError` before anything
  is written.
- Tests (tests/test_output.py, 30) committed red first. Mutations checked: writing straight to
  the output directory (11 failures), skipping the header check (5 failures).
