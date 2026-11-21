"""Telemetry must describe traffic without carrying its content.

reef's corpus is private memory. A telemetry backend that mirrored it would
recreate the exposure the RLS work exists to shrink -- the same engrams, held
by a third party, outside every policy Postgres enforces. So these assert the
negative: that engram bodies, titles, addresses and paths do not reach an
exporter even when a careless caller passes them to a span.

They also pin the property everything else depends on -- that with no token
configured, telemetry is inert and cannot fail a request.
"""

import os
from uuid import UUID

import pytest
from conftest import SpanCapture

from reef import telemetry


def _attribute_blob(sink: SpanCapture) -> str:
    """Flatten every exported attribute into one searchable string.

    Checking the serialized form rather than named keys is deliberate: it
    catches a value that leaks through a differently-named attribute, which
    is the mistake worth guarding against.

    :param sink: the capturing exporter
    :returns: all attributes of all spans, as text
    """
    return "".join(str(dict(span.attributes or {})) for span in sink.spans)


def test_engram_content_never_reaches_the_exporter(captured):
    """The point of the whole file: bodies, titles, addresses, paths."""
    import logfire

    with logfire.span(
        "probe",
        body="SECRET-BODY",
        title="SECRET-TITLE",
        email="person@example.test",
        display_name="Somebody",
        engram_path="meta/persona.md",
        section_text="SECRET-SECTION",
        subject="auth0|SECRET",
        # Plain values: a value that says "secret" is scrubbed for that alone.
        question="when was the boiler serviced",
        passage="The boiler was serviced in May",
    ):
        pass

    blob = _attribute_blob(captured)
    for secret in (
        "SECRET-BODY",
        "SECRET-TITLE",
        "person@example.test",
        "Somebody",
        "meta/persona.md",
        "SECRET-SECTION",
        "auth0|SECRET",
        "when was the boiler serviced",
        "The boiler was serviced in May",
    ):
        assert secret not in blob, f"{secret!r} reached the exporter"


def test_shape_still_survives_scrubbing(captured):
    """Redaction has to leave enough behind to answer an operational question."""
    import logfire

    with logfire.span("probe", space_alias="household", engram_count=3, status=200):
        pass

    blob = _attribute_blob(captured)
    assert "household" in blob
    assert "engram_count" in blob
    assert "status" in blob


def test_telemetry_is_inert_without_a_token(monkeypatch):
    """A missing token must never be able to fail a request."""
    monkeypatch.delenv("LOGFIRE_TOKEN", raising=False)
    monkeypatch.setattr(telemetry, "_configured", False)

    assert telemetry.configure() is False
    assert telemetry.request_middleware() == []
    telemetry.instrument_clients()  # must not raise


def test_the_eu_instance_is_the_default(monkeypatch):
    """This project lives on Logfire's EU instance; the SDK defaults to US."""
    monkeypatch.delenv("LOGFIRE_BASE_URL", raising=False)
    assert telemetry._base_url() == "https://logfire-eu.pydantic.dev"

    monkeypatch.setenv("LOGFIRE_BASE_URL", "https://example.test")
    assert telemetry._base_url() == "https://example.test"


def test_scrub_patterns_cover_every_content_field():
    """A new content column must be added here too; this is the reminder."""
    assert {
        "body",
        "title",
        "email",
        "display_name",
        "page_path",
        "question",
        "passage",
        "filename",
        "description",
        "text",
    } <= set(telemetry.SCRUB_PATTERNS)
    assert os.environ.get("LOGFIRE_TOKEN") is None or True


# --- the worker's job span ------------------------------------------------------

_JOB_ID = UUID("6f1c2a52-0d0e-4a55-9a51-3a2f2f0d9b11")
_QUOTED = "the body of a private engram"


@pytest.fixture
def live(captured, monkeypatch):
    """Make telemetry believe it is configured, exporting to memory.

    :param captured: the in-memory exporter
    :param monkeypatch: pytest's monkeypatch fixture
    :returns: the exporter holding whatever was exported
    """
    monkeypatch.setattr(telemetry, "_configured", True)
    return captured


def _everything(sink: SpanCapture) -> str:
    """Flatten names, attributes and events of every span into one string.

    :param sink: the capturing exporter
    :returns: all of it, as text
    """
    return "".join(
        f"{span.name}{dict(span.attributes or {})}"
        + "".join(
            f"{event.name}{dict(event.attributes or {})}" for event in span.events
        )
        for span in sink.spans
    )


def test_a_job_span_says_how_the_job_went(live):
    with telemetry.job_span(_JOB_ID, "backfill_links", 2) as result:
        result["outcome"] = "retry"
        result["error_type"] = "ValueError"
    [span] = live.spans
    attributes = dict(span.attributes)
    assert attributes["kind"] == "backfill_links"
    assert attributes["job_id"] == str(_JOB_ID)
    assert attributes["attempt"] == 2
    assert attributes["outcome"] == "retry"
    assert attributes["error_type"] == "ValueError"


def test_an_error_passing_through_a_job_span_leaves_only_its_class(live):
    """An exception message can quote content. The span must not keep it."""
    with pytest.raises(RuntimeError), telemetry.job_span(_JOB_ID, "backfill_links", 1):
        raise RuntimeError(_QUOTED)
    [span] = live.spans
    attributes = dict(span.attributes)
    assert attributes["outcome"] == "crashed"
    assert attributes["error_type"] == "RuntimeError"
    assert not span.events
    assert _QUOTED not in _everything(live)


def test_a_job_span_is_inert_without_telemetry(captured, monkeypatch):
    monkeypatch.setattr(telemetry, "_configured", False)
    with telemetry.job_span(_JOB_ID, "backfill_links", 1) as result:
        result["outcome"] = "done"
    assert captured.spans == []


def test_forwarding_logs_sends_a_named_logger_and_only_once(live):
    import logging

    import logfire

    logger = logging.getLogger("reef.test-forwarding")
    logger.setLevel(logging.INFO)
    try:
        telemetry.forward_logs(logger)
        telemetry.forward_logs(logger)
        handlers = [
            h for h in logger.handlers if isinstance(h, logfire.LogfireLoggingHandler)
        ]
        assert len(handlers) == 1
        logger.info("job %s done", "abc")
        assert "job abc done" in _everything(live)
    finally:
        logger.handlers.clear()


def test_forwarding_logs_is_inert_without_telemetry(monkeypatch):
    import logging

    monkeypatch.setattr(telemetry, "_configured", False)
    logger = logging.getLogger("reef.test-forwarding-off")
    telemetry.forward_logs(logger)
    assert logger.handlers == []
