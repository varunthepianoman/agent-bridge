"""Stable Python Codex SDK adapter for structured coordinator turns."""

from __future__ import annotations

import importlib
from copy import deepcopy
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent_bridge_protocol.models import CoordinatorRole

from .engine import ConversationUnavailable
from .models import (
    BudgetUsage,
    CoordinatorModelOutput,
    CoordinatorSession,
    CoordinatorTurn,
)


class CodexCoordinatorModel:
    """Uses output_schema and rejects non-JSON/prose responses instead of guessing."""

    def __init__(
        self,
        *,
        model: str | None = None,
        sandbox: str = "full_access",
        approval_mode: str = "deny_all",
        default_cwd: Path | None = None,
        unavailable_error: Callable[[Exception], bool] | None = None,
    ) -> None:
        self.model = model
        self.sandbox = sandbox
        self.approval_mode = approval_mode
        self.default_cwd = default_cwd
        self.unavailable_error = unavailable_error or _looks_like_missing_thread
        self._active: dict[str, tuple[Any, Any]] = {}

    async def prepare(
        self,
        role: CoordinatorRole,
        *,
        current_conversation_id: str | None,
        current_provider_thread_id: str | None,
        cwd: str | None,
        force_new: bool = False,
    ) -> CoordinatorSession:
        try:
            sdk = importlib.import_module("openai_codex")
        except ImportError as error:
            raise RuntimeError(
                "Codex coordinator requires the 'codex' optional dependency"
            ) from error
        client: Any = sdk.AsyncCodex()
        await client.__aenter__()
        options: dict[str, Any] = {
            "approval_mode": getattr(sdk.ApprovalMode, self.approval_mode),
            "sandbox": getattr(sdk.Sandbox, self.sandbox),
        }
        if self.model is not None:
            options["model"] = self.model
        resolved_cwd = cwd or (str(self.default_cwd) if self.default_cwd else None)
        if resolved_cwd is not None:
            options["cwd"] = resolved_cwd
        try:
            if current_provider_thread_id is not None and not force_new:
                thread = await client.thread_resume(current_provider_thread_id, **options)
            else:
                thread = await client.thread_start(**options)
        except Exception:
            await client.__aexit__(None, None, None)
            raise
        adapter_handle = f"codex-session-{uuid4().hex}"
        session = CoordinatorSession(
            conversation_id=current_conversation_id or str(thread.id),
            provider_thread_id=str(thread.id),
            cwd=resolved_cwd,
            is_replacement=(
                force_new or current_conversation_id is None or current_provider_thread_id is None
            ),
            handoff_summary=(
                f"Replaced unavailable coordinator conversation {current_conversation_id}"
                if force_new and current_conversation_id
                else None
            ),
            state={"codex_adapter_handle": adapter_handle},
        )
        self._active[adapter_handle] = (client, thread)
        return session

    async def run(self, session: CoordinatorSession, prompt: str) -> CoordinatorTurn:
        active = self._active.pop(_adapter_handle(session), None)
        if active is None:
            raise RuntimeError("coordinator session was not prepared by this model adapter")
        client, thread = active
        try:
            result = await thread.run(
                prompt,
                output_schema=_strict_output_schema(CoordinatorModelOutput.model_json_schema()),
            )
            if result.final_response is None:
                raise ValueError("Codex coordinator returned no final structured response")
            output = CoordinatorModelOutput.model_validate_json(result.final_response)
            total = result.usage.total if result.usage is not None else None
            usage = BudgetUsage(
                attempts=1,
                input_tokens=total.input_tokens if total is not None else 0,
                output_tokens=total.output_tokens if total is not None else 0,
                cost_known=False,
            )
            return CoordinatorTurn(output=output, session=session, usage=usage)
        except Exception as error:
            if self.unavailable_error(error):
                raise ConversationUnavailable(str(error)) from error
            raise
        finally:
            await client.__aexit__(None, None, None)

    async def abort(self, session: CoordinatorSession) -> None:
        active = self._active.pop(_adapter_handle(session), None)
        if active is not None:
            client, _thread = active
            await client.__aexit__(None, None, None)


def _adapter_handle(session: CoordinatorSession) -> str:
    handle = session.state.get("codex_adapter_handle")
    if not isinstance(handle, str) or not handle:
        raise RuntimeError("coordinator session has no Codex adapter handle")
    return handle


def _strict_output_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Adapt Pydantic's schema to the strict object contract used by Codex.

    Pydantic omits defaulted fields from ``required``. Codex structured output
    instead requires every declared property to be present; optional values are
    expressed by the nullable type already emitted by Pydantic.
    """
    strict = deepcopy(schema)

    def visit(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item)
            return
        if not isinstance(node, dict):
            return
        properties = node.get("properties")
        if isinstance(properties, dict):
            node["required"] = list(properties)
            node.setdefault("additionalProperties", False)
        elif node.get("type") == "object" and node.get("additionalProperties") is True:
            # Strict structured output cannot express an arbitrary JSON mapping.
            # Expose the closed subset currently understood by the Bridge executor.
            # Arbitrary parameters/extensions need first-class models before they can
            # safely become coordinator output.
            node.clear()
            node.update(_coordinator_payload_schema())
        node.pop("default", None)
        for value in node.values():
            visit(value)

    visit(strict)
    return strict


def _coordinator_payload_schema() -> dict[str, Any]:
    nullable_string = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    target = {
        "anyOf": [
            {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": [
                            "conversation",
                            "role",
                            "node",
                            "capability",
                            "room",
                            "endpoint",
                        ],
                    },
                    "id": {"type": "string"},
                },
                "required": ["kind", "id"],
                "additionalProperties": False,
            },
            {"type": "null"},
        ]
    }
    properties = {
        "operation": {
            "type": "string",
            "enum": [
                "new_execution",
                "resume_conversation",
                "wake_endpoint",
                "invoke_adapter",
            ],
        },
        "instruction": deepcopy(nullable_string),
        "target": target,
        "conversation_id": deepcopy(nullable_string),
        "adapter": deepcopy(nullable_string),
        "work_id": deepcopy(nullable_string),
        "repository_id": deepcopy(nullable_string),
        "path": deepcopy(nullable_string),
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _looks_like_missing_thread(error: Exception) -> bool:
    text = str(error).lower()
    return "thread" in text and any(
        marker in text for marker in ("not found", "missing", "does not exist", "unknown")
    )
