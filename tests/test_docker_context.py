"""The image carries every file the project metadata says it needs, and the
reader's sandbox: Tesseract, the ``reader`` user, and a root worker.

CI's ``image`` job builds the image now; these checks run with the tests,
in seconds, and name the line that is wrong. Before that job, nothing in CI
built this image: Railway was the first thing to run the Dockerfile, *after*
the merge. So a file that `uv build` found on a laptop but the build context
lacked did not fail a check; it failed production, and kept failing for
every merge after it.

That is not hypothetical. Declaring `license-files = ["LICENSE"]` without
adding LICENSE to a COPY line broke four consecutive deploys, because the
laptop had the file and the image did not.
"""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "Dockerfile"


def copied_from_repo() -> set[str]:
    """Every repository path the Dockerfile copies into the image.

    ``COPY --from=<stage>`` lines are skipped: they move files between build
    stages and say nothing about what the repository must provide.

    :returns: the source paths, relative to the repository root
    """
    sources: set[str] = set()
    for line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("COPY "):
            continue
        arguments = stripped.split()[1:]
        if any(argument.startswith("--from=") for argument in arguments):
            continue
        # The last token is the destination inside the image.
        sources.update(arguments[:-1])
    return sources


def is_copied(relative: str, sources: set[str]) -> bool:
    """Whether ``relative`` reaches the image, named or under a copied directory."""
    if relative in sources:
        return True
    return any(relative.startswith(f"{source.rstrip('/')}/") for source in sources)


def test_declared_license_files_reach_the_image():
    """`uv sync` builds the project in the image, and reads this metadata."""
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = metadata["project"]["license-files"]
    assert patterns, "the root project should declare its licence"
    sources = copied_from_repo()
    for pattern in patterns:
        matches = list(ROOT.glob(pattern))
        assert matches, f"license-files pattern {pattern!r} matches nothing"
        for match in matches:
            relative = match.relative_to(ROOT).as_posix()
            assert is_copied(relative, sources), (
                f"{relative} is declared in license-files but no COPY line "
                "puts it in the image; uv sync will fail the build"
            )


def _instructions() -> str:
    """The Dockerfile with its continued lines joined.

    :returns: the text, one instruction per line
    """
    return DOCKERFILE.read_text(encoding="utf-8").replace("\\\n", " ")


def test_the_image_installs_tesseract_and_the_reader_user():
    """Scans are read by Tesseract in Dutch, French and English, as ``reader``."""
    text = _instructions()
    words = set(text.split())
    for package in ("tesseract-ocr", "tesseract-ocr-nld", "tesseract-ocr-fra"):
        assert package in words
    assert re.search(
        r"useradd --system --no-create-home --shell /usr/sbin/nologin reader\b", text
    )


def test_the_image_runs_as_root():
    """The worker runs as root, so it can hand each file to ``reader``.

    A worker that is not root runs the child as itself (see
    ``reef.reading.sandbox.child_user``). A child that a hostile file has
    taken over could then read ``/proc/<worker>/environ``, and with it the
    storage key and the database URL. A ``USER`` line would do exactly that.
    """
    users = [
        line
        for line in _instructions().splitlines()
        if line.strip().upper().startswith("USER ")
    ]
    assert users == []
