"""Front matter keeps every line that is not a relation."""

import random

import pytest

from reef.models import LinkKind
from reef.relations import InvalidRelations, take_block


@pytest.mark.parametrize(
    ("body", "plain", "given"),
    [
        (
            "---\nreplaces: [[a.md]]\n---\n# T\n",
            "# T\n",
            {LinkKind.SUPERSEDES: ("a.md",)},
        ),
        (
            "---\nreplaces: [[a.md]], [[fam:b.md]]\n---\nx",
            "x",
            {LinkKind.SUPERSEDES: ("a.md", "fam:b.md")},
        ),
        (
            "---\nabout:\n  - [[a.md]]\n  - [[b.md]]\n---\nx",
            "x",
            {LinkKind.ABOUT: ("a.md", "b.md")},
        ),
        (
            "---\ntitle: T\nreplaces: [[a.md]]\ntags: [x]\n---\nx",
            "---\ntitle: T\ntags: [x]\n---\nx",
            {LinkKind.SUPERSEDES: ("a.md",)},
        ),
        ("---\nreplaces:\n---\nx", "x", {LinkKind.SUPERSEDES: ()}),
        ("---\ntitle: T\n---\nx", "---\ntitle: T\n---\nx", {}),
        (
            "# T\n\n---\nreplaces: [[a.md]]\n---\n",
            "# T\n\n---\nreplaces: [[a.md]]\n---\n",
            {},
        ),
        ("---\n\nA rule, then text.\n", "---\n\nA rule, then text.\n", {}),
        (
            "---\r\nreplaces: [[a.md]]\r\n---\r\nx",
            "x",
            {LinkKind.SUPERSEDES: ("a.md",)},
        ),
        (
            "---\nreplaces: [[a.md]], [[a.md]]\n---\nx",
            "x",
            {LinkKind.SUPERSEDES: ("a.md",)},
        ),
    ],
)
def test_take_block(body: str, plain: str, given: dict) -> None:
    """Remove only the recognized key lines.

    :param body: the supplied Markdown
    :param plain: the expected stored Markdown
    :param given: the expected relations
    """
    actual, relations = take_block(body)
    assert actual == plain
    assert relations.given == given
    assert relations.empty == (not given)


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("a.md", "relation_invalid"),
        ("[[a.md]] and more", "relation_invalid"),
        (", ".join(f"[[{n}.md]]" for n in range(21)), "relation_too_many"),
    ],
)
def test_bad_relation(value: str, code: str) -> None:
    """Refuse malformed or oversized lists.

    :param value: the written value
    :param code: the refusal code
    """
    with pytest.raises(InvalidRelations) as caught:
        take_block(f"---\npart_of: {value}\n---\nx")
    assert caught.value.code == code


def test_unrecognized_bodies_are_unchanged() -> None:
    """Keep 2,000 generated bodies without recognized keys byte for byte."""
    rng = random.Random(36)
    lines = [
        "title: T",
        "",
        "# About",
        "A rule, then text.",
        "  about: x",
        "[[a.md]]",
        "tags: [x]",
    ]
    for number in range(2000):
        newline = rng.choice(["\n", "\r\n"])
        middle = newline.join(rng.choices(lines, k=rng.randrange(1, 12)))
        body = (
            f"---{newline}{middle}{newline}---{newline}tail" if number % 2 else middle
        )
        plain, relations = take_block(body)
        assert plain == body
        assert relations.empty


@pytest.mark.parametrize("item", ["", "[[a.md]], [[b.md]]"])
def test_each_list_item_names_exactly_one_link(item: str) -> None:
    """An empty or multi-link list item is not the promised list syntax.

    :param item: the text after an indented list marker
    """
    with pytest.raises(InvalidRelations) as caught:
        take_block(f"---\nabout:\n  - {item}\n---\nx")
    assert caught.value.code == "relation_invalid"
