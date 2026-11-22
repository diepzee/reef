"""Test accounts: the operator-only flag, the reset and hurry functions, and totals."""

import asyncpg
import pytest

from reef.access import Principal, arm
from reef.db import transaction_scope
from reef.models import Person

YESTERDAY = (
    "date_trunc('day', now() AT TIME ZONE 'UTC') - interval '1 day' + interval '1 hour'"
)


def principal_for(person) -> Principal:
    """Return the principal a signed-in person acts as.

    :param person: a seeded person
    :returns: the principal
    """
    return Principal(person_id=person.id, email=person.email)


async def _flag(seed, person) -> None:
    """Flag a person as a test account, as the operator would.

    :param seed: the connection that is not bound by row security
    :param person: the person to flag
    """
    await seed.execute("INSERT INTO test_accounts (person_id) VALUES ($1)", person.id)


async def _solo(graph, email: str = "solo@example.test"):
    """Create a person with a personal space and nobody to share it with.

    :param graph: the topology builders
    :param email: the person's address
    :returns: the person
    """
    person = await graph.person(email, "Solo")
    await graph.personal_space(person)
    return person


async def _job(seed, person, status: str, hours_ahead: int = 0) -> None:
    """Add one job for a person.

    :param seed: the connection that is not bound by row security
    :param person: the job's person
    :param status: the job's status
    :param hours_ahead: how far ahead the job is due
    """
    await seed.execute(
        "INSERT INTO jobs (id, person_id, kind, status, run_after) VALUES "
        "(gen_random_uuid(), $1, 'x', $2, LOCALTIMESTAMP + make_interval(hours => $3))",
        person.id,
        status,
        hours_ahead,
    )


async def test_person_cannot_flag_themselves(household):
    me = principal_for(household["wouter"])
    with pytest.raises(asyncpg.InsufficientPrivilegeError, match="row-level security"):
        async with transaction_scope():
            await arm(me)
            await Person.raw(
                "INSERT INTO test_accounts (person_id) VALUES ({})", me.person_id
            )


async def test_person_sees_only_own_flag(household, seed):
    await _flag(seed, household["wouter"])
    await _flag(seed, household["partner"])
    for person in (household["wouter"], household["partner"]):
        async with transaction_scope():
            await arm(principal_for(person))
            rows = await Person.raw("SELECT person_id FROM test_accounts")
        assert [row["person_id"] for row in rows] == [person.id]


async def test_reset_function_refuses_unflagged(household, seed):
    person = household["wouter"]
    await _job(seed, person, "queued")
    with pytest.raises(asyncpg.InsufficientPrivilegeError, match="not a test account"):
        async with transaction_scope():
            await arm(principal_for(person))
            await Person.raw("SELECT reef_test_account_reset()")
    assert await seed.fetchval("SELECT count(*) FROM jobs") == 1


async def test_reset_function_clears_jobs_and_spend(graph, seed):
    person = await _solo(graph)
    await _flag(seed, person)
    await _job(seed, person, "queued")
    await _job(seed, person, "running")
    await seed.execute(
        "INSERT INTO embedding_spend (id, person_id, day, pieces) "
        "VALUES (gen_random_uuid(), $1, current_date, 3)",
        person.id,
    )
    await seed.execute(
        "INSERT INTO reading_spend (id, person_id, day, reader, pages) "
        "VALUES (gen_random_uuid(), $1, current_date, 'tesseract', 2)",
        person.id,
    )
    async with transaction_scope():
        await arm(principal_for(person))
        [row] = await Person.raw("SELECT reef_test_account_reset() AS stamp")
    assert row["stamp"] is not None
    assert await seed.fetchval("SELECT status FROM jobs") == "running"
    assert await seed.fetchval("SELECT count(*) FROM jobs") == 1
    assert await seed.fetchval("SELECT count(*) FROM embedding_spend") == 0
    assert await seed.fetchval("SELECT count(*) FROM reading_spend") == 0
    assert await seed.fetchval("SELECT reset_at FROM test_accounts") == row["stamp"]


async def test_reset_function_leaves_other_people_alone(graph, seed):
    person = await _solo(graph)
    other = await _solo(graph, "other@example.test")
    await _flag(seed, person)
    await _job(seed, other, "queued")
    async with transaction_scope():
        await arm(principal_for(person))
        await Person.raw("SELECT reef_test_account_reset()")
    assert await seed.fetchval("SELECT count(*) FROM jobs") == 1


async def test_reset_function_refuses_shared_spaces(household, graph, seed):
    """Owner, member, or owner of a space they have left: each one is refused."""
    loner = await _solo(graph)
    left = await graph.shared_space("left", loner, household["partner"])
    await graph.drop_membership(loner, left)
    for person in (household["wouter"], household["partner"], loner):
        await _flag(seed, person)
        await _job(seed, person, "queued")
        with pytest.raises(
            asyncpg.InsufficientPrivilegeError, match="shares a space with others"
        ):
            async with transaction_scope():
                await arm(principal_for(person))
                await Person.raw("SELECT reef_test_account_reset()")
    assert await seed.fetchval("SELECT count(*) FROM jobs") == 3
    assert (
        await seed.fetchval(
            "SELECT count(*) FROM test_accounts WHERE reset_at IS NOT NULL"
        )
        == 0
    )


async def test_hurry_moves_queued_jobs(household, seed):
    person = household["wouter"]
    await _flag(seed, person)
    await _job(seed, person, "queued", hours_ahead=1)
    async with transaction_scope():
        await arm(principal_for(person))
        [row] = await Person.raw("SELECT reef_test_account_hurry() AS moved")
    assert row["moved"] == 1
    assert await seed.fetchval("SELECT run_after <= LOCALTIMESTAMP FROM jobs")


async def test_hurry_does_nothing_for_unflagged(household, seed):
    person = household["wouter"]
    await _job(seed, person, "queued", hours_ahead=1)
    async with transaction_scope():
        await arm(principal_for(person))
        [row] = await Person.raw("SELECT reef_test_account_hurry() AS moved")
    assert row["moved"] == 0
    assert await seed.fetchval("SELECT run_after > LOCALTIMESTAMP FROM jobs")


async def test_totals_skip_test_accounts(household, seed):
    await _flag(seed, household["wouter"])
    for person in (household["wouter"], household["partner"]):
        await seed.execute(
            "INSERT INTO call_log (id, person_id, method, reason, writer, outcome, "
            f"called_at) VALUES (gen_random_uuid(), $1, 'recall', 'why', 'assistant', "
            f"'ok', {YESTERDAY})",
            person.id,
        )
    async with transaction_scope():
        rows = await Person.raw("SELECT * FROM reef_call_log_totals(7)")
    assert [row["calls"] for row in rows] == [1]


async def test_hurry_leaves_running_jobs_and_other_people_alone(household, seed):
    person = household["wouter"]
    await _flag(seed, person)
    await _job(seed, person, "running", hours_ahead=1)
    await _job(seed, household["partner"], "queued", hours_ahead=1)
    async with transaction_scope():
        await arm(principal_for(person))
        [row] = await Person.raw("SELECT reef_test_account_hurry() AS moved")
    assert row["moved"] == 0
    assert await seed.fetchval("SELECT bool_and(run_after > LOCALTIMESTAMP) FROM jobs")
