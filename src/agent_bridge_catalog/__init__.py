"""Local-first AI work catalog."""

from typing import Any


def create_app(*args: Any, **kwargs: Any) -> Any:
    """Build the Catalog app without eagerly constructing the module-level ASGI app."""
    from .app import create_app as _create_app

    return _create_app(*args, **kwargs)


__all__ = ["create_app"]
