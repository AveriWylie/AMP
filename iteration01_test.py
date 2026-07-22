"""
--------------------------------------------------------------------------------------------
Iteration 01 Test Module - connection compression, login flow, pathfinder correctness
--------------------------------------------------------------------------------------------
Covers the units changed for a connectable, command-executing, pathfinding base: the
compressed frame round-trip, the login state machine (Set Compression -> Login Success)
over a fake socket, the fixed pathfinder walkability rule, and the measurable weighted-A*
mode difference. No live server, everything is driven off in-memory buffers and hand-built
chunks. Same convention as connection_test: assert, print [PASS], run() drives them.
--------------------------------------------------------------------------------------------
"""
# imports
from bot import Connection
from pathfinder import Pathfinder

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


# A Chunk stand-in. get_block returns a name from a dict keyed by absolute (x, y, z), defaulting
# to "stone" below build height and "air" at/above it so a flat solid floor exists by default.
class _FakeChunk:
    def __init__(self, overrides=None):
        self._overrides = overrides or {}

    def get_block(self, x, y, z):
        if (x, y, z) in self._overrides:
            return self._overrides[(x, y, z)]
        return "stone" if y <= 63 else "air"


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

    print("[PASS] test_compression_round_trip")


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

    print("[PASS] test_uncompressed_read")


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

    print("[PASS] test_login_flow_with_compression")


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

    print("[PASS] test_login_rejections")


# --------------------------------------------------------------------------------------------
# 5. Pathfinder walkability rule
# --------------------------------------------------------------------------------------------
def test_walkable_rule():
    world = {"map": {(0, 0): _FakeChunk()}}
    pf = Pathfinder(world)

    assert pf._is_walkable(0, 64, 0) is True, "solid floor with clear space is walkable"
    assert pf._is_walkable(0, 65, 0) is False, "no solid floor is not walkable"

    world_head = {"map": {(0, 0): _FakeChunk({(0, 65, 0): "stone"})}}
    pf2 = Pathfinder(world_head)
    assert pf2._is_walkable(0, 64, 0) is False, "blocked head is not walkable"

    world_feet = {"map": {(0, 0): _FakeChunk({(0, 64, 0): "stone"})}}
    pf3 = Pathfinder(world_feet)
    assert pf3._is_walkable(0, 64, 0) is False, "solid feet cell is not walkable"

    print("[PASS] test_walkable_rule")


# --------------------------------------------------------------------------------------------
# 6. find_path basic correctness
# --------------------------------------------------------------------------------------------
def test_find_path_basic():
    world = {"map": {(0, 0): _FakeChunk()}}
    pf = Pathfinder(world)

    path = pf.find_path((0, 64, 0), (5, 64, 0))
    assert path, "a straight open path must exist"
    assert path[0] == (0, 64, 0)
    assert path[-1] == (5, 64, 0)

    for (ax, ay, az), (bx, by, bz) in zip(path, path[1:]):
        step = abs(ax - bx) + abs(ay - by) + abs(az - bz)
        assert step == 1, f"each step is one block, got {step}"

    print("[PASS] test_find_path_basic")


# --------------------------------------------------------------------------------------------
# 7. Measurable mode difference (weighted A*)
# --------------------------------------------------------------------------------------------
def test_mode_expansion_difference():
    wall = {}
    for z in range(0, 8):
        wall[(5, 64, z)] = "stone"
        wall[(5, 65, z)] = "stone"
    world = {"map": {(0, 0): _FakeChunk(wall)}}
    pf = Pathfinder(world)

    start, goal = (0, 64, 0), (10, 64, 0)

    guided = pf.find_path(start, goal, weight=1.0)
    exp_guided = pf._last_nodes_expanded

    autonomous = pf.find_path(start, goal, weight=1.5)
    exp_autonomous = pf._last_nodes_expanded

    assert guided, "guided must find a path around the wall"
    assert autonomous, "autonomous must find a path around the wall"
    assert exp_guided > 0 and exp_autonomous > 0
    assert exp_autonomous <= exp_guided, (
        f"autonomous(w=1.5) expanded {exp_autonomous}, guided(w=1.0) expanded {exp_guided}; "
        f"greedier weight should expand no more")

    print(f"[PASS] test_mode_expansion_difference "
          f"(guided={exp_guided}, autonomous={exp_autonomous} nodes)")


def run():
    test_compression_round_trip()
    test_uncompressed_read()
    test_login_flow_with_compression()
    test_login_rejections()
    test_walkable_rule()
    test_find_path_basic()
    test_mode_expansion_difference()
