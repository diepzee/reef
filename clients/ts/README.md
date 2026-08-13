# reef-cli

Command-line client for [reef](https://reefwith.me) — shared, living memory
for people and their AI assistants, reached over MCP.

    npm install -g @haai/reef-cli
    reef login

`reef call <tool> '<json>'` is the exact MCP passthrough; `reef tools` lists
the live schemas. JSON on stdout, always. (A Python twin exists:
`uv tool install reef-cli`.)
