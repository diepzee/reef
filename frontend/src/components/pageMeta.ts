import { relativeTime } from "../relativeTime";

/**
 * Build a metadata sentence for a page, e.g. "seen by everyone in reef · edited by Wouter, 2 h ago · v2".
 */
export function pageMetaSentence(parts: {
  space: string;
  personal: boolean;
  lastEditor: string | null;
  updated: string;
  version?: number;
}): string {
  const clauses: string[] = [];

  // Audience clause
  if (parts.personal) {
    clauses.push("only you");
  } else {
    clauses.push(`seen by everyone in ${parts.space}`);
  }

  // Editor clause (omitted if null)
  if (parts.lastEditor) {
    clauses.push(`edited by ${parts.lastEditor}, ${relativeTime(parts.updated)}`);
  } else {
    clauses.push(relativeTime(parts.updated));
  }

  // Version clause (omitted if undefined)
  if (parts.version !== undefined) {
    clauses.push(`v${parts.version}`);
  }

  return clauses.join(" · ");
}
