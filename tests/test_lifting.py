"""The weights and bounded lift of search matches."""

from uuid import uuid4

import pytest

from reef.lifting import Why, lift, linked_weights, primed_weights


def test_newest_search_counts_half_and_each_one_back_half_of_that():
    """Newest search counts half and each one back half of that."""
    a, b, c, d = (uuid4() for _ in range(4))
    weights = primed_weights([[a, b], [b, c], [d]])
    assert weights == {a: 0.5, b: 0.75, c: 0.25, d: 0.125}


def test_only_the_first_three_of_a_search_and_ten_engrams_in_all():
    """Only the first three of a search and ten engrams in all."""
    ids = [uuid4() for _ in range(20)]
    weights = primed_weights([ids[0:5], ids[5:10], ids[10:15], ids[15:20]])
    assert set(weights) == set(ids[0:3] + ids[5:8] + ids[10:13] + ids[15:16])


def test_a_source_shares_half_its_weight_among_its_neighbours():
    """A source shares half its weight among its neighbours."""
    a, b, c = uuid4(), uuid4(), uuid4()
    received, origins = linked_weights({a: 1.0}, {a: [b, c]}, set())
    assert received[b] == received[c] == 0.25
    assert origins[b] == {a: 0.25}


def test_the_second_round_passes_half_of_what_was_received():
    """The second round passes half of what was received."""
    a, b, c = uuid4(), uuid4(), uuid4()
    received, origins = linked_weights({a: 1.0}, {a: [b], b: [a, c]}, set())
    assert received[b] == 0.5
    assert received[c] == 0.125  # half of 0.5, shared with a
    assert origins[c] == {a: 0.125}  # traced back to the first source


def test_a_source_gets_nothing_back_from_itself():
    """A source gets nothing back from itself."""
    a, b = uuid4(), uuid4()
    received, _ = linked_weights({a: 1.0}, {a: [b], b: [a]}, set())
    assert a not in received


def test_a_replaced_engram_receives_nothing_and_passes_nothing():
    """A replaced engram receives nothing and passes nothing."""
    a, old, c = uuid4(), uuid4(), uuid4()
    received, _ = linked_weights({a: 1.0}, {a: [old], old: [c]}, {old})
    assert old not in received and c not in received


def test_full_lift_of_point_three_moves_the_sixth_to_fifth_and_no_further():
    """Full lift of point three moves the sixth to fifth and no further."""
    ids = [uuid4() for _ in range(8)]
    order, why = lift(
        [("page", i) for i in ids],
        {ids[5]: 0.5},
        {},
        {},
        priming_lift=0.3,
        spreading_lift=0.3,
    )
    assert [i for _, i in order].index(ids[5]) == 4
    assert why[ids[5]] == Why(place_by_match=6, primed=True, linked_from=())
    assert ids[4] not in why  # pushed down by one: no note
    assert ids[0] not in why


def test_at_point_three_the_seventh_stays_out_of_the_first_five():
    """At point three the seventh stays out of the first five."""
    ids = [uuid4() for _ in range(8)]
    order, _ = lift(
        [("page", i) for i in ids],
        {ids[6]: 0.5},
        {},
        {},
        priming_lift=0.3,
        spreading_lift=0.3,
    )
    assert [i for _, i in order].index(ids[6]) >= 5


def test_shares_use_fixed_references_and_are_capped():
    """A large sum is capped and a small weight keeps its smaller share."""
    ids = [uuid4() for _ in range(10)]
    order, _ = lift(
        [("page", i) for i in ids],
        {},
        {ids[8]: 1.0, ids[9]: 0.1},
        {},
        priming_lift=0.3,
        spreading_lift=1.0,
    )
    assert [i for _, i in order].index(ids[8]) == 4  # 1/9 * 2 beats 1/5
    assert [i for _, i in order].index(ids[9]) == 9  # 1/10 * 1.2 stays last


@pytest.mark.parametrize("kind", ["primed", "linked"])
@pytest.mark.parametrize("weight,place", [(0.125, 4), (0.01, 5), (2.0, 3)])
def test_absolute_shares_keep_decay_and_cap_sums(kind, weight, place):
    """A lone small weight gets only its share of the allowed lift."""
    ids = [uuid4() for _ in range(6)]
    weights = {ids[5]: weight}
    order, _ = lift(
        [("page", i) for i in ids],
        weights if kind == "primed" else {},
        weights if kind == "linked" else {},
        {},
        priming_lift=1.0,
        spreading_lift=1.0,
    )
    assert [i for _, i in order].index(ids[5]) == place


def test_files_keep_their_place_among_candidates():
    """Files keep their place among candidates."""
    f, a, b = uuid4(), uuid4(), uuid4()
    order, why = lift(
        [("file", f), ("page", a), ("page", b)],
        {f: 0.5},
        {f: 1.0},
        {},
        priming_lift=1.0,
        spreading_lift=1.0,
    )
    assert order == [("file", f), ("page", a), ("page", b)] and why == {}


def test_no_weight_means_the_same_order_and_no_notes():
    """No weight means the same order and no notes."""
    ids = [uuid4() for _ in range(5)]
    order, why = lift(
        [("page", i) for i in ids], {}, {}, {}, priming_lift=0.3, spreading_lift=0.3
    )
    assert order == [("page", i) for i in ids] and why == {}


def test_linked_from_names_the_three_strongest_sources_strongest_first():
    """Linked from names the three strongest sources strongest first."""
    target, s1, s2, s3, s4 = (uuid4() for _ in range(5))
    origins = {target: {s1: 0.1, s2: 0.4, s3: 0.3, s4: 0.2}}
    ids = [uuid4() for _ in range(5)] + [target]
    _, why = lift(
        [("page", i) for i in ids],
        {},
        {target: 1.0},
        origins,
        priming_lift=0.3,
        spreading_lift=0.3,
    )
    assert why[target].linked_from == (s2, s3, s4)


def test_repeats_do_not_use_a_place_in_the_ten():
    """Repeated results add weight without pushing another engram out."""
    ids = [uuid4() for _ in range(11)]
    weights = primed_weights([[ids[0]], ids[:3], ids[3:6], ids[6:9], ids[9:]])
    assert len(weights) == 10
    assert weights[ids[0]] == 0.75
    assert ids[9] in weights and ids[10] not in weights


def test_files_keep_their_slots_when_an_engram_crosses_them():
    """Only the engram slots are filled in score order."""
    a, f, b = (uuid4() for _ in range(3))
    order, why = lift(
        [("page", a), ("file", f), ("page", b)],
        {b: 1},
        {},
        {},
        priming_lift=3,
        spreading_lift=0,
    )
    assert order == [("page", b), ("file", f), ("page", a)]
    assert why[b].place_by_match == 3


def test_non_candidates_neither_enter_nor_set_the_largest_weight():
    """A change of subject cannot bring an old result into the candidates."""
    ids = [uuid4() for _ in range(7)]
    order, why = lift(
        [("page", i) for i in ids[:6]],
        {ids[5]: 0.5, ids[6]: 100},
        {},
        {},
        priming_lift=0.3,
        spreading_lift=0.3,
    )
    assert order[4] == ("page", ids[5])
    assert ("page", ids[6]) not in order
    assert set(why) == {ids[5]}


def test_ties_keep_match_order_and_zero_lifts_make_no_notes():
    """Equal scores and disabled lifts do not swap matches."""
    a, b = uuid4(), uuid4()
    candidates = [("page", a), ("page", b)]
    for amount in (0, 1):
        assert lift(
            candidates, {b: 1}, {}, {}, priming_lift=amount, spreading_lift=0
        ) == (candidates, {})


def test_spreading_stops_after_two_rounds_and_closed_sources_pass_nothing():
    """A third hop and a replaced starting source receive no weight."""
    a, b, c, d = (uuid4() for _ in range(4))
    neighbours = {a: [b], b: [c], c: [d]}
    weights, _ = linked_weights({a: 1}, neighbours, set())
    assert weights == {b: 0.5, c: 0.25}
    assert linked_weights({a: 1}, neighbours, {a}) == ({}, {})
