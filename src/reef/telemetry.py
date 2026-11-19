"""Observability, wired so it can see the shape of traffic but not its content.

reef's whole corpus is private memory. A telemetry backend that mirrored it
would recreate exactly the exposure the RLS work has been shrinking -- the
engrams would simply leak somewhere else, to a third party, outside every
policy Postgres enforces. So the rule here is narrower than "avoid PII":
**no engram body, no engram title, no email address, and no engram path ever
leaves this process.**

Instrumentation is therefore deliberately partial. ``instrument_asyncpg``
would attach every statement's parameters to a span, and those parameters are
engram bodies, so it is not enabled; ``instrument_starlette`` captures request
metadata but is configured not to record headers, which carry the session
cookie. What is left -- request timing, status codes, error types, spans
around the expensive paths -- is enough to answer "is it up, is it slow, what
broke" without holding a copy of anyone's memory.

Absent ``LOGFIRE_TOKEN`` every function here is a no-op, so imports, the test
suite, and local development neither require credentials nor reach the
network. That is a hard requirement, not a convenience: a missing token must
never be able to fail a request.
"""

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

# Fields Logfire scrubs from spans in addition to its own defaults (which
# already cover password, secret, token, and similar). These are the names
# this codebase actually uses for content and identity, and the list is a
# backstop -- the primary defence is never passing the values in.
SCRUB_PATTERNS = [
    "body",
    "display_name",
    "email",
    "engram_path",
    # The same path under the tools' and the web API's name for it.
    "page_path",
    "query",
    "section_text",
    "subject",
    "title",
]

_configured = False


def configure() -> bool:
    """Configure Logfire once, if a write token is present.

    Called from ``reef.server.main`` before the server starts, so every later
    span has somewhere to go. Safe to call more than once; later calls do
    nothing.

    The base URL is explicit because this project lives on Logfire's EU
    instance, and the SDK would otherwise send to the US default.

    :returns: True if telemetry was configured, False if it is disabled
    """
    global _configured
    if _configured:
        return True
    if not os.environ.get("LOGFIRE_TOKEN"):
        return False

    import logfire

    logfire.configure(
        service_name="reef",
        # Railway injects the deploying commit, which makes it possible to
        # say which build a trace came from. Absent locally, hence the guard.
        service_version=os.environ.get("RAILWAY_GIT_COMMIT_SHA") or None,
        environment=os.environ.get("RAILWAY_ENVIRONMENT_NAME") or "local",
        scrubbing=logfire.ScrubbingOptions(extra_patterns=SCRUB_PATTERNS),
        advanced=logfire.AdvancedOptions(base_url=_base_url()),
        # The console exporter would duplicate every span into Railway's log
        # stream, where it is neither searchable nor scrubbed.
        console=False,
    )
    _configured = True
    return True


def _base_url() -> str:
    """Return the Logfire instance to export to.

    :returns: the configured base URL, defaulting to the EU instance
    """
    return os.environ.get("LOGFIRE_BASE_URL") or "https://logfire-eu.pydantic.dev"


def is_configured() -> bool:
    """Report whether telemetry is live.

    Callers that emit records use this to stay inert when it is not, rather
    than importing logfire and discovering there is nowhere to send.

    :returns: True once :func:`configure` has run successfully
    """
    return _configured


def request_middleware() -> list:
    """Return ASGI middleware giving one span per request, for ``mcp.run``.

    Middleware rather than ``logfire.instrument_starlette(app)`` because
    ``FastMCP.run`` builds its own application internally -- instrumenting an
    app from ``mcp.http_app()`` would decorate a throwaway object and export
    nothing, while appearing to work. ``run(middleware=...)`` is passed
    straight through to the application actually served.

    ``capture_headers`` stays off: the session cookie is a header, and a
    captured one is a live credential sitting in a third party's database for
    as long as the trace is retained.

    :returns: middleware to pass to ``mcp.run``, empty when disabled
    """
    if not _configured:
        return []

    from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware
    from starlette.middleware import Middleware

    return [Middleware(OpenTelemetryMiddleware)]


def instrument_clients() -> None:
    """Instrument outbound HTTP, which is process-wide rather than per-app.

    A no-op when :func:`configure` did not run.

    httpx carries the WorkOS token exchange, and each search's query on its
    way to the embedding service. Headers and bodies are not captured, so
    the authorization code, the client secret and the query stay out of
    spans. That is said outright here, because ``LOGFIRE_HTTPX_CAPTURE_ALL``
    in the environment would otherwise switch body capture on.
    """
    if not _configured:
        return

    import logfire

    logfire.instrument_httpx(capture_all=False)


@contextmanager
def job_span(job_id: UUID, kind: str, attempt: int) -> Iterator[dict[str, str]]:
    """Wrap one worker job in a span that says how it went, and nothing else.

    The span carries the job's id, its kind, the attempt number, the outcome
    and the *class* of any error. Never the person, and never an error's
    message or traceback: both can quote the content a job was working on.
    For the same reason the span is always closed as if nothing was raised,
    so Logfire does not attach an exception that passes through it.

    The caller reports the result by filling the yielded mapping, with
    ``outcome`` and, on failure, ``error_type``. An exception that escapes
    is recorded as outcome ``crashed`` with its class, then raised again.

    Anything else that reports to Logfire while the job runs becomes a child
    of this span. No handler does today. One that starts to, for instance by
    writing an audit record, which names its actor, would tie that person to
    this job's id in Logfire. Weigh that before adding one.

    Without telemetry the mapping is still yielded and simply discarded.

    :param job_id: the job's id, a random value that names no person
    :param kind: the job's kind
    :param attempt: which attempt this is, counting from one
    :returns: a context manager yielding the mapping to report the result in
    """
    result: dict[str, str] = {}
    if not _configured:
        yield result
        return

    import logfire

    span = logfire.span("job {kind}", kind=kind, job_id=str(job_id), attempt=attempt)
    span.__enter__()
    try:
        yield result
    except BaseException as error:
        result["outcome"] = "crashed"
        result["error_type"] = type(error).__name__
        raise
    finally:
        for name, value in result.items():
            span.set_attribute(name, value)
        if result.get("outcome") in {"retry", "failed", "crashed"}:
            span.set_level("warn" if result["outcome"] == "retry" else "error")
        span.__exit__(None, None, None)


def forward_logs(logger: logging.Logger) -> None:
    """Send one logger's records to Logfire as well as to where they go now.

    Takes a single named logger on purpose. The root logger would also carry
    whatever libraries log, and a database driver's error can quote a
    statement's parameters, which are engram bodies. Only pass a logger whose
    every message is known to be free of content and identity.

    Besides the message and its arguments, the handler sends where in the
    code the record was logged: file path, line and function name.

    A no-op when :func:`configure` did not run, and safe to call twice.

    :param logger: the logger whose records to forward
    """
    if not _configured:
        return

    import logfire

    if any(isinstance(h, logfire.LogfireLoggingHandler) for h in logger.handlers):
        return
    logger.addHandler(logfire.LogfireLoggingHandler())
