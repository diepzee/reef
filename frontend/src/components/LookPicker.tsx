/**
 * "How this cove looks to me" — a hue row and a creature row.
 *
 * Deliberately phrased in the first person throughout, because that is the
 * surprising part: this changes nothing for anybody else in the cove. Two
 * members can hold entirely different pictures of the same place, and
 * neither can restyle it for the other.
 *
 * Both rows offer a "derive it" option rather than only concrete choices,
 * so a cove can always be put back to the colour and creature its name
 * gives it — the state it was in before anyone chose.
 */

import { useState } from "react";

import { apiSend } from "../api";
import { useAppearance } from "../useAppearance";
import { HUE_NAMES, HUES, spaceColor } from "./spaceColor";
import { SpaceGlyph } from "./spaceGlyph";

/**
 * Body plans on offer, mirroring `GLYPHS` in `src/rif/appearance.py` — the
 * living families, which is `FAMILIES` minus the three retired plans.
 */
const FAMILY_NAMES = [
  "sunAnemone",
  "tubes",
  "staghorn",
  "flower",
  "scallop",
  "spiral",
  "bubbles",
  "seagrass",
] as const;

export function LookPicker({ alias }: { alias: string }) {
  const { appearance, setAppearance } = useAppearance();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const chosen = appearance[alias] ?? { color: null, glyph: null };

  async function choose(next: { color: string | null; glyph: string | null }) {
    // Optimistic: the whole point is watching the sidebar and cards change,
    // and there is nothing to lose by being wrong for one round trip.
    const previous = chosen;
    setAppearance(alias, next);
    setBusy(true);
    setError(null);
    try {
      await apiSend(
        "PUT",
        `/api/spaces/${encodeURIComponent(alias)}/appearance`,
        next,
      );
    } catch {
      setAppearance(alias, previous);
      setError("that could not be saved");
    } finally {
      setBusy(false);
    }
  }

  const preview = spaceColor(alias, chosen.color);

  return (
    <div className="look">
      <div className="section-label">How this cove looks to me</div>
      <p className="muted look-note">
        Only to you. Everyone else keeps seeing it their own way.
      </p>
      {error && <div className="notice">{error}</div>}

      <div className="look-row" role="group" aria-label="Colour">
        <button
          type="button"
          className={`look-swatch look-auto ${chosen.color === null ? "on" : ""}`}
          disabled={busy}
          title="From its name"
          aria-label="Colour from its name"
          aria-pressed={chosen.color === null}
          onClick={() => choose({ ...chosen, color: null })}
        >
          auto
        </button>
        {HUE_NAMES.map((name) => (
          <button
            key={name}
            type="button"
            className={`look-swatch ${chosen.color === name ? "on" : ""}`}
            style={{ background: HUES[name].base }}
            disabled={busy}
            title={name}
            aria-label={name}
            aria-pressed={chosen.color === name}
            onClick={() => choose({ ...chosen, color: name })}
          />
        ))}
      </div>

      <div className="look-row" role="group" aria-label="Creature">
        <button
          type="button"
          className={`look-glyph look-auto ${chosen.glyph === null ? "on" : ""}`}
          disabled={busy}
          title="From its name"
          aria-label="Creature from its name"
          aria-pressed={chosen.glyph === null}
          onClick={() => choose({ ...chosen, glyph: null })}
        >
          auto
        </button>
        {FAMILY_NAMES.map((family) => (
          <button
            key={family}
            type="button"
            className={`look-glyph ${chosen.glyph === family ? "on" : ""}`}
            disabled={busy}
            title={family}
            aria-label={family}
            aria-pressed={chosen.glyph === family}
            onClick={() => choose({ ...chosen, glyph: family })}
          >
            <SpaceGlyph
              alias={alias}
              color={preview.base}
              size={22}
              family={family}
            />
          </button>
        ))}
      </div>
    </div>
  );
}
