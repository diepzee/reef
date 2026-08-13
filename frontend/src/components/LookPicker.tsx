/**
 * A colour row and a creature row, for how one viewer sees one cove.
 *
 * Renders the two rows and nothing around them: no heading and no "only
 * you" note, because both callers already sit inside a section that says
 * so once — the members sheet's "Appearance", and `SpaceView`'s for the
 * personal cove. Saying it twice under one heading read as a warning
 * rather than a fact.
 *
 * Both rows lead with a "from its name" tile rather than offering only
 * concrete choices, so a cove can always be put back to the colour and
 * creature its name gives it — the state it was in before anyone chose.
 * That tile is painted in the derived value itself, so it shows what
 * choosing it would give you rather than describing it.
 *
 * The two rows mark their choice differently, and deliberately. A ring
 * around a colour reads as one more shade, so the chosen colour carries a
 * check instead; the icon tiles are drawings on a neutral tile, where a
 * ring is unambiguous and a check laid over the creature only obscures it.
 * The "from its name" tile is badged in both rows, since it is otherwise
 * indistinguishable from whichever concrete tile the name happens to give.
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

/** The check drawn on the chosen tile. */
function Tick({ color }: { color: string }) {
  return (
    <svg
      className="look-tick"
      width="14"
      height="14"
      viewBox="0 0 14 14"
      aria-hidden="true"
    >
      <path
        d="M3 7.3 5.8 10 11 4.4"
        fill="none"
        stroke={color}
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

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
  const derived = spaceColor(alias, null);

  return (
    <div className="look">
      {error && <div className="notice">{error}</div>}

      <div className="look-field">
        <div className="look-label" id={`look-colour-${alias}`}>
          Colour
        </div>
        <div
          className="look-row"
          role="group"
          aria-labelledby={`look-colour-${alias}`}
        >
          <button
            type="button"
            className={`look-tile look-tile-auto ${chosen.color === null ? "on" : ""}`}
            style={{ background: derived.base }}
            disabled={busy}
            title="From its name"
            aria-label="Colour from its name"
            aria-pressed={chosen.color === null}
            onClick={() => choose({ ...chosen, color: null })}
          >
            {chosen.color === null && <Tick color="#fff" />}
          </button>
          {HUE_NAMES.map((name) => (
            <button
              key={name}
              type="button"
              className={`look-tile ${chosen.color === name ? "on" : ""}`}
              style={{ background: HUES[name].base }}
              disabled={busy}
              title={name}
              aria-label={name}
              aria-pressed={chosen.color === name}
              onClick={() => choose({ ...chosen, color: name })}
            >
              {chosen.color === name && <Tick color="#fff" />}
            </button>
          ))}
        </div>
      </div>

      <div className="look-field">
        <div className="look-label" id={`look-icon-${alias}`}>
          Icon
        </div>
        <div
          className="look-row"
          role="group"
          aria-labelledby={`look-icon-${alias}`}
        >
          <button
            type="button"
            className={`look-tile look-tile-glyph look-tile-auto ${
              chosen.glyph === null ? "on" : ""
            }`}
            disabled={busy}
            title="From its name"
            aria-label="Icon from its name"
            aria-pressed={chosen.glyph === null}
            onClick={() => choose({ ...chosen, glyph: null })}
          >
            <SpaceGlyph alias={alias} color={preview.base} size={20} family={null} />
          </button>
          {FAMILY_NAMES.map((family) => (
            <button
              key={family}
              type="button"
              className={`look-tile look-tile-glyph ${chosen.glyph === family ? "on" : ""}`}
              disabled={busy}
              title={family}
              aria-label={family}
              aria-pressed={chosen.glyph === family}
              onClick={() => choose({ ...chosen, glyph: family })}
            >
              <SpaceGlyph
                alias={alias}
                color={preview.base}
                size={20}
                family={family}
              />
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
