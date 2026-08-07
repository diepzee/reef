"""Arm row-level security on ``promotions``.

The table shipped without a policy. Its rows are share nonces, and
``section_text`` holds the exact span extracted from a personal page, so an
unprotected row is an unprotected private paragraph. ``confirm_promotion``
fetched the row by id and compared ``person_id`` in Python afterwards --
correct, but the correct-if-nobody-slips shape the RLS design exists to
replace.

Idempotent by construction (see ``rls.promotion_statements``): databases
created after this change already get the policy from ``enable_statements``,
and re-running drops the policy before recreating it.
"""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from rif.db import DB
from rif.rls import promotion_statements

ID = "2026-08-07T11:00:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "row-level security on promotions"


async def forwards() -> MigrationManager:
    """Turn on RLS for promotions and attach the ownership policy.

    :returns: the migration manager
    """
    manager = MigrationManager(migration_id=ID, app_name="rif", description=DESCRIPTION)

    async def run() -> None:
        """Execute the DDL, in order."""
        for statement in promotion_statements():
            await DB._run_in_new_connection(statement)

    manager.add_raw(run)
    return manager
