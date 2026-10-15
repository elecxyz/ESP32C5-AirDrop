#!/usr/bin/env python3
"""Run the installed patched OpenDrop with a separate C5 receiver identity."""
# SPDX-License-Identifier: GPL-3.0-only
import logging
import os
import signal
import socket
import time
from pathlib import Path
from opendrop.config import AirDropConfig
from opendrop.server import AirDropServer, AirDropServerHandler
from archive_end import DvzipEnd


class UploadTrace:
    """Log chunk boundaries, never file contents, for final-handshake diagnosis."""
    def __init__(self, stream, archive_end=None):
        self.stream = stream
        self.started = time.monotonic()
        self.total = 0
        self.archive_end = archive_end
        self.chunk_remaining = 0
        self.synthetic_end = False

    def read(self, size=-1):
        data = self.stream.read(size)
        self.total += len(data)
        if self.archive_end and self.chunk_remaining:
            n = min(len(data), self.chunk_remaining)
            self.archive_end.feed(data[:n])
            self.chunk_remaining -= n
        return data

    def readline(self, *args):
        if self.synthetic_end:
            return b"\r\n"
        if self.archive_end and self.archive_end.complete and not self.chunk_remaining:
            # The observed iPhone stops at a complete archive without sending
            # the final HTTP chunk until it gets a response. Give OpenDrop an
            # EOF only after structural validation, then close the connection;
            # never reuse a connection with an unread request tail. This is an
            # early final response, not a claim that HTTP EOF was observed.
            logging.info("Validated dvzip/cpio end after %.3f s; completing upload",
                         time.monotonic() - self.started)
            self.synthetic_end = True
            return b"0\r\n"
        line = self.stream.readline(*args)
        # Only sanitized numeric chunk sizes are logged; no arbitrary peer text.
        try:
            length = int(line.strip().split(b";", 1)[0], 16)
        except ValueError:
            length = "EOF" if not line else "trailer/invalid"
        if isinstance(length, int):
            if self.archive_end and length == 0 and not self.archive_end.complete:
                raise EOFError("HTTP ended before a complete dvzip/cpio archive")
            self.chunk_remaining = length
        logging.info("Upload chunk=%s, body-read=%d B, elapsed=%.3f s",
                     length, self.total, time.monotonic() - self.started)
        return line


class C5Handler(AirDropServerHandler):
    def handle_upload(self):
        stream = self.rfile
        dvzip = self.headers.get("content-type", "").lower() == "application/x-dvzip"
        self.rfile = UploadTrace(stream, DvzipEnd() if dvzip else None)
        self.close_connection = True
        try:
            super().handle_upload()
        except Exception:
            logging.exception("C5 upload did not complete")
            raise
        finally:
            self.rfile = stream


class C5Server(AirDropServer):
    def _init_server(self):
        self.Handler = C5Handler
        self.Handler.config = self.config
        httpd = super()._init_server()
        # The normal USB Wi-Fi receiver has a much larger receive path. Here
        # cap bytes in flight to fit the C5's 64 KiB ring plus USB buffering.
        # Keeping a larger kernel buffer allows out-of-order recovery without
        # inviting a multi-megabyte opening burst into the microcontroller.
        httpd.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 64 * 1024)
        httpd.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_WINDOW_CLAMP, 32 * 1024)
        logging.info("C5 TCP receive-window clamp: 32768 B")
        return httpd


def interrupted(signum, frame):
    raise KeyboardInterrupt


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    signal.signal(signal.SIGTERM, interrupted)
    state = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    config = AirDropConfig(host_name="esp32c5-airdrop", computer_name="ESP32C5 AirDrop",
                           interface="awdlc5", airdrop_dir=str(state / "esp32c5-airdrop/c5/identity"))
    server = C5Server(config)
    try:
        server.start_service()
        server.start_server()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()


if __name__ == "__main__":
    main()
