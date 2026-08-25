"""Authenticated HTTP transport between a native node and the private hub."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from .runner import CommandResult, NodeCommand


class HubProtocolError(RuntimeError):
    """The hub returned a response that cannot be safely interpreted."""


class HubTransportError(RuntimeError):
    """A transient transport failure, with no request payload or credential details."""


class HubClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def synchronize(
        self,
        registration: Mapping[str, Any],
        conversations: Sequence[Mapping[str, Any]],
        environments: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        return self._post(
            "/api/v1/node/sync",
            {
                "registration": dict(registration),
                "conversations": conversations,
                "environments": environments,
            },
        )

    def heartbeat(self, heartbeat: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._post("/api/v1/node/heartbeat", heartbeat)

    def claim_commands(self, node_id: str) -> list[NodeCommand]:
        payload = self._post("/api/v1/node/commands/claim", {"node_id": node_id})
        raw_command = payload.get("command")
        if raw_command is None:
            return []
        if not isinstance(raw_command, dict):
            raise HubProtocolError("command claim response command must be an object or null")
        try:
            return [NodeCommand.model_validate(raw_command)]
        except (TypeError, ValueError) as error:
            raise HubProtocolError(f"invalid command in claim response: {error}") from error

    def report_result(self, node_id: str, result: CommandResult) -> Mapping[str, Any]:
        return self._post(
            f"/api/v1/node/commands/{result.command_id}/result",
            {"node_id": node_id, **result.model_dump(mode="json", exclude={"command_id"})},
        )

    def _post(self, path: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        try:
            response = self._client.post(path, json=dict(payload))
        except httpx.TransportError as error:
            raise HubTransportError(
                f"Hub transport failed ({type(error).__name__})"
            ) from None
        response.raise_for_status()
        if not response.content:
            return {}
        value = response.json()
        if not isinstance(value, dict):
            raise HubProtocolError(f"{path} response must be a JSON object")
        return value
