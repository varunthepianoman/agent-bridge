"""Durable-state defaults for the Windows-Ubuntu bridge."""

from __future__ import annotations

import importlib.util
import json
import os
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest import mock


BRIDGE_SERVER = Path(__file__).parents[1] / "bridge_server.py"


def bridge_module():
    spec = importlib.util.spec_from_file_location("agent_bridge_server", BRIDGE_SERVER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DurableStateDefaultTest(unittest.TestCase):
    def test_uses_absolute_xdg_state_home(self) -> None:
        bridge = bridge_module()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": "/persistent/state"}):
            self.assertEqual(
                bridge.default_state_dir(),
                Path("/persistent/state/agent-bridge"),
            )

    def test_falls_back_to_persistent_user_state(self) -> None:
        bridge = bridge_module()
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                bridge.default_state_dir(),
                Path.home() / ".local" / "state" / "agent-bridge",
            )

    def test_ignores_relative_xdg_state_home(self) -> None:
        bridge = bridge_module()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": "relative/state"}):
            state_dir = bridge.default_state_dir()
        self.assertTrue(state_dir.is_absolute())
        self.assertEqual(
            state_dir,
            Path.home() / ".local" / "state" / "agent-bridge",
        )


class AuthenticatedApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.bridge = bridge_module()
        self.temp_dir = TemporaryDirectory()
        state_dir = Path(self.temp_dir.name)
        mailbox = self.bridge.Mailbox(state_dir / "messages.jsonl")
        handler = self.bridge.make_handler(
            mailbox,
            "integration-test-token-that-is-long-enough",
            Path(__file__).parents[1] / "windows_bridge.ps1",
        )
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp_dir.cleanup()

    def request(
        self, path: str, *, method: str = "GET", payload: dict | None = None
    ) -> dict:
        data = json.dumps(payload).encode() if payload is not None else None
        request = Request(
            self.base_url + path,
            method=method,
            data=data,
            headers={
                "Authorization": (
                    "Bearer integration-test-token-that-is-long-enough"
                ),
                "Content-Type": "application/json",
            },
        )
        with urlopen(request) as response:
            return json.load(response)

    def test_authenticated_send_and_receive_round_trip(self) -> None:
        sent = self.request(
            "/v1/messages",
            method="POST",
            payload={
                "sender": "ubuntu",
                "recipient": "windows",
                "kind": "task",
                "correlation_id": "smoke-1",
                "message": "provider-neutral smoke test",
            },
        )
        self.assertEqual(sent["id"], 1)

        received = self.request("/v1/messages?recipient=windows&after=0")
        self.assertEqual(received["messages"], [sent])

    def test_messages_require_authentication(self) -> None:
        with self.assertRaises(HTTPError) as raised:
            urlopen(self.base_url + "/v1/messages?recipient=windows&after=0")
        self.assertEqual(raised.exception.code, 401)


if __name__ == "__main__":
    unittest.main()
