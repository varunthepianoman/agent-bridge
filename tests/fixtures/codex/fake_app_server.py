"""Small headerless JSON-RPC JSONL server used by Codex adapter tests."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from typing import Any

_write_lock = threading.Lock()
_initialized = False


THREADS = [
    {
        "id": "thr_a",
        "name": "Agent Bridge design",
        "preview": "Design a durable catalog",
        "cwd": "/workspace/agent-bridge",
        "source": "vscode",
        "modelProvider": "openai",
        "createdAt": 100,
        "updatedAt": 200,
        "isPinned": True,
        "ephemeral": False,
        "status": {"type": "active", "activeFlags": ["waitingOnApproval"]},
        "gitInfo": {
            "sha": "abc123",
            "branch": "feature/catalog",
            "originUrl": "https://example.test/repo.git",
        },
        "futureCodexField": {"preserve": True},
    },
    {
        "id": "thr_b",
        "preview": "Audit the implementation",
        "cwd": "/workspace/agent-bridge",
        "sourceKind": "subAgent",
        "modelProvider": "openai",
        "createdAt": 110,
        "updatedAt": 190,
        "parentThreadId": "thr_a",
        "status": {"type": "notLoaded"},
    },
]


def send(message: dict[str, Any]) -> None:
    payload = json.dumps(message, separators=(",", ":")) + "\n"
    with _write_lock:
        sys.stdout.write(payload)
        sys.stdout.flush()


def delayed_echo(request_id: int, params: dict[str, Any]) -> None:
    time.sleep(float(params.get("delay", 0)))
    send({"id": request_id, "result": {"value": params.get("value")}})


for raw_line in sys.stdin:
    try:
        request = json.loads(raw_line)
    except json.JSONDecodeError:
        continue
    method = request.get("method")
    request_id = request.get("id")
    params = request.get("params") or {}

    if method == "initialize":
        if _initialized:
            send({"id": request_id, "error": {"code": -32000, "message": "Already initialized"}})
        else:
            _initialized = True
            send(
                {
                    "id": request_id,
                    "result": {
                        "userAgent": "fake-codex/1.0",
                        "platformFamily": "unix",
                        "platformOs": "linux",
                    },
                }
            )
    elif method == "initialized":
        send({"method": "server/ready", "params": {"pid": os.getpid()}})
    elif not _initialized:
        send({"id": request_id, "error": {"code": -32000, "message": "Not initialized"}})
    elif method == "thread/list":
        cursor = params.get("cursor")
        if cursor is None:
            send({"id": request_id, "result": {"data": [THREADS[0]], "nextCursor": "page-2"}})
        elif cursor == "page-2":
            send({"id": request_id, "result": {"data": [THREADS[1]], "nextCursor": None}})
        else:
            send({"id": request_id, "error": {"code": -32602, "message": "bad cursor"}})
    elif method == "thread/read":
        thread = next((item for item in THREADS if item["id"] == params.get("threadId")), None)
        if thread is None:
            send({"id": request_id, "error": {"code": 404, "message": "thread not found"}})
        else:
            result = dict(thread)
            if params.get("includeTurns"):
                result["turns"] = [
                    {
                        "id": "turn_1",
                        "items": [
                            {
                                "type": "userMessage",
                                "content": [{"type": "text", "text": "Find the reconnect bug"}],
                            },
                            {"type": "commandExecution", "output": "SECRET_TOOL_OUTPUT"},
                            {
                                "type": "agentMessage",
                                "text": "The session generation is stale.",
                            },
                        ],
                    }
                ]
            send({"id": request_id, "result": {"thread": result}})
    elif method == "thread/start":
        thread = {
            "id": "thr_new",
            "cwd": params.get("cwd"),
            "status": {"type": "idle"},
            "fullAccess": params.get("approvalPolicy") == "never"
            and params.get("sandbox") == "danger-full-access",
            "model": params.get("model"),
        }
        send({"id": request_id, "result": {"thread": thread}})
        send({"method": "thread/started", "params": {"thread": thread}})
    elif method == "thread/resume":
        send(
            {
                "id": request_id,
                "result": {
                    "thread": {
                        "id": params.get("threadId"),
                        "cwd": params.get("cwd"),
                        "fullAccess": params.get("approvalPolicy") == "never"
                        and params.get("sandbox") == "danger-full-access",
                    }
                },
            }
        )
    elif method == "turn/start":
        turn = {
            "id": "turn_new",
            "status": "inProgress",
            "items": [],
            "fullAccess": params.get("approvalPolicy") == "never"
            and params.get("sandboxPolicy") == {"type": "dangerFullAccess"},
            "model": params.get("model"),
            "effort": params.get("effort"),
        }
        send({"id": request_id, "result": {"turn": turn}})
        send(
            {
                "method": "turn/completed",
                "params": {"turn": {**turn, "status": "completed"}},
            }
        )
    elif method == "test/echo":
        threading.Thread(target=delayed_echo, args=(request_id, params), daemon=True).start()
    elif method == "test/large":
        send({"id": request_id, "result": {"value": "x" * (128 * 1024)}})
    elif method == "test/malformed":
        with _write_lock:
            sys.stdout.write("not-json\n")
            sys.stdout.flush()
        send({"id": request_id, "result": {}})
    elif method == "test/crash":
        sys.stderr.write("intentional fake server crash\n")
        sys.stderr.flush()
        os._exit(23)
    else:
        send({"id": request_id, "error": {"code": -32601, "message": f"unknown: {method}"}})
