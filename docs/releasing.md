# Releasing reef

reef has no manual release step. Merge a pull request to `main`, and a
version number, a changelog entry, and (if warranted) two published packages
follow automatically. This page covers what a contributor needs to type, what
happens after that, and the one-time setup a human still has to do by hand.

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

## What happens when you merge

Nothing here is a step you run. It's what the `release` job does, on every
push to `main`, once `backend` and `frontend` have both passed:

1. Reads every commit since the last tag and computes the next version.
2. Writes the release notes into `CHANGELOG.md`, generated from commit
   subjects — this is the log talking to itself, not to a user.
3. Runs `scripts/stamp_version.py` and `scripts/fold_changes.py`, which:
   - fold this release's `changes/*.md` fragments into
     `site/release-notes.json` and re-render `site/changelog.html`, then
     delete the fragments they consumed;
   - write the new version into all three manifests —
     `pyproject.toml`, `clients/python/pyproject.toml`, and
     `clients/ts/package.json` — so `reef-cli` and `@haai/reef-cli` never
     drift apart or fall behind the server.
4. Commits all of that as `chore(release): X.Y.Z [skip ci]`, tags it
   `vX.Y.Z`, and pushes both to `main`.
5. Publishes a GitHub Release with the generated notes.
6. Hands off to the `publish` job, which diffs the previous release against
   this one and pushes only the client that actually changed —
   `reef-cli` to PyPI, `@haai/reef-cli` to npm, or both, or neither.

One consequence worth knowing about: `[skip ci]` stops GitHub Actions from
re-running CI on that release commit, but it does **not** stop Railway,
which redeploys on every push to `main` regardless. So every release costs
one extra, no-op production deploy. That's known and accepted, not a bug to
fix.

## Saying what changed, for a person

The changelog above is commit subjects — useful to us, meaningless to
someone using reef. If your change is something they'd notice, add a
fragment under `changes/`. See [`changes/README.md`](../changes/README.md)
for the format and when to skip it.

## Set up once, by hand

None of this lives in code, and none of it can be tested by CI — a human has
to click through it. **The first release fails without the first two
items**, because on the very first release there's no earlier tag to diff
against, so the `publish` job tries to publish both clients unconditionally.

1. **PyPI trusted publishing for `reef-cli`.** On PyPI, add a trusted
   publisher for this repository, workflow `ci.yml`, environment left
   blank.
2. **`NPM_TOKEN` repo secret.** Create an npm automation token with publish
   rights on `@haai/reef-cli`, and store it as the `NPM_TOKEN` repository
   secret.
3. **`RELEASE_TOKEN` repo secret.** Create a fine-grained personal access
   token with `contents: write` on this repository, and store it as the
   `RELEASE_TOKEN` repository secret. Branch protection on `main` rejects
   a push from the default `GITHUB_TOKEN`, so without this, the release
   fails at its very last step — the tag push — with a permissions error
   that reads like a bug in the workflow rather than a missing secret.
4. **Create the `no-changelog` label** on the repository. Without it, a PR
   that genuinely changes nothing user-facing has no way to satisfy the
   `Changelog fragment` check.
5. **Require `Changelog fragment` and `Release dry run` in branch
   protection.** Both are their own checks — gated with a job-level `if:`
   so they only run on pull requests — and neither one is actually
   enforced until branch protection names it by that exact title. Until
   this is set, both checks are decoration: they run, they can fail, and a
   PR merges anyway.

## Checking a release went out

Nobody has cut a release with this pipeline yet — not even a dry run has
been exercised end to end, and no PR has ever triggered the `Changelog
fragment` or `Release dry run` checks for real. What follows is what should
happen, read from the code above, not something confirmed working.

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

If a release lands and doesn't match this, the code and this page have
drifted — fix whichever one is wrong.
