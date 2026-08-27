from authentication import AuthenticationError, MinecraftSession
from realms import RealmError, RealmResolver


SESSION = MinecraftSession("secret", "profile", "Player")


class Transport:
    def __init__(self, worlds=None, address="realm.example:25565", error=None):
        self.worlds = worlds or []
        self.address = address
        self.error = error
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if self.error:
            raise AuthenticationError(self.error)
        if url.endswith("/worlds"):
            return {"servers": self.worlds}
        return {"address": self.address}


def world(**overrides):
    value = {"id": 42, "name": "Build", "owner": "Friend", "state": "OPEN", "expired": False}
    value.update(overrides)
    return value


def test_resolve_realm_by_name_and_normalize_endpoint():
    transport = Transport([world()])
    endpoint = RealmResolver(transport).resolve(SESSION, "build")

    assert (endpoint.host, endpoint.port, endpoint.realm.id) == ("realm.example", 25565, 42)
    assert all(call[2]["headers"]["Authorization"] == "Bearer secret" for call in transport.calls)


def test_resolve_rejects_missing_closed_and_invalid_realms():
    for transport, selection, message in (
        (Transport([]), "missing", "No accessible"),
        (Transport([world(state="CLOSED")]), 42, "not open"),
        (Transport([world()], address="invalid"), 42, "invalid server address"),
        (Transport(error="service unavailable"), 42, "service unavailable"),
    ):
        try:
            RealmResolver(transport).resolve(SESSION, selection)
            assert False
        except RealmError as error:
            assert message in str(error)
