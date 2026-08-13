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

/** A resolved wiki reference from one indexed page to another visible page. */
export interface PageReference {
  space: string;
  path: string;
}

/** One page's metadata within a space's index — no body. */
export interface PageMeta {
  path: string;
  title: string;
  tags: string[];
  description: string;
  updated: string;
  size: number;
  version: number;
  last_editor: string | null;
  references: PageReference[];
}

/** One stored file's metadata within a space's index. */
export interface AttachmentMeta {
  key: string;
  filename: string;
  mime: string;
  size: number;
  description: string;
  page_path: string | null;
}

/** The map of one space: page metadata and described files, no bodies. */
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
  last_editor: string | null;
}

/**
 * `GET /api/me` — the logged-in person's identity.
 *
 * `avatar` is a URL to fetch the picture from, or null when they have not
 * chosen one and the UI should fall back to their initials.
 */
export interface Me {
  person_id: string;
  email: string;
  display_name: string;
  avatar: string | null;
}

/**
 * One member of a shared space's roster.
 *
 * `email` is blanked to `""` for every viewer but the cove's owner, so it is
 * not a key the UI can rely on — `person_id` is. `avatar` is a URL to fetch
 * the picture from, or null when they have chosen none and the UI should
 * fall back to their initial, exactly as `Me.avatar` works.
 */
export interface Member {
  person_id: string;
  display_name: string;
  email: string;
  avatar: string | null;
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

/**
 * `POST /api/invites` — the outcome of inviting someone to reef itself.
 *
 * No `space` and no `disclosure`, unlike `InviteResult`: this grants sight
 * of nothing, so there is nothing to disclose. `next_step` carries the words
 * the inviter relays, since reef sends no invitation email.
 */
export interface ReefInviteResult {
  email: string;
  already_known: boolean;
  invites_left: number;
  next_step: string;
}

/** `GET /api/invites` — how many reef invites the caller has left. */
export interface InviteBudget {
  invites_left: number;
  budget: number;
  window_days: number;
}
