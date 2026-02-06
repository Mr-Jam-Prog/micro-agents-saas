"""
Lightweight entrypoint for the API.

Falls back to a minimal ASGI app when optional dependencies are unavailable,
while delegating to the full FastAPI implementation when installed.
"""

from __future__ import annotations

import importlib.util
from typing import Callable


def _build_minimal_app() -> Callable:  # pragma: no cover - trivial fallback
    async def app(scope, receive, send) -> None:
        if scope["type"] != "http":
            return
        path = scope.get("path", "/")
        if path == "/health":
            body = b'{"status":"ok"}'
            content_type = b"application/json"
        else:
            body = b"ok"
            content_type = b"text/plain"
        headers = [
            (b"content-type", content_type),
            (b"content-length", str(len(body)).encode("ascii")),
        ]
        await send({"type": "http.response.start", "status": 200, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    return app


if importlib.util.find_spec("fastapi") is None:
    app = _build_minimal_app()
else:
    from .full_app import app  # noqa: F401
