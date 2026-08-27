"""Opt-in authenticated join check for a direct online-mode Java server."""

import argparse
import os
import time

from authentication import MicrosoftAuthenticator
from bot import Bot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host")
    parser.add_argument("--port", type=int, default=25565)
    parser.add_argument("--version", default="26.2")
    args = parser.parse_args()
    session = MicrosoftAuthenticator(os.getenv("AMP_MICROSOFT_CLIENT_ID")).authorize()
    bot = Bot({
        "host": args.host, "port": args.port, "version": args.version,
        "username": session.profile_name, "auth_session": session,
        "game_mode": "survival", "behavior_mode": "passive",
    })
    bot._connection.connect()
    time.sleep(2)
    if not bot._connection._connected:
        raise SystemExit("Authenticated connection did not reach Play")
    print(f"Authenticated as {session.profile_name}; online-mode Play reached")
    bot.disconnect()


if __name__ == "__main__":
    main()
