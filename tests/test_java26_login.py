from connection import Connection
from java26_protocol import Java26ProtocolAdapter


class FakeSocket:
    def __init__(self, incoming=b""):
        self.incoming = bytearray(incoming)
        self.sent = []

    def recv(self, size):
        result = bytes(self.incoming[:size])
        del self.incoming[:size]
        return result

    def sendall(self, data):
        self.sent.append(data)


def frame(body):
    return Connection._encode_varint(len(body)) + body


def body(frame_value):
    _, consumed = Connection._decode_varint_bytes(frame_value, 0)
    return frame_value[consumed:]


def connection(version="26.1", protocol=775, family="java-26.1"):
    value = Connection("localhost", 25565, version, "AMP", None, protocol)
    adapter = Java26ProtocolAdapter(family, version, value)
    value.set_protocol_adapter(adapter)
    value._start_func = lambda: None
    return value, adapter


def test_java26_login_handles_cookie_and_enters_configuration():
    value, adapter = connection()
    login = adapter.login_clientbound
    config = adapter.configuration_clientbound
    encode = Connection._encode_varint
    incoming = b"".join((
        frame(encode(login["cookie_request"]) + value._encode_string("amp:test")),
        frame(encode(login["success"]) + b"profile"),
        frame(encode(config["select_known_packs"]) + b"\x00"),
        frame(encode(config["keep_alive"]) + b"12345678"),
        frame(encode(config["finish_configuration"])),
    ))
    value._socket = FakeSocket(incoming)

    value._login()

    sent = [body(packet) for packet in value._socket.sent]
    assert sent[0].endswith(value._encode_string("amp:test") + b"\x00")
    assert sent[1] == encode(adapter.login_serverbound["login_acknowledged"])
    assert sent[3] == encode(adapter.configuration_serverbound["select_known_packs"]) + b"\x00"
    assert sent[4].endswith(b"12345678")
    assert sent[5] == encode(adapter.configuration_serverbound["finish_configuration"])
    assert value._connected is True


def test_java26_configuration_declines_resource_pack_by_uuid():
    value, adapter = connection()
    pack_id = bytes(range(16))
    value._socket = FakeSocket(b"".join((
        frame(Connection._encode_varint(adapter.configuration_clientbound["add_resource_pack"]) + pack_id),
        frame(Connection._encode_varint(adapter.configuration_clientbound["finish_configuration"])),
    )))

    adapter.handle_configuration()

    response = body(value._socket.sent[1])
    assert response.endswith(pack_id + b"\x01")


def test_java26_login_rejects_online_mode_until_account_session_is_added():
    value, adapter = connection()
    value._socket = FakeSocket(frame(
        Connection._encode_varint(adapter.login_clientbound["encryption_begin"])
    ))

    try:
        value._login()
        assert False
    except ConnectionError as error:
        assert "authenticated online-mode" in str(error)
