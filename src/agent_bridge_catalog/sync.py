from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from .repository import CatalogRepository


class ConversationProvider(Protocol):
    def discover(self, *, include_turns: bool = True) -> AsyncIterator[Any]: ...


@dataclass(frozen=True, slots=True)
class SyncResult:
    discovered: int
    imported: int


class CatalogSynchronizer:
    def __init__(
        self,
        repository: CatalogRepository,
        provider: ConversationProvider,
        *,
        node_id: str,
        environment_id: str,
        auto_add_new_chats: Callable[[], bool] | None = None,
    ) -> None:
        self.repository = repository
        self.provider = provider
        self.node_id = node_id
        self.environment_id = environment_id
        self.auto_add_new_chats = auto_add_new_chats or (lambda: False)

    async def reconcile(self, *, include_turns: bool = True) -> SyncResult:
        discovered = imported = 0
        async for item in self.provider.discover(include_turns=include_turns):
            discovered += 1
            self.repository.upsert_discovered(
                item,
                node_id=self.node_id,
                environment_id=self.environment_id,
                transcript_included=include_turns,
                select_if_new=self.auto_add_new_chats(),
            )
            imported += 1
        self.repository.resolve_parents()
        return SyncResult(discovered=discovered, imported=imported)
