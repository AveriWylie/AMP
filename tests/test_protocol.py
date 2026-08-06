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
from bot import Connection

"""
--------------------------------------------------------------------------------------------
Helpers
--------------------------------------------------------------------------------------------
"""
# A socket stand-in that serves a fixed byte buffer to recv() and swallows sends.
class _FakeSocket:
    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0

    def recv(self, n):
        chunk = self._data[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk

    def sendall(self, data):
        return None

    def close(self):
        return None


def _conn():
    return Connection("localhost", 25565, "1.19.4", "TestBot", None, 762)


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



# --------------------------------------------------------------------------------------------
# 3. Login state machine (Set Compression -> Login Success)
# --------------------------------------------------------------------------------------------
def test_login_flow_with_compression():
    conn = _conn()
    conn._start_func = lambda: None   # stub: don't spawn the listen thread in a unit test

    enc = Connection._encode_varint

    set_comp_body = enc(0x03) + enc(256)
    set_comp_frame = _uncompressed_frame(set_comp_body)

    login_body = enc(0x02) + b"uuid+name"
    payload = enc(0) + login_body
    login_frame = enc(len(payload)) + payload

    conn._socket = _FakeSocket(set_comp_frame + login_frame)
    conn._login()

    assert conn._connected is True
    assert conn._compression_threshold == 256



# --------------------------------------------------------------------------------------------
# 4. Login rejects online-mode and disconnect cleanly
# --------------------------------------------------------------------------------------------
def test_login_rejections():
    enc = Connection._encode_varint

    conn = _conn()
    conn._socket = _FakeSocket(_uncompressed_frame(enc(0x01) + b"server-id"))
    try:
        conn._login()
        assert False, "encryption request should raise"
    except ConnectionError as e:
        assert "offline" in str(e).lower() or "encryption" in str(e).lower()

    conn = _conn()
    conn._socket = _FakeSocket(_uncompressed_frame(enc(0x00) + b"reason"))
    try:
        conn._login()
        assert False, "disconnect should raise"
    except ConnectionError:
        pass


