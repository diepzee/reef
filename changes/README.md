# What to write here

Add a file here when your change is something a **person using reef** would
notice. Most changes are not: test isolation, CI tuning and refactors never
need one.

Name it for your PR number, so two branches never collide:
`changes/57-search-pages.md`.

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
