"""Permanent account deletion and its shared-space preservation rules."""

from conftest import _login

from reef.access import Principal, arm
from reef.account import delete_account_rows
from reef.db import transaction_scope
from reef.engrams import save_engram
from reef.models import (
    Attachment,
    AttachmentStatus,
    Engram,
    MemberRole,
    Revision,
)


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


async def _gone(seed, table: str, row_id) -> bool:
    """Report whether a row is absent, read past the identity policies.

    :param seed: the policy-free connection
    :param table: which table
    :param row_id: the primary key
    :returns: True when no such row remains
    """
    return not await seed.fetchval(
        f"SELECT count(*) FROM {table} WHERE id = $1", row_id
    )


async def test_delete_account_erases_private_data_and_preserves_shared_spaces(
    graph, seed
):
    alice = await graph.person("alice@x.com", "Alice")
    bob = await graph.person("bob@x.com", "Bob")
    invitee = await graph.person("invited@x.com", "Invited", invited_by=alice)
    personal = await graph.personal_space(alice)
    await graph.personal_space(bob)
    team = await graph.shared_space("team", alice, bob)
    solo = await graph.shared_space("solo", alice)
    principal = principal_for(alice)

    async with transaction_scope():
        private_engram = await save_engram(
            principal, "personal", "private.md", "private", message="private"
        )
        team_engram = await save_engram(
            principal, "team", "shared.md", "shared", message="shared"
        )
        solo_engram = await save_engram(
            principal, "solo", "solo.md", "solo", message="solo"
        )
        for space, engram, key in (
            (personal, private_engram, "attachments/private"),
            (solo, solo_engram, "attachments/solo"),
            (team, team_engram, "attachments/shared"),
        ):
            await Attachment(
                space_id=space.id,
                engram_id=engram.id,
                object_key=key,
                filename=f"{key.rsplit('/', 1)[-1]}.txt",
                mime="text/plain",
                byte_size=1,
                description=key,
                status=AttachmentStatus.READY.value,
            ).save()

        result = await delete_account_rows(principal)

    # Asserted after the transaction commits: the seed connection cannot see
    # uncommitted work, and the principal is erased by now so nothing is
    # armed that could see these rows through the policies either.
    assert set(result.deleted_spaces) == {"personal", "solo"}
    assert result.transferred_spaces == ["team"]
    assert set(result.file_keys) == {"attachments/private", "attachments/solo"}
    assert await _gone(seed, "persons", alice.id)
    assert await _gone(seed, "spaces", personal.id)
    assert await _gone(seed, "spaces", solo.id)

    surviving = await seed.fetchrow(
        "SELECT invited_by_person_id FROM persons WHERE id = $1", invitee.id
    )
    assert surviving is not None
    assert surviving["invited_by_person_id"] is None

    owner_id = await seed.fetchval(
        "SELECT owner_person_id FROM spaces WHERE id = $1", team.id
    )
    assert owner_id == bob.id
    assert not await seed.fetchval(
        "SELECT count(*) FROM memberships WHERE space_id = $1 AND person_id = $2",
        team.id,
        alice.id,
    )

    # Armed as the remaining member: shared content stays, but Alice's
    # identity is removed from its retained revision history.
    async with transaction_scope():
        await arm(principal_for(bob))
        assert (
            await Engram.objects().where(Engram.id == team_engram.id).first()
            is not None
        )
        revision = (
            await Revision.objects().where(Revision.engram_id == team_engram.id).first()
        )
        assert revision is not None and revision.author_id is None
        assert (
            await Attachment.objects()
            .where(Attachment.object_key == "attachments/shared")
            .first()
            is not None
        )


async def test_delete_account_promotes_a_viewer_who_inherits_ownership(graph, seed):
    alice = await graph.person("alice@x.com", "Alice")
    bob = await graph.person("bob@x.com", "Bob")
    await graph.personal_space(alice)
    team = await graph.shared_space("team", alice, bob)
    # Seeded: memberships has no UPDATE policy, because role changes belong
    # to the ownership-transfer function rather than to any caller.
    await graph.set_role(bob, team, MemberRole.VIEWER.value)

    async with transaction_scope():
        await delete_account_rows(principal_for(alice))

    owner_id = await seed.fetchval(
        "SELECT owner_person_id FROM spaces WHERE id = $1", team.id
    )
    role = await seed.fetchval(
        "SELECT role FROM memberships WHERE space_id = $1 AND person_id = $2",
        team.id,
        bob.id,
    )
    assert owner_id == bob.id
    assert role == MemberRole.MEMBER.value


async def test_delete_api_requires_both_guards_and_clears_session(api, world, seed):
    alice, bob, team = world
    _login(api, alice)
    headers = {"X-Rif-Csrf": "1"}

    for incomplete_guard in (
        {"acknowledge_shared": False, "confirmation": "DELETE"},
        {"acknowledge_shared": True, "confirmation": "delete"},
    ):
        refused = await api.post(
            "/api/account/delete", headers=headers, json=incomplete_guard
        )
        assert refused.status_code == 400
        # Read past the policies: the request's own transaction is gone, so
        # nothing here is armed to see Alice's row through them.
        assert await seed.fetchval(
            "SELECT count(*) FROM persons WHERE id = $1", alice.id
        )

    deleted = await api.post(
        "/api/account/delete",
        headers=headers,
        json={"acknowledge_shared": True, "confirmation": "DELETE"},
    )
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert "team" in deleted.json()["transferred_spaces"]
    assert "Max-Age=0" in deleted.headers["set-cookie"]
    assert not await seed.fetchval(
        "SELECT count(*) FROM persons WHERE id = $1", alice.id
    )
    assert (
        await seed.fetchval("SELECT owner_person_id FROM spaces WHERE id = $1", team.id)
        == bob.id
    )
