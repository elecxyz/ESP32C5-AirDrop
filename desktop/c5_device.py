#!/usr/bin/env python3
"""Registered C5 discovery and an install-time, HELLO-only firmware verifier.

USB 303a:1001 is shared by several Espressif chips. It is never sufficient on
its own to opt a device into automatic receiving. Registration additionally
requires this project's firmware response and an exact USB serial allowlist.
"""
# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

import argparse
import dataclasses
import fcntl
import json
import os
from pathlib import Path
import re
import select
import stat
import struct
import sys
import termios
import time
import zlib

SYS_TTY = Path("/sys/class/tty")
REGISTRY = Path("/etc/esp32c5-airdrop/devices.json")
INTERFACE_RE = re.compile(r"ttyACM[0-9]+\Z", re.ASCII)
SERIAL_RE = re.compile(r"[A-Za-z0-9:_-]{1,128}\Z", re.ASCII)
HEADER = struct.Struct("<4sBBHI")
MAX_BODY = 4200
FIRMWARE_PREFIX = "cachydrop-c5-radio-"
DEVICE_LABEL = "CachyDrop-C5-Reciever"


@dataclasses.dataclass(frozen=True)
class Device:
    interface: str
    serial: str

    @property
    def port(self) -> str:
        return f"/dev/{self.interface}"


def valid_interface(value: str) -> bool:
    return isinstance(value, str) and INTERFACE_RE.fullmatch(value) is not None


def discover(sys_tty: Path = SYS_TTY) -> list[Device]:
    """Read sysfs only; do not open or reset connected serial devices."""
    devices = []
    try:
        entries = sorted(sys_tty.iterdir())
    except OSError:
        return devices
    for entry in entries:
        if not valid_interface(entry.name):
            continue
        try:
            node = entry.joinpath("device").resolve(strict=True)
            for parent in (node, *node.parents):
                if not (parent / "idVendor").is_file():
                    continue
                vendor = (parent / "idVendor").read_text().strip().lower()
                product = (parent / "idProduct").read_text().strip().lower()
                serial = (parent / "serial").read_text().strip()
                if (vendor, product) == ("303a", "1001") and SERIAL_RE.fullmatch(serial):
                    devices.append(Device(entry.name, serial))
                break
        except (OSError, UnicodeError):
            continue
    return devices


def registered_serials(path: Path = REGISTRY) -> set[str]:
    try:
        payload = json.loads(path.read_text())
        serials = payload.get("serials") if isinstance(payload, dict) else None
        if not isinstance(serials, list) or not all(
            isinstance(value, str) and SERIAL_RE.fullmatch(value) for value in serials
        ):
            return set()
        return set(serials)
    except (OSError, UnicodeError, ValueError):
        return set()


def registered_devices(sys_tty: Path = SYS_TTY, registry: Path = REGISTRY) -> list[Device]:
    allowed = registered_serials(registry)
    return [device for device in discover(sys_tty) if device.serial in allowed]


def is_registered(interface: str, sys_tty: Path = SYS_TTY, registry: Path = REGISTRY) -> bool:
    return valid_interface(interface) and any(
        device.interface == interface for device in registered_devices(sys_tty, registry)
    )


def packet(kind: int, sequence: int, body: bytes = b"") -> bytes:
    frame = HEADER.pack(b"C5AW", kind, 1, len(body), sequence) + body
    return frame + struct.pack("<I", zlib.crc32(frame))


def decode(buffer: bytearray):
    """Yield CRC-checked frames, tolerating boot text and fragmented reads."""
    while len(buffer) >= HEADER.size:
        magic, kind, version, size, sequence = HEADER.unpack_from(buffer)
        if magic != b"C5AW" or version != 1 or size > MAX_BODY:
            del buffer[0]
            continue
        length = HEADER.size + size + 4
        if len(buffer) < length:
            return
        checksum, = struct.unpack_from("<I", buffer, length - 4)
        if zlib.crc32(buffer[:length - 4]) != checksum:
            del buffer[0]
            continue
        body = bytes(buffer[HEADER.size:length - 4])
        del buffer[:length]
        yield kind, sequence, body


def hello(port: str, timeout: float = 3.0) -> str:
    """Verify firmware with HELLO only. Never send START or toggle DTR/RTS.

    O_NONBLOCK avoids waiting for modem lines. Removing HUPCL keeps closing the
    port from deliberately dropping those lines. TIOCEXCL refuses competing
    opens while the verifier owns the port; an already busy receiver must be
    stopped before the installer runs verification.
    """
    fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK | os.O_CLOEXEC)
    exclusive = False
    try:
        fcntl.ioctl(fd, termios.TIOCEXCL)
        exclusive = True
        settings = termios.tcgetattr(fd)
        settings[0] = 0
        settings[1] = 0
        settings[2] = (settings[2] & ~(termios.CSIZE | termios.PARENB | termios.CSTOPB |
                                      termios.HUPCL | getattr(termios, "CRTSCTS", 0)))
        settings[2] |= termios.CS8 | termios.CREAD | termios.CLOCAL
        settings[3] = 0
        settings[4] = settings[5] = termios.B921600
        settings[6][termios.VMIN] = 0
        settings[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, settings)
        sequence = int.from_bytes(os.urandom(4), "little") or 1
        pending = memoryview(packet(1, sequence))
        deadline = time.monotonic() + timeout
        buffer = bytearray()
        while time.monotonic() < deadline:
            remaining = max(0, deadline - time.monotonic())
            readable, writable, _ = select.select([fd], [fd] if pending else [], [], remaining)
            if writable:
                try:
                    pending = pending[os.write(fd, pending):]
                except BlockingIOError:
                    pass
            if readable:
                try:
                    data = os.read(fd, 8192)
                except BlockingIOError:
                    continue
                if not data:
                    raise OSError("C5 disconnected during verification")
                buffer.extend(data)
                for kind, reply_sequence, body in decode(buffer):
                    if kind != 129 or reply_sequence != sequence:
                        continue
                    info = json.loads(body)
                    firmware = info.get("firmware") if isinstance(info, dict) else None
                    if (not isinstance(info, dict) or type(info.get("protocol")) is not int or
                            info.get("protocol") != 1 or
                            not isinstance(firmware, str) or not firmware.startswith(FIRMWARE_PREFIX)):
                        raise ValueError("device does not have compatible C5 AirDrop firmware")
                    return firmware
        raise TimeoutError("no compatible C5 firmware response")
    finally:
        if exclusive:
            fcntl.ioctl(fd, termios.TIOCNXCL)
        os.close(fd)


def private_output(path: Path, payload: dict) -> None:
    """Write installer-only identities without following symlinks or links."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        metadata = os.fstat(fd)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid() or
                metadata.st_mode & 0o077 or metadata.st_nlink != 1):
            raise ValueError("verification output must be an owned private regular file")
        data = (json.dumps(payload, separators=(",", ":")) + "\n").encode()
        os.ftruncate(fd, 0)
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(data)
    finally:
        os.close(fd)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    find = subparsers.add_parser("discover")
    find.add_argument("--verify", action="store_true", help="send HELLO only to one selected device")
    find.add_argument("--interface", help="verify only this ttyACM interface when several boards are connected")
    find.add_argument("--output", type=Path, help="private JSON output for installer registration")
    check = subparsers.add_parser("check")
    check.add_argument("interface")
    args = parser.parse_args(argv)
    if args.command == "check":
        return 0 if is_registered(args.interface) else 3
    if args.output and not args.verify:
        parser.error("--output requires --verify")
    if args.interface is not None:
        if not args.verify:
            parser.error("--interface requires --verify")
        if not valid_interface(args.interface):
            parser.error("--interface must be a ttyACM interface")
    candidates = discover()
    if not args.verify:
        print(json.dumps({"candidates": len(candidates), "registered": len(registered_devices())}))
        return 0
    if args.interface is not None:
        candidates = [device for device in candidates if device.interface == args.interface]
    if len(candidates) != 1:
        # Never probe every ESP32 to guess which physical board the user meant.
        print(json.dumps({"verified": 0, "candidates": len(candidates),
                          "error": "Verification requires exactly one selected device"}))
        return 3
    verified = []
    failures = 0
    for device in candidates:
        try:
            firmware = hello(device.port)
            # Check the same physical device is still present after the probe.
            if device not in discover():
                raise OSError("device changed during verification")
            verified.append({**dataclasses.asdict(device), "firmware": firmware})
        except (OSError, ValueError, TimeoutError, termios.error):
            failures += 1
    if args.output:
        private_output(args.output, {"devices": verified})
    print(json.dumps({"verified": len(verified), "unverified": failures}))
    return 0 if verified else 3


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as error:
        # Do not put device serials or a private output path into normal logs.
        print(f"C5 device verification failed ({type(error).__name__})", file=sys.stderr)
        raise SystemExit(1)
