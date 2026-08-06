import pytest

from rif.access import AccessDenied, Principal, accessible_spaces, resolve_space


def principal_for(person) -> Principal:
    return Principal(person_id=person.id, email=person.email)


async def test_personal_alias_resolves_to_own_space(tx, household):
    space = await resolve_space(principal_for(household["wouter"]), "personal")
    assert space.id == household["w_personal"].id


async def test_personal_alias_never_resolves_to_the_other_persons_space(tx, household):
    space = await resolve_space(principal_for(household["partner"]), "personal")
    assert space.id == household["p_personal"].id


async def test_household_alias_resolves_shared_for_both(tx, household):
    for key in ("wouter", "partner"):
        space = await resolve_space(principal_for(household[key]), "household")
        assert space.id == household["shared"].id


async def test_unknown_alias_is_denied(tx, household):
    with pytest.raises(AccessDenied):
        await resolve_space(principal_for(household["wouter"]), "theirs")


async def test_stranger_is_denied(tx, household):
    from uuid import uuid4

    stranger = Principal(person_id=uuid4(), email="stranger@example.test")
    with pytest.raises(AccessDenied):
        await resolve_space(stranger, "household")


async def test_accessible_spaces_excludes_the_other_personal_space(tx, household):
    spaces = await accessible_spaces(principal_for(household["wouter"]))
    assert {s.id for s in spaces} == {
        household["w_personal"].id,
        household["shared"].id,
    }
