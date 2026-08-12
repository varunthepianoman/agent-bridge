"""Smoke-check the checked-in NATS user permissions against a running broker."""

from __future__ import annotations

import argparse
import asyncio

import nats


async def check(url: str, user: str, password: str) -> None:
    errors: list[Exception] = []

    async def record(error: Exception) -> None:
        errors.append(error)

    client = await nats.connect(url, user=user, password=password, error_cb=record)
    own = await client.subscribe("bridge.v1.inbox.node.node-a")
    await client.publish("bridge.v1.inbox.node.node-a", b"allowed")
    await client.flush()
    received = await own.next_msg(timeout=2)
    assert received.data == b"allowed"

    await client.subscribe("bridge.v1.inbox.node.node-b")
    await client.publish("bridge.v1.control.node.node-b", b"denied")
    await client.flush()
    await asyncio.sleep(0.2)
    await client.close()
    permission_errors = [error for error in errors if "permissions violation" in str(error).lower()]
    assert len(permission_errors) >= 2, [str(error) for error in errors]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="nats://127.0.0.1:44222")
    parser.add_argument("--user", default="node-a")
    parser.add_argument("--password", required=True)
    args = parser.parse_args()
    asyncio.run(check(args.url, args.user, args.password))
    print("NATS subject permission smoke: PASS")


if __name__ == "__main__":
    main()
