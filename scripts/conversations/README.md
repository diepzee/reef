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

A scenario can set `review_failed_tools = ["write"]` to ask for review when
a call to that tool fails, even if a retry works. Other failures still keep
their `fail` verdict. `replace-contract` uses this to catch a confirmation
sent without the original arguments.

## Run it

Once, before any run (local or production), sign in the runner's own Codex home:

```
CODEX_HOME=~/.reef-conversations/codex codex login
```

Your own Codex is not affected: typing `codex` keeps using `~/.codex`. The
process that runs the tests needs read and write access to
`~/.reef-conversations/codex`. Set `REEF_CONVERSATIONS_CODEX_HOME` to use
another folder.

For production, also sign the Reef command-line tool in as the test account,
once, in a private browser window:

```
REEF_CONFIG_DIR=~/.reef-conversations/reef-cli reef login
```

This is the only Reef sign-in production needs. Your own sign-in, in
`~/.config/reef`, is not affected. Codex gets the sign-in from the
command-line tool for each run: before each message, the runner asks
`reef token` for an access token and passes it to Codex in memory only. Set
`REEF_CONVERSATIONS_REEF_CONFIG_DIR` to keep the test sign-in in another folder.

```
just conversations-account                      # local only, once
uv run python scripts/conversations/run.py --target local
uv run python scripts/conversations/run.py --target production --only dentist-moved
```

Options: `--only ID...` plays only those scenarios; `--file-text on|off`
overrides whether the target reads file text. Reports go to
`~/.reef-conversations/reports/`, outside git. Every scenario uses Codex quota.

## Timing in the report

`Seconds` is the time taken to play all messages in a scenario. It includes
getting each token and starting Codex. Resets and checks of the memory
afterwards are outside that time.

The report does not measure the longest gap without output from Codex.
The runner gets all output at once, after `codex exec` ends. The JSON events
it reads have no timestamps. The total time cannot tell us how long the
model was silent.

Measuring that gap would need live output reading with a clock, while still
handling both output streams, timeouts and token removal. Reading Codex's
session files would be another way. Both are left for a separate change.

## Through Orca

The controller fills in the target and starts a Codex worker:

```
sed 's/{target}/local/g' scripts/conversations/worker-task.md > "$TMPDIR/task.md"
orca orchestration worker-start --spec-file "$TMPDIR/task.md" \
  --worktree new-child --name conversations-local --agent codex --model gpt-6-astra
```

The worker runs the runner, changes nothing, and reports the report paths and
the count per verdict with `worker_done`.
