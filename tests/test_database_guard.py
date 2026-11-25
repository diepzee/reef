"""The test suite builds its schema only in the test database.

The ``schema`` fixture drops and recreates every table. On 24 November 2026 a
test file that imported ``reef`` before ``tests/conftest.py`` had set
``DATABASE_URL`` ran those statements against the development database and
emptied it. The fixture now asks the database for its name first.
"""

import pytest
from conftest import refuse_unless_test_database

TEST_URL = "postgresql://reef:secret@localhost:5433/reef_test"


def test_a_database_that_is_not_the_test_one_is_refused():
    """The development database's name raises, and the message names both
    databases so the reader sees which one the engine points at."""
    with pytest.raises(RuntimeError) as refused:
        refuse_unless_test_database("reef", TEST_URL)
    assert "'reef'" in str(refused.value)
    assert "'reef_test'" in str(refused.value)


def test_the_test_database_is_accepted():
    """The name in the test connection string passes without a word."""
    refuse_unless_test_database("reef_test", TEST_URL)


def test_the_message_holds_no_password():
    """The refusal shows database names only, never the connection string."""
    with pytest.raises(RuntimeError) as refused:
        refuse_unless_test_database("reef", TEST_URL)
    assert "secret" not in str(refused.value)
