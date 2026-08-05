# rif

A remote MCP server over the `mark` knowledge layers.

`mark` is a markdown-in-git personal knowledge base. `rif` makes it reachable
from surfaces that have no filesystem and no GitHub account — chiefly the Claude
mobile app — so more than one person in a household can have an assistant with
long-term memory without anyone but the owner touching git.

The store stays plain markdown in git. `rif` is an adapter, not an owner: the
repos remain fully usable from Claude Code, Codex, or a text editor whether or
not this server is running.

Design: [`docs/spec.md`](docs/spec.md).
Architecture it depends on: `mark/meta/architecture.md`.

**Status:** spec only. No implementation yet.
