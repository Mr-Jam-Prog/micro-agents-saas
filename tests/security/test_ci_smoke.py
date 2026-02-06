"""Security smoke tests for CI."""

from src.ci_smoke import ping


def test_ping_security() -> None:
    """Ensure ping is stable for security checks."""

    assert ping() == "ok"
