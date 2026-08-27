from amp.connection import Connection
from amp.java26_protocol import Java26ProtocolAdapter
from amp.authentication import MinecraftSession
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


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


def test_java26_login_rejects_online_mode_without_account_session():
    value, adapter = connection()
    value._socket = FakeSocket(frame(
        Connection._encode_varint(adapter.login_clientbound["encryption_begin"])
    ))

    try:
        value._login()
        assert False
    except ConnectionError as error:
        assert "Microsoft-authenticated" in str(error)


def test_java26_login_joins_session_and_enables_encrypted_transport(monkeypatch):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    verify_token = b"verify"
    session = MinecraftSession("access-secret", "profile-id", "AMP")

    class Joiner:
        def __init__(self):
            self.joined = None

        def join(self, joined_session, server_hash):
            self.joined = (joined_session, server_hash)

    joiner = Joiner()
    value = Connection(
        "localhost", 25565, "26.1", "AMP", None, 775,
        auth_session=session, session_joiner=joiner,
    )
    adapter = Java26ProtocolAdapter("java-26.1", "26.1", value)
    value.set_protocol_adapter(adapter)
    raw_socket = FakeSocket()
    value._socket = raw_socket
    monkeypatch.setattr("amp.connection.os.urandom", lambda size: b"s" * size)
    encode = Connection._encode_varint
    payload = (
        value._encode_string("") + encode(len(public_key)) + public_key
        + encode(len(verify_token)) + verify_token + b"\x01"
    )

    assert adapter.handle_login(
        adapter.login_clientbound["encryption_begin"], payload, session
    ) is False

    assert joiner.joined[0] is session
    response = body(raw_socket.sent[0])
    _, offset = Connection._decode_varint_bytes(response, 0)
    secret_length, consumed = Connection._decode_varint_bytes(response, offset)
    offset += consumed
    encrypted_secret = response[offset:offset + secret_length]
    offset += secret_length
    token_length, consumed = Connection._decode_varint_bytes(response, offset)
    offset += consumed
    encrypted_token = response[offset:offset + token_length]
    assert private_key.decrypt(encrypted_secret, padding.PKCS1v15()) == b"s" * 16
    assert private_key.decrypt(encrypted_token, padding.PKCS1v15()) == verify_token
