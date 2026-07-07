#!/usr/bin/env python3
"""Install or update the macOS launchd agent for the BigQuant daily daemon."""

from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_LABEL = "com.personal-strategy.bigquant-daily"
DEFAULT_PYTHON = Path("/opt/homebrew/Caskroom/miniforge/base/envs/bigquant/bin/python")
DEFAULT_PLIST = Path.home() / "Library/LaunchAgents" / f"{DEFAULT_LABEL}.plist"


def build_plist(args: argparse.Namespace) -> dict:
    log_dir = REPO_ROOT / "logs"
    run_dir = REPO_ROOT / "run"
    log_dir.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)
    program_arguments = [
        str(Path(args.python)),
        "-u",
        str(REPO_ROOT / "bigquant_daily_daemon.py"),
        "--data-time",
        args.data_time,
        "--strategy-time",
        args.strategy_time,
        "--interval-seconds",
        str(args.interval_seconds),
    ]
    return {
        "Label": args.label,
        "ProgramArguments": program_arguments,
        "WorkingDirectory": str(REPO_ROOT),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "StandardOutPath": str(log_dir / "bigquant_daily_daemon.launchd.log"),
        "StandardErrorPath": str(log_dir / "bigquant_daily_daemon.launchd.err.log"),
        "EnvironmentVariables": {
            "HOME": str(REPO_ROOT),
            "PATH": os.environ.get("PATH", ""),
        },
    }


def run_launchctl(command: list[str]) -> None:
    completed = subprocess.run(command, text=True, capture_output=True)
    if completed.returncode != 0:
        text = (completed.stderr or completed.stdout or "").strip()
        if text:
            print(text)


def install(args: argparse.Namespace) -> None:
    plist_path = Path(args.plist)
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    with plist_path.open("wb") as handle:
        plistlib.dump(build_plist(args), handle, sort_keys=False)

    run_launchctl(["launchctl", "bootout", f"gui/{os.getuid()}", str(plist_path)])
    run_launchctl(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist_path)])
    run_launchctl(["launchctl", "enable", f"gui/{os.getuid()}/{args.label}"])
    run_launchctl(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{args.label}"])
    print(f"Installed launchd agent: {plist_path}")


def uninstall(args: argparse.Namespace) -> None:
    plist_path = Path(args.plist)
    run_launchctl(["launchctl", "bootout", f"gui/{os.getuid()}", str(plist_path)])
    if plist_path.exists():
        plist_path.unlink()
    print(f"Uninstalled launchd agent: {plist_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install the BigQuant daily daemon as a macOS launchd agent.")
    parser.add_argument("--label", default=DEFAULT_LABEL)
    parser.add_argument("--plist", default=str(DEFAULT_PLIST))
    parser.add_argument("--python", default=str(DEFAULT_PYTHON))
    parser.add_argument("--data-time", default="16:30")
    parser.add_argument("--strategy-time", default="16:50")
    parser.add_argument("--interval-seconds", type=int, default=300)
    parser.add_argument("--uninstall", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.uninstall:
        uninstall(args)
    else:
        install(args)


if __name__ == "__main__":
    main()
