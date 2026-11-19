"""The migration that creates the avatar helpers production never got."""

import importlib

from reef.access import Principal, arm
from reef.coves import member_roster
from reef.db import DB, run_ddl_atomically
from reef.models import Person
from reef.rls import drop_avatar_statements

MIGRATION = importlib.import_module(
    "reef.piccolo_migrations.reef_2026_11_19t16_00_00_000000"
)

HELPERS = ("reef_member_faces", "reef_member_avatar")


async def _present() -> set[str]:
    rows = await Person.raw(
        "SELECT proname FROM pg_proc WHERE proname = ANY({}::text[])", list(HELPERS)
    )
    return {row["proname"] for row in rows}


async def test_it_restores_helpers_missing_as_in_production(tx, household):
    """Production lacks both helpers; after the migration a roster works."""
    for statement in drop_avatar_statements():
        await DB._run_in_new_connection(statement)
    assert await _present() == set()

    await run_ddl_atomically(MIGRATION.STATEMENTS)

    assert await _present() == set(HELPERS)
    owner = household["wouter"]
    await arm(Principal(person_id=owner.id, email=owner.email))
    roster = await member_roster(household["shared"].id)
    assert {member["person_id"] for member in roster} == {
        household["wouter"].id,
        household["partner"].id,
    }


async def test_it_is_harmless_where_the_helpers_exist(tx, household):
    await run_ddl_atomically(MIGRATION.STATEMENTS)
    assert await _present() == set(HELPERS)
