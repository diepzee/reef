/**
 * A single shared fetch of `/api/index`, so views that need the index no
 * longer each issue their own `apiGet` on mount.
 *
 * Fetches once when the provider mounts (the cancelled-flag pattern from
 * `SpaceView`'s effects, so an unmounted provider never calls `setState`).
 * Mutating views (`NewSpace`, `Editor`, `NewPage`) call `refresh()` after a
 * successful create/save so the index — and anything rendered from it,
 * like `Home`'s space list or `SpaceView`'s page list — is current before
 * the caller navigates away.
 *
 * A 401 during either fetch means `apiGet` is already redirecting to the
 * login route (see `api.ts`'s `handleError`): the thrown `ApiError` is
 * swallowed here rather than turned into a rendered error, so the UI does
 * not flash a "could not load" notice during the redirect.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { ApiError, apiGet } from "./api";
import type { IndexPayload } from "./types";

/** What {@link useIndex} exposes: the current index, any load error, and a refetch. */
interface IndexContextValue {
  index: IndexPayload | null;
  error: string | null;
  refresh(): Promise<void>;
}

const IndexContext = createContext<IndexContextValue | null>(null);

/** Fetches `/api/index` once on mount and shares it with every descendant view. */
export function IndexProvider({ children }: { children: ReactNode }) {
  const [index, setIndex] = useState<IndexPayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  /**
   * Fetch `/api/index` and apply the result, unless `isCancelled` reports
   * true by the time the response lands.
   */
  const load = useCallback(async (isCancelled: () => boolean = () => false) => {
    try {
      const payload = await apiGet<IndexPayload>("/api/index");
      if (isCancelled()) return;
      setIndex(payload);
      setError(null);
    } catch (err) {
      if (isCancelled()) return;
      // A 401 is already being handled by apiGet's redirect to the login
      // route — don't render an error while that navigation is in flight.
      if (err instanceof ApiError && err.status === 401) return;
      setError(err instanceof ApiError ? err.message : "could not load spaces");
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    load(() => cancelled);
    return () => {
      cancelled = true;
    };
  }, [load]);

  const value = useMemo(() => ({ index, error, refresh: load }), [index, error, load]);

  return <IndexContext.Provider value={value}>{children}</IndexContext.Provider>;
}

/** The shared `/api/index` result — must be called under {@link IndexProvider}. */
export function useIndex(): IndexContextValue {
  const value = useContext(IndexContext);
  if (value === null) {
    throw new Error("useIndex must be used within an IndexProvider");
  }
  return value;
}
