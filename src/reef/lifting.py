"""Lift matching engrams using this session and readable links.

All inputs are plain values. Nothing is read or stored here.
"""

from dataclasses import dataclass
from uuid import UUID

PRIMED_PER_SEARCH = 3
PRIMED_ENGRAMS = 10
PRIMING_MINUTES = 60
SPREAD_SOURCES = 20
CANDIDATES = 50
NEWEST_WEIGHT = 0.5
PASSED_ON = 0.5
LINKED_FROM = 3


def primed_weights(earlier: list[list[UUID]]) -> dict[UUID, float]:
    """Count earlier results, halving weight for each older search.

    :param earlier: searches newest first, each with its best engrams first
    :returns: weight for at most ten distinct engrams
    """
    weights: dict[UUID, float] = {}
    weight = NEWEST_WEIGHT
    for ids in earlier:
        for engram_id in dict.fromkeys(ids[:PRIMED_PER_SEARCH]):
            if engram_id in weights or len(weights) < PRIMED_ENGRAMS:
                weights[engram_id] = weights.get(engram_id, 0.0) + weight
        weight *= 0.5
    return weights


def linked_weights(
    sources: dict[UUID, float],
    neighbours: dict[UUID, list[UUID]],
    closed: set[UUID],
) -> tuple[dict[UUID, float], dict[UUID, dict[UUID, float]]]:
    """Pass half the weight along links twice, keeping the first source.

    :param sources: starting weight for each source
    :param neighbours: readable neighbours in both directions
    :param closed: replaced engrams, which receive and pass nothing
    :returns: received weights and their contributions by original source
    """
    origins: dict[UUID, dict[UUID, float]] = {}
    for source, weight in sources.items():
        if source in closed:
            continue
        current = {source: weight}
        for _ in range(2):
            following: dict[UUID, float] = {}
            for sender, amount in current.items():
                targets = list(
                    dict.fromkeys(
                        target
                        for target in neighbours.get(sender, [])
                        if target not in closed
                    )
                )
                if not targets:
                    continue
                share = amount * PASSED_ON / len(targets)
                for target in targets:
                    if target == source:
                        continue
                    following[target] = following.get(target, 0.0) + share
                    by_source = origins.setdefault(target, {})
                    by_source[source] = by_source.get(source, 0.0) + share
            current = following
    return {target: sum(parts.values()) for target, parts in origins.items()}, origins


@dataclass(frozen=True)
class Why:
    """The match place and the sources of weight for an engram that rose."""

    place_by_match: int
    primed: bool
    linked_from: tuple[UUID, ...]


def lift(
    candidates: list[tuple[str, UUID]],
    primed: dict[UUID, float],
    linked: dict[UUID, float],
    origins: dict[UUID, dict[UUID, float]],
    *,
    priming_lift: float,
    spreading_lift: float,
) -> tuple[list[tuple[str, UUID]], dict[UUID, Why]]:
    """Sort engrams by a bounded lift while keeping every file in its slot.

    :param candidates: matching rows, best first
    :param primed: weight from earlier searches
    :param linked: weight received along links
    :param origins: contributions by the first source
    :param priming_lift: maximum factor added by priming
    :param spreading_lift: maximum factor added by spreading
    :returns: the new order and notes only for engrams that rose
    """
    places = {
        row_id: place
        for place, (kind, row_id) in enumerate(candidates, 1)
        if kind == "page"
    }

    def score(row_id: UUID) -> float:
        """Return a candidate's score from its place and its two shares.

        :param row_id: the engram
        :returns: the score used for a stable sort
        """
        primed_share = min(1.0, primed.get(row_id, 0.0) / NEWEST_WEIGHT)
        linked_share = min(1.0, linked.get(row_id, 0.0) / PASSED_ON)
        return (
            (1 / places[row_id])
            * (1 + priming_lift * primed_share)
            * (1 + spreading_lift * linked_share)
        )

    ordered = iter(sorted(places, key=score, reverse=True))
    ranking = [
        (kind, next(ordered)) if kind == "page" else (kind, row_id)
        for kind, row_id in candidates
    ]
    notes = {}
    for place, (kind, row_id) in enumerate(ranking, 1):
        if kind != "page" or place >= places[row_id]:
            continue
        if not (primed.get(row_id, 0.0) or linked.get(row_id, 0.0)):
            continue
        parts = origins.get(row_id, {})
        notes[row_id] = Why(
            place_by_match=places[row_id],
            primed=primed.get(row_id, 0.0) > 0,
            linked_from=tuple(sorted(parts, key=parts.get, reverse=True)[:LINKED_FROM]),
        )
    return ranking, notes
