"""Verify a vanilla 1.20.2 server accepts AMP's creative mining packet."""

import argparse
import math
import os
import time

from bot import Bot
from tools.check_movement import _rcon_command


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=25565)
    parser.add_argument("--rcon-port", type=int, default=25575)
    parser.add_argument("--username", default="AMPMiningCheck")
    args = parser.parse_args(argv)
    password = os.environ.get("MC_RCON_PASSWORD")
    if not password:
        parser.error("set MC_RCON_PASSWORD to the server's RCON password")

    bot = Bot({
        "host": args.host,
        "port": args.port,
        "username": args.username,
        "version": "1.20.2",
        "game_mode": "creative",
        "behavior_mode": "passive",
    })
    target = None
    try:
        bot._connection.connect()
        deadline = time.time() + 10
        while bot._world_state["position"]["y"] == 0 and time.time() < deadline:
            time.sleep(0.1)
        position = bot._world_state["position"]
        target = (math.floor(position["x"]) + 1, math.floor(position["y"]),
                  math.floor(position["z"]))
        coords = " ".join(map(str, target))
        _rcon_command(args.host, args.rcon_port, password,
                      f"setblock {coords} minecraft:stone")
        time.sleep(0.25)
        before = _rcon_command(
            args.host, args.rcon_port, password,
            f"execute if block {coords} minecraft:stone run seed",
        )
        bot._executor._execute({
            "action": "mine", "x": target[0], "y": target[1], "z": target[2], "face": 4
        })
        time.sleep(0.5)
        after = _rcon_command(
            args.host, args.rcon_port, password,
            f"execute if block {coords} minecraft:air run seed",
        )
        if "Seed" not in before or "Seed" not in after:
            raise AssertionError(f"mining was not confirmed: before={before!r}, after={after!r}")
        print(f"Mining accepted: stone at {target} became air")
    finally:
        if target is not None:
            coords = " ".join(map(str, target))
            _rcon_command(args.host, args.rcon_port, password,
                          f"setblock {coords} minecraft:air")
        bot._connection.disconnect()


if __name__ == "__main__":
    main()
