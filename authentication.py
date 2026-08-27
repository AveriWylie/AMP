"""Microsoft device authorization and Minecraft account session exchange."""

from dataclasses import dataclass
import json
import time
import urllib.error
import urllib.parse
import urllib.request


MICROSOFT = "https://login.microsoftonline.com/consumers/oauth2/v2.0"
XBOX_USER = "https://user.auth.xboxlive.com/user/authenticate"
XBOX_XSTS = "https://xsts.auth.xboxlive.com/xsts/authorize"
MINECRAFT = "https://api.minecraftservices.com"


class AuthenticationError(RuntimeError):
    pass


@dataclass(frozen=True, repr=False)
class MinecraftSession:
    access_token: str
    profile_id: str
    profile_name: str
    refresh_token: str | None = None

    def __repr__(self):
        return f"MinecraftSession(profile_id={self.profile_id!r}, profile_name={self.profile_name!r})"


class UrlLibTransport:
    def request(self, method, url, *, form=None, json_body=None, headers=None):
        request_headers = dict(headers or {})
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            request_headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif json_body is not None:
            data = json.dumps(json_body).encode()
            request_headers["Content-Type"] = "application/json"
        else:
            data = None
        request = urllib.request.Request(
            url, data=data, headers=request_headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response) if response.length != 0 else {}
        except urllib.error.HTTPError as error:
            try:
                detail = json.load(error)
                code = detail.get("error") or detail.get("path") or "request_failed"
            except (ValueError, AttributeError):
                code = "request_failed"
            raise AuthenticationError(f"Account service rejected request: {code}") from None


class SessionJoiner:
    """Authorize one Minecraft server hash without retaining account credentials."""

    def __init__(self, transport=None):
        self.transport = transport or UrlLibTransport()

    def join(self, session, server_hash):
        self.transport.request(
            "POST", "https://sessionserver.mojang.com/session/minecraft/join",
            json_body={
                "accessToken": session.access_token,
                "selectedProfile": session.profile_id.replace("-", ""),
                "serverId": server_hash,
            },
        )


class MicrosoftAuthenticator:
    def __init__(self, client_id, transport=None, clock=time, notify=print):
        if not client_id:
            raise ValueError("A Microsoft public-client application ID is required")
        self.client_id = client_id
        self.transport = transport or UrlLibTransport()
        self.clock = clock
        self.notify = notify

    def authorize(self):
        device = self.transport.request("POST", f"{MICROSOFT}/devicecode", form={
            "client_id": self.client_id,
            "scope": "XboxLive.signin offline_access",
        })
        self.notify(device["message"])
        interval = int(device.get("interval", 5))
        deadline = self.clock.time() + int(device["expires_in"])
        while self.clock.time() < deadline:
            self.clock.sleep(interval)
            try:
                token = self.transport.request("POST", f"{MICROSOFT}/token", form={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "client_id": self.client_id,
                    "device_code": device["device_code"],
                })
                return self._minecraft_session(token)
            except AuthenticationError as error:
                if "authorization_pending" in str(error):
                    continue
                if "slow_down" in str(error):
                    interval += 5
                    continue
                raise
        raise AuthenticationError("Microsoft device authorization expired")

    def refresh(self, refresh_token):
        token = self.transport.request("POST", f"{MICROSOFT}/token", form={
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "refresh_token": refresh_token,
            "scope": "XboxLive.signin offline_access",
        })
        return self._minecraft_session(token)

    def _minecraft_session(self, microsoft):
        xbox = self.transport.request("POST", XBOX_USER, json_body={
            "Properties": {
                "AuthMethod": "RPS", "SiteName": "user.auth.xboxlive.com",
                "RpsTicket": f"d={microsoft['access_token']}",
            },
            "RelyingParty": "http://auth.xboxlive.com",
            "TokenType": "JWT",
        })
        xsts = self.transport.request("POST", XBOX_XSTS, json_body={
            "Properties": {
                "SandboxId": "RETAIL", "UserTokens": [xbox["Token"]],
            },
            "RelyingParty": "rp://api.minecraftservices.com/",
            "TokenType": "JWT",
        })
        user_hash = xsts["DisplayClaims"]["xui"][0]["uhs"]
        minecraft = self.transport.request(
            "POST", f"{MINECRAFT}/authentication/login_with_xbox",
            json_body={"identityToken": f"XBL3.0 x={user_hash};{xsts['Token']}"},
        )
        headers = {"Authorization": f"Bearer {minecraft['access_token']}"}
        entitlement = self.transport.request(
            "GET", f"{MINECRAFT}/entitlements/mcstore", headers=headers
        )
        if not entitlement.get("items"):
            raise AuthenticationError("Account does not own Minecraft: Java Edition")
        profile = self.transport.request(
            "GET", f"{MINECRAFT}/minecraft/profile", headers=headers
        )
        return MinecraftSession(
            minecraft["access_token"], profile["id"], profile["name"],
            microsoft.get("refresh_token"),
        )
