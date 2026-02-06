"""Chaos smoke tests for CI."""

from src.ci_smoke import ping


def test_ping_chaos() -> None:
    """Ensure ping is resilient in chaos context."""

    assert ping() == "ok"
