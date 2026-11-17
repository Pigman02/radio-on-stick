#!/usr/bin/env python3
"""Cross-platform ffmpeg detection/installer for Radio on Stick.

Usage:
    python3 deploy/ensure_ffmpeg.py --check
    sudo python3 deploy/ensure_ffmpeg.py --install

Supported package managers:
  Linux: apt-get, dnf, yum, pacman, apk, zypper
  macOS: Homebrew, MacPorts
  Windows: winget, Chocolatey, Scoop
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.ffmpeg import ffmpeg_status, resolve_ffmpeg  # noqa: E402


def _run(command: list[str], *, quiet: bool = False) -> bool:
    if not quiet:
        print("+ " + " ".join(command), flush=True)
    try:
        result = subprocess.run(
            command,
            check=False,
            stdout=subprocess.DEVNULL if quiet else None,
            stderr=subprocess.DEVNULL if quiet else None,
        )
    except OSError as exc:
        print(f"无法执行 {command[0]}: {exc}", file=sys.stderr)
        return False
    return result.returncode == 0


def _sudo(command: list[str]) -> list[str]:
    if os.name != "posix" or os.geteuid() == 0:
        return command
    sudo = shutil.which("sudo")
    if not sudo:
        raise RuntimeError("安装系统包需要 root；请用 sudo 运行本脚本")
    return [sudo, *command]


def linux_manager() -> tuple[str, list[list[str]]] | None:
    if shutil.which("apt-get"):
        return "apt", [
            ["apt-get", "update"],
            ["apt-get", "install", "-y", "--no-install-recommends", "ffmpeg"],
        ]
    if shutil.which("dnf"):
        return "dnf", [["dnf", "install", "-y", "ffmpeg"]]
    if shutil.which("yum"):
        return "yum", [["yum", "install", "-y", "ffmpeg"]]
    if shutil.which("pacman"):
        return "pacman", [["pacman", "-Sy", "--noconfirm", "ffmpeg"]]
    if shutil.which("apk"):
        return "apk", [["apk", "add", "--no-cache", "ffmpeg"]]
    if shutil.which("zypper"):
        return "zypper", [["zypper", "--non-interactive", "install", "ffmpeg"]]
    return None


def install_ffmpeg(*, quiet: bool = False, ffmpeg_path: str = "ffmpeg") -> bool:
    if resolve_ffmpeg(ffmpeg_path):
        return True
    system = platform.system().lower()
    commands: list[list[str]] = []
    if system == "linux":
        manager = linux_manager()
        if manager is None:
            print("未找到 apt/dnf/yum/pacman/apk/zypper，请手动安装 ffmpeg。", file=sys.stderr)
            return False
        name, commands = manager
        try:
            commands = [_sudo(list(command)) for command in commands]
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return False
    elif system == "darwin":
        if shutil.which("brew"):
            commands = [["brew", "install", "ffmpeg"]]
        elif shutil.which("port"):
            commands = [_sudo(["port", "install", "ffmpeg"])]
        else:
            print("未找到 Homebrew/MacPorts，请手动安装 ffmpeg。", file=sys.stderr)
            return False
    elif system == "windows":
        if shutil.which("winget"):
            commands = [[
                "winget", "install", "--id", "Gyan.FFmpeg", "-e",
                "--accept-source-agreements", "--accept-package-agreements",
            ]]
        elif shutil.which("choco"):
            commands = [["choco", "install", "ffmpeg", "-y"]]
        elif shutil.which("scoop"):
            commands = [["scoop", "install", "ffmpeg"]]
        else:
            print("未找到 winget/choco/scoop，请手动安装 ffmpeg。", file=sys.stderr)
            return False
    else:
        print(f"未知系统 {system}，请手动安装 ffmpeg。", file=sys.stderr)
        return False

    for command in commands:
        if not _run(command, quiet=quiet):
            # Do not abort immediately: a package manager may return a warning
            # while an already-installed ffmpeg is still usable.
            break
    return bool(resolve_ffmpeg(ffmpeg_path))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只检测，不安装")
    parser.add_argument("--install", action="store_true", help="检测并尝试安装")
    parser.add_argument("--json", action="store_true", help="输出 JSON 状态")
    parser.add_argument("--quiet", action="store_true", help="安装时少输出")
    parser.add_argument("--ffmpeg", default=os.environ.get("RADIO_FFMPEG", "ffmpeg"))
    args = parser.parse_args()
    if not args.check and not args.install:
        args.check = True

    status = ffmpeg_status(args.ffmpeg)
    if status["available"]:
        if args.json:
            print(json.dumps(status, ensure_ascii=False))
        elif not args.quiet:
            print(f"ffmpeg 已就绪: {status['version']} ({status['path']})")
        return 0

    if args.install:
        ok = install_ffmpeg(quiet=args.quiet, ffmpeg_path=args.ffmpeg)
        status = ffmpeg_status(args.ffmpeg)
        if args.json:
            print(json.dumps(status, ensure_ascii=False))
        elif not args.quiet:
            if ok:
                print(f"ffmpeg 安装成功: {status['version']} ({status['path']})")
            else:
                print("ffmpeg 安装失败，请手动安装。", file=sys.stderr)
        return 0 if ok else 1

    if args.json:
        print(json.dumps(status, ensure_ascii=False))
    else:
        print("ffmpeg 未安装。运行下面的命令尝试自动安装：", file=sys.stderr)
        print("  sudo python3 deploy/ensure_ffmpeg.py --install", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
