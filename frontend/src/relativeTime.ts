/**
 * Format an ISO timestamp as a short relative age, e.g. "3 h ago".
 *
 * Cove and page lists show "updated" times far more often than exact
 * dates matter, so a compact relative form (matching the brief's "3 h
 * ago" style) reads faster than a full date at a glance; it falls back
 * to a plain date once the age is old enough that "N d ago" stops being
 * useful.
 *
 * :param iso: an ISO 8601 timestamp, as returned by the API
 * :returns: a short relative-age string
 */
export function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) {
    return iso;
  }
  const seconds = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (seconds < 60) {
    return "just now";
  }
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) {
    return `${minutes} min ago`;
  }
  const hours = Math.round(minutes / 60);
  if (hours < 24) {
    return `${hours} h ago`;
  }
  const days = Math.round(hours / 24);
  if (days < 30) {
    return `${days} d ago`;
  }
  return new Date(iso).toLocaleDateString();
}
