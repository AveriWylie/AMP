"""Verify a live server accepts AMP's movement packet using its RCON position."""

import argparse
import os
import socket
import struct
import time

from bot import Bot


def _rcon_packet(request_id, packet_type, body):
    payload = struct.pack("<ii", request_id, packet_type) + body.encode() + b"\0\0"
    return struct.pack("<i", len(payload)) + payload


def _rcon_command(host, port, password, command):
    with socket.create_connection((host, port), timeout=5) as connection:
        connection.sendall(_rcon_packet(1, 3, password))
        auth = connection.recv(4096)
        if len(auth) < 12 or struct.unpack_from("<i", auth, 4)[0] == -1:
            raise ConnectionError("RCON authentication failed")
        connection.sendall(_rcon_packet(2, 2, command))
        response = connection.recv(4096)
    size = struct.unpack_from("<i", response)[0]
    return response[12:4 + size - 2].decode()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=25565)
    parser.add_argument("--rcon-port", type=int, default=25575)
    parser.add_argument("--username", default="AMPMovementCheck")
    parser.add_argument("--version", default="26.2")
    args = parser.parse_args(argv)
    password = os.environ.get("MC_RCON_PASSWORD")
    if not password:
        parser.error("set MC_RCON_PASSWORD to the server's RCON password")

    bot = Bot({
        "host": args.host,
        "port": args.port,
        "username": args.username,
        "version": args.version,
        "game_mode": "creative",
    })
    try:
        bot._connection.connect()
        deadline = time.time() + 10
        while bot._world_state["position"]["y"] == 0 and time.time() < deadline:
            time.sleep(0.1)
        start = dict(bot._world_state["position"])
        target_x = start["x"] + 1.0
        before = _rcon_command(
            args.host, args.rcon_port, password,
            f"data get entity {args.username} Pos",
        )
        bot._executor._execute({
            "action": "move", "x": target_x, "y": start["y"], "z": start["z"]
        })
        time.sleep(1)
        after = _rcon_command(
            args.host, args.rcon_port, password,
            f"data get entity {args.username} Pos",
        )
        if f"{target_x}d" not in after:
            raise AssertionError(f"server rejected target x={target_x}: {after}")
        print(f"Movement accepted: x {start['x']} -> {target_x}")
        print(f"Before: {before}")
        print(f"After:  {after}")
    finally:
        bot._connection.disconnect()


if __name__ == "__main__":
    main()
