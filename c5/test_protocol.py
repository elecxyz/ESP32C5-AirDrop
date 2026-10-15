"""Transport tests: corruption, fragmentation, noise and bounded packet sizes."""
import struct
import unittest
from probe import ACK, RX, Decoder, encode


class ProtocolTests(unittest.TestCase):
    def test_every_split(self):
        packet = encode(RX, 17, bytes(range(256)))
        for offset in range(len(packet) + 1):
            decoder = Decoder()
            result = decoder.feed(packet[:offset]) + decoder.feed(packet[offset:])
            self.assertEqual(result, [(RX, 17, bytes(range(256)))])

    def test_byte_at_a_time_after_boot_noise(self):
        decoder = Decoder()
        result = []
        for byte in b"ESP-ROM:boot message\r\n" + encode(ACK, 2, b"\0" * 4):
            result.extend(decoder.feed(bytes([byte])))
        self.assertEqual(result, [(ACK, 2, b"\0" * 4)])

    def test_corruption_recovers(self):
        bad = bytearray(encode(RX, 1, b"damaged"))
        bad[13] ^= 1
        good = encode(ACK, 3, struct.pack("<i", 0))
        decoder = Decoder()
        self.assertEqual(decoder.feed(bad + good), [(ACK, 3, b"\0" * 4)])
        self.assertEqual(decoder.bad_crc, 1)

    def test_impossible_length_recovers(self):
        bad = b"C5AW" + bytes([RX, 1]) + struct.pack("<HI", 65535, 1)
        decoder = Decoder()
        self.assertEqual(decoder.feed(bad + encode(ACK, 2)), [(ACK, 2, b"")])

    def test_multiple_messages_and_truncated_tail(self):
        decoder = Decoder()
        packet = encode(RX, 3, b"tail")
        self.assertEqual(decoder.feed(encode(ACK, 1) + encode(ACK, 2) + packet[:14]),
                         [(ACK, 1, b""), (ACK, 2, b"")])
        self.assertEqual(decoder.feed(packet[14:]), [(RX, 3, b"tail")])

    def test_size_limit(self):
        with self.assertRaises(ValueError):
            encode(RX, 1, b"x" * 4201)


if __name__ == "__main__":
    unittest.main()
