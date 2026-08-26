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
    return Connection("localhost", 25565, "1.19.4", "TestBot", None, 762)


def _conn_1202():
    return Connection("localhost", 25565, "1.20.2", "TestBot", None, 764)


def _uncompressed_frame(body: bytes) -> bytes:
    return Connection._encode_varint(len(body)) + body


def _uncompressed_body(frame: bytes) -> bytes:
    length, offset = Connection._decode_varint_bytes(frame, 0)
    body = frame[offset:]
    assert len(body) == length
    return body


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


def test_1202_login_start_includes_offline_uuid():
    conn = _conn_1202()
    body = _uncompressed_body(conn._serialize_login_start("TestBot"))

    assert body[:9] == b"\x00\x07TestBot"
    assert len(body) == 25  # packet ID + string length/name + 16-byte UUID



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


def test_1202_login_runs_configuration_before_play():
    conn = _conn_1202()
    conn._start_func = lambda: None
    enc = Connection._encode_varint

    incoming = b"".join(
        (
            _uncompressed_frame(enc(0x02) + b"uuid+name"),
            _uncompressed_frame(enc(0x03) + b"12345678"),
            _uncompressed_frame(enc(0x04) + b"ping"),
            _uncompressed_frame(enc(0x02)),
        )
    )
    fake_socket = _FakeSocket(incoming)
    conn._socket = fake_socket

    conn._login()

    assert conn._connected is True
    sent = [_uncompressed_body(frame) for frame in fake_socket.sent]
    assert sent[0] == enc(0x03)  # Login Acknowledged
    assert sent[1].startswith(enc(0x00) + enc(5) + b"en_us")  # Client Information
    assert sent[2] == enc(0x03) + b"12345678"  # Keep Alive response
    assert sent[3] == enc(0x04) + b"ping"  # Pong
    assert sent[4] == enc(0x02)  # Finish Configuration acknowledgement



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


