"""Performance benchmarks for CI."""

from src.ci_smoke import add


def test_add_benchmark(benchmark) -> None:
    """Benchmark the add helper."""

    result = benchmark(lambda: add(100, 200))
    assert result == 300
