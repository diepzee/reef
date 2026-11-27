# MCP 400 diagnostics and the 21 November observation

Investigation: 26 November 2026, for [RUG-88](https://linear.app/rugvin/issue/RUG-88).
No deployment or production request was made. Historical logs were read into
memory and reduced to counts; request bodies, headers, identifiers, addresses,
tokens and validation text were neither printed nor saved.

## What the evidence establishes

The contemporaneous [RUG-46 comment](https://linear.app/rugvin/issue/RUG-46)
reported ten MCP 400 responses during a working Claude session on 21 November,
with successful requests afterwards. It explicitly said the server did not
record why it refused those requests.

A read-only Railway query of the app deployment created at 08:52:58 UTC on
21 November returned 309 log rows containing `/mcp` between 08:52 and 12:37
UTC, below the requested limit of 1,000. Of these, 278 were exact `/mcp`
access entries:

| Method | HTTP status | Count |
|---|---:|---:|
| POST | 400 | 10 |
| POST | 200 | 25 |
| POST | 202 | 7 |
| POST | 401 | 230 |
| GET | 401 | 6 |

Six of the 400s were in the 10:00 UTC hour and four in the 11:00 UTC hour.
There were no reason diagnostics. The remaining 31 rows were other HTTP paths
mentioning `/mcp` or non-access messages; they are not counted as MCP requests.
The service-wide HTTP-log query for the day returned no rows; the counts above
come from the retained deployment access log, not that empty query.

These counts independently confirm the ten refusals and continued successful
traffic. They do not establish which successful request belongs to which
refusal: traffic is interleaved, and no client or session identifiers were
used for this analysis. The historical record cannot distinguish the rejection
reasons. It therefore cannot prove that Claude reconnected without a session
ID, or rule out invalid JSON, an invalid JSON-RPC envelope, an unsupported
protocol version or an invalid/missing content type.

## What the change establishes

The HTTP startup observes the MCP SDK's error-response factory and security
validation result. Every matching rejection writes one warning to `reef.transport`:

```text
MCP request rejected: missing_session_id
MCP request rejected: unsupported_protocol_version
MCP request rejected: unparseable_json
MCP request rejected: invalid_json_rpc
MCP request rejected: invalid_content_type
```

Only the fixed category is a log argument. The logger is forwarded to Logfire
when telemetry is configured; otherwise it remains an ordinary application
log. There are no request-derived fields or exception attachments. The
category survives production scrubbing because it is not stored in a field
named `reason`, which Reef deliberately scrubs for tool-call explanations.

The SDK already decides which request to reject. Its JSON-RPC error codes
identify parse and validation failures; a fixed message or fixed prefix
distinguishes the two header failures. The response is returned unchanged.
FastMCP has no callback for these pre-tool rejections, so the implementation
uses small, idempotent SDK hooks, as Reef already does for the request
body limit. Transport tests guard the dependency boundary when the SDK changes.

Review identified a fifth path missing from the original ticket: the SDK
security middleware returns 400 for a missing or unsupported content type,
before the error factory runs. The extra hook reads only that validation
result's status, never request headers or response content. It logs
`invalid_content_type` and returns the original response. The SDK immediately
returns a security rejection, so the factory cannot report it again. Tests
combine a bad content type with malformed JSON and a missing session to
verify that only the first rejection is diagnosed. Existing SDK logging is
unchanged; only `reef.transport`, whose records are category-only, is forwarded
by this change.

Synthetic tests enter Reef's HTTP startup, run the actual FastMCP app lifespan
and session manager, and send HTTP requests through ASGI. They cover all five
POST rejections, header failures on GET and DELETE, telemetry on/off, repeated
installation, byte-for-byte error bodies and unchanged headers (apart from
new random session IDs), successful initialization/notification/ping, and
unrelated responses. Ordinary synthetic content that appears in the SDK's
validation response must be absent from both the complete new log record and
its exported diagnostic attributes and events. Request IDs, session IDs,
credentials, request content and validation text are checked too.

A separate synthetic sequence produces a missing-session 400, then initializes
successfully and receives a successful ping response. This makes a reconnect
explanation plausible; it is not evidence of what Claude actually sent.

## Resolve the remaining observation on the next occurrence

After this change is deployed through the normal release process, select a
short incident window and count the five fixed diagnostic messages. Each
Logfire diagnostic is a child of the existing request span, so its category
can be associated with an HTTP 400 without adding a session ID or request body
to the diagnostic. Use only category/status totals and timing in reports.

If the categories are `missing_session_id` and the client recovers after
initialization, that supports the reconnect hypothesis. For definitive client
behavior, reproduce a reconnect in an isolated synthetic client session while
observing these same categories and the client's success; coincident successful
traffic in a shared service alone does not prove recovery of that client.
Other categories point to the corresponding protocol or serialization fault.
A 400 without one of these messages requires checking a different rejection
path or whether this build is serving, not logging raw validation errors.

The original requirement to explain the historical cause remains an observation
gap: it was not recorded and cannot be recovered from status counts. This
change makes the next occurrence distinguishable without collecting private
request data.
