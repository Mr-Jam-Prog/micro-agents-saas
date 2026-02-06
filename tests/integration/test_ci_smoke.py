"""Integration smoke tests for CI."""

from src.ci_smoke import add


def test_add_integration() -> None:
    """Verify add works in integration context."""

    assert add(10, 15) == 25
