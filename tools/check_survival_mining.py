"""Verify full-inventory tool selection and timed survival mining on vanilla Java."""

import argparse
import math
import os
import time

from amp.bot import Bot
from tools.check_movement import _rcon_command


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=25565)
    parser.add_argument("--rcon-port", type=int, default=25575)
    parser.add_argument("--username", default="AMPSurvival")
    parser.add_argument("--version", default="26.2")
    args = parser.parse_args(argv)
    password = os.environ.get("MC_RCON_PASSWORD")
    if not password:
        parser.error("set MC_RCON_PASSWORD to the server's RCON password")

    def command(value):
        return _rcon_command(args.host, args.rcon_port, password, value)

    bot = Bot({
        "host": args.host, "port": args.port, "username": args.username,
        "version": args.version, "game_mode": "survival",
    })
    target = None
    try:
        bot._connection.connect()
        command(f"gamemode survival {args.username}")
        command(f"clear {args.username}")
        deadline = time.time() + 5
        while bot._world_state["inventory"]["state_id"] == 0 and time.time() < deadline:
            time.sleep(0.05)
        command(
            f"item replace entity {args.username} inventory.0 "
            "with minecraft:diamond_pickaxe 1"
        )
        deadline = time.time() + 5
        while time.time() < deadline:
            tools = [
                (slot, item) for slot, item in bot._world_state["inventory"]["slots"].items()
                if item["name"] == "diamond_pickaxe"
            ]
            if tools:
                break
            time.sleep(0.05)
        else:
            raise AssertionError("AMP did not decode the main-inventory diamond pickaxe")
        source_slot = tools[0][0]
        if source_slot >= 36:
            raise AssertionError(f"test tool unexpectedly started in hotbar slot {source_slot}")

        position = bot._world_state["position"]
        target = (math.floor(position["x"]) + 1, math.floor(position["y"]),
                  math.floor(position["z"]))
        coords = " ".join(map(str, target))
        command(f"setblock {coords} minecraft:stone")
        time.sleep(0.25)
        if not bot.mine_block(target):
            raise AssertionError("AMP could not plan a reachable survival mining action")
        while bot._executor._command_queue:
            bot._executor.execute_queue()
            time.sleep(0.05)
        time.sleep(0.25)

        after = command(f"execute if block {coords} minecraft:air run seed")
        selected = command(f"data get entity {args.username} SelectedItem.id")
        if "Seed" not in after or "diamond_pickaxe" not in selected:
            raise AssertionError(f"survival mining failed: block={after!r}, item={selected!r}")
        print(f"Full inventory accepted: diamond_pickaxe moved from slot {source_slot}")
        print(f"Survival mining accepted: stone at {target} became air")
    finally:
        if bot._connection._connected:
            command(f"clear {args.username}")
            command(f"gamemode creative {args.username}")
            if target is not None:
                command(f"setblock {' '.join(map(str, target))} minecraft:air")
        bot._connection.disconnect()


if __name__ == "__main__":
    main()
