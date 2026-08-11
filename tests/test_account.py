"""Permanent account deletion and its shared-cove preservation rules."""

from conftest import _login

from rif.access import Principal, arm
from rif.account import delete_account_rows
from rif.db import transaction_scope
from rif.models import (
    Attachment,
    AttachmentStatus,
    MemberRole,
    Membership,
    Page,
    Person,
    Revision,
    Space,
)
from rif.pages import save_page


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


async def test_delete_account_erases_private_data_and_preserves_shared_coves(graph):
    alice = await graph.person("alice@x.com", "Alice")
    bob = await graph.person("bob@x.com", "Bob")
    invitee = Person(
        email="invited@x.com",
        display_name="Invited",
        invited_by_person_id=alice.id,
    )
    await invitee.save()
    personal = await graph.personal_space(alice)
    await graph.personal_space(bob)
    team = await graph.shared_space("team", alice, bob)
    solo = await graph.shared_space("solo", alice)
    principal = principal_for(alice)

    async with transaction_scope():
        private_page = await save_page(
            principal, "personal", "private.md", "private", message="private"
        )
        team_page = await save_page(
            principal, "team", "shared.md", "shared", message="shared"
        )
        solo_page = await save_page(
            principal, "solo", "solo.md", "solo", message="solo"
        )
        for space, page, key in (
            (personal, private_page, "attachments/private"),
            (solo, solo_page, "attachments/solo"),
            (team, team_page, "attachments/shared"),
        ):
            await Attachment(
                space_id=space.id,
                page_id=page.id,
                object_key=key,
                filename=f"{key.rsplit('/', 1)[-1]}.txt",
                mime="text/plain",
                byte_size=1,
                description=key,
                status=AttachmentStatus.READY.value,
            ).save()

        result = await delete_account_rows(principal)

        assert set(result.deleted_coves) == {"personal", "solo"}
        assert result.transferred_coves == ["team"]
        assert set(result.file_keys) == {"attachments/private", "attachments/solo"}
        assert await Person.objects().where(Person.id == alice.id).first() is None
        assert await Space.objects().where(Space.id == personal.id).first() is None
        assert await Space.objects().where(Space.id == solo.id).first() is None

        surviving_invitee = (
            await Person.objects().where(Person.id == invitee.id).first()
        )
        assert surviving_invitee is not None
        assert surviving_invitee.invited_by_person_id is None

        surviving_team = await Space.objects().where(Space.id == team.id).first()
        assert surviving_team.owner_person_id == bob.id
        assert (
            await Membership.objects()
            .where(Membership.space_id == team.id, Membership.person_id == alice.id)
            .first()
            is None
        )

        # Re-arm as the remaining member: shared content stays, but Alice's
        # identity is removed from its retained revision history.
        await arm(principal_for(bob))
        assert await Page.objects().where(Page.id == team_page.id).first() is not None
        revision = (
            await Revision.objects().where(Revision.page_id == team_page.id).first()
        )
        assert revision is not None and revision.author_id is None
        assert (
            await Attachment.objects()
            .where(Attachment.object_key == "attachments/shared")
            .first()
            is not None
        )


async def test_delete_account_promotes_a_viewer_who_inherits_ownership(graph):
    alice = await graph.person("alice@x.com", "Alice")
    bob = await graph.person("bob@x.com", "Bob")
    await graph.personal_space(alice)
    team = await graph.shared_space("team", alice, bob)
    await Membership.update({Membership.role: MemberRole.VIEWER.value}).where(
        Membership.space_id == team.id,
        Membership.person_id == bob.id,
    )

    async with transaction_scope():
        await delete_account_rows(principal_for(alice))
        surviving_team = await Space.objects().where(Space.id == team.id).first()
        successor = (
            await Membership.objects()
            .where(Membership.space_id == team.id, Membership.person_id == bob.id)
            .first()
        )
        assert surviving_team.owner_person_id == bob.id
        assert successor.role == MemberRole.MEMBER.value


async def test_delete_api_requires_both_guards_and_clears_session(api, world):
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
        assert await Person.objects().where(Person.id == alice.id).first() is not None

    deleted = await api.post(
        "/api/account/delete",
        headers=headers,
        json={"acknowledge_shared": True, "confirmation": "DELETE"},
    )
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert "team" in deleted.json()["transferred_coves"]
    assert "Max-Age=0" in deleted.headers["set-cookie"]
    assert await Person.objects().where(Person.id == alice.id).first() is None
    assert (
        await Space.objects().where(Space.id == team.id).first()
    ).owner_person_id == bob.id
