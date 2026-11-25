# reef-cli

Command-line client for [reef](https://reefwith.me) — shared, living memory
for people and their AI assistants, reached over MCP.

    uv tool install reef-cli
    reef login

Every MCP tool is mirrored as a subcommand; `reef call <tool> '<json>'` is the
exact passthrough. JSON on stdout, always, with one exception: `reef token`
prints a bare access token, for scripts.

Reef reads the text inside the files you store. `recall` answers from inside
a file and names the page; `open` reads the file's text, from any page:

    reef recall 'what does the quote say about the price'
    reef open 'personal/file/<key>#page=3'
