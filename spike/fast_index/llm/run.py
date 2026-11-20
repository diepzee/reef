"""Run Claude Code against a local Reef with each index variant, on test data only.

Spike code. For each variant and task, this:

1. copies the test template ``reef_spike_llm_tpl`` to a fresh
   ``reef_spike_llm_run``, so every run starts from the same memory;
2. writes an MCP config (mode 600) for a stdio Reef on that copy, as the
   benchmark person, with ``REEF_SPIKE_INDEX`` set to the variant;
3. runs ``claude -p`` with only the Reef tools, and records success,
   tool calls, tokens and wall time.

The memory is the benchmark's 523 sessions plus the made-up layer of
:mod:`testlayer`. Never point this at the private memory.

Run as::

    .venv/bin/python spike/fast_index/llm/run.py --variants today v2 v4:16000 \\
        --out <dir>
"""

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import asyncpg

ROOT = Path(__file__).parents[3]
TEMPLATE = "reef_spike_llm_tpl"
RUN_DB = os.environ.get("SPIKE_RUN_DB", "reef_spike_llm_run")
if not RUN_DB.startswith("reef_spike_llm_"):
    sys.exit("refused: the run database must be a reef_spike_llm_* copy")
SUPER = "postgresql://postgres:postgres@localhost:5433/postgres"
APP = f"postgresql://reef:reef@localhost:5433/{RUN_DB}"
EMAIL = "eval@reef.local"
SYSTEM = (
    "You are the person's personal assistant, in a chat. Their long-term "
    "memory is available through the reef tools. Answer briefly."
)


@dataclass(frozen=True)
class Task:
    """One task: what the person says, and how success is checked."""

    name: str
    kind: str
    prompt: str
    groups: tuple[str, ...] = ()
    need: int = 0


TASKS = [
    Task(
        "orient",
        "orientation",
        "What do you have on my Japan trip?",
        (
            r"kyoto",
            r"osaka",
            r"6[,.]?000",
            r"ryokan",
            r"shigaraki|pottery",
            r"3 april|april 3",
        ),
        4,
    ),
    Task(
        "lookup_layer",
        "lookup",
        "When does my car insurance renew?",
        (r"14 march|march 14",),
        1,
    ),
    Task(
        "lookup_bench",
        "lookup",
        "How many skeins of worsted weight yarn did I find in my stash?",
        (r"\b17\b",),
        1,
    ),
    Task(
        "place",
        "placement",
        "Remember this for me: Ben and Sofie had a baby girl last Sunday. She's called Lotte.",
    ),
    Task(
        "brief",
        "brief",
        "I see the physio tomorrow. Brief me on where I stand with my running and my knee.",
        (r"it band|it-band|iliotibial", r"17 may|half marathon", r"15 ?km|leie"),
        3,
    ),
]


async def fresh_copy() -> None:
    """Replace the run database with a fresh copy of the template."""
    connection = await asyncpg.connect(SUPER)
    try:
        for name in (RUN_DB, TEMPLATE):
            await connection.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = $1 AND pid <> pg_backend_pid()",
                name,
            )
        await connection.execute(f"DROP DATABASE IF EXISTS {RUN_DB}")
        await connection.execute(
            f"CREATE DATABASE {RUN_DB} TEMPLATE {TEMPLATE} OWNER reef"
        )
    finally:
        await connection.close()


def write_config(directory: Path, variant: str, secret: str) -> Path:
    """Write the MCP config for one variant, readable by this user only.

    :param directory: where to write it
    :param variant: the ``REEF_SPIKE_INDEX`` value; ``+strong`` at the end
        adds the stronger placement rule to the protocol
    :param secret: the local session secret
    :returns: the config's path
    """
    path = directory / f"mcp-{variant.replace(':', '-').replace('+', '-')}.json"
    config = {
        "mcpServers": {
            "reef": {
                "command": str(ROOT / ".venv" / "bin" / "python"),
                "args": ["-m", "reef.server"],
                "env": {
                    "REEF_DATABASE_URL": APP,
                    "REEF_DEV_PRINCIPAL_EMAIL": EMAIL,
                    "REEF_SESSION_SECRET": secret,
                    "REEF_SPIKE_INDEX": variant.split("+")[0],
                    "REEF_SPIKE_PROTOCOL": "strong"
                    if variant.endswith("+strong")
                    else "",
                    "REEF_EMBEDDING_URL": "http://127.0.0.1:11434",
                },
            }
        }
    }
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text(json.dumps(config))
    return path


async def _versions(database: str) -> dict[str, tuple[int, bool]]:
    """Read every engram's version, and whether it mentions the placement fact.

    :param database: the database to read
    :returns: version and mention, by path
    """
    connection = await asyncpg.connect(SUPER.rsplit("/", 1)[0] + f"/{database}")
    try:
        rows = await connection.fetch(
            "SELECT path, version, body ILIKE '%lotte%' AS holds FROM engrams"
        )
    finally:
        await connection.close()
    return {row["path"]: (row["version"], row["holds"]) for row in rows}


async def placement_outcome() -> dict:
    """Say where the placement task's fact was written, against the template.

    :returns: whether ``people/ben.md`` now holds it, and every engram the
        run created or changed, with whether it holds the fact
    """
    before = await _versions(TEMPLATE)
    after = await _versions(RUN_DB)
    changed = {
        path: {"version": version, "holds": holds}
        for path, (version, holds) in after.items()
        if before.get(path, (None, None))[0] != version
    }
    return {
        "in_ben": changed.get("people/ben.md", {}).get("holds", False),
        "changed": changed,
    }


def run_claude(
    prompt: str, config: Path, cwd: Path, model: str
) -> tuple[list[dict], float]:
    """Run one Claude Code conversation and collect its event stream.

    :param prompt: what the person says
    :param config: the MCP config
    :param cwd: an empty directory to run in
    :param model: the model to use
    :returns: the stream's events, and the wall time in seconds
    """
    command = [
        "claude",
        "-p",
        prompt,
        "--model",
        model,
        "--output-format",
        "stream-json",
        "--verbose",
        "--strict-mcp-config",
        "--mcp-config",
        str(config),
        "--allowedTools",
        "mcp__reef__*",
        "--tools",
        "",
        "--no-session-persistence",
        "--append-system-prompt",
        SYSTEM,
    ]
    env = {**os.environ, "MAX_MCP_OUTPUT_TOKENS": "200000"}
    env.pop("REEF_DATABASE_URL", None)
    start = time.perf_counter()
    done = subprocess.run(
        command, cwd=cwd, env=env, capture_output=True, text=True, timeout=900
    )
    wall = time.perf_counter() - start
    events = []
    for line in done.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    if not events:
        events.append({"type": "error", "stderr": done.stderr[-2000:]})
    return events, wall


def summarise(events: list[dict], task: Task) -> dict:
    """Pull the measures out of one run's events.

    :param events: the stream
    :param task: the task
    :returns: tool calls, tokens, the answer and whether the text check passed
    """
    calls: list[dict] = []
    result_sizes: dict[str, int] = {}
    pending: dict[str, str] = {}
    for event in events:
        message = event.get("message") or {}
        for block in message.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                name = block["name"].removeprefix("mcp__reef__")
                calls.append({"tool": name, "input": block.get("input", {})})
                pending[block["id"]] = name
            elif block.get("type") == "tool_result":
                name = pending.get(block.get("tool_use_id"), "?")
                content = block.get("content")
                size = (
                    len(json.dumps(content))
                    if not isinstance(content, str)
                    else len(content)
                )
                result_sizes[name] = result_sizes.get(name, 0) + size
    final = next((e for e in reversed(events) if e.get("type") == "result"), {})
    usage = final.get("usage") or {}
    answer = final.get("result") or ""
    text = answer.lower()
    hits = sum(1 for pattern in task.groups if re.search(pattern, text))
    return {
        "answer": answer,
        "text_hits": hits,
        "text_ok": hits >= task.need if task.groups else None,
        "tool_calls": len(calls),
        "calls": [c["tool"] for c in calls],
        "expands": [
            c["input"].get("expand")
            for c in calls
            if c["tool"] == "load_index" and c["input"].get("expand")
        ],
        "result_chars": result_sizes,
        "input_tokens": usage.get("input_tokens", 0),
        "cache_creation_tokens": usage.get("cache_creation_input_tokens", 0),
        "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "total_input_tokens": usage.get("input_tokens", 0)
        + usage.get("cache_creation_input_tokens", 0)
        + usage.get("cache_read_input_tokens", 0),
        "turns": final.get("num_turns"),
        "duration_ms": final.get("duration_ms"),
        "cost_usd": final.get("total_cost_usd"),
        "is_error": final.get("is_error", not final),
    }


def main() -> None:
    """Run every task with every variant and write the results."""
    import asyncio

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--variants", nargs="+", default=["today", "v2", "v4:16000"])
    parser.add_argument("--tasks", nargs="+", default=[t.name for t in TASKS])
    parser.add_argument("--model", default="sonnet")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "cwd").mkdir(exist_ok=True)
    secret = secrets.token_hex(32)
    results_path = args.out / "results.jsonl"
    for repeat in range(args.repeat):
        for variant in args.variants:
            config = write_config(args.out, variant, secret)
            for task in [t for t in TASKS if t.name in args.tasks]:
                asyncio.run(fresh_copy())
                events, wall = run_claude(
                    task.prompt, config, args.out / "cwd", args.model
                )
                stem = f"{variant.replace(':', '-')}-{task.name}-{repeat}"
                (args.out / f"{stem}.jsonl").write_text(
                    "\n".join(json.dumps(e) for e in events)
                )
                row = {
                    "variant": variant,
                    "task": task.name,
                    "kind": task.kind,
                    "repeat": repeat,
                    "wall_s": round(wall, 1),
                    **summarise(events, task),
                }
                if task.name == "place":
                    outcome = asyncio.run(placement_outcome())
                    row["placement"] = outcome
                    row["text_ok"] = outcome["in_ben"]
                with results_path.open("a") as handle:
                    handle.write(json.dumps(row) + "\n")
                print(
                    f"{variant:>9} {task.name:<13} ok={row['text_ok']} calls={row['tool_calls']} "
                    f"in={row['total_input_tokens']:,} wall={row['wall_s']}s {row['calls']}",
                    flush=True,
                )
    for config in args.out.glob("mcp-*.json"):
        config.unlink()


if __name__ == "__main__":
    sys.exit(main())
