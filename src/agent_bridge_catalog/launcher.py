from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class LaunchResult:
    command: str
    launched: bool
    detail: str


class NativeLauncher:
    def __init__(self, *, enabled: bool) -> None:
        self.enabled = enabled

    def launch(self, command: str, *, requested: bool) -> LaunchResult:
        if not requested:
            return LaunchResult(command, False, "Command prepared; launch was not requested")
        if not self.enabled:
            return LaunchResult(
                command,
                False,
                "Native launch is disabled; copy the command or set AGENT_BRIDGE_NATIVE_LAUNCH=1",
            )
        argv, cwd = _parse_generated_command(command)
        if os.name == "nt":
            terminal_argv = ["wt.exe"]
            if cwd is not None:
                terminal_argv.extend(["-d", cwd])
            subprocess.Popen([*terminal_argv, *argv])
            return LaunchResult(command, True, "Opened in Windows Terminal")
        terminal = shutil.which("x-terminal-emulator")
        if terminal:
            launch_argv = [terminal, "-e", *argv]
            if cwd is not None:
                launch_argv = [terminal, "-e", "env", f"--chdir={cwd}", *argv]
            subprocess.Popen(launch_argv, start_new_session=True)
            return LaunchResult(command, True, "Opened in the system terminal")
        return LaunchResult(command, False, "No supported terminal launcher was found")


def _parse_generated_command(command: str) -> tuple[list[str], str | None]:
    """Parse the one compound form generated for providers that require a cwd.

    This deliberately does not invoke a shell. Any other shell operator remains
    an ordinary argument and cannot become executable syntax.
    """

    argv = shlex.split(command, posix=os.name != "nt")
    if len(argv) >= 4 and argv[0] == "cd" and argv[2] == "&&":
        return argv[3:], argv[1]
    return argv, None
