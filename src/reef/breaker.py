"""Leave a failing service alone for a while, so searches do not wait on it.

Search asks two optional services for help: the embedder and the reranker.
Each answer has a time limit, so a slow service costs a search that limit
and no more. But a service that is down would cost *every* search its
limit. A :class:`Breaker` stops that: after a few failures in a row it
answers "not now" at once, for a while, and then lets one search try again.

One person's odd query must not switch a service off for everybody, so the
caller decides what counts as a failure. A refused question does not.

A breaker lives in one process and is not shared between processes. Its
state is two numbers, changed only between awaits, so concurrent searches
on the event loop cannot tear it.
"""

import time

import httpx

#: Answers that say something about one request, not about the service: a
#: query too long, a body the service will not take. Every other status,
#: a redirect, 401, 404 or 429 among them, says the service is unusable
#: as configured or overloaded, and counts as a failure.
REFUSALS = frozenset({400, 413, 422})


def is_refusal(error: BaseException) -> bool:
    """Say whether an error is the service refusing one request.

    :param error: what was raised
    :returns: True for an HTTP answer in :data:`REFUSALS`
    """
    return (
        isinstance(error, httpx.HTTPStatusError)
        and error.response.status_code in REFUSALS
    )


def describe(error: BaseException) -> str:
    """Name an error for the log, without anything it carries.

    :param error: what was raised
    :returns: its class, and the HTTP status if it has one
    """
    if isinstance(error, httpx.HTTPStatusError):
        return f"{type(error).__name__} {error.response.status_code}"
    return type(error).__name__


class Breaker:
    """Counts failures in a row, and rests the service after too many."""

    def __init__(
        self, failures: int = 3, rest_seconds: float = 60.0, probe_seconds: float = 2.0
    ) -> None:
        """Set the thresholds.

        :param failures: how many failures in a row start a rest
        :param rest_seconds: how long a rest lasts
        :param probe_seconds: how long the one search that tries again after
            a rest has, before others may try too. Set it to the service's
            time limit.
        """
        self.threshold = failures
        self.rest_seconds = rest_seconds
        self.probe_seconds = probe_seconds
        self.failures = 0
        self.resting_until = 0.0

    def resting(self) -> bool:
        """Say whether the service is being left alone, without changing that.

        :returns: True while a rest or another caller's trial lasts
        """
        return time.monotonic() < self.resting_until

    def ready(self, probe_seconds: float | None = None) -> bool:
        """Say whether the service may be asked now, and claim the trial.

        After a rest, the first caller gets a yes and the others a no until
        that caller's answer is in or its time is up. Call it only right
        before asking: a claimed trial that is not used leaves the service
        alone for the length of the trial.

        :param probe_seconds: how long the trial may take, if longer than
            the breaker's own
        :returns: True if the caller may ask the service
        """
        now = time.monotonic()
        if now < self.resting_until:
            return False
        if self.failures >= self.threshold:
            self.resting_until = now + max(self.probe_seconds, probe_seconds or 0.0)
        return True

    def failed(self) -> None:
        """Count a failure of the service itself."""
        self.failures += 1
        if self.failures >= self.threshold:
            self.resting_until = time.monotonic() + self.rest_seconds

    def succeeded(self) -> None:
        """Count a good answer: the service is well."""
        self.failures = 0
        self.resting_until = 0.0
