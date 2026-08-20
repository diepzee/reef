"""The privileged-act trail: that it records, and that it records only ids.

Some operations reach past the row policies through ``SECURITY DEFINER``
functions. That is deliberate -- no policy can express "the owner may remove
a member" without permitting much more -- and it means they carry
accountability rather than prevention. Others stay inside the policies but
destroy what would otherwise answer the question later. These assert that both
kinds leave a record, and that buying it did not quietly create a second copy
of the corpus somewhere else.
"""

import pytest
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)

from reef import audit, telemetry
from reef.access import Principal
from reef.coves import create_cove, delete_cove, invite, remove_member
from reef.db import DB
from reef.invitations import allowlist


def principal_for(person) -> Principal:
    """Build a principal for a seeded person.

    :param person: the person to act as
    :returns: the principal
    """
    return Principal(person_id=person.id, email=person.email)


class _Capture(SpanExporter):
    """Collects records in memory instead of shipping them anywhere."""

    def __init__(self) -> None:
        self.spans: list = []

    def export(self, spans) -> SpanExportResult:
        """Record the batch.

        :param spans: spans being exported
        :returns: always success
        """
        self.spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        """Nothing to release."""


@pytest.fixture
def recorded(monkeypatch):
    """Capture what the trail emits, offline.

    ``is_configured`` is forced true so :func:`reef.audit.record` runs, while
    ``send_to_logfire=False`` keeps the suite off the network.

    :param monkeypatch: pytest's monkeypatch fixture
    :returns: the exporter holding whatever was recorded
    """
    import logfire

    sink = _Capture()
    logfire.configure(
        service_name="reef-test",
        send_to_logfire=False,
        console=False,
        scrubbing=logfire.ScrubbingOptions(extra_patterns=telemetry.SCRUB_PATTERNS),
        additional_span_processors=[SimpleSpanProcessor(sink)],
    )
    monkeypatch.setattr(telemetry, "_configured", True)
    return sink


def _actions(sink: _Capture) -> list[str]:
    """Return the action name of every recorded privileged act.

    :param sink: the capturing exporter
    :returns: action names, in order
    """
    return [
        (span.attributes or {}).get("action")
        for span in sink.spans
        if (span.attributes or {}).get("action")
    ]


def _blob(sink: _Capture) -> str:
    """Flatten every recorded attribute into one searchable string.

    :param sink: the capturing exporter
    :returns: all attributes of all records, as text
    """
    return "".join(str(dict(span.attributes or {})) for span in sink.spans)


async def test_minting_an_invitation_is_recorded(tx, household, recorded):
    """Spending budget on somebody is an act with an accountable actor."""
    me = principal_for(household["wouter"])
    await allowlist(me, "recorded@example.test")

    assert audit.INVITE_MINTED in _actions(recorded)
    assert str(household["wouter"].id) in _blob(recorded)


async def test_admitting_and_removing_a_member_are_both_recorded(
    tx, household, recorded
):
    """The two acts that change who can read a cove."""
    me = principal_for(household["wouter"])
    await invite(me, "household", "joiner@example.test", display_name="Joiner")
    await remove_member(me, "household", "joiner@example.test")

    actions = _actions(recorded)
    assert audit.MEMBER_ADMITTED in actions
    assert audit.MEMBER_REMOVED in actions


async def test_the_trail_carries_no_addresses_or_content(tx, household, recorded):
    """A trail holding the corpus would be the leak this work exists to close.

    The addresses below are supplied to the operations under test, so if any
    of them reached a record it would be through a field somebody added
    without thinking. Checked against the serialized attributes rather than
    named keys, which is what catches that.
    """
    me = principal_for(household["wouter"])
    await invite(me, "household", "Secret-Person@example.test", display_name="Secret")

    blob = _blob(recorded)
    for leaked in ("secret-person@example.test", "Secret-Person", "Secret"):
        assert leaked not in blob, f"{leaked!r} reached the trail"


async def test_erasing_an_account_is_recorded(graph, recorded):
    """The most consequential act of the four, and the least reversible."""
    from reef.account import delete_account_rows

    alice = await graph.person("erased@example.test", "Alice")
    await graph.personal_cove(alice)
    async with DB.transaction():
        await delete_account_rows(principal_for(alice))

    assert audit.ACCOUNT_ERASED in _actions(recorded)


async def test_the_trail_is_inert_when_telemetry_is_off(monkeypatch):
    """A telemetry outage must never be able to fail the act being recorded."""
    monkeypatch.setattr(telemetry, "_configured", False)
    audit.record(audit.INVITE_MINTED, actor=__import__("uuid").uuid4())


async def test_destroying_a_cove_is_recorded(tx, household, recorded):
    """The one act that leaves nothing behind to read afterwards.

    Unlike the acts above this stays inside the policies -- an owner deleting
    their own cove is exactly what ``coves_owner_delete`` permits. It is
    recorded because the rows are gone: without an entry made at the time,
    nothing can say the cove existed or who ended it.
    """
    me = principal_for(household["wouter"])
    cove_id = household["shared"].id
    await remove_member(me, "household", "partner@example.test")
    await delete_cove(me, "household")

    assert audit.COVE_DELETED in _actions(recorded)
    assert str(cove_id) in _blob(recorded)


async def test_the_trail_never_carries_a_cove_name(tx, household, recorded):
    """A slug is the user's words, so it stays out of the record like a page would.

    Worth its own test because the deletion path is the one place a slug is
    the natural thing to reach for -- the id it records identifies a row that
    no longer exists, which is precisely the trade the trail makes.
    """
    me = principal_for(household["wouter"])
    await create_cove(me, "sailing-holiday")
    await delete_cove(me, "sailing-holiday")

    assert audit.COVE_DELETED in _actions(recorded)
    assert "sailing-holiday" not in _blob(recorded)
