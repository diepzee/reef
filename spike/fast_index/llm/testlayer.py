"""A made-up structured layer on top of the benchmark, for the LLM-in-the-loop runs.

Spike code. Test data only: every name, place and fact below is invented.

The benchmark gives 523 flat conversation engrams under ``sessions/``: the
mass of detail a long-used memory accumulates. This layer adds what a real
memory also has and the benchmark lacks: a dozen curated engrams in folders,
linked to each other, which a fast index should surface as main concepts.

Run as::

    REEF_DATABASE_URL=postgresql://reef:reef@localhost:5433/reef_spike_llm_tpl \\
        .venv/bin/python spike/fast_index/llm/testlayer.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3] / "scripts" / "eval"))

import evalkit

from reef.access import Principal
from reef.config import get_settings
from reef.db import DB, transaction_scope
from reef.engrams import save_engram
from reef.identity import person_by_email

#: path -> (title, tags, body). Invented facts only.
LAYER: dict[str, tuple[str, list[str], str]] = {
    "profile.md": (
        "Profile",
        ["core"],
        """Sam Peeters, 38, product designer in Ghent. Lives with partner
[[people/ana.md]] and daughter [[people/noor.md]].

## Work
Designs the rider app at a bike-sharing start-up; manager is
[[people/mira.md]]. Current work project: [[work/design-system.md]].

## Now
- Planning [[projects/japan-trip.md]] for April 2027.
- Training for a half marathon: [[health/running.md]].
- Building a garden shed: [[projects/garden-shed.md]].
""",
    ),
    "people/ana.md": (
        "Ana",
        ["family"],
        """Ana Ruiz, Sam's partner since 2015. Ceramicist with a studio in
Sint-Amandsberg. Birthday 12 June.

- Allergic to shellfish.
- Coming on [[projects/japan-trip.md]]; wants a day in a Kyoto pottery village.
""",
    ),
    "people/noor.md": (
        "Noor",
        ["family"],
        """Noor, Sam and [[people/ana.md]]'s daughter, born 3 February 2020.
Goes to primary school De Kleine Prins, class of Juf Leen.

- Swimming lessons on Wednesday at 16:30.
- Comes on [[projects/japan-trip.md]].
""",
    ),
    "people/ben.md": (
        "Ben",
        ["friends"],
        """Ben Janssens, Sam's oldest friend from university. Lives in Antwerp
with his wife Sofie. Works as a nurse at the university hospital.

- Runs too; joined Sam for the 10 km in 2025 (see [[health/running.md]]).
- Birthday 30 September.
""",
    ),
    "people/mira.md": (
        "Mira",
        ["work"],
        """Mira Desmet, Sam's manager, head of design. Weekly one-to-one on
Tuesday morning.

- Wants the [[work/design-system.md]] tokens finished before Q1 2027.
""",
    ),
    "projects/japan-trip.md": (
        "Japan trip, April 2027",
        ["travel"],
        """Family trip to Japan, 3 to 17 April 2027: Tokyo, then Kyoto, then
Osaka. Budget 6,000 euro for three. With [[people/ana.md]] and
[[people/noor.md]].

## Booked
- Flights Brussels to Tokyo Haneda on 3 April, back from Osaka Kansai on 17 April.
- Ryokan in Kyoto, 8 to 11 April.

## Open
- Pottery village day trip for Ana (Shigaraki is the candidate).
- JR Pass or regional passes: not decided.
""",
    ),
    "projects/garden-shed.md": (
        "Garden shed",
        ["home"],
        """Building a 3 by 4 metre timber shed at the back of the garden.
No permit needed under 40 square metres in Ghent. Two quotes so far.

- Quote from Houtbouw Verhaeghe: 7,400 euro, foundation included.
- Quote from DIY kit plus a carpenter for two days: about 4,900 euro.
- Decision due by end of January 2027.
""",
    ),
    "home/car.md": (
        "Car",
        ["home"],
        """The family car: a 2019 Skoda Octavia estate, petrol, about 58,000 km.

- Insurance with AG, policy renews on 14 March each year. Premium 690 euro.
- Next service at 60,000 km, at Garage Van Hecke in Gentbrugge.
""",
    ),
    "home/house.md": (
        "House",
        ["home"],
        """Terraced house in Ghent, bought in 2018. Mortgage with KBC at 1.6 percent
fixed, ends 2043.

- Heat pump installed in 2024; service contract with Thermia every autumn.
- The garden shed is planned at the back: [[projects/garden-shed.md]].
""",
    ),
    "health/running.md": (
        "Running",
        ["health", "core"],
        """Sam is training for the Ghent half marathon on 17 May 2027, aiming
for under 1 hour 50. Plan: three runs a week, long run on Sunday.

## Knee
Right knee pain since October 2026, outside of the knee: likely IT band.
Physiotherapist Dr. Claes, Thursdays. Exercises: hip abductor strengthening,
foam roller. No downhill running until it settles.

Sessions are in [[health/running-log.md]].
""",
    ),
    "health/running-log.md": (
        "Running log",
        ["log", "health"],
        """Training sessions, newest last.

## 2026-11-08
Long run 14 km at 6:05 per km. Knee fine until km 11, then sore.

## 2026-11-12
Easy 7 km. No pain. Did the abductor exercises after.

## 2026-11-16
Long run 15 km on the flat along the Leie. Knee fine throughout.
""",
    ),
    "work/design-system.md": (
        "Design system",
        ["work"],
        """The rider app's design system: tokens, components and their
documentation. Sam leads it; [[people/mira.md]] sponsors it.

- Colour and spacing tokens done; type scale in review.
- Target: tokens complete before Q1 2027, components by summer.
""",
    ),
}


def unwrap(body: str) -> str:
    """Join hard-wrapped prose lines, as an assistant would have written them.

    The index shows an engram's first prose line, so a wrapped opening would
    show half a sentence.

    :param body: the body as written above
    :returns: the body with each paragraph and list item on one line
    """
    lines: list[str] = []
    for line in body.splitlines():
        joins = lines and line and lines[-1] and not line.startswith(("-", "#"))
        if joins and not lines[-1].startswith("#"):
            lines[-1] = f"{lines[-1]} {line}"
        else:
            lines.append(line)
    return "\n".join(lines) + "\n"


async def main() -> None:
    """Write the layer as the benchmark person, through the app."""
    dsn = get_settings().dsn
    if "reef_spike_llm" not in dsn:
        sys.exit("refused: REEF_DATABASE_URL must name a reef_spike_llm* database")
    evalkit.refuse_unless_local(dsn)
    await DB.start_connection_pool()
    try:
        identity = await person_by_email(evalkit.EVAL_EMAIL)
        principal = Principal(person_id=identity.person_id, email=identity.email)
        for path, (title, tags, body) in LAYER.items():
            async with transaction_scope():
                await save_engram(
                    principal,
                    "personal",
                    path,
                    unwrap(body),
                    message="spike test layer",
                    title=title,
                    tags=tags,
                    via=None,
                )
    finally:
        await DB.close_connection_pool()
    print(f"wrote {len(LAYER)} engrams")


if __name__ == "__main__":
    asyncio.run(main())
