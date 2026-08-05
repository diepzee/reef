"""seed persons and spaces

Revision ID: d8567d9ee6dc
Revises: 0f1d29c16349
Create Date: 2026-08-05 17:17:43.864481

"""
from collections.abc import Sequence
from uuid import uuid4

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd8567d9ee6dc'
down_revision: str | Sequence[str] | None = '0f1d29c16349'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WOUTER = str(uuid4())
PARTNER = str(uuid4())
W_SPACE, P_SPACE, SHARED = str(uuid4()), str(uuid4()), str(uuid4())


def upgrade() -> None:
    """Seed the two-person, two-personal-plus-one-household access topology.

    This is the actual household this deployment serves, not a fixture —
    hand-written and reviewed rather than generated. ``<HER-EMAIL>`` and
    ``<HER-NAME>`` are deliberate literal placeholders: her identity is
    hers to give, not the implementer's to guess. Fill both in from a
    verified source before running this migration for real.
    """
    op.execute(f"""
        INSERT INTO persons (id, email, display_name) VALUES
        ('{WOUTER}', 'wouter@rugvin.be', 'Wouter'),
        ('{PARTNER}', '<HER-EMAIL>', '<HER-NAME>')
    """)
    op.execute(f"""
        INSERT INTO spaces (id, slug, kind, owner_person_id, version) VALUES
        ('{W_SPACE}', 'wouter', 'PERSONAL', '{WOUTER}', 0),
        ('{P_SPACE}', 'partner', 'PERSONAL', '{PARTNER}', 0),
        ('{SHARED}', 'school', 'HOUSEHOLD', NULL, 0)
    """)
    op.execute(f"""
        INSERT INTO memberships (person_id, space_id) VALUES
        ('{WOUTER}', '{W_SPACE}'), ('{PARTNER}', '{P_SPACE}'),
        ('{WOUTER}', '{SHARED}'), ('{PARTNER}', '{SHARED}')
    """)


def downgrade() -> None:
    """Remove the seeded persons, spaces, and memberships, by id."""
    op.execute(
        f"DELETE FROM memberships WHERE space_id IN "
        f"('{W_SPACE}', '{P_SPACE}', '{SHARED}')"
    )
    op.execute(f"DELETE FROM spaces WHERE id IN ('{W_SPACE}', '{P_SPACE}', '{SHARED}')")
    op.execute(f"DELETE FROM persons WHERE id IN ('{WOUTER}', '{PARTNER}')")
