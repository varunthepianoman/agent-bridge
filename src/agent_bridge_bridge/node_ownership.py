"""Broker-backed exclusive ownership for durable node identities."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from uuid import uuid4

from nats.js.api import KeyValueConfig
from nats.js.errors import (
    BucketNotFoundError,
    KeyNotFoundError,
    KeyWrongLastSequenceError,
)
from nats.js.kv import KeyValue

from .transport import JetStreamTransport

NODE_OWNERS_BUCKET = "AGENT_BRIDGE_NODE_OWNERS_V1"


class NodeOwnershipConflict(RuntimeError):
    pass


class NodeOwnershipLease:
    """Guarantee at most one live runner instance for a node ID."""

    def __init__(
        self,
        transport: JetStreamTransport,
        *,
        node_id: str,
        ttl_seconds: float = 30,
        instance_id: str | None = None,
    ) -> None:
        self.transport = transport
        self.node_id = node_id
        self.ttl_seconds = ttl_seconds
        self.instance_id = instance_id or f"runner-{uuid4()}"
        self._kv: KeyValue | None = None
        self._revision: int | None = None

    async def acquire(self) -> None:
        self._kv = await self._bucket()
        payload = self._payload()
        try:
            self._revision = await self._kv.create(self.node_id, payload)
        except KeyWrongLastSequenceError as error:
            try:
                incumbent = await self._kv.get(self.node_id)
                owner = json.loads(incumbent.value.decode("utf-8")).get("instance_id")
            except (KeyNotFoundError, ValueError, UnicodeDecodeError):
                owner = "unknown"
            raise NodeOwnershipConflict(
                f"node {self.node_id!r} already has active runner {owner!r}"
            ) from error

    async def maintain(self) -> None:
        if self._kv is None or self._revision is None:
            raise RuntimeError("node ownership lease has not been acquired")
        while True:
            await asyncio.sleep(self.ttl_seconds / 3)
            try:
                self._revision = await self._kv.update(
                    self.node_id, self._payload(), last=self._revision
                )
            except KeyWrongLastSequenceError as error:
                raise NodeOwnershipConflict(
                    f"node ownership for {self.node_id!r} was lost"
                ) from error

    async def release(self) -> None:
        if self._kv is None or self._revision is None:
            return
        try:
            await self._kv.delete(self.node_id, last=self._revision)
        except (KeyNotFoundError, KeyWrongLastSequenceError):
            pass
        finally:
            self._revision = None

    async def _bucket(self) -> KeyValue:
        try:
            return await self.transport.jetstream.key_value(NODE_OWNERS_BUCKET)
        except BucketNotFoundError:
            try:
                return await self.transport.jetstream.create_key_value(
                    KeyValueConfig(
                        bucket=NODE_OWNERS_BUCKET,
                        description="Exclusive active Agent Bridge runner ownership by node ID",
                        history=1,
                        ttl=self.ttl_seconds,
                    )
                )
            except Exception:
                # Another runner may have created the bucket concurrently.
                return await self.transport.jetstream.key_value(NODE_OWNERS_BUCKET)

    def _payload(self) -> bytes:
        return json.dumps(
            {
                "node_id": self.node_id,
                "instance_id": self.instance_id,
                "renewed_at": datetime.now(UTC).isoformat(),
            },
            separators=(",", ":"),
        ).encode("utf-8")
