/**
 * The `/api/*` fetch client every later task's data access builds on.
 *
 * Requests always send same-origin cookies (session auth); mutations carry
 * the CSRF header the backend requires (`X-Rif-Csrf: 1`, see
 * `src/rif/web/requests.py:require_csrf`). A 401 anywhere means the session
 * is gone, so it redirects to the login route rather than surfacing an
 * error the caller would have to special-case.
 */

/** An error response from the API, carrying the parsed `{error, detail}` body. */
export class ApiError extends Error {
  status: number;
  code: string;
  detail?: string;

  constructor(status: number, code: string, detail?: string) {
    super(detail ? `${code}: ${detail}` : code);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

/**
 * Parse a non-2xx response into an :class:`ApiError`, or redirect on 401.
 *
 * :param response: the failed fetch response
 * :raises ApiError: always, unless the response was a 401 (which redirects
 *     instead, so the caller never sees a rejected promise for it)
 */
async function handleError(response: Response): Promise<never> {
  if (response.status === 401) {
    window.location.href = "/api/auth/login";
    // Redirect is async from the caller's perspective; throw so nothing
    // downstream runs on an unauthenticated response while it happens.
    throw new ApiError(401, "unauthenticated");
  }
  let code = "error";
  let detail: string | undefined;
  try {
    const body = await response.json();
    if (body && typeof body === "object") {
      if (typeof body.error === "string") code = body.error;
      if (typeof body.detail === "string") detail = body.detail;
    }
  } catch {
    // Non-JSON error body: fall back to the generic code above.
  }
  throw new ApiError(response.status, code, detail);
}

/**
 * Issue a GET request against the JSON API.
 *
 * :param path: the API path, e.g. ``"/api/index"``
 * :returns: the parsed JSON body
 */
export async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(path, { credentials: "same-origin" });
  if (!response.ok) {
    return handleError(response);
  }
  return (await response.json()) as T;
}

/**
 * Issue a mutating request (POST/PUT/PATCH/DELETE) against the JSON API.
 *
 * :param method: the HTTP method
 * :param path: the API path
 * :param body: an optional JSON-serializable request body
 * :returns: the parsed JSON body
 */
export async function apiSend<T>(
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: {
      "X-Rif-Csrf": "1",
      "Content-Type": "application/json",
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    return handleError(response);
  }
  return (await response.json()) as T;
}

/**
 * Read the filename out of a `Content-Disposition` header.
 *
 * :param header: the raw header value, or null when absent
 * :returns: the quoted filename, or null when there isn't a usable one
 */
function dispositionFilename(header: string | null): string | null {
  if (!header) return null;
  const match = /filename="([^"]+)"/.exec(header);
  return match?.[1] ?? null;
}

/**
 * Download a file from a POST endpoint, preserving the server's filename.
 *
 * The export routes are POSTs rather than plain links so that they carry the
 * CSRF header (see `src/rif/web/routes_api.py:_export`), which means a bare
 * `<a href>` can no longer fetch them: the response has to be read here and
 * handed to the browser as a blob.
 *
 * :param path: the API path
 * :param body: an optional JSON-serializable request body
 * :param fallbackName: filename to use if the response doesn't name one
 */
export async function apiDownload(
  path: string,
  body: unknown,
  fallbackName: string,
): Promise<void> {
  const response = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "X-Rif-Csrf": "1",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body ?? {}),
  });
  if (!response.ok) {
    await handleError(response);
  }
  const filename = dispositionFilename(
    response.headers.get("Content-Disposition"),
  );
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  try {
    const link = document.createElement("a");
    link.href = url;
    link.download = filename ?? fallbackName;
    document.body.append(link);
    link.click();
    link.remove();
  } finally {
    // Revoking immediately can cancel the download in some browsers, so the
    // handle is released on the next turn of the event loop instead.
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }
}
