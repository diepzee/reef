"""Write the runbook's table of every setting, from the settings themselves.

Run as ``just settings-table``. ``tests/test_settings.py`` fails when the
table in ``docs/runbook.md`` differs from what this would write, so a setting
cannot be added, renamed or redescribed without the runbook following.
"""

import sys
from pathlib import Path

from reef.config import variables

RUNBOOK = Path(__file__).resolve().parent.parent / "docs" / "runbook.md"
START = "<!-- settings:start -->"
END = "<!-- settings:end -->"


def _default(value: object) -> str:
    """Show a default the way an operator would type it.

    :param value: the default
    :returns: the text for the table cell
    """
    if value == "":
        return "(none)"
    if isinstance(value, bool):
        return "`1`" if value else "`0`"
    if isinstance(value, float) and value.is_integer():
        return f"`{int(value)}`"
    if isinstance(value, float):
        return f"`{value:.4g}`"
    return f"`{value}`"


def table() -> str:
    """Build the table.

    :returns: Markdown, one row per variable, grouped
    """
    rows = ["| Group | Variable | Default | What it does |", "|---|---|---|---|"]
    for group, name, default, description in variables():
        text = " ".join(description.split()).replace("|", "\\|")
        rows.append(f"| {group} | `{name}` | {_default(default)} | {text} |")
    return "\n".join(rows)


def section() -> str:
    """Build the whole generated section, markers included.

    :returns: Markdown
    """
    return f"{START}\n{table()}\n{END}"


def current(text: str) -> str:
    """Find the generated section in the runbook.

    :param text: the runbook
    :returns: the section, markers included, or an empty string
    """
    if START not in text or END not in text:
        return ""
    return text[text.index(START) : text.index(END) + len(END)]


def main() -> None:
    """Rewrite the table in the runbook."""
    text = RUNBOOK.read_text()
    found = current(text)
    if not found:
        sys.exit(f"no {START} ... {END} in {RUNBOOK}")
    RUNBOOK.write_text(text.replace(found, section()))
    print(f"wrote {len(variables())} settings to {RUNBOOK}")


if __name__ == "__main__":
    main()
