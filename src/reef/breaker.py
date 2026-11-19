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

    def ready(self) -> bool:
        """Say whether the service may be asked now.

        After a rest, the first caller gets a yes and the others a no until
        that caller's answer is in or its time is up.

        :returns: True if the caller may ask the service
        """
        now = time.monotonic()
        if now < self.resting_until:
            return False
        if self.failures >= self.threshold:
            self.resting_until = now + self.probe_seconds
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
