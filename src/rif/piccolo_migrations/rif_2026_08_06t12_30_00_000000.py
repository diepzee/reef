"""Seed the two-person, two-personal-plus-one-household access topology.

This is the actual household this deployment serves, not a fixture --
hand-written and reviewed rather than generated. ``<HER-EMAIL>`` and
``<HER-NAME>`` are deliberate literal placeholders: her identity is hers to
give, not the implementer's to guess. Fill both in from a verified source
before running this migration for real.
"""

from uuid import UUID

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from rif.db import DB

ID = "2026-08-06T12:30:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "seed persons, spaces and memberships"

WOUTER = UUID("11111111-1111-1111-1111-111111111111")
PARTNER = UUID("22222222-2222-2222-2222-222222222222")
W_SPACE = UUID("33333333-3333-3333-3333-333333333333")
P_SPACE = UUID("44444444-4444-4444-4444-444444444444")
SHARED = UUID("55555555-5555-5555-5555-555555555555")


async def forwards() -> MigrationManager:
    """Insert the household rows.

    :returns: the migration manager
    """
    manager = MigrationManager(migration_id=ID, app_name="rif", description=DESCRIPTION)

    async def run() -> None:
        """Execute the seed inserts, parents before children."""
        await DB._run_in_new_connection(
            "INSERT INTO persons (id, email, display_name) VALUES "
            f"('{WOUTER}', 'wouter@rugvin.be', 'Wouter'), "
            f"('{PARTNER}', '<HER-EMAIL>', '<HER-NAME>')"
        )
        await DB._run_in_new_connection(
            "INSERT INTO spaces (id, slug, kind, owner_person_id, version) VALUES "
            f"('{W_SPACE}', 'wouter', 'personal', '{WOUTER}', 0), "
            f"('{P_SPACE}', 'partner', 'personal', '{PARTNER}', 0), "
            f"('{SHARED}', 'school', 'household', NULL, 0)"
        )
        await DB._run_in_new_connection(
            "INSERT INTO memberships (person_id, space_id) VALUES "
            f"('{WOUTER}', '{W_SPACE}'), ('{PARTNER}', '{P_SPACE}'), "
            f"('{WOUTER}', '{SHARED}'), ('{PARTNER}', '{SHARED}')"
        )

    manager.add_raw(run)
    return manager
