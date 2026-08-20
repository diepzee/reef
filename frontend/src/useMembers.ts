/**
 * A cove's member roster, shared across every mounted consumer.
 *
 * `Sidebar` (the active cove's avatar stack) and `CoveView` (the members
 * panel) both need the same `GET /api/coves/{cove}/members` response when
 * a shared cove is open — without coordination that's two identical
 * requests per navigation. A module-scope cache keyed by cove alias plus
 * an in-flight-promise map fix that: concurrent calls for the same key
 * share one network request, and a resolved roster is available
 * synchronously to any hook instance that mounts afterward.
 *
 * A listener set per key rebroadcasts a fresh roster to every mounted
 * consumer of that cove, so an invite/removal in `CoveView` is reflected
 * in `Sidebar`'s avatar stack (and vice versa) without either needing to
 * remount. The cancelled-flag + generation-counter guard mirrors
 * `IndexProvider`: a generation captured before an await is only applied if
 * it is still current, so a slow response for a cove the hook has since
 * moved on from can never clobber newer state.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, apiGet } from "./api";
import type { Members } from "./types";

const cache = new Map<string, Members>();
const inFlight = new Map<string, Promise<Members>>();
const listeners = new Map<string, Set<(members: Members) => void>>();

/** Cache a fresh roster for `key` and notify every mounted subscriber. */
function setCached(key: string, members: Members): void {
  cache.set(key, members);
  for (const listener of listeners.get(key) ?? []) listener(members);
}

/** Fetch `key`'s roster, deduplicating concurrent callers onto one request. */
function fetchMembers(key: string): Promise<Members> {
  const pending = inFlight.get(key);
  if (pending) return pending;

  const promise = apiGet<Members>(`/api/coves/${key}/members`).finally(() => {
    if (inFlight.get(key) === promise) inFlight.delete(key);
  });
  inFlight.set(key, promise);
  return promise;
}

/** What {@link useMembers} exposes: the current roster, any load error, and a refetch. */
interface UseMembersResult {
  members: Members | null;
  error: string | null;
  refresh(): Promise<void>;
}

/**
 * The member roster for `cove`, or `null` for no cove (or the personal
 * cove, which has no membership to administer — the same rule `CoveView`
 * applied inline before this hook existed).
 *
 * :param cove: a cove alias, or `null` when no cove is in scope
 * :returns: the roster, a load error if any, and a `refresh()` to re-fetch
 */
export function useMembers(cove: string | null): UseMembersResult {
  const key = cove && cove !== "personal" ? cove : null;
  const [members, setMembers] = useState<Members | null>(key ? (cache.get(key) ?? null) : null);
  const [error, setError] = useState<string | null>(null);

  // Bumped by every dispatch (mount load and every refresh()) for the
  // *current* key; a response only applies if it's still the most recent
  // dispatch for that key when it lands.
  const genRef = useRef(0);

  const load = useCallback(async (isCancelled: () => boolean = () => false) => {
    if (!key) return;
    const gen = ++genRef.current;
    try {
      const payload = await fetchMembers(key);
      if (isCancelled() || gen !== genRef.current) return;
      setCached(key, payload);
      setError(null);
    } catch (err) {
      if (isCancelled() || gen !== genRef.current) return;
      // A 401 is already being handled by apiGet's redirect to the login
      // route — don't render an error while that navigation is in flight.
      if (err instanceof ApiError && err.status === 401) return;
      setError(err instanceof ApiError ? err.message : "could not load members");
    }
  }, [key]);

  useEffect(() => {
    if (!key) {
      setMembers(null);
      setError(null);
      return;
    }

    let cancelled = false;
    setMembers(cache.get(key) ?? null);
    setError(null);

    let set = listeners.get(key);
    if (!set) {
      set = new Set();
      listeners.set(key, set);
    }
    const listener = (payload: Members) => {
      if (!cancelled) setMembers(payload);
    };
    set.add(listener);

    load(() => cancelled);

    return () => {
      cancelled = true;
      set.delete(listener);
      if (set.size === 0) listeners.delete(key);
    };
  }, [key, load]);

  const refresh = useCallback(async () => {
    await load();
  }, [load]);

  return { members, error, refresh };
}
