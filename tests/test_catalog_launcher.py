from agent_bridge_catalog.launcher import _parse_generated_command


def test_claude_generated_resume_is_parsed_without_a_shell() -> None:
    argv, cwd = _parse_generated_command("cd '/work/robot project' && claude --resume session-1")

    assert cwd == "/work/robot project"
    assert argv == ["claude", "--resume", "session-1"]


def test_unrecognized_shell_operators_are_not_executed() -> None:
    argv, cwd = _parse_generated_command("claude --resume safe && touch /tmp/not-created")

    assert cwd is None
    assert argv == ["claude", "--resume", "safe", "&&", "touch", "/tmp/not-created"]
