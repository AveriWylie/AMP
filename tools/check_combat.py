"""Verify tracked-entity combat against a supported vanilla Java server."""

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
    parser.add_argument("--username", default="AMPCombat")
    parser.add_argument("--version", default="26.1.2")
    args = parser.parse_args(argv)
    password = os.environ.get("MC_RCON_PASSWORD")
    if not password:
        parser.error("set MC_RCON_PASSWORD to the server's RCON password")

    def command(value):
        return _rcon_command(args.host, args.rcon_port, password, value)

    bot = Bot({
        "host": args.host, "port": args.port, "username": args.username,
        "version": args.version, "game_mode": "survival", "behavior_mode": "passive",
    })
    try:
        bot._connection.connect()
        command(f"gamemode survival {args.username}")
        command(f"effect give {args.username} minecraft:resistance 10 4 true")
        command(f"execute at {args.username} run kill @e[tag=AMPCombatTarget]")
        command(
            f"execute at {args.username} run summon minecraft:cow ^ ^ ^2 "
            "{NoAI:1b,Silent:1b,Tags:[\"AMPCombatTarget\"]}"
        )
        deadline = time.time() + 5
        target_id = None
        while time.time() < deadline:
            targets = [
                (entity_id, entity) for entity_id, entity in bot._world_state["entities"].items()
                if entity.get("name") == "cow"
            ]
            if targets:
                target_id, _ = min(targets, key=lambda entry: entry[1]["z"])
                break
            time.sleep(0.05)
        if target_id is None:
            raise AssertionError("AMP did not track the summoned cow")
        before = command("data get entity @e[tag=AMPCombatTarget,limit=1] Health")
        if not bot.attack_entity(target_id):
            raise AssertionError("AMP refused an in-reach tracked entity")
        while bot._executor._command_queue:
            bot._executor.execute_queue()
            time.sleep(0.05)
        time.sleep(0.5)
        after = command("data get entity @e[tag=AMPCombatTarget,limit=1] Health")
        if before == after:
            raise AssertionError(f"server health did not change: {before!r}")
        print(f"Entity tracking accepted: cow entity {target_id}")
        print(f"Combat accepted: {before} -> {after}")
    finally:
        if bot._connection._connected:
            command(f"execute at {args.username} run kill @e[tag=AMPCombatTarget]")
        bot._connection.disconnect()


if __name__ == "__main__":
    main()
