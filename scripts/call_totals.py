"""Print how often each tool was called, and how the calls ended, across everybody.

Read-only. It asks :func:`reef.calllog.totals`, which returns totals only,
and prints a header and one line per method and outcome: how many calls, how
many came without a reason, and how many people made them. Nothing else is
printed: no person, reason, session or time.

Run as ``python scripts/call_totals.py [--days N]``, or ``just call-totals``.
It counts the last 30 days unless told otherwise. Against production, run it
inside ``reef-app``, so it uses the app's own database role::

    railway ssh -s reef-app -- uv run --frozen --no-dev \\
      python scripts/call_totals.py --days 30
"""

import argparse
import asyncio
from datetime import UTC, datetime, timedelta

from reef.calllog import Total, totals
from reef.rls import CALL_LOG_TOTALS_COLUMNS


def render(rows: list[Total]) -> str:
    """Lay totals out as a table: a header, then one line per total.

    :param rows: the totals
    :returns: the table, without a final newline
    """
    table = [
        CALL_LOG_TOTALS_COLUMNS,
        *(
            (
                row.method,
                row.outcome,
                str(row.calls),
                str(row.empty_reasons),
                str(row.people),
            )
            for row in rows
        ),
    ]
    widths = [max(len(line[i]) for line in table) for i in range(len(table[0]))]
    return "\n".join(
        "  ".join(
            cell.ljust(width) for cell, width in zip(line, widths, strict=True)
        ).rstrip()
        for line in table
    )


async def report(days: int) -> str:
    """Count the calls of the last ``days`` days, as a table.

    :param days: how many days back to count
    :returns: the table
    """
    return render(await totals(datetime.now(UTC) - timedelta(days=days)))


def main() -> None:
    """Read the number of days, and print the table."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--days", type=int, default=30, help="how many days back to count (30)"
    )
    days = parser.parse_args().days
    if days < 1:
        parser.error("--days must be at least 1")
    print(asyncio.run(report(days)))


if __name__ == "__main__":
    main()
