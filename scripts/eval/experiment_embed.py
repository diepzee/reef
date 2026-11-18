"""Offline experiment: do embeddings beat keyword search, and by how much?

Run as::

    uv run --with numpy python scripts/eval/experiment_embed.py \
        --set benchmark|private --model qwen|gemma [--prefix] [--questions N]

Nothing here is part of the product. It embeds the pages of a test set with
a local model served by Ollama, scores keyword search, embeddings alone and
the two fused, and prints one table. Vectors are cached under
``scripts/eval/data/out/`` (ignored by git), so a second run is quick.
Embedding is checkpointed every 2,000 chunks: run the same command again
after an interruption and it picks up where it stopped.

Text is sent to Ollama on this machine and nowhere else.
"""

import argparse
import asyncio
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

import evalkit
import numpy as np

from reef import sections
from reef.access import Principal
from reef.config import get_settings
from reef.db import DB, transaction_scope
from reef.identity import person_by_email
from reef.indexfields import summary
from reef.search import search_pages

OLLAMA = "http://127.0.0.1:11434/api/embed"
OUT = evalkit.DATA_DIR / "out"

#: Longest chunk, in characters. About 500 tokens: inside every model's window.
CHUNK = 2000

#: How many results of each ranking take part in the fusion.
DEPTH = 50

#: The constant of reciprocal rank fusion. 60 is the value from the paper.
RRF_K = 60

#: The weights tried for the embeddings in the fusion. 1 is plain fusion.
WEIGHTS = (1.0, 2.0, 3.0)

#: Each model's Ollama name, and how it wants a query and a passage written.
MODELS = {
    "qwen": {
        "name": "qwen3-embedding:0.6b",
        "query": (
            "Instruct: Given a question, retrieve the notes that answer it\nQuery:{q}"
        ),
        "passage": "{text}",
    },
    "gemma": {
        "name": "embeddinggemma:300m",
        "query": "task: search result | query: {q}",
        "passage": "title: none | text: {text}",
    },
}


def chunks(body: str) -> list[str]:
    """Cut a page into pieces of at most :data:`CHUNK` characters.

    Paragraphs are kept whole where they fit. One that is too long is cut.

    :param body: the page text
    :returns: the pieces, in order
    """
    pieces: list[str] = []
    current = ""
    for paragraph in body.split("\n\n"):
        while len(paragraph) > CHUNK:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(paragraph[:CHUNK])
            paragraph = paragraph[CHUNK:]
        if len(current) + len(paragraph) + 2 > CHUNK and current:
            pieces.append(current)
            current = ""
        current = f"{current}\n\n{paragraph}" if current else paragraph
    if current.strip():
        pieces.append(current)
    return pieces


#: How many chunks go into one checkpoint file.
SHARD = 2000


def embed(
    model: str, texts: list[str], batch: int = 16, checkpoint: Path | None = None
) -> np.ndarray:
    """Embed texts with a local Ollama model.

    With ``checkpoint``, every :data:`SHARD` texts are written to a file in
    that directory as soon as they are done. A run that was interrupted
    picks up after the last file, so at most one shard of work is lost.

    :param model: the Ollama model name
    :param texts: what to embed
    :param batch: how many texts per request
    :param checkpoint: a directory for the shard files, or None to keep
        everything in memory
    :returns: one unit-length row per text
    """
    shards: list[np.ndarray] = []
    if checkpoint is not None:
        checkpoint.mkdir(parents=True, exist_ok=True)
        for start in range(0, len(texts), SHARD):
            part = checkpoint / f"part-{start:08d}.npy"
            if not part.exists():
                break
            shards.append(np.load(part))
    done = sum(len(shard) for shard in shards)
    if done:
        print(f"  resuming after {done}/{len(texts)} chunks", flush=True)
    started = time.monotonic()
    for start in range(done, len(texts), SHARD):
        rows: list[list[float]] = []
        for at in range(start, min(start + SHARD, len(texts)), batch):
            payload = json.dumps(
                {"model": model, "input": texts[at : at + batch], "truncate": True}
            ).encode()
            request = urllib.request.Request(
                OLLAMA, data=payload, headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(request, timeout=600) as response:
                rows.extend(json.loads(response.read())["embeddings"])
        matrix = np.asarray(rows, dtype=np.float32)
        matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
        shards.append(matrix)
        if checkpoint is not None:
            # Written under another name first, so a file that exists is whole.
            temporary = checkpoint / f"part-{start:08d}.tmp.npy"
            np.save(temporary, matrix)
            temporary.rename(checkpoint / f"part-{start:08d}.npy")
        finished = start + len(rows)
        rate = (finished - done) / (time.monotonic() - started)
        left = (len(texts) - finished) / rate / 60
        print(f"  {finished}/{len(texts)} chunks, {left:.0f} min left", flush=True)
    return np.concatenate(shards) if shards else np.zeros((0, 0), dtype=np.float32)


def page_vectors(
    pages: list[evalkit.EvalPage], model: dict, prefix: bool | str, tag: str
) -> tuple[np.ndarray, np.ndarray]:
    """Embed every chunk of every page, or read the cached result.

    :param pages: the pages
    :param model: an entry of :data:`MODELS`
    :param prefix: put the page title in front of each chunk, or
        ``"sections"`` to cut by heading with :mod:`reef.sections`
    :param tag: names the cache file
    :returns: the chunk vectors, and for each chunk the index of its page
    """
    texts: list[str] = []
    owner: list[int] = []
    for index, page in enumerate(pages):
        if prefix == "sections":
            # The product's own cutting: by heading, with context in front.
            for section in sections.split(page.title, summary(page.body), page.body):
                texts.append(model["passage"].format(text=section.text))
                owner.append(index)
            continue
        for piece in chunks(page.body):
            text = f"{page.title}\n\n{piece}" if prefix and page.title else piece
            texts.append(model["passage"].format(text=text))
            owner.append(index)
    digest = hashlib.sha256("\x1f".join(texts).encode()).hexdigest()[:12]
    cache = OUT / f"emb-{tag}-{digest}.npy"
    OUT.mkdir(parents=True, exist_ok=True)
    if cache.exists():
        return np.load(cache), np.asarray(owner)
    print(f"embedding {len(texts)} chunks of {len(pages)} pages", flush=True)
    vectors = embed(model["name"], texts, checkpoint=OUT / f"emb-{tag}-{digest}")
    np.save(cache, vectors)
    return vectors, np.asarray(owner)


def semantic_ranking(
    query: np.ndarray, vectors: np.ndarray, owner: np.ndarray, paths: list[str]
) -> list[str]:
    """Rank pages by their best chunk.

    :param query: the question's vector
    :param vectors: the chunk vectors
    :param owner: for each chunk the index of its page
    :param paths: the page paths, by index
    :returns: the :data:`DEPTH` best page paths, best first
    """
    scores = vectors @ query
    best = np.full(len(paths), -1.0, dtype=np.float32)
    np.maximum.at(best, owner, scores)
    return [paths[i] for i in np.argsort(-best)[:DEPTH]]


def fuse(lexical: list[str], semantic: list[str], weight: float = 1.0) -> list[str]:
    """Merge two rankings by reciprocal rank fusion.

    :param lexical: keyword search's page paths, best first
    :param semantic: the embeddings' page paths, best first
    :param weight: how much the embeddings count, against 1 for keyword
    :returns: the merged ranking
    """
    score: dict[str, float] = {}
    for ranking, share in ((lexical, 1.0), (semantic, weight)):
        for rank, path in enumerate(ranking, 1):
            score[path] = score.get(path, 0.0) + share / (RRF_K + rank)
    return sorted(score, key=score.get, reverse=True)


async def lexical_rankings(
    email: str, questions: list[evalkit.EvalQuestion]
) -> dict[str, list[str]]:
    """Ask keyword search every question, :data:`DEPTH` results deep.

    :param email: the eval person who owns the pages
    :param questions: the questions
    :returns: page paths per question id, best first
    """
    await DB.start_connection_pool()
    try:
        identity = await person_by_email(email)
        if identity is None:
            sys.exit(f"no person {email}: seed first")
        principal = Principal(person_id=identity.person_id, email=identity.email)
        found: dict[str, list[str]] = {}
        for question in questions:
            async with transaction_scope():
                hits = await search_pages(
                    principal, question.text, cove="personal", limit=DEPTH
                )
            found[question.id] = [h["path"] for h in hits if h["kind"] == "page"]
        return found
    finally:
        await DB.close_connection_pool()


def line(report: evalkit.Report) -> str:
    """Lay one report out as text, a line per question type.

    :param report: the report
    :returns: the table
    """
    rows = [*sorted(report.by_kind.items()), ("all", report.overall)]
    out = [f"{report.mode}"]
    for name, tally in rows:
        s = tally.summary()
        out.append(
            f"  {name:<28}{s['questions']:>4}{s['recall_any']:>8.1%}"
            f"{s['recall_all']:>8.1%}{s['mrr']:>7.3f}"
        )
    return "\n".join(out)


def main() -> None:
    """Parse the command line, embed, score and print."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--set", choices=["benchmark", "private"], required=True)
    parser.add_argument("--model", choices=sorted(MODELS), required=True)
    parser.add_argument("--prefix", action="store_true")
    parser.add_argument(
        "--sections",
        action="store_true",
        help="cut pages as the product does: by heading, with context in front",
    )
    parser.add_argument("--questions", type=int, default=100)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    evalkit.refuse_unless_local(get_settings().dsn)

    if args.set == "private":
        pages = evalkit.private_pages(json.loads(evalkit.PRIVATE_EXPORT.read_text()))
        questions = evalkit.private_questions(
            json.loads(evalkit.PRIVATE_QUESTIONS.read_text()), (p.path for p in pages)
        )
        email, strip = evalkit.PRIVATE_EMAIL, ""
    else:
        eval_set = evalkit.convert(
            evalkit.choose(evalkit.load_benchmark(), args.questions)
        )
        pages, questions = list(eval_set.pages), list(eval_set.questions)
        email, strip = evalkit.EVAL_EMAIL, ""

    model = MODELS[args.model]
    how = "sections" if args.sections else "prefix" if args.prefix else "plain"
    tag = f"{args.set}-{args.model}-{how}"
    vectors, owner = page_vectors(
        pages, model, "sections" if args.sections else args.prefix, tag
    )
    paths = [page.path.removeprefix(strip) for page in pages]
    queries = embed(model["name"], [model["query"].format(q=q.text) for q in questions])

    lexical = asyncio.run(lexical_rankings(email, questions))
    semantic = {
        q.id: semantic_ranking(queries[i], vectors, owner, paths)
        for i, q in enumerate(questions)
    }
    reports = [
        evalkit.score("keyword", questions, lexical),
        evalkit.score(f"embeddings ({tag})", questions, semantic),
    ]
    for weight in WEIGHTS:
        hybrid = {q.id: fuse(lexical[q.id], semantic[q.id], weight) for q in questions}
        reports.append(evalkit.score(f"hybrid x{weight:g} ({tag})", questions, hybrid))
    print(f"{'':<30}{'n':>4}{'any@5':>8}{'all@5':>8}{'MRR':>7}")
    print("\n".join(line(report) for report in reports))
    if args.json:
        args.json.write_text(
            json.dumps([r.to_dict() for r in reports], ensure_ascii=False, indent=1)
        )


if __name__ == "__main__":
    main()
