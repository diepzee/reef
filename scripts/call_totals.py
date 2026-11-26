"""Print how often each tool was called, and how the calls ended, across everybody.

Read-only. It asks :func:`reef.calllog.totals`, which returns totals only,
and prints a header and one line per method and outcome: how many calls, and
how many came without a reason. Nothing else is printed: no person, reason,
session or time, and not how many people.

Run as ``python scripts/call_totals.py [--days N]``, or ``just call-totals``.
It counts the last 30 whole days in UTC, ending at the start of today, unless
told otherwise; from 7 to 89 days. Against production, run it inside
``reef-app``, so it uses the app's own database role::

    railway ssh -s reef-app -- uv run --frozen --no-dev \\
      python scripts/call_totals.py --days 30
"""

import argparse
import asyncio

from reef.calllog import Total, totals
from reef.rls import (
    CALL_LOG_TOTALS_COLUMNS,
    CALL_LOG_TOTALS_MAX_DAYS,
    CALL_LOG_TOTALS_MIN_DAYS,
)


def render(rows: list[Total]) -> str:
    """Lay totals out as a table: a header, then one line per total.

    :param rows: the totals
    :returns: the table, without a final newline
    """
    table = [
        CALL_LOG_TOTALS_COLUMNS,
        *(
            (row.method, row.outcome, str(row.calls), str(row.empty_reasons))
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
    """Count the calls of the last ``days`` whole days, as a table.

    :param days: how many whole days to count
    :returns: the table
    """
    return render(await totals(days))


def main() -> None:
    """Read the number of days, and print the table."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help=(
            f"how many whole days to count, from {CALL_LOG_TOTALS_MIN_DAYS} "
            f"to {CALL_LOG_TOTALS_MAX_DAYS} (30)"
        ),
    )
    days = parser.parse_args().days
    if not CALL_LOG_TOTALS_MIN_DAYS <= days <= CALL_LOG_TOTALS_MAX_DAYS:
        parser.error(
            f"--days must be from {CALL_LOG_TOTALS_MIN_DAYS} to {CALL_LOG_TOTALS_MAX_DAYS}"
        )
    print(asyncio.run(report(days)))


if __name__ == "__main__":
    main()
