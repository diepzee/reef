# reef

**Shared, living memory for you and the people you share your life with — and
for the AI assistants you both already use.**

[![PyPI](https://img.shields.io/pypi/v/reef-cli?label=reef-cli&logo=pypi&logoColor=white)](https://pypi.org/project/reef-cli/)
[![npm](https://img.shields.io/npm/v/%40haai%2Freef-cli?label=%40haai%2Freef-cli&logo=npm)](https://www.npmjs.com/package/@haai/reef-cli)
[![Python](https://img.shields.io/badge/python-3.13%2B-3776AB?logo=python&logoColor=white)](https://pypi.org/project/reef-cli/)
[![MCP](https://img.shields.io/badge/MCP-remote%20server-1f6feb)](https://modelcontextprotocol.io)
[![Live](https://img.shields.io/website?url=https%3A%2F%2Freefwith.me&label=reefwith.me)](https://reefwith.me)

Your assistant forgets you between conversations. reef gives it a memory that
lasts — and one your partner, your household, or your project can share.

- **Ask once.** Tell your assistant the boiler is a Vaillant. Next month, in a
  different conversation, it still knows.
- **Share on purpose.** Your private notes stay private. A shared space holds
  only what you deliberately put there.
- **Bring your own assistant.** Claude, ChatGPT desktop, or Codex. reef is a
  remote [MCP](https://modelcontextprotocol.io) connector, not another chat app.

---

## Try it

You need an invitation — reef is invite-only. Once you have one:

```bash
uv tool install reef-cli   # or: npm install -g @haai/reef-cli
reef login
reef load-index
```

To use it from an assistant instead, add `https://reefwith.me/mcp` as a remote
MCP connector and sign in with the address you were invited on.

There is a browser app too, at [reefwith.me/app](https://reefwith.me/app). Use
it to read and edit pages without an assistant.

## How memory is organised

Memory lives in **spaces**. You get one private space the first time you sign
in. You can create any number of shared ones — a household, a school circle, an
accountant, a small project.

| | Private space | Shared space |
|---|---|---|
| Who can read it | Only you | Everyone invited |
| Created | At first sign-in | By whoever needs it |
| Named | Always `personal` | By you, for you |
| People join | Never | By email invitation from the owner |

**You name shared spaces for yourself.** Your name for a space is yours alone.
Two households can each have a `family` without either knowing the other
exists, and nobody can take a name from anybody else.

**Sharing is deliberate and permanent.** Moving something out of your private
space takes two steps. First your assistant shows you the exact text and names
everyone who will be able to read it. Only then does it move.

Writing private content into a shared space any other way is refused — not
discouraged, refused. And there is no un-sharing.

## How your assistant reads it

reef loads an **index first, then pages** — the
[LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f),
adapted for shared, permissioned memory.

1. The assistant calls `load_index` and gets a map of every page it may see:
   path, title, tags, and a one-line description. No page bodies.
2. It reads the map, decides what this conversation needs, and fetches only
   that with `read_pages`.
3. It fetches again as the topic moves.

The index is rebuilt from the database on every call, so it cannot fall out of
date.

## How your privacy is protected

The database enforces it, not the application code.

Every page, file, and membership is guarded by PostgreSQL
[Row-Level Security](https://www.postgresql.org/docs/current/ddl-rowsecurity.html).
A query with a forgotten filter returns **nothing** rather than somebody else's
data. Getting it wrong fails closed.

This works only because the app connects as an ordinary database role. A
superuser ignores every policy, so reef runs as `rif_app` — which can read and
write rows but cannot change the schema. Migrations and backups use a separate,
more privileged credential. See
[`scripts/provision_app_role.py`](scripts/provision_app_role.py).

## Your data stays yours

Pages are Markdown from the moment you write them. The web app exports:

- **current content**, as a Markdown archive or JSON, and
- **everything**, as one download with full revision history and your stored
  file bytes.

Both are plain files that outlive this deployment. Export is one-way, out of
reef — nothing here locks your memory in.

## Using the CLI

Two packages, both installing a `reef` command:

| | Install | What you get |
|---|---|---|
| **Python** | `uv tool install reef-cli` | Every MCP tool as a named command |
| **npm** | `npm install -g @haai/reef-cli` | `login`, `tools`, and `call` |

Named commands use hyphens: `load_index` becomes `load-index`. Every result is
JSON, and an error such as `not_found` also exits non-zero.

```bash
reef read-pages personal profile.md preferences.md
reef write-page personal plans.md --body-file ./plans.md \
  --message "Add the summer plan" --title Plans
reef add-file personal ./lease.pdf --description "Signed rental agreement"
reef call read_pages '{"space":"personal","paths":["plans.md"]}'
```

`reef call` takes any MCP tool name and a JSON object, so it reaches everything
the named commands do. Large inputs can come from a file or stdin; uploads are
encoded for you.

Both packages sign in through the same browser flow. Each caches its own token,
so logging in with one does not log in the other. Tokens live in a user-private
config file.

Set `REEF_MCP_URL` to point at another server, or `REEF_ACCESS_TOKEN` for a
headless run.

Run `reef tools` for the live schemas, and `reef <command> --help` for
arguments.

**Agent skill:** [`skills/reef/SKILL.md`](skills/reef/SKILL.md) teaches an
assistant the retrieval protocol, private-by-default writing, optimistic
locking, and when it must ask you before acting.

## Status

**Live since 6 August 2026** at [reefwith.me](https://reefwith.me), deployed on
Railway behind WorkOS AuthKit, and in daily use.

Complete and reviewed: the schema and access control, index and page reads,
versioned writes, and section-level sharing. Also multi-user spaces with
owner-managed invitations, file storage, the browser app, import, backup, and
export.

Tests run against a real PostgreSQL, not a mock.

Known gaps, honestly:

- **Backups run by hand.** One real dump exists and a restore drill passed
  against it, but the daily job is not set up yet.
- **Few people are on it.** Growth is one invitation at a time, by design.
- **The context ceiling has not been measured on a phone.**

Everything outstanding is tracked in the "Open items" list at the top of
[`docs/runbook.md`](docs/runbook.md).

## Development

You need [Docker](https://www.docker.com/) and [uv](https://docs.astral.sh/uv/).
[just](https://github.com/casey/just) is optional but does the setup for you.

```bash
just setup   # dependencies, database, and the roles the tests need
just test    # lint, Python suite, frontend suite
just dev     # serve the app locally
```

`just` on its own lists every task. Without it:

```bash
docker compose up -d              # PostgreSQL on port 5433
uv sync && (cd frontend && bun install)
uv run pytest                     # builds its own schema in rif_test
```

A cluster created before the test roles existed is missing them, and the suite
says so loudly rather than testing a weaker shape. `just db-roles` repairs it.

Two things about the local database are deliberate, not accidental:

- **It does not run the app as a superuser.** A superuser ignores Row-Level
  Security, so the security tests would pass without proving anything. See the
  comment in [`docker-compose.yml`](docker-compose.yml).
- **It creates a separate non-owner role for tests.** A table's owner is not
  bound by column grants. Without that role, a test asserting "a member cannot
  rename a space" would pass for the wrong reason.

Migrations seed one person and their spaces. Nobody else is seeded, on purpose.
An email address is the key a first sign-in binds against, and a migration does
not re-run to correct a wrong guess. Everyone else arrives by invitation.

## Documentation

| Document | What it covers |
|---|---|
| [`docs/spec.md`](docs/spec.md) | The design and why it is shaped this way |
| [`docs/runbook.md`](docs/runbook.md) | Deploying, and what is still open |
| [`docs/restore.md`](docs/restore.md) | Backup and restore |
| [`docs/competitor-research.md`](docs/competitor-research.md) | The market around it |

## A note on the name

The product is **reef**. The repository, the Python module, and the database
role are all called **rif**. That was the working name.

It stayed because renaming a live schema and a deployed service is real risk
for no reader-facing gain. If you are reading the code, `rif` is reef.
