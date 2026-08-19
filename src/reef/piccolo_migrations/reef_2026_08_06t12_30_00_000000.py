"""Seed the first principal and the household space.

Only Wouter is seeded here, deliberately. The second member's row needs her
real email -- it is the key her first login binds against -- and seeding a
placeholder would put an unusable address in production that no later run of
this migration would correct, since migrations do not re-run. She is added
by her own migration once that address is settled.

The household space is created now regardless: it is not owned by anyone
(``owner_person_id`` is NULL), so it does not depend on her existing, and it
lets the household alias resolve for a single member in the meantime.
"""

from uuid import UUID

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from reef.db import DB

ID = "2026-08-06T12:30:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "seed the first principal, his personal space, and the household space"

WOUTER = UUID("11111111-1111-1111-1111-111111111111")
W_SPACE = UUID("33333333-3333-3333-3333-333333333333")
SHARED = UUID("55555555-5555-5555-5555-555555555555")


async def forwards() -> MigrationManager:
    """Insert the first person, his personal space, and the household space.

    :returns: the migration manager
    """
    manager = MigrationManager(
        migration_id=ID, app_name="reef", description=DESCRIPTION
    )

    async def run() -> None:
        """Execute the seed inserts, parents before children."""
        await DB._run_in_new_connection(
            "INSERT INTO persons (id, email, display_name) VALUES "
            f"('{WOUTER}', 'wouter@rugvin.be', 'Wouter')"
        )
        await DB._run_in_new_connection(
            "INSERT INTO spaces (id, slug, kind, owner_person_id, version) VALUES "
            f"('{W_SPACE}', 'wouter', 'personal', '{WOUTER}', 0), "
            f"('{SHARED}', 'school', 'household', NULL, 0)"
        )
        await DB._run_in_new_connection(
            "INSERT INTO memberships (person_id, space_id) VALUES "
            f"('{WOUTER}', '{W_SPACE}'), ('{WOUTER}', '{SHARED}')"
        )

    manager.add_raw(run)
    return manager
