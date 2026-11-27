"""Remove expired write confirmation records without reading their contents."""

from reef.models import UsedWriteToken


async def purge() -> None:
    """Delete used tokens older than a day without returning rows or a count.

    Needs no principal. Run inside the worker's own transaction scope.
    """
    await UsedWriteToken.raw("SELECT reef_purge_write_tokens()")
