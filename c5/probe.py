#!/usr/bin/env python3
"""CachyDrop C5 radio diagnostics. Does not change any Linux Wi-Fi interface."""
# SPDX-License-Identifier: GPL-3.0-only
import argparse
import collections
import json
import struct
import time
import zlib

MAGIC = b"C5AW"
HEADER = struct.Struct("<4sBBHI")
HELLO, START, STOP, STATS, TX = range(1, 6)
ACK, INFO, COUNTERS, RX = range(128, 132)
MAX_BODY = 4200


def encode(kind, seq, body=b""):
    if len(body) > MAX_BODY:
        raise ValueError("message too large")
    packet = HEADER.pack(MAGIC, kind, 1, len(body), seq) + body
    return packet + struct.pack("<I", zlib.crc32(packet))


class Decoder:
    def __init__(self):
        self.buffer = bytearray()
        self.bad_crc = 0

    def feed(self, data):
        self.buffer.extend(data)
        result = []
        while len(self.buffer) >= HEADER.size:
            magic, kind, version, size, seq = HEADER.unpack_from(self.buffer)
            if magic != MAGIC or version != 1 or size > MAX_BODY:
                del self.buffer[0]
                continue
            total = HEADER.size + size + 4
            if len(self.buffer) < total:
                break
            wanted, = struct.unpack_from("<I", self.buffer, total - 4)
            if zlib.crc32(self.buffer[:total - 4]) != wanted:
                self.bad_crc += 1
                del self.buffer[0]
                continue
            result.append((kind, seq, bytes(self.buffer[HEADER.size:total - 4])))
            del self.buffer[:total]
        return result


class Radio:
    def __init__(self, port):
        import serial
        self.serial = serial.Serial(port=None, baudrate=921600, timeout=0.1,
                                    write_timeout=1, exclusive=True)
        self.serial.dtr = False
        self.serial.rts = False
        self.serial.port = port
        self.serial.open()
        self.decoder = Decoder()
        self.seq = 0
        self.frames = collections.Counter()
        self.last_frames = {}

    def receive(self):
        messages = self.decoder.feed(self.serial.read(max(1, min(self.serial.in_waiting, 65536))))
        for kind, seq, body in messages:
            if kind == RX and len(body) >= 32:
                channel, rssi, _, timestamp = struct.unpack_from("<BbHI", body)
                frame = body[8:]
                label = "action" if frame[0] == 0xd0 else "data"
                self.frames[(channel, label)] += 1
                # Deliberately report no peer addresses, names, or packet payloads.
                self.last_frames[channel] = {"rssi": rssi, "length": len(frame)}
        return messages

    def command(self, kind, body=b"", timeout=4):
        self.seq += 1
        self.serial.write(encode(kind, self.seq, body))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for reply_kind, seq, payload in self.receive():
                if seq != self.seq or reply_kind == RX:
                    continue
                if reply_kind == ACK:
                    code, = struct.unpack("<i", payload)
                    if code:
                        raise RuntimeError(f"command {kind}: ESP error 0x{code:x}")
                    return {"ok": True}
                return json.loads(payload)
        raise TimeoutError(f"No response to command {kind} on {self.serial.port}")

    def close(self):
        self.serial.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port", default="/dev/ttyACM0")
    p.add_argument("--channels", default="6")
    p.add_argument("--country", default="00", help="ISO country code, applied only to C5")
    p.add_argument("--seconds", type=float, default=15)
    p.add_argument("--tx-test", action="store_true", help="send one AWDL action and one multicast data frame")
    p.add_argument("--info", action="store_true")
    args = p.parse_args()
    country = args.country.upper()
    if len(country) != 2 or not (country == "00" or country.isascii() and country.isalpha()):
        p.error("country must be 00 or two ASCII letters")
    radio = Radio(args.port)
    started = False
    try:
        info = radio.command(HELLO)
        mac = bytes.fromhex(info.pop("mac").replace(":", ""))
        print(json.dumps(info), flush=True)
        if args.info:
            return
        for channel in map(int, args.channels.split(",")):
            if channel not in (6, 36, 44, 149):
                p.error("supported channels: 6,36,44,149")
            try:
                radio.command(START, bytes([channel]) + country.encode("ascii") + b"\0")
            except RuntimeError:
                detail = radio.command(HELLO)
                detail.pop("mac", None)
                print(json.dumps(detail), flush=True)
                raise
            started = True
            print(f"Listening for AWDL on channel {channel} for {args.seconds:g}s", flush=True)
            if args.tx_test:
                bssid = bytes.fromhex("002500ff9473")
                header = b"\xd0\0\0\0" + b"\xff" * 6 + mac + bssid + b"\0\0"
                action = bytes.fromhex("7f0017f208100300") + b"\0" * 8 + bytes.fromhex("1502001001")
                print("Action TX:", radio.command(TX, header + action), flush=True)
                # IPv6 multicast with an empty no-next-header payload.
                ipv6 = b"\x60\0\0\0\0\0\x3b\x01" + b"\0" * 16 + bytes.fromhex("ff020000000000000000000000000001")
                data = b"\x08\0\0\0" + bytes.fromhex("333300000001") + mac + bssid + b"\x10\0"
                data += bytes.fromhex("aaaa030017f2080003040000000086dd") + ipv6
                print("Data TX:", radio.command(TX, data), flush=True)
            end = time.monotonic() + args.seconds
            while time.monotonic() < end:
                radio.receive()
            print(json.dumps(radio.command(STATS)), flush=True)
            print(json.dumps({"received": {f"ch{ch}_{kind}": n for (ch, kind), n in radio.frames.items()},
                              "last_frames": radio.last_frames, "usb_crc_errors": radio.decoder.bad_crc}), flush=True)
    finally:
        if started:
            try:
                radio.command(STOP)
            except (OSError, RuntimeError, TimeoutError):
                pass
        radio.close()


if __name__ == "__main__":
    main()
