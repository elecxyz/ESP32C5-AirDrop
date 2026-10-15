"""Optional real host/PTY integration test, with no radio hardware access."""
import json
import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import threading
import unittest
from probe import ACK, HELLO, INFO, START, STOP, TX, Decoder, encode


@unittest.skipUnless(os.environ.get("CACHYDROP_C5_HOST_TEST") == "1",
                     "set CACHYDROP_C5_HOST_TEST=1 to test user/network namespaces")
class HostIntegration(unittest.TestCase):
    def test_native_bridge_with_emulated_radio(self):
        executable = Path(__file__).parent / "host/build/cachydrop-c5"
        self.assertTrue(executable.is_file())
        master, slave = pty.openpty()
        received, errors = [], []
        stop = threading.Event()

        def radio():
            decoder = Decoder()
            try:
                while not stop.is_set():
                    if not select.select([master], [], [], 0.1)[0]:
                        continue
                    for kind, seq, body in decoder.feed(os.read(master, 8192)):
                        received.append((kind, body))
                        if kind == HELLO:
                            info = json.dumps({"firmware": "cachydrop-c5-radio-test", "protocol": 1, "mac": "02:00:00:00:00:01"}, separators=(",", ":")).encode()
                            response = encode(INFO, seq, info)
                        else:
                            response = encode(ACK, seq, struct.pack("<i", 0))
                        # Exercise fragmented USB replies, not only full packets.
                        os.write(master, response[:9])
                        os.write(master, response[9:])
            except OSError as exc:
                if not stop.is_set():
                    errors.append(exc)

        worker = threading.Thread(target=radio)
        worker.start()
        try:
            result = subprocess.run(["unshare", "--user", "--map-root-user", "--net",
                                     str(executable), "-p", os.ttyname(slave), "-c", "6", "-r", "00", "-s", "2"],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn((START, b"\x06" + b"00\0"), received)
            self.assertTrue(any(kind == STOP for kind, _ in received))
            frames = [body for kind, body in received if kind == TX]
            self.assertGreater(len(frames), 10)
            for frame in frames:
                self.assertLessEqual(len(frame), 1500)
                self.assertEqual(frame[10:16], bytes.fromhex("020000000001"))
                self.assertEqual(frame[16:22], bytes.fromhex("002500ff9473"))
            self.assertFalse(errors)
        finally:
            stop.set()
            worker.join(timeout=1)
            os.close(master)
            os.close(slave)


if __name__ == "__main__":
    unittest.main()
