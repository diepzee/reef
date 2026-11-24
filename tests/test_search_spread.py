"""The block rule: at most so many files among every five results."""

import random

from reef.search import spread


def E(number: int) -> tuple[str, int]:
    """Name an engram row.

    :param number: its place among engrams
    :returns: the row
    """
    return ("page", number)


def F(number: int) -> tuple[str, int]:
    """Name a file row.

    :param number: its place among files
    :returns: the row
    """
    return ("file", number)


def is_file(row: tuple[str, int]) -> bool:
    """Tell whether a row is a file.

    :param row: a row
    :returns: True for a file
    """
    return row[0] == "file"


def test_files_over_the_limit_wait_for_the_next_block():
    """Five files in front leave two places of the first five to engrams."""
    ranking = [F(1), F(2), F(3), F(4), F(5), E(1), E(2), F(6), E(3)]
    assert spread(ranking, is_file, 3) == [
        F(1),
        F(2),
        F(3),
        E(1),
        E(2),
        F(4),
        F(5),
        F(6),
        E(3),
    ]


def test_files_fill_the_places_no_engram_is_left_for():
    """With one engram, the fifth place goes to a file: no place stays empty."""
    ranking = [F(1), F(2), F(3), F(4), E(1)]
    assert spread(ranking, is_file, 3) == [F(1), F(2), F(3), E(1), F(4)]
    only_files = [F(n) for n in range(1, 8)]
    assert spread(only_files, is_file, 3) == only_files


def test_engrams_alone_and_an_order_within_the_limit_are_unchanged():
    """Nothing moves when no block holds more files than the limit."""
    engrams = [E(n) for n in range(1, 8)]
    assert spread(engrams, is_file, 3) == engrams
    mixed = [E(1), F(1), E(2), F(2), F(3), E(3), F(4)]
    assert spread(mixed, is_file, 3) == mixed


def test_the_limits_at_both_ends():
    """Five is no limit; zero puts every engram before every file."""
    ranking = [F(1), E(1), F(2), E(2), F(3), F(4), F(5), F(6)]
    assert spread(ranking, is_file, 5) == ranking
    assert spread(ranking, is_file, 0) == [
        E(1),
        E(2),
        F(1),
        F(2),
        F(3),
        F(4),
        F(5),
        F(6),
    ]


def test_any_order_keeps_each_kind_in_order_and_settles_at_once():
    """For random orders: no row lost, each kind keeps its order, every block
    holds at most the limit of files while engrams remain, and a second pass
    changes nothing."""
    rng = random.Random(77)
    for _ in range(300):
        rows = [E(n) for n in range(rng.randint(0, 12))]
        rows += [F(n) for n in range(rng.randint(0, 30))]
        rng.shuffle(rows)
        for limit in range(6):
            out = spread(rows, is_file, limit)
            assert sorted(out) == sorted(rows)
            assert [r for r in out if not is_file(r)] == [
                r for r in rows if not is_file(r)
            ]
            assert [r for r in out if is_file(r)] == [r for r in rows if is_file(r)]
            assert spread(out, is_file, limit) == out
            for start in range(0, len(out), 5):
                block = out[start : start + 5]
                later_engram = any(not is_file(r) for r in out[start + len(block) :])
                if later_engram:
                    assert sum(map(is_file, block)) <= limit
