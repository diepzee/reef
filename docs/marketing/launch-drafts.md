# Launch drafts — edit before posting

## Show HN

**Title:** Show HN: Reef – shared long-term memory for your household's AI, invite-only by design

**Body:**

I built reef because my partner and I kept telling our assistants the same
things twice. It's a remote MCP server that gives a group long-term memory:
one private cove per person, plus shared coves for any circle with a "we" —
the household, the school run, you and your accountant.

Design choices HN might find interesting:

- Memory is a wiki, not a blob: human-readable Markdown pages behind an
  index-first retrieval pattern (Karpathy's "LLM wiki" idea). You can open,
  edit, and export everything.
- Privacy is enforced in Postgres row-level security, not application code.
  The app connects as a non-superuser; a person's private cove is invisible
  to queries made on someone else's behalf.
- Sharing personal content into a shared cove is a two-step consent ceremony.
- reef is invite-only: you get in when someone already on reef invites you
  (each member can invite 5 people per 30 days). That's deliberate — memory
  this personal should arrive through trust. For the launch we've set aside a
  limited number of places you can take yourself, while they last.

Works from Claude (including the phone app), ChatGPT desktop, and Codex as a
remote MCP connector, plus a CLI (`uv tool install reef-cli` or
`npm install -g @haai/reef-cli`) and an agent skill.

Site: https://reefwith.me — Source: https://github.com/diepzee/rif

## X thread

1/ Your AI assistant forgets everything, and your partner's assistant never
knew it in the first place. reef fixes both: shared, living memory for the
people in your life — readable, editable, yours.

2/ Memory lives in coves: one private cove each, shared coves for any circle
with a "we". The household. The school run. You and your accountant.

3/ It's a wiki, not a memory blob. Real Markdown pages your assistant reads
and tends mid-conversation — and you can open, edit, and export every one.

4/ Privacy isn't a promise in app code. It's Postgres row-level security:
the database itself cannot show your private cove to anyone else's session.

5/ reef is invite-only — memory this personal should arrive through trust.
For the launch we set aside a number of places. Take one while they last:
reefwith.me
