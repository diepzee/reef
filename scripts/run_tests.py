"""Run pytest, one worktree at a time.

Every checkout of this repository points at the same Postgres and the same
``rif_test`` database, and ``tests/conftest.py``'s session-scoped ``schema``
fixture drops and recreates the RLS helper functions *globally*. Two suites
running at once therefore rebuild the schema under each other, and the second
one reports failures that have nothing to do with the code under test:
``UndefinedFunctionError: function reef_person_bind(text, text) does not
exist``, duplicate keys on ``persons_email_key``, foreign-key violations on
``spaces_owner_person_id_fkey``, deadlocks, connection timeouts. Dozens at
once, in tests nobody touched, and green again on the next run.

That failure is indistinguishable from a real regression until you notice it
is *wholesale* rather than one assertion, which costs an investigation every
time. A lock is cheaper: the second suite waits a few seconds and then runs
against a schema nobody else is rebuilding.

The lock is a file, held for as long as this process lives and released by
the kernel however it dies -- including a ``kill -9``, which a lock stored in
the database or in a sentinel file would survive. It is advisory and applies
only to runs started through here; a bare ``pytest`` still races, which is
why the Justfile routes ``just test-py`` through this.

CI is unaffected: one runner, its own database, and the lock is uncontended.
"""

import fcntl
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

#: One lock for every worktree on this machine. In the temp directory rather
#: than the repository, because each worktree has its own checkout and a
#: repository-local path would give each of them a private lock -- which is
#: exactly the thing that does not work.
LOCK_PATH = Path(tempfile.gettempdir()) / "rif-test-suite.lock"


def main() -> int:
    """Wait for the test database to be free, then run pytest.

    :returns: pytest's exit code
    """
    # Opened, never truncated: another process may be holding a lock on this
    # same inode right now, and "w" would empty it under them.
    handle = os.open(LOCK_PATH, os.O_RDWR | os.O_CREAT, 0o644)
    started = time.monotonic()
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        # Said out loud, and on stderr so it cannot be mistaken for pytest's
        # own output: an unexplained pause here reads as a hang, and the next
        # move would be to kill it and run the bare pytest that races.
        print(
            f"Another worktree is running the suite against {LOCK_PATH.name}; "
            "waiting for it to finish.",
            file=sys.stderr,
            flush=True,
        )
        fcntl.flock(handle, fcntl.LOCK_EX)
        print(
            f"Waited {time.monotonic() - started:.0f}s; starting.",
            file=sys.stderr,
            flush=True,
        )

    try:
        return subprocess.call([sys.executable, "-m", "pytest", *sys.argv[1:]])
    finally:
        # Explicit, though closing the descriptor would do it: the lock must
        # outlive pytest and nothing else in this file may be tempted to
        # release it earlier.
        fcntl.flock(handle, fcntl.LOCK_UN)
        os.close(handle)


if __name__ == "__main__":
    raise SystemExit(main())
