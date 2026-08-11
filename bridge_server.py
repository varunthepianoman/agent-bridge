#!/usr/bin/env python3
"""Small authenticated mailbox for cooperating processes on Ubuntu and Windows."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import threading
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

MAX_BODY_BYTES = 128 * 1024
PARTIES = {"ubuntu", "windows"}
KINDS = {"task", "result", "status", "note"}


def default_state_dir() -> Path:
    """Return a persistent per-user state directory, never a temporary path."""
    configured = os.environ.get("XDG_STATE_HOME")
    if configured:
        state_home = Path(configured).expanduser()
        if state_home.is_absolute():
            return state_home / "agent-bridge"
    return Path.home() / ".local" / "state" / "agent-bridge"


class Mailbox:
    def __init__(self, messages_path: Path) -> None:
        self.messages_path = messages_path
        self.lock = threading.Lock()
        self.messages_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.messages_path.parent, 0o700)
        self._next_id = self._find_next_id()

    def _find_next_id(self) -> int:
        if not self.messages_path.exists():
            return 1
        last_id = 0
        with self.messages_path.open(encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    last_id = max(last_id, int(json.loads(line)["id"]))
        return last_id + 1

    def send(self, payload: dict[str, object]) -> dict[str, object]:
        sender = payload.get("sender")
        recipient = payload.get("recipient")
        kind = payload.get("kind", "note")
        message = payload.get("message")
        correlation_id = payload.get("correlation_id")

        if sender not in PARTIES or recipient not in PARTIES or sender == recipient:
            raise ValueError("sender and recipient must be different known parties")
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {sorted(KINDS)}")
        if not isinstance(message, str) or not message.strip():
            raise ValueError("message must be a non-empty string")
        if len(message) > 100_000:
            raise ValueError("message is too long")
        if correlation_id is not None and not isinstance(correlation_id, str):
            raise ValueError("correlation_id must be a string or null")

        with self.lock:
            entry: dict[str, object] = {
                "id": self._next_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "sender": sender,
                "recipient": recipient,
                "kind": kind,
                "correlation_id": correlation_id,
                "message": message,
            }
            self._next_id += 1
            with self.messages_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, separators=(",", ":")) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            return entry

    def receive(self, recipient: str, after: int) -> list[dict[str, object]]:
        if recipient not in PARTIES:
            raise ValueError("recipient must be a known party")
        result: list[dict[str, object]] = []
        with self.lock:
            if not self.messages_path.exists():
                return result
            with self.messages_path.open(encoding="utf-8") as stream:
                for line in stream:
                    if not line.strip():
                        continue
                    entry = json.loads(line)
                    if entry["recipient"] == recipient and int(entry["id"]) > after:
                        result.append(entry)
        return result


def load_or_create_token(token_path: Path) -> str:
    token_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(token_path.parent, 0o700)
    if token_path.exists():
        token = token_path.read_text(encoding="utf-8").strip()
        if len(token) < 32:
            raise ValueError(f"token in {token_path} is too short")
        return token
    token = secrets.token_urlsafe(32)
    token_path.write_text(token + "\n", encoding="utf-8")
    os.chmod(token_path, 0o600)
    return token


def make_handler(
    mailbox: Mailbox, token: str, windows_client_path: Path
) -> type[BaseHTTPRequestHandler]:
    token_digest = hashlib.sha256(token.encode()).digest()

    class Handler(BaseHTTPRequestHandler):
        server_version = "AgentBridge/1"

        def log_message(self, format_string: str, *args: object) -> None:
            print(
                f"{self.log_date_time_string()} {self.client_address[0]} "
                + format_string % args,
                flush=True,
            )

        def _json(self, status: HTTPStatus, value: object) -> None:
            body = json.dumps(value, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            supplied = self.headers.get("Authorization", "")
            if not supplied.startswith("Bearer "):
                return False
            supplied_digest = hashlib.sha256(supplied[7:].encode()).digest()
            return hmac.compare_digest(token_digest, supplied_digest)

        def _require_auth(self) -> bool:
            if self._authorized():
                return True
            self._json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return False

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/v1/health":
                self._json(HTTPStatus.OK, {"status": "ok"})
                return
            if parsed.path == "/bootstrap/windows_bridge.ps1":
                body = windows_client_path.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.path != "/v1/messages":
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            if not self._require_auth():
                return
            query = parse_qs(parsed.query)
            try:
                recipient = query.get("recipient", [""])[0]
                after = int(query.get("after", ["0"])[0])
                if after < 0:
                    raise ValueError("after must be non-negative")
                messages = mailbox.receive(recipient, after)
            except ValueError as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            self._json(HTTPStatus.OK, {"messages": messages})

        def do_POST(self) -> None:
            if urlparse(self.path).path != "/v1/messages":
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            if not self._require_auth():
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > MAX_BODY_BYTES:
                    raise ValueError("invalid Content-Length")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("request body must be a JSON object")
                entry = mailbox.send(payload)
            except (ValueError, json.JSONDecodeError) as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            self._json(HTTPStatus.CREATED, entry)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", default=58081, type=int)
    parser.add_argument("--state-dir", type=Path, default=default_state_dir())
    args = parser.parse_args()

    state_dir = args.state_dir.resolve()
    token_path = state_dir / "token"
    token = load_or_create_token(token_path)
    mailbox = Mailbox(state_dir / "messages.jsonl")
    windows_client_path = Path(__file__).with_name("windows_bridge.ps1")
    handler = make_handler(mailbox, token, windows_client_path)
    server = ThreadingHTTPServer((args.bind, args.port), handler)
    print(f"Listening on http://{args.bind}:{args.port}", flush=True)
    print(f"Token file: {token_path}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
