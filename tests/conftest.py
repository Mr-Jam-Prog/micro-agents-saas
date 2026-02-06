"""Pytest configuration to scope CI collections."""

from __future__ import annotations

from pathlib import Path


ALLOWED_TESTS = {
    "tests/unit/test_ci_smoke.py",
    "tests/integration/test_ci_smoke.py",
    "tests/security/test_ci_smoke.py",
    "tests/e2e/test_ci_smoke.py",
    "tests/chaos/test_ci_smoke.py",
    "tests/performance/test_benchmark_smoke.py",
}


def pytest_ignore_collect(path: Path, config) -> bool:  # noqa: D103
    path_str = str(path).replace("\\", "/")
    if path_str.endswith(tuple(ALLOWED_TESTS)):
        return False
    if path_str.startswith("tests/") or "/tests/" in path_str:
        return True
    return False
