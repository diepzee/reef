"""Measure today's index and the fast-index variants on one spike database.

Spike code. Run once per database, as the person seeded into it::

    REEF_DATABASE_URL=postgresql://reef:reef@localhost:5433/reef_spike_b500 \\
    REEF_EMBEDDING_URL=http://127.0.0.1:11434 \\
        .venv/bin/python spike/fast_index/measure.py bench 11 --out results/b500.json

    REEF_DATABASE_URL=postgresql://reef:reef@localhost:5433/reef_spike_private \\
    REEF_EMBEDDING_URL=http://127.0.0.1:11434 \\
        .venv/bin/python spike/fast_index/measure.py private --out results/private.json

It writes and prints aggregates only: sizes, times and shares. Never a path,
a title, a description or a question. The private memory is personal data.

Sizes are characters of the compact JSON the MCP tool sends. Tokens are
estimated as characters divided by four.

**Routing** is a proxy for an assistant's judgement, with no model in the
loop: each line of an index and each question are embedded by the local
embedding service, and the lines are ranked by cosine similarity to the
question. A question is routed at ``n`` when one of the first ``n`` lines is
an expected engram, or a collapsed line that holds one.
"""

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

import httpx
import numpy as np
from pydantic_core import to_json

sys.path.insert(0, str(Path(__file__).parent))

import snapshot as snap
import variants as var
from snapshot import Question, Snapshot

from reef.config import get_settings
from reef.db import DB
from reef.embedding import passage, question
from reef.methods import Caller
from reef.methods.find import load_index

RUNS = 20
WARMUP = 3


def percentile(values: list[float], q: float) -> float:
    """Return a percentile by linear interpolation.

    :param values: the sample
    :param q: the percentile, from 0 to 100
    :returns: the value
    """
    return float(np.percentile(np.array(values), q)) if values else 0.0


async def timed(make, runs: int = RUNS, warmup: int = WARMUP) -> dict[str, float]:
    """Time an async callable, warm.

    :param make: a callable returning an awaitable
    :param runs: timed runs
    :param warmup: untimed runs first
    :returns: median and 95th percentile, in milliseconds
    """
    for _ in range(warmup):
        await make()
    times = []
    for _ in range(runs):
        start = time.perf_counter()
        await make()
        times.append((time.perf_counter() - start) * 1000)
    return {
        "median_ms": round(statistics.median(times), 1),
        "p95_ms": round(percentile(times, 95), 1),
    }


def timed_sync(make, runs: int = RUNS, warmup: int = 1) -> dict[str, float]:
    """Time a plain callable, warm.

    :param make: the callable
    :param runs: timed runs
    :param warmup: untimed runs first
    :returns: median and 95th percentile, in milliseconds
    """
    for _ in range(warmup):
        make()
    times = []
    for _ in range(runs):
        start = time.perf_counter()
        make()
        times.append((time.perf_counter() - start) * 1000)
    return {
        "median_ms": round(statistics.median(times), 1),
        "p95_ms": round(percentile(times, 95), 1),
    }


def _pair_chars(key: str, value: object) -> int:
    """Count the characters one ``"key": value`` pair takes, with its comma.

    :param key: the key
    :param value: the value
    :returns: characters
    """
    return len(to_json({key: value}).decode()) - 2 + 1


def breakdown(index: dict) -> dict[str, int]:
    """Attribute today's payload size to its parts.

    :param index: today's payload, protocol included
    :returns: characters per part, largest first
    """
    parts: dict[str, int] = {}

    def add(name: str, chars: int) -> None:
        """Add characters to a part.

        :param name: the part
        :param chars: characters
        """
        parts[name] = parts.get(name, 0) + chars

    for key, value in index.items():
        if key != "spaces":
            add(key, _pair_chars(key, value))
    for space in index["spaces"]:
        for key, value in space.items():
            if key == "engrams":
                for engram in value:
                    add("engram: {}", 2)
                    for field, v in engram.items():
                        add(f"engram: {field}", _pair_chars(field, v))
            elif key == "attachments":
                for attachment in value:
                    add("file: {}", 2)
                    for field, v in attachment.items():
                        add(f"file: {field}", _pair_chars(field, v))
            else:
                add(f"space: {key}", _pair_chars(key, value))
    return dict(sorted(parts.items(), key=lambda item: -item[1]))


class Embedder:
    """Embeds lines and questions with the local service, with a memory cache."""

    def __init__(self) -> None:
        """Connect to the local embedding service named in the settings."""
        settings = get_settings().embedding
        if not settings.url.startswith(("http://127.0.0.1", "http://localhost")):
            sys.exit("the embedding service must be on this machine")
        self.url = settings.url.rstrip("/") + "/api/embed"
        self.model = settings.model
        self.cache: dict[str, np.ndarray] = {}
        self.client = httpx.Client(timeout=120)

    def embed(self, texts: list[str], *, as_question: bool = False) -> np.ndarray:
        """Return unit vectors for texts, embedding only the ones not cached.

        :param texts: the texts
        :param as_question: write them as questions rather than passages
        :returns: one row per text
        """
        form = question if as_question else passage
        keys = [("q:" if as_question else "p:") + t for t in texts]
        missing = list(dict.fromkeys(k for k in keys if k not in self.cache))
        for start in range(0, len(missing), 64):
            batch = missing[start : start + 64]
            response = self.client.post(
                self.url,
                json={
                    "model": self.model,
                    "input": [form(self.model, k[2:]) for k in batch],
                    "truncate": True,
                },
            )
            response.raise_for_status()
            for k, vector in zip(batch, response.json()["embeddings"], strict=True):
                v = np.array(vector, dtype=np.float32)
                self.cache[k] = v / (float(np.linalg.norm(v)) or 1.0)
        return np.stack([self.cache[k] for k in keys])


def coverage(
    built: var.Built,
    questions: list[Question],
    embedder: Embedder | None,
    entries: dict[tuple[str, str], snap.Entry],
) -> dict[str, float | int | None]:
    """Measure how well an index orients towards each question's engrams.

    :param built: the variant
    :param questions: the questions
    :param embedder: the local embedding service, or None to skip routing
    :param entries: every engram, by space alias and path
    :returns: shares and sizes, aggregates only
    """
    visible = built.visible()
    refs = [(q, key) for q in questions for key in q.expected]
    shown = sum(1 for _, key in refs if key in visible)
    reachable = 0
    expansion_sizes = []
    for _, key in refs:
        if key in visible:
            reachable += 1
            continue
        container = built.container_of(key)
        if container is not None:
            reachable += 1
            expansion_sizes.append(
                len(to_json(built.expansions.get(container.expand, [])).decode())
            )
    result: dict[str, float | int | None] = {
        "lines": len(built.lines),
        "visible_engrams": round(shown / len(refs), 3),
        "visible_questions": round(
            sum(1 for q in questions if any(k in visible for k in q.expected))
            / len(questions),
            3,
        ),
        "reachable_engrams": round(reachable / len(refs), 3),
        "expansion_chars_median": int(statistics.median(expansion_sizes))
        if expansion_sizes
        else 0,
        "expansion_chars_max": max(expansion_sizes) if expansion_sizes else 0,
    }
    if embedder is None:
        return result
    line_vectors = embedder.embed([line.text for line in built.lines])
    question_vectors = embedder.embed([q.text for q in questions], as_question=True)
    scores = question_vectors @ line_vectors.T
    for n in (1, 3, 5):
        hits = 0
        for qi, q in enumerate(questions):
            wanted = set(q.expected)
            best = np.argsort(-scores[qi])[:n]
            if any(built.lines[i].members & wanted for i in best):
                hits += 1
        result[f"routed_at_{n}"] = round(hits / len(questions), 3)
    result.update(
        two_step(built, questions, scores, question_vectors, embedder, entries)
    )
    return result


def two_step(
    built: var.Built,
    questions: list[Question],
    scores: np.ndarray,
    question_vectors: np.ndarray,
    embedder: Embedder,
    entries: dict[tuple[str, str], snap.Entry],
    lines: int = 3,
    opens: int = 3,
) -> dict[str, float | int]:
    """Simulate fast-then-slow orientation with the embedding service as judge.

    For each question: take the ``lines`` index lines closest to it; expand
    each collapsed one among them; then rank every engram now in view and
    open the ``opens`` closest. The question is found when one of those is
    an expected engram. Every variant opens the same number of engrams, so
    the comparison is fair; what differs is what was in view, and how many
    characters it took to get there.

    :param built: the variant
    :param questions: the questions
    :param scores: question-by-line similarities
    :param question_vectors: the questions' unit vectors
    :param embedder: the local embedding service
    :param entries: every engram, by space alias and path
    :param lines: how many index lines the judge looks at closely
    :param opens: how many engrams it opens
    :returns: the found share and the median characters read beyond the index
    """
    member_text: dict[tuple[str, str], str] = {}
    found = 0
    extra: list[int] = []
    for qi, q in enumerate(questions):
        best = np.argsort(-scores[qi])[:lines]
        candidates: dict[tuple[str, str], str] = {}
        read = 0
        for i in best:
            line = built.lines[i]
            if line.direct:
                key = next(iter(line.members))
                candidates[key] = line.text
                continue
            expansion = built.expansions.get(line.expand, [])
            read += len(to_json(expansion).decode())
            for key in line.members:
                if key not in member_text:
                    member_text[key] = var.engram_line(entries[key])
                candidates[key] = member_text[key]
        extra.append(read)
        keys = list(candidates)
        vectors = embedder.embed([candidates[k] for k in keys])
        order = np.argsort(-(vectors @ question_vectors[qi]))[:opens]
        if {keys[i] for i in order} & set(q.expected):
            found += 1
    return {
        "two_step_found": round(found / len(questions), 3),
        "two_step_extra_chars_median": int(statistics.median(extra)) if extra else 0,
    }


async def run(name: str, count: int | None, out: Path, routing: bool) -> dict:
    """Measure one database.

    :param name: ``private`` or ``bench``
    :param count: the benchmark's ``--questions``, for ``bench``
    :param out: where to write the results
    :param routing: whether to measure routing with the embedding service
    :returns: the results
    """
    import evalkit

    email = evalkit.PRIVATE_EMAIL if name == "private" else evalkit.EVAL_EMAIL
    label = name if name == "private" else f"bench{count}"
    await DB.start_connection_pool()
    try:
        s: Snapshot = await snap.take(label, email)
        s.questions = (
            snap.private_questions()
            if name == "private"
            else snap.bench_questions(count)
        )
        var.set_now(s)
        caller = Caller(principal=s.principal, writer="assistant", session_id=None)

        async def today_call() -> None:
            """Call load_index and serialise it, as the tool does."""
            to_json(await load_index(caller, "spike latency"))

        latency_today = await timed(today_call)
        latency_vectors = await timed(lambda: snap._vectors_and_activation(s.principal))
    finally:
        await DB.close_connection_pool()

    protocol = s.protocol
    builders = {
        "today": lambda: var.today(s),
        "slim": lambda: var.slim(s),
        "v1": lambda: var.folders(s),
        "v2": lambda: var.folders(s, k=10),
        "v3": lambda: var.semantic(s),
        "v4_8k": lambda: var.budgeted(s, 8_000),
        "v4_16k": lambda: var.budgeted(s, 16_000),
    }
    embedder = Embedder() if routing else None
    variants = {}
    for key, build in builders.items():
        built = build()
        runs = 5 if key in {"v3", "v4_8k", "v4_16k"} else RUNS
        index_chars = built.chars()
        total_chars = built.chars(protocol)
        variants[key] = {
            "index_chars": index_chars,
            "index_tokens_est": index_chars // 4,
            "total_chars": total_chars,
            "total_tokens_est": total_chars // 4,
            "build": timed_sync(build, runs=runs),
            **coverage(built, s.questions, embedder, {e.key: e for e in s.entries}),
        }
        print(
            f"{label:>10} {key:<8} {total_chars:>9,} chars  "
            f"visible {variants[key]['visible_engrams']:.0%}  "
            f"routed@3 {variants[key].get('routed_at_3', float('nan')):.0%}  "
            f"two-step {variants[key].get('two_step_found', float('nan')):.0%} "
            f"+{variants[key].get('two_step_extra_chars_median', 0):,} chars",
            flush=True,
        )
    results = {
        "dataset": label,
        "engrams": len(s.entries),
        "files": sum(len(sp["attachments"]) for sp in s.index["spaces"]),
        "spaces": len(s.index["spaces"]),
        "questions": len(s.questions),
        "expected_refs": sum(len(q.expected) for q in s.questions),
        "with_vector": sum(1 for e in s.entries if e.vector is not None),
        "with_inbound": sum(1 for e in s.entries if e.inbound),
        "with_activation": sum(1 for e in s.entries if e.activation is not None),
        "protocol_chars": len(protocol),
        "today_latency": latency_today,
        "vectors_latency": latency_vectors,
        "today_breakdown": breakdown(s.index),
        "variants": variants,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=1))
    return results


def main() -> None:
    """Parse the command line and measure."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("name", choices=["private", "bench"])
    parser.add_argument("count", nargs="?", type=int)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--no-routing", action="store_true")
    args = parser.parse_args()
    dsn = get_settings().dsn
    if "reef_spike_" not in dsn or "localhost" not in dsn:
        sys.exit("refused: measure only a local reef_spike_* database")
    asyncio.run(run(args.name, args.count, args.out, not args.no_routing))


if __name__ == "__main__":
    main()
