from __future__ import annotations

import pytest

from agent_bridge_node.config import ExclusionRules, NodeAgentSettings


def test_non_loopback_node_hub_requires_https() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        NodeAgentSettings(hub_url="http://catalog.internal", token="secret")
    assert NodeAgentSettings(hub_url="http://127.0.0.1:8000", token="secret").hub_url


def test_exclusions_cover_all_local_privacy_dimensions() -> None:
    rules = ExclusionRules(
        providers=("claude",),
        repositories=("*private.git",),
        folders=("C:/secret", "/srv/customer/*"),
        conversations=("thread-secret-*",),
    )
    assert rules.excludes(provider="CLAUDE", provider_thread_id="ok", cwd="/tmp", repository=None)
    assert rules.excludes(
        provider="codex", provider_thread_id="ok", cwd="/tmp", repository="ssh://private.git"
    )
    assert rules.excludes(
        provider="codex", provider_thread_id="ok", cwd=r"C:\secret\project", repository=None
    )
    assert rules.excludes(
        provider="codex", provider_thread_id="thread-secret-7", cwd="/tmp", repository=None
    )
    assert not rules.excludes(
        provider="codex", provider_thread_id="public", cwd="/srv/open", repository=None
    )


def test_provider_concurrency_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BRIDGE_HUB_URL", "http://localhost:8000")
    monkeypatch.setenv("AGENT_BRIDGE_NODE_TOKEN", "secret")
    monkeypatch.setenv("AGENT_BRIDGE_MAX_PROVIDER_CONCURRENCY", "7")

    assert NodeAgentSettings.from_environment().max_provider_concurrency == 7
    with pytest.raises(ValueError, match="concurrency"):
        NodeAgentSettings(
            hub_url="http://localhost:8000", token="secret", max_provider_concurrency=0
        )
