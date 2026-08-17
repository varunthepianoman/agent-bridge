"""Human and agent CLI for the single-user Agent Bridge hub."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, TextIO

import httpx
import uvicorn

REASONING_EFFORTS = ("low", "medium", "high", "xhigh", "max", "ultra")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-bridge")
    parser.add_argument(
        "--api-url",
        default=os.environ.get("AGENT_BRIDGE_API_URL", "http://127.0.0.1:58081/api/v1"),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help="run the hub daemon and web API")
    serve.add_argument("--bind", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=58081)
    serve.add_argument("--reload", action="store_true")

    chats = commands.add_parser("chats", help="list selected conversations")
    chats.add_argument("--query")
    chats.add_argument("--provider")
    chats.add_argument("--node")

    candidates = commands.add_parser("candidates", help="list discovered chats available to add")
    candidates.add_argument("--node")

    add = commands.add_parser("add", help="select one or more discovered chats")
    add.add_argument("conversation_ids", nargs="+")

    remove = commands.add_parser("remove", help="remove a chat from the selected catalog")
    remove.add_argument("conversation_id")

    show = commands.add_parser("show", help="show one selected conversation")
    show.add_argument("conversation_id")

    rename = commands.add_parser("rename", help="set a Bridge alias")
    rename.add_argument("conversation_id")
    rename.add_argument("alias")

    message = commands.add_parser("message", help="send a message to a chat or room")
    target = message.add_mutually_exclusive_group(required=True)
    target.add_argument("--chat")
    target.add_argument("--room")
    message.add_argument("body")
    message.add_argument("--from-chat")
    message.add_argument("--operation", default="message")
    message.add_argument("--correlation-id")

    turn = commands.add_parser("turn", help="send a local user turn to a selected chat")
    turn.add_argument("conversation_id")
    turn.add_argument("prompt")
    turn.add_argument("--effort", choices=REASONING_EFFORTS)

    start = commands.add_parser("start", help="start a new provider conversation")
    start.add_argument("--provider", choices=("codex", "claude"), required=True)
    start.add_argument("--cwd", required=True)
    start.add_argument("prompt")
    start.add_argument("--alias")
    start.add_argument("--node")
    start.add_argument("--environment")
    start.add_argument("--model")
    start.add_argument("--effort", choices=REASONING_EFFORTS)

    open_chat = commands.add_parser("open", help="open a chat in its native provider")
    open_chat.add_argument("conversation_id")

    commands.add_parser("attention", help="list attention items")
    ack = commands.add_parser("ack", help="acknowledge an attention item")
    ack.add_argument("attention_id")
    commands.add_parser("nodes", help="list Bridge nodes and environments")
    commands.add_parser("rooms", help="list rooms")
    commands.add_parser("nats", help="show broker health and diagnostics")
    commands.add_parser("reconcile", help="run discovery reconciliation now")
    return parser


def run(
    argv: list[str] | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "serve":
        uvicorn.run(
            "agent_bridge_catalog.app:app",
            host=args.bind,
            port=args.port,
            reload=args.reload,
        )
        return 0
    output_stream = stdout or sys.stdout
    error_stream = stderr or sys.stderr
    with httpx.Client(base_url=args.api_url.rstrip("/"), transport=transport, timeout=30) as client:
        try:
            response = _request(client, args)
            response.raise_for_status()
        except (OSError, httpx.HTTPError) as exc:
            print(str(exc), file=error_stream)
            return 1
    if response.content:
        print(json.dumps(response.json(), indent=2, sort_keys=True), file=output_stream)
    return 0


def _request(client: httpx.Client, args: argparse.Namespace) -> httpx.Response:
    command = str(args.command)
    if command == "chats":
        return client.get(
            "/conversations",
            params=_without_none(
                {"q": args.query, "provider": args.provider, "node_id": args.node}
            ),
        )
    if command == "candidates":
        return client.get("/conversations/candidates", params=_without_none({"node_id": args.node}))
    if command == "add":
        return client.post(
            "/conversations/import", json={"conversation_ids": args.conversation_ids}
        )
    if command == "remove":
        return client.delete(f"/conversations/{args.conversation_id}")
    if command == "show":
        return client.get(f"/conversations/{args.conversation_id}")
    if command == "rename":
        return client.patch(f"/conversations/{args.conversation_id}", json={"alias": args.alias})
    if command == "message":
        return client.post(
            "/messages",
            json=_without_none(
                {
                    "body": args.body,
                    "target_conversation_id": args.chat,
                    "room_id": args.room,
                    "source_conversation_id": args.from_chat,
                    "operation": args.operation,
                    "correlation_id": args.correlation_id,
                }
            ),
        )
    if command == "turn":
        return client.post(
            f"/conversations/{args.conversation_id}/turns",
            json=_without_none({"prompt": args.prompt, "effort": args.effort}),
        )
    if command == "start":
        return client.post(
            "/conversations",
            json=_without_none(
                {
                    "provider": args.provider,
                    "cwd": args.cwd,
                    "initial_prompt": args.prompt,
                    "alias": args.alias,
                    "node_id": args.node,
                    "environment_id": args.environment,
                    "model": args.model,
                    "effort": args.effort,
                }
            ),
        )
    if command == "open":
        return client.post(f"/conversations/{args.conversation_id}/open")
    if command == "attention":
        return client.get("/attention")
    if command == "ack":
        return client.post(f"/attention/{args.attention_id}/acknowledge")
    if command == "nodes":
        return client.get("/nodes")
    if command == "rooms":
        return client.get("/rooms")
    if command == "nats":
        return client.get("/nats/summary")
    if command == "reconcile":
        return client.post("/reconciliation")
    raise AssertionError(f"unhandled command: {command}")


def _without_none(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
