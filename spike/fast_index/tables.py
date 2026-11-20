"""Turn the measurement and LLM-run results into the report's Markdown tables.

Spike code. Reads only the aggregate files :mod:`measure` and
:mod:`llm.run` write, so it never sees engram text.

Run as::

    .venv/bin/python spike/fast_index/tables.py spike/fast_index/results \
        spike/fast_index/results/llm_runs.jsonl
"""

import json
import sys
from pathlib import Path

ORDER = ["private", "b100", "b500", "b2000", "b5000"]
VARIANTS = ["today", "slim", "v1", "v2", "v3", "v4_8k", "v4_16k"]


def k(chars: int) -> str:
    """Write a character count in thousands.

    :param chars: characters
    :returns: for example ``12.5k``
    """
    return f"{chars / 1000:,.1f}k"


def pct(share: float | None) -> str:
    """Write a share as a whole percentage.

    :param share: from 0 to 1, or None
    :returns: for example ``82%``
    """
    return "–" if share is None else f"{share * 100:.0f}%"


def cost_table(results: dict[str, dict]) -> str:
    """Today's size and latency per memory size.

    :param results: per dataset
    :returns: Markdown
    """
    rows = [
        "| Memory | Engrams | Files | Payload | Tokens (est.) | Index without protocol | Median | p95 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, r in results.items():
        today = r["variants"]["today"]
        rows.append(
            f"| {name} | {r['engrams']:,} | {r['files']:,} | {k(today['total_chars'])} | "
            f"{today['total_tokens_est']:,} | {k(today['index_chars'])} | "
            f"{r['today_latency']['median_ms']:.0f} ms | {r['today_latency']['p95_ms']:.0f} ms |"
        )
    return "\n".join(rows)


def breakdown_table(results: dict[str, dict], top: int = 8) -> str:
    """The largest parts of today's payload, per memory.

    :param results: per dataset
    :param top: how many parts to show
    :returns: Markdown
    """
    rows = []
    for name, r in results.items():
        total = sum(r["today_breakdown"].values())
        parts = ", ".join(
            f"{part} {chars * 100 / total:.0f}%"
            for part, chars in list(r["today_breakdown"].items())[:top]
        )
        rows.append(f"- **{name}**: {parts}")
    return "\n".join(rows)


def variant_table(r: dict) -> str:
    """Every variant on one memory.

    :param r: one dataset's results
    :returns: Markdown
    """
    rows = [
        "| Variant | Payload | Index only | Lines | Visible | In view after 1 expansion | Routed@3 | Two-step found | Chars expanded (median) | Build |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name in VARIANTS:
        v = r["variants"].get(name)
        if v is None:
            continue
        rows.append(
            f"| {name} | {k(v['total_chars'])} | {k(v['index_chars'])} | {v['lines']:,} | "
            f"{pct(v['visible_engrams'])} | {pct(v['reachable_engrams'])} | "
            f"{pct(v.get('routed_at_3'))} | {pct(v.get('two_step_found'))} | "
            f"{k(v.get('two_step_extra_chars_median', 0))} | {v['build']['median_ms']:.0f} ms |"
        )
    return "\n".join(rows)


def llm_table(path: Path) -> str:
    """The LLM-in-the-loop runs, per variant and task.

    :param path: the runs' ``results.jsonl``
    :returns: Markdown
    """
    runs = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    rows = [
        "| Variant | Task | Success | Tool calls | Calls | Input tokens | Index chars received | Wall |",
        "|---|---|---|---:|---|---:|---:|---:|",
    ]
    for run in runs:
        received = run["result_chars"].get("load_index", 0)
        rows.append(
            f"| {run['variant']} | {run['task']} | {'yes' if run['text_ok'] else 'no'} | "
            f"{run['tool_calls']} | {', '.join(run['calls'])} | {run['total_input_tokens']:,} | "
            f"{received:,} | {run['wall_s']:.0f} s |"
        )
    totals = [
        "",
        "| Variant | Successes | Tool calls | Input tokens | Wall |",
        "|---|---:|---:|---:|---:|",
    ]
    for variant in dict.fromkeys(run["variant"] for run in runs):
        mine = [run for run in runs if run["variant"] == variant]
        totals.append(
            f"| {variant} | {sum(1 for r in mine if r['text_ok'])}/{len(mine)} | "
            f"{sum(r['tool_calls'] for r in mine)} | "
            f"{sum(r['total_input_tokens'] for r in mine):,} | "
            f"{sum(r['wall_s'] for r in mine):.0f} s |"
        )
    return "\n".join(rows + totals)


def main() -> None:
    """Print every table."""
    results_dir = Path(sys.argv[1])
    results = {}
    for name in ORDER:
        path = results_dir / f"{name}.json"
        if path.exists():
            results[name] = json.loads(path.read_text())
            # Latency re-measured on a quiet machine, when there is such a file.
            quiet = results_dir / f"lat_{name}.json"
            if quiet.exists():
                lat = json.loads(quiet.read_text())
                results[name]["today_latency"] = lat["today_latency"]
                results[name]["vectors_latency"] = lat["vectors_latency"]
                for key, variant in lat["variants"].items():
                    results[name]["variants"][key]["build"] = variant["build"]
    print("## Cost today\n")
    print(cost_table(results))
    print("\n## Where the size goes\n")
    print(breakdown_table(results))
    for name, r in results.items():
        print(
            f"\n## {name}: {r['engrams']:,} engrams, {r['questions']} questions, {r['expected_refs']} expected\n"
        )
        print(variant_table(r))
        print(
            f"\nvectors read: median {r['vectors_latency']['median_ms']:.0f} ms; "
            f"with inbound links: {r['with_inbound']}; with use: {r['with_activation']}"
        )
    if len(sys.argv) > 2:
        print("\n## LLM runs\n")
        runs = Path(sys.argv[2])
        print(llm_table(runs if runs.is_file() else runs / "results.jsonl"))


if __name__ == "__main__":
    main()
