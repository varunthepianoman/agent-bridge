from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class LaunchResult:
    command: str
    launched: bool
    detail: str


class NativeLauncher:
    def launch(self, command: str, *, requested: bool) -> LaunchResult:
        if not requested:
            return LaunchResult(command, False, "Command prepared; launch was not requested")
        argv, cwd = _parse_generated_command(command)
        if os.name == "nt":
            terminal_argv = ["wt.exe"]
            if cwd is not None:
                terminal_argv.extend(["-d", cwd])
            subprocess.Popen([*terminal_argv, *argv])
            return LaunchResult(command, True, "Opened in Windows Terminal")
        terminal = shutil.which("x-terminal-emulator")
        if terminal:
            terminal_name = Path(os.path.realpath(terminal)).name
            if terminal_name == "terminator":
                launch_argv = [terminal]
                if cwd is not None:
                    launch_argv.extend(["--working-directory", cwd])
                launch_argv.extend(["-x", *argv])
            else:
                launch_argv = [terminal, "-e", *argv]
                if cwd is not None:
                    launch_argv = [terminal, "-e", "env", f"--chdir={cwd}", *argv]
            subprocess.Popen(launch_argv, start_new_session=True)
            return LaunchResult(command, True, "Opened in the system terminal")
        return LaunchResult(command, False, "No supported terminal launcher was found")

    def open_url(self, url: str, *, requested: bool) -> LaunchResult:
        if not requested:
            return LaunchResult(url, False, "Desktop link prepared; launch was not requested")
        if os.name == "nt":
            os.startfile(url)  # type: ignore[attr-defined]
            return LaunchResult(url, True, "Opened in the desktop app")
        opener = shutil.which("open" if sys.platform == "darwin" else "xdg-open")
        if opener is None:
            return LaunchResult(url, False, "No supported desktop URL opener was found")
        subprocess.Popen(
            [opener, url],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return LaunchResult(url, True, "Opened in the desktop app")


def _parse_generated_command(command: str) -> tuple[list[str], str | None]:
    """Parse the one compound form generated for providers that require a cwd.

    This deliberately does not invoke a shell. Any other shell operator remains
    an ordinary argument and cannot become executable syntax.
    """

    argv = shlex.split(command, posix=os.name != "nt")
    if len(argv) >= 4 and argv[0] == "cd" and argv[2] == "&&":
        return argv[3:], argv[1]
    return argv, None
