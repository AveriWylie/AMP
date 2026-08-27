"""Connect an AMP player and keep it online until interrupted."""

import argparse
import signal
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=25565)
    parser.add_argument("--username", default="AMP")
    parser.add_argument("--version", default="26.2")
    parser.add_argument("--game-mode", choices=("survival", "creative"), default="survival")
    return parser.parse_args()


def main():
    args = parse_args()
    from bot import Bot

    stopped = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: stopped.set())

    bot = Bot({
        "host": args.host,
        "port": args.port,
        "username": args.username,
        "version": args.version,
        "game_mode": args.game_mode,
        "behavior_mode": "passive",
    })
    bot.start()
    if not bot._connection._connected:
        raise SystemExit("AMP did not connect to the server")

    print(f"{args.username} is online. Press Ctrl+C to disconnect and stop the local server.")
    try:
        stopped.wait()
    finally:
        bot.disconnect()


if __name__ == "__main__":
    main()
