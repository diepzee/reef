"""Observability, wired so it can see the shape of traffic but not its content.

reef's whole corpus is private memory. A telemetry backend that mirrored it
would recreate exactly the exposure the RLS work has been shrinking -- the
pages would simply leak somewhere else, to a third party, outside every
policy Postgres enforces. So the rule here is narrower than "avoid PII":
**no page body, no page title, no email address, and no page path ever
leaves this process.**

Instrumentation is therefore deliberately partial. ``instrument_asyncpg``
would attach every statement's parameters to a span, and those parameters are
page bodies, so it is not enabled; ``instrument_starlette`` captures request
metadata but is configured not to record headers, which carry the session
cookie. What is left -- request timing, status codes, error types, spans
around the expensive paths -- is enough to answer "is it up, is it slow, what
broke" without holding a copy of anyone's memory.

Absent ``LOGFIRE_TOKEN`` every function here is a no-op, so imports, the test
suite, and local development neither require credentials nor reach the
network. That is a hard requirement, not a convenience: a missing token must
never be able to fail a request.
"""

import os

# Fields Logfire scrubs from spans in addition to its own defaults (which
# already cover password, secret, token, and similar). These are the names
# this codebase actually uses for content and identity, and the list is a
# backstop -- the primary defence is never passing the values in.
SCRUB_PATTERNS = [
    "body",
    "display_name",
    "email",
    "page_path",
    "section_text",
    "subject",
    "title",
]

_configured = False


def configure() -> bool:
    """Configure Logfire once, if a write token is present.

    Called from ``rif.server.main`` before the server starts, so every later
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
        service_name="rif",
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

    httpx carries the WorkOS token exchange. Headers and bodies are not
    captured, so the authorization code and client secret stay out of spans.
    """
    if not _configured:
        return

    import logfire

    logfire.instrument_httpx(capture_headers=False)
