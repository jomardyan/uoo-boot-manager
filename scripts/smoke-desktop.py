#!/usr/bin/env python3
"""Check --help and a real demo window without touching boot configuration."""

import argparse
import os
import signal
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        parser.error("Provide the desktop command after --")
    help_result = subprocess.run([*command, "--help"], capture_output=True, text=True, timeout=30)
    if help_result.returncode or "--demo" not in help_result.stdout:
        sys.exit("Desktop --help failed:\n" + help_result.stdout + help_result.stderr)
    process = subprocess.Popen([*command, "--demo"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, start_new_session=True)
    try:
        stdout, stderr = process.communicate(timeout=8)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate(timeout=5)
        if "Traceback (most recent call last)" in stderr:
            sys.exit("Desktop raised an exception:\n" + stderr)
        print("Desktop --help and eight-second demo startup passed.")
        return
    sys.exit(f"Demo exited before the startup check (status {process.returncode}):\n{stdout}{stderr}")


if __name__ == "__main__":
    main()
