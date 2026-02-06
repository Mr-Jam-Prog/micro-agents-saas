"""Minimal, dependency-free helpers for CI smoke tests."""

from __future__ import annotations


def add(left: int, right: int) -> int:
    """Return the sum of two integers."""

    return left + right


def ping() -> str:
    """Return a simple health string."""

    return "ok"
