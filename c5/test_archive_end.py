"""Regression tests for early final-response validation, using synthetic data."""
# SPDX-License-Identifier: GPL-3.0-only
import struct
import unittest
import zlib
from archive_end import DvzipEnd


def entry(name, data=b"", mode=0o100644):
    name = name.encode() + b"\0"
    fields = (0, 1, mode, 0, 0, 1, 0)
    header = b"070707" + b"".join(f"{v:06o}".encode() for v in fields)
    header += f"{0:011o}{len(name):06o}{len(data):011o}".encode()
    return header + name + data


def block(data, stored=False):
    payload = data if stored else zlib.compress(data)
    return struct.pack(">I", len(payload) | (0x80000000 if stored else 0)) + payload


class ArchiveEndTests(unittest.TestCase):
    def setUp(self):
        self.archive = entry("photo.jpg", b"test bytes TRAILER!!!") + entry("TRAILER!!!", mode=0) + b"\0" * 10

    def test_all_packet_splits(self):
        data = block(self.archive)
        for split in range(len(data) + 1):
            decoder = DvzipEnd()
            decoder.feed(data[:split])
            decoder.feed(data[split:])
            self.assertTrue(decoder.complete)

    def test_stored_and_compressed_blocks_byte_by_byte(self):
        data = block(self.archive[:90], True) + block(self.archive[90:])
        decoder = DvzipEnd()
        for b in data:
            decoder.feed(bytes([b]))
        self.assertTrue(decoder.complete)

    def test_partial_payload_is_never_complete(self):
        data = block(self.archive)
        for end in range(len(data)):
            decoder = DvzipEnd()
            decoder.feed(data[:end])
            self.assertFalse(decoder.complete)

    def test_marker_inside_file_is_not_completion(self):
        decoder = DvzipEnd()
        decoder.feed(block(entry("photo.jpg", b"TRAILER!!!")))
        self.assertFalse(decoder.complete)

    def test_missing_file_bytes(self):
        decoder = DvzipEnd()
        decoder.feed(block(entry("photo.jpg", b"data")[:-1]))
        self.assertFalse(decoder.complete)

    def test_bad_checksum(self):
        data = bytearray(block(self.archive))
        data[-1] ^= 1
        with self.assertRaises(zlib.error):
            DvzipEnd().feed(data)

    def test_rejects_unsafe_paths_and_symlinks(self):
        for name, mode in (("../escape", 0o100644), ("/escape", 0o100644), ("link", 0o120777)):
            with self.subTest(name=name), self.assertRaises(ValueError):
                DvzipEnd().feed(block(entry(name, mode=mode)))

    def test_trailing_nonzero_data_is_invalid(self):
        with self.assertRaises(ValueError):
            DvzipEnd().feed(block(self.archive + b"x"))

    def test_excessive_decompression_is_bounded(self):
        with self.assertRaises(ValueError):
            DvzipEnd().feed(block(b"\0" * (DvzipEnd.MAX_BLOCK + 1)))

    def test_incomplete_next_block_prevents_completion(self):
        decoder = DvzipEnd()
        decoder.feed(block(self.archive) + b"\0\0")
        self.assertFalse(decoder.complete)
