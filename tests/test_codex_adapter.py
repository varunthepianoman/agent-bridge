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

    def test_restarts_after_process_exit_without_replaying_inflight_request(self) -> None:
        async def scenario() -> None:
            async with make_client() as client:
                original_pid = client.diagnostics().pid
                with self.assertRaises(AppServerClosedError):
                    await client.request("test/crash")
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
