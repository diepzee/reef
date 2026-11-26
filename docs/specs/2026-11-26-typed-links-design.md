# Typed links, and engrams that have been replaced (26 November 2026)

Design for the two leftovers of RUG-36 that are not frontend work. It builds
on the links table of PR #128 and on the memory engine design of
15 November 2026 (`docs/superpowers/specs/2026-11-15-memory-engine-upgrade-design.md`,
not in git).

## Intent

An engram can say how it relates to another engram: it **replaces** it,
**contradicts** it, is **about** it, or is **part of** it. Today the only
link is a plain `[[wiki link]]`, which says "this mentions that".

The one relation that changes behaviour is "replaces". When a person says
"the 2027 contract replaces the 2026 one", an assistant that later searches
for the contract must answer from the 2027 engram. Today both engrams rank
by their words, and the old one can win.

Why now: dreaming (RUG-28, RUG-39) will find duplicates and outdated
engrams. It needs a way to say "this one replaces that one" that keeps the
old engram, and a search that respects it. This builds that way first, for
a person to use by hand.

## Goals

1. A person, or an assistant on a person's word, can mark an engram as
   replacing another one.
2. A replaced engram stays readable and findable, ranks below engrams that
   are not replaced, and names its replacement wherever it is shown.
3. The other three relations can be written and are shown when an engram is
   opened. They change no ranking.
4. A memory without typed links behaves exactly as it does today.

## Non-goals

- Dates of validity (`valid_from`, `valid_to`) and a confidence value. The
  15 November design has them in the same block. They wait for dreaming.
- Proposed links and a tool or a screen to review them.
- Anything the dreamer does.
- A screen in the web app for links, or the graph reading the table. Both
  belong to the redo of the web app (RUG-59).
- Links to a section of an engram.
- Stopping writes to a replaced engram.

## Anti-goals

- A replaced engram is hidden or cannot be opened.
- An assistant marks an engram as replaced without the person's yes.
- A reader learns that an engram exists which they may not read, through a
  "replaced by" note, a lower rank or a count.
- Text is lost when a front matter block is read: a line Reef does not
  understand must stay in the body.
- Search results change for a memory that has no "replaces" link.

## Constraints

- The `links` table already has what is needed: `kind` (six kinds, one of
  them `supersedes`), `source` (`author`, `wikilink`, …) and `status`. No
  new table and no new column. One migration is still needed: `links` has
  no way to retire a typed link today (`reef_retire_links` only knows wiki
  links, by the words left in the body), and the rule of part 2 about
  write access belongs in the database too.
- A link exists for a reader only when they can read both ends. Row
  security enforces this, and it refuses a link from a shared space into a
  personal one. Both rules stay as they are.
- Tools are workflows (RUG-40). No new tool.
- `share`, `forget` and `instruct` already ask twice through staged tokens
  (`src/reef/methods/staged.py`). The same mechanism is used here.
- The stored body never holds the link lines. Everything that reads a body
  (the summary, the leak guard, the inbox, `correct`, the export, the web
  app) keeps seeing plain Markdown.

## Approaches

### How "replaces" from an assistant becomes valid

1. **The tool asks twice** (chosen by Wouter on 26 November 2026). The
   first call changes nothing and returns the effect and a token. The
   person says yes in the conversation. The second call brings the token
   back. It keeps "a person decides" with what exists.
2. **A review tool**, as the 15 November design said: an assistant's
   "replaces" is stored as a proposal that a person approves later. It
   needs a tool or a screen that does not exist, and only earns its place
   once dreaming makes proposals.
3. **Leave "replaces" for dreaming** and build only the other three
   relations. The search part would then wait too.

### Where a relation is written

1. **A front matter block at the top of the body** (my call). It is what
   the ticket says, it is the only way a person can write a relation in
   the web app's editor today, and an assistant writes it as easily. One
   syntax, one parser, in the layer under both the tools and the web app.
2. **A field on the `write` tool's engram object.** Checked by the tool's
   schema, but it gives a person in the web app no way to write a relation,
   so the parser would be needed anyway.
3. **Both.** Two ways to say the same thing, for no gain today.

## The design

### 1. Writing a relation

A body may start with a front matter block:

```markdown
---
replaces: [[contract-2026.md]]
about: [[family:house.md]]
---
# Contract 2027

The new contract runs from 1 January 2027.
```

- **Keys:** `replaces`, `contradicts`, `about`, `part_of`. `replaces` is
  the plain word for the stored kind `supersedes`; the stored kind does not
  change.
- **Values:** one or more wiki links, on the key's line separated by
  commas, or as a list with one link per line. A wiki link names an engram
  exactly as it does in a body: `[[x.md]]`, `[[family:x.md]]`,
  `[[personal:x.md]]`.
- **What Reef takes out:** the lines of these four keys. Every other line
  of the block stays where it is, as text. A block that is empty afterwards
  is removed with its two `---` lines. A body without such a block, or
  whose block holds none of the four keys, is stored exactly as today.
- **Where it is read:** in the one place every save passes through, so it
  works the same for the `write` tool, for the web app's editor and for
  any later caller. `correct`, `remember` and `log` change a passage or add
  to a body; they never read a block.

### 2. What a save does with it

- **The target must exist** and the writer must be able to read it. If it
  does not, the save is refused and names the link. (A plain wiki link may
  point at an engram that comes later; a typed link may not.)
- **For `replaces`, the writer must also be able to write in the target's
  space.** Marking an engram as replaced changes what every member finds.
  The database enforces this rule, not only the code above it.
- **No link to itself, and no circle of `replaces`.** A replaces B and B
  replaces A is refused.
- **At most 20 links per key.**
- **The existing rule stands:** no link from a shared space into a
  personal one.
- **Each link becomes a row** in `links` with its kind and the source
  `author`.
- **What is left unsaid stays.** A save whose body has no line for a key
  leaves the links of that kind as they are. A line for a key replaces the
  list of that kind. A key with an empty value (`replaces:`) removes them.
  A removed link is marked `superseded` and kept, as a removed wiki link
  is.
- **A save acts only on links the saver can read.** In a shared engram,
  another member may have written a typed link to a space the saver is not
  in. The saver cannot see that link, so their line neither lists it nor
  removes it. (A wiki link is different: it is retired when its words leave
  the body, whoever saves.)
- **Two engrams may replace the same one.** That happens when an engram is
  split in two, or when two members each write a replacement. Nothing
  refuses it.
- **`undo` restores a body.** Typed links are not part of a revision, so
  `undo` leaves them alone. Taking a "replaces" back is a save with
  `replaces:`.

### 3. The person's yes

- **An assistant's save that adds a `replaces` link asks twice.** The
  `write` tool gains one optional parameter, `confirm`. Without it, a call
  that would add a `replaces` link writes nothing at all, also not the
  other engrams of the same call, and returns the effect and a token:

  ```json
  {"effect": "This marks personal/contract-2026.md as replaced by personal/contract-2027.md. The old engram stays readable and ranks lower in search.",
   "token": "<token>",
   "expires_in": 600}
  ```

  That is the shape `share`, `forget` and `instruct` already return. The
  second call repeats the same engrams and passes the token as `confirm`.
  The token is bound to the person, to the action and to the pairs it
  named, and it expires as the other staged tokens do.
- **A person's own save in the web app needs no second step.** They typed
  it. The save route already knows the writer is a person.
- **The other three keys, and removing any link, need no yes.**

### 4. What "replaced" changes

An engram is **replaced, for a reader**, when an active `replaces` link
points at it from an engram that reader can read. Its **replacements** are
the engrams at the ends of the chains: if A replaces B and B replaces C,
C's replacement is A. There is usually one. When two engrams replace the
same one, there are two, and the one whose link is newest comes first.

- **Everything that searches.** The order is changed inside the search
  itself, so `recall`, `brief` and the connector's `search` all get it.
  Among the engram hits, those that are replaced come after those that are
  not; each group keeps its order. A replaced hit carries `replaced_by`, a
  list of the refs of its replacements. When none of them is among the
  hits, the first one is put directly before the replaced hit, and the list
  is cut to the asked length from the end. It is put there only when it
  lies in a space that was searched; otherwise the note alone has to do.
  Files keep their place: the rule of RUG-77 that files leave room for
  engrams still holds.
- **`brief`** hands out whole bodies. An item for a replaced engram
  carries `replaced_by` too, so an assistant does not advise from the old
  text without knowing.
- **`open` and the connector's `fetch`.** An opened engram that is
  replaced carries `replaced_by`. Its links already come with their kind.
- **`load_index`.** The line of a replaced engram ends with
  `(replaced by <path>)`, with the first replacement.
- **The protocol** (`src/reef/protocol.md`) gains a short passage: how to
  write the four relations; that `replaces` takes two calls and the
  person's yes; and that a replaced engram is out of date, so answer from
  its replacement and add new facts there.
- **Nothing else changes.** `catch_up` is as it is, and every write to a
  replaced engram still works.

### 5. Privacy

- "Replaced" is decided per reader, with the same rule as every link. A
  reader who cannot read the replacement sees the old engram as not
  replaced: no note, no lower rank.
- The effect text and the token name paths, never text from an engram.
- Nothing new is logged.

### 6. The data download and deleting

- `data/links.json` already holds every link with its kind. Typed links
  need nothing more.
- Deleting an engram removes its links, as today.

## Done when

- A body with a block is stored without the four keys' lines, and with
  every other line unchanged.
- The rules of part 2 each have a test, with the refusal a person can
  understand.
- An assistant's `replaces` writes nothing on the first call and everything
  on the second; a person's save in the web app writes at once.
- In search, a replaced engram ranks below the others and names its
  replacement; for a reader who cannot read the replacement, nothing
  changes.
- The private question set and the benchmark give the same scores as
  before the change. They hold no typed links, so any difference is a
  fault.
- A new test conversation: the person says one engram replaces another;
  the assistant asks, writes after the yes, and a later question is
  answered from the new engram.
- The whole suite passes, and the change is live in production. It needs
  no switch: it does nothing until somebody writes a typed link.

## Decided by Wouter (26 November 2026)

- The design is approved.
- An assistant's `replaces` becomes valid through two calls and the
  person's yes.
- In the web app's editor the block disappears after a save and nothing
  shows the relation. That is accepted until the web app is redone
  (RUG-59). `open` shows the links to an assistant.

## Calls I made (check these while reading)

- The word in the block is `replaces`, not `supersedes`.
- Front matter only; no field on the `write` tool.
- Lines Reef does not understand stay in the body. Reef does not parse the
  block as full YAML; it reads its own four keys.
- A typed link's target must exist, unlike a wiki link's.
- `replaces` needs write access to the target's space.
- What a save leaves unsaid stays; `undo` does not touch typed links.
- A whole `write` call waits for the yes, not only the engram with the
  `replaces` line.
- Replaced hits go after all hits that are not replaced, and a missing
  replacement is put before its replaced hit, but only from a space that
  was searched.
- Every search gets the new order, `brief` included.
- Two engrams may replace the same one; `replaced_by` is a list, newest
  link first.
- A save changes only the typed links the saver can read.
- The write-access rule for `replaces` is enforced in the database.
- The index line gets a `(replaced by …)` note.
- `contradicts`, `about` and `part_of` are stored and shown, and do
  nothing else yet.

## Left to the builder

- The exact grammar of the block and the refusal texts, in the style of
  the existing messages in `src/reef/methods/__init__.py`.
- Where in the search pipeline the replaced hits are moved, so that the
  rule of RUG-77 for files still holds and the reranker's order inside
  each group is kept.
- How the chain of replacements is read without one query per hit.
- Whether `tests/test_protocol.py` needs new allowed words for the tool
  description and the protocol text.
