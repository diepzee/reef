/**
 * Wire types for the `/api/*` JSON surface.
 *
 * Mirrors the dataclasses in `src/rif/context.py` (`IndexPayload`,
 * `SpaceIndex`, and the per-page/per-attachment dicts built in
 * `build_index`) and the handler return shapes in
 * `src/rif/web/routes_api.py` (`_get_page`, `_put_page`, `_me`,
 * `_space_members`, `_invite`). Keep this file in lockstep with those —
 * it is the single source of truth every later task's fetch call trusts.
 */

/** One page's metadata within a space's index — no body. */
export interface PageMeta {
  path: string;
  title: string;
  tags: string[];
  description: string;
  updated: string;
  size: number;
  version: number;
}

/** One attachment's metadata within a space's index. */
export interface AttachmentMeta {
  key: string;
  mime: string;
  description: string;
}

/** The map of one space: page metadata and image descriptions, no bodies. */
export interface SpaceIndex {
  alias: string;
  version: number;
  pages: PageMeta[];
  attachments: AttachmentMeta[];
}

/** `GET /api/index` — the index of every space the principal may see. */
export interface IndexPayload {
  version: string;
  spaces: SpaceIndex[];
}

/** A full page, with its body — `GET`/`PUT /api/pages/{space}/{path}`. */
export interface Page {
  space: string;
  path: string;
  title: string;
  tags: string[];
  body: string;
  version: number;
  updated: string;
}

/** `GET /api/me` — the logged-in person's identity. */
export interface Me {
  person_id: string;
  email: string;
  display_name: string;
}

/** One member of a shared space's roster: display name plus the email removal is keyed by. */
export interface Member {
  display_name: string;
  email: string;
}

/** `GET /api/spaces/{space}/members` — a shared space's roster. */
export interface Members {
  members: Member[];
  owner_email: string;
  is_owner: boolean;
}

/** `POST /api/spaces/{space}/invites` — the outcome of inviting an email. */
export interface InviteResult {
  space: string;
  email: string;
  already_member: boolean;
  disclosure: string;
}
