
from dataclasses import dataclass
from amp.authentication import AuthenticationError, request


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


"""
--------------------------------------------------------------------------------------------
Class Header - Realm resolver
--------------------------------------------------------------------------------------------
Turns a Realm, named or numbered, into the host and port AMP can actually connect to. Realms
have no fixed address, the service hands out a temporary endpoint when you ask to join, so
this is a lookup that has to happen every session rather than something you configure once.

Every AuthenticationError is re-raised as RealmError, and every one uses "from None". The
caller asked about a Realm, not about a token exchange, so the auth chain is noise in the
traceback. It also keeps the access token out of anything that gets printed or logged.
--------------------------------------------------------------------------------------------
"""
class RealmResolver:

    def __init__(self, transport=None):
        self.transport = transport or request


    # The Realms service rejects requests without a client version, so these are required
    # rather than decoration, it will not answer a bare authenticated request.
    @staticmethod
    def _headers(session):
        return {"Authorization": f"Bearer {session.access_token}", "User-Agent": "AMP/1.0", "Client-Version": "1.0"}


    # Missing keys default rather than raising, an unnamed or ownerless Realm is still listable
    # and still joinable, so it should not take out the whole listing.
    def list(self, session):
        try:
            response = self.transport("GET", f"{REALMS_API}/worlds", headers=self._headers(session))
        except AuthenticationError as error:
            raise RealmError(str(error)) from None

        return tuple(Realm(
            int(world["id"]), world["name"], world.get("owner", ""),
            world.get("state", "UNKNOWN"), bool(world.get("expired", False)),
        ) for world in response.get("servers", ()))


    """
    --------------------------------------------------------------------------------------------
    Function Header - Resolve
    --------------------------------------------------------------------------------------------
    Matches a selection against the account's Realms, then asks the service for a join address.

    Selection accepts an ID or a name because both are things a person reasonably has to hand,
    and names are matched case-insensitively since nobody remembers their own capitalisation.
    IDs are compared as strings so "42" and 42 behave the same.

    An ambiguous name is an error rather than a first-match guess. Two Realms can share a name,
    and silently joining the wrong world is worse than making the caller disambiguate with an ID.

    The state and expiry check happens before the join request, not after. Asking the service to
    join a closed Realm returns a less useful error than the one written here.

    The address arrives as one "host:port" string, so it is split from the right, a hostname can
    contain colons and the port never does. Both the parse and the range check answer the same
    message, from the caller's side a malformed address and an impossible port are one problem.
    --------------------------------------------------------------------------------------------
    """
    def resolve(self, session, selection):
        realms = self.list(session)

        matches = [realm for realm in realms if (str(realm.id) == str(selection)
                                                 or realm.name.casefold() == str(selection).casefold())]

        if not matches:
            raise RealmError(f"No accessible Realm matches {selection!r}")

        # names are not unique, so refuse rather than guessing which world was meant
        if len(matches) > 1:
            raise RealmError(f"Realm name {selection!r} is ambiguous; use its numeric ID")

        realm = matches[0]

        # check before asking to join, the service's own refusal is less informative than this
        if realm.expired or realm.state != "OPEN":
            raise RealmError(f"Realm {realm.name!r} is not open")

        try:
            response = self.transport("GET", f"{REALMS_API}/worlds/v1/{realm.id}/join/pc",headers=self._headers(session))
        except AuthenticationError as error:
            raise RealmError(str(error)) from None

        address = response.get("address", "")

        # split from the right, hostnames may contain colons and the port never does
        try:
            host, port = address.rsplit(":", 1)
            port = int(port)
        except (ValueError, AttributeError):
            raise RealmError("Realm service returned an invalid server address") from None

        if not host or port not in range(1, 65536):
            raise RealmError("Realm service returned an invalid server address")

        return RealmEndpoint(realm, host, port)
