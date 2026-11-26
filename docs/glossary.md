# Reef glossary

**Status:** adopted 2026-11-17 for documents, the team deck and tickets. Applied 2026-11-20 to the backend, database tables, web app, public site, settings, export and MCP tools (RUG-40). The sixteen workflow tools replaced the old ones, such as `read_page` and `list_coves`, and use the new words.

One term per concept. The "Replaces" column lists words the documents used for the same thing. Those words are retired.

**The naming rule.** The system is a reef. That is the only marine term. What lives in it and how it behaves follows the brain. A biological term is used only where the real mechanism matches. Plain engineering terms stay where biology has no honest equivalent.

## Places and people

| Term | Meaning | Replaces |
|---|---|---|
| **Reef** | The whole system, and one installation of it | store, substrate |
| **Space** | An area with its own members. Personal or shared | cove, scope |
| **Person** | A human with an account | principal, user |
| **Assistant** | A model acting for a person | agent, client |

## Engrams

| Term | Meaning | Replaces | From biology |
|---|---|---|---|
| **Engram** | One stored memory: the unit a person or assistant writes and reads. A Markdown document | page, note, node, concept page, memory (as a unit) | Engram: the physical trace of one memory in the brain |
| **Episode** | Reef's unchangeable record of one session: what was searched, read, written and remembered, including the assistant's wrap-up of what was discussed and decided. Visible only to the person. Kept forever, dormant over time | session trace, session notes | Episodic memory: what happened |
| **Gist** | An engram the system wrote by consolidating others. It links to its sources and never replaces them | summary, topic page, abstraction | Gist memory: the meaning kept after detail fades |
| **Section** | A part of an engram under one heading | block | none |
| **File** | An uploaded document, stored unchanged | attachment | none |
| **Revision** | One saved state of an engram. Never deleted | version | none |

## Connections

| Term | Meaning | Replaces | From biology |
|---|---|---|---|
| **Link** | A connection someone wrote, such as a `[[wiki link]]`. Shared by everyone who can read both ends | edge, structural edge, reference | none |
| **Association** | A connection Reef learned because one person used two engrams together. Private to that person | learned link, learned edge, Hebbian edge | Associative learning: what fires together wires together |
| **Strength** | How strong an association is | weight | Synaptic strength |
| **Pruning** | Removing associations that have become too weak | none | Synaptic pruning |
| **Scaling** | Keeping the total strength leaving one engram within a fixed budget | normalisation, fan penalty | Synaptic scaling |

## Recall

| Term | Meaning | Replaces | From biology |
|---|---|---|---|
| **Recall** | Finding engrams for a question. The tool keeps the name `search` | retrieval | Recall |
| **Cue** | Anything that starts a recall: the question itself, or a **learned cue** | seed, starting point | Retrieval cue |
| **Learned cue** | Wording that Reef remembers as leading to an engram, learned from a failed search | cue row, miss | Cue-dependent recall |
| **Priming** | Engrams used earlier in the session make related engrams easier to recall | session priming, session context | Priming |
| **Activation** | How readily an engram comes to mind for one person, from how often and how recently they used it | heat, usage score, hot path | Activation |
| **Spreading** | Activation passing from an engram to the engrams connected to it | graph expansion, spread | Spreading activation |
| **Salience** | How much one event counts. A correction or explicit "remember this" counts three times a routine read or write | weight (of an access) | Salience |
| **Active, resting, dormant** | The three states of an engram for one person. A dormant engram stays findable but drops to the end of the index | hot, warm, cold, tier, forgetting | Dormancy |
| **Index** | The short list of every engram an assistant loads at the start of a session | digest, briefing | Hippocampal index |
| **Recall path** | The explanation attached to a result: how well it matched, its activation, and what spread into it | trace | none |

The usage log records `read`, `write` and `web` with weight 1, and `correct`
and `remember` with weight 3. A refused correction, or one that replaces a
passage with itself, records no use. `undo` still records a routine `write`.
Being shown in search results or the index records nothing.

The full data download includes these kinds and their stored weights in
`data/usage.json`, with the person's activation in `data/activation.json`.
Both contain only that person's usage, including in shared spaces. Old
corrections recorded as writes keep their original kind and weight.

## Dreaming

| Term | Meaning | Replaces | From biology |
|---|---|---|---|
| **Dreaming** | The nightly process in which Reef reorganises one person's engrams | maintenance, the night | Sleep |
| **Dream** | One night's run for one person | dream run, run | none |
| **Replay** | Choosing which engrams and episodes a dream revisits: recent, surprising, and a random sample of old ones | replay selection | Hippocampal replay |
| **Consolidation** | What dreaming does with replayed material: strengthen associations, write gists, flag contradictions | passes, steps | Memory consolidation |
| **Dream journal** | The record of everything a dream did, with the state before and after. People read it in the morning and can undo from it | dream action journal, morning log | none |
| **Undo** | Reverting one dream, or one entry in the dream journal. A change a person made afterwards is left alone | rollback, revert | none |
| **Gate** | The check a dream must pass before its changes are kept | quality gate, eval gate | none |

## Engineering terms that stay

These have no honest biological equivalent, so they keep their plain names: **embedding**, **worker**, **job**, **switch** (replaces flag, ablation flag), **benchmark**, **supersede**, **contradiction**, **chunk** (a part of a file).

## Open points

1. **How far the rename goes.** Documents, deck and tickets use these terms. So do the backend, database tables, web app, site, settings, export and MCP tools. The old tool names (`read_page`, `write_pages`, `list_coves`) are retired: a call to one is answered with the name of the tool that replaces it.
2. **"Memory" still names the whole.** "The memory engine" and "Reef is a memory" stay. Only the unit changed to "engram".
