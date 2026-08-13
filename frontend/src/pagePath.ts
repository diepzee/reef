/**
 * Page-path rules for the "new page" form.
 *
 * The path shape is a convention of this UI, not a constraint the server
 * enforces — `save_page` stores whatever string it is handed, and the export
 * sanitises paths again on its way out (`_safe_archive_path`). So the form's
 * job is to steer people into the shape the rest of reef expects, quietly
 * fixing what can be fixed and naming what cannot, while they type.
 *
 * Anything mechanical is a fix, not an error: case, stray whitespace, and the
 * `.md` everybody leaves off. What is left over genuinely needs a decision
 * from the person — a reserved prefix, an empty segment, a character with no
 * obvious repair — so it is reported instead.
 */

/** Characters a normalized path may contain. */
const ALLOWED = /[a-z0-9\-/._]/;

/** Path prefix reserved for the protocol and persona pages. */
export const PROTECTED_PREFIX = "meta/";

/**
 * Turn what someone typed into the path that will actually be created.
 *
 * Trims, lowercases, turns whitespace runs into `-`, and appends `.md` when
 * it is missing. The result is shown back to them, so none of this is a
 * silent rewrite.
 */
export function normalizePagePath(typed: string): string {
  const trimmed = typed.trim().toLowerCase().replace(/\s+/g, "-");
  if (!trimmed) return "";
  return trimmed.endsWith(".md") ? trimmed : `${trimmed}.md`;
}

/**
 * Describe what is still wrong with a normalized path, or null when it is
 * usable. Written to be read mid-typing, so it names the offending thing
 * rather than restating the whole rule.
 */
export function pagePathProblem(path: string): string | null {
  if (!path) return null;
  if (path.startsWith(PROTECTED_PREFIX)) {
    return (
      "meta/ is reserved — the protocol and persona pages change only " +
      "through their own dedicated tool, not the web editor."
    );
  }
  if (path.startsWith("/")) return "Don’t start the path with “/”.";
  const segments = path.split("/");
  if (segments.some((segment) => segment === "")) {
    return "There’s an empty folder in there — check the “/”s.";
  }
  if (segments.some((segment) => segment === ".." || segment === ".")) {
    return "“.” and “..” aren’t folder names here.";
  }
  const offenders = [...new Set([...path])].filter((ch) => !ALLOWED.test(ch));
  if (offenders.length > 0) {
    const shown = offenders.map((ch) => `“${ch}”`).join(", ");
    return `${shown} can’t be used in a path — letters, digits, “-”, “_”, “.” and “/” only.`;
  }
  // Catches both ".md" and "notes/.md" — a path whose filename is nothing
  // but the extension the normalizer just added.
  if (segments[segments.length - 1] === ".md") return "Give the page a name.";
  return null;
}
