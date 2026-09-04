from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agent_bridge_providers.codex import (  # noqa: E402
    AppServerClient,
    AppServerClosedError,
    AppServerError,
    CodexCatalogAdapter,
)

FAKE_SERVER = Path(__file__).parent / "fixtures" / "codex" / "fake_app_server.py"


def make_client(*, timeout: float = 2.0) -> AppServerClient:
    return AppServerClient((sys.executable, str(FAKE_SERVER)), request_timeout=timeout)


class CodexAdapterTest(unittest.TestCase):
    def test_maps_latest_assistant_message_from_sanitized_prose(self) -> None:
        record = CodexCatalogAdapter.map_thread(
            {
                "id": "thr-latest",
                "turns": [
                    {
                        "items": [
                            {"type": "agentMessage", "text": "Earlier reply"},
                            {"type": "reasoning", "text": "SECRET_REASONING"},
                        ]
                    },
                    {
                        "items": [
                            {
                                "type": "agentMessage",
                                "content": [
                                    {"type": "output_text", "text": "Latest"},
                                    {"type": "text", "text": "reply"},
                                ],
                            },
                            {"type": "commandExecution", "output": "SECRET_TOOL_OUTPUT"},
                            {"type": "userMessage", "text": "One more question"},
                        ]
                    },
                ],
            }
        )

        self.assertEqual(record.last_assistant_message, "Latest reply")
        self.assertIn("user: One more question", record.transcript_text)
        self.assertNotIn("SECRET_REASONING", record.transcript_text)
        self.assertNotIn("SECRET_TOOL_OUTPUT", record.transcript_text)

    def test_maps_missing_assistant_message_as_none(self) -> None:
        record = CodexCatalogAdapter.map_thread(
            {
                "id": "thr-no-reply",
                "turns": [{"items": [{"type": "userMessage", "text": "Still waiting"}]}],
            }
        )

        self.assertIsNone(record.last_assistant_message)

    def test_initializes_and_receives_notifications(self) -> None:
        async def scenario() -> None:
            client = make_client()
            try:
                await client.start()
                method, params = await client.next_notification(timeout=1)
                self.assertEqual(method, "server/ready")
                self.assertIsInstance(params["pid"], int)
                diagnostics = client.diagnostics()
                self.assertEqual(diagnostics.state, "ready")
                self.assertEqual(diagnostics.generation, 1)
                self.assertEqual(
                    diagnostics.initialize_result,
                    {
                        "userAgent": "fake-codex/1.0",
                        "platformFamily": "unix",
                        "platformOs": "linux",
                    },
                )
            finally:
                await client.close()
            self.assertEqual(client.diagnostics().state, "closed")

        asyncio.run(scenario())

    def test_starts_resumes_and_completes_turns(self) -> None:
        async def scenario() -> None:
            async with make_client() as client:
                thread = await client.start_thread(cwd="/tmp", model="gpt-5.6-sol")
                self.assertEqual(thread["id"], "thr_new")
                self.assertTrue(thread["fullAccess"])
                self.assertEqual(thread["model"], "gpt-5.6-sol")
                resumed = await client.resume_thread("thr_new", cwd="/tmp")
                self.assertEqual(resumed["id"], "thr_new")
                self.assertTrue(resumed["fullAccess"])
                self.assertEqual(await client.unsubscribe_thread("thr_new"), "unsubscribed")
                turn = await client.start_turn(
                    "thr_new",
                    "Check the socket",
                    model="gpt-5.6-sol",
                    effort="high",
                )
                self.assertEqual(turn["status"], "inProgress")
                self.assertTrue(turn["fullAccess"])
                self.assertEqual(turn["model"], "gpt-5.6-sol")
                self.assertEqual(turn["effort"], "high")
                methods = set()
                for _ in range(3):
                    method, _params = await client.next_notification(timeout=1)
                    methods.add(method)
                self.assertIn("turn/completed", methods)

        asyncio.run(scenario())

    def test_correlates_concurrent_out_of_order_responses(self) -> None:
        async def scenario() -> None:
            async with make_client() as client:
                slow = asyncio.create_task(
                    client.request("test/echo", {"value": "slow", "delay": 0.08})
                )
                fast = asyncio.create_task(
                    client.request("test/echo", {"value": "fast", "delay": 0.01})
                )
                self.assertEqual(await fast, {"value": "fast"})
                self.assertEqual(await slow, {"value": "slow"})

        asyncio.run(scenario())

    def test_catalog_adapter_pages_and_maps_provider_neutral_records(self) -> None:
        async def scenario() -> None:
            async with make_client() as client:
                adapter = CodexCatalogAdapter(client)
                records = await adapter.list_conversations(
                    page_size=1, source_kinds=["vscode", "subAgent"]
                )
                self.assertEqual(
                    [record.provider_thread_id for record in records],
                    ["thr_a", "thr_b"],
                )
                first, child = records
                self.assertEqual(first.provider, "codex")
                self.assertEqual(first.title, "Agent Bridge design")
                self.assertEqual(first.status, "active")
                self.assertEqual(first.active_flags, ("waitingOnApproval",))
                self.assertEqual(first.git_branch, "feature/catalog")
                self.assertTrue(first.is_pinned)
                self.assertEqual(first.raw_metadata, {"futureCodexField": {"preserve": True}})
                self.assertEqual(child.source_kind, "subAgent")
                self.assertEqual(child.parent_thread_id, "thr_a")

                reread = await adapter.get_conversation("thr_a", include_turns=True)
                self.assertEqual(reread.provider_thread_id, "thr_a")
                self.assertNotIn("turns", reread.raw_metadata)
                self.assertIn("Find the reconnect bug", reread.transcript_text)
                self.assertIn("session generation is stale", reread.transcript_text)
                self.assertNotIn("SECRET_TOOL_OUTPUT", reread.transcript_text)

                discovered = [item async for item in adapter.discover(include_turns=True)]
                self.assertEqual(len(discovered), 4)
                self.assertEqual(discovered[1].provider_thread_id, "thr_b")
                self.assertEqual(discovered[1].transcript_text, "")
                self.assertTrue(discovered[2].is_archived)

        asyncio.run(scenario())

    def test_surfaces_app_server_errors(self) -> None:
        async def scenario() -> None:
            async with make_client() as client:
                with self.assertRaises(AppServerError) as caught:
                    await client.read_thread("missing")
                self.assertEqual(caught.exception.code, 404)
                self.assertEqual(caught.exception.message, "thread not found")

        asyncio.run(scenario())

    def test_records_malformed_output_without_losing_connection(self) -> None:
        async def scenario() -> None:
            async with make_client() as client:
                self.assertEqual(await client.request("test/malformed"), {})
                diagnostics = client.diagnostics()
                self.assertEqual(diagnostics.malformed_messages, 1)
                self.assertTrue(
                    any("malformed stdout" in item for item in diagnostics.recent_stderr)
                )

        asyncio.run(scenario())

    def test_accepts_app_server_lines_larger_than_asyncio_default(self) -> None:
        async def scenario() -> None:
            async with make_client() as client:
                result = await client.request("test/large")
                self.assertEqual(len(result["value"]), 17 * 1024 * 1024)

        asyncio.run(scenario())

    def test_restarts_after_process_exit_without_replaying_inflight_request(self) -> None:
        async def scenario() -> None:
            async with make_client() as client:
                closed: list[str] = []

                async def observe_close(error: AppServerClosedError) -> None:
                    closed.append(str(error))

                client.add_close_handler(observe_close)
                original_pid = client.diagnostics().pid
                with self.assertRaises(AppServerClosedError):
                    await client.request("test/crash")
                self.assertEqual(len(closed), 1)
                result = await client.request("test/echo", {"value": "after restart"})
                self.assertEqual(result, {"value": "after restart"})
                diagnostics = client.diagnostics()
                self.assertEqual(diagnostics.state, "ready")
                self.assertNotEqual(diagnostics.pid, original_pid)
                self.assertEqual(diagnostics.generation, 2)
                self.assertEqual(diagnostics.restart_count, 1)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
