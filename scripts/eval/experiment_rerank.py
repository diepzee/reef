"""Offline experiment: does a reranker lift hybrid search?

Run as::

    uv run --with numpy --with sentence-transformers python \
        scripts/eval/experiment_rerank.py --set benchmark|private \
        [--sections] [--questions N] [--reranker NAME ...]

Run ``experiment_embed.py`` for the same set first: this reads its cached
vectors. Nothing here is part of the product.

For each question it takes the first :data:`CANDIDATES` pages of hybrid
search, lets a cross-encoder read the question next to each page's best
passages, and orders the pages by that score. A cross-encoder reads both
texts together, so it is more exact than comparing two vectors, and far too
slow to run over every page. Hence: retrieve widely, then rerank a few.

The reranker runs on this machine. Its weights are downloaded once from
Hugging Face. No page text leaves the machine.
"""

import argparse
import asyncio
import json
import time
from pathlib import Path

import evalkit
import experiment_embed as base
import numpy as np

from reef.config import get_settings

#: How many pages of hybrid search the reranker gets to reorder.
CANDIDATES = 30

#: How many of a page's best passages the reranker reads.
PASSAGES = 2

#: The weight of the embeddings in the fusion that picks the candidates.
WEIGHT = 3.0

#: The rerankers that can be tried, by short name.
RERANKERS = {
    # Multilingual, 568M parameters, MIT licence.
    "bge-m3": "BAAI/bge-reranker-v2-m3",
    # Multilingual, 118M parameters. The cheap one.
    "minilm": "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
}


def best_passages(
    query: np.ndarray, vectors: np.ndarray, owner: np.ndarray, page: int
) -> list[int]:
    """Pick the passages of one page that are closest to the question.

    :param query: the question's vector
    :param vectors: every chunk vector
    :param owner: for each chunk the index of its page
    :param page: the page's index
    :returns: the indexes of its :data:`PASSAGES` best chunks
    """
    mine = np.flatnonzero(owner == page)
    scores = vectors[mine] @ query
    return [int(i) for i in mine[np.argsort(-scores)[:PASSAGES]]]


def main() -> None:
    """Parse the command line, rerank, score and print."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--set", choices=["benchmark", "private"], required=True)
    parser.add_argument("--model", choices=sorted(base.MODELS), default="gemma")
    parser.add_argument("--sections", action="store_true")
    parser.add_argument("--questions", type=int, default=500)
    parser.add_argument(
        "--reranker", action="append", choices=sorted(RERANKERS), dest="rerankers"
    )
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    evalkit.refuse_unless_local(get_settings().dsn)

    if args.set == "private":
        pages = evalkit.private_pages(json.loads(evalkit.PRIVATE_EXPORT.read_text()))
        questions = evalkit.private_questions(
            json.loads(evalkit.PRIVATE_QUESTIONS.read_text()), (p.path for p in pages)
        )
        email = evalkit.PRIVATE_EMAIL
    else:
        eval_set = evalkit.convert(
            evalkit.choose(evalkit.load_benchmark(), args.questions)
        )
        pages, questions = list(eval_set.pages), list(eval_set.questions)
        email = evalkit.EVAL_EMAIL

    model = base.MODELS[args.model]
    how = "sections" if args.sections else "plain"
    tag = f"{args.set}-{args.model}-{how}"
    prefix = "sections" if args.sections else False
    texts, _ = base.pieces(pages, prefix)
    vectors, owner = base.page_vectors(pages, model, prefix, tag)
    paths = [page.path for page in pages]
    index = {path: i for i, path in enumerate(paths)}
    queries = base.embed(
        model["name"], [model["query"].format(q=q.text) for q in questions]
    )
    lexical = asyncio.run(base.lexical_rankings(email, questions))

    candidates: dict[str, list[str]] = {}
    for i, question in enumerate(questions):
        semantic = base.semantic_ranking(queries[i], vectors, owner, paths)
        fused = base.fuse(lexical[question.id], semantic, WEIGHT)
        candidates[question.id] = fused[:CANDIDATES]

    reports = [
        evalkit.score("keyword", questions, lexical),
        evalkit.score(f"hybrid x{WEIGHT:g}", questions, candidates),
    ]
    reach = sum(bool(set(q.expected) & set(candidates[q.id])) for q in questions) / len(
        questions
    )
    print(f"a right page is among the {CANDIDATES} candidates for {reach:.1%}")

    from sentence_transformers import CrossEncoder

    for name in args.rerankers or sorted(RERANKERS):
        encoder = CrossEncoder(RERANKERS[name], max_length=512)
        started = time.monotonic()
        reranked: dict[str, list[str]] = {}
        for i, question in enumerate(questions):
            pairs: list[tuple[str, str]] = []
            pair_page: list[str] = []
            for path in candidates[question.id]:
                # A page keyword search found may be missing from the
                # embedded set only if the two sets differ. Skip it then.
                if path not in index:
                    continue
                for chunk in best_passages(queries[i], vectors, owner, index[path]):
                    pairs.append((question.text, texts[chunk]))
                    pair_page.append(path)
            scores = encoder.predict(pairs, batch_size=16, show_progress_bar=False)
            best: dict[str, float] = {}
            for path, score in zip(pair_page, scores, strict=True):
                best[path] = max(best.get(path, -1e9), float(score))
            reranked[question.id] = sorted(best, key=best.get, reverse=True)
            if i and i % 50 == 0:
                rate = (time.monotonic() - started) / i
                left = (len(questions) - i) * rate / 60
                print(
                    f"  {name}: {i}/{len(questions)}, {left:.0f} min left", flush=True
                )
        seconds = (time.monotonic() - started) / len(questions)
        print(f"{name}: {seconds:.2f} seconds per question")
        reports.append(evalkit.score(f"hybrid + rerank ({name})", questions, reranked))

    print(f"{'':<30}{'n':>4}{'any@5':>8}{'all@5':>8}{'MRR':>7}")
    print("\n".join(base.line(report) for report in reports))
    if args.json:
        args.json.write_text(
            json.dumps([r.to_dict() for r in reports], ensure_ascii=False, indent=1)
        )


if __name__ == "__main__":
    main()
