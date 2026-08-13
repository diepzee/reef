# reefwith

Command-line client for [reef](https://reefwith.me) — shared, living memory
for people and their AI assistants, reached over MCP.

    uv tool install reefwith
    reef login

Every MCP tool is mirrored as a subcommand; `reef call <tool> '<json>'` is the
exact passthrough. JSON on stdout, always.
