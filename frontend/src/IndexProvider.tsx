/**
 * A single shared fetch of `/api/index`, so views that need the index no
 * longer each issue their own `apiGet` on mount.
 *
 * Fetches once when the provider mounts (the cancelled-flag pattern from
 * `CoveView`'s effects, so an unmounted provider never calls `setState`).
 * Mutating views (`NewCove`, `Editor`, `NewPage`) call `refresh()` after a
 * successful create/save so the index — and anything rendered from it,
 * like `Home`'s cove list or `CoveView`'s page list — is current before
 * the caller navigates away.
 *
 * A 401 during either fetch means `apiGet` is already redirecting to the
 * login route (see `api.ts`'s `handleError`): the thrown `ApiError` is
 * swallowed here rather than turned into a rendered error, so the UI does
 * not flash a "could not load" notice during the redirect.
 *
 * The mount fetch and a mutation-triggered `refresh()` can both be in
 * flight at once, and network order does not have to match start order —
 * if the mount fetch is still pending when a save's `refresh()` resolves
 * first, the mount fetch's late response must not clobber the fresher one.
 * A monotonically increasing `genRef` guards this: each dispatch captures
 * the generation it belongs to, and only applies its result if that is
 * still the current generation by the time it lands.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { ApiError, apiGet } from "./api";
import type { IndexPayload } from "./types";

/** What {@link useIndex} exposes: the current index, any load error, and a refetch. */
interface IndexContextValue {
  /** Content-facing index with protected meta pages removed. */
  index: IndexPayload | null;
  /** Exact `/api/index` payload, for the dedicated index viewer. */
  rawIndex: IndexPayload | null;
  error: string | null;
  refresh(): Promise<void>;
}

const IndexContext = createContext<IndexContextValue | null>(null);

/** Fetches `/api/index` once on mount and shares it with every descendant view. */
export function IndexProvider({ children }: { children: ReactNode }) {
  const [index, setIndex] = useState<IndexPayload | null>(null);
  const [rawIndex, setRawIndex] = useState<IndexPayload | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Bumped by every dispatch (mount load and every refresh()); a response
  // only applies if it's still the most recent dispatch when it lands, so
  // an earlier, slower request can never overwrite a later one's result.
  const genRef = useRef(0);

  /**
   * Fetch `/api/index` and apply the result, unless a newer dispatch has
   * since superseded this one or `isCancelled` reports true.
   */
  const load = useCallback(async (isCancelled: () => boolean = () => false) => {
    const gen = ++genRef.current;
    try {
      const payload = await apiGet<IndexPayload>("/api/index");
      if (isCancelled() || gen !== genRef.current) return;
      setRawIndex(payload);
      // meta/* pages steer the assistant (persona); they're not content,
      // so they don't belong in cove listings or page counts.
      setIndex({
        ...payload,
        coves: payload.coves.map((cove) => ({
          ...cove,
          pages: cove.pages.filter((page) => !page.path.startsWith("meta/")),
        })),
      });
      setError(null);
    } catch (err) {
      if (isCancelled() || gen !== genRef.current) return;
      // A 401 is already being handled by apiGet's redirect to the login
      // route — don't render an error while that navigation is in flight.
      if (err instanceof ApiError && err.status === 401) return;
      setError(err instanceof ApiError ? err.message : "could not load coves");
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    load(() => cancelled);
    return () => {
      cancelled = true;
    };
  }, [load]);

  const value = useMemo(
    () => ({ index, rawIndex, error, refresh: load }),
    [index, rawIndex, error, load],
  );

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
