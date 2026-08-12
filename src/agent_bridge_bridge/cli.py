from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, TextIO

import httpx


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-bridge")
    parser.add_argument(
        "--api-url",
        default="http://127.0.0.1:58081/api/v1",
        help="Catalog hub API root (default: %(default)s)",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    message_send = commands.add_parser("message-send", help="submit a Manual Bridge envelope")
    message_send.add_argument("--json", required=True, metavar="PATH", help="request JSON or -")

    message_list = commands.add_parser("message-list", help="list Manual Bridge submissions")
    message_list.add_argument("--status")
    message_list.add_argument("--work-id")

    request_submit = commands.add_parser("request-submit", help="submit an execution request")
    request_submit.add_argument("--json", required=True, metavar="PATH", help="request JSON or -")

    execution_list = commands.add_parser("execution-list", help="list executions")
    execution_list.add_argument("--status")
    execution_list.add_argument("--work-id")

    execution_status = commands.add_parser("execution-status", help="show one execution")
    execution_status.add_argument("execution_id")

    execution_cancel = commands.add_parser("execution-cancel", help="cancel one execution")
    execution_cancel.add_argument("execution_id")
    execution_cancel.add_argument("--reason", default="cancelled from agent-bridge CLI")
    return parser


def run(
    argv: list[str] | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    input_stream = stdin or sys.stdin
    output_stream = stdout or sys.stdout
    error_stream = stderr or sys.stderr
    with httpx.Client(base_url=args.api_url.rstrip("/"), transport=transport, timeout=30) as client:
        try:
            response = _request(client, args, input_stream)
            response.raise_for_status()
        except (OSError, json.JSONDecodeError, httpx.HTTPError) as exc:
            print(str(exc), file=error_stream)
            return 1
    print(json.dumps(response.json(), indent=2, sort_keys=True), file=output_stream)
    return 0


def _request(client: httpx.Client, args: argparse.Namespace, stdin: TextIO) -> httpx.Response:
    command = str(args.command)
    if command == "message-send":
        return client.post("/bridge/messages", json=_read_json(args.json, stdin))
    if command == "message-list":
        return client.get(
            "/bridge/messages",
            params=_without_none({"status": args.status, "work_id": args.work_id}),
        )
    if command == "request-submit":
        return client.post("/bridge/requests", json=_read_json(args.json, stdin))
    if command == "execution-list":
        return client.get(
            "/bridge/executions",
            params=_without_none({"status": args.status, "work_id": args.work_id}),
        )
    if command == "execution-status":
        return client.get(f"/bridge/executions/{args.execution_id}")
    if command == "execution-cancel":
        return client.post(
            f"/bridge/executions/{args.execution_id}/cancel",
            json={"reason": args.reason},
        )
    raise AssertionError(f"unhandled command: {command}")


def _read_json(path: str, stdin: TextIO) -> dict[str, Any]:
    raw = stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("submission JSON must be an object")
    return value


def _without_none(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
