# Reading the text inside files (RUG-37)

Date: 21 November 2026. Status: approved by Wouter on 21 November 2026,
including the five calls under "Approved calls".

## Why

Reef finds a file only by the words of its filename and of the description
an assistant wrote for it (`search.py`). What a letter, a policy or a plan
says is invisible to `recall`. Production holds 638 stored files: 264 PDFs,
187 Markdown or HTML files, 39 Office files, and images and other formats.
Wouter's memory holds 32 engrams and about 445 of those files, so most of
what he keeps is out of reach of search. He wants to store a PDF or a photo
of a letter and find passages inside it as he finds them inside engrams:
medical letters, insurance policies, house plans.

Every stored file already has a description, written when it was stored (60
to 270 characters on average). Search matches only its words, not its
meaning.

## Decided

By Wouter, on 20 and 21 November 2026. Binding.

1. **What people get.** `recall` finds passages inside a stored PDF or photo
   as inside an engram. `open` returns the text Reef read, with the file.
2. **Text PDFs** are read by Reef itself: the text layer, no model, no OCR.
3. **Every scan is read locally first**, by Tesseract (Dutch, French,
   English) on Reef's own servers, whatever the switches say.
4. **An outside reader behind two switches, both off by default.** Layer 1,
   the deployment: for reefwith.me the **Gemini API, paid tier**, chosen on
   21 November 2026 over Vertex AI. Accepted: processing may happen outside
   the EU; Google keeps prompts and outputs up to 55 days for abuse
   monitoring, and authorised staff may read flagged content; no training
   (research 1.4). Never a free-tier key (research 1.5). The AI Studio
   project's own logging is off. Layer 2, each space: the owner may switch on
   "Let Google Gemini read scans in this space". Each new scan there is then
   read by Gemini once. Switching on offers once to read the scans already
   there. Switching off keeps text already read and offers a separate delete.
5. **Only scans ever go out.** Text PDFs, vectors, search and reranking stay
   local.
6. **One reader interface**, open to another provider (Mistral OCR, research
   3.2) and to dreaming (RUG-26, RUG-28, RUG-39).
7. **The privacy page** names Google, the Gemini API, processing outside the
   EU, the 55 days, and that it applies only where an owner switched it on.
8. **A scan test first:** 3 to 5 of Wouter's scans, local first, Gemini only
   with his OK. Its phase 1 part is waived: see 12.
9. **Rollout:** local reading first; the Gemini layer second, after Wouter
   creates the Google account and key himself. Reef's maintainers never
   handle the key's value.
10. **Every file's description is embedded locally in phase 1.** Each file
    gets a vector from the local embedding model, so every file, photos and
    plans included, is found by meaning. No new model. Image vectors (as
    CLIP makes them) stay out of scope. In phase 2, Gemini may write a
    better description for an image page, only in a switched-on space.
11. **Phase 1 also reads text formats locally:** Markdown, plain text, HTML
    (text only, no scripts or styles), Word (`.docx`), Excel (`.xlsx`, cell
    text per sheet) and PowerPoint (`.pptx`, slide text), next to text PDFs
    and scans. Most of Wouter's file text is in these formats. Reef uses
    standard, permissively licensed Python libraries and no model (see "How
    a file is read"). Other formats get their description embedded and
    nothing more in phase 1.
12. **The scan test's phase 1 part is waived** (Wouter, 21 November 2026:
    "let's skip that, I trust it"). The first local measurement below is
    phase 1's evidence of quality. The 10-seconds-per-page check on the
    Railway worker stays, before switch-on. The Gemini part of the test
    stays a condition for phase 2 unless Wouter waives it too.

## Approved calls

Approved by Wouter on 21 November 2026 with the rest of this spec. My calls
with the most weight, each argued below:

1. **One text per page.** Gemini's reading replaces the local one when it
   answers cleanly; otherwise the local one stays. Not both.
2. **What may go out:** only an image page in which the local reading found
   words. A photo without writing never leaves. A PDF page with any text
   layer is a text page, also when a scanner app made that layer, so it is
   never sent and never read by OCR.
3. **The switch is a row in an owner-only table**, naming the reader. It
   counts only while the person who switched it on owns the space: a new
   owner starts with it off, and consent for Google never carries over to
   another provider.
4. **The worker gets a read-only storage key.** Today it holds none (runbook,
   "The worker"); it must now read file bytes.
5. **`open` returns the whole text up to the engram cap** (200,000
   characters). A longer file is cut at a page boundary; a reference may end
   in `#page=N` to read from there. No new parameter.

## Goals

1. Search inside every PDF, image and text document (decision 11) a person
   stores, by words and by meaning, ranked with engram sections.
2. A `recall` hit on a file carries a passage from inside it, and its page.
3. Every file is found by meaning, through its description at least, also
   when Reef cannot read it.
4. The 638 existing files are read locally and their descriptions embedded,
   without anyone asking.
5. Scans leave Reef through one checked gate, only where both switches allow.

## Non-goals

- Quality guarantees for handwriting or drawings. Tesseract reads the labels
  on a plan, not the plan (research 4.1).
- Tables rebuilt as tables; editing a file's text; showing it on the website.
- Describing what a photo shows, and image vectors. Reef reads writing; a
  photo is found by its description.
- Formats outside decision 11 in phase 1: PostScript, Photoshop, SVG, XML,
  SQL and unknown binaries get their description embedded and nothing more.
  The `<text>` of an SVG and the text of XML are candidates for later.
- E-mail, audio, speaker notes in slides; images in engrams that are not
  stored files.
- French stemming. Search indexes English and Dutch (`SEARCH_CONFIGS`);
  French text is found by exact words and by meaning.
- Files in `brief`, and a second outside provider.

## Anti-goals

This fails, even if it works, when:

- a page from a switched-off space, any page with a text layer, or any text
  of a text format reaches Google;
- file text, a filename, a description or a passage reaches Logfire or the
  app log;
- someone reads text from a file they could not open;
- Reef sends to Google without the operator's paid-tier statement;
- a hostile file stops the worker or reads its secrets;
- engram questions are found less often than today.

## Where the text lives

| | Approach | Trade-off |
|---|---|---|
| A | One text column on `attachments`, in its search vector | Least code; no page numbers, no reader per page, huge rows, a long file cannot be read in parts |
| **B, chosen** | **One row per page; chunk vectors in their own table** | Page numbers in passages, a reader per page, long files read in batches |
| C | A generated engram per file | Reuses everything, but files would appear in the index, history, links and `share` |

My call: B. Files are not engrams (glossary: "file", "chunk").

| New table | Holds | Visible to |
|---|---|---|
| `file_pages` | Per page: file, number, text, reader (`pdf-text`, `tesseract`, one per text format: `markdown`, `plain`, `html`, `docx`, `xlsx`, `pptx`; `gemini` in phase 2), model, when read, and a generated English and Dutch search vector | Exactly who can see the file. Written with write access to its space |
| `file_embeddings` | One vector per chunk and model, with a digest, as `engram_embeddings` | As above |
| `space_scan_readers` | Per space: the reader switched on, by whom, when | Every member reads. Only the owner writes, enforced by row security |
| `reading_spend` | Per person, day and reader: pages, input and output tokens | The person, as `embedding_spend` |

`attachments` gains `text_status` (`waiting`, `read`, `partly_read`,
`unreadable`, `not_read`; NULL means never looked at), `pages`, `text_error`
(an error class) and, in phase 2, `outside_read_at`. Phase 1 creates
`file_pages`, `file_embeddings` and `reading_spend`; phase 2 creates
`space_scan_readers`.

**What a page is.** A PDF page, an image or one frame of a TIFF, a slide or
a sheet. Markdown, plain text, HTML and Word have no pages of their own:
their text is cut between paragraphs into parts of at most 20,000
characters, and each part is a page. A sheet longer than that goes on over
the next pages. Tools say "page" for all of them.

The switch is not a column on `spaces`: that table's update policy admits
every member, narrowed to the `version` column by a grant (`rls.py`,
`identity_grant_statements`). A table of its own makes "owner only" a plain
policy.

**Chunks** follow `sections.split`: one chunk for the whole file (filename,
description, start of the text), then each page cut between paragraphs at
`REEF_EMBEDDING_PIECE_MAX_CHARS`. A very short page joins the one before.
Each chunk starts with filename, description and "page N", as an engram's
sections start with title and headings. Embedding reuses the engram path:
same service, digests, daily cap, spend table and retries.

**Every file has a vector** (decision 10). A file without text, because it
is a photo without writing, a format Reef does not read, or a file it could
not read, has the whole-file chunk only: its filename and description. So
all 638 files are found by meaning from phase 1 on.

## How a file is read

- **The type** comes from the bytes, not from the MIME type the caller gave.
  Text formats carry no signature in their bytes: Reef checks that the bytes
  are text, and the filename's extension then says which format.
- **PDF:** a page with almost no text in its layer is a **scan page**,
  rendered at 200 dpi (see "First local measurement"). Any other page is a
  **text page**. Mixed PDFs are handled page by page. An encrypted PDF is
  `unreadable`.
- **JPEG, PNG, WebP, TIFF:** each image or frame is a scan page, turned
  upright from its EXIF data.
- **Text formats** (decision 11), each by its reader:

  | Format | What Reef keeps | Read with |
  |---|---|---|
  | Markdown, plain text | The text as written: UTF-8, else Windows-1252 | Python itself |
  | HTML | The visible text; no scripts, styles or markup | Python's `html.parser` |
  | Word (`.docx`) | Paragraphs and table cells, in order | `python-docx` |
  | Excel (`.xlsx`) | Cell values per sheet, the sheet's name first; stored values, not formulas | `openpyxl`, read-only, with `defusedxml` |
  | PowerPoint (`.pptx`) | The text on each slide | `python-pptx` |

- **Anything else** (PostScript, Photoshop, SVG, XML, SQL, unknown binaries)
  is `not_read`. It is found by name and description, by words and, from
  phase 1, by meaning.
- **Local OCR:** Tesseract 5, `nld+fra+eng`. A page with no words, or only
  low-confidence noise, is stored with empty text: not read again, no junk
  in search.

**Licences.** Every library is permissive and compatible with Reef's
AGPL-3.0-or-later: `pypdfium2` (Apache-2.0 or BSD-3-Clause; PDFium itself
BSD-3-Clause), `Pillow` (MIT-CMU), `python-docx`, `openpyxl` and
`python-pptx` (MIT), `lxml` (BSD-3-Clause, needed by `python-docx` and
`python-pptx`), `defusedxml` (PSF-2.0), and Tesseract with its language
data (Apache-2.0). None is a model.

### Readers and the gate

`reef/reading/` holds one small interface. A reader takes one scan page, as
an image, and returns its text, a confidence if it has one, and its token
use. It declares whether the page leaves Reef. `tesseract` ships in phase 1
and `gemini` in phase 2. Text pages and text formats are read by Reef's own
code (`pdf-text` and one reader per text format); they never go out, so
they need no gate.

**The gate** is one function in front of every outside reader. At the moment
of sending it checks: layer 1 is set up; the space's switch is on for this
reader, set by its current owner; the page is a scan page with words; the
person's daily allowance has room; the breaker is not resting. Readers never
check switches themselves, and nothing calls an outside reader around the
gate. Mistral or dreaming plug in behind it.

### The outside reader

- **Sent:** one page image, re-encoded so no EXIF or GPS data travels, and a
  fixed instruction: transcribe exactly, in reading order, do not summarise,
  translate or correct, mark unreadable words. No filename, description,
  space, person or other page.
- **Model:** `gemini-3.5-flash-lite` by default, the research's first choice
  (about $0.34 per 100 pages, research 1.3). The scan test may choose a
  Flash model. Thinking off; an output cap per page.
- **Once:** `outside_read_at` marks the file. Deleting Google's text clears
  the mark.
- **Image descriptions:** in a switched-on space, Gemini may also write a
  better description of an image page (decision 10). Phase 2 designs this.

### Which text Reef keeps

Decision 3 settles "after": the local reading always comes first. My call
for what follows: **one text per page.** Gemini's text replaces the local
text when its answer finished normally and is not empty. Otherwise the local
text stays. Each page records its reader.

- **Search:** two readings of a page are near-duplicates. They double the
  vectors and compete for one passage, with no new meaning.
- **`open`:** one text per page, with its reader named, is what a person can
  check against the original.
- **Deletion:** deleting Google's text sends the page back to local reading,
  seconds of work. Nothing needs reconciling.
- **Risk:** a language model can invent plausible text; Tesseract garbles but
  does not invent. The scan test fails the model on any invention.

## Behaviour per tool

**`store_file`** queues the local reading at once. It answers as today,
plus `text` (`waiting`, or `not_read`), and `scans_read_by: "Google Gemini"`
in a switched-on space.

**`recall`** ranks file chunks with engram sections. A file hit carries
`passage`, the chunk closest to the question (at most 1,500 characters, as
for engrams), or without vectors the best page's keyword snippet, or without
text the description snippet as today; `page`, or None; and `ref`, ending in
`#page=N` when there is a page.

**`open`** on a file adds `text_status`, `pages`, `read_by` (plain names:
`["Reef", "Google Gemini"]`) and `text`, every page marked with its number.
Text past the engram cap (`REEF_ENGRAM_MAX_CHARS`) is cut at a page, and
`text_cut_after_page` says where. With `#page=N`, a long file is read from
page N; a file that fits comes whole whatever the anchor.

**`load_index`** keeps its file counts per space and per engram. It adds
`scans_read_by` to a space whose switch is on, so an assistant can tell the
person before storing a scan there. Nothing per file.

**`forget`** on a file says the text goes too. If Google read it, it adds
that Google may keep a copy up to 55 days, beyond Reef's reach.

**`fetch`** returns description plus text, cut at the same cap. **`search`**
and **`brief`** are unchanged. **`protocol.md`** explains pages, `#page=N`
and `scans_read_by`.

## Search

- **By words:** a file matches on filename, description or any page, and
  counts once, by its best match. Its snippet comes from the best page.
- **By meaning:** file chunks join the ranking by meaning. A file counts by
  its closest chunk, as an engram by its closest section. `fuse` takes kind
  and id from both rankings; fusion is otherwise unchanged. Every file has
  at least its whole-file chunk (decision 10), so a photo or a plan is found
  by the meaning of its description.
- **Sureness stays as measured:** a file is sure only in a short query.
  Credit for files in whole questions dropped engram finding from 95.0% to
  82.5% on 19 November 2026 (`search.py`). Text inside files makes the rule
  matter more.
- **Reranker:** a file gives its best chunks, as an engram its best
  sections.
- No GIN index and no vector index, as today. File chunks may add thousands
  of vectors to the exact scan (my estimate); the guards measure it.
- Without an embedding service, file text is found by words.

## Worker jobs

A job names things, never carries them (`jobs.py`). Progress is read from
`file_pages`, so no job stores a page offset. No job kind contains `text`,
`filename` or `description`: Logfire redacts any value that contains a
scrubbed word (see "Logging").

| Kind | Subject | What it does |
|---|---|---|
| `read_file` | file ids | Reads up to 10 pages locally, then queues itself for the rest. A text format is read whole in one job. A file Reef does not read is marked `not_read`. When done: sets `text_status`, queues `embed_file`, and in phase 2 `read_file_outside` if the gate could pass |
| `read_file_outside` | file ids | Phase 2. Sends up to 10 scan pages through the gate, keeps the better text, records spend, queues `embed_file` |
| `embed_file` | file ids | Embeds changed chunks, as for engrams. A file without text gets its whole-file chunk: filename and description |
| `backfill_files` | whole person | Queues `read_file` for never-read files and `embed_file` for files without a current whole-file vector, in the person's writable spaces. Asked for on index load, as the embedding backfill is |

A job runs as the person whose act queued it (uploader, index loader, or the
owner who accepted the switch-on offer) and touches only files that person
may write.

| Limit | Default |
|---|---|
| Pages read per file | 200; the rest is not read (`partly_read`) |
| Pages per job | 10, so transactions stay short and other jobs interleave; a text format whole |
| Time per page | 60 s local, 120 s outside; a text format 60 s in all |
| Text per page | 20,000 characters; also the size of a text format's parts |
| Pages per person per day | 2,000 local; 300 outside, about $1 (research 1.3) |
| Bytes | the existing 25 MB file cap |

- **Failures.** A file that cannot be parsed or runs out of time is
  `unreadable`, with its error class, and not retried. A full allowance or a
  failing outside reader requeues the file later and ends the job normally,
  so spend is committed (as `_embed_engram` does). After 5 failed outside
  attempts the page keeps its local text and is not sent again. Anything
  else falls to the queue's retries.
- **Breaker.** One `Breaker` (`breaker.py`) in the worker for the outside
  reader: 3 failures in a row rest it 15 minutes. Timeouts, 5xx, 429, 401 and
  403 count; a refusal (400, 413, 422) or a blocked answer counts against the
  page only.
- **Costs.** `reading_spend` per person; the same counts per job to Logfire,
  without the person. File chunks, description chunks included, count
  against the embedding cap (`REEF_EMBEDDING_DAILY_CAP`, 2,000 pieces per
  person per day), so a large account is embedded over a few days. In phase
  2 the operator sets a budget alert and a daily quota on the Google project.
- **Backfill.** The 638 files are read locally only, and each gets its
  description vector. Old scans go out only through the switch-on offer.
  Before switch-on, a script prints counts by type and pages for Wouter's
  account, no names.

## The space switch

- **Where:** the space's settings on the website (the members sheet), shown
  only when layer 1 is set up. Every member sees its state, who switched it
  on and when. Only the owner changes it; in a personal space, the person.
- **On:** a confirmation says what goes (photos and scanned pages, never text
  PDFs), to whom, where (possibly outside the EU), how long (55 days) and
  that Google does not train on it. Then one offer: "Also read the N scans
  already here (about P pages)?"
- **Off:** text already read stays. A separate action, "Delete the text
  Google read in this space", deletes it and requeues those pages for local
  reading. Google's own copy may remain up to 55 days; the dialog says so.
- **New owner:** off, with no change to `reef_transfer_space_ownership`.
- **Audit** (`audit.py`): `space.scan_reader_on`, `space.scan_reader_off`,
  `space.scan_text_deleted`, with actor, space id, reader and counts. Each is
  one person deciding what leaves Reef for every member, so the record must
  outlive the space.
- **Export:** the space manifest gets `scans_read_by` and since when.

## Cross-cutting rules

**Logging.** May be logged: job kind, page counts per reader and outcome,
page numbers, durations, PDF or image, error class and HTTP status
(`breaker.describe`), token counts, breaker state. Never: text, passages,
filenames, descriptions, space names, the person, the prompt, Google's
answer or error body, the key. `filename`, `description` and `text` join
`SCRUB_PATTERNS`. Logfire redacts every value that contains one of these
words, not only keys, so log lines, job kinds and span values avoid them.
The key goes in the `x-goog-api-key` header, never a URL,
because httpx logs URLs; proxies are ignored (`trust_env=False`), as for the
embedder. A test reads a file with a marker word through every reader and
finds the marker in no log record or span.

**Untrusted files.** Parsing happens only in the worker. Each file is read
in a child process with an empty environment, an unprivileged user, and CPU,
memory and time limits: bytes in on stdin, text out on stdout. A hostile file
costs one job and cannot read the worker's credentials. Image size is capped
before decoding. An Office file is a ZIP archive: its unpacked size and
number of entries are capped before parsing, and its XML is read without
entities or network access. `OMP_THREAD_LIMIT=1`, because OpenMP threads can
stall each other on Railway's 96-core hosts, as llama.cpp's did (runbook).

**Deletion.** `forget` deletes the row with its pages and vectors, then the
bytes, as today. Space and account deletion take pages and vectors by
cascade, as engram vectors go; `reading_spend` goes with the person, the
switch with the space. A test covers each path. A job for a deleted file
finds nothing and ends.

**Export.** The full dump puts each file's text beside it as
`<filename>.txt`, pages marked; `files.json` gains status, pages and readers.
Vectors stay out, as today. Smaller exports carry the status only.

**Secrets and the free tier.**

- `REEF_SCAN_READER=gemini` on app and worker sets up layer 1; the app only
  shows the switch. `REEF_GEMINI_API_KEY` is on `reef-worker` only, pasted
  into Railway by Wouter.
- I know of no API call that tells a billed key from a free one, and the
  research names none. So layer 1 stays off unless the operator also sets
  `REEF_GEMINI_PAID_TIER=yes`. If the builder finds a machine check, the
  worker runs it at start and refuses on failure.
- The runbook covers: confirming billing in AI Studio; switching off the
  project's request logging (on by default for projects created on or after
  26 October 2026, research 1.4); a budget alert and a daily quota.
- The worker gets a read-only R2 token, never the app's read-write keys.

**Docker image and CPU.** Debian's `tesseract-ocr` (with English and
orientation data), `tesseract-ocr-nld`, `tesseract-ocr-fra`; the Python
libraries under "Licences"; an unprivileged `reader` user for the child
process. About 60 to 100 MB more (my estimate). App and worker share the
image; only the worker runs it. On Wouter's laptop, Tesseract took 6 seconds
per page at 200 dpi in three languages (see "First local measurement"). The
10-second bar is checked on the Railway worker before switch-on.

## Privacy page

- **Phase 1:** "Where it lives" adds that text read from files is kept in
  the database with the engrams (Railway, United States). The files stay in
  Cloudflare R2, in the EU. "Search vectors" adds that the same two models
  on Reef's own servers also read file descriptions and the text read from
  files.
- **Phase 2:** a section on scans, per decision 7. "Neither sends your text
  to another company" gains its one exception.

## Rollout

1. **Phase 1, local reading and description vectors,** ships dark behind
   `REEF_FILE_TEXT`. Its switch-on ticket: the read-only storage key on the
   worker, the 10-second check on the Railway worker, the phase 1 privacy
   line, the backfill on Wouter's account first, the measures below, then
   everyone.
2. **The scan test.** The local part is waived (decision 12). The Gemini
   part runs before phase 2 is built, only with Wouter's OK, with his key,
   on his machine.
3. **Phase 2, the Gemini layer,** is built once the test passes. Its
   switch-on ticket: the privacy section live, layer 1 on, Wouter's spaces
   on, a shared space only after its owner has told its members. Reef sends
   no notices; the website shows the state to every member.

## Measuring it

Scans, questions and outputs stay in `~/.reef-eval-private`, never in git, a
ticket or a pull request. Reports carry numbers only.

**The scan test (decision 8).**

- **Set:** a medical letter, a multi-page insurance policy, a house plan, and
  if possible a phone photo of a letter. For each, Wouter writes down 5 facts
  he would ask about: a date, an amount, a name, a number, a room size.
- **Found** means the fact is in the text, ignoring case, spaces and line
  breaks; numbers match exactly.
- **Local, for phase 1: waived** by Wouter on 21 November 2026 (decision
  12). It would have asked for 80% of the facts in the letter and the
  policy. The first local measurement stands as the evidence instead. What
  remains is speed: under 10 seconds per page on the Railway worker,
  measured before switch-on.
- **Gemini is good enough for phase 2** at 95% of all facts, plan labels
  included, with **nothing invented**: no name, number or date that is on no
  page. One invention fails the model; the next try uses a Flash model or a
  stricter instruction. Cost per page stays within twice research 1.3's
  estimate. This part stays a condition for phase 2 unless Wouter waives it.
- The Gemini run also reports how many of the facts the local reading
  found, so Wouter can decide whether phase 2 is still worth it.

**Search inside files.** 20 questions over Wouter's files whose answer is
only in the text, not in the filename or description; today none is found.
Scans, text PDFs and text formats all count. After phase 1, the file is in
`recall`'s first five for at least 80%, and the passage shows the answer for
at least 60%.

**Guards.**

- Engram questions in the private set stay at 95% or more in the first five,
  measured on production after Wouter's backfill, when his file chunks
  compete with his engrams.
- The benchmark slice test passes.
- Median `recall` time on production rises by at most 100 ms after the
  backfill.
- Phase 1 sends nothing out: a test fails if reading a file calls anything
  but the object store, and embedding anything but the embedding service.
- Phase 2: every switched-off and text-page case runs against a fake outside
  reader that fails the test when called.

## First local measurement (21 November 2026)

Tesseract 5.5.4 on Wouter's laptop (14 cores), on his three insurance scans: 35 pages, 12,800 words. Only counts were looked at, never the text.

- **Accuracy.** The median word confidence was 96 on all three scans. 3 to 5% of words fell below 60. Both policy numbers in the filenames were read correctly.
- **Speed depends on the settings, not the engine.**

  | Resolution | Languages | Seconds per page | Words below 60 |
  |---|---|---|---|
  | 300 dpi | Dutch, French, English | 19.5 | 5% |
  | 300 dpi | Dutch only | 15.5 | 12% |
  | 200 dpi | Dutch only | 3 | 12% |
  | 200 dpi | Dutch, French, English | 6 | 6% |

- **Start with 200 dpi and all three languages, one thread per page.** Run pages in parallel across the worker's cores. Dutch alone loses the French and English words in Belgian policies.
- **Measure the Railway worker** before switch-on. The 10-second bar applies there.

## Left to the builder

- Thresholds, set from the test files: how little text makes a scan page,
  how many words a scan needs to go out, below what confidence a reading is
  noise.
- Tesseract settings: resolution, page segmentation, fast or best models,
  language detection first or not.
- The ranking SQL, chunk anchors, page markers in `open`.
- Gemini's instruction, media resolution and output cap. Plain httpx is
  preferred over Google's SDK: one dependency less, full control of logs.
- The child process mechanics; job kind names; batch size.
- The switch's copy and layout in the members sheet.
- HEIC, if Pillow reads it cheaply.
- Which parts of a Word file count beyond paragraphs and tables (headers,
  footers, footnotes), and the caps on an Office file's unpacked size.
