from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from agent_bridge_catalog import cli
from agent_bridge_catalog.config import Settings


def _clear_broker_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_BRIDGE_BROKER_REQUIRED", raising=False)
    monkeypatch.delenv("AGENT_BRIDGE_NATS_SERVERS", raising=False)


def test_catalog_cli_requires_broker_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_broker_environment(monkeypatch)
    monkeypatch.setenv("AGENT_BRIDGE_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["agent-bridge-catalog"])

    with pytest.raises(ValueError, match="broker is required"):
        cli.main()


def test_catalog_cli_allows_explicit_http_only_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_broker_environment(monkeypatch)
    monkeypatch.setenv("AGENT_BRIDGE_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(
        sys, "argv", ["agent-bridge-catalog", "--allow-http-only"]
    )
    launched: dict[str, object] = {}
    monkeypatch.setattr(cli.uvicorn, "run", lambda *args, **kwargs: launched.update(kwargs))

    cli.main()

    assert launched["host"] == "127.0.0.1"
    assert Settings.from_environment().broker_required is False


def test_required_broker_configuration_accepts_servers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AGENT_BRIDGE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_BRIDGE_BROKER_REQUIRED", "1")
    monkeypatch.setenv("AGENT_BRIDGE_NATS_SERVERS", "nats://127.0.0.1:4222")

    settings = Settings.from_environment()

    assert settings.broker_required is True
    assert settings.nats_servers == ("nats://127.0.0.1:4222",)


def test_cli_import_does_not_construct_app_before_broker_policy() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import agent_bridge_catalog.cli; "
            "assert 'agent_bridge_catalog.app' not in sys.modules",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
