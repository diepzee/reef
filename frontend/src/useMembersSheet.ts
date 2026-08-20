/**
 * Context for the single app-wide `MembersSheet` that `AppShell` owns.
 *
 * Every trigger — `CoveView`'s whobar (stack + "Manage"), `Sidebar`'s
 * active-cove stack, and (from Task 6) the page header's stack — needs to
 * open "the" sheet for whatever cove it's showing, without owning a
 * sheet instance itself (there must only ever be one mounted). This lives
 * in its own module rather than inside `AppShell.tsx` so `Sidebar.tsx`
 * (which `AppShell` renders) can import it without an `AppShell` <->
 * `Sidebar` circular import.
 */

import { createContext, useContext } from "react";

/** What {@link useMembersSheet} exposes: a way to open the shared members sheet. */
export interface MembersSheetContextValue {
  openMembers(cove: string): void;
}

export const MembersSheetContext = createContext<MembersSheetContextValue | null>(null);

/** Opens the app-wide members sheet for `cove` — must be called under `AppShell`. */
export function useMembersSheet(): MembersSheetContextValue {
  const value = useContext(MembersSheetContext);
  if (value === null) {
    throw new Error("useMembersSheet must be used within an AppShell");
  }
  return value;
}
