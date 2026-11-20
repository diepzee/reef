---
name: recall
description: Answer from Reef memory, using the passages that fit the question
argument-hint: <topic or question>
---

Answer this from Reef: $ARGUMENTS

1. Call `load_index` if you have not already in this conversation.
2. Call `recall` with the question in the person's words. Give it a short
   reason taken from their request.
3. Call `open` on a ref only if its passage is not enough.
4. Answer from what you read, and name the space and engram behind each claim.

If Reef holds nothing on this, say so plainly. Do not fill the gap with your
own guesses. An invented memory is worse than an absent one.
