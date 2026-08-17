#!/usr/bin/env python3
"""Generate the shared JSON Schema bundle consumed by Python and TypeScript."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import TypeAdapter

from agent_bridge_protocol import BridgeEnvelope


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    output = root / "schemas" / "agent-bridge-v1.schema.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    schema = TypeAdapter(BridgeEnvelope).json_schema()
    schema["$id"] = "https://agent-bridge.local/schemas/agent-bridge-v1.schema.json"
    schema["title"] = "AgentBridgeProtocolV1"
    output.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
