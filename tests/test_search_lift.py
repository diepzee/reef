"""The lift stays inside each search route and the reader's permissions."""

import hashlib
import re
from uuid import uuid4

import pytest
from test_search_files import reranker as shared_reranker
from test_search_hybrid import service as shared_service

from reef import embedding, lifting, links, search, searchlog, usage
from reef.access import Principal, arm
from reef.config import get_settings
from reef.db import DB, transaction_scope
from reef.engrams import save_engram
from reef.models import Attachment, AttachmentStatus, Engram, Link

service = shared_service
reranker = shared_reranker


@pytest.fixture
async def matches(household, service, monkeypatch):
    """Make eight matches with the same clear order by words and meaning."""
    person = household["wouter"]
    me = Principal(person_id=person.id, email=person.email)

    def vector(text):
        """Place each made engram a little further from the question."""
        number = re.search(r"grain(\d+)", text)
        return [1.0, int(number[1]) / 10 if number else 0.0, 0.0, 0.0]

    monkeypatch.setattr(service, "_vector", vector)
    rows = []
    async with transaction_scope():
        for number in range(8):
            row = await save_engram(
                me,
                "personal",
                f"match-{number}.md",
                "car " * (8 - number) + f"grain{number} " + "word " * 50,
                title=f"Note {number}",
                message="x",
            )
            await embedding.embed_engram(me, row.id, service)
            rows.append(row)
    return me, rows


async def _link(me, source, target, kind="references"):
    """Save a made link through the same row policy as an authored link."""
    async with transaction_scope():
        await arm(me)
        await Link(
            from_engram_id=source,
            to_engram_id=target,
            kind=kind,
            source="author",
            author_id=me.person_id,
        ).save()


def _paths(hits):
    """Read the path order of engram hits."""
    return [hit["path"] for hit in hits if hit["kind"] == "page"]


@pytest.mark.parametrize("semantic", [False, True], ids=["words", "hybrid"])
async def test_priming_lifts_sixth_and_does_not_add_non_candidates(
    matches, monkeypatch, semantic
):
    """Earlier results lift a matching engram by the stated bound."""
    me, rows = matches
    monkeypatch.setattr(get_settings().search, "priming", True)
    hits = await search.search_engrams(
        me,
        "car",
        limit=5,
        semantic=semantic,
        rerank=False,
        earlier=[[rows[5].id, uuid4()]],
    )
    assert _paths(hits) == [row.path for row in rows[:4]] + [rows[5].path]
    assert hits[4]["why"] == {"place_by_match": 6, "primed": True}
    assert all("why" not in hit for hit in hits[:4])
    hits = await search.search_engrams(
        me, "car", limit=5, semantic=semantic, rerank=False, earlier=[[rows[6].id]]
    )
    assert rows[6].path not in _paths(hits)


@pytest.mark.parametrize("semantic", [False, True], ids=["words", "hybrid"])
async def test_spreading_lifts_a_linked_match_with_readable_origins(
    matches, monkeypatch, semantic
):
    """A strong match lifts its linked sixth result into the first five."""
    me, rows = matches
    await _link(me, rows[0].id, rows[5].id)
    monkeypatch.setattr(get_settings().search, "spreading", True)
    hits = await search.search_engrams(
        me, "car", limit=5, semantic=semantic, rerank=False
    )
    assert _paths(hits)[4] == rows[5].path
    assert hits[4]["why"] == {
        "place_by_match": 6,
        "linked_from": [{"cove": "personal", "path": rows[0].path}],
    }


async def test_priming_reads_this_sessions_log_and_explicit_history_skips_it(
    matches, monkeypatch
):
    """A session uses the log unless the measuring caller supplies history."""
    me, rows = matches
    settings = get_settings().search
    monkeypatch.setattr(settings, "priming", True)
    monkeypatch.setattr(settings, "log", True)
    monkeypatch.setattr(usage, "current_session_id", lambda: "made-session")
    monkeypatch.setattr(searchlog, "current_session_id", lambda: "made-session")
    async with transaction_scope():
        await searchlog.record(me, "earlier", [rows[5].id])
    hits = await search.search_engrams(me, "car", limit=5, rerank=False)
    assert hits[4]["why"] == {"place_by_match": 6, "primed": True}

    async def refuse(*args):
        """Fail if explicit history still reads the search log."""
        raise AssertionError("the log was read")

    monkeypatch.setattr(searchlog, "earlier_results", refuse)
    assert _paths(
        await search.search_engrams(me, "car", limit=5, rerank=False, earlier=[])
    ) == [r.path for r in rows[:5]]


async def test_a_primed_non_candidate_can_pass_weight_but_cannot_enter(
    matches, monkeypatch
):
    """The earlier subject may spread to a match without becoming a match."""
    me, rows = matches
    async with transaction_scope():
        source = await save_engram(me, "personal", "earlier.md", "holiday", message="x")
    await _link(me, source.id, rows[5].id)
    monkeypatch.setattr(get_settings().search, "priming", True)
    monkeypatch.setattr(get_settings().search, "spreading", True)
    hits = await search.search_engrams(
        me,
        "car",
        limit=5,
        semantic=False,
        rerank=False,
        earlier=[[source.id], [source.id]],
    )
    assert hits[4]["path"] == rows[5].path
    assert hits[4]["why"]["linked_from"] == [{"cove": "personal", "path": source.path}]
    assert "primed" not in hits[4]["why"]
    assert source.path not in _paths(hits)


@pytest.mark.parametrize("query", ['"car"', "car -missing"])
async def test_exact_questions_do_not_run_the_stage(matches, monkeypatch, query):
    """Both exact forms use their old route, even with both switches on."""
    me, rows = matches
    before = await search.search_engrams(me, query, limit=5)
    monkeypatch.setattr(get_settings().search, "priming", True)
    monkeypatch.setattr(get_settings().search, "spreading", True)
    assert (
        await search.search_engrams(me, query, limit=5, earlier=[[rows[5].id]])
        == before
    )


@pytest.mark.parametrize("reader", ["log", "links"])
@pytest.mark.parametrize("semantic", [False, True], ids=["words", "hybrid"])
async def test_a_failed_read_rolls_back_and_keeps_the_order(
    matches, monkeypatch, caplog, reader, semantic
):
    """A real SQL error cannot abort the caller's transaction or expose text."""
    me, _ = matches
    before = await search.search_engrams(
        me, "car", limit=5, semantic=semantic, rerank=False
    )
    monkeypatch.setattr(get_settings().search, "priming", True)
    monkeypatch.setattr(get_settings().search, "spreading", True)
    monkeypatch.setattr(usage, "current_session_id", lambda: "made-session")

    async def fail(*args):
        """Cause a database failure, rather than merely a Python exception."""
        await Engram.raw("SELECT * FROM missing_lift_table")

    monkeypatch.setattr(
        searchlog if reader == "log" else links,
        "earlier_results" if reader == "log" else "neighbourhood",
        fail,
    )
    async with transaction_scope():
        hits = await search.search_engrams(
            me, "car", limit=5, semantic=semantic, rerank=False
        )
        assert await Engram.raw("SELECT 1 AS works") == [{"works": 1}]
    assert hits == before
    assert "UndefinedTableError" in caplog.text
    assert "missing_lift_table" not in caplog.text
    assert "made-session" not in caplog.text


async def test_replaced_matches_receive_no_linked_weight_and_step_back(
    matches, monkeypatch
):
    """Replacement demotion still follows the lift."""
    me, rows = matches
    await _link(me, rows[0].id, rows[5].id)
    await _link(me, rows[7].id, rows[5].id, "supersedes")
    monkeypatch.setattr(get_settings().search, "spreading", True)
    hits = await search.search_engrams(me, "car", limit=8, semantic=False, rerank=False)
    assert hits[-1]["path"] == rows[5].path
    assert "why" not in hits[-1]
    assert hits[-1]["replaced_by"][0]["path"] == rows[7].path


async def test_a_primed_match_that_steps_back_has_no_reason(matches, monkeypatch):
    """A temporary rise is not reported after replacement handling undoes it."""
    me, rows = matches
    await _link(me, rows[7].id, rows[5].id, "supersedes")
    monkeypatch.setattr(get_settings().search, "priming", True)
    hits = await search.search_engrams(
        me, "car", limit=8, semantic=False, rerank=False, earlier=[[rows[5].id]]
    )
    assert hits[-1]["path"] == rows[5].path
    assert "why" not in hits[-1]


async def test_hidden_links_cannot_lift_or_name_a_private_source(
    matches, household, monkeypatch
):
    """A private source is absent from another member's linked weight."""
    me, _ = matches
    partner = Principal(
        person_id=household["partner"].id, email=household["partner"].email
    )
    async with transaction_scope():
        shared = [
            await save_engram(
                me,
                "household",
                f"shared-{n}.md",
                "tram " * (8 - n) + "ticket " * 50,
                message="x",
            )
            for n in range(8)
        ]
        hidden = await save_engram(me, "personal", "private.md", "private", message="x")
    await _link(me, hidden.id, shared[5].id)
    before = await search.search_engrams(partner, "tram", semantic=False, rerank=False)
    monkeypatch.setattr(get_settings().search, "spreading", True)
    monkeypatch.setattr(get_settings().search, "priming", True)
    assert (
        await search.search_engrams(
            partner, "tram", semantic=False, rerank=False, earlier=[[hidden.id]]
        )
        == before
    )


async def test_files_in_the_first_twenty_do_not_admit_later_sources(
    matches, monkeypatch
):
    """Only engrams in positions one through twenty may start spreading."""
    me, rows = matches
    await _link(me, rows[0].id, rows[1].id)
    monkeypatch.setattr(get_settings().search, "spreading", True)
    candidates = [("file", uuid4()) for _ in range(20)] + [("page", r.id) for r in rows]
    async with transaction_scope():
        await arm(me)
        order, notes = await search._lift_order(
            me, candidates, {rows[0].space_id: "personal"}, []
        )
    assert order == candidates and notes == {}


async def test_only_spreading_sources_seed_the_link_read(matches, monkeypatch):
    """Lower candidates do not spend the graph's row bound on their links."""
    me, rows = matches
    settings = get_settings().search
    monkeypatch.setattr(settings, "priming", True)
    monkeypatch.setattr(settings, "spreading", True)
    candidates = [("page", uuid4()) for _ in range(50)]
    candidates[0] = ("file", uuid4())
    primed = uuid4()
    seen = []

    async def record(ids):
        """Capture the ordered seeds without adding any graph edges."""
        seen.append(ids)
        return {}, set()

    monkeypatch.setattr(links, "neighbourhood", record)
    async with transaction_scope():
        await arm(me)
        await search._lift_order(
            me, candidates, {rows[0].space_id: "personal"}, [[primed]]
        )
    assert seen == [
        [row_id for kind, row_id in candidates[:20] if kind == "page"] + [primed]
    ]


async def test_replaced_primed_sources_keep_priming_but_pass_nothing(
    matches, monkeypatch
):
    """The confirmed boundary keeps a replaced source out of both rounds."""
    me, rows = matches
    await _link(me, rows[5].id, rows[6].id)
    await _link(me, rows[7].id, rows[5].id, "supersedes")
    monkeypatch.setattr(get_settings().search, "priming", True)
    monkeypatch.setattr(get_settings().search, "spreading", True)
    captured = []
    original = lifting.linked_weights

    def record(sources, neighbours, closed):
        """Capture the contributions while retaining the real two rounds."""
        result = original(sources, neighbours, closed)
        captured.append(result)
        return result

    monkeypatch.setattr(lifting, "linked_weights", record)
    async with transaction_scope():
        await arm(me)
        _, notes = await search._lift_order(
            me,
            [("page", r.id) for r in rows],
            {rows[0].space_id: "personal"},
            [[rows[5].id]],
        )
    assert notes[rows[5].id]["primed"] is True
    assert all(rows[5].id not in origins for origins in captured[0][1].values())


async def test_the_stage_runs_after_the_reranker(matches, reranker, monkeypatch):
    """The reported match place is the reranker's place, not the word rank."""
    me, rows = matches

    async def reverse(query, documents):
        """Prefer the last match first."""
        return list(range(len(documents)))

    monkeypatch.setattr(reranker, "score", reverse)
    baseline = await search.search_engrams(me, "car", limit=8, semantic=False)
    target = next(r for r in rows if r.path == baseline[5]["path"])
    monkeypatch.setattr(get_settings().search, "priming", True)
    hits = await search.search_engrams(
        me, "car", limit=5, semantic=False, earlier=[[target.id]]
    )
    assert hits[4]["path"] == target.path
    assert hits[4]["why"] == {"place_by_match": 6, "primed": True}


async def test_match_place_follows_the_rerankers_file_spread(
    matches, reranker, monkeypatch
):
    """The note starts from the same file spacing as the disabled stage."""
    me, rows = matches
    monkeypatch.setattr(get_settings().reading, "enabled", True)
    async with transaction_scope():
        await arm(me)
        for number in range(4):
            await Attachment(
                space_id=rows[0].space_id,
                object_key=f"file-{number}",
                filename=f"filemark-{number}.pdf",
                mime="application/pdf",
                byte_size=1,
                description="car filemark",
                status=AttachmentStatus.READY.value,
            ).save()

    async def files_first(query, documents):
        """Put all four files first, so file spreading must make room."""
        return [1000 if "filemark" in doc else doc.count("car") for doc in documents]

    monkeypatch.setattr(reranker, "score", files_first)
    before = await search.search_engrams(me, "car", limit=12, semantic=False)
    assert before[4]["kind"] == "page"
    target = next(row for row in rows if row.path == before[4]["path"])
    monkeypatch.setattr(get_settings().search, "priming", True)
    after = await search.search_engrams(
        me, "car", limit=12, semantic=False, earlier=[[target.id]]
    )
    assert after[3]["path"] == target.path
    assert after[3]["why"] == {"place_by_match": 5, "primed": True}


@pytest.mark.parametrize("route", ["words", "hybrid", "reranker"])
async def test_both_off_keep_the_original_statements_and_cut(
    matches, reranker, monkeypatch, route
):
    """Pin statements captured before wiring in the stage, on all routes."""
    me, rows = matches
    recorded = []
    original = DB.run_querystring

    async def record(engine, querystring, in_pool=True):
        """Remember each statement and its arguments before executing it."""
        sql, args = querystring.compile_string(engine_type=DB.engine_type)
        recorded.append((sql, args))
        return await original(querystring, in_pool=in_pool)

    monkeypatch.setattr(type(DB), "run_querystring", record)
    hits = await search.search_engrams(
        me, "car", limit=5, semantic=route == "hybrid", rerank=route == "reranker"
    )
    assert _paths(hits) == [r.path for r in rows[:5]]
    assert all("why" not in hit for hit in hits)
    hashes = [hashlib.sha256(sql.encode()).hexdigest() for sql, _ in recorded]
    assert hashes == BASELINE[route], {route: hashes}
    if route == "words":
        matching = [
            (sql, args)
            for sql, args in recorded
            if sql.lstrip().startswith("WITH strict AS")
        ]
        assert len(matching) == 1 and matching[0][1][-1] == 5


# Statement hashes captured before the lift was wired into search.
BASELINE = {
    "words": [
        "3fe9c87f44fb190a85d33bc8109ea8f95673dd70e498b1fd49d38390e6c92c94",
        "5f8c991bb2f4ef48db0f98164e7aac98968c3b7c4450c4705522ba6b0cfc31d0",
        "bffec0ed632965f3e21c00ec60b9f9cc89b6796b1f3fd5fc59a7abd05c5d6347",
        "1739763be5cd8330a7c232ea87b33d845d5bba0b980eb877a95bea946fe134e8",
    ],
    "hybrid": [
        "3fe9c87f44fb190a85d33bc8109ea8f95673dd70e498b1fd49d38390e6c92c94",
        "5f8c991bb2f4ef48db0f98164e7aac98968c3b7c4450c4705522ba6b0cfc31d0",
        "5fe6465c5b517cf2b27d80b57a9004f2dd22ec38aecc26fcc13f70338a1b0121",
        "2627a601b07c37e67989b4037b071d00a4ba3eb5eaef8184935cd5b49e2a8a4a",
        "3fe9c87f44fb190a85d33bc8109ea8f95673dd70e498b1fd49d38390e6c92c94",
        "1739763be5cd8330a7c232ea87b33d845d5bba0b980eb877a95bea946fe134e8",
        "04c41afe71426b6554af24a4118c9c62c312f77adac2ce6ff8e07184467a87d2",
    ],
    "reranker": [
        "3fe9c87f44fb190a85d33bc8109ea8f95673dd70e498b1fd49d38390e6c92c94",
        "5f8c991bb2f4ef48db0f98164e7aac98968c3b7c4450c4705522ba6b0cfc31d0",
        "e95bd6c4238a9b1fd1af1876ee73f93a780dd76301c2e7d898eaaaab98b11835",
        "1d3c713b551fb23263b64775ba748633a2dbb4201c937e1e2cb7816378059df3",
        "3fe9c87f44fb190a85d33bc8109ea8f95673dd70e498b1fd49d38390e6c92c94",
        "1739763be5cd8330a7c232ea87b33d845d5bba0b980eb877a95bea946fe134e8",
        "04c41afe71426b6554af24a4118c9c62c312f77adac2ce6ff8e07184467a87d2",
    ],
}


@pytest.mark.parametrize("route", ["words", "hybrid", "reranker", "meaning"])
@pytest.mark.parametrize("limit", [5, 60])
async def test_every_enabled_route_gives_the_stage_fifty_candidates(
    matches, service, reranker, monkeypatch, route, limit
):
    """The pool has the same bound, even on the old five-result route."""
    me, rows = matches
    async with transaction_scope():
        for number in range(8, 60):
            row = await save_engram(
                me, "personal", f"match-{number}.md", f"car grain{number}", message="x"
            )
            await embedding.embed_engram(me, row.id, service)
            rows.append(row)
    seen = []
    original = lifting.lift

    def record(candidates, *args, **kwargs):
        """Observe the stage's pool without changing its order."""
        seen.append(len(candidates))
        return original(candidates, *args, **kwargs)

    monkeypatch.setattr(lifting, "lift", record)
    monkeypatch.setattr(get_settings().search, "priming", True)
    monkeypatch.setattr(get_settings().search, "max_results", 100)
    hits = await search.search_engrams(
        me,
        "car" if route != "meaning" else "unmatched",
        limit=limit,
        semantic=route in ("hybrid", "meaning"),
        rerank=route == "reranker",
        earlier=[],
    )
    assert len(hits) == limit
    assert seen == [50]
    if limit > 50:
        target = next(row for row in rows if row.path == hits[-1]["path"])
        monkeypatch.setattr(get_settings().search, "priming_lift", 100.0)
        after = await search.search_engrams(
            me,
            "car" if route != "meaning" else "unmatched",
            limit=limit,
            semantic=route in ("hybrid", "meaning"),
            rerank=route == "reranker",
            earlier=[[target.id]],
        )
        assert after == hits
        assert seen == [50, 50]


async def test_a_file_keeps_its_slot_and_never_gets_a_reason(matches, monkeypatch):
    """Lifting an engram past a file does not move the file's slot."""
    me, rows = matches
    async with transaction_scope():
        await arm(me)
        await Attachment(
            space_id=rows[0].space_id,
            object_key="files/car",
            filename="car.pdf",
            mime="application/pdf",
            byte_size=1,
            description="car",
            status=AttachmentStatus.READY.value,
        ).save()
    before = await search.search_engrams(
        me, "car", limit=10, semantic=False, rerank=False
    )
    file_place = next(n for n, hit in enumerate(before) if hit["kind"] == "file")
    target = rows[-1]
    monkeypatch.setattr(get_settings().search, "priming", True)
    monkeypatch.setattr(get_settings().search, "priming_lift", 10.0)
    hits = await search.search_engrams(
        me, "car", limit=10, semantic=False, rerank=False, earlier=[[target.id]]
    )
    assert hits[file_place] == before[file_place]
    assert _paths(hits)[0] == target.path
    assert "why" not in hits[file_place]


async def test_no_session_or_disabled_log_gives_no_priming(matches, monkeypatch):
    """Scripts without history and a disabled log do not read session rows."""
    me, _ = matches
    before = await search.search_engrams(me, "car", limit=5, rerank=False)
    monkeypatch.setattr(get_settings().search, "priming", True)
    monkeypatch.setattr(usage, "current_session_id", lambda: None)
    assert await search.search_engrams(me, "car", limit=5, rerank=False) == before
    monkeypatch.setattr(usage, "current_session_id", lambda: "made-session")
    monkeypatch.setattr(get_settings().search, "log", False)
    assert await search.search_engrams(me, "car", limit=5, rerank=False) == before


async def test_empty_matches_skip_the_stage(matches, monkeypatch):
    """An empty ranking opens no stage savepoint and reads no session log."""
    me, _ = matches
    monkeypatch.setattr(get_settings().search, "priming", True)
    monkeypatch.setattr(usage, "current_session_id", lambda: "made-session")
    seen = []
    original = search._lift_order

    async def record(*args):
        """Observe the whole stage, including its savepoint and reads."""
        seen.append(args)
        return await original(*args)

    monkeypatch.setattr(search, "_lift_order", record)
    assert await search.search_engrams(me, "absent", semantic=False, rerank=False) == []
    assert seen == []


async def test_another_members_session_cannot_prime_shared_matches(
    household, monkeypatch
):
    """One shared label cannot transfer the other member's search weight."""
    me = Principal(person_id=household["wouter"].id, email=household["wouter"].email)
    partner = Principal(
        person_id=household["partner"].id, email=household["partner"].email
    )
    async with transaction_scope():
        rows = [
            await save_engram(
                me,
                "household",
                f"shared-{n}.md",
                "tram " * (8 - n) + "word " * 50,
                title=f"Note {n}",
                message="x",
            )
            for n in range(8)
        ]
    monkeypatch.setattr(get_settings().search, "log", True)
    monkeypatch.setattr(get_settings().search, "priming", True)
    monkeypatch.setattr(usage, "current_session_id", lambda: "same")
    monkeypatch.setattr(searchlog, "current_session_id", lambda: "same")
    async with transaction_scope():
        await searchlog.record(partner, "earlier", [rows[5].id])
    hits = await search.search_engrams(
        me, "tram", limit=5, semantic=False, rerank=False
    )
    assert _paths(hits) == [row.path for row in rows[:5]]
    assert all("why" not in hit for hit in hits)
    async with transaction_scope():
        await searchlog.record(me, "earlier", [rows[5].id])
    own = await search.search_engrams(me, "tram", limit=5, semantic=False, rerank=False)
    assert own[4]["path"] == rows[5].path
    assert own[4]["why"]["primed"] is True
