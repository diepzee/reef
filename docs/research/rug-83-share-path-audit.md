# RUG-83: path checks before writes

Reviewed on 25 November 2026 on `fix/rug-83-share-taken-path`.
Only share is changed. The other findings below come from reading the code;
they are not newly reproduced bugs.

## Reproduction and fix

The assistant's `share` method replaced `household/boiler.md` when asked to
share to `Boiler.md` or `boiler`. Tests reproduced this for whole engrams and
sections, both when the destination already existed and when it was created
between the two calls. The destination body changed and its version rose from
1 to 2. Each test checked history before checking the current body: the old
body was still there.

One example in the report needed correction: `Boiler .md` becomes `boiler-.md`,
not `boiler.md`. Tests cover a collision with `boiler-.md` and a successful
share that leaves `boiler.md` unchanged.

Preparation now checks and stores the repaired destination. Confirmation repairs
older staged destinations too. The method compares repaired destination arguments
with the staged path, so repeating the original spelling still works. The existing
normalizer and refusal messages are reused; no migration is needed. Invalid paths
keep the `invalid_path` refusal, now at preparation rather than after confirmation.

## Other locations

| Location | Finding |
| --- | --- |
| `src/reef/engrams.py:329`, `:335`, `:338` | `save_engram` looks up the exact spelling, then repairs and looks up again when absent. This is the shared write behavior behind the bug. Existing legacy names stay writable as stored. A caller requiring creation must pass `expected_version=0`; the version check also applies to the repaired match. |
| `src/reef/methods/capture.py:668`, `:90` | The assistant's `write` passes the supplied path and optional version to `save_engram`. It promises creation or replacement, so a differently spelled name can replace a repaired match when no version is supplied. There is no separate absence check. |
| `src/reef/web/routes_api.py:530`, `:560` | GET looks up the spelling as sent; PUT passes it to `save_engram`. A GET for `Boiler.md` can return 404 while a PUT to that spelling updates `boiler.md`. PUT explicitly allows replacement without `expected_version`; zero requires creation. |
| `frontend/src/views/NewEngram.tsx:22`, `:41`; `frontend/src/views/Editor.tsx:132`, `:137` | Related creation risk: the new-engram form repairs the path but starts with a null version, which the editor sends to PUT. It does not require the destination to be absent. This is a missing creation guard rather than a raw-path lookup followed by repair; left unchanged. |
| `src/reef/methods/maintain.py:78`, `:81`, `:139`, `:199` | `correct` and `undo` try the raw name and then the repaired name. They save the found row's actual path with its version, so the check and save address the same engram. |
| `src/reef/engrams.py:432`, `:440` | `edit_section` looks up the raw name, refuses if absent, and saves that existing name with a version check. It does not fall through to a repaired name after a missing lookup. |
| `src/reef/methods/capture.py:384`, `:388`, `:409`, `:479` | Starting a log tries the raw name, then checks the repaired name too. Creation requires version zero. |
| `src/reef/methods/capture.py:523`, `:528`, `:543`; `src/reef/methods/logs.py:138` | Log rollover checks a candidate path before saving. It first requires a normalized base path, skips occupied candidates, and requires version zero for the new part. |
| `src/reef/methods/capture.py:151`, `:160` | Inbox appends look up and save the same fixed `inbox.md` path. |
| `src/reef/methods/share.py:130`, `:79` | Persona updates look up and save the fixed `meta/persona.md` path with the staged version. |

Other reads in `find`, `connector`, `protocol`, and revision retrieval do not
lead to an engram save. Space setup and test-account fixtures use fixed seed
paths, without a caller-supplied absence check.

## Validation before the fix

`just test-py tests/test_method_share.py`: **22 failed, 21 passed in 11.60s**.
Twelve failures showed the existing destination being replaced, six showed
the staged path was unrepaired, and four covered older tokens returning the
wrong path or allowing replacement. The invalid-path refusal tests passed.

After the fix, `just test-py tests/test_method_share.py tests/test_promotion.py`:
**61 passed in 12.24s**. Final whole-suite and lint results are recorded in the PR.
