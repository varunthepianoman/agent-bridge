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
        default=os.environ.get("AGENT_BRIDGE_API_URL", "http://127.0.0.1:58080/api/v1"),
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

    refresh = commands.add_parser("refresh", help="refresh a remote Codex transcript safely")
    refresh.add_argument("conversation_id")
    refresh.add_argument("--wait-seconds", type=float, default=10.0)

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
    message.add_argument("--request-ack", action="store_true")
    message.add_argument("--wait-for", choices=("claimed", "acknowledged", "terminal"))
    message.add_argument("--timeout", dest="timeout_seconds", type=float, default=30.0)

    inbox = commands.add_parser("inbox", help="list durable mail for one chat")
    inbox.add_argument("conversation_id")
    inbox.add_argument(
        "--state", choices=("pending", "received", "succeeded", "blocked", "failed")
    )
    inbox.add_argument("--limit", type=int, default=200)

    wait = commands.add_parser("wait", help="wait in the foreground for chat mail")
    wait.add_argument("conversation_id")
    wait.add_argument("--max-wait-seconds", type=float, default=3600)
    wait.add_argument("--batch-limit", type=int, default=50)

    complete = commands.add_parser("complete", help="record a mailbox message outcome")
    complete.add_argument("conversation_id")
    complete.add_argument("message_id")
    complete.add_argument("--outcome", choices=("succeeded", "blocked", "failed"), required=True)
    complete.add_argument("--detail")
    complete.add_argument("--reply-body")

    acknowledge = commands.add_parser(
        "acknowledge", help="acknowledge requested mail before longer processing"
    )
    acknowledge.add_argument("conversation_id")
    acknowledge.add_argument("message_id")
    acknowledge.add_argument("--detail")

    wait_receipt = commands.add_parser(
        "wait-receipt", help="wait in the foreground for a direct-message receipt"
    )
    wait_receipt.add_argument("source_conversation_id")
    wait_receipt.add_argument("message_id")
    wait_receipt.add_argument(
        "--until", choices=("claimed", "acknowledged", "terminal"), default="acknowledged"
    )
    wait_receipt.add_argument("--timeout", dest="timeout_seconds", type=float, default=3600.0)
    wait_receipt.add_argument("--after-revision", type=int)

    message_status = commands.add_parser(
        "message-status", help="show transport and receipt status for one message"
    )
    message_status.add_argument("message_id")

    requeue = commands.add_parser("requeue", help="explicitly return mail to pending")
    requeue.add_argument("conversation_id")
    requeue.add_argument("message_id")
    requeue.add_argument("--detail")

    stop_listener = commands.add_parser(
        "stop-listener", help="request cancellation of a chat mailbox listener"
    )
    stop_listener.add_argument("conversation_id")

    turn = commands.add_parser("turn", help="send a local user turn to a selected chat")
    turn.add_argument("conversation_id")
    turn.add_argument("prompt")
    turn.add_argument("--effort", choices=REASONING_EFFORTS)

    steer = commands.add_parser(
        "steer", help="explicitly steer a local active Codex turn without fallback"
    )
    steer.add_argument("conversation_id")
    steer.add_argument("prompt")

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
        except (OSError, ValueError, httpx.HTTPError) as exc:
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
    if command == "refresh":
        return client.post(
            f"/conversations/{args.conversation_id}/refresh",
            params={"wait_seconds": args.wait_seconds},
        )
    if command == "rename":
        return client.patch(f"/conversations/{args.conversation_id}", json={"alias": args.alias})
    if command == "message":
        if args.wait_for is not None and args.from_chat is None:
            raise ValueError("--from-chat is required with --wait-for")
        request_acknowledgement = args.request_ack or args.wait_for in {
            "acknowledged",
            "terminal",
        }
        response = client.post(
            "/messages",
            json=_without_none(
                {
                    "body": args.body,
                    "target_conversation_id": args.chat,
                    "room_id": args.room,
                    "source_conversation_id": args.from_chat,
                    "operation": args.operation,
                    "correlation_id": args.correlation_id,
                    "acknowledgement_requested": request_acknowledgement,
                }
            ),
        )
        if args.wait_for is None:
            return response
        response.raise_for_status()
        message_id = response.json()["message_id"]
        return client.post(
            f"/messages/{message_id}/wait-receipt",
            json={
                "source_conversation_id": args.from_chat,
                "until": args.wait_for,
                "timeout_seconds": args.timeout_seconds,
                "after_revision": None,
            },
            timeout=max(30, args.timeout_seconds + 10),
        )
    if command == "inbox":
        return client.get(
            f"/mailbox/{args.conversation_id}",
            params=_without_none({"state": args.state, "limit": args.limit}),
        )
    if command == "wait":
        return client.post(
            f"/mailbox/{args.conversation_id}/wait",
            json={
                "max_wait_seconds": args.max_wait_seconds,
                "batch_limit": args.batch_limit,
            },
            timeout=max(30, args.max_wait_seconds + 10),
        )
    if command == "complete":
        return client.post(
            f"/messages/{args.message_id}/complete",
            json=_without_none(
                {
                    "conversation_id": args.conversation_id,
                    "outcome": args.outcome,
                    "detail": args.detail,
                    "reply_body": args.reply_body,
                }
            ),
        )
    if command == "acknowledge":
        return client.post(
            f"/messages/{args.message_id}/acknowledge",
            json=_without_none(
                {"conversation_id": args.conversation_id, "detail": args.detail}
            ),
        )
    if command == "wait-receipt":
        return client.post(
            f"/messages/{args.message_id}/wait-receipt",
            json=_without_none(
                {
                    "source_conversation_id": args.source_conversation_id,
                    "until": args.until,
                    "timeout_seconds": args.timeout_seconds,
                    "after_revision": args.after_revision,
                }
            ),
            timeout=max(30, args.timeout_seconds + 10),
        )
    if command == "message-status":
        return client.get(f"/messages/{args.message_id}")
    if command == "requeue":
        return client.post(
            f"/messages/{args.message_id}/requeue",
            json=_without_none(
                {"conversation_id": args.conversation_id, "detail": args.detail}
            ),
        )
    if command == "stop-listener":
        return client.post(f"/mailbox/{args.conversation_id}/stop-listener")
    if command == "turn":
        return client.post(
            f"/conversations/{args.conversation_id}/turns",
            json=_without_none({"prompt": args.prompt, "effort": args.effort}),
        )
    if command == "steer":
        return client.post(
            f"/conversations/{args.conversation_id}/steer", json={"prompt": args.prompt}
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
