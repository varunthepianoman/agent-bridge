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
        if stage not in {
            "review_intake",
            "review_intake_and_plan",
            "implementation",
            "audit",
            "draft_replies",
            "publish",
        }:
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
        if stage in {"review_intake", "review_intake_and_plan"}:
            state["status"] = "awaiting_user_implementation_approval"
            self._persist(work.work_id, extensions, policy, state)
            return
        if stage == "draft_replies":
            if bool(policy.get("publish_gate_required", True)):
                state["status"] = "awaiting_publish_approval"
                self._persist(work.work_id, extensions, policy, state)
                return
            state["status"] = "publishing"
            self._persist(work.work_id, extensions, policy, state)
            await self._dispatch_publish(work.work_id, policy)
            return
        if stage == "publish":
            state["status"] = "completed"
            self._persist(work.work_id, extensions, policy, state)
            return
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
            state["status"] = "preparing_publish_package"
            self._persist(work.work_id, extensions, policy, state)
            await self._dispatch(
                work_id=work.work_id,
                role_id=str(policy["developer_role_id"]),
                stage="draft_replies",
                instruction=(
                    "The auditor accepted the implementation. Prepare the complete local "
                    "publish package: verify the final diff and tests, create one local commit "
                    "per review-thread topic, and draft the exact GitHub review-thread replies. "
                    "Each draft reply must identify and link the commit hash for its topic so "
                    "the reviewer can inspect that isolated change directly. Do not push, post, "
                    "reply, resolve threads, or make "
                    "any other remote change. Report the commit SHA, test evidence, and exact "
                    "draft replies for the single publish gate. If blocked, begin with `Blocked`."
                ),
            )
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

    async def approve_implementation(self, work_id: str) -> dict[str, Any]:
        work = self.roles.get_work(work_id)
        if work is None:
            raise LookupError(f"work item not found: {work_id}")
        extensions = dict(work.extensions)
        policy = extensions.get("agent_bridge.convergence")
        if not isinstance(policy, dict) or not policy.get("enabled"):
            raise ValueError("convergence workflow is not enabled")
        state = dict(policy.get("state") or {})
        if state.get("status") != "awaiting_user_implementation_approval":
            raise ValueError(
                "implementation gate is not ready; current state is "
                f"{state.get('status', 'unknown')}"
            )
        state.update({"status": "implementing", "implementation_approved": True})
        self._persist(work_id, extensions, policy, state)
        try:
            await self._dispatch(
                work_id=work_id,
                role_id=str(policy["developer_role_id"]),
                stage="implementation",
                instruction=(
                    "The user approved the implementation proposal recorded during review "
                    "intake. Execute that approved plan now, including its documented scope, "
                    "tests, and local commit policy. Update the canonical review documents "
                    "with accurate after-state evidence. This authorizes local code and "
                    "documentation edits, tests, and local commits only. Do not push, post or "
                    "reply on GitHub, submit a review, resolve threads, or make any other "
                    "remote change. If blocked, begin with `Blocked` and explain why."
                ),
            )
        except Exception:
            state.update(
                {
                    "status": "awaiting_user_implementation_approval",
                    "implementation_approved": False,
                }
            )
            self._persist(work_id, extensions, policy, state)
            raise
        return {"work_id": work_id, "status": "implementing"}

    async def approve_publish(self, work_id: str) -> dict[str, Any]:
        work = self.roles.get_work(work_id)
        if work is None:
            raise LookupError(f"work item not found: {work_id}")
        extensions = dict(work.extensions)
        policy = extensions.get("agent_bridge.convergence")
        if not isinstance(policy, dict) or not policy.get("enabled"):
            raise ValueError("convergence workflow is not enabled")
        state = dict(policy.get("state") or {})
        if state.get("status") != "awaiting_publish_approval":
            raise ValueError(
                f"publish gate is not ready; current state is {state.get('status', 'unknown')}"
            )
        state["status"] = "publishing"
        self._persist(work_id, extensions, policy, state)
        try:
            await self._dispatch_publish(work_id, policy)
        except Exception:
            state["status"] = "awaiting_publish_approval"
            self._persist(work_id, extensions, policy, state)
            raise
        return {"work_id": work_id, "status": "publishing"}

    async def _dispatch_publish(self, work_id: str, policy: dict[str, Any]) -> None:
        gate_required = bool(policy.get("publish_gate_required", True))
        authorization = (
            "The user approved the single publish gate."
            if gate_required
            else "This work item's publish policy authorizes automatic publishing."
        )
        await self._dispatch(
            work_id=work_id,
            role_id=str(policy["developer_role_id"]),
            stage="publish",
            remote_write_authorized=True,
            instruction=(
                f"{authorization} Re-verify the intended repository, branch, authenticated "
                "GitHub identity, local commit, and exact drafted replies. Then push the "
                "approved code and post the drafted GitHub review-thread replies. Do not "
                "expand scope or alter the approved substance. Report every remote action "
                "and link. If any verification fails, begin with `Blocked` and make no "
                "further remote changes."
            ),
        )

    async def _dispatch(
        self,
        *,
        work_id: str,
        role_id: str,
        stage: str,
        instruction: str,
        remote_write_authorized: bool = False,
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
                        "remote_write_authorized": remote_write_authorized,
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
