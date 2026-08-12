"""Provider-neutral composition for local Catalog discovery."""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Sequence
from typing import Any, Protocol


class CatalogProvider(Protocol):
    def discover(self, *, include_turns: bool = True) -> AsyncIterator[Any]: ...


class CompositeCatalogAdapter:
    def __init__(self, providers: Sequence[CatalogProvider]) -> None:
        self._providers = tuple(providers)

    async def discover(self, *, include_turns: bool = True) -> AsyncIterator[Any]:
        for provider in self._providers:
            async for item in provider.discover(include_turns=include_turns):
                yield item

    async def close(self) -> None:
        for provider in reversed(self._providers):
            close = getattr(provider, "close", None)
            if close is not None:
                result = close()
                if inspect.isawaitable(result):
                    await result
