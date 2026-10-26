#!/usr/bin/env python3
"""User-scoped, reversible native Khroxy install; does not touch system networking."""
import argparse
import hashlib
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent
TARGET = Path.home() / ".local" / "bin"
XRAY_VERSION = "v26.10.21"
XRAY_ASSETS = {
    "x86_64": ("Xray-linux-64.zip", "0eec3ed4163c273d8c8bf821f7d219e257921cf584aa3fb970715e458e631978"),
    "aarch64": ("Xray-linux-arm64-v8a.zip", "d1304946c486d98e6b886e9b931eaaaebb23835b30e217638037b7f2721d4e9c"),
}
class Refused(RuntimeError):
    pass

def check():
    if os.geteuid() == 0:
        raise Refused("Never run khroxy install with sudo")
    if platform.system() != "Linux":
        raise Refused("Native Khroxy requires Linux")
    if sys.version_info < (3, 10):
        raise Refused("Python 3.10 or newer required")
    if not (ROOT / "khroxy").is_file():
        raise Refused("khroxy script missing")
    if platform.machine() not in XRAY_ASSETS:
        raise Refused("Unsupported Xray architecture")

def approve(question):
    return input(question + " [y/N]: ").strip().lower() in ("y", "yes")

def install_file(data, destination):
    if destination.is_symlink():
        raise Refused("Refusing symbolic link at target: " + str(destination))
    if destination.exists():
        if not destination.is_file():
            raise Refused("Target exists but is not regular file")
        if destination.read_bytes() == data:
            return "already installed"
        if not approve("Existing " + destination.name + " differs. Back it up and replace?"):
            raise Refused("Replacement declined")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.parent.chmod(0o700)
    with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".khroxy-new-", delete=False) as tmp:
        candidate = Path(tmp.name)
        tmp.write(data)
    try:
        candidate.chmod(0o755)
        if destination.exists():
            # Never delete the previous working executable.
            backup = destination.with_name(destination.name + ".previous")
            if backup.exists():
                raise Refused("Previous backup already exists: " + str(backup))
            os.replace(destination, backup)
        os.replace(candidate, destination)
    finally:
        candidate.unlink(missing_ok=True)
    return "installed"

def download_xray():
    asset, digest = XRAY_ASSETS[platform.machine()]
    url = "https://github.com/XTLS/Xray-core/releases/download/" + XRAY_VERSION + "/" + asset
    with urllib.request.urlopen(url, timeout=30) as response:
        raw = response.read(40 * 1024 * 1024 + 1)
    if len(raw) > 40 * 1024 * 1024:
        raise Refused("Xray download larger than expected")
    if hashlib.sha256(raw).hexdigest() != digest:
        raise Refused("Xray SHA256 mismatch; nothing installed")
    import io
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        return archive.read("xray")

def main(argv=None):
    p = argparse.ArgumentParser(description="Native user-scoped Khroxy installer")
    p.add_argument("--check", action="store_true", help="read-only inspection")
    p.add_argument("--install-xray", action="store_true",
                   help="opt in to pinned, checksum-verified user Xray download")
    args = p.parse_args(argv)
    check()
    dest = TARGET / "khroxy"
    print("Source:", ROOT / "khroxy")
    print("Target:", dest)
    print("Rootless/user-only. Will not touch NetworkManager, firewall, SSH, Podman or host VPN.")
    if args.check:
        print("Read-only check passed. No changes.")
        return 0
    if not sys.stdin.isatty():
        raise Refused("Install requires a terminal and explicit confirmation")
    if not approve("Install standalone Khroxy into ~/.local/bin?"):
        print("Declined. No changes.")
        return 0
    print("Khroxy:", install_file((ROOT / "khroxy").read_bytes(), dest))
    xray = TARGET / "xray"
    if args.install_xray:
        if xray.exists():
            print("Xray exists; no update performed without separate decision.")
        elif approve("Download pinned Xray " + XRAY_VERSION + " (SHA256 checked)?"):
            print("Xray:", install_file(download_xray(), xray))
    elif not xray.exists():
        print("Xray not found in ~/.local/bin; use --install-xray or install manually.")
    print("Installed without altering existing profiles/services. Run: khroxy --help")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except (Refused, OSError, ValueError, zipfile.BadZipFile) as err:
        print("Khroxy setup stopped:", err, file=sys.stderr)
        sys.exit(1)
