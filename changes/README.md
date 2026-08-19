# What to write here

Add a file here when your change is something a **person using reef** would
notice. Most changes are not: test isolation, CI tuning and refactors never
need one.

Name it for your branch, and commit it **with your work, before you open
the pull request**: `changes/search-pages.md`.

Branch names are unique, so two branches cannot collide — and unlike a PR
number, you know yours before the PR exists. That ordering matters. The
Changelog fragment check runs the moment a PR opens, so a fragment named
after a number you cannot know yet fails once, every time, before you have
done anything wrong. Nothing reads the filename: fragments are collected by
glob and ordered by `kind`.

```markdown
---
kind: added
---
Search your pages from your assistant — ask for anything you've written
down and reef finds it.
```

`kind` is one of `added`, `changed`, `fixed`. Nothing else is accepted.

Write the body for the person reading it, not for us:

- Say what they can now do, not what we built.
- One or two sentences, present tense, no identifiers.
- No `search_pages`, no RLS, no worktrees. "Search your pages", not
  "search_pages: RLS-scoped full-text search".

At release time these files are folded into `site/release-notes.json` and
deleted. That file feeds the public changelog and the "What's new" panel in
the app.

If your PR genuinely changes nothing a user would notice, add the
`no-changelog` label instead and CI will let it through.

## How these become a release

A bot keeps one open PR titled `chore(release): X.Y.Z` (branch
`bot/release`), refreshed on every merge to main. It folds the fragments
here into `site/release-notes.json`, stamps the version into the three
manifests, and prepends `CHANGELOG.md`. **Squash-merging that PR is the
release** — it tags, creates the GitHub release, and publishes any client
that changed. Nobody pushes to main, the bot included; don't edit
`bot/release` by hand, the next merge to main rebuilds it from scratch.
