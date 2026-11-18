"""Ask the benchmark questions in each search mode and report the scores.

Run as: ``python scripts/eval/run.py [--questions N] [--mode M ...] [--json PATH]``

Seed first with ``scripts/eval/seed.py``, using the same ``--questions``.

With ``--private`` it asks the questions in ``scripts/eval/private/`` of your
own pages instead.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

import evalkit
from seed import DEFAULT_QUESTIONS

from reef.access import Principal
from reef.config import get_settings
from reef.db import DB
from reef.identity import person_by_email


def render(report: evalkit.Report) -> str:
    """Lay a report out as text.

    :param report: the report
    :returns: one table, a line per question type
    """
    k = report.k
    lines = [
        f"mode: {report.mode}",
        f"{'type':<28}{'n':>5}{f'any@{k}':>9}{f'all@{k}':>9}{'MRR':>8}",
    ]
    rows = [*sorted(report.by_kind.items()), ("all", report.overall)]
    for name, tally in rows:
        s = tally.summary()
        lines.append(
            f"{name:<28}{s['questions']:>5}{s['recall_any']:>9.1%}"
            f"{s['recall_all']:>9.1%}{s['mrr']:>8.3f}"
        )
    return "\n".join(lines)


def _private_questions() -> list[evalkit.EvalQuestion]:
    """Read the private set's questions, checked against the export.

    :returns: the questions
    :raises FileNotFoundError: if the export or the questions are missing
    """
    for path in (evalkit.PRIVATE_EXPORT, evalkit.PRIVATE_QUESTIONS):
        if not path.exists():
            raise FileNotFoundError(f"{path} is missing. See scripts/eval/README.md")
    pages = evalkit.private_pages(json.loads(evalkit.PRIVATE_EXPORT.read_text()))
    return evalkit.private_questions(
        json.loads(evalkit.PRIVATE_QUESTIONS.read_text()), (p.path for p in pages)
    )


async def measure(
    count: int | None, modes: list[str], k: int, *, private: bool = False
) -> list[evalkit.Report]:
    """Run every asked-for mode over the chosen questions.

    :param count: how many questions, or None for all
    :param modes: the modes to run
    :param k: how many leading results count for recall
    :param private: ask the private set of your own pages, not the benchmark
    :returns: one report per mode
    """
    evalkit.refuse_unless_local(get_settings().dsn)
    if private:
        questions = _private_questions()
        email = evalkit.PRIVATE_EMAIL
    else:
        questions = evalkit.convert(
            evalkit.choose(evalkit.load_benchmark(), count)
        ).questions
        email = evalkit.EVAL_EMAIL
    await DB.start_connection_pool()
    try:
        identity = await person_by_email(email)
        if identity is None:
            sys.exit("no eval person: run scripts/eval/seed.py first")
        principal = Principal(person_id=identity.person_id, email=identity.email)
        return [await evalkit.run(principal, questions, mode, k) for mode in modes]
    finally:
        await DB.close_connection_pool()


def main() -> None:
    """Parse the command line, run, and print."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--questions", default=str(DEFAULT_QUESTIONS))
    parser.add_argument(
        "--mode", action="append", choices=sorted(evalkit.MODES), dest="modes"
    )
    parser.add_argument("-k", type=int, default=5)
    parser.add_argument(
        "--private", action="store_true", help="ask the private set instead"
    )
    parser.add_argument("--json", type=Path, help="also write full reports here")
    args = parser.parse_args()
    count = None if args.questions == "all" else int(args.questions)
    try:
        reports = asyncio.run(
            measure(
                count,
                args.modes or sorted(evalkit.MODES),
                args.k,
                private=args.private,
            )
        )
    except (evalkit.NotLocal, FileNotFoundError, ValueError) as error:
        sys.exit(str(error))
    print("\n\n".join(render(report) for report in reports))
    if args.json:
        args.json.write_text(
            json.dumps([r.to_dict() for r in reports], ensure_ascii=False, indent=1)
        )


if __name__ == "__main__":
    main()
