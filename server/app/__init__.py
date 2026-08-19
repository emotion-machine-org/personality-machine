"""Application package init.

This module avoids importing heavy optional dependencies (like pipecat / numpy)
when the `app` package is imported for utilities or tests. Accessing the FastAPI
app or factory happens lazily to keep lightweight modules usable without the
full runtime stack.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - only for type checkers
    from fastapi import FastAPI

__all__ = ["app", "create_app"]


def create_app() -> FastAPI:
    """Return a configured FastAPI application instance."""

    from .main import create_app as _create_app

    return _create_app()


def __getattr__(name: str) -> Any:  # pragma: no cover - simple delegation
    if name == "app":
        from .main import app as fastapi_app

        return fastapi_app
    raise AttributeError(name)
