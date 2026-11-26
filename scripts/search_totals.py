"""Print how many searches missed at least one read, across everybody.

Read-only. It asks :func:`reef.searchlog.totals`, which returns two counts,
and prints a header and one line: searches and searches that missed.
No query, person, engram, session or time, and not how many people.

Run as ``python scripts/search_totals.py [--days N]``, or ``just search-totals``.
It counts the last 30 whole days in UTC, ending at the start of today, unless
told otherwise; from 7 to 89 days. Use the app's own database role, as for
``scripts/call_totals.py``.
"""

import argparse
import asyncio

from reef.rls import (
    SEARCH_LOG_TOTALS_COLUMNS,
    SEARCH_LOG_TOTALS_MAX_DAYS,
    SEARCH_LOG_TOTALS_MIN_DAYS,
)
from reef.searchlog import Total, totals


def render(row: Total) -> str:
    """Lay out the counts as a header and one numeric row.

    :param row: the totals
    :returns: the table, without a final newline
    """
    table = [SEARCH_LOG_TOTALS_COLUMNS, (str(row.searches), str(row.misses))]
    widths = [max(len(line[i]) for line in table) for i in range(len(table[0]))]
    return "\n".join(
        "  ".join(
            cell.ljust(width) for cell, width in zip(line, widths, strict=True)
        ).rstrip()
        for line in table
    )


async def report(days: int) -> str:
    """Count searches of the last ``days`` whole days, as a table.

    :param days: how many whole days to count
    :returns: the table
    """
    return render(await totals(days))


def main() -> None:
    """Read the number of days and print the table."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help=(
            f"how many whole days to count, from {SEARCH_LOG_TOTALS_MIN_DAYS} "
            f"to {SEARCH_LOG_TOTALS_MAX_DAYS} (30)"
        ),
    )
    days = parser.parse_args().days
    if not SEARCH_LOG_TOTALS_MIN_DAYS <= days <= SEARCH_LOG_TOTALS_MAX_DAYS:
        parser.error(
            f"--days must be from {SEARCH_LOG_TOTALS_MIN_DAYS} to {SEARCH_LOG_TOTALS_MAX_DAYS}"
        )
    print(asyncio.run(report(days)))


if __name__ == "__main__":
    main()
