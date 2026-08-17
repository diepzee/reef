"""What changed across the caller's spaces: the awareness surface.

A shared space only works if its members can see what the others'
assistants wrote — otherwise the cove drifts while everyone assumes it
stands still. Everything here reads under the same armed RLS session as
any other read, so activity in somebody else's personal space simply does
not come back.
"""

from datetime import UTC, datetime, timedelta

from rif.access import Principal, alias_map
from rif.models import Attachment, AttachmentStatus, Revision
from rif.spaces import member_roster

_DEFAULT_WINDOW = timedelta(days=7)

_MAX_EVENTS = 100


def _naive_local(moment: datetime) -> datetime:
    """Convert a possibly-aware moment to the naive local time rows store.

    :param moment: the moment to normalize
    :returns: the same instant, naive, in local time
    """
    if moment.tzinfo is not None:
        return moment.astimezone().replace(tzinfo=None)
    return moment


async def whats_new(
    principal: Principal,
    since: datetime | None = None,
    limit: int = _MAX_EVENTS,
) -> list[dict]:
    """List page writes and file arrivals since a moment, newest first.

    Page events carry the author's display name and the write message —
    the same accountability the revision history stores. File events carry
    no author, because attachments do not record one. An author whose
    person row is no longer visible reads as None rather than a guess.

    :param principal: the authenticated person
    :param since: include events after this moment; the last 7 days if None
    :param limit: maximum events returned
    :returns: one dict per event, newest first
    """
    aliases = await alias_map(principal)
    cutoff = _naive_local(
        since if since is not None else datetime.now(UTC) - _DEFAULT_WINDOW
    )
    revisions = await Revision.select(
        Revision.path,
        Revision.title,
        Revision.message,
        Revision.created_at,
        Revision.author_id,
        Revision.page_id.space_id,
    ).where(Revision.created_at > cutoff)
    files = await Attachment.select(
        Attachment.space_id,
        Attachment.object_key,
        Attachment.filename,
        Attachment.created_at,
    ).where(
        Attachment.created_at > cutoff,
        Attachment.status == AttachmentStatus.READY.value,
    )
    # Direct person reads are RLS-scoped to the caller's own row; co-member
    # names come from the roster functions, per space, like every other
    # surface that shows who is in the room.
    names: dict = {}
    for space_id in {r["page_id.space_id"] for r in revisions}:
        for member in await member_roster(space_id):
            names[member["person_id"]] = member["display_name"]
    events = [
        {
            "kind": "page",
            "space": aliases[r["page_id.space_id"]],
            "path": r["path"],
            "title": r["title"],
            "message": r["message"],
            "author": names.get(r["author_id"]),
            "at": r["created_at"],
        }
        for r in revisions
    ] + [
        {
            "kind": "file",
            "space": aliases[f["space_id"]],
            "key": f["object_key"],
            "filename": f["filename"],
            "author": None,
            "at": f["created_at"],
        }
        for f in files
    ]
    events.sort(key=lambda e: e["at"], reverse=True)
    return [{**e, "at": e["at"].isoformat()} for e in events[:limit]]
