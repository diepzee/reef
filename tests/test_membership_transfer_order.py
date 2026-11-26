"""Check the reverse ordering of ownership transfer and membership departure."""

import asyncio

import asyncpg

from reef.config import get_settings


async def test_transfer_waits_for_departure_before_checking_successor(
    household,
) -> None:
    """A transfer must not assign ownership to a member whose departure commits."""
    owner = await asyncpg.connect(get_settings().database.test_url)
    departing = await asyncpg.connect(get_settings().database.test_url)
    pending = None
    try:
        async with owner.transaction():
            await owner.execute(
                "SELECT set_config('app.person_id', $1, true)",
                str(household["wouter"].id),
            )
            async with departing.transaction():
                await departing.execute(
                    "SELECT set_config('app.person_id', $1, true)",
                    str(household["partner"].id),
                )
                result = await departing.fetchrow(
                    "SELECT * FROM reef_remove_member($1, $2)",
                    household["shared"].id,
                    household["partner"].id,
                )
                assert tuple(result) == (True, False)
                pending = asyncio.create_task(
                    owner.fetchval(
                        "SELECT reef_transfer_space_ownership($1, $2)",
                        household["shared"].id,
                        household["partner"].id,
                    )
                )
                blocked = False
                async with asyncio.timeout(5):
                    while not pending.done():
                        blockers = await departing.fetchval(
                            "SELECT pg_blocking_pids($1)", owner.get_server_pid()
                        )
                        if departing.get_server_pid() in blockers:
                            blocked = True
                            break
                        await asyncio.sleep(0)
                assert blocked
            assert await pending is False
            assert (
                await owner.fetchval(
                    "SELECT owner_person_id FROM spaces WHERE id = $1",
                    household["shared"].id,
                )
                == household["wouter"].id
            )
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await departing.close()
        await owner.close()
