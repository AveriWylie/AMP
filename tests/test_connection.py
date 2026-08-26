"""
--------------------------------------------------------------------------------------------
Specific Test Module - Bot and Connection structural and behavioral tests
--------------------------------------------------------------------------------------------
Tests the initialization, validation, connection lifecycle, packet encoding, keepalive,
and failure handling of Bot and Connection. Execution layer excluded by design.
Meant to be imported and called from a main test runner. Documented less thoroughly then
the class that which this test's.
--------------------------------------------------------------------------------------------
"""
# imports
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading

import pytest
from bot import Bot, Connection

"""
--------------------------------------------------------------------------------------------
Helpers
--------------------------------------------------------------------------------------------
"""
# var=... is a default value and cant be mutable therefor cant do overrides={} in python as
# a parameter
def _make_bot(overrides=None):
    if overrides is None:
        overrides = {}

    base = {
        "host": "localhost", "port": 25565, "username": "TestBot",
        "version": "1.20.2", "game_mode": "survival", "behavior_mode": "passive",
    }
    base.update(overrides)
    return Bot(base)

# Returns True if something is listening on localhost:25565
def _probe_localhost() -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(1)
    reachable = probe.connect_ex(("localhost", 25565)) == 0
    probe.close()
    return reachable

# --------------------------------------------------------------------------------------------

# --------------------------------------------------------------------------------------------
# 1. Validation
# --------------------------------------------------------------------------------------------
# Bot correctly accepts good config, rejects bad values with fallback to defaults.
def test_validation():
    # All valid â€” all flags should be True
    bot_good = _make_bot()
    assert all(bot_good._valid_flags.values()), "Expected all flags True for good config"

    # Bad values â€” flags False, fields replaced by defaults
    bot_bad = _make_bot({"port": 99, "version": "0.0.0", "game_mode": "god_mode"})
    assert not bot_bad._valid_flags["port"]
    assert not bot_bad._valid_flags["version"]
    assert not bot_bad._valid_flags["game_mode"]
    assert bot_bad._port == Bot.default_values["port"]
    assert bot_bad._version == Bot.default_values["version"]
    assert bot_bad._game_mode == Bot.default_values["game_mode"]

    # These three fields were passed valid values in the bad config, The assertion is
    # confirming that the validation loop (called after make bot calls init) only replaces the
    # fields that failed, and leaves the valid ones exactly as passed. It's checking that
    # fixing one field doesn't accidentally clobber another.
    assert bot_bad._host == "localhost"
    assert bot_bad._username == "TestBot"
    assert bot_bad._behavior_mode == "passive"

    # Empty config â€” everything replaced by defaults, we are making bot without helper here
    bot_empty = Bot({})
    for key, default in Bot.default_values.items():
        assert getattr(bot_empty, f"_{key}") == default, (f"Expected default for {key}, "
                                                          f"got {getattr(bot_empty, f'_{key}')}")

    # None values explicitly passed â€” treated same as missing
    bot_none = Bot({k: None for k in Bot.default_values})
    for key, default in Bot.default_values.items():
        assert getattr(bot_none, f"_{key}") == default, \
            f"Expected default for {key} when None passed"


def test_bot_module_imports_outside_repository_root(tmp_path):
    repository = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, (
        str(repository), environment.get("PYTHONPATH")
    )))

    result = subprocess.run(
        [sys.executable, "-c", "import bot"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr



# --------------------------------------------------------------------------------------------
# 2. set()
# --------------------------------------------------------------------------------------------
# Bot.set() updates valid fields correctly and falls back to default on invalid input without
# corrupting other fields.
def test_set():
    bot = _make_bot()

    # Valid update
    bot.set("game_mode", "creative")
    assert bot._game_mode == "creative"
    assert bot._valid_flags["game_mode"] is True

    # Invalid update â€” falls back to default, flag corrects itself
    bot.set("behavior_mode", "berserker")
    assert bot._behavior_mode == Bot.default_values["behavior_mode"]
    assert bot._valid_flags["behavior_mode"] is True

    # Setting a field to its current value â€” no-op, stays valid
    bot.set("game_mode", "creative")
    assert bot._game_mode == "creative"

    # All valid game_modes accepted
    for mode in Bot.allowed_values["game_mode"]:
        bot.set("game_mode", mode)
        assert bot._game_mode == mode

    # All valid behavior_modes accepted
    for mode in Bot.allowed_values["behavior_mode"]:
        bot.set("behavior_mode", mode)
        assert bot._behavior_mode == mode



# --------------------------------------------------------------------------------------------
# 3. Connection composition
# --------------------------------------------------------------------------------------------
# Connection object inside Bot is correctly initialized with the right attributes and
# initial state before any connect() call.
def test_connection_composition():
    bot = _make_bot()
    conn = bot._connection

    assert isinstance(conn, Connection)
    assert conn._host == "localhost"
    assert conn._port == 25565
    assert conn._username == "TestBot"
    assert conn._protocol_version == 764
    assert conn._connected is False
    assert conn._started is False
    assert conn._socket is None
    assert conn._thread_a is None
    # == not is: each access to bot._handle_failure builds a fresh bound-method object, so `is`
    # is always False. == compares __self__ and __func__, the identity we actually mean.
    assert conn._on_failure == bot._handle_failure



# --------------------------------------------------------------------------------------------
# 4. Encode varint
# --------------------------------------------------------------------------------------------
# _encode_varint produces correct byte sequences for known values including boundary cases
# and multibyte encodings, and round-trips back to the original integer.
def test_encode_varint():
    enc = Connection._encode_varint

    # Single byte â€” 0 to 127 fit in 7 bits, no continuation bit needed
    assert enc(0) == b"\x00"
    assert enc(1) == b"\x01"
    assert enc(127) == b"\x7f"

    # 128 is the first value that spills into a second byte
    assert enc(128) == b"\x80\x01"

    # 300 â€” known 2-byte encoding
    assert enc(300) == b"\xac\x02"

    # Protocol version used in handshake
    assert len(enc(762)) == 2

    # Negative must raise
    try:
        enc(-1)
        assert False, "Should have raised ValueError"
    except ValueError:
        pass

    # Round-trip every boundary value â€” encode then decode manually
    for val in [0, 1, 127, 128, 255, 300, 762, 2097151]:
        encoded = enc(val)
        decoded = 0
        shift = 0
        for byte in encoded:
            decoded |= (byte & 0x7F) << shift
            shift += 7
        assert decoded == val, f"Round-trip failed for {val}"



# --------------------------------------------------------------------------------------------
# 5. Encode string
# --------------------------------------------------------------------------------------------
# _encode_string produces a VarInt length prefix followed by the UTF-8 bytes of the string,
# including the multibyte prefix case for strings over 127 bytes.
def test_encode_string():
    conn = Connection("localhost", 25565, "1.21.4", "TestBot", None, 762)

    # "hello" is 5 UTF-8 bytes â€” VarInt(5) = 0x05
    assert conn._encode_string("hello") == b"\x05hello"

    # Empty string â€” VarInt(0) + nothing
    assert conn._encode_string("") == b"\x00"

    # 200 'a's â€” VarInt(200) = [0xC8, 0x01] as a 2-byte prefix
    result = conn._encode_string("a" * 200)
    assert result[:2] == b"\xc8\x01"
    assert result[2:] == b"a" * 200



# --------------------------------------------------------------------------------------------
# 6. Encode unsigned short
# --------------------------------------------------------------------------------------------
# _encode_unsigned_short produces exactly 2 big-endian bytes for any valid port.
def test_encode_unsigned_short():
    """
    _encode_unsigned_short produces exactly 2 big-endian bytes for any valid port.
    """
    enc = Connection._encode_unsigned_short

    assert enc(25565) == b"\x63\xdd"
    assert enc(0) == b"\x00\x00"
    assert enc(65535) == b"\xff\xff"
    assert len(enc(25565)) == 2



def _decode_packet_length(packet) -> tuple:
    idx = 0
    length = 0
    shift = 0
    while True:
        byte = packet[idx]
        length |= (byte & 0x7F) << shift
        idx += 1
        shift += 7
        if not (byte & 0x80):
            break

    return idx, length

# --------------------------------------------------------------------------------------------
# 7. Serialize handshake structure
# --------------------------------------------------------------------------------------------
# _serialize_handshake produces a packet with the correct envelope: VarInt length prefix,
# packet_id 0x00, then data fields containing host and port.
def test_serialize_handshake():
    conn = Connection("localhost", 25565, "1.21.4", "TestBot", None, 762)
    packet = conn._serialize_handshake()

    # Decode the leading VarInt length prefix
    idx, length = _decode_packet_length(packet)

    payload = packet[idx:]

    # Length prefix must exactly match the remaining payload
    assert len(payload) == length, "Length prefix does not match payload size"

    # First byte of payload is packet_id 0x00
    assert payload[0] == 0x00

    # Host string must appear in the payload
    assert b"localhost" in payload

    # Next state VarInt(2) must be the last byte â€” login intent
    assert payload[-1] == 0x02



# --------------------------------------------------------------------------------------------
# 8. Keepalive response structure
# --------------------------------------------------------------------------------------------
# _keepalive_response_aux wraps a payload in the correct envelope: VarInt length,
# packet_id 0x12, then the original payload echoed unchanged.
def test_keepalive_response():
    conn = Connection("localhost", 25565, "1.21.4", "TestBot", None, 762)

    payload = b"\x00\x00\x00\x00\x00\x00\x04\xd2"
    packet = conn._keepalive_response_aux(payload)

    # Decode the length prefix
    idx, length = _decode_packet_length(packet)

    body = packet[idx:]
    assert len(body) == length
    assert body[0:1] == b"\x12"  # correct packet_id for protocol 762
    assert body[1:] == payload  # payload echoed exactly

    # Empty payload â€” packet_id still present, length accounts for it
    empty = conn._keepalive_response_aux(b"")
    assert empty == b"\x01\x12"



# --------------------------------------------------------------------------------------------
# 9. Disconnect state
# --------------------------------------------------------------------------------------------
# disconnect() resets _connected to False and _socket to None, and is safe to call when
# already disconnected.
def test_disconnect_state():
    import socket as _socket

    bot = _make_bot()
    conn = bot._connection

    # Manually fake a connected state without a real server
    conn._socket = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    conn._connected = True

    conn.disconnect()
    assert conn._connected is False
    assert conn._socket is None

    # Second disconnect â€” must not raise
    try:
        conn.disconnect()
    except Exception as e:
        assert False, f"disconnect() raised on already-disconnected: {e}"



# --------------------------------------------------------------------------------------------
# 10. Double connect guard
# --------------------------------------------------------------------------------------------
# Calling connect() when already connected does not replace the socket or raise.
def test_double_connect_guard():
    import socket as _socket

    bot = _make_bot()
    conn = bot._connection

    fake_sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    conn._socket = fake_sock
    conn._connected = True

    conn.connect()
    assert conn._socket is fake_sock, "Socket should not be replaced on double connect"

    # Clean up
    conn._connected = False
    fake_sock.close()



# --------------------------------------------------------------------------------------------
# 11. Failure handler
# --------------------------------------------------------------------------------------------
# _handle_failure fires the reconnect loop exactly 3 times when connect() always raises
# ConnectionError
def test_failure_handler():
    bot = _make_bot()
    attempts = []  # list not int â€” avoids Python closure rebind scoping issue

    def fake_connect():
        attempts.append(1)
        raise ConnectionError("Simulated server unreachable")

    bot._connection.connect = fake_connect
    bot._handle_failure(ConnectionError("Simulated drop"))

    assert len(attempts) == 3, f"Expected 3 reconnect attempts, got {len(attempts)}"


# _handle_failure with a non-ConnectionError does not enter the reconnect loop, the loop
# is gated on isinstance(e, ConnectionError)
def test_failure_handler_non_connection_error():
    bot = _make_bot()
    attempts = []

    def fake_connect():
        attempts.append(1)

    bot._connection.connect = fake_connect
    bot._handle_failure(ValueError("some protocol error"))

    assert len(attempts) == 0, "Reconnect loop should not fire for non-ConnectionError"


# _handle_failure stops retrying as soon as connect() succeeds, before exhausting all 3
# attempts.
def test_failure_handler_succeeds_on_retry():
    bot = _make_bot()
    attempts = []

    def fake_connect():
        attempts.append(1)
        if len(attempts) < 2:
            raise ConnectionError("Still unreachable")
        # succeeds silently on attempt 2

    bot._connection.connect = fake_connect
    bot._handle_failure(ConnectionError("Simulated drop"))

    assert len(attempts) == 2, f"Expected 2 attempts before success, got {len(attempts)}"



# --------------------------------------------------------------------------------------------
# 12. Live connection (skipped if no server running)
# --------------------------------------------------------------------------------------------
# If a Minecraft server is running on localhost:25565, verifies the full connect/keepalive/
# disconnect lifecycle including keepalive thread state.
def test_live_connection():

    if not _probe_localhost():
        pytest.skip("no server on localhost:25565")

    import time
    bot = _make_bot()
    conn = bot._connection

    try:
        conn.connect()
        assert conn._connected is True
        assert conn._started is True
        assert conn._thread_a is not None
        assert conn._thread_a.is_alive()

        time.sleep(5)  # let keepalive cycle at least once
        assert conn._connected is True  # still alive after keepalive

        conn.disconnect()
        assert conn._connected is False
        assert conn._socket is None

    except ConnectionError as e:
        pytest.skip(f"server rejected handshake: {e}")


def test_start_execution_does_not_duplicate_live_worker():
    bot = Bot.__new__(Bot)
    bot._connection = type("Connected", (), {"_connected": True})()
    bot._execution_started = False
    release = threading.Event()
    started = []

    def execution_loop():
        started.append(threading.current_thread())
        release.wait(1)

    bot._execution_loop = execution_loop
    try:
        bot._start_execution()
        first_worker = bot._execution_thread
        bot._start_execution()

        assert bot._execution_thread is first_worker
        assert len(started) == 1
    finally:
        release.set()
        for worker in started:
            worker.join(1)
