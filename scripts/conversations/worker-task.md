# Run the test conversations against {target}

You run Reef's test conversations once and report what happened. You change
nothing.

## Do this

1. From the repository root (your current worktree), run:

   ```
   uv run python scripts/conversations/run.py --target {target}
   ```

   It plays every scenario through `codex exec`, which takes several minutes
   to an hour. Wait for it to finish. It prints one line per scenario
   (`<id>: <verdict> (<seconds>s)`) and, at the end, `Report: <path>`.
2. Open the Markdown report at that path. It sits in
   `~/.reef-conversations/reports/`, with a JSON file of the same name beside
   it.
3. Report with `worker_done`.

## Rules

- Do not edit, create or delete any file in the repository. Do not commit.
- Do not start other runs, and do not rerun scenarios.
- If `run.py` exits with a non-zero status, do not retry it. Report the exit
  status and the last lines it printed. If it printed a report path, include
  it anyway: the report is written even when a run stops.
- Never print or copy a token, sign-in code or cookie into your report.

## What `worker_done` holds

- The status: finished, or stopped (with the error text).
- The paths of the Markdown and JSON reports.
- The count per verdict, as in the report's totals line: `pass`, `fail`,
  `review`, `skipped`, `invalid`.
- The id of every scenario that is not `pass` or `skipped`, with the reason
  the report gives, in one line each.
