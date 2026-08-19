"""Let the people who share a cove with you see your face."""

from piccolo.apps.migrations.auto.migration_manager import MigrationManager

from reef.db import run_ddl_atomically
from reef.rls import avatar_statements, disclosure_statements

ID = "2026-08-13T14:00:00:000000"
VERSION = "1.36.0"
DESCRIPTION = "roster person ids, and the co-member avatar functions"


async def forwards() -> MigrationManager:
    """Add the functions that disclose a co-member's picture, and a key for it.

    A person's avatar was reachable only through ``/api/me/avatar``, which is
    scoped to the caller by construction. ``persons`` is self-only under RLS,
    so nothing else could read it either: every member of a cove rendered as
    a coloured initial to everybody else in that cove, including on their own
    roster.

    Two functions carry the rule, both demanding that the caller *and* the
    person asked about are members of the cove named. See
    :func:`reef.rls.avatar_statements` for why the second half is not
    redundant, and for why this group is deliberately absent from
    ``enable_statements``: it names ``persons.avatar_bytes``, which three
    historical migrations predate, and a ``LANGUAGE sql`` body is validated
    at creation.

    ``reef_roster`` is dropped rather than replaced. It gains ``person_id``,
    and widening a ``RETURNS TABLE`` is a signature change -- ``CREATE OR
    REPLACE`` refuses it outright, and creating alongside would leave two
    candidates and make every call ambiguous. The roster needs that id
    because ``email``, the only key it had, is blanked to ``''`` for everyone
    but the cove's owner: the members who most need to address a co-member
    had nothing to address them by.

    One transaction. Apart, there is a window where ``_space_members`` reads
    a ``person_id`` column the recreated function does not return yet, and
    every members panel 500s.

    :returns: configured migration manager
    """
    manager = MigrationManager(
        migration_id=ID, app_name="reef", description=DESCRIPTION
    )

    async def run() -> None:
        await run_ddl_atomically(
            [
                "DROP FUNCTION IF EXISTS reef_roster(uuid)",
                *disclosure_statements(),
                *avatar_statements(),
            ]
        )

    manager.add_raw(run)
    return manager
