# Releasing reef

reef has no manual release step for day-to-day work. Merge a pull request
to `main`, and a bot keeps one release pull request up to date for you.
When a human squash-merges *that* PR, a version number, a changelog entry,
a GitHub release, and (if warranted) two published packages follow
automatically. This page covers what a contributor needs to type, what
happens after that, who has to click what, and the one-time setup a human
still has to do by hand.

## What to type in your PR title

reef decides the next version from your commit history using
[Conventional Commits](https://www.conventionalcommits.org/). Pull requests
are **squash-merged**, so the commit that lands on `main` is your **PR
title** — not any of the individual commits inside the PR. Write the PR
title as the conventional-commit subject; the commits underneath it can say
whatever helped you while you worked.

Format: `type(scope): what changed`. The scope is optional. Keep the
narrative style the rest of the log uses — say what the change does, not
which files moved:

```
feat(search): answer "what did we know in March" with a WHERE clause
fix: remove unnecessary noqa comment
docs(release-notes): name the checks branch protection has to require
refactor(release-notes): rename feature and fix README.md handling
```

The type decides the version bump (`.releaserc.json` is the source of truth
if this table and the file ever disagree):

| Type | Bumps |
|---|---|
| `feat` | minor |
| `fix`, `perf`, `refactor`, `chore`, `docs`, `build`, `ci`, `style`, `test`, `revert` | patch |
| any type, with `!` after the type/scope or a `BREAKING CHANGE:` footer | major |

A PR that changes nothing worth a version bump — say, a comment fix folded
into another PR — still needs a type; there's no "none" option. Pick the
type that best describes what you touched.

This table only governs the *second* release onward. semantic-release bumps
from the most recent tag; with none, it doesn't bump at all — it starts the
repo at `1.0.0` regardless of commit type. reef already has tags on `main`
from earlier releases, so this only matters again if the repo's tag history
is ever wiped.

## What happens when you merge

Nothing here is a step you run yourself.

1. Your PR merges to `main` as a squash commit, once `Backend (pytest +
   ruff)` and `Frontend (bun test + tsc + build)` have both passed.
2. That push triggers the `Release PR` workflow. It runs semantic-release
   in **dry-run mode** — it never writes to `main` — to work out the next
   version and generate release notes from every commit since the last tag.
3. If there's a release to make, the bot (`rif-release-bot`, a GitHub App)
   builds it on a branch called `bot/release`:
   - runs `scripts/stamp_version.py` and `scripts/fold_changes.py`, which
     fold this release's `changes/*.md` fragments into
     `site/release-notes.json` and re-render `site/changelog.html`, then
     delete the fragments they consumed, and write the new version into
     every manifest — `pyproject.toml`, `clients/python/pyproject.toml`,
     `clients/ts/package.json`, and the plugin's `plugin.json` and
     `marketplace.json` — so `reef-cli`, `@haai/reef-cli` and the plugin
     never drift apart or fall behind the server;
   - writes the generated notes into `CHANGELOG.md`;
   - commits all of that as one commit, `chore(release): X.Y.Z`, with the
     release notes as the commit body;
   - force-pushes `bot/release` and opens or refreshes a pull request
     titled `chore(release): X.Y.Z`, labelled `release` and
     `no-changelog`.
4. The bot repeats step 3 on every subsequent push to `main`, so the release
   PR always reflects everything merged so far. It never merges the PR
   itself — a human decides when to release by squash-merging it.
5. **Squash-merging the release PR is the release.** That puts
   `chore(release): X.Y.Z (#N)` on `main` as a single commit. CI's `tag`
   job recognises that commit, tags it `vX.Y.Z`, and publishes a GitHub
   Release using the commit body as the notes.
6. The `publish` job then diffs the previous release against this one and
   pushes only the client that actually changed — `reef-cli` to PyPI,
   `@haai/reef-cli` to npm, or both, or neither.

Nobody — human or bot — pushes straight to `main`. The release PR goes
through the same protected path, checks and all, as any other change; the
branch ruleset has no bypass actors. **Squash-merge only**, never rebase-
or merge-commit the release PR: the tag job and the publish gate both
depend on the release landing as exactly one commit.

Don't edit `bot/release` by hand. The next push to `main` rebuilds it from
scratch and force-pushes over whatever was there.

## Saying what changed, for a person

The changelog above is commit subjects — useful to us, meaningless to
someone using reef. If your change is something they'd notice, add a
fragment under `changes/`. See [`changes/README.md`](../changes/README.md)
for the format and when to skip it.

## Set up once, by hand

None of this lives in code, and none of it can be tested by CI — a human has
to click through it once, when standing up the pipeline (or moving it to a
new repository).

1. **The `rif-release-bot` GitHub App.** Install it on this repository
   only, with `Contents: RW`, `Pull requests: RW`, and `Issues: RW`. Store
   its App ID and private key as the `RELEASE_BOT_APP_ID` and
   `RELEASE_BOT_PRIVATE_KEY` repository secrets — both workflows mint a
   short-lived installation token from these on every run rather than using
   a long-lived credential. The app token, not the default `GITHUB_TOKEN`,
   is what lets a bot-pushed `bot/release` branch trigger `pull_request`
   checks at all.
2. **Zero bypass actors on the `main` ruleset, squash-only merges.**
   Nothing — human or bot — may push directly to `main`, and the allowed
   merge methods on `main` are squash only. Both are what make the release
   PR's exactly-one-commit invariant hold without anyone having to remember
   to squash by hand.
3. **PyPI trusted publishing for `reef-cli`.** On PyPI, add a trusted
   publisher for this repository, workflow `ci.yml`, environment left
   blank.
4. **`NPM_TOKEN` repo secret.** Create an npm automation token with publish
   rights on `@haai/reef-cli`, and store it as the `NPM_TOKEN` repository
   secret.
5. **The `release` and `no-changelog` labels** exist on the repository.
   The bot applies both to the release PR: `no-changelog` because the PR
   *consumes* fragments rather than adding one, and `release` to make it
   easy to spot in the PR list.
6. **Require `Backend (pytest + ruff)` and `Frontend (bun test + tsc +
   build)` in branch protection.** Both gate the `tag` job too — a broken
   build never gets tagged, because the same run that tests the merge does
   the tagging.

## Checking a release went out

v0.3.1 shipped through this pipeline on 2026-08-17 — the model described
above is exercised, not aspirational.

After squash-merging a release PR, look for:

- A new tag `vX.Y.Z` exists on `main`.
- A matching entry appears on the repo's GitHub Releases page.
- `CHANGELOG.md`, at the repo root on `main`, has a new section.
- If `clients/python/` changed since the last release, the new version is
  on [PyPI](https://pypi.org/project/reef-cli/).
- If `clients/ts/` changed since the last release, the new version is on
  [npm](https://www.npmjs.com/package/@haai/reef-cli).
- If any `changes/*.md` fragments existed, they're gone from `changes/`,
  and `site/release-notes.json` and `site/changelog.html` on `main` both
  carry the new entry.
- `gh pr list --head bot/release` is empty, and the `Release PR` workflow
  run triggered by the release merge shows its job skipped — the release
  merge must not spawn another release PR for itself.

If a release lands and doesn't match this, the code and this page have
drifted — fix whichever one is wrong.
