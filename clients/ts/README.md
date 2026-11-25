# reef-cli

Command-line client for [reef](https://reefwith.me) — shared, living memory
for people and their AI assistants, reached over MCP.

    npm install -g @haai/reef-cli
    reef login

`reef call <tool> '<json>'` is the exact MCP passthrough; `reef tools` lists
the live schemas. JSON on stdout, always. (A Python twin exists:
`uv tool install reef-cli`.)

Reef reads the text inside the files you store. `recall` answers from inside
a file and names the page; `open` reads the file's text, from any page:

    reef call recall '{"reason":"asked","question":"what does the quote say about the price"}'
    reef call open '{"reason":"asked","refs":["personal/file/<key>#page=3"]}'
