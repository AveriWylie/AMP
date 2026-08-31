"""
--------------------------------------------------------------------------------------------
Local World Module - Copied-world runner
--------------------------------------------------------------------------------------------
Runs a copied single-player world and AMP through one local workflow. Backs the amp-world
command, and owns everything Bot deliberately does not, downloading and verifying a server
JAR, copying the save, writing server configuration, starting Java, running AMP against it,
shutting down cleanly and optionally copying the played world back.

The central rule is that the source world is never touched while playing. AMP plays a copy,
under its own profile directory, and the original is only written at the very end and only
after a backup exists. A crashed session therefore costs nothing.

This exists because AMP cannot join an Open to LAN session, the integrated server requires a
Microsoft-authenticated session, so playing a single-player save means hosting it as a
dedicated server first.

Everything here is process and filesystem work, no protocol knowledge. Bot is only constructed
near the end, in run_amp, once there is a server to talk to.
--------------------------------------------------------------------------------------------
"""

# imports
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

# global constants
# Everything AMP writes lives under one root, world copies and downloaded server JARs, so a
# full reset is deleting one directory. Overridable for tests and for anyone who does not want
# multi-gigabyte world copies in their home directory.
DATA_ROOT = Path(
    os.getenv("AMP_DATA_DIR", Path.home() / ".amp")
).expanduser().resolve()
MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"


"""
--------------------------------------------------------------------------------------------
Function Field Header - Identity helpers
--------------------------------------------------------------------------------------------
offline_player_uuid reproduces vanilla's offline-mode identity exactly, an MD5 of
"OfflinePlayer:<name>" with the version and variant bits forced to make it a valid UUID
version 3. It has to match byte for byte, because this is the ID the server will already have
recorded for that username in the world being copied. Getting it wrong would create a second
player rather than recognising the existing one, which matters for the operator list.

MD5 is not a security choice here, it is the algorithm vanilla uses, so it is the only one
that produces the right answer.

world_profile names the directory a copied world lives in. The hash makes two worlds with the
same folder name distinct, and the version is in the name because the same save opened under a
different Minecraft version needs its own copy rather than being upgraded in place.
--------------------------------------------------------------------------------------------
"""
def offline_player_uuid(username):
    digest = bytearray(hashlib.md5(f"OfflinePlayer:{username}".encode()).digest())
    # forced to UUID version 3 and RFC 4122 variant, matching vanilla exactly
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


"""
--------------------------------------------------------------------------------------------
Function Field Header - Server configuration files
--------------------------------------------------------------------------------------------
Editing the two files a dedicated server keeps its settings in, without destroying what is
already there.

update_properties rewrites server.properties in place. It preserves unmanaged keys, comments
and ordering rather than writing a fresh file, so anything the operator set by hand survives
an AMP run. Managed keys are replaced where they already appear, keeping their position, and
only appended when genuinely absent. The written set means a duplicated key collapses to one
rather than being replaced twice.

add_operator writes ops.json. It removes any existing entry for the same UUID or name before
appending, so repeated runs cannot stack duplicates, and it matches on both because a name can
be re-cased. Level 4 is full operator, which is what makes the human player's cheats work.

has_operators is the question resolve_startup asks before prompting, so someone who already
granted themselves operator is not asked again on every launch.
--------------------------------------------------------------------------------------------
"""
def update_properties(path, settings):
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    output = []
    written = set()

    # rewrite managed keys in place, keep everything else exactly as it was
    for line in lines:
        key = line.split("=", 1)[0] if "=" in line and not line.startswith("#") else None

        if key not in settings:
            output.append(line)
        # only the first occurrence is written, a duplicated key collapses to one
        elif key not in written:
            output.append(f"{key}={settings[key]}")
            written.add(key)

    # anything the file never mentioned gets appended
    output.extend(f"{key}={value}" for key, value in settings.items() if key not in written)
    path.write_text("\n".join(output) + "\n", encoding="utf-8")


def add_operator(path, username):
    operators = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []

    if not isinstance(operators, list):
        raise ValueError(f"Expected an operator list in {path}")

    player_uuid = offline_player_uuid(username)

    # drop any prior entry for this player, matched by UUID or by name, so runs do not stack
    operators = [
        operator for operator in operators
        if operator.get("uuid") != player_uuid
        and operator.get("name", "").casefold() != username.casefold()
    ]

    # level 4 is full operator, which is what makes the human player's cheats work
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


"""
--------------------------------------------------------------------------------------------
Function Field Header - Server JAR acquisition
--------------------------------------------------------------------------------------------
Fetches the official server JAR for a version, straight from Mojang's manifest, never from a
mirror. Two hops, the manifest lists every version with a URL to its own metadata, and that
metadata carries the download URL and the expected hash.

The SHA-1 check is not optional. This downloads an executable that is then run as a
subprocess, so verifying it against Mojang's published digest is the difference between
running their server and running whatever answered the request. SHA-1 is weak in general but
it is what Mojang publishes, so it is what can be checked.

The download is written to a .part file and renamed only after it verifies, which makes the
final path atomic. An interrupted download therefore leaves a partial file that is never
mistaken for a complete one, so the next run redownloads instead of trying to run a truncated
JAR.

The longer timeout on the payload is because the JAR is tens of megabytes while the metadata
is a few kilobytes.
--------------------------------------------------------------------------------------------
"""
def read_json(url):
    with urlopen(url, timeout=30) as response:
        return json.load(response)


def download_server(version, destination):
    manifest = read_json(MANIFEST_URL)
    release = next((item for item in manifest["versions"] if item["id"] == version), None)

    if release is None:
        raise RuntimeError(f"Minecraft version {version} is absent from Mojang's manifest")

    # second hop, the per-version metadata is what carries the URL and the hash
    download = read_json(release["url"]).get("downloads", {}).get("server")

    if download is None:
        raise RuntimeError(f"Mojang does not publish a server JAR for {version}")

    # longer timeout, this is the JAR itself rather than a few kilobytes of metadata
    with urlopen(download["url"], timeout=120) as response:
        payload = response.read()

    # this gets run as a subprocess, so it is verified against Mojang's own digest first
    if hashlib.sha1(payload).hexdigest() != download["sha1"].lower():
        raise RuntimeError("The server JAR failed Mojang's SHA-1 integrity check")

    destination.parent.mkdir(parents=True, exist_ok=True)
    # written aside and renamed only after verifying, so a partial download is never runnable
    partial = destination.with_suffix(".jar.part")
    partial.write_bytes(payload)
    partial.replace(destination)


"""
--------------------------------------------------------------------------------------------
Function Field Header - Java version compatibility
--------------------------------------------------------------------------------------------
Checks the installed Java is new enough for the server JAR before starting it, because a
mismatch otherwise surfaces as UnsupportedClassVersionError buried in a server log, minutes
after launch, which tells a user nothing useful.

The requirement is read out of the JAR rather than kept in a table. Every .class file carries
the bytecode version it was compiled for in bytes 6 and 7, right after the 0xCAFEBABE magic,
and that number is 44 plus the Java feature release, so Java 21 is 65. Reading it means a new
Minecraft release needs no update here, the JAR states its own requirement.

The installed version comes from parsing java -version, which prints to stderr rather than
stdout, hence the redirect. The regex tolerates both "java version" and "openjdk version",
and the optional 1. prefix handles the old 1.8 style numbering.

check=False because a failed run is handled through the message rather than an exception, the
returncode and a failed match produce the same actionable error.
--------------------------------------------------------------------------------------------
"""
def server_java_feature(server_jar):
    with zipfile.ZipFile(server_jar) as archive:
        class_file = archive.read("net/minecraft/bundler/Main.class")

    # 0xCAFEBABE is the class file magic, anything else is not a JAR worth running
    if class_file[:4] != b"\xca\xfe\xba\xbe" or len(class_file) < 8:
        raise ValueError(f"Invalid Minecraft server JAR: {server_jar}")

    # bytecode version sits at bytes 6-7, and is 44 plus the Java feature release
    class_version = int.from_bytes(class_file[6:8], "big")
    return class_version - 44


def installed_java_feature(java):
    # -version prints to stderr, so it is redirected into stdout to be read
    result = subprocess.run(
        [java, "-version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        text=True,
    )

    # matches both "java version" and "openjdk version", the 1. prefix covers old 1.8 numbering
    match = re.search(r'(?:java|openjdk) version "?(?:1\.)?(\d+)', result.stdout)

    if result.returncode or match is None:
        raise ValueError(f"Could not determine the Java version from: {java}")

    return int(match.group(1))


def validate_server_java(java, server_jar, minecraft_version):
    required = server_java_feature(server_jar)
    installed = installed_java_feature(java)

    # caught here rather than as UnsupportedClassVersionError buried in a server log later
    if installed < required:
        raise ValueError(
            f"Minecraft {minecraft_version} requires Java {required} or newer; "
            f"found Java {installed}. Install a compatible JDK, then pass its "
            "java executable with --java or set AMP_JAVA_PATH."
        )


"""
--------------------------------------------------------------------------------------------
Function Field Header - Probe and prompt helpers
--------------------------------------------------------------------------------------------
port_is_open answers whether something is already listening, used both to refuse a launch that
would collide with an existing server and to detect when the one just started is ready.
connect_ex returns an error number rather than raising, so a refused connection is a value to
compare instead of an exception to catch, and the short timeout keeps the readiness poll
responsive.

ask_yes_no loops until the answer is one it understands. Every question it is used for changes
something on disk, accepting the EULA, granting operator, copying a world back over the
original, so treating an unrecognised answer as the default would decide something the user
did not. Pressing enter alone is the one shortcut, and the Y/n casing shows which way that
goes.

input_fn is injected rather than calling input directly, which is what lets the whole startup
sequence be tested without a terminal.
--------------------------------------------------------------------------------------------
"""
def port_is_open(port):
    with socket.socket() as connection:
        connection.settimeout(0.25)
        # connect_ex returns an error number instead of raising, so this is a comparison
        return connection.connect_ex(("127.0.0.1", port)) == 0


def ask_yes_no(prompt, default, input_fn=input):
    # casing shows which way a bare enter goes
    marker = "Y/n" if default else "y/N"

    # loops rather than defaulting, every caller is about to change something on disk
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
        raise ValueError(f"Unsupported Minecraft version: {args.version}; choose {supported}")

    run_root = world_profile(source, args.version)
    eula_file = run_root / "eula.txt"
    eula_accepted = eula_file.exists() and "eula=true" in eula_file.read_text(encoding="ascii", errors="ignore").lower()

    if not eula_accepted and not args.accept_eula:
        if not interactive or not ask_yes_no("Have you read and accepted https://aka.ms/MinecraftEULA?",False, input_fn):
            raise ValueError("Minecraft EULA acceptance is required for first setup")

        args.accept_eula = True

    operators_file = run_root / "ops.json"
    if args.operator is None and interactive and not has_operators(operators_file):
        if ask_yes_no("Allow operator commands/cheats for your human player?",False, input_fn):
            args.operator = input_fn("Exact in-game username to make operator: ").strip()

            if not args.operator:
                raise ValueError("The operator username cannot be blank")

            output_fn("The username is case-sensitive and must match in-game exactly.")

    if args.mode is None:
        if interactive:
            mode = input_fn("AMP mode ([G]uided/[a]utonomous/[i]dle): ").strip().lower()
            args.mode = {"": "guided", "g": "guided", "a": "autonomous", "i": "idle"}.get(mode, mode)
        else:
            args.mode = "guided"

    if args.mode not in {"guided", "autonomous", "idle"}:
        raise ValueError("AMP mode must be guided, autonomous, or idle")

    if args.amp_game_mode is None:
        if interactive:
            game_mode = input_fn("AMP gameplay mode ([S]urvival/[c]reative): ").strip().lower()
            args.amp_game_mode = {"": "survival", "s": "survival", "c": "creative",}.get(game_mode, game_mode)
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


"""
--------------------------------------------------------------------------------------------
Function Header - Copy back
--------------------------------------------------------------------------------------------
The only function that writes to the source world, and the one place a mistake would cost real
data, so it is ordered so no step destroys anything that is not already duplicated.

Copy the played world to a staging directory beside the source, rename the source aside as a
timestamped backup, then rename staging into place. Copy first and rename after, because a
rename is atomic and a copy is not, so the window where neither world exists never opens.

If the final rename fails, the backup is renamed straight back and staging is removed, so the
source is restored rather than left missing.

The backup is kept rather than deleted on success. Copy-back overwrites a save the player may
care about more than the session, so the previous state stays on disk and its path is returned
for the caller to report.

Staging is dot-prefixed to keep it out of Minecraft's world list if anything interrupts
mid-operation, and the collision check refuses to start rather than reusing a path that may
be a previous failed attempt.
--------------------------------------------------------------------------------------------
"""
def copy_world_back(server_world, source, now=None):
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    backup = source.with_name(f"{source.name}.amp-backup-{stamp}")
    # dot-prefixed so an interrupted copy-back does not show up in Minecraft's world list
    staging = source.with_name(f".{source.name}.amp-copyback-{stamp}")

    # refuse rather than reuse, an existing path here may be a previous failed attempt
    if backup.exists() or staging.exists():
        raise FileExistsError("A copy-back backup or staging path already exists")

    # copy first, then two atomic renames, so the source is never briefly absent
    shutil.copytree(server_world, staging)
    source.rename(backup)

    try:
        staging.rename(source)
    except Exception:
        # put the original back before surfacing the failure
        backup.rename(source)
        shutil.rmtree(staging, ignore_errors=True)
        raise

    return backup


"""
--------------------------------------------------------------------------------------------
Function Header - Run AMP
--------------------------------------------------------------------------------------------
Connects AMP to the server this module just started and hands control to the chosen mode.

Bot and the CLI loops are imported inside the function rather than at module scope, which
keeps the import graph acyclic, cli imports Bot and this is what cli's own entry point sits
beside, and it means amp-world --help does not pay for constructing the protocol stack.

Connects to 127.0.0.1 explicitly rather than localhost, so it cannot resolve to IPv6 while the
server listens on IPv4.

Idle mode sleeps instead of planning, which is the mode that needs no model provider, and is
what makes a pure connection test possible.

The finally disconnects on every path including Ctrl+C, so the server sees a clean client
departure and saves the player's state rather than timing the connection out.
--------------------------------------------------------------------------------------------
"""
def run_amp(args):
    # imported here to keep the import graph acyclic and --help cheap
    from amp.bot import Bot
    from amp.cli import autonomous_loop, guided_loop

    # explicit IPv4, localhost could resolve to IPv6 while the server listens on IPv4
    bot = Bot({
        "host": "127.0.0.1", "port": args.port,
        "username": args.username, "version": args.version,
        "game_mode": args.amp_game_mode,
        "model_optional": args.mode == "idle",
    })
    bot.start()

    if not bot.is_connected():
        raise RuntimeError("AMP did not connect to the server")

    try:
        # idle needs no provider, it just stays online so the connection can be observed
        if args.mode == "idle":
            print(f"{args.username} is online. Press Ctrl+C to disconnect.")

            while True:
                time.sleep(3600)

        bot.set_mode(args.mode)
        if args.mode == "guided":
            guided_loop(bot)
        else:
            autonomous_loop(bot)

    except KeyboardInterrupt:
        print("\nDisconnecting AMP...")

    # runs on Ctrl+C too, so the server saves player state instead of timing us out
    finally:
        bot.disconnect()


"""
--------------------------------------------------------------------------------------------
Function Header - Stop server
--------------------------------------------------------------------------------------------
Shuts the server down in escalating stages, and the order matters because the world is only
written to disk during a clean stop.

The stop command on stdin is the graceful path, it makes the server save chunks and player
data and exit on its own, which is the only ending that leaves a world worth copying back. It
gets 30 seconds, since saving a large world is not instant.

terminate is SIGTERM, a request the JVM can still act on, and kill is SIGKILL, which it
cannot. Reaching kill means the world on disk may be mid-save, so the earlier stages are worth
waiting for.

The early return covers a server that already exited on its own, where writing to stdin would
raise on a closed pipe.
--------------------------------------------------------------------------------------------
"""
def stop_server(server):
    # already gone, writing to its stdin would raise on a closed pipe
    if server.poll() is not None:
        return

    print("Stopping and saving the local Minecraft server...")

    # the graceful path, this is what makes the server flush the world to disk
    try:
        server.stdin.write("stop\n")
        server.stdin.flush()
        server.wait(timeout=30)
    except subprocess.TimeoutExpired:
        # SIGTERM, still a request the JVM can act on
        server.terminate()

        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            # SIGKILL, unignorable, so the world may be left mid-save
            server.kill()
            server.wait()


"""
--------------------------------------------------------------------------------------------
Function Header - Main
--------------------------------------------------------------------------------------------
The whole workflow end to end, and the ordering is what makes it safe to interrupt.

Everything that can be checked cheaply is checked before anything expensive happens.
Configuration is resolved, the model provider is validated and the port is tested before a
world is copied or a JVM is started, so a misconfigured run fails in a second rather than
after a multi-gigabyte copy.

Server output goes to log files rather than the terminal, because AMP's own prompts share that
terminal and interleaved server logging would make it unusable. The paths appear in the error
message when startup fails, which is when they are wanted.

Readiness is detected by polling the port rather than scraping the log for a "Done" line, so
it does not depend on log wording. The loop also watches for the process exiting, which is the
common failure and would otherwise wait out the full three minutes.

The try/finally around the session guarantees the server is stopped and the logs closed on
every path, including a failure inside AMP. The error is captured rather than raised
immediately so the copy-back question can still be asked, since a crash mid-session is exactly
when someone may still want the world that was played.

ready gates that question. If the server never started there is nothing to copy back, and
asking would risk overwriting a real save with an unplayed copy.
--------------------------------------------------------------------------------------------
"""
def main(argv=None, input_fn=input):
    args = parse_args(argv)
    source, run_root = resolve_startup(args, input_fn)
    validate_model_configuration(args.mode)

    # every cheap check happens before the expensive world copy and JVM start
    if port_is_open(args.port):
        raise SystemExit(f"Port {args.port} is already in use")

    server_jar = prepare_server(args, source, run_root)
    # server output goes to files, AMP's prompts own the terminal
    stdout = (run_root / "server-console.log").open("w", encoding="utf-8")
    stderr = (run_root / "server-error.log").open("w", encoding="utf-8")
    # keeps a console window from flashing up on Windows, no effect elsewhere
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
        # polls the port rather than scraping the log, so it does not depend on log wording
        while not port_is_open(args.port):
            # the common failure, caught here instead of waiting out the full timeout
            if server.poll() is not None:
                raise RuntimeError("Minecraft server exited; inspect {stdout.name} and {stderr.name}")

            if time.monotonic() >= deadline:
                raise TimeoutError("Minecraft server did not start within 3 minutes")

            time.sleep(2)

        ready = True
        print(f"Join localhost:{args.port} with Minecraft {args.version}.")
        run_amp(args)

    # held rather than raised, so copy-back can still be offered after a mid-session failure
    except Exception as error:
        run_error = error

    finally:
        stop_server(server)
        stdout.close()
        stderr.close()

    should_copy = args.copy_back

    # only offered when a session actually ran, otherwise this would overwrite a real save
    # with an unplayed copy. --copy-back skips the question for non-interactive runs.
    if ready and not args.non_interactive and not should_copy:
        should_copy = ask_yes_no("Copy the played server world back over the source world?",True,input_fn)

    if should_copy:
        backup = copy_world_back(run_root / "world", source)
        print(f"World copied back. Previous source preserved at: {backup}")

    # re-raised last, after the world was safely dealt with either way
    if run_error is not None:
        raise run_error


# Expected failures become a one-line message rather than a traceback, since a missing world
# path or an unusable Java install is a user problem, not a bug worth a stack trace. Anything
# not listed still raises normally, which is what keeps real bugs visible.
if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError) as error:
        raise SystemExit(f"Error: {error}") from error
