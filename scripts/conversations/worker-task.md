# Run the test conversations against {target}

You run Reef's test conversations once and report what happened. You change
nothing.

## Before you start

The runner uses its own Codex home, `~/.reef-conversations/codex`. It must be
signed in once: `CODEX_HOME=~/.reef-conversations/codex codex login`. For
production, the Reef command-line tool must also be signed in once, as the
test account, in a private window:
`REEF_CONFIG_DIR=~/.reef-conversations/reef-cli reef login`. Codex gets that
sign-in from the command-line tool for each run, in memory only.
Only the operator signs in. If the runner says something is not signed in,
do not run these commands: report that and stop. The process that runs the
tests needs read and write access to `~/.reef-conversations/codex` and, for
production, `~/.reef-conversations/reef-cli`. Your own Codex is not
affected: typing `codex` keeps using `~/.codex`.

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
  it anyway: the report is written even when a run stops. The checks before
  the run (the sign-in folder, the Codex home and the sign-ins) stop it
  before a report exists: then there is no report path to give.
- Never print or copy a token, sign-in code or cookie into your report. Do
  not run `reef token` yourself: the runner does.

## What `worker_done` holds

- The status: finished, or stopped (with the error text).
- The paths of the Markdown and JSON reports.
- The count per verdict, as in the report's totals line: `pass`, `fail`,
  `review`, `skipped`, `invalid`.
- The id of every scenario that is not `pass` or `skipped`, with the reason
  the report gives, in one line each.
