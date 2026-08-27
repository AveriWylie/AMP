"""Verify live inventory updates and serverbound hotbar selection."""

import argparse
import os
import time

from bot import Bot
from tools.check_movement import _rcon_command


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=25565)
    parser.add_argument("--rcon-port", type=int, default=25575)
    parser.add_argument("--username", default="AMPInvCheck")
    parser.add_argument("--version", default="26.1.2")
    args = parser.parse_args(argv)
    password = os.environ.get("MC_RCON_PASSWORD")
    if not password:
        parser.error("set MC_RCON_PASSWORD to the server's RCON password")

    def command(value):
        return _rcon_command(args.host, args.rcon_port, password, value)

    bot = Bot({
        "host": args.host, "port": args.port, "username": args.username,
        "version": args.version, "game_mode": "creative", "behavior_mode": "passive",
    })
    try:
        bot._connection.connect()
        command(f"item replace entity {args.username} hotbar.2 with minecraft:diamond_pickaxe 1")
        deadline = time.time() + 5
        while time.time() < deadline:
            item = bot._world_state["inventory"]["slots"].get(38)
            if item and item["name"] == "diamond_pickaxe":
                break
            time.sleep(0.05)
        else:
            raise AssertionError(f"AMP did not receive slot 38: {bot._world_state['inventory']}")

        bot._executor._execute({"action": "select_hotbar", "slot": 2})
        time.sleep(0.25)
        selected = command(f"data get entity {args.username} SelectedItemSlot")
        if not selected.rstrip().endswith("2"):
            raise AssertionError(f"server did not select hotbar slot 2: {selected}")
        print("Inventory accepted: slot 38 contains diamond_pickaxe x1")
        print("Hotbar accepted: server SelectedItemSlot is 2")
    finally:
        if bot._connection._connected:
            command(f"clear {args.username}")
        bot._connection.disconnect()


if __name__ == "__main__":
    main()
