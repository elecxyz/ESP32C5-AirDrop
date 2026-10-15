#!/usr/bin/env python3
"""Relay a completed-transfer notification to the normal desktop namespace."""
# SPDX-License-Identifier: GPL-3.0-only
import json
import os
import socket
import subprocess
import sys
from pathlib import Path


def main():
    address = os.environ["CACHYDROP_C5_NOTIFY_SOCKET"]
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    if len(sys.argv) == 2 and sys.argv[1] == "--listen":
        sock.bind(address)
        os.chmod(address, 0o600)
        notify = Path(__file__).resolve().parents[1] / "desktop/cachydrop-notify"
        children = []
        while True:
            message = sock.recv(65536)
            try:
                event = json.loads(message)
                directory = Path(event["directory"]).resolve(strict=True)
                paths = [Path(p).resolve(strict=True) for p in event["files"]]
                if not directory.is_dir() or not paths or any(not p.is_relative_to(directory) for p in paths):
                    continue
                proc = subprocess.Popen([str(notify), str(directory)], stdin=subprocess.PIPE)
                proc.stdin.write(b"\0".join(os.fsencode(p) for p in paths) + b"\0")
                proc.stdin.close()
                children = [p for p in children if p.poll() is None] + [proc]
            except (ValueError, KeyError, TypeError, OSError):
                continue
    elif len(sys.argv) == 2:
        paths = [os.fsdecode(p) for p in sys.stdin.buffer.read(60000).split(b"\0") if p]
        data = json.dumps({"directory": sys.argv[1], "files": paths}).encode()
        if len(data) > 65536:
            raise ValueError("notification too large")
        sock.sendto(data, address)
    else:
        raise SystemExit("usage: notify_relay.py --listen | RECEIVED_DIRECTORY")


if __name__ == "__main__":
    main()
