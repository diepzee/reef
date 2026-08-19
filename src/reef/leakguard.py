"""Make the share ceremony the only route for personal content into a cove.

``prepare_to_share``/``confirm_share`` exists so that moving something out of
the personal space is a deliberate, disclosed act. Nothing enforced it. A
plain ``write_page`` to a shared cove carrying a body just read out of
``personal`` did the same thing in one call, with no nonce, no disclosure and
no user confirmation — which made the ceremony a convention the assistant was
asked to follow rather than a boundary the server held.

That matters most when the assistant is not acting for its user. Every
co-member of a cove controls text that lands verbatim in the victim's
``load_index`` output — page titles, tags, and the first prose line, which
becomes the description — and that is the first thing an assistant reads in
every conversation. An instruction planted there reaches a model that is
holding both ``read_page(personal, …)`` and ``write_page(<cove>, …)`` at the
same time. The gap is not that the model can be persuaded; it is that being
persuaded was sufficient.

So this module removes the sufficiency. A write to a shared cove is checked
against the caller's own personal pages, and a substantial verbatim run in
common is refused with a pointer to the ceremony.

**What this is not.** It matches text, so it stops copying and does not stop
paraphrasing or summarising. A determined attacker who instructs the model to
reword defeats it. It is worth having anyway: the realistic injected
exfiltration is a copy, the check is cheap, and the failure mode is a refusal
that names the sanctioned path rather than a silent leak. Treated as a proof
it would be dangerous; treated as one layer it closes the easy route and
leaves consent as the only wide one.

The comparison is deliberately against **new** text only. Re-saving a page
that already carries shared content must not start failing because the same
words also exist somewhere in the personal space, so the shingles already
present in the stored page are subtracted before anything is judged. Only
text this write introduces can trip the guard.
"""

import re
from hashlib import blake2b

_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)

SHINGLE_WORDS = 12
"""Words in a matched run.

A floor, not a preference. Long enough that ordinary phrasing does not
collide by chance -- twelve words identical across two documents is rarely a
coincidence -- and short enough to catch a copied paragraph rather than only
a copied page. Below roughly eight it starts refusing people quoting
themselves; far above it, the check only fires on wholesale duplication and
misses the passage-sized leak that is the realistic one.

A fact shorter than this cannot be matched at all, which is a real limit and
is stated here rather than hidden: the guard raises the cost of bulk copying,
it does not make a short private sentence unleakable.
"""


def _tokens(text: str) -> list[str]:
    """Reduce text to comparable word tokens.

    Case, punctuation and whitespace are discarded so that reformatting --
    which an assistant does routinely, and which is not evasion -- does not
    slip past, while leaving genuine rewording (different words) unmatched.

    :param text: the text to tokenize
    :returns: lowercase word tokens in order
    """
    return [match.group(0).lower() for match in _WORD_RE.finditer(text)]


def shingles(text: str, *, size: int = SHINGLE_WORDS) -> set[bytes]:
    """Return hashed overlapping word runs of ``size`` words.

    Hashed rather than kept whole so that the set holds no readable content:
    these are computed from personal pages and compared against a cove write,
    and a bug that logged or returned one should not spill a private
    sentence. The digest is short because a collision here costs a false
    refusal, not a leak.

    :param text: the text to shingle
    :param size: words per run
    :returns: the set of hashed runs, empty when the text is shorter
    """
    words = _tokens(text)
    if len(words) < size:
        return set()
    return {
        blake2b(" ".join(words[index : index + size]).encode(), digest_size=16).digest()
        for index in range(len(words) - size + 1)
    }


def overlaps(new_body: str, previous_body: str, private_bodies: list[str]) -> bool:
    """Report whether this write introduces text copied from private pages.

    :param new_body: the body about to be written to a shared cove
    :param previous_body: what that page holds now; its runs are exempt, so
        re-saving already-shared text never trips the guard
    :param private_bodies: the caller's own personal page bodies
    :returns: True if a run of :data:`SHINGLE_WORDS` words is both new here
        and present in a personal page
    """
    introduced = shingles(new_body) - shingles(previous_body)
    if not introduced:
        return False
    for body in private_bodies:
        if introduced & shingles(body):
            return True
    return False
