# Priming, spreading and a recall path (28 November 2026)

Design for RUG-25, with the test sets of RUG-32 that it needs. It builds on
`docs/specs/memory-engine.md`, section 3.2, and changes two things in it
(see "Decisions").

## Intent

Today `recall` ranks an engram by how well it matches the question, by
words and by meaning. Nothing else counts. Two things a person would
expect to count do not:

- **What the conversation is about.** A follow-up question such as "and
  what did that cost?" is searched as if nothing came before it.
- **What is linked.** An engram that the best match links to is treated
  like any stranger.

This is the first step in which something other than the match shapes
what comes up. It is also the first real test of the self-organising
design: whether such signals find what matching misses.

Why now: it is the next step in the agreed build order, and everything
after it (associations, dreaming) spreads along the same paths.

## What was found before designing

Counted in production on 28 November 2026 (counts only, read-only):

- **Use is nearly empty.** 91 logged searches, but 2 rows in the usage
  log. `recall` answers with passages, so an assistant rarely opens an
  engram, and only opening and writing count as use.
- **Links are there.** 67 of 77 engrams have a link; 176 links in all.
- **Nothing can show a gain yet.** The private question set is at its
  ceiling (37 to 40 of 40), and the benchmark has neither links nor
  sessions.
- **Real sessions hold several searches.** Of the 18 searches by a real
  account, 8 came later in a session that had already searched. So the
  session label works for real clients, and priming would have fired for
  almost half of them.
- **The test conversations do not.** Their runner starts Codex anew for
  each message, so each message is a session of its own: 73 searches in
  67 sessions. Priming cannot be tested through them.

## Decisions

By Wouter, 28 November 2026:

1. **Priming and activation feed on different things.**
   - **Priming** feeds on what the earlier searches of the same session
     returned. That is session context. It is not use, it is not stored
     as use, and it stops counting when the session ends.
   - **Activation** keeps the strict rule: only opening and writing count.
     It waits for real use.

   This changes section 3.2 of the memory engine design, which primed
   from "the last ten engrams a person used in the session".

My calls, for Wouter to check:

2. **The bonus for activation is not built now.** With 2 usage rows there
   is nothing to rank by and nothing to test on. The recall path does not
   show activation either. Both come when the usage log holds real data.
   The daily limit on what counts as use (open point 13 of the memory
   engine design) moves with them: it matters only once something ranks
   by activation.
3. **`open` gets no "associated" list now.** Without learned associations
   it would repeat the links `open` already returns. It comes with
   associations (RUG-29).
4. **Test sets first.** Each mechanism gets a set it can prove itself on
   before it is judged (see "Measuring").

## Goals

1. A follow-up question finds the engrams the session was just about.
2. An engram linked from a good match comes up higher than one that is
   not.
3. A result that either of them moved up says so, and from where.
4. Each mechanism has its own switch, off by default, and a test set
   that can show whether it helps.
5. With both switches off, search answers exactly as today.

## Non-goals

- A bonus for activation, a daily limit on use, an "associated" list in
  `open`: see decisions 2 and 3.
- Learned associations and learned cues (RUG-29, RUG-30).
- Bringing in an engram that does not match at all (see "Approaches").
- Switching anything on for everyone. That is RUG-38, after the numbers.
- Changing what the search log stores or how long.

## Anti-goals

- **What is shown gets shown more.** Priming must not feed anything that
  lasts. It reads the session's earlier results; it writes nothing.
- **An engram that does not match rides in on a link.** A link may lift a
  match. It may not replace one.
- **A slower search.** Both mechanisms together may add at most 20 ms to
  the median `recall`.
- **A leak.** Links and logged searches are read as the person, under row
  security. One member's session never primes another member's search.
- **A longer answer for nothing.** Only a result that was moved up
  carries extra text.

## Constraints

- Search carries places, not scores. After fusion and after the reranker
  a result has a place in a list and nothing else. The lift has to work
  on places.
- Search has three routes (words only, words and meaning, reranker). The
  new stage runs in all three, at one point: after the reranker, before
  replaced engrams step back and before the last spreading of files and
  the cut.
- The routes do not have the same pool today. Words only cuts at the
  asked number (5 for `recall`); the reranker without vectors has 15;
  words and meaning has up to 100. With a switch on, every route gives
  the stage up to 50 candidates. With both off, each route is exactly as
  today.
- The reranker runs only for a memory of 1,000 engrams or more. No memory
  is that large, so the stage on that route is covered by tests with made
  engrams, not by the test sets.
- A question with a quoted phrase or an exclusion stays exact: no lift.
- Links connect engrams. Files get no lift and are not primed.
- The session is the MCP session, by the digest the logs already use.
  Outside one (web app, scripts) there is no priming.
- Priming reads the search log, so it needs `REEF_SEARCH_LOG` on. With it
  off there is nothing to prime from, and priming does nothing.
- The search log holds what `recall` and the connector's `search`
  returned. `brief` is not logged, and that stays so: a brief does not
  prime the next search.

## Approaches

**A. Reorder the matches (chosen).** Take the candidates that matching
found (up to 50). Work out which of them the session or the links point
at. Lift those by a bounded factor and sort again. Nothing new enters the
list.

- For: small, fast, easy to explain in a recall path, and it cannot bring
  in an engram that does not match.
- Against: an answer that matches so poorly that it is not among the
  candidates stays unfound. In a memory of 77 engrams nearly everything
  is a candidate, so this hardly bites today.

**B. Also bring in linked engrams that did not match.** Closer to what
"spreading" means, and it could find more. But it can put an engram in
front of a person for no reason they can see, it needs more reading per
search, and nothing today shows it is needed. If the linked set shows
answers that are missed because they were never candidates, this is the
follow-up.

**C. Personalised PageRank over the link graph**, ten rounds, as the
memory engine design proposed. On a neighbourhood of two hops, more
rounds change little, and it is harder to explain per result. Two rounds
of passing weight along links (below) give the same shape.

Wouter left the how to me. I chose A, with C's idea cut to two rounds.

## The design

### One stage, two sources of weight

The stage gets the candidates in order, best first. It gives some of them
**weight** from two sources, each behind its own switch.

**Priming** (`REEF_SEARCH_PRIMING`, off by default)

- Read this session's earlier searches from the search log: the person's
  own rows with this session's label, from the last 60 minutes, newest
  first.
- From each, take the first 3 engrams it returned. Stop at 10 engrams.
- The engrams of the newest search each get weight 0.5. Each search
  further back gets half of the one after it.
- An engram returned by several searches gets the sum.

**Spreading** (`REEF_SEARCH_SPREADING`, off by default)

- The sources are the first 20 candidates, each with weight `1 / place`,
  and, when priming is on, the primed engrams with their weight.
  Files in positions 1 to 20 are skipped; no later candidate takes their place as a source.
- Each source passes half of its weight to its link neighbours, shared
  equally among them. So an engram with many links gives each neighbour
  less.
- Each neighbour passes half of what it received onward the same way.
  That is two rounds, and it stops there.
- Links count in both directions and all kinds count alike, with one
  exception: nothing is passed into an engram that has been replaced.
  A replaced engram is never a source and receives or passes no linked weight, but keeps its own primed weight.
- Only active links that the person can read at both ends exist for this.

A candidate does not get weight from itself: it already has its place.

So a candidate can have two weights, kept apart:

- its **primed weight**: what it got for being returned earlier in the
  session;
- its **linked weight**: what was passed to it along links, from the
  question's best matches and, with priming on, from the primed engrams.

With priming on and spreading off, there is only primed weight. With
spreading on and priming off, linked weight comes from the question's own
best matches alone.

**Accepted:** a primed engram that reaches the first three results primes
the next search again. That loop stays inside one session, halves with
each search, and ends after 60 minutes.

### The lift

- A candidate at place `p` has the base score `1 / p`.
- Its primed share is its primed weight divided by the largest primed
  weight of any candidate in this search. Its linked share likewise. No
  weight, no share.
- Its score is `base × (1 + priming_lift × primed share) ×
  (1 + spreading_lift × linked share)`.
- Sort by score. Equal scores keep their order.

Two settings, `REEF_SEARCH_PRIMING_LIFT` and `REEF_SEARCH_SPREADING_LIFT`,
so each mechanism is tuned on its own set.

A candidate with the full lift moves from place `p` to about
`p / (1 + lift)`. With a lift of 0.3 the sixth becomes fifth and little
else changes. With 1.0 the ninth can reach the fifth. Both start at 0.3,
the number the memory engine design gave for spreading. The test sets
decide between 0.3, 0.5 and 1.0 for each, the way they decided the weight
of search by meaning. That design meant its 1.3 for scores; search here
has only places, so the bound is stated in places instead.

Only candidates are sorted. An engram that is not a candidate gets no
place, however much weight it has.

### The recall path

A result of `recall` or `brief` that the stage moved up carries `why`:

```json
"why": {
  "place_by_match": 6,
  "primed": true,
  "linked_from": ["personal/projects/bathroom-renovation.md"]
}
```

- `place_by_match`: its place before the stage.
- `primed`: present and true when this session's earlier searches
  returned it.
- `linked_from`: up to three references of the engrams that passed it the
  most weight, strongest first. Only engrams the person can read.

Every other result has no `why`, also one that was pushed down a place
by another's lift. The connector's
`search` keeps its fixed shape and never has it. The protocol gets one
sentence that says what `why` means, so an assistant can tell the person.

### Measuring (the part of RUG-32 this needs)

Two kinds of test set, each in a public and a private form.

- **Linked questions.** The question matches engram A well. The answer is
  in engram B, which A links to and which matches the question poorly.
  The expected engram is B.
- **Follow-up questions.** Two questions asked in one session. The second
  only makes sense after the first ("and what did that cost?"). The
  expected engram for the second is the one the first was about.
- **Changes of subject**, inside the follow-up set: pairs where the
  second question is about something else. Priming must not pull the
  first subject into them. They count as questions that may not be lost.

**Public sets**, on the made-up test account, committed: at least 10
linked questions, 10 follow-up pairs and 5 changes of subject. The
account has 34 engrams and 40 wiki links to build on; a few engrams or
links may be added where the sets need them.

**Which route.** The sets are judged on search by words and meaning,
which is what production runs: locally with an embedding service, and
against the test account in production. By words alone a follow-up such
as "and what did that cost?" shares no word with its answer, so that
route cannot judge priming.

**In CI**, which has no embedding service, the sets' scores are not
checked. CI pins the stage itself: tests with made engrams and links for
each rule above (the weights, the two rounds, the replaced engram, the
lift, the recall path, both switches off), on every route.

**Private sets**, on Wouter's own engrams, outside git: 15 to 20 of each.
I draft them from his export; he checks them. They are never given to an
outside model. Only numbers go into tickets.

**The tools.** `scripts/measure_recall.py` learns to play a session: a
list of questions asked in order, where each search can be primed by the
ones before it. It needs no MCP session and no search log for that: the
search function takes the earlier results as an argument. It reports, per
set and per setting, how many answers are in the first five, and the
median time.

**The rule for judging** (the memory engine design asks for three points;
on sets this small a point is less than a question):

- A mechanism passes when its own set gains at least 2 questions in the
  first five, and no other set loses any: the two public sets, the two
  private sets, the existing 40 engram and 20 file questions, and the
  benchmark slice in CI.
- A mechanism that does not pass stays off and is reported as such. If it
  still does not pass after one round of tuning the lift, it is removed.

### Switching on

Not part of this build, and written here so it is not forgotten:

- The result of the measuring goes to Wouter with a recommendation per
  mechanism. Switching on in production is his decision (RUG-38).
- The privacy page says today that searches are kept "to measure how well
  search works and to improve it". Before priming goes on, it also says
  that a session's earlier searches help rank the next one, and that
  nothing of that lasts beyond the session.

## Who does what

- **The builder** (an Astra worker): the stage, the settings, the recall
  path, the protocol sentence, the session mode of the measuring script,
  the public sets, and the tests. The build ends there, with both
  switches off.
- **Me:** drafting the private sets from Wouter's export, running the
  measuring locally and against production's test account, and the
  report with a recommendation per mechanism.
- **Wouter:** checking the private sets, and every decision to switch a
  mechanism on or to remove it.

## Left to the builder

- Where exactly the stage sits in each of the three routes, and whether
  the words-only route keeps its single statement when both switches are
  off (it must: goal 5).
- How the links between candidates and their neighbours are read: one
  query, as the person, bounded.
- The shape of the argument through which the measuring script passes
  earlier results.
- Names of settings beyond the three above, and the limits (3 per search,
  10 engrams, 60 minutes, 20 sources) as settings or constants.
- The public questions themselves.

## Open

- Whether a lift of 0.3 can show anything when `recall` returns five
  results. The measuring answers it.
- Whether priming should also read what `brief` and the connector's
  `search` returned. Starting point: every search that is logged counts.
