/**
 * Context for the single app-wide what's-new panel that `AppShell` owns.
 *
 * `AccountMenu` is rendered twice — once in the sidebar, once in the mobile
 * header — and both copies need the same unread flag and the same panel.
 * Fetching per copy would mean two requests and two panels; the state lives
 * in `AppShell` and reaches both through here, exactly as `useMembersSheet`
 * does for the members sheet.
 */

import { createContext, useContext } from "react";

/** What {@link useReleaseNotes} exposes: the marker, and a way to open the panel. */
export interface ReleaseNotesContextValue {
  unread: boolean;
  openReleaseNotes(): void;
}

export const ReleaseNotesContext = createContext<ReleaseNotesContextValue | null>(null);

/** The what's-new marker and opener — must be called under `AppShell`. */
export function useReleaseNotes(): ReleaseNotesContextValue {
  const value = useContext(ReleaseNotesContext);
  if (value === null) {
    throw new Error("useReleaseNotes must be used within an AppShell");
  }
  return value;
}
