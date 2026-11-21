# Reading the text inside files, phase 1: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reef reads the text inside stored files on its own servers (text PDFs, scans with Tesseract, Markdown, plain text, HTML, Word, Excel, PowerPoint), embeds every file's description and text locally, and `recall` finds passages inside files, all shipped dark behind `REEF_FILE_TEXT`.

**Architecture:** A worker job reads each file in a sandboxed child process and stores one row per page in `file_pages`. A second job cuts the file into chunks (a whole-file chunk of filename, description and the start of the text, then the pages) and embeds them into `file_embeddings` along the engram embedding path. Search adds pages to the ranking by words and chunks to the ranking by meaning; `recall`, `open` and `fetch` return passages, pages and text. Scan pages pass through a `ScanReader` interface: phase 2 adds a Gemini reader, its gate and `read_file_outside` behind it, while `ScanPage`, `Reading` (with token counts) and `reading_spend` (with token columns) stay as they are.

**Tech Stack:** Python 3.13, Piccolo, Postgres 18 with forced row security and pgvector, pydantic-settings, Tesseract 5 (Debian `tesseract-ocr`, `tesseract-ocr-nld`, `tesseract-ocr-fra`), `pypdfium2`, `Pillow`, `python-docx`, `openpyxl` with `defusedxml`, `python-pptx`; `pypdf` for test fixtures only. Tests via `just test-py`; CI runs `uv run pytest -q`.

**Spec:** `docs/specs/2026-11-21-file-text-design.md` (approved 21 November 2026). Research: `docs/research/2026-11-21-scan-readers.md`.

**Who runs what.** A subagent runs Tasks 1 to 11 and every step marked **(subagent)**. The controller runs every step marked **(controller)**: anything touching production, Wouter's private data (`~/.reef-eval-private`) or an external account (Railway, Cloudflare, GitHub, Linear).

## Global Constraints

- Phase 1 only. Phase 2 (Gemini, the gate, `read_file_outside`, `space_scan_readers`, `outside_read_at`, `scans_read_by`, the space switch) is out of this plan.
- "Phase 1, local reading and description vectors, ships dark behind `REEF_FILE_TEXT`." With it unset, tools, search, the index and the worker behave as today; exports may carry the new fields, empty.
- "Every scan is read locally first, by Tesseract (Dutch, French, English) on Reef's own servers." "Start with 200 dpi and all three languages, one thread per page. Run pages in parallel across the worker's cores." Nothing leaves Reef in phase 1.
- "The type comes from the bytes, not from the MIME type the caller gave. Text formats carry no signature in their bytes: Reef checks that the bytes are text, and the filename's extension then says which format."
- "A page with no words, or only low-confidence noise, is stored with empty text: not read again, no junk in search."
- "What a page is. A PDF page, an image or one frame of a TIFF, a slide or a sheet. Markdown, plain text, HTML and Word have no pages of their own: their text is cut between paragraphs into parts of at most 20,000 characters, and each part is a page. A sheet longer than that goes on over the next pages."
- Limits: "Pages read per file: 200; the rest is not read (`partly_read`)". "Pages per job: 10 ... a text format whole". "Time per page: 60 s local ... a text format 60 s in all". "Text per page: 20,000 characters". "Pages per person per day: 2,000 local". "Bytes: the existing 25 MB file cap".
- `text_status`: "`waiting`, `read`, `partly_read`, `unreadable`, `not_read`; NULL means never looked at".
- "A job names things, never carries them." "Progress is read from `file_pages`, so no job stores a page offset." "A job runs as the person whose act queued it ... and touches only files that person may write."
- "A file that cannot be parsed or runs out of time is `unreadable`, with its error class, and not retried. A full allowance ... requeues the file later and ends the job normally, so spend is committed."
- "Every file has a vector (decision 10). A file without text ... has the whole-file chunk only: its filename and description." "Embedding reuses the engram path: same service, digests, daily cap, spend table and retries."
- "Sureness stays as measured: a file is sure only in a short query." No GIN index and no vector index.
- "`open` returns the whole text up to the engram cap (200,000 characters). A longer file is cut at a page boundary; a reference may end in `#page=N` to read from there. No new parameter." A `recall` passage is "at most 1,500 characters, as for engrams".
- "`load_index` keeps its file counts per space and per engram. ... Nothing per file." Its size does not change.
- Logging: "May be logged: job kind, page counts per reader and outcome, page numbers, durations, PDF or image, error class ... Never: text, passages, filenames, descriptions, space names, the person." "`filename`, `description` and `text` join `SCRUB_PATTERNS`." No job kind, log line or span value contains `text`, `filename` or `description`: Logfire redacts any value that contains a scrubbed word.
- "Each file is read in a child process with an empty environment, an unprivileged user, and CPU, memory and time limits: bytes in on stdin, text out on stdout." "Image size is capped before decoding." Office archives: "unpacked size and number of entries are capped before parsing, and its XML is read without entities or network access." `OMP_THREAD_LIMIT=1`.
- "The worker gets a read-only R2 token, never the app's read-write keys."
- Migrations carry fixed SQL and never import `reef.rls` or `reef.frozen_ddl`; `tests/test_migration_chain.py` compares them with the schema the suite builds.
- Private eval data stays in `~/.reef-eval-private`, never in git, a ticket or a pull request. Reports carry numbers only.
- Modern Python types; docstrings in ReST, mandatory, no types in them. Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Merging to `main` deploys to production and needs Wouter's approval.

## Review Focus

1. A hostile file (a ZIP bomb `.docx`, an `.xlsx` with an entity bomb, a PNG that declares 20,000 × 20,000 pixels, a truncated PDF): that file becomes `unreadable` with its error class, and the next job runs. Tests in Task 3, Task 4 and Task 6.
2. A long PDF of text and scan pages read over several jobs, with one job retried after a crash mid-batch: every page is stored once, numbered in order. Test in Task 6.
3. A reference ending in `#page=0`, `#page=x`, `#page=`, a page past the last, or any anchor on a file that fits: a defined answer, never an error. Test in Task 8.
4. Text in Windows-1252, UTF-8 with a byte order mark, HTML with scripts, styles and entities: readable text, no script or style text. Test in Task 2.
5. A person who lost write access between queueing and running, or a viewer: the job reads nothing and ends normally; a viewer's index load queues no backfill. Test in Task 6.

---

### Task 1: Settings, tables and row security

**Files:**
- Create: `src/reef/piccolo_migrations/reef_2026_11_21t12_00_00_000000.py`
- Modify: `src/reef/config.py`, `src/reef/models.py`, `src/reef/rls.py`, `src/reef/search.py` (`search_statements`), `tests/conftest.py` (apply the new DDL; grant `reef_probe` on the three tables), `docs/runbook.md` (by `just settings-table`)
- Test: `tests/test_file_tables.py`, `tests/test_settings.py`

**Interfaces:**
- Produces:
  - `class ReadingSettings(BaseSettings)`, prefix `REEF_FILE_TEXT_`, mounted as `Settings.reading`: `enabled: bool = False` (variable `REEF_FILE_TEXT`), `dpi: int = 200`, `languages: str = "nld+fra+eng"`, `parallel_pages: int = 4`, `pages_per_file: int = 200`, `pages_per_job: int = 10`, `page_seconds: float = 60.0`, `page_chars: int = 20_000`, `daily_pages: int = 2_000`, `retry_seconds: float = 21_600.0` (wait after a spent allowance), `memory_mb: int = 1_024` (per child).
  - `class TextStatus(StrEnum)`: `WAITING`, `READ`, `PARTLY_READ`, `UNREADABLE`, `NOT_READ` with the spec's values. `Attachment` gains `text_status: str | None`, `pages: int | None`, `text_error: str | None` (at most 255 characters).
  - `rls.READERS = ("pdf-text", "tesseract", "markdown", "plain", "html", "docx", "xlsx", "pptx")` and `rls.file_text_statements() -> list[str]`, not part of `enable_statements` (for the reason `appearance_statements` gives).
  - Table `file_pages(id, attachment_id → attachments ON DELETE CASCADE, number ≥ 1, text NOT NULL DEFAULT '', reader IN READERS, model, confidence NULL or 0–100, read_at, search_vector)`, unique `(attachment_id, number)`. `search_vector` is generated by `search_statements()` from `("file_pages", "NULL", "text")`: weight B only, English and Dutch.
  - Table `file_embeddings(id, attachment_id → attachments ON DELETE CASCADE, chunk_anchor NULL, model_id, dims, content_hash, embedding, created_at)`, with the dims check and unique `(attachment_id, model_id, COALESCE(chunk_anchor, ''))`, as `engram_embeddings`.
  - Table `reading_spend(id, person_id → persons ON DELETE CASCADE, day, reader, pages ≥ 0, input_tokens ≥ 0, output_tokens ≥ 0)`, unique `(person_id, day, reader)`.
  - Policies: `file_pages` select `attachment_id IN (SELECT id FROM attachments)`; insert, update and delete `attachment_id IN (SELECT id FROM attachments WHERE space_id IN (SELECT reef_member_space_ids()))`. `file_embeddings` select, insert and delete the same way. `reading_spend_owner` on `person_id = PRINCIPAL`. Grants: `file_pages` SELECT, INSERT, UPDATE, DELETE; `file_embeddings` SELECT, INSERT, DELETE; `reading_spend` SELECT, INSERT, UPDATE, DELETE. `attachments_text_status_known` check.

- [ ] **Step 1: Write the failing tests** in `tests/test_file_tables.py`, writing as the app role under `arm`: `test_a_page_is_visible_exactly_when_its_file_is` (the partner reads no page of Wouter's personal file; both read a page of a `household` file); `test_a_viewer_cannot_write_a_page` (insert raises `asyncpg.InsufficientPrivilegeError`); `test_a_chunk_vector_is_visible_exactly_when_its_file_is`; `test_reading_spend_is_private_to_the_person`; `test_one_row_per_page` (second insert of number 1 raises `UniqueViolationError`); `test_an_unknown_reader_or_status_is_refused` (`CheckViolationError`); `test_a_page_is_found_by_its_words` (`search_vector @@ websearch_to_tsquery('dutch', 'polisnummers')` matches a page holding "polisnummer"); `test_deleting_a_file_deletes_its_pages_and_vectors`. In `tests/test_settings.py`: `test_file_text_is_off_by_default` (`get_settings().reading.enabled is False`) and a row `("REEF_FILE_TEXT", "1", lambda s: s.reading.enabled is True)` in `test_a_variable_reaches_its_setting`.
- [ ] **Step 2: Run** `just test-py tests/test_file_tables.py tests/test_settings.py`. Expected: FAIL (no `reading` group, no tables).
- [ ] **Step 3: Implement** the settings group, models (add the three tables to `TABLES`), `file_text_statements()`, the `search_statements()` entry, and the conftest changes. Write the migration: `CREATE TABLE IF NOT EXISTS` for the three tables, with column types exactly as Piccolo creates them from the models (the chain test compares them), `ALTER TABLE attachments ADD COLUMN IF NOT EXISTS` for the three columns, grants per role as in `reef_2026_11_20t12_00_00_000000.py`, then the statements printed once from `file_text_statements()` and the `file_pages` line of `search_statements()`, pasted as fixed text.
- [ ] **Step 4: Run** `just settings-table`, then `just test-py tests/test_file_tables.py tests/test_settings.py tests/test_migration_chain.py tests/test_schema.py`. Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat(files): tables for the text read from files, under row security (RUG-37)"`

### Task 2: The sandbox, file kinds, and Markdown, text and HTML

**Files:**
- Create: `src/reef/reading/__init__.py`, `src/reef/reading/kinds.py`, `src/reef/reading/formats.py`, `src/reef/reading/sandbox.py`, `src/reef/reading/child.py`, `tests/filefixtures.py` (generators for every test file; nothing binary in git)
- Modify: `src/reef/sections.py` (`cut(text: str, limit: int) -> list[str]` made public; `_parts` calls it)
- Test: `tests/test_reading_kinds.py`, `tests/test_reading_formats.py`, `tests/test_reading_sandbox.py`

**Interfaces:**
- Produces, in `reef.reading`:
  - `class FileKind(StrEnum)`: `PDF = "pdf"`, `IMAGE = "image"`, `MARKDOWN = "markdown"`, `PLAIN = "plain"`, `HTML = "html"`, `DOCX = "docx"`, `XLSX = "xlsx"`, `PPTX = "pptx"`, `OTHER = "other"`; `TEXT_FORMATS: frozenset[FileKind]` (the six text formats).
  - `@dataclass(frozen=True) class ScanPage: number: int; image: bytes; source: Literal["pdf", "image"]` (`image` is a PNG, upright, without metadata).
  - `@dataclass(frozen=True) class Reading: text: str; reader: str; model: str; confidence: float | None = None; input_tokens: int = 0; output_tokens: int = 0`.
  - `class ScanReader(Protocol): name: str; leaves_reef: bool; async def read(self, page: ScanPage) -> Reading`.
  - `READER_NAMES: dict[str, str]`: every value of `rls.READERS` → `"Reef"`.
  - Errors whose class name is stored in `text_error`: `class ReadFailed(Exception)` with subclasses `Unreadable`, `Encrypted`, `TooLarge`, `ReadTimedOut`, `ChildCrashed`. `class SandboxUnavailable(Exception)` is not a `ReadFailed`: it fails the job, so the queue retries it.
  - `kinds.likely_kind(head: bytes, filename: str) -> FileKind`: pure, reads at most the first 8,192 bytes. `%PDF-` in the first 1,024 bytes → PDF; JPEG, PNG, WebP or TIFF signature → IMAGE; `PK\x03\x04` with suffix `.docx`, `.xlsx` or `.pptx` → that kind; text bytes (no NUL, no control bytes but tab, CR, LF, form feed) not starting with `%!PS`, `<?xml` or `<svg` → by suffix: `.md`/`.markdown` MARKDOWN, `.txt`/`.text` PLAIN (HTML when it starts with `<!doctype html` or `<html`), `.html`/`.htm` HTML; everything else OTHER.
  - `formats.decode_text(data: bytes) -> str` (UTF-8 without BOM, else Windows-1252 with replacement), `formats.html_text(data: bytes) -> str` (`html.parser`, `convert_charrefs=True`; skips `script`, `style`, `template`, `noscript`; block elements end a line), `formats.text_parts(data: bytes, kind: FileKind, part_chars: int) -> list[str]` (decoded text cut with `sections.cut`).
  - `sandbox.child_command() -> list[str]` = `[sys.executable, "-I", "-B", "-m", "reef.reading.child"]`; `sandbox.child_environment() -> dict[str, str]` = exactly `{"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "OMP_THREAD_LIMIT": "1"}`; `sandbox.child_user() -> str | None` (`"reader"` when the worker runs as root, None otherwise; raises `SandboxUnavailable` when root and `reader` does not exist); `async def sandbox.run_child(args: list[str], data: bytes, *, seconds: float) -> dict` (puts `--memory-mb <reading.memory_mb> --cpu-seconds <ceil(seconds)>` before `args`; stdin `data`, stderr discarded, new session; kills the process group and raises `ReadTimedOut` past `seconds`; a child's `{"error": "<class>"}` re-raises that `ReadFailed` class; anything else that is not JSON raises `ChildCrashed`; no message ever carries child output).
  - `child.apply_limits(memory_mb: int, cpu_seconds: int) -> None` (`RLIMIT_AS` on Linux only, `RLIMIT_CPU`, `RLIMIT_NOFILE` 64, `RLIMIT_CORE` 0); `child.main(argv: list[str]) -> int`, argv `--memory-mb N --cpu-seconds S <op> ...`. Op here: `parts <kind> <part_chars>` → `{"parts": [...]}`.

- [ ] **Step 1: Write the failing tests.** `test_the_kind_comes_from_the_bytes`, parametrised: `(%PDF bytes, "x.txt") → PDF`, `(JPEG bytes, "scan.pdf") → IMAGE`, `(b"# Title\n", "notes.md") → MARKDOWN`, `(b"hello", "a.txt") → PLAIN`, `(b"<!doctype html><p>x", "page.txt") → HTML`, `(b"<svg", "logo.svg") → OTHER`, `(b"<?xml", "data.xml") → OTHER`, `(b"%!PS-Adobe", "x.ps") → OTHER`, `(b"8BPS", "x.psd") → OTHER`, `(b"SELECT 1;", "q.sql") → OTHER`, `(b"\x00\x01", "x.txt") → OTHER`, `(b"PK\x03\x04", "a.docx") → DOCX`, `(b"PK\x03\x04", "a.zip") → OTHER`. Formats: `test_markdown_and_plain_text_are_kept_as_written`; `test_windows_1252_text_is_decoded` (`"café ë".encode("cp1252")` → `"café ë"`); `test_a_byte_order_mark_is_dropped`; `test_html_keeps_visible_text_only` (marker `ZEBRAQUILT` inside `<script>` and `<style>` absent; `&amp;` → `&`; two `<p>` on two lines); `test_long_text_is_cut_between_paragraphs_into_parts` (45 paragraphs of 1,000 characters at 20,000 → 3 parts, each ≤ 20,000, no paragraph lost). Sandbox: `test_the_child_gets_no_variable_of_the_worker` (with `REEF_S3_SECRET_KEY=canary` set, the captured `env` of `asyncio.create_subprocess_exec` equals `child_environment()`); `test_the_child_runs_as_reader_when_the_worker_is_root` (`os.geteuid` → 0: `"reader"`; user missing: `SandboxUnavailable`); `test_a_child_that_runs_too_long_is_stopped` (`child_command` patched to `sleep(5)`, `seconds=0.5` → `ReadTimedOut` within 2 s, process gone); `test_a_crashing_child_is_reported_by_class_only` (child writes "secret" to stderr and exits 3 → `ChildCrashed`, "secret" not in `str(error)`); `test_a_markdown_file_is_read_in_the_child` (real `run_child`); `test_the_child_limits_itself` (Linux only: `RLIMIT_AS` reads back 64 MiB after `apply_limits(64, 5)` in a subprocess).
- [ ] **Step 2: Run** `just test-py tests/test_reading_kinds.py tests/test_reading_formats.py tests/test_reading_sandbox.py`. Expected: FAIL (no `reef.reading`).
- [ ] **Step 3: Implement** the package as the Interfaces say.
- [ ] **Step 4: Run** the same command plus `tests/test_sections.py`. Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat(files): read Markdown, text and HTML in a sandboxed child (RUG-37)"`

### Task 3: Word, Excel and PowerPoint

**Files:**
- Modify: `pyproject.toml` and `uv.lock` (`uv add python-docx openpyxl python-pptx defusedxml`), `src/reef/reading/formats.py`, `tests/filefixtures.py` (`docx_bytes`, `xlsx_bytes`, `pptx_bytes`, built with the same libraries)
- Test: `tests/test_reading_formats.py`

**Interfaces:**
- Consumes: Task 2's `text_parts`, `FileKind`, errors.
- Produces: `formats.ZIP_MAX_BYTES = 200_000_000`, `formats.ZIP_MAX_ENTRIES = 10_000`; `formats.check_archive(data: bytes, kind: FileKind, *, max_bytes: int = ZIP_MAX_BYTES, max_entries: int = ZIP_MAX_ENTRIES) -> None` (from the central directory only: `TooLarge` past either cap; `Unreadable` when not a ZIP or the main part is missing: `word/document.xml`, `xl/workbook.xml`, `ppt/presentation.xml`). `text_parts` handles DOCX (paragraphs and table cells in body order, cells of a row joined by ` | `, then cut at `part_chars`), XLSX (`load_workbook(read_only=True, data_only=True)`; per sheet the title on the first line, then each row's non-empty values joined by ` | `; one sheet per page, a longer sheet goes on over the next pages) and PPTX (per slide the text of each shape and table cell in shape order; one slide per page).

- [ ] **Step 1: Write the failing tests:** `test_word_keeps_paragraphs_and_table_cells_in_order`; `test_excel_gives_one_page_per_sheet_with_its_name_first` (sheets "Budget" and "Leningen", values `1250.5` and `"Polis 4711"`; a cell `=A1*2` never yields the text `=A1*2`); `test_powerpoint_gives_one_page_per_slide` (3 slides, the second empty → 3 parts, part 2 `""`); `test_a_zip_bomb_is_refused_before_parsing` (a 2 MB entry of zeros with `max_bytes=1_000_000` → `TooLarge`); `test_a_renamed_zip_is_unreadable`; `test_xml_entities_are_refused` (an `.xlsx` whose shared strings declare an entity → `Unreadable`, no expanded text); `test_each_office_format_round_trips_through_the_child` (real `run_child`).
- [ ] **Step 2: Run** `just test-py tests/test_reading_formats.py`. Expected: FAIL.
- [ ] **Step 3: Implement.** Call `check_archive` before any library opens the bytes; turn every library error into `Unreadable`.
- [ ] **Step 4: Check licences:** `uv run python -c "import importlib.metadata as m; [print(p, m.metadata(p).get('License-Expression') or m.metadata(p).get('License')) for p in ('python-docx', 'openpyxl', 'python-pptx', 'defusedxml', 'lxml', 'XlsxWriter', 'et-xmlfile')]"`. Expected: MIT, BSD or PSF only. Anything else: stop and report.
- [ ] **Step 5: Run** `just test-py tests/test_reading_formats.py`. Expected: PASS.
- [ ] **Step 6: Commit.** `git commit -m "feat(files): read Word, Excel and PowerPoint files (RUG-37)"`

### Task 4: PDF pages, images and Tesseract, in the image

**Files:**
- Create: `src/reef/reading/pages.py` (runs in the child), `src/reef/reading/local.py` (runs in the worker)
- Modify: `src/reef/reading/child.py` (ops), `pyproject.toml` and `uv.lock` (`uv add pypdfium2 pillow`; `uv add --dev pypdf`), `Dockerfile`, `.github/workflows/ci.yml`, `tests/filefixtures.py` (`text_pdf(pages)` written by hand with Helvetica; `scan_pdf(lines)` and `scan_image(lines, fmt, exif_orientation=None)` drawn with Pillow's default font at 48 px; `encrypted_pdf()` with `pypdf`), `tests/test_docker_context.py`
- Test: `tests/test_reading_pages.py`

**Interfaces:**
- Consumes: Task 2's sandbox, `ScanPage`, `Reading`, errors.
- Produces:
  - Child ops: `survey <kind>` → `{"pages": int}`; `page <number> <dpi>` → `{"text": str}` for a text page or `{"png": base64}` for a scan page; `frame <number>` → `{"png": base64}`; `ocr <tesseract> <languages> <dpi>` (stdin PNG) → `{"text": str, "confidence": float | None, "model": str}`.
  - In `pages.py`: `SCAN_TEXT_CHARS = 20` (a text layer with fewer non-space characters makes a scan page), `MAX_PIXELS = 40_000_000` (checked from the header, before decoding; past it `TooLarge`), `NOISE_WORDS = 3`, `NOISE_CONFIDENCE = 40.0` (fewer words, or a lower mean word confidence, gives empty text). Starting values; the builder may tune them on the test files.
  - In `local.py`, each child call limited to `reading.page_seconds`: `async def survey(data: bytes, kind: FileKind) -> int`; `async def open_page(data: bytes, kind: FileKind, number: int, *, dpi: int) -> Reading | ScanPage` (a PDF text page gives `Reading(reader="pdf-text", model="pdfium")`; a PDF scan page or image frame gives a `ScanPage`); `async def read_parts(data: bytes, kind: FileKind) -> list[str]` (one child call for the whole file); `class TesseractReader` with `name = "tesseract"`, `leaves_reef = False`, `async def read(self, page: ScanPage) -> Reading`. It runs `tesseract stdin stdout -l <languages> --dpi <dpi> tsv` in the child, from the absolute path `shutil.which("tesseract")` found once; text rebuilt from the TSV words by block, paragraph and line; confidence is the mean word confidence.
  - Image: `apt-get install --no-install-recommends tesseract-ocr tesseract-ocr-nld tesseract-ocr-fra` and `useradd --system --no-create-home --shell /usr/sbin/nologin reader`.

- [ ] **Step 1: Write the failing tests** (a `tesseract` fixture fails, not skips, with `brew install tesseract tesseract-lang` or the apt line when the binary or a language is missing): `test_a_text_page_is_read_from_its_layer_without_ocr` (`"Polisnummer 4711-2026"` comes back with reader `pdf-text`; `TesseractReader.read` patched to raise is never called); `test_a_page_without_a_layer_is_a_scan_page` (an A4 page at 200 dpi gives a PNG of about 1,654 × 2,339 pixels); `test_a_mixed_pdf_is_handled_page_by_page` (text, scan, text); `test_an_encrypted_pdf_is_refused` (`Encrypted`); `test_an_image_is_turned_upright_and_loses_its_metadata` (EXIF orientation 6 swaps width and height; the PNG carries no EXIF or GPS); `test_each_tiff_frame_is_a_page` (2 frames → `survey` 2); `test_an_image_too_large_is_refused_before_decoding` (hand-written PNG header of 20,000 × 20,000 → `TooLarge`); `test_tesseract_reads_dutch_french_and_english` ("Verzekering", "Assurance", "Insurance" all in the text, confidence above 60); `test_a_page_with_no_words_is_empty_text` (a white page → `""`); `test_the_image_installs_tesseract_and_the_reader_user` (the Dockerfile names `tesseract-ocr-nld`, `tesseract-ocr-fra` and `useradd` of `reader`).
- [ ] **Step 2: Run** `just test-py tests/test_reading_pages.py tests/test_docker_context.py`. Expected: FAIL.
- [ ] **Step 3: Implement** the ops, `pages.py` and `local.py`. Add to the Dockerfile's apt layer the packages and the user. In CI, add a step "Install Tesseract" before "Test" in `backend` (`sudo apt-get update && sudo apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-nld tesseract-ocr-fra`), and in `image` build with `load: true` and `tags: reef:ci`, then run `docker run --rm reef:ci sh -c 'tesseract --list-langs && id reader'`.
- [ ] **Step 4: Run** `just test-py tests/test_reading_pages.py tests/test_docker_context.py`. Expected: PASS.
- [ ] **Step 5: Check licences** as in Task 3 Step 4, for `pypdfium2`, `pillow` and `pypdf`. Expected: Apache-2.0 or BSD-3-Clause, MIT-CMU, BSD-3-Clause.
- [ ] **Step 6: Build** `docker build -t reef:file-text .` and run `docker run --rm reef:file-text sh -c 'tesseract --list-langs && id reader'`. Expected: `eng`, `fra`, `nld`, `osd` listed; `reader` has a uid.
- [ ] **Step 7: Commit.** `git commit -m "feat(files): read PDF pages and scans with Tesseract, in the image (RUG-37)"`

### Task 5: File chunks and their vectors

**Files:**
- Create: `src/reef/filechunks.py`
- Modify: `src/reef/sections.py` (`_piece` becomes public `piece`), `src/reef/embedding.py`, `src/reef/jobs.py` (`EMBED_FILE = "embed_file"`), `src/reef/worker.py` (`_embed_file`)
- Test: `tests/test_filechunks.py`, `tests/test_file_embedding.py`, `tests/test_embedding.py` (must still pass)

**Interfaces:**
- Consumes: Task 1's tables and `get_settings().reading.enabled`.
- Produces:
  - `filechunks.split(filename: str, description: str, pages: list[tuple[int, str]]) -> list[Section]`: the whole-file chunk first (anchor None; `sections.piece` with title `filename`, the description, and the start of the pages' text up to `embedding.engram_chars`), then each non-empty page as anchor `p<N>`, a longer page cut with `sections.cut` into `p<N>`, `p<N>~2`, …; a page shorter than `piece_min_chars` joins the chunk before it. The context line `page N` (as the heading path) appears only when the file has more than one page. A file without text gives exactly the whole-file chunk.
  - `filechunks.page_of(anchor: str | None) -> int | None`.
  - `embedding.embed_file(principal: Principal, attachment_id: UUID, embedder: Embedder) -> int`: as `embed_engram`, for a ready file in a writable space, from its filename, description and `file_pages`; raises `CapReached` and `ServiceFailed` the same way. Both share one private helper that diffs digests, checks the cap, embeds, charges `embedding_spend` and writes rows; the table and column names it takes are fixed literals, never input.
  - `embedding.request_file(principal: Principal, attachment_id: UUID, delay: timedelta | None = None) -> bool`: no-op without `REEF_EMBEDDING_URL` or with `reading.enabled` off; one waiting job per file.
  - `embedding.files_lacking(principal: Principal, limit: int | None = None) -> list[UUID]`: ready files in writable spaces with a final `text_status` (`read`, `partly_read`, `unreadable`, `not_read`) and no whole-file vector of this model.
  - Worker: `HANDLERS[EMBED_FILE] = _embed_file`, the same retries as `_embed_engram`; it does nothing with the switch off.

- [ ] **Step 1: Write the failing tests.** Chunks: `test_a_file_without_text_is_one_chunk_of_name_and_description` (`split("plan.png", "Floor plan of the ground floor", [])` → one chunk, anchor None, text starting `"plan.png\nFloor plan of the ground floor"`); `test_each_page_is_a_chunk_that_says_its_page` (two 600-character pages → anchors `[None, "p1", "p2"]`, chunk `p2` holds `"page 2"`); `test_a_long_page_is_cut_between_paragraphs`; `test_a_short_page_joins_the_one_before`; `test_a_one_page_file_has_no_page_label`; `test_page_of_reads_the_anchor` (`page_of("p12~3") == 12`, `page_of(None) is None`). Embedding (with `FakeEmbedder` from `tests/test_embedding.py`, pages inserted directly): `test_every_file_gets_a_vector_for_its_description` (a `not_read` file → one vector, anchor None); `test_a_read_file_gets_a_vector_per_chunk`; `test_unchanged_chunks_are_not_embedded_again`; `test_file_chunks_count_against_the_daily_cap`; `test_a_file_over_the_cap_waits` (requeued after `cap_retry_seconds`); `test_a_viewer_cannot_embed_a_file`; `test_nothing_is_asked_for_with_the_switch_off`.
- [ ] **Step 2: Run** `just test-py tests/test_filechunks.py tests/test_file_embedding.py`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `just test-py tests/test_filechunks.py tests/test_file_embedding.py tests/test_embedding.py tests/test_sections.py`. Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat(files): embed every file's description and the text read from it (RUG-37)"`

### Task 6: The reading job

**Files:**
- Create: `src/reef/filetext.py`
- Modify: `src/reef/jobs.py` (`READ_FILE = "read_file"`, `BACKFILL_FILES = "backfill_files"`), `src/reef/worker.py` (`_read_file`, `_backfill_files`), `src/reef/attachments.py` (`add_attachment` calls `queue_reading` in its ready transaction), `src/reef/context.py` (`_queue_backfills` calls `filetext.request_backfill`), `tests/conftest.py` (`MemoryStore`, which keeps bytes and answers `get`; `store_as(caller, space, filename, data, mime, description) -> Attachment`)
- Test: `tests/test_file_reading.py`

**Interfaces:**
- Consumes: Tasks 2 to 4 (`likely_kind`, `survey`, `open_page`, `read_parts`, `TesseractReader`, errors), Task 5 (`embedding.request_file`, `files_lacking`).
- Produces, in `reef.filetext` (all inside the caller's transaction):
  - `@dataclass(frozen=True) class StoredPage: number: int; text: str; reader: str`.
  - `class AllowanceSpent(Exception)`.
  - `async def queue_reading(principal: Principal, attachment: Attachment, head: bytes) -> TextStatus | None`: None with the switch off; otherwise `waiting` plus a `read_file` job for a likely-readable kind, or `not_read` plus `embedding.request_file`.
  - `async def request_read(principal: Principal, attachment_id: UUID, delay: timedelta = timedelta(0)) -> bool`: one waiting `read_file` job per file and person.
  - `async def read_file(principal: Principal, attachment_id: UUID, store: ObjectStore | None = None) -> TextStatus | None` (None means `S3ObjectStore()`): None with the switch off, or for a file that is gone, not ready or not writable. A final status is returned as it is. Otherwise it reads the bytes and the kind. OTHER (also when the child finds no main part) gives `not_read`. A text format is read whole; its pages carry its `FileKind` value as reader. A paged file reads the first `pages_per_job` page numbers not yet in `file_pages`, up to `pages_per_file`, at most `parallel_pages` at a time, and only as many as the day's allowance leaves; none left raises `AllowanceSpent`. Each page's text is cut to `page_chars`. Rows are inserted with `ON CONFLICT (attachment_id, number) DO NOTHING`, and `reading_spend` is charged per reader. More pages left returns `waiting`. Otherwise the status becomes `read`, or `partly_read` past `pages_per_file`, `pages` is set to the file's page count, and `embedding.request_file` is called. A `ReadFailed` keeps the pages read so far, sets `unreadable` and `text_error` to the class name, and calls `embedding.request_file`.
  - `async def spent_pages_today(principal: Principal) -> int`.
  - `async def request_backfill(principal: Principal) -> bool`: with the switch on, queues `backfill_files` when a writable ready file has `text_status` NULL, or, with an embedding service, when `files_lacking(limit=1)` finds one. Without a service it never asks on account of vectors, or it would ask on every index load.
  - `async def backfill(principal: Principal) -> int`: sets `waiting` and queues `read_file` for writable ready files whose status is NULL; queues `read_file` again for `waiting` files without a waiting job of this person's; calls `request_file` for each of `files_lacking`.
  - `async def stored_pages(attachment_ids: list[UUID]) -> dict[UUID, list[StoredPage]]`, in page order, under the caller's row security.
  - Worker: `_read_file` does nothing with the switch off. On `waiting` it calls `request_read` at once; on `AllowanceSpent` with `delay=retry_seconds`. It logs one line per file, `"read_file: %d pages (layer %d, ocr %d, format %d) %s in %.1f s"`, with the counts, the status and the time; nothing else. `_backfill_files` calls `backfill`. Both are in `HANDLERS`.

- [ ] **Step 1: Write the failing tests** (`MemoryStore` patched into `filetext.S3ObjectStore`; switch on unless the test says off): `test_a_stored_file_is_queued_only_with_the_switch_on` (off: no job, `text_status` None; on: one `read_file`, `waiting`); `test_a_text_pdf_is_read_and_marked_read` (3 rows, reader `pdf-text`, `pages == 3`, an `embed_file` request with an embedding URL set); `test_a_long_file_is_read_ten_pages_a_job` (25 pages → after `drain`, numbers 1 to 25 once each, three `read_file` jobs done); `test_reading_resumes_without_duplicates_after_a_crash` (pages 1 to 10 inserted, status `waiting` → the next job stores 11 to 20); `test_pages_past_the_limit_are_not_read` (`pages_per_file=3`, 5 pages → 3 rows, `partly_read`, `pages == 5`); `test_a_scan_is_read_by_tesseract` ("Polisnummer 4711" → reader `tesseract`, text holds `4711`, confidence set); `test_a_word_file_is_read_whole_in_one_job`; `test_an_unread_format_is_marked_not_read_and_still_embedded`; `test_a_broken_file_is_unreadable_and_not_retried` (`b"%PDF-1.7 garbage"` → `unreadable`, `text_error == "Unreadable"`, job `done`); `test_a_page_that_runs_out_of_time_makes_the_file_unreadable` (`page_seconds=0.001` → `text_error == "ReadTimedOut"`); `test_a_broken_file_does_not_stop_the_next_job` (two jobs, the first file hostile: the second file is `read`); `test_a_spent_allowance_requeues_the_file_for_later` (`daily_pages=2`, 5 pages → 2 rows, job `done`, a `read_file` waiting with `run_after` at least `retry_seconds` ahead, `reading_spend.pages == 2`); `test_a_job_for_a_deleted_file_ends_quietly`; `test_a_job_reads_only_files_its_person_may_write` (the partner's job for Wouter's personal file, and a viewer's job for a shared file: nothing read, job `done`); `test_loading_the_index_asks_for_the_backfill_once` (two `build_index` calls → one `backfill_files`; switch off → none; a viewer → none); `test_the_backfill_reads_never_read_files_and_requeues_stuck_ones`; `test_the_worker_does_nothing_with_the_switch_off`.
- [ ] **Step 2: Run** `just test-py tests/test_file_reading.py`. Expected: FAIL.
- [ ] **Step 3: Implement** `filetext.py`, the handlers, and the two hooks.
- [ ] **Step 4: Run** `just test-py tests/test_file_reading.py tests/test_attachments.py tests/test_jobs.py tests/test_context.py`. Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat(files): the worker reads each file's pages and keeps them (RUG-37)"`

### Task 7: Search inside files

**Files:**
- Modify: `src/reef/search.py`
- Test: `tests/test_search_files.py`, `tests/test_search_hybrid.py` (the `fuse` calls take kinds)

**Interfaces:**
- Consumes: Task 1's `file_pages` and `file_embeddings`; Task 5's `filechunks.split` and `page_of`.
- Produces:
  - `fuse(lexical: list[tuple[str, UUID, bool]], semantic: list[tuple[str, UUID]]) -> list[tuple[str, UUID]]`; otherwise unchanged.
  - `search_engrams` file hits always carry `page: int | None`. With `passages=True` they also carry `passage` (at most `PASSAGE_CHARS`): the closest chunk's text; else the best page's snippet; else the description's snippet. `page` is the closest chunk's page, else the best page by words, else None.
  - With `reading.enabled` off, the statements and results are today's, plus `page: None` and, with `passages=True`, `passage` equal to the snippet.
  - Decisions for the SQL (written by the implementer): the file branch of `_HITS_SQL` takes the attachment's own row (page NULL) and every matching `file_pages` row, and keeps one row per file, its best by `every_word`, then rank, carrying that `page`. A file's snippet is the headline of its best page's text when `page` is set. By meaning, one statement ranks engram sections and file chunks (files joined to `attachments` with status `ready`, so row security and readiness apply), each owner by its closest piece, `fusion_depth` in all. `_RANKING_SQL` is unchanged. The reranker reads each file candidate's `rerank.passages` closest chunks, rebuilt from its pages with `filechunks.split`; a file without vectors gives its whole-file chunk. Every read stays in a savepoint, as today.

- [ ] **Step 1: Write the failing tests** (pages inserted directly; `FakeEmbedder` and a recording fake reranker as in `tests/test_search_hybrid.py`): `test_a_file_is_found_by_a_word_on_one_of_its_pages` (the word is only on page 3 → a file hit with `page == 3` and the word in bold in its snippet); `test_a_file_counts_once_by_its_best_page`; `test_page_words_are_not_searched_with_the_switch_off`; `test_a_photo_is_found_by_the_meaning_of_its_description` (no pages, no shared word, closest vector → a file hit); `test_a_file_hit_carries_the_passage_closest_to_the_question` (`page == 2`, `len(passage) <= 1500`); `test_without_vectors_the_passage_is_the_best_pages_snippet`; `test_a_file_is_not_sure_in_a_whole_question` (an engram that answers stays first when a file with a matching page shares a word); `test_fuse_merges_files_and_engrams_by_kind_and_id`; `test_the_reranker_reads_a_files_best_chunks` (the sent documents include the chunk with `"page 2"`); `test_nobody_finds_text_in_a_file_they_cannot_open` (the partner, by words and by meaning: no hit on Wouter's personal file).
- [ ] **Step 2: Run** `just test-py tests/test_search_files.py`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `just test-py tests/test_search.py tests/test_search_hybrid.py tests/test_search_files.py tests/test_rerank.py tests/test_eval_harness.py`, then the same with `REEF_FILE_TEXT=1`. Expected: PASS both times.
- [ ] **Step 5: Commit.** `git commit -m "feat(search): find passages inside files, by words and by meaning (RUG-37)"`

### Task 8: `recall`, `open`, `fetch`, `store_file` and `forget`

**Files:**
- Modify: `src/reef/filetext.py`, `src/reef/methods/find.py`, `src/reef/methods/connector.py`, `src/reef/methods/capture.py`, `src/reef/methods/maintain.py`, `src/reef/server.py` (docstrings of `recall`, `open`, `store_file`), `src/reef/protocol.md`
- Test: `tests/test_method_file_text.py`, `tests/test_protocol.py`, `tests/test_tool_surface.py`

**Interfaces:**
- Consumes: Task 6's `stored_pages` and `StoredPage`; Task 7's file hits.
- Produces:
  - `filetext.render_text(pages: list[StoredPage], *, start: int | None = None, cap: int | None = None) -> tuple[str, int | None]`: each page begins with the line `--- page N ---`; with `cap`, whole pages up to the cap, returning the last page included when it cut, else None; with `start`, from page `min(start, last)`, only when the whole text does not fit.
  - `filetext.read_by(pages: list[StoredPage]) -> list[str]`: the distinct `READER_NAMES` of pages with text, in order (`["Reef"]` in phase 1).
  - `find.Ref`: `@dataclass(frozen=True) class Ref: space: str; kind: str; locator: str; page: int | None = None`; `_parse_ref(ref: str) -> Ref | None`. On a file reference only `#page=<integer ≥ 1>` is accepted; any other anchor is malformed, so `not_found`.
  - `recall` file items, with the switch on: `passage` from the hit, `page`, and `ref` ending `#page=N` when there is a page. Engram items are unchanged.
  - `open` file items, with the switch on, add `text_status`, `pages`, `read_by`, `text` and `text_cut_after_page`, cut at `get_settings().engrams.max_chars`.
  - `fetch` on a file: `text` is the description, a blank line, then `render_text` cut at the same cap minus the description's length.
  - `store_file` answers `text`: `"waiting"` or `"not_read"`, with the switch on only.
  - `forget` on a file whose pages exist: the effect reads `This permanently deletes the file "<name>" (<size> bytes) and the text Reef read from it. It cannot be undone.`
  - `protocol.md`: Reef can read the text inside stored files; a file passage names its page; a ref may end in `#page=N`, and `open` on it reads a long file from that page; a page is a PDF page, an image, a slide, a sheet, or about 20,000 characters of a text document. `load_index` is unchanged.

- [ ] **Step 1: Write the failing tests:** `test_recall_returns_a_passage_and_page_from_inside_a_file` (`ref` ends `#page=2`); `test_recall_file_hits_are_as_today_with_the_switch_off`; `test_open_returns_the_text_with_every_page_marked` (`--- page 1 ---`, `--- page 2 ---`, `read_by == ["Reef"]`, `text_status == "read"`, `pages == 2`); `test_open_cuts_a_long_file_at_a_page` (cap 1,000, pages of 400 → pages 1 and 2, `text_cut_after_page == 2`); `test_open_reads_a_long_file_from_the_anchored_page` (`#page=3` → text starts `--- page 3 ---`); `test_an_anchor_on_a_file_that_fits_returns_it_whole`; `test_a_malformed_anchor_is_not_found` (`#page=0`, `#page=x`, `#page=`, `#p=2`); `test_an_anchor_past_the_last_page_reads_the_last_page`; `test_open_on_a_file_is_as_today_with_the_switch_off` (same keys as today); `test_fetch_returns_description_and_text`; `test_store_file_says_whether_reef_will_read_it` (`.pdf` → `"waiting"`, `.psd` → `"not_read"`, off → no `text` key); `test_forget_says_the_text_goes_too`; `test_load_index_is_the_same_size_with_the_switch_on` (`json.dumps` of the payload identical on and off after two files were read).
- [ ] **Step 2: Run** `just test-py tests/test_method_file_text.py`. Expected: FAIL.
- [ ] **Step 3: Implement**, then update `protocol.md` and the three tool docstrings.
- [ ] **Step 4: Run** `just test-py tests/test_method_file_text.py tests/test_method_find.py tests/test_method_capture.py tests/test_method_maintain.py tests/test_connector_tools.py tests/test_protocol.py tests/test_tool_surface.py`. Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat(methods): recall, open and fetch read inside files (RUG-37)"`

### Task 9: Deletion and export

**Files:**
- Modify: `src/reef/export.py`
- Test: `tests/test_file_text_deletion.py`, `tests/test_export.py`

**Interfaces:**
- Consumes: Task 6's `stored_pages`; Task 8's `render_text` and `read_by`.
- Produces: the full dump writes `spaces/<space>/files/<token>/<filename>.txt` (whole text, pages marked, no cap) for a file with pages; its `data/files.json` entries gain `text_status`, `pages`, `readers` and `text_path` (None without text). `build_json_export` and the Markdown archive's `files.json` gain `text_status` only. Vectors stay out.

- [ ] **Step 1: Write the failing tests:** `test_forget_takes_the_pages_and_vectors` (through `maintain.forget` with its token; counted through the `seed` connection); `test_deleting_a_space_takes_its_files_pages_and_vectors` (`spaces.delete_space`); `test_deleting_an_account_takes_pages_vectors_and_reading_spend` (`account.delete_account_rows`); `test_the_full_dump_puts_each_files_text_beside_it`; `test_smaller_exports_carry_the_status_only`; `test_vectors_stay_out_of_the_dump`.
- [ ] **Step 2: Run** `just test-py tests/test_file_text_deletion.py tests/test_export.py`. Expected: FAIL on the export tests; the deletion tests may already pass through the cascades, which is what they pin.
- [ ] **Step 3: Implement** the export.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "feat(export): the text read from a file travels with it, and goes with it (RUG-37)"`

### Task 10: Nothing leaves Reef

**Files:**
- Modify: `src/reef/telemetry.py` (`"filename"`, `"description"`, `"text"` in `SCRUB_PATTERNS`)
- Test: `tests/test_file_text_privacy.py`, `tests/test_telemetry.py`

**Interfaces:**
- Consumes: everything above. Produces nothing new.

- [ ] **Step 1: Write the failing tests:** in `test_scrub_patterns_cover_every_content_field`, add `filename`, `description` and `text`. `test_a_marker_word_reaches_no_log_or_span`, parametrised over a text PDF, a scan PDF, a JPEG, Markdown, HTML, `.docx`, `.xlsx` and `.pptx`: `ZEBRAQUILT` in the content, the filename and the description; with the `live` capture and `caplog` at DEBUG on every logger, store the file, `drain` (read and embed with `FakeEmbedder`), `recall`, `open` → the marker is in neither `caplog.text` nor the exported spans. `test_file_job_spans_keep_their_kind` (job spans of `read_file`, `embed_file`, `backfill_files` keep `kind` after scrubbing, and the `read_file:` log line arrives unredacted). `test_reading_and_embedding_reach_only_their_own_services` (`httpx.AsyncClient.send` patched to record: reading every kind sends nothing; embedding with a real `HttpEmbedder` sends only to `REEF_EMBEDDING_URL`).
- [ ] **Step 2: Run** `just test-py tests/test_file_text_privacy.py tests/test_telemetry.py`. Expected: FAIL on the scrub patterns.
- [ ] **Step 3: Implement.** If a log line or kind is redacted, rename it; never weaken a pattern.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "test(files): file text reaches no log, span or outside service (RUG-37)"`

### Task 11: Operator scripts, runbook, privacy page and changelog

**Files:**
- Create: `scripts/file_report.py`, `scripts/measure_recall.py`, `changes/rug-37-file-text.md`
- Modify: `docs/runbook.md`, `site/privacy.html`
- Test: `tests/test_file_report.py`, `tests/test_measure_recall.py`

**Interfaces:**
- Consumes: Task 4's `local` readers, Task 6's `backfill`, Task 7's `search_engrams`.
- Produces:
  - `scripts/file_report.py --email E [--time N] [--queue]`, run in `reef-worker` as that person. It prints JSON with the number of ready files and pages per kind, and nothing else: no names, keys or text. `--time N` reads up to N scan pages through the real sandbox, stores nothing, and adds `seconds_per_page` (median, max) and the median word confidence. `--queue` calls `filetext.backfill` and adds the number of jobs queued.
  - `scripts/measure_recall.py --email E --file-text on|off`, run in `reef-app`, reads questions as JSON on stdin: `[{"id", "text", "expected": ["<space>/<path>" | "<space>/file/<key>"], "answer"?}]`. The existing private questions file fits as it is. It searches with `search_engrams(..., limit=5, log=False, passages=True)`, forcing `reading.enabled` for its own process. It prints `{"engrams": {"asked", "found_at_5"}, "files": {"asked", "found_at_5", "answer_in_passage"}, "median_ms"}` and never a question, ref or passage. "Answer in passage": the answer, lowercased and without white space, is inside the passage treated the same way.
  - Runbook section "Reading files (RUG-37)": what it does; `REEF_FILE_TEXT` on `reef-app` and `reef-worker`; the worker's read-only R2 token (`REEF_S3_ENDPOINT`, `REEF_S3_BUCKET`, `REEF_S3_ACCESS_KEY`, `REEF_S3_SECRET_KEY`, from a Cloudflare token with "Object Read only" on the bucket; never the app's keys); the settings; costs (2,000 pages and 2,000 embedded pieces per person per day, so a large account takes days); the sandbox and the `reader` user; what is logged; how to switch off (unset the variable: reading and file search stop, stored text stays); the two scripts. "The worker" section: the storage variables are now needed, read-only.
  - Privacy page: under "Where it lives", "Text read from your files — in the same database as your engrams. The files stay in Cloudflare R2, in the European Union. Not switched on yet." The "Search vectors" line names file descriptions and text read from files.
  - `changes/rug-37-file-text.md`, kind `changed`: "The privacy page now says where Reef would keep the text it reads from your files: in its own database, next to your engrams. Reef does not read files yet."

- [ ] **Step 1: Write the failing tests:** `test_the_report_counts_without_names` (two files; stdout holds kinds and pages, no filename or description); `test_the_report_times_pages_and_stores_nothing` (`--time 1` on a scan → `seconds_per_page` set, `file_pages` empty); `test_measuring_prints_numbers_only_and_logs_no_search` (search log switched on; a marker in a question; stdout lacks it; `search_log` has no row; `found_at_5` correct for one engram and one file question).
- [ ] **Step 2: Run** `just test-py tests/test_file_report.py tests/test_measure_recall.py`. Expected: FAIL.
- [ ] **Step 3: Implement** the scripts, then write the runbook section, the privacy lines and the fragment. Run `just settings-table`.
- [ ] **Step 4: Run** `just test-py tests/test_file_report.py tests/test_measure_recall.py tests/test_settings.py tests/test_web_static.py tests/test_release_notes.py`. Expected: PASS.
- [ ] **Step 5: Commit.** `git commit -m "docs(files): runbook, privacy page and the operator's scripts for reading files (RUG-37)"`

### Task 12: Verify and open the pull request

**Files:**
- Create: the switch-on ticket in Linear, under RUG-37, holding Task 13's steps.

- [ ] **Step 1 (subagent): Run** `just test`. Expected: all pass. Then `REEF_FILE_TEXT=1 just test-py tests/test_eval_harness.py`. Expected: pass (the benchmark slice guard).
- [ ] **Step 2 (subagent): Try it end to end on made-up files only.** `just test-account`, then `REEF_FILE_TEXT=1 just worker` and `REEF_FILE_TEXT=1 just dev test@reef.local`, with `REEF_EMBEDDING_URL` pointing at a local Ollama. Through Claude Code: store a generated scan PDF, a `.docx` and a photo; after the worker drains, `recall` a word found only inside each. Expected: a passage with its page for the scan and the `.docx`; the photo found by its description. Put a summary, without file contents, in the pull request.
- [ ] **Step 3 (controller): Rehearse the migration** on a local database restored from production's schema only (`pg_dump --schema-only`), as in RUG-40: apply every migration up to `main`, then this branch's; run `just test-py tests/test_migration_chain.py tests/test_file_tables.py`. Expected: pass. Never copy production's data.
- [ ] **Step 4 (controller): Create the switch-on ticket** in Linear under RUG-37, with Task 13's steps as its checklist.
- [ ] **Step 5 (controller): Open the pull request**, saying it ships dark and naming the switch-on ticket. Merging waits for Wouter.

### Task 13: Switch on (the ticket's contents; controller only)

Every step touches production, Wouter's private data or an external account. Questions and outputs stay in `~/.reef-eval-private`; the ticket gets numbers only.

- [ ] **Step 1 (Wouter): Read-only storage key.** Wouter creates a Cloudflare R2 token with "Object Read only" on the bucket and sets the four `REEF_S3_*` variables on `reef-worker` only.
- [ ] **Step 2: Switch on the worker alone.** `REEF_FILE_TEXT=1` on `reef-worker`. `reef-app` stays off, so nobody's index load queues reading and no search reads file text.
- [ ] **Step 3: Speed on Railway.** In `reef-worker`: `uv run --frozen --no-dev python scripts/file_report.py --email <Wouter> --time 10`. Expected: median under 10 seconds per page. Otherwise stop and report the numbers to Wouter.
- [ ] **Step 4: Counts.** `file_report.py --email <Wouter>`. Record pages per kind in the ticket. Over 2,000 pages means several days of backfill, unless Wouter raises `REEF_FILE_TEXT_DAILY_PAGES` for it.
- [ ] **Step 5: The question sets.** Wouter writes, with the controller, 20 questions over his files whose answer is only inside the text (scans, text PDFs and text formats), with the file's key from his JSON export, in `~/.reef-eval-private/file-questions.json`. The 40 engram questions are `~/.reef-eval-private/questions.json`.
- [ ] **Step 6: Baseline.** In `reef-app`, with each question set passed on stdin from Wouter's machine and never written to the container's disk: `measure_recall.py --email <Wouter> --file-text off`, once for each set. Expected: file answers in passages near 0 of 20. Record the numbers and `median_ms`.
- [ ] **Step 7: Backfill Wouter's account.** `file_report.py --email <Wouter> --queue`, then watch the `read_file` and `embed_file` spans in Logfire until none remain for his jobs.
- [ ] **Step 8: Measure.** The same commands with `--file-text on`. Expected: files found in the first five ≥ 16 of 20 (80%); answer in the passage ≥ 12 of 20 (60%); engrams found in the first five ≥ 38 of 40 (95%); `median_ms` at most 100 above the baseline. If any guard fails, stop and report to Wouter; do not switch on for everyone.
- [ ] **Step 9: Privacy page live.** A small pull request drops "Not switched on yet" and adds `changes/rug-37-file-text-on.md`, kind `added`: "Your assistant can now find what is written inside the PDFs, scans and documents you store, and tell you the page." Merge with Wouter's OK.
- [ ] **Step 10: Everyone.** `REEF_FILE_TEXT=1` on `reef-app`. Each person's next index load queues their backfill.
- [ ] **Step 11: A week later.** Compare the median duration of `recall` tool spans in Logfire over the 7 days before and after Step 10. Expected: at most 100 ms more. Close the ticket.
