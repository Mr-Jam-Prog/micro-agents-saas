"""Unit smoke tests for CI."""

from src.ci_smoke import add, ping


def test_add() -> None:
    """Ensure add returns deterministic output."""

    assert add(2, 3) == 5


def test_ping() -> None:
    """Ensure ping returns expected response."""

    assert ping() == "ok"
