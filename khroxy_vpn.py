#!/usr/bin/env python3
"""Khroxy VPN backend definitions, independent of the Podman orchestrator.

Windscribe CLI is OPTIONAL. No host route, firewall, network interface,
service or VPN is modified by this module. The Khagent container owns its
namespace-local network policy and backend switching.
"""
import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys

WINDSCRIBE_VERSION = "v2.24.13"
WINDSCRIBE_RELEASE = "https://github.com/Windscribe/Desktop-App/releases/download/"
WINDSCRIBE_ASSETS = {
    "amd64": (
        "windscribe-cli_2.24.13_amd64.deb",
        "96b5d3c627342fe6b5de79d04d194a61458196b72ff4ccd0da1b59a75f804fb0",
    ),
    "arm64": (
        "windscribe-cli_2.24.13_arm64.deb",
        "38cfb7d223262b9ea90a0633b6066080837e1ed7aef555edf501d136c92bbb19",
    ),
}

class BackendUnavailable(RuntimeError):
    pass

@dataclass(frozen=True)
class WindscribeStatus:
    connected: bool
    firewall: bool
    pending_test: bool
    interference: bool
    location: str = ""

    @property
    def ready(self):
        return self.connected and self.firewall and not self.pending_test and not self.interference

def package_for(arch):
    if arch not in WINDSCRIBE_ASSETS:
        raise BackendUnavailable("No pinned Windscribe CLI package for this architecture")
    name, digest = WINDSCRIBE_ASSETS[arch]
    return WINDSCRIBE_RELEASE + WINDSCRIBE_VERSION + "/" + name, digest

def parse_status(raw):
    """Parse official Windscribe CLI 2.x status without trusting exit code alone."""
    if not isinstance(raw, str) or len(raw) > 16000:
        raise BackendUnavailable("Invalid or oversized Windscribe status")
    lines = raw.splitlines()
    conn = next((line.split(":", 1)[1].strip() for line in lines
                 if line.lower().startswith("connect state:")), "")
    fw = next((line.split(":", 1)[1].strip() for line in lines
               if line.lower().startswith("firewall state:")), "")
    connection = conn.lstrip("* ").strip()
    location = connection.split(":", 1)[1].strip() if connection.startswith("Connected:") else ""
    return WindscribeStatus(
        connected=connection.startswith("Connected:"),
        firewall=fw in ("On", "Always On"),
        pending_test=conn.lstrip().startswith("*"),
        interference="network interference" in raw.lower(),
        location=location,
    )

def read_status():
    cli = shutil.which("windscribe-cli")
    if not cli:
        raise BackendUnavailable("Windscribe CLI not installed")
    try:
        result = subprocess.run([cli, "status"], capture_output=True, text=True, timeout=25)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BackendUnavailable("Windscribe helper not responding") from exc
    if result.returncode != 0:
        raise BackendUnavailable("Windscribe CLI status returned an error")
    return parse_status(result.stdout)

def require_isolated_controller():
    """No write operations on Debian host even if the CLI exists there."""
    if os.environ.get("KHAGENT_CONTAINER") != "1" or not (
        Path("/run/.containerenv").exists() or Path("/.containerenv").exists()
    ):
        raise BackendUnavailable("Windscribe integration is container-only")
    expected = os.environ.get("KHAGENT_HOST_NETNS", "")
    if not re.fullmatch(r"net:\[\d+\]", expected):
        raise BackendUnavailable("Missing host-network fingerprint")
    if os.readlink("/proc/self/ns/net") == expected:
        raise BackendUnavailable("Refusing to operate in host network namespace")

def require_ready_for_switch():
    require_isolated_controller()
    if not Path("/run/systemd/system").is_dir():
        raise BackendUnavailable("Windscribe requires its in-container systemd helper")
    status = read_status()
    if not status.ready:
        raise BackendUnavailable("Windscribe not fully connected or firewall/test pending")
    return status

def main(argv=None):
    p = argparse.ArgumentParser(description="Khroxy optional VPN backend metadata")
    p.add_argument("command", choices=("package", "status"))
    args = p.parse_args(argv)
    if args.command == "package":
        arch = ("amd64" if platform.machine() == "x86_64" else
                "arm64" if platform.machine() == "aarch64" else "unsupported")
        url, digest = package_for(arch)
        print("Windscribe " + WINDSCRIBE_VERSION + ":", url, "sha256=" + digest)
    elif args.command == "status":
        st = read_status()
        print("Windscribe connected:", st.connected)
        print("Firewall active:", st.firewall)
        print("Ready for independent tunnel validation:", st.ready)
        return 0 if st.ready else 1
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except BackendUnavailable as exc:
        print("Khroxy VPN backend unavailable:", exc, file=sys.stderr)
        sys.exit(1)
