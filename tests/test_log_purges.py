"""Log retention uses UTC even when a caller changes their session time zone."""

import asyncpg
import pytest


@pytest.mark.parametrize("zone", ["UTC", "<+48>-48", "<-48>+48"])
@pytest.mark.parametrize(
    ("table", "column", "values"),
    [
        ("search_log", "searched_at", "query, engram_ids"),
        ("call_log", "called_at", "method, writer, outcome"),
    ],
)
async def test_purge_keeps_younger_rows_and_removes_older_rows_in_every_zone(
    household: dict,
    seed: asyncpg.Connection,
    zone: str,
    table: str,
    column: str,
    values: str,
) -> None:
    """An app caller cannot move either log's ninety-day UTC cutoff."""
    fields = (
        "'private query', '{}'"
        if table == "search_log"
        else "'find', 'assistant', 'ok'"
    )
    async with seed.transaction():
        await seed.execute(
            f"INSERT INTO {table} (id, person_id, {values}, {column}) "
            f"SELECT gen_random_uuid(), $1, {fields}, "
            "(now() AT TIME ZONE 'UTC') - interval '90 days' + age "
            "FROM unnest(ARRAY[interval '-1 hour', interval '1 hour']) AS age",
            household["wouter"].id,
        )
        await seed.execute("SET LOCAL ROLE reef_probe")
        await seed.execute("SELECT set_config('TimeZone', $1, true)", zone)
        removed = await seed.fetchval(f"SELECT reef_purge_{table}()")
        await seed.execute("RESET ROLE")
        left = await seed.fetch(
            f"SELECT {column} > (now() AT TIME ZONE 'UTC') - interval '90 days' "
            f"AS younger FROM {table}"
        )
        assert removed == 1
        assert [row["younger"] for row in left] == [True]
