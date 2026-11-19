/**
 * How this viewer has chosen to see each space.
 *
 * Appearance is per person, not per space: two members of the same space can
 * see it in different colours, and neither can restyle it for the other. So
 * this is fetched once with the shell, alongside `/api/me`, and consulted
 * wherever a space's hue or creature is drawn.
 *
 * Every consumer goes through `useSpaceLook`, which folds the override and
 * the alias-derived fallback into one answer — so a component never has to
 * know whether a choice exists.
 */

import { createContext, useCallback, useContext } from "react";

import { spaceColor, type SpaceHue } from "./components/spaceColor";

/** One space's chosen look; either field null when it is still derived. */
export interface SpaceAppearance {
  color: string | null;
  glyph: string | null;
}

/** `GET /api/appearance` — every choice this person has made, by space slug. */
export interface AppearanceMap {
  [alias: string]: SpaceAppearance;
}

/** What {@link useAppearance} exposes: the choices, and a way to record one. */
export interface AppearanceContextValue {
  appearance: AppearanceMap;
  setAppearance(alias: string, look: SpaceAppearance): void;
}

export const AppearanceContext = createContext<AppearanceContextValue | null>(
  null,
);

/** The viewer's space appearance choices — must be called under `AppShell`. */
export function useAppearance(): AppearanceContextValue {
  const value = useContext(AppearanceContext);
  if (value === null) {
    throw new Error("useAppearance must be used within an AppShell");
  }
  return value;
}

/** One space's resolved look: the hue to paint, and the body plan to grow. */
export interface SpaceLook {
  hue: SpaceHue;
  family: string | null;
}

/**
 * Resolve any space's look, override first and derivation behind it.
 *
 * Returns a function rather than a value so a component that draws a list of
 * spaces can call it per row without a hook per row.
 */
export function useSpaceLook(): (alias: string) => SpaceLook {
  const { appearance } = useAppearance();
  return useCallback(
    (alias: string) => {
      const chosen = appearance[alias];
      return {
        hue: spaceColor(alias, chosen?.color),
        family: chosen?.glyph ?? null,
      };
    },
    [appearance],
  );
}
