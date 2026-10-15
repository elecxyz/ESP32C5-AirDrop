"""Bounded validation of the self-delimiting dvzip/ODC-cpio upload format.

Completion requires every declared file byte, the actual cpio trailer entry,
valid dvzip block checksums and only zero archive padding. A trailer-looking
string inside a photo is never treated as an end marker.
"""
# SPDX-License-Identifier: GPL-3.0-only
import stat
import struct
import zlib
from pathlib import PurePosixPath


class CpioEnd:
    def __init__(self):
        self.buffer = bytearray()
        self.remaining = 0
        self.name_size = None
        self.file_size = 0
        self.mode = 0
        self.done = False

    def feed(self, data):
        self.buffer.extend(data)
        while self.buffer:
            if self.done:
                if any(self.buffer):
                    raise ValueError("nonzero data after cpio trailer")
                self.buffer.clear()
                break
            if self.remaining:
                n = min(self.remaining, len(self.buffer))
                del self.buffer[:n]
                self.remaining -= n
                continue
            if self.name_size is None:
                if len(self.buffer) < 76:
                    break
                header = self.buffer[:76]
                if header[:6] != b"070707" or any(c not in b"01234567" for c in header[6:]):
                    raise ValueError("invalid ODC cpio header")
                self.mode = int(header[18:24], 8)
                self.name_size = int(header[59:65], 8)
                self.file_size = int(header[65:76], 8)
                if not 1 <= self.name_size <= 4096:
                    raise ValueError("invalid cpio name length")
                del self.buffer[:76]
            if len(self.buffer) < self.name_size:
                break
            name = bytes(self.buffer[:self.name_size])
            del self.buffer[:self.name_size]
            self.name_size = None
            if not name.endswith(b"\0") or b"\0" in name[:-1]:
                raise ValueError("invalid cpio filename")
            if name == b"TRAILER!!!\0":
                if self.file_size:
                    raise ValueError("cpio trailer has a body")
                self.done = True
                continue
            path = PurePosixPath(name[:-1].decode("utf-8", errors="surrogateescape"))
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("unsafe cpio path")
            if not (stat.S_ISREG(self.mode) or stat.S_ISDIR(self.mode)):
                raise ValueError("only regular files and directories are accepted")
            self.remaining = self.file_size


class DvzipEnd:
    MAX_BLOCK = 1024 * 1024

    def __init__(self):
        self.buffer = bytearray()
        self.cpio = CpioEnd()

    @property
    def complete(self):
        return self.cpio.done and not self.buffer

    def feed(self, data):
        self.buffer.extend(data)
        while len(self.buffer) >= 4:
            word, = struct.unpack_from(">I", self.buffer)
            size = word & 0x7fffffff
            if not 0 < size <= self.MAX_BLOCK:
                raise ValueError("invalid or unsupported dvzip block size")
            if len(self.buffer) < size + 4:
                break
            payload = bytes(self.buffer[4:size + 4])
            del self.buffer[:size + 4]
            if not word & 0x80000000:
                decoder = zlib.decompressobj()
                payload = decoder.decompress(payload, self.MAX_BLOCK + 1)
                if (len(payload) > self.MAX_BLOCK or not decoder.eof or
                        decoder.unused_data or decoder.unconsumed_tail):
                    raise ValueError("invalid or oversized compressed dvzip block")
            self.cpio.feed(payload)
