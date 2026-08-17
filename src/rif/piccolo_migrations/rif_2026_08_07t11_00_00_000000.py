"""Arm row-level security on ``promotions``.

The table shipped without a policy. Its rows are share nonces, and
``section_text`` holds the exact span extracted from a personal page, so an
unprotected row is an unprotected private paragraph. ``confirm_promotion``
fetched the row by id and compared ``person_id`` in Python afterwards --
correct, but the correct-if-nobody-slips shape the RLS design exists to
replace.

Idempotent by construction: re-running drops the policy before recreating it.

Holds a **frozen snapshot** of the DDL as it stood on 7 August rather than
importing ``rif.rls`` live, which is what it used to do. See the module
docstring of ``2026-08-06T12:20:00`` for why history is frozen and only the
newest migration re-applies the current policies.
"""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from rif.db import DB

ID = "2026-08-07T11:00:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "row-level security on promotions"

STATEMENTS = [
    "ALTER TABLE promotions ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE promotions FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS promotions_owner ON promotions",
    (
        "CREATE POLICY promotions_owner ON promotions USING (person_id = "
        "NULLIF(current_setting('app.person_id', true), '')::uuid) WITH CHECK "
        "(person_id = NULLIF(current_setting('app.person_id', true), '')::uuid)"
    ),
]


async def forwards() -> MigrationManager:
    """Turn on RLS for promotions and attach the ownership policy.

    :returns: the migration manager
    """
    manager = MigrationManager(migration_id=ID, app_name="rif", description=DESCRIPTION)

    async def run() -> None:
        """Execute the DDL, in order."""
        for statement in STATEMENTS:
            await DB._run_in_new_connection(statement)

    manager.add_raw(run)
    return manager
