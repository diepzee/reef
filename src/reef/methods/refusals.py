"""Refusals: what the storage layer says no to, as a :class:`MethodError`.

Shared by the methods that write: :mod:`reef.methods.capture` and
:mod:`reef.methods.maintain`. The storage layer's own messages are not passed
on: some name the caller's address, and some name tools that are gone.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from reef.access import AccessDenied, ReadOnlyMembership
from reef.engrams import (
    EngramTooLarge,
    InvalidPath,
    PrivateContentLeak,
    ProtectedPath,
    VersionConflict,
)
from reef.methods import MESSAGES, MethodError
from reef.promotion import PromotionError

#: The code for each refusal of an engram write.
ENGRAM_REFUSALS: dict[type[Exception], str] = {
    VersionConflict: "version_conflict",
    ProtectedPath: "protected_path",
    InvalidPath: "invalid_path",
    EngramTooLarge: "engram_too_large",
    PrivateContentLeak: "private_content",
}


@contextmanager
def refusals(path: str | None = None) -> Iterator[None]:
    """Turn what the storage layer refuses into a :class:`MethodError`.

    A read-only membership is told apart from an unknown space, which it is
    a kind of. The original error is not chained: its message may name the
    caller's address.

    :param path: the engram being written, named in the message of a
        refusal about it
    :raises MethodError: for each refusal, with its code and plain message
    """
    try:
        yield
    except ReadOnlyMembership:
        raise MethodError("read_only") from None
    except AccessDenied:
        raise MethodError("unknown_space") from None
    except PromotionError as error:
        raise MethodError(error.code) from None
    except tuple(ENGRAM_REFUSALS) as error:
        code = ENGRAM_REFUSALS[type(error)]
        message = MESSAGES[code] if path is None else f"{path}: {MESSAGES[code]}"
        raise MethodError(code, message) from None
