from unittest.mock import Mock, patch

from agent_bridge_catalog.launcher import NativeLauncher, _parse_generated_command


def test_claude_generated_resume_is_parsed_without_a_shell() -> None:
    argv, cwd = _parse_generated_command(
        "cd '/work/robot project' && claude --dangerously-skip-permissions --resume session-1"
    )

    assert cwd == "/work/robot project"
    assert argv == ["claude", "--dangerously-skip-permissions", "--resume", "session-1"]


def test_unrecognized_shell_operators_are_not_executed() -> None:
    argv, cwd = _parse_generated_command("claude --resume safe && touch /tmp/not-created")

    assert cwd is None
    assert argv == ["claude", "--resume", "safe", "&&", "touch", "/tmp/not-created"]


@patch("agent_bridge_catalog.launcher.subprocess.Popen")
@patch("agent_bridge_catalog.launcher.shutil.which", return_value="/usr/bin/x-terminal-emulator")
@patch("agent_bridge_catalog.launcher.os.path.realpath", return_value="/usr/bin/terminator")
def test_terminator_uses_execute_argv_and_working_directory(
    _realpath: Mock, _which: Mock, popen: Mock
) -> None:
    result = NativeLauncher(enabled=True).launch(
        "cd '/work/robot project' && claude --dangerously-skip-permissions --resume session-1",
        requested=True,
    )

    assert result.launched
    popen.assert_called_once_with(
        [
            "/usr/bin/x-terminal-emulator",
            "--working-directory",
            "/work/robot project",
            "-x",
            "claude",
            "--dangerously-skip-permissions",
            "--resume",
            "session-1",
        ],
        start_new_session=True,
    )


@patch("agent_bridge_catalog.launcher.subprocess.Popen")
@patch("agent_bridge_catalog.launcher.shutil.which", return_value="/usr/bin/xdg-open")
def test_desktop_url_is_opened_by_the_operating_system(_which: Mock, popen: Mock) -> None:
    result = NativeLauncher(enabled=True).open_url(
        "claude://code/new?folder=%2Fwork%2Frepo", requested=True
    )

    assert result.launched
    popen.assert_called_once_with(
        ["/usr/bin/xdg-open", "claude://code/new?folder=%2Fwork%2Frepo"],
        start_new_session=True,
        stdout=-3,
        stderr=-3,
    )
