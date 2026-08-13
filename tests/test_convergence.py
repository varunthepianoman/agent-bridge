from __future__ import annotations

import asyncio

from agent_bridge_catalog.convergence import ConvergenceController, _audit_verdict
from agent_bridge_catalog.db import Database
from agent_bridge_catalog.manual_bridge import ManualBridgeService
from agent_bridge_catalog.repository import CatalogRepository
from agent_bridge_catalog.roles import RoleStore
from agent_bridge_protocol import (
    BridgeEnvelope,
    CoordinatorRole,
    EndpointKind,
    EndpointRef,
    MessageKind,
    WorkItem,
)


class Publisher:
    def __init__(self) -> None:
        self.published: list[tuple[BridgeEnvelope, str | None]] = []

    async def publish(self, envelope: BridgeEnvelope, *, subject: str | None = None):
        self.published.append((envelope, subject))
        return type("Ack", (), {"stream": "WORK", "sequence": 1, "duplicate": False})()


def test_audit_verdict_is_explicit_and_exact() -> None:
    assert _audit_verdict("VERDICT: accepted\nLooks good") == "accepted"
    assert _audit_verdict("VERDICT: changes_requested\nFix it") == "changes_requested"
    assert _audit_verdict("I accept this") is None


def test_convergence_reuses_two_role_conversations_and_stops_on_acceptance(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'loop.db'}")
    database.initialize()
    repository = CatalogRepository(database)
    roles = RoleStore(database)
    publisher = Publisher()
    bridge = ManualBridgeService(database, publisher=publisher)
    developer_conversation = repository.upsert_discovered(
        {"provider_thread_id": "thread-dev", "title": "Developer"},
        node_id="node-a",
        environment_id="host",
    )
    auditor_conversation = repository.upsert_discovered(
        {"provider_thread_id": "thread-audit", "title": "Auditor"},
        node_id="node-a",
        environment_id="host",
    )
    roles.create_work(
        WorkItem(
            work_id="work-loop",
            title="Converge",
            extensions={
                "agent_bridge.convergence": {
                    "enabled": True,
                    "developer_role_id": "role-dev",
                    "auditor_role_id": "role-audit",
                    "max_rounds": 10,
                    "state": {"round": 0, "status": "implementation"},
                }
            },
        )
    )
    for role_id, conversation_id in (
        ("role-dev", developer_conversation.conversation_id),
        ("role-audit", auditor_conversation.conversation_id),
    ):
        roles.create_role(
            CoordinatorRole(
                role_id=role_id,
                role_type="worker",
                scope="work:work-loop",
                charter=role_id,
                authority_profile="local",
                current_conversation_id=conversation_id,
                extensions={
                    "agent_bridge.runner_node": "node-a",
                    "agent_bridge.cwd": "/repo",
                },
            )
        )
    controller = ConvergenceController(bridge, roles, repository)

    async def result(execution_id: str, stage: str, response: str) -> None:
        created = await bridge.submit_request(
            request_input={
                "operation": "new_execution",
                "instruction": "test",
                "target": {"kind": "node", "id": "node-a"},
                "work_id": "work-loop",
                "parameters": {"stage": stage},
            }
        )
        actual_id = created["execution"]["execution_id"]
        envelope = BridgeEnvelope(
            message_id=f"result-{execution_id}",
            kind=MessageKind.RESPONSE,
            sender=EndpointRef(kind=EndpointKind.NODE, id="node-a"),
            destination=EndpointRef(kind=EndpointKind.ENDPOINT, id="catalog-user"),
            body={
                "execution_id": actual_id,
                "attempt_id": created["execution"]["attempts"][0]["attempt_id"],
                "status": "succeeded",
                "summary": "done",
                "output": {"final_response": response},
            },
        )
        await controller.process(envelope)

    asyncio.run(result("intake", "review_intake_and_plan", "Plan ready"))
    state = roles.get_work("work-loop").extensions["agent_bridge.convergence"]["state"]
    assert state["status"] == "awaiting_user_implementation_approval"

    asyncio.run(controller.approve_implementation("work-loop"))
    dispatched = publisher.published[-1][0].body
    assert dispatched["operation"] == "resume_conversation"
    assert dispatched["conversation_id"] == "thread-dev"
    assert dispatched["parameters"]["stage"] == "implementation"
    assert dispatched["extensions"]["agent_bridge.workflow"]["remote_write_authorized"] is False

    asyncio.run(result("one", "implementation", "Implemented and tested"))
    dispatched = publisher.published[-1][0].body
    assert dispatched["operation"] == "resume_conversation"
    assert dispatched["conversation_id"] == "thread-audit"
    assert dispatched["parameters"]["stage"] == "audit"

    asyncio.run(result("two", "audit", "VERDICT: changes_requested\nFix race"))
    dispatched = publisher.published[-1][0].body
    assert dispatched["conversation_id"] == "thread-dev"
    state = roles.get_work("work-loop").extensions["agent_bridge.convergence"]["state"]
    assert state["round"] == 1

    published_before = len(publisher.published)
    asyncio.run(result("three", "audit", "VERDICT: accepted\nTests pass"))
    assert len(publisher.published) == published_before + 2
    assert publisher.published[-1][0].body["parameters"]["stage"] == "draft_replies"

    asyncio.run(result("four", "draft_replies", "Commit abc; draft replies ready"))
    state = roles.get_work("work-loop").extensions["agent_bridge.convergence"]["state"]
    assert state["status"] == "awaiting_publish_approval"

    asyncio.run(controller.approve_publish("work-loop"))
    published = publisher.published[-1][0].body
    assert published["parameters"]["stage"] == "publish"
    assert published["extensions"]["agent_bridge.workflow"]["remote_write_authorized"] is True

    asyncio.run(result("five", "publish", "Pushed and posted"))
    state = roles.get_work("work-loop").extensions["agent_bridge.convergence"]["state"]
    assert state["status"] == "completed"

    work = roles.get_work("work-loop")
    policy = dict(work.extensions["agent_bridge.convergence"])
    policy.update(
        {
            "publish_gate_required": False,
            "state": {"round": 0, "status": "preparing_publish_package"},
        }
    )
    roles.update_work(
        "work-loop",
        {"extensions": {**work.extensions, "agent_bridge.convergence": policy}},
    )
    asyncio.run(result("six", "draft_replies", "Automatic publish package ready"))
    published = publisher.published[-1][0].body
    assert published["parameters"]["stage"] == "publish"
    assert published["extensions"]["agent_bridge.workflow"]["remote_write_authorized"] is True
    state = roles.get_work("work-loop").extensions["agent_bridge.convergence"]["state"]
    assert state["status"] == "publishing"
