# Release versioning and per-release feature tracking

**Date:** 2026-08-17
**Status:** approved design, not yet implemented

## The problem

reef ships three artifacts and versions none of them. `pyproject.toml`,
`clients/python/pyproject.toml` and `clients/ts/package.json` have all read
`0.1.0` since the repo began. There is no `CHANGELOG.md`, no tag, and no release
job. Railway deploys every push to `main`, so a merge is a production deploy and
nothing records which merge that was.

Two readers are underserved by that:

- **A person using reef** has no way to learn that something new exists. Someone
  who signed in a month ago does not know `search_pages` shipped.
- **We** cannot answer "which version introduced this", and cannot publish
  `reef-cli` with a number that means anything to somebody pinning it.

A third reader — the connected assistant — is a bonus, not a goal, and this
design does not build for it.

## What is deliberately not changing

Continuous deploy stays. `main` keeps deploying to production on every push. The
server has no meaningful version number; it has a deploy date. Only the two
CLIs, which people install and pin, get semantic versions that carry a promise.

## Decisions taken

| Decision | Choice |
|---|---|
| Release model | Continuous deploy for the server, tagged semver for the CLIs |
| Version computation | Automatic, from conventional commits (semantic-release) |
| Commit style | Conventional-commit prefixes adopted on all subjects |
| Engineering changelog | Generated — `CHANGELOG.md` |
| User-facing entries | Hand-written fragments, enforced in CI |
| Client versioning | Lockstep — one number, both packages, one tag |
| Client publishing | Path-gated — publish only what actually changed |
| User-facing surfaces | Public page **and** in-app panel with an unread marker |

## Architecture

One merge produces two records in two registers.

```
conventional commit ──▶ semantic-release ──▶ version + tag + CHANGELOG.md
                                          └─▶ GitHub release
changes/*.md fragment ─▶ fold step ───────▶ site/release-notes.json
                                             ├─▶ site/changelog.html   (public)
                                             └─▶ GET /api/release-notes    (in-app)
```

`CHANGELOG.md` is the engineering record and reads like one. `release-notes.json` is
what a user reads, in their vocabulary. Deriving the second from the first was
rejected: commit subjects are written for the repo, and a user has no worktrees,
no RLS and no schema.

### Component 1 — Fragments

A PR that changes something a user would notice adds one file under `changes/`,
named for the PR number (`changes/57-search-pages.md`):

```markdown
---
kind: added
---
Search your pages from your assistant — ask for anything you've written down
and reef finds it.
```

- `kind` is exactly one of `added`, `changed`, `fixed`. Anything else is an
  error, not a default.
- The body is one or two plain sentences addressed to a person, present tense,
  no identifiers. It follows ISO 24495-1 like everything else a user reads.
- Separate files per PR, so two open branches never conflict.

Most PRs add nothing. Test isolation, CI tuning and refactors are invisible to
users by definition.

### Component 2 — The fold

A step in the release run reads every fragment, groups by `kind`, and prepends
one entry to `site/release-notes.json`:

```json
{
  "entries": [
    {
      "version": "0.4.0",
      "date": "2026-08-17",
      "changes": [
        { "kind": "added", "text": "Search your pages from your assistant — …" }
      ]
    }
  ]
}
```

The file is append-only and committed. It is the single source for both
user-facing surfaces, so the public page and the in-app panel can never disagree.
The fold then deletes the consumed fragments in the same commit.

It lives under `site/` rather than `docs/` because the Dockerfile copies `src`,
`scripts`, `site` and `clients/python` — not `docs`. A feed the running server
cannot open would leave the in-app panel with nothing to show, and `site/` is
already both shipped in the image and publicly served.

A release with no fragments writes no entry. A patch nobody notices should not
announce itself.

### Component 3 — Release automation

`.releaserc.json` on `main`, run by `cycjimmy/semantic-release-action@v4`,
modelled directly on `hybrix-app`:

- `@semantic-release/commit-analyzer` with the `conventionalcommits` preset and
  the same `releaseRules` — `breaking` major, `feat` minor, everything else
  patch.
- `@semantic-release/release-notes-generator`
- `@semantic-release/changelog`
- `@semantic-release/exec` — stamps the computed version into all three
  manifests and runs the fold
- `@semantic-release/git` — commits `CHANGELOG.md`, `site/release-notes.json`,
  `site/changelog.html`, the three manifests and the fragment deletions as
  `chore(release): ${nextRelease.version} [skip ci]`
- `@semantic-release/github`

The PR-only dry run comes across from hybrix too: a no-write semantic-release
run on pull requests, so a broken `.releaserc.json` or an unparseable history
fails in review rather than on `main`.

**Known consequence.** `[skip ci]` stops GitHub Actions; it does not stop
Railway, which deploys every push to `main`. Each release therefore causes one
extra no-op production deploy. Accepted, and recorded here so it is not
rediscovered as a bug.

### Component 4 — Path-gated publishing

After a release is cut, one job publishes to the registries. It publishes
`reef-cli` only if `clients/python/**` changed, and `@haai/reef-cli` only if
`clients/ts/**` changed.

The comparison is `<previous tag>..HEAD~1` — the commit before semantic-release's
own, which would otherwise register as a change to every manifest. This is
correct only because `@semantic-release/git` always writes exactly one commit;
if that ever stops being true, the gate silently publishes nothing. The job
must log which packages it published and which it skipped, so a skip is visible
rather than assumed.

Client versions therefore skip numbers — 0.3.0 to 0.7.0 — and every version on
a registry corresponds to a real change to that client.

### Component 5 — The public page

`site/changelog.html`, generated from `release-notes.json` during the fold and
committed. Served by the existing `GET /site/{path:path}` route out of
`Settings.site_dir`. No build step and no new route: it is a static file beside
`how-it-works.html` and follows that page's markup and styling.

### Component 6 — The in-app panel

- `GET /api/release-notes` returns the entries and `unread: bool`, computed by
  comparing the newest entry's version against the caller's `last_seen_release`.
- `POST /api/release-notes/seen` stamps `last_seen_release` to the newest version.
- `AccountMenu` gains a "What's new" item carrying a dot while `unread` is true.
  Opening the panel posts the stamp and clears the dot.
- The panel follows `MembersSheet` for structure and dismissal.

Versions are compared as parsed semver triples, never as strings — `"0.10.0" >
"0.9.0"` is false lexically and true in fact. That comparison lives **only** in
Python, behind the `unread` flag the endpoint returns. The frontend never parses
a version: two implementations of one rule is how the dot ends up disagreeing
with the list it decorates.

### Component 7 — Storage

A single nullable column:

```python
last_seen_release = Varchar(null=True, default=None)  # on Person
```

**This is why the design is cheap.** `persons` already carries
`persons_self_select` and `persons_self_update` in `rls.py`. A column on an
existing table inherits both. There is no new table, no new policy, and nothing
is added to `enable_statements()` — which is the trap this repo has hit twice
(see the "deliberately **not** called from `enable_statements`" headers around
`rls.py:923` and `rls.py:1035`). The migration is a plain `ALTER TABLE`.

`NULL` means "never seen anything", which correctly reads as unread for a person
who predates the feature.

### Component 8 — CI enforcement

A `changelog` job, pull requests only:

> pass if the PR adds a file under `changes/`, **or** carries the
> `no-changelog` label.

It never runs on `main`, so it can never block a release. The label is the
escape hatch that keeps enforcement from becoming a hostage situation.

## Testing

Following the repo's existing layers.

**Unit (Python).** Fragment parsing: valid fragment, unknown `kind`, missing
front matter, empty body, no fragments at all. The fold: correct grouping,
prepend order, and idempotence when run twice.

Semver comparison, including the `0.10.0` vs `0.9.0` case, is tested in Python
alongside the fold — it is the only place that rule exists.

**Unit (frontend).** Panel render and dismissal, colocated as
`ReleaseNotes.test.tsx`.

**API.** `GET /api/release-notes` unread → seen → read transition; the stamp is
idempotent; an unauthenticated caller is refused.

**Integration, real Postgres.** A second person's `last_seen_release` is neither
readable nor writable. RLS is tested here, not assumed — consistent with
`test_security.py` and `test_authz_primitive.py`.

**Schema.** `test_schema.py` covers the new column, and the migration chain is
exercised against an empty database.

## Operator steps

These cannot be done in code and must be done by hand before the first release:

1. **PyPI trusted publishing** for `reef-cli`, pointing at this repo and the
   release workflow.
2. **npm automation token** for `@haai/reef-cli`, stored as a repo secret.
3. **A push token for semantic-release.** Branch protection on `main` will
   reject its `chore(release)` commit otherwise.
4. **Create the `no-changelog` label.**

Until 1 and 2 exist, the publish job fails. Cut the first release only after
they are in place.

## Rollout

1. Column, migration, and its tests.
2. Fragment format, fold, and their tests.
3. `.releaserc.json`, release job, PR dry run — no publishing yet.
4. Operator steps.
5. Path-gated publishing.
6. Public page.
7. In-app panel and unread marker.

Steps 1–3 are independently useful: they give real versions and a `CHANGELOG.md`
before any user-facing surface exists. The first release is cut with an empty
`release-notes.json`, which both surfaces must handle without looking broken.

## Rejected alternatives

**Cut releases and stop auto-deploying `main`.** The textbook answer. Rejected
because it costs the fastest feedback loop on the component that changes most,
to give a version number to a hosted server nobody installs.

**Derive user-facing entries from commits or labels.** Rejected: the register is
wrong. `fix(api): bind Content-Length into the presigned PUT` is a good commit
subject and unusable as a "what's new" line.

**Hand-written `CHANGELOG.md` at release time.** Rejected: it reconstructs user
impact weeks later from memory, and it is the step that gets skipped.

**Release-please-style bot PR.** Rejected: one more moving part, and
semantic-release already matches how `hybrix-app` works.

**Independent versions per client.** Rejected: two implementations of the same
five commands (`login`, `logout`, `tools`, `call`, `help`). Two version lines
would only confuse.
