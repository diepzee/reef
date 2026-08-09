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
