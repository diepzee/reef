/**
 * What shipped, for the person it shipped to.
 *
 * Read-only and shallow on purpose: a reader opens this to find out what
 * changed, not to do anything. The copy is written in `changes/*.md` by
 * whoever made the change, so this component only groups and renders — if
 * a line here reads like release notes, the fix belongs in the fragment.
 *
 * Overlay, Escape handling and focus follow `MembersSheet`, so the app has
 * one way of dismissing a surface rather than two.
 */

import { useEffect } from "react";

import type { Change, ReleaseEntry } from "../types";

/** How each kind is announced. The reader is told what happened to them. */
const HEADINGS: Record<Change["kind"], string> = {
  added: "New",
  changed: "Changed",
  fixed: "Fixed",
};

const ORDER: Change["kind"][] = ["added", "changed", "fixed"];

export function ReleaseNotes({
  entries,
  onClose,
}: {
  entries: ReleaseEntry[];
  onClose(): void;
}) {
  // Escape closes, as it does for every other dismissible surface here.
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div className="wn-overlay" onClick={onClose}>
      <div
        className="wn-panel"
        role="dialog"
        aria-label="What's new"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="wn-head">
          <h2>What's new</h2>
          <button type="button" className="wn-close" onClick={onClose}>
            Close
          </button>
        </div>

        {entries.length === 0 ? (
          <p className="wn-empty">
            Nothing to report yet — check back after the next release.
          </p>
        ) : (
          entries.map((entry) => (
            <section key={entry.version} className="wn-entry">
              <h3>
                {entry.version}
                <span className="wn-date">{entry.date}</span>
              </h3>
              {ORDER.map((kind) => {
                const lines = entry.changes.filter((c) => c.kind === kind);
                if (lines.length === 0) return null;
                return (
                  <div key={kind}>
                    <h4 className="wn-kind">{HEADINGS[kind]}</h4>
                    <ul>
                      {lines.map((line) => (
                        <li key={line.text}>{line.text}</li>
                      ))}
                    </ul>
                  </div>
                );
              })}
            </section>
          ))
        )}
      </div>
    </div>
  );
}
