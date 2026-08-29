"""Run a copied single-player world and AMP through one local workflow."""

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen

DATA_ROOT = Path(
    os.getenv("AMP_DATA_DIR", Path.home() / ".amp")
).expanduser().resolve()
MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"


def offline_player_uuid(username):
    digest = bytearray(hashlib.md5(f"OfflinePlayer:{username}".encode()).digest())
    digest[6] = (digest[6] & 0x0F) | 0x30
    digest[8] = (digest[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(digest)))


def world_profile(source, version, temp_root=None):
    normalized = os.path.normcase(str(source.resolve()))
    if sys.platform == "darwin":
        # normcase folds case for Windows only, but macOS formats its volumes
        # case-insensitively by default, so two spellings name one world.
        normalized = normalized.casefold()
    identity = hashlib.sha256(normalized.encode()).hexdigest()[:10]
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", source.name).strip("-.") or "world"
    root = temp_root or DATA_ROOT
    return root / f"local-world-{slug}-{identity}-{version}"


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


def has_operators(path):
    if not path.exists():
        return False
    operators = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(operators, list):
        raise ValueError(f"Expected an operator list in {path}")
    return bool(operators)


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
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(".jar.part")
    partial.write_bytes(payload)
    partial.replace(destination)


def server_java_feature(server_jar):
    with zipfile.ZipFile(server_jar) as archive:
        class_file = archive.read("net/minecraft/bundler/Main.class")
    if class_file[:4] != b"\xca\xfe\xba\xbe" or len(class_file) < 8:
        raise ValueError(f"Invalid Minecraft server JAR: {server_jar}")
    class_version = int.from_bytes(class_file[6:8], "big")
    return class_version - 44


def installed_java_feature(java):
    result = subprocess.run(
        [java, "-version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        text=True,
    )
    match = re.search(r'(?:java|openjdk) version "?(?:1\.)?(\d+)', result.stdout)
    if result.returncode or match is None:
        raise ValueError(f"Could not determine the Java version from: {java}")
    return int(match.group(1))


def validate_server_java(java, server_jar, minecraft_version):
    required = server_java_feature(server_jar)
    installed = installed_java_feature(java)
    if installed < required:
        raise ValueError(
            f"Minecraft {minecraft_version} requires Java {required} or newer; "
            f"found Java {installed}. Install a compatible JDK, then pass its "
            "java executable with --java or set AMP_JAVA_PATH."
        )


def port_is_open(port):
    with socket.socket() as connection:
        connection.settimeout(0.25)
        return connection.connect_ex(("127.0.0.1", port)) == 0


def ask_yes_no(prompt, default, input_fn=input):
    marker = "Y/n" if default else "y/N"
    while True:
        answer = input_fn(f"{prompt} [{marker}]: ").strip().lower()
        if not answer:
            return default
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("Enter y or n.")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world", default=os.getenv("AMP_WORLD_PATH"))
    parser.add_argument("--version", default=os.getenv("AMP_MC_VERSION", "26.2"))
    parser.add_argument("--port", type=int, default=int(os.getenv("AMP_SERVER_PORT", "25565")))
    parser.add_argument("--username", default=os.getenv("AMP_BOT_USERNAME", "AMP"))
    parser.add_argument("--operator", default=os.getenv("AMP_OPERATOR_USERNAME"))
    parser.add_argument(
        "--amp-game-mode", choices=("survival", "creative"),
        default=os.getenv("AMP_BOT_GAME_MODE"),
    )
    parser.add_argument(
        "--mode", choices=("guided", "autonomous", "idle"),
        default=os.getenv("AMP_MODE"),
    )
    parser.add_argument("--java", default=os.getenv("AMP_JAVA_PATH", "java"))
    parser.add_argument("--accept-eula", action="store_true")
    parser.add_argument("--refresh-world-copy", action="store_true")
    parser.add_argument("--copy-back", action="store_true")
    parser.add_argument("--non-interactive", action="store_true")
    return parser.parse_args(argv)


def resolve_startup(args, input_fn=input, output_fn=print):
    from amp.version_support import runnable_version_protocols

    interactive = not args.non_interactive
    if not args.world and interactive:
        args.world = input_fn("Minecraft world path: ").strip()
    if not args.world:
        raise ValueError("Supply --world or set AMP_WORLD_PATH")
    source = Path(args.world).expanduser().resolve()
    if not (source / "level.dat").is_file():
        raise ValueError(f"Not a Minecraft world: {source}")
    if shutil.which(args.java) is None and not Path(args.java).is_file():
        raise ValueError(f"Java executable not found: {args.java}")
    if not 1024 <= args.port <= 65535:
        raise ValueError("Port must be between 1024 and 65535")
    supported_versions = runnable_version_protocols()
    if args.version not in supported_versions:
        supported = ", ".join(sorted(supported_versions))
        raise ValueError(
            f"Unsupported Minecraft version: {args.version}; choose {supported}"
        )

    run_root = world_profile(source, args.version)
    eula_file = run_root / "eula.txt"
    eula_accepted = eula_file.exists() and "eula=true" in eula_file.read_text(
        encoding="ascii", errors="ignore"
    ).lower()
    if not eula_accepted and not args.accept_eula:
        if not interactive or not ask_yes_no(
            "Have you read and accepted https://aka.ms/MinecraftEULA?",
            False,
            input_fn,
        ):
            raise ValueError("Minecraft EULA acceptance is required for first setup")
        args.accept_eula = True

    operators_file = run_root / "ops.json"
    if args.operator is None and interactive and not has_operators(operators_file):
        if ask_yes_no(
            "Allow operator commands/cheats for your human player?",
            False,
            input_fn,
        ):
            args.operator = input_fn(
                "Exact in-game username to make operator: "
            ).strip()
            if not args.operator:
                raise ValueError("The operator username cannot be blank")
            output_fn("The username is case-sensitive and must match in-game exactly.")

    if args.mode is None:
        if interactive:
            mode = input_fn(
                "AMP mode ([G]uided/[a]utonomous/[i]dle): "
            ).strip().lower()
            args.mode = {
                "": "guided", "g": "guided", "a": "autonomous", "i": "idle",
            }.get(mode, mode)
        else:
            args.mode = "guided"
    if args.mode not in {"guided", "autonomous", "idle"}:
        raise ValueError("AMP mode must be guided, autonomous, or idle")

    if args.amp_game_mode is None:
        if interactive:
            game_mode = input_fn(
                "AMP gameplay mode ([S]urvival/[c]reative): "
            ).strip().lower()
            args.amp_game_mode = {
                "": "survival", "s": "survival", "c": "creative",
            }.get(game_mode, game_mode)
        else:
            args.amp_game_mode = "survival"
    if args.amp_game_mode not in {"survival", "creative"}:
        raise ValueError("AMP gameplay mode must be survival or creative")

    output_fn(f"Source world:      {source}")
    output_fn(f"Active world copy: {run_root / 'world'}")
    return source, run_root


def validate_model_configuration(mode):
    if mode == "idle":
        return
    from dotenv import find_dotenv, load_dotenv
    from amp.model_clients import build_model_client

    load_dotenv(find_dotenv(usecwd=True))
    try:
        client = build_model_client(os.environ)
    except ValueError as error:
        raise ValueError(f"Model configuration is invalid: {error}") from error
    if client is None:
        raise ValueError(
            "Guided and autonomous modes require a model provider. Configure "
            "ANTHROPIC_API_KEY, or configure AMP_MODEL_PROVIDER=openai-compatible "
            "with OPENAI_BASE_URL and OPENAI_MODEL. Use --mode idle only for a "
            "connection test."
        )


def prepare_server(args, source, run_root):
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
        print(f"Reusing server world copy: {server_world}")

    server_jar = DATA_ROOT / "server-jars" / args.version / "server.jar"
    if not server_jar.exists():
        print(f"Downloading and verifying Minecraft {args.version} server...")
        download_server(args.version, server_jar)
    validate_server_java(args.java, server_jar, args.version)
    (run_root / "eula.txt").write_text("eula=true\n", encoding="ascii")
    update_properties(run_root / "server.properties", {
        "server-port": args.port,
        "server-ip": "127.0.0.1",
        "level-name": "world",
        "online-mode": "false",
        "enforce-secure-profile": "false",
        "motd": "AMP local world",
        "spawn-protection": 0,
        "allow-flight": "true",
    })
    if args.operator:
        add_operator(run_root / "ops.json", args.operator)
    return server_jar


def copy_world_back(server_world, source, now=None):
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    backup = source.with_name(f"{source.name}.amp-backup-{stamp}")
    staging = source.with_name(f".{source.name}.amp-copyback-{stamp}")
    if backup.exists() or staging.exists():
        raise FileExistsError("A copy-back backup or staging path already exists")
    shutil.copytree(server_world, staging)
    source.rename(backup)
    try:
        staging.rename(source)
    except Exception:
        backup.rename(source)
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return backup


def run_amp(args):
    from amp.bot import Bot
    from amp.cli import autonomous_loop, guided_loop

    bot = Bot({
        "host": "127.0.0.1", "port": args.port,
        "username": args.username, "version": args.version,
        "game_mode": args.amp_game_mode,
        "model_optional": args.mode == "idle",
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


def stop_server(server):
    if server.poll() is not None:
        return
    print("Stopping and saving the local Minecraft server...")
    try:
        server.stdin.write("stop\n")
        server.stdin.flush()
        server.wait(timeout=30)
    except subprocess.TimeoutExpired:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()


def main(argv=None, input_fn=input):
    args = parse_args(argv)
    source, run_root = resolve_startup(args, input_fn)
    validate_model_configuration(args.mode)
    if port_is_open(args.port):
        raise SystemExit(f"Port {args.port} is already in use")
    server_jar = prepare_server(args, source, run_root)
    stdout = (run_root / "server-console.log").open("w", encoding="utf-8")
    stderr = (run_root / "server-error.log").open("w", encoding="utf-8")
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    server = subprocess.Popen(
        [args.java, "-Xms1G", "-Xmx2G", "-jar", str(server_jar), "nogui"],
        cwd=run_root, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
        creationflags=creationflags, text=True,
    )
    ready = False
    run_error = None
    try:
        deadline = time.monotonic() + 180
        while not port_is_open(args.port):
            if server.poll() is not None:
                raise RuntimeError(
                    "Minecraft server exited; inspect "
                    f"{stdout.name} and {stderr.name}"
                )
            if time.monotonic() >= deadline:
                raise TimeoutError("Minecraft server did not start within 3 minutes")
            time.sleep(2)
        ready = True
        print(f"Join localhost:{args.port} with Minecraft {args.version}.")
        run_amp(args)
    except Exception as error:
        run_error = error
    finally:
        stop_server(server)
        stdout.close()
        stderr.close()

    should_copy = args.copy_back
    if ready and not args.non_interactive and not should_copy:
        should_copy = ask_yes_no(
            "Copy the played server world back over the source world?",
            True,
            input_fn,
        )
    if should_copy:
        backup = copy_world_back(run_root / "world", source)
        print(f"World copied back. Previous source preserved at: {backup}")
    if run_error is not None:
        raise run_error


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError) as error:
        raise SystemExit(f"Error: {error}") from error
