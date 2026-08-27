"""Resolve a Java Realm to its temporary direct-server endpoint."""

from dataclasses import dataclass

from amp.authentication import AuthenticationError, UrlLibTransport


REALMS_API = "https://pc.realms.minecraft.net"


class RealmError(RuntimeError):
    pass


@dataclass(frozen=True)
class Realm:
    id: int
    name: str
    owner: str
    state: str
    expired: bool


@dataclass(frozen=True)
class RealmEndpoint:
    realm: Realm
    host: str
    port: int


class RealmResolver:
    def __init__(self, transport=None):
        self.transport = transport or UrlLibTransport()

    @staticmethod
    def _headers(session):
        return {
            "Authorization": f"Bearer {session.access_token}",
            "User-Agent": "AMP/1.0",
            "Client-Version": "1.0",
        }

    def list(self, session):
        try:
            response = self.transport.request(
                "GET", f"{REALMS_API}/worlds", headers=self._headers(session)
            )
        except AuthenticationError as error:
            raise RealmError(str(error)) from None
        return tuple(Realm(
            int(world["id"]), world["name"], world.get("owner", ""),
            world.get("state", "UNKNOWN"), bool(world.get("expired", False)),
        ) for world in response.get("servers", ()))

    def resolve(self, session, selection):
        realms = self.list(session)
        matches = [realm for realm in realms if (
            str(realm.id) == str(selection)
            or realm.name.casefold() == str(selection).casefold()
        )]
        if not matches:
            raise RealmError(f"No accessible Realm matches {selection!r}")
        if len(matches) > 1:
            raise RealmError(f"Realm name {selection!r} is ambiguous; use its numeric ID")
        realm = matches[0]
        if realm.expired or realm.state != "OPEN":
            raise RealmError(f"Realm {realm.name!r} is not open")
        try:
            response = self.transport.request(
                "GET", f"{REALMS_API}/worlds/v1/{realm.id}/join/pc",
                headers=self._headers(session),
            )
        except AuthenticationError as error:
            raise RealmError(str(error)) from None
        address = response.get("address", "")
        try:
            host, port = address.rsplit(":", 1)
            port = int(port)
        except (ValueError, AttributeError):
            raise RealmError("Realm service returned an invalid server address") from None
        if not host or port not in range(1, 65536):
            raise RealmError("Realm service returned an invalid server address")
        return RealmEndpoint(realm, host, port)
