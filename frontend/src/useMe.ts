/**
 * Context for the signed-in person that `AppShell` fetches once.
 *
 * `Sidebar` already received `me` as a prop, but `Profile` is a *routed*
 * view — it renders inside `AppShell`'s children, where props cannot reach
 * it — and it needs to write the value back after a picture changes so the
 * avatar in the account row updates without a reload. Lives in its own
 * module for the same reason `useMembersSheet` does: `AppShell` imports the
 * views' router, so a view importing `AppShell` would be circular.
 */

import { createContext, useContext } from "react";

import type { Me } from "./types";

/** What {@link useMe} exposes: the person, and a way to patch their avatar. */
export interface MeContextValue {
  me: Me | null;
  setAvatar(avatar: string | null): void;
}

export const MeContext = createContext<MeContextValue | null>(null);

/** The signed-in person — must be called under `AppShell`. */
export function useMe(): MeContextValue {
  const value = useContext(MeContext);
  if (value === null) {
    throw new Error("useMe must be used within an AppShell");
  }
  return value;
}
