"""The launch exception: letting strangers in, briefly and countably.

reef is invitation-only (:mod:`rif.auth`), and that is the steady state. This
module is the deliberate, time-boxed hole in it: for a launch, somebody who
arrives cold can admit themselves rather than meeting a 403 they have no way
past.

Two things are worth being blunt about.

**This is open signup with a button in front of it.** The person proves their
address to AuthKit before the button is reachable, so no code, token, or link
adds anything on top of that. Naming it an invite would be theatre, and the
version that can be reasoned about at three in the morning is the one named
for what it does.

**It is built to close itself.** A launch spike peaks while the person who
could flip a switch is asleep, so the limits are the kind that hold without
supervision: a seat count that stops selling, and a date that passes. Neither
needs anybody to be awake, and neither survives being forgotten.

The whole exception lives in this file and one column, so that removing it is
a deletion rather than an excavation.
"""

from dataclasses import dataclass
from datetime import date

from rif.config import get_settings
from rif.identity import IdentityRow, _one
from rif.models import Person


@dataclass(frozen=True)
class DoorPolicy:
    """Whether the door admits anybody right now, and why not when it doesn't.

    ``reason`` is for the operator, never the visitor. A door that is shut
    because of a typo in a date looks exactly like one shut on purpose, and
    the difference is the sort of thing worth finding in a log on launch day
    rather than by reading this file.
    """

    is_open: bool
    seats: int
    reason: str


def door_policy(today: date | None = None) -> DoorPolicy:
    """Return whether the open door is admitting, from the environment.

    Both settings must be present. Requiring both is the fail-closed choice:
    a missing one means the door never opens, which is loud and gets noticed
    on launch day, where the opposite failure -- a door left open because a
    boolean was never flipped back -- is silent and gets noticed in December.

    :param today: clock override for tests; defaults to the local date
    :returns: the policy, carrying the seat ceiling when open
    """
    settings = get_settings()
    seats = settings.open_seats
    until = settings.open_until.strip()
    if seats <= 0:
        return DoorPolicy(False, 0, "RIF_OPEN_SEATS is unset or zero")
    if not until:
        return DoorPolicy(False, 0, "RIF_OPEN_UNTIL is unset")
    try:
        closes = date.fromisoformat(until)
    except ValueError:
        # A misspelled date must not read as "no limit". Failing closed here
        # costs a launch morning; failing open costs an unbounded one.
        return DoorPolicy(False, 0, f"RIF_OPEN_UNTIL is not a YYYY-MM-DD date: {until}")
    # Inclusive: the door closes when that day ends, so an operator setting
    # today's date gets today, which is what naming a day means.
    if (today or date.today()) > closes:  # noqa: DTZ011
        return DoorPolicy(False, 0, f"the open door closed after {closes.isoformat()}")
    return DoorPolicy(True, seats, "")


async def admit(email: str, subject: str, display_name: str) -> IdentityRow | None:
    """Admit a verified stranger against the seat count, or refuse.

    The mirror image of :func:`rif.invitations.allowlist`, which refuses to
    run unarmed because nobody would be accountable for the row. Here nobody
    *is* accountable, by design, and the ``joined_open_door`` flag is what
    keeps those rows distinguishable from the founding person's -- who also
    has no inviter -- and countable against the ceiling.

    Minting and binding happen together, so the row never exists in an
    unbound state and no half-made account is left sitting on the allowlist.

    :param email: the verified address from the OIDC claims
    :param subject: the provider's durable subject claim
    :param display_name: how members will see them
    :returns: the admitted identity, or None if the door is shut or full
    """
    policy = door_policy()
    if not policy.is_open:
        return None
    return _one(
        await Person.raw(
            "SELECT * FROM rif_open_door_admit({}, {}, {}, {})",
            email.strip().lower(),
            subject,
            display_name,
            policy.seats,
        )
    )
