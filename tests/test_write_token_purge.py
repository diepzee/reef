"""Expired confirmation records leave through the worker's private purge."""

import pytest

from reef.worker import purge_old_logs


def test_tokens_expire_well_before_records_are_purged() -> None:
    """Keep at least ten token lifetimes before a use record may be removed."""
    from reef.methods.staged import TOKEN_TTL_SECONDS
    from reef.rls import WRITE_TOKEN_RETENTION_HOURS

    assert TOKEN_TTL_SECONDS * 10 < WRITE_TOKEN_RETENTION_HOURS * 3600


def test_frozen_migration_matches_current_token_rules() -> None:
    """The frozen migration and the fixture must install the same token rules."""
    from reef.piccolo_migrations.reef_2026_11_27t08_00_00_000000 import STATEMENTS
    from reef.rls import write_token_statements

    assert STATEMENTS[1:] == write_token_statements()


@pytest.mark.parametrize("zone", ["UTC", "Pacific/Kiritimati", "America/Los_Angeles"])
async def test_purge_removes_only_records_older_than_a_day(
    household, seed, probe, zone
) -> None:
    """A caller without an identity can purge, but learns no rows or count."""
    person = household["wouter"].id
    await seed.execute(
        "INSERT INTO used_write_tokens (id, person_id, token_digest, used_at) VALUES "
        "(gen_random_uuid(), $1, 'old', now() - interval '25 hours'), "
        "(gen_random_uuid(), $1, 'young', now() - interval '23 hours')",
        person,
    )
    async with probe.transaction():
        await probe.execute("SELECT set_config('TimeZone', $1, true)", zone)
        assert await probe.fetch("SELECT * FROM used_write_tokens") == []
        assert await probe.fetchval("SELECT reef_purge_write_tokens()") is None
    assert [
        row["token_digest"]
        for row in await seed.fetch("SELECT token_digest FROM used_write_tokens")
    ] == ["young"]


async def test_worker_purges_tokens_without_counting_them(household, seed) -> None:
    """The hourly worker removes old token rows alongside its log purges."""
    await seed.execute(
        "INSERT INTO used_write_tokens (id, person_id, token_digest, used_at) "
        "VALUES (gen_random_uuid(), $1, 'old', now() - interval '25 hours')",
        household["wouter"].id,
    )
    assert await purge_old_logs() == 0
    assert await seed.fetchval("SELECT count(*) FROM used_write_tokens") == 0
