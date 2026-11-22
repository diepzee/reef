# Test conversations

An assistant (Codex on `gpt-6-astra`) plays scripted conversations against a
made-up person in Reef, and each one gets a verdict. Run it after a change to
tools, search, the protocol or file reading.

Design: `docs/specs/2026-11-22-test-conversations-design.md`.
Steps to run it, and the one-time production setup: the "Test conversations"
section of `docs/runbook.md`.

## Files

- `run.py`: the runner. Plays the scenarios, resets the account, writes the report.
- `score.py`: turns what Codex did into a verdict and writes the report.
- `events.py`: reads Codex's event stream.
- `local_account.py`: creates or resets the local made-up person (`just conversations-account`).
- `make_files.py`: builds the made-up person's files.
- `worker-task.md`: the task for an Orca worker; `{target}` is `local` or `production`.
- `../../tests/conversations/scenarios.toml`: the scenarios.

## Run it

```
just conversations-account                      # local only, once
uv run python scripts/conversations/run.py --target local
uv run python scripts/conversations/run.py --target production --only dentist-moved
```

Options: `--only ID...` plays only those scenarios; `--file-text on|off`
overrides whether the target reads file text. Reports go to
`~/.reef-conversations/reports/`, outside git. Every scenario uses Codex quota.

## Through Orca

The controller fills in the target and starts a Codex worker:

```
sed 's/{target}/local/g' scripts/conversations/worker-task.md > "$TMPDIR/task.md"
orca orchestration worker-start --spec-file "$TMPDIR/task.md" \
  --worktree new-child --name conversations-local --agent codex --model gpt-6-astra
```

The worker runs the runner, changes nothing, and reports the report paths and
the count per verdict with `worker_done`.
