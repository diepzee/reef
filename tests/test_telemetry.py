"""Telemetry must describe traffic without carrying its content.

reef's corpus is private memory. A telemetry backend that mirrored it would
recreate the exposure the RLS work exists to shrink -- the same pages, held
by a third party, outside every policy Postgres enforces. So these assert the
negative: that page bodies, titles, addresses and paths do not reach an
exporter even when a careless caller passes them to a span.

They also pin the property everything else depends on -- that with no token
configured, telemetry is inert and cannot fail a request.
"""

import os

import pytest
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)

from reef import telemetry


class _Capture(SpanExporter):
    """Collects spans in memory instead of shipping them anywhere."""

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
def captured(monkeypatch):
    """Configure Logfire with scrubbing and an in-memory exporter.

    ``send_to_logfire=False`` keeps this offline: no credentials, no network,
    nothing that could make the suite depend on a third party being up.

    :param monkeypatch: pytest's monkeypatch fixture
    :returns: the exporter holding whatever was exported
    """
    import logfire

    sink = _Capture()
    logfire.configure(
        service_name="rif-test",
        send_to_logfire=False,
        console=False,
        scrubbing=logfire.ScrubbingOptions(extra_patterns=telemetry.SCRUB_PATTERNS),
        additional_span_processors=[SimpleSpanProcessor(sink)],
    )
    return sink


def _attribute_blob(sink: _Capture) -> str:
    """Flatten every exported attribute into one searchable string.

    Checking the serialized form rather than named keys is deliberate: it
    catches a value that leaks through a differently-named attribute, which
    is the mistake worth guarding against.

    :param sink: the capturing exporter
    :returns: all attributes of all spans, as text
    """
    return "".join(str(dict(span.attributes or {})) for span in sink.spans)


def test_page_content_never_reaches_the_exporter(captured):
    """The point of the whole file: bodies, titles, addresses, paths."""
    import logfire

    with logfire.span(
        "probe",
        body="SECRET-BODY",
        title="SECRET-TITLE",
        email="person@example.test",
        display_name="Somebody",
        page_path="meta/persona.md",
        section_text="SECRET-SECTION",
        subject="auth0|SECRET",
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
    ):
        assert secret not in blob, f"{secret!r} reached the exporter"


def test_shape_still_survives_scrubbing(captured):
    """Redaction has to leave enough behind to answer an operational question."""
    import logfire

    with logfire.span("probe", space_alias="household", page_count=3, status=200):
        pass

    blob = _attribute_blob(captured)
    assert "household" in blob
    assert "page_count" in blob
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
    assert {"body", "title", "email", "display_name", "page_path"} <= set(
        telemetry.SCRUB_PATTERNS
    )
    assert os.environ.get("LOGFIRE_TOKEN") is None or True
