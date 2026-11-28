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

## Verdicts

| Verdict | Meaning |
| --- | --- |
| `pass` | The tools, answer and memory checks passed. |
| `fail` | A required tool did not work, a forbidden tool was used, or another check failed. |
| `review` | No failing checks, but the answer needs review or a call failed to a tool named in `review_failed_tools`. |
| `skipped` | The target lacks something the scenario needs. |
| `invalid` | Codex failed, used a tool outside Reef, or Reef refused a bearer sign-in near its time limit. |

The runner times each Codex message, after getting its token. The sign-in
provider's access tokens last 300 seconds (as of 27 November 2026). With a
30-second margin, a refused sign-in in a message that took at least 270
seconds makes the scenario `invalid`, even if a later call succeeds. The
reason gives the measured time and the token lifetime. Every other reason,
including failed calls, stays in the report.

A refusal in a shorter message keeps the usual verdict. For example, the
report adds "Reef refused a fresh sign-in after 12 seconds". A required
call that failed still makes the scenario `fail`. The runner recognizes
the MCP reply `Authentication required` and FastMCP's longer reply about
an invalid or expired bearer token. Without a measured message time, it
reports the refusal without claiming that the sign-in ran out.

Local runs have no bearer token to expire. Their tool errors keep the usual
scoring, including an auth error: it may point to a local setup problem.
Other tool errors, such as `invalid_arguments`, also keep the usual scoring.

## Timing in the report

`Seconds` is the time taken to play all messages in a scenario. It includes
getting each token and starting Codex. Resets and checks of the memory
afterwards are outside that time.

`Longest message (s)` is the longest Codex message in the scenario, rounded
to whole seconds. It includes starting Codex, model work and tool calls,
but excludes getting the token. The JSON field is `longest_message_seconds`.
An unmeasured or skipped scenario shows `—` in the table and `null` in JSON.

The runner measures the time around each Codex call. It cannot measure
silent gaps inside a message: it gets all output at once, after Codex ends,
and the JSON events have no timestamps. Measuring those gaps would need
live output reading or a reader for Codex's session files.

## Through Orca

The controller fills in the target and starts a Codex worker:

```
sed 's/{target}/local/g' scripts/conversations/worker-task.md > "$TMPDIR/task.md"
orca orchestration worker-start --spec-file "$TMPDIR/task.md" \
  --worktree new-child --name conversations-local --agent codex --model gpt-6-astra
```

The worker runs the runner, changes nothing, and reports the report paths and
the count per verdict with `worker_done`.
