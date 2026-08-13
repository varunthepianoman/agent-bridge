from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from pathlib import Path


def default_state_dir() -> Path:
    configured = os.environ.get("XDG_STATE_HOME")
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_absolute():
            return candidate / "agent-bridge"
    return Path.home() / ".local" / "state" / "agent-bridge"


@dataclass(frozen=True, slots=True)
class Settings:
    state_dir: Path
    database_url: str
    node_id: str
    environment_id: str
    codex_bin: str = "codex"
    native_launch_enabled: bool = False
    nats_servers: tuple[str, ...] = ()
    broker_required: bool = False
    nats_credentials_file: Path | None = None
    nats_username: str | None = None
    nats_password: str | None = None
    nats_client_name: str = "agent-bridge-catalog"
    result_consumer_durable: str = "catalog-execution-results-v1"
    coordinator_enabled: bool = False
    coordinator_holder_id: str = "catalog-portfolio-runtime"
    coordinator_model: str | None = None
    coordinator_workspace: Path | None = None
    coordinator_lease_seconds: float = 300.0
    metrics_enabled: bool = False
    telemetry_interval_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.broker_required and not self.nats_servers:
            raise ValueError(
                "broker is required but AGENT_BRIDGE_NATS_SERVERS is not configured"
            )
        if (self.nats_username is None) != (self.nats_password is None):
            raise ValueError("NATS username and password must be configured together")
        if self.nats_credentials_file and self.nats_username:
            raise ValueError("use either a NATS credentials file or username/password")
        if self.telemetry_interval_seconds <= 0:
            raise ValueError("telemetry interval must be positive")

    @classmethod
    def from_environment(cls) -> Settings:
        state_dir = Path(os.environ.get("AGENT_BRIDGE_STATE_DIR", default_state_dir()))
        database_url = os.environ.get(
            "AGENT_BRIDGE_DATABASE_URL", f"sqlite:///{state_dir / 'catalog.db'}"
        )
        servers = tuple(
            item.strip()
            for item in os.environ.get("AGENT_BRIDGE_NATS_SERVERS", "").split(",")
            if item.strip()
        )
        credentials = os.environ.get("AGENT_BRIDGE_NATS_CREDENTIALS_FILE")
        return cls(
            state_dir=state_dir,
            database_url=database_url,
            node_id=os.environ.get("AGENT_BRIDGE_NODE_ID", socket.gethostname()),
            environment_id=os.environ.get("AGENT_BRIDGE_ENVIRONMENT_ID", "host"),
            codex_bin=os.environ.get("AGENT_BRIDGE_CODEX_BIN", "codex"),
            native_launch_enabled=os.environ.get("AGENT_BRIDGE_NATIVE_LAUNCH", "0") == "1",
            nats_servers=servers,
            broker_required=os.environ.get("AGENT_BRIDGE_BROKER_REQUIRED", "0") == "1",
            nats_credentials_file=Path(credentials) if credentials else None,
            nats_username=os.environ.get("AGENT_BRIDGE_NATS_USERNAME"),
            nats_password=os.environ.get("AGENT_BRIDGE_NATS_PASSWORD"),
            nats_client_name=os.environ.get(
                "AGENT_BRIDGE_NATS_CLIENT_NAME", "agent-bridge-catalog"
            ),
            result_consumer_durable=os.environ.get(
                "AGENT_BRIDGE_RESULT_CONSUMER", "catalog-execution-results-v1"
            ),
            coordinator_enabled=os.environ.get("AGENT_BRIDGE_COORDINATOR_ENABLED", "0") == "1",
            coordinator_holder_id=os.environ.get(
                "AGENT_BRIDGE_COORDINATOR_HOLDER_ID", "catalog-portfolio-runtime"
            ),
            coordinator_model=os.environ.get("AGENT_BRIDGE_COORDINATOR_MODEL") or None,
            coordinator_workspace=(
                Path(value)
                if (value := os.environ.get("AGENT_BRIDGE_COORDINATOR_WORKSPACE"))
                else None
            ),
            coordinator_lease_seconds=float(
                os.environ.get("AGENT_BRIDGE_COORDINATOR_LEASE_SECONDS", "300")
            ),
            metrics_enabled=os.environ.get("AGENT_BRIDGE_METRICS_ENABLED", "0") == "1",
            telemetry_interval_seconds=float(
                os.environ.get("AGENT_BRIDGE_TELEMETRY_INTERVAL_SECONDS", "30")
            ),
        )
