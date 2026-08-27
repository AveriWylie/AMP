"""Run a copied single-player world and AMP through one local workflow."""

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.request import urlopen

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"


def offline_player_uuid(username):
    digest = bytearray(hashlib.md5(f"OfflinePlayer:{username}".encode()).digest())
    digest[6] = (digest[6] & 0x0F) | 0x30
    digest[8] = (digest[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(digest)))


def update_properties(path, settings):
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    output = []
    written = set()
    for line in lines:
        key = line.split("=", 1)[0] if "=" in line and not line.startswith("#") else None
        if key not in settings:
            output.append(line)
        elif key not in written:
            output.append(f"{key}={settings[key]}")
            written.add(key)
    output.extend(
        f"{key}={value}" for key, value in settings.items() if key not in written
    )
    path.write_text("\n".join(output) + "\n", encoding="utf-8")


def add_operator(path, username):
    operators = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    if not isinstance(operators, list):
        raise ValueError(f"Expected an operator list in {path}")
    player_uuid = offline_player_uuid(username)
    operators = [
        operator for operator in operators
        if operator.get("uuid") != player_uuid
        and operator.get("name", "").casefold() != username.casefold()
    ]
    operators.append({
        "uuid": player_uuid,
        "name": username,
        "level": 4,
        "bypassesPlayerLimit": False,
    })
    path.write_text(json.dumps(operators, indent=2) + "\n", encoding="utf-8")


def read_json(url):
    with urlopen(url, timeout=30) as response:
        return json.load(response)


def download_server(version, destination):
    manifest = read_json(MANIFEST_URL)
    release = next((item for item in manifest["versions"] if item["id"] == version), None)
    if release is None:
        raise RuntimeError(f"Minecraft version {version} is absent from Mojang's manifest")
    download = read_json(release["url"]).get("downloads", {}).get("server")
    if download is None:
        raise RuntimeError(f"Mojang does not publish a server JAR for {version}")
    with urlopen(download["url"], timeout=120) as response:
        payload = response.read()
    if hashlib.sha1(payload).hexdigest() != download["sha1"].lower():
        raise RuntimeError("The server JAR failed Mojang's SHA-1 integrity check")
    partial = destination.with_suffix(".jar.part")
    partial.write_bytes(payload)
    partial.replace(destination)


def port_is_open(port):
    with socket.socket() as connection:
        connection.settimeout(0.25)
        return connection.connect_ex(("127.0.0.1", port)) == 0


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world", default=os.getenv("AMP_WORLD_PATH"))
    parser.add_argument("--version", default=os.getenv("AMP_MC_VERSION", "26.2"))
    parser.add_argument("--port", type=int, default=int(os.getenv("AMP_SERVER_PORT", "25565")))
    parser.add_argument("--username", default=os.getenv("AMP_BOT_USERNAME", "AMP"))
    parser.add_argument("--operator", default=os.getenv("AMP_OPERATOR_USERNAME"))
    parser.add_argument(
        "--game-mode", choices=("survival", "creative"),
        default=os.getenv("AMP_GAME_MODE", "survival"),
    )
    parser.add_argument(
        "--mode", choices=("guided", "autonomous", "idle"),
        default=os.getenv("AMP_MODE", "guided"),
    )
    parser.add_argument("--java", default=os.getenv("AMP_JAVA_PATH", "java"))
    parser.add_argument("--accept-eula", action="store_true")
    parser.add_argument("--refresh-world-copy", action="store_true")
    return parser.parse_args()


def prepare_server(args):
    if not args.world:
        raise ValueError("Supply --world or set AMP_WORLD_PATH")
    if not args.accept_eula:
        raise ValueError(
            "Read https://aka.ms/MinecraftEULA, then use --accept-eula if you agree"
        )
    source = Path(args.world).expanduser().resolve()
    if not (source / "level.dat").is_file():
        raise ValueError(f"Not a Minecraft world: {source}")
    if shutil.which(args.java) is None and not Path(args.java).is_file():
        raise ValueError(f"Java executable not found: {args.java}")
    if not 1024 <= args.port <= 65535:
        raise ValueError("Port must be between 1024 and 65535")

    run_root = REPO_ROOT / ".tmp" / f"local-world-{args.version}"
    server_world = run_root / "world"
    run_root.mkdir(parents=True, exist_ok=True)
    if args.refresh_world_copy and server_world.exists():
        if not server_world.resolve().is_relative_to(run_root.resolve()):
            raise ValueError("Refusing to remove a world outside the run directory")
        shutil.rmtree(server_world)
    if not server_world.exists():
        print("Copying the world. The original save will not be modified...")
        shutil.copytree(source, server_world)
    else:
        print(f"Reusing server world copy at {server_world}")

    server_jar = run_root / "server.jar"
    if not server_jar.exists():
        print(f"Downloading and verifying Minecraft {args.version} server...")
        download_server(args.version, server_jar)
    (run_root / "eula.txt").write_text("eula=true\n", encoding="ascii")
    update_properties(run_root / "server.properties", {
        "server-port": args.port,
        "server-ip": "127.0.0.1",
        "level-name": "world",
        "online-mode": "false",
        "enforce-secure-profile": "false",
        "motd": "AMP local world",
        "spawn-protection": 0,
    })
    if args.operator:
        add_operator(run_root / "ops.json", args.operator)
    return run_root, server_jar


def run_amp(args):
    from bot import Bot
    from cli import autonomous_loop, guided_loop

    bot = Bot({
        "host": "127.0.0.1", "port": args.port,
        "username": args.username, "version": args.version,
        "game_mode": args.game_mode, "behavior_mode": "passive",
    })
    bot.start()
    if not bot._connection._connected:
        raise RuntimeError("AMP did not connect to the server")
    try:
        if args.mode == "idle":
            print(f"{args.username} is online. Press Ctrl+C to disconnect.")
            while True:
                time.sleep(3600)
        bot.set_mode(args.mode)
        (guided_loop if args.mode == "guided" else autonomous_loop)(bot)
    except KeyboardInterrupt:
        print("\nDisconnecting AMP...")
    finally:
        bot.disconnect()


def main():
    args = parse_args()
    run_root, server_jar = prepare_server(args)
    if port_is_open(args.port):
        raise SystemExit(f"Port {args.port} is already in use")
    stdout = (run_root / "server-console.log").open("w", encoding="utf-8")
    stderr = (run_root / "server-error.log").open("w", encoding="utf-8")
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    server = subprocess.Popen(
        [args.java, "-Xms1G", "-Xmx2G", "-jar", server_jar.name, "nogui"],
        cwd=run_root, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
        creationflags=creationflags, text=True,
    )
    try:
        deadline = time.monotonic() + 180
        while not port_is_open(args.port):
            if server.poll() is not None:
                raise RuntimeError(f"Minecraft server exited; inspect {stdout.name}")
            if time.monotonic() >= deadline:
                raise TimeoutError("Minecraft server did not start within 3 minutes")
            time.sleep(2)
        print(f"Join localhost:{args.port} with Minecraft {args.version}.")
        run_amp(args)
    finally:
        if server.poll() is None:
            print("Stopping the local Minecraft server...")
            try:
                server.stdin.write("stop\n")
                server.stdin.flush()
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait()
        stdout.close()
        stderr.close()


if __name__ == "__main__":
    main()
