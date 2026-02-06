"""E2E smoke tests for CI."""

from src.ci_smoke import add


def test_add_e2e() -> None:
    """Verify add works in E2E context."""

    assert add(1, 1) == 2
