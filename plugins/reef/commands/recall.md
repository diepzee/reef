---
name: recall
description: Answer from reef memory, reading the pages before answering
argument-hint: <topic or question>
---

Answer this from reef: $ARGUMENTS

1. Call `load_index` if you have not already this conversation. The index is a
   map, not evidence — never answer from its one-line descriptions alone.
2. Call `search_pages` when the index does not settle which pages matter. It
   matches words inside bodies, titles, and stored files that the index omits.
3. Call `read_pages` for everything that looks relevant, in one call.
4. Answer from what you read, naming the space and path behind each claim.

If reef holds nothing on this, say so plainly rather than filling the gap
from your own guesses. An invented memory is worse than an absent one.
