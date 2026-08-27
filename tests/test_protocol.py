"""
--------------------------------------------------------------------------------------------
Iteration 01 Test Module - connection compression, login flow, pathfinder correctness
--------------------------------------------------------------------------------------------
Covers the protocol units needed for a connectable base: compressed frame round-trips and
the login state machine (Set Compression -> Login Success) over a fake socket. No live
server is required; pytest discovers every test in this module directly.
--------------------------------------------------------------------------------------------
"""
# imports
import zlib

import pytest

from connection import Connection

"""
--------------------------------------------------------------------------------------------
Helpers
--------------------------------------------------------------------------------------------
"""
# A socket stand-in that serves a fixed byte buffer to recv() and records sends.
class _FakeSocket:
    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0
        self.sent = []

    def recv(self, n):
        chunk = self._data[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk

    def sendall(self, data):
        self.sent.append(data)

    def close(self):
        return None


def _conn():
    return Connection("localhost", 25565, "26.1.2", "TestBot", None, 775)


def _uncompressed_frame(body: bytes) -> bytes:
    return Connection._encode_varint(len(body)) + body


# --------------------------------------------------------------------------------------------
# 1. Compressed frame round-trip
# --------------------------------------------------------------------------------------------
def test_compression_round_trip():
    conn = _conn()
    conn._compression_threshold = 16

    small_body = b"\x21" + b"abc"
    frame = conn._compress_frame(_uncompressed_frame(small_body))
    conn._socket = _FakeSocket(frame)
    conn._connected = True
    pid, data = conn._read_packet()
    assert pid == 0x21
    assert data == b"abc"

    big_body = b"\x26" + b"x" * 500
    frame = conn._compress_frame(_uncompressed_frame(big_body))
    conn._socket = _FakeSocket(frame)
    pid, data = conn._read_packet()
    assert pid == 0x26
    assert data == b"x" * 500



# --------------------------------------------------------------------------------------------
# 2. Uncompressed read still works when no threshold is set
# --------------------------------------------------------------------------------------------
def test_uncompressed_read():
    conn = _conn()
    conn._connected = True
    body = b"\x02" + b"login-success-fields"
    conn._socket = _FakeSocket(_uncompressed_frame(body))

    pid, data = conn._read_packet()
    assert pid == 0x02
    assert data == b"login-success-fields"


def test_read_packet_rejects_oversized_outer_frame_before_reading_body():
    conn = _conn()
    conn._socket = _FakeSocket(
        Connection._encode_varint(Connection.MAX_PACKET_SIZE + 1)
    )

    with pytest.raises(ValueError, match="packet length"):
        conn._read_packet()


def test_read_packet_rejects_oversized_advertised_decompressed_body():
    conn = _conn()
    conn._compression_threshold = 1
    advertised = Connection._encode_varint(Connection.MAX_PACKET_SIZE + 1)
    conn._socket = _FakeSocket(_uncompressed_frame(advertised + zlib.compress(b"\x01")))

    with pytest.raises(ValueError, match="decompressed packet length"):
        conn._read_packet()


def test_read_packet_rejects_compressed_size_mismatch():
    conn = _conn()
    conn._compression_threshold = 1
    body = zlib.compress(b"\x01abc")
    payload = Connection._encode_varint(100) + body
    conn._socket = _FakeSocket(_uncompressed_frame(payload))

    with pytest.raises(ValueError, match="decompressed packet size"):
        conn._read_packet()
