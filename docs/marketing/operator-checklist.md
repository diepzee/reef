# Going-public checklist (operator actions, in this order)

Sequencing matters: main auto-deploys production, so the site must not
advertise install commands before the packages exist.

1. [ ] Review + merge this branch's PR — EXCEPT do not merge before steps 2–3
       if the PR already contains the new site install copy.
2. [ ] Publish the Python CLI: `rm -rf dist && uv build clients/python && uv publish dist/reef_cli-*`
       (needs a PyPI token for a new `reef-cli` project).
3. [ ] Publish the TS CLI: `cd clients/ts && npm publish` (needs `npm login`;
       first publish of `reef-cli`).
4. [ ] Verify both installs from clean environments:
       `uv tool install reef-cli && reef --help`, `npx @haai/reef-cli --help`.
5. [ ] Merge the branch; confirm reefwith.me shows the new copy.
6. [ ] Flip the repo public: GitHub → Settings → General → Danger zone →
       Change visibility. (gitleaks scanned all history 2026-08-13: clean.)
7. [ ] Upload the social preview image (Settings → Social preview, 1280×640).
8. [ ] Official MCP registry: install `mcp-publisher`, verify the
       `me.reefwith` namespace via DNS or HTTP, `mcp-publisher publish`
       with the repo's server.json.
9. [ ] mcp.so — submit via their "Submit" flow with https://reefwith.me/mcp.
10. [ ] glama.ai/mcp — claim/submit the server listing
        (see directory-submissions.md for the enrichment notes).
10b. [ ] **Anthropic connector directory** — the highest-leverage listing;
        ready-to-paste copy and the invite-only review caveat are in
        directory-submissions.md. Needs a privacy policy page first.
11. [ ] awesome-mcp-servers — PR adding reef under a memory/knowledge section.
12. [ ] Post Show HN and the X thread (docs/marketing/launch-drafts.md) —
        ideally a weekday morning US time; be around to answer comments.
