"""Deterministic two-role remediation/audit convergence loops."""

from __future__ import annotations

from typing import Any

from agent_bridge_protocol.models import BridgeEnvelope

from .manual_bridge import ManualBridgeService
from .repository import CatalogRepository
from .roles import RoleStore


class ConvergenceController:
    """Advance an explicitly configured developer/auditor pair after results."""

    def __init__(
        self,
        bridge: ManualBridgeService,
        roles: RoleStore,
        conversations: CatalogRepository,
    ) -> None:
        self.bridge = bridge
        self.roles = roles
        self.conversations = conversations

    async def process(self, envelope: BridgeEnvelope) -> None:
        execution_id = envelope.body.get("execution_id")
        if not isinstance(execution_id, str):
            return
        execution = self.bridge.get_execution(execution_id)
        if execution is None or not isinstance(execution.get("work_id"), str):
            return
        work = self.roles.get_work(execution["work_id"])
        if work is None:
            return
        extensions = dict(work.extensions)
        policy = extensions.get("agent_bridge.convergence")
        if not isinstance(policy, dict) or not policy.get("enabled"):
            return
        state = dict(policy.get("state") or {})
        if state.get("last_execution_id") == execution_id:
            return
        request = execution.get("request")
        parameters = request.get("parameters") if isinstance(request, dict) else None
        stage = parameters.get("stage") if isinstance(parameters, dict) else None
        if stage not in {"implementation", "audit"}:
            return

        status = envelope.body.get("status")
        state["last_execution_id"] = execution_id
        if status != "succeeded":
            state.update({"status": "blocked", "blocked_stage": stage})
            self._persist(work.work_id, extensions, policy, state)
            return

        output = envelope.body.get("output")
        response = output.get("final_response") if isinstance(output, dict) else ""
        response = response if isinstance(response, str) else ""
        if stage == "implementation":
            state["status"] = "awaiting_audit"
            self._persist(work.work_id, extensions, policy, state)
            await self._dispatch(
                work_id=work.work_id,
                role_id=str(policy["auditor_role_id"]),
                stage="audit",
                instruction=(
                    "Audit the developer's latest candidate for this work item. Run the "
                    "required tests and inspect the actual diff. Begin your final answer with "
                    "exactly `VERDICT: accepted` or `VERDICT: changes_requested`, followed by "
                    "specific findings and evidence. Do not modify code or remote state.\n\n"
                    f"Developer report:\n{response}"
                ),
            )
            return

        verdict = _audit_verdict(response)
        if verdict == "accepted":
            state["status"] = "accepted"
            self._persist(work.work_id, extensions, policy, state)
            return
        if verdict != "changes_requested":
            state.update({"status": "blocked", "blocked_stage": "audit_verdict"})
            self._persist(work.work_id, extensions, policy, state)
            return
        round_number = int(state.get("round", 0)) + 1
        state["round"] = round_number
        if round_number >= int(policy.get("max_rounds", 10)):
            state.update({"status": "blocked", "blocked_stage": "round_limit"})
            self._persist(work.work_id, extensions, policy, state)
            return
        state["status"] = "changes_requested"
        self._persist(work.work_id, extensions, policy, state)
        await self._dispatch(
            work_id=work.work_id,
            role_id=str(policy["developer_role_id"]),
            stage="implementation",
            instruction=(
                "Address every finding in the auditor report below, run the relevant tests, "
                "and report the new candidate revision and evidence. Do not push or write to "
                "GitHub. If you cannot proceed, begin the answer with `Blocked`.\n\n"
                f"Auditor report:\n{response}"
            ),
        )

    async def _dispatch(
        self, *, work_id: str, role_id: str, stage: str, instruction: str
    ) -> None:
        role = self.roles.get_role(role_id)
        if role is None:
            raise LookupError(f"convergence role not found: {role_id}")
        node_id = role.extensions.get("agent_bridge.runner_node")
        cwd = role.extensions.get("agent_bridge.cwd")
        if not isinstance(node_id, str) or not isinstance(cwd, str):
            raise ValueError(f"convergence role {role_id} requires runner_node and cwd")
        provider_thread_id: str | None = None
        if role.current_conversation_id:
            conversation = self.conversations.get(role.current_conversation_id)
            provider_thread_id = conversation.provider_thread_id if conversation else None
        await self.bridge.submit_request(
            request_input={
                "operation": "resume_conversation" if provider_thread_id else "new_execution",
                "instruction": instruction,
                "target": {"kind": "node", "id": node_id},
                "work_id": work_id,
                "conversation_id": provider_thread_id,
                "cwd": cwd,
                "parameters": {"stage": stage, "role_id": role_id},
                "extensions": {
                    "agent_bridge.workflow": {
                        "stage": stage,
                        "role_id": role_id,
                        "remote_write_authorized": False,
                    }
                },
            }
        )

    def _persist(
        self,
        work_id: str,
        extensions: dict[str, Any],
        policy: dict[str, Any],
        state: dict[str, Any],
    ) -> None:
        updated_policy = {**policy, "state": state}
        self.roles.update_work(
            work_id, {"extensions": {**extensions, "agent_bridge.convergence": updated_policy}}
        )


def _audit_verdict(response: str) -> str | None:
    first = next((line.strip().casefold() for line in response.splitlines() if line.strip()), "")
    if first == "verdict: accepted":
        return "accepted"
    if first == "verdict: changes_requested":
        return "changes_requested"
    return None
