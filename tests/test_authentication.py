from authentication import (
    AuthenticationError, MINECRAFT, MicrosoftAuthenticator, MinecraftSession,
)


class Clock:
    def __init__(self):
        self.value = 0

    def time(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class FakeTransport:
    def __init__(self, token_error=None, entitled=True):
        self.calls = []
        self.token_error = token_error
        self.entitled = entitled

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if url.endswith("devicecode"):
            return {"message": "Open the code page", "device_code": "device", "expires_in": 30, "interval": 1}
        if url.endswith("token"):
            if self.token_error:
                error, self.token_error = self.token_error, None
                raise AuthenticationError(error)
            return {"access_token": "ms-secret", "refresh_token": "refresh-secret"}
        if "user.auth" in url:
            return {"Token": "xbox-secret"}
        if "xsts.auth" in url:
            return {"Token": "xsts-secret", "DisplayClaims": {"xui": [{"uhs": "user"}]}}
        if url.endswith("login_with_xbox"):
            return {"access_token": "minecraft-secret"}
        if url.endswith("mcstore"):
            return {"items": [{}]} if self.entitled else {"items": []}
        if url.endswith("profile"):
            return {"id": "12345678123456781234567812345678", "name": "Player"}
        raise AssertionError(url)


def test_device_authorization_normalizes_to_redacted_minecraft_session():
    transport = FakeTransport("authorization_pending")
    notices = []
    session = MicrosoftAuthenticator(
        "client", transport, Clock(), notices.append
    ).authorize()

    assert session == MinecraftSession(
        "minecraft-secret", "12345678123456781234567812345678", "Player", "refresh-secret"
    )
    assert notices == ["Open the code page"]
    assert "secret" not in repr(session)
    profile_call = next(call for call in transport.calls if call[1].endswith("profile"))
    assert profile_call[2]["headers"] == {"Authorization": "Bearer minecraft-secret"}


def test_refresh_uses_refresh_grant_and_checks_entitlement():
    transport = FakeTransport(entitled=False)
    authenticator = MicrosoftAuthenticator("client", transport, Clock())

    try:
        authenticator.refresh("refresh-secret")
        assert False
    except AuthenticationError as error:
        assert "does not own" in str(error)
    token_call = transport.calls[0]
    assert token_call[2]["form"]["grant_type"] == "refresh_token"
    assert not any(call[1] == f"{MINECRAFT}/minecraft/profile" for call in transport.calls)
