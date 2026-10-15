"""C5-only receiver tuning and diagnostics; run with the OpenDrop environment."""
# SPDX-License-Identifier: GPL-3.0-only
import io
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from test_archive_end import entry, block

try:
    from receiver import C5Server, C5Handler, UploadTrace
except ImportError:
    C5Server = None


@unittest.skipUnless(C5Server, "requires the patched OpenDrop environment")
class ReceiverTests(unittest.TestCase):
    def test_trace_preserves_chunk_stream(self):
        stream = UploadTrace(io.BytesIO(b"3\r\nabc\r\n0\r\n\r\n"))
        with self.assertLogs(level="INFO") as logs:
            self.assertEqual(stream.readline(), b"3\r\n")
            self.assertEqual(stream.read(3), b"abc")
            self.assertEqual(stream.read(2), b"\r\n")
            self.assertEqual(stream.readline(), b"0\r\n")
            self.assertEqual(stream.readline(), b"\r\n")
            self.assertEqual(stream.readline(), b"")
        self.assertTrue(any("chunk=0" in line for line in logs.output))
        self.assertNotIn("abc", str(logs.output))

    def test_bad_chunk_text_is_not_logged(self):
        stream = UploadTrace(io.BytesIO(b"private-text\r\n"))
        with self.assertLogs(level="INFO") as logs:
            self.assertEqual(stream.readline(), b"private-text\r\n")
        self.assertNotIn("private-text", str(logs.output))

    def test_c5_window_does_not_modify_normal_receiver(self):
        server = object.__new__(C5Server)
        server.config = Mock()
        httpd = Mock()
        with patch("receiver.AirDropServer._init_server", return_value=httpd):
            self.assertIs(server._init_server(), httpd)
        httpd.socket.setsockopt.assert_any_call(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
        httpd.socket.setsockopt.assert_any_call(socket.IPPROTO_TCP, socket.TCP_WINDOW_CLAMP, 32768)
        self.assertIs(server.Handler, C5Handler)

    def run_upload(self, payload, suffix=b"", expect_error=False):
        handler = object.__new__(C5Handler)
        handler.headers = {"content-type": "application/x-dvzip", "transfer-encoding": "chunked"}
        handler.rfile = io.BytesIO(f"{len(payload):x}\r\n".encode() + payload + b"\r\n" + suffix)
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        before = Path.cwd()
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"AIRDROP_RECEIVED": ""}):
            try:
                os.chdir(directory)
                if expect_error:
                    with self.assertLogs(level="ERROR"), self.assertRaises(EOFError):
                        handler.handle_upload()
                    handler.send_response.assert_not_called()
                    self.assertFalse(Path("photo.jpg").exists())
                else:
                    handler.handle_upload()
                    handler.send_response.assert_called_with(200)
                    self.assertTrue(handler.close_connection)
                    self.assertEqual(Path("photo.jpg").read_bytes(), b"example photo bytes")
                    self.assertFalse(list(Path().glob("airdrop-received-*")))
            finally:
                os.chdir(before)

    def test_complete_archive_without_http_terminator(self):
        self.run_upload(block(entry("photo.jpg", b"example photo bytes") + entry("TRAILER!!!", mode=0)))

    def test_complete_archive_with_http_terminator(self):
        self.run_upload(block(entry("photo.jpg", b"example photo bytes") + entry("TRAILER!!!", mode=0)), b"0\r\n\r\n")

    def test_incomplete_archive_is_not_reported_as_success(self):
        self.run_upload(block(entry("photo.jpg", b"example photo bytes")[:-3]), b"0\r\n\r\n", expect_error=True)


if __name__ == "__main__":
    unittest.main()
