from __future__ import annotations

import os
import shutil


def elevated(command: list[str]) -> list[str]:
    if os.geteuid() == 0:
        return command
    if shutil.which("pkexec"):
        return ["pkexec", *command]
    if shutil.which("sudo"):
        return ["sudo", *command]
    raise PermissionError("This step requires root. Re-run as root or install pkexec/sudo.")

