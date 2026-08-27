"""Opt-in non-destructive Realm resolve/join/position/disconnect smoke check."""

def main():
    raise SystemExit(
        "Unavailable: AMP's Microsoft client ID is not approved by Minecraft "
        "Services. See docs/AUTHENTICATION.md."
    )


def _approved_main():
    """Retained live gate to activate after Minecraft Services approval."""
    import argparse
    import os
    import time

    from amp.authentication import MicrosoftAuthenticator
    from amp.bot import Bot
    from amp.realms import RealmResolver

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("realm", help="Realm name or numeric ID")
    parser.add_argument("--version", default="26.2")
    args = parser.parse_args()
    session = MicrosoftAuthenticator(os.getenv("AMP_MICROSOFT_CLIENT_ID")).authorize()
    endpoint = RealmResolver().resolve(session, args.realm)
    bot = Bot({
        "host": endpoint.host, "port": endpoint.port, "version": args.version,
        "username": session.profile_name, "auth_session": session,
        "game_mode": "survival",
    })
    bot._connection.connect()
    time.sleep(2)
    position = bot._world_state["position"]
    if not bot._connection._connected or position == {
        "x": 0.0, "y": 0.0, "z": 0.0, "yaw": 0.0, "pitch": 0.0
    }:
        raise SystemExit("Realm smoke check did not reach positioned Play state")
    print(f"Realm {endpoint.realm.name!r} reached Play at {position}")
    bot.disconnect()


if __name__ == "__main__":
    main()
