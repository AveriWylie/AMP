# Run AMP in a local Minecraft world

AMP cannot connect directly to a single-player **Open to LAN** session because
the integrated Java server requires a Microsoft-authenticated session. AMP 1.0
intentionally disables that authentication path pending approval from Minecraft
Services.

The local-world runner uses the practical alternative: it copies a single-player
save, runs the copy in the official dedicated Java server with authentication
disabled, and connects both the human player and AMP to that server. The
original save is never opened or modified by the script.

## One-command setup

Requirements:

- Minecraft Java Edition and a local world created with a version supported by
  AMP
- Python 3.10 or later with AMP's dependencies installed
- A JDK compatible with that Minecraft server release
- Java available on `PATH`, or its path supplied with `--java`

Close the world in Minecraft before copying it. From the AMP repository, run
the command for your shell.

Windows PowerShell:

```powershell
python tools\run_local_world.py `
    --world "$env:APPDATA\.minecraft\saves\My World" `
    --version 26.2 --port 25576 `
    --operator YourMinecraftName --accept-eula
```

Linux:

```bash
python tools/run_local_world.py \
    --world "$HOME/.minecraft/saves/My World" \
    --version 26.2 \
    --port 25576 \
    --operator YourMinecraftName \
    --accept-eula
```

`--accept-eula` confirms that the operator has read and accepts the
[Minecraft EULA](https://aka.ms/MinecraftEULA). The runner will not write
`eula=true` without that explicit flag.

The first run downloads the matching official server JAR from Mojang, verifies
its published SHA-1 digest, and copies the save. When the console says AMP is
online:

1. Launch the same Minecraft Java version.
2. Select **Multiplayer**, then **Direct Connection**.
3. Join `localhost:25576`.
4. AMP appears as a player named `AMP`.
5. Enter instructions in the runner terminal.
6. Enter `quit` to disconnect AMP and stop the dedicated server.

Later runs reuse the dedicated-server copy, including changes made while playing
there. To discard that copy and recopy the original save, close Minecraft and
add `--refresh-world-copy`. This permanently removes only the copy under AMP's
ignored `.tmp` directory; it does not remove the source save.

## Environment variables

Parameters override these environment variables when both are supplied.

| Variable | Default | Purpose |
| --- | --- | --- |
| `AMP_WORLD_PATH` | none | Source Java save path |
| `AMP_MC_VERSION` | `26.2` | Supported Minecraft Java version |
| `AMP_SERVER_PORT` | `25565` | Local dedicated-server port |
| `AMP_BOT_USERNAME` | `AMP` | Name displayed above the bot |
| `AMP_GAME_MODE` | `survival` | `survival` or `creative` |
| `AMP_MODE` | `guided` | `guided`, `autonomous`, or `idle` |
| `AMP_OPERATOR_USERNAME` | none | Human player granted commands |
| `AMP_JAVA_PATH` | `java` on `PATH` | Compatible Java executable |

For example, a reusable PowerShell configuration is:

```powershell
$env:AMP_WORLD_PATH = "$env:APPDATA\.minecraft\saves\My World"
$env:AMP_MC_VERSION = "26.2"
$env:AMP_SERVER_PORT = "25576"
$env:AMP_BOT_USERNAME = "AMP"
$env:AMP_OPERATOR_USERNAME = "YourMinecraftName"
$env:AMP_MODE = "guided"
$env:AMP_JAVA_PATH = "C:\Program Files\Java\jdk-25\bin\java.exe"
python tools\run_local_world.py --accept-eula
```

The equivalent Bash configuration is:

```bash
export AMP_WORLD_PATH="$HOME/.minecraft/saves/My World"
export AMP_MC_VERSION=26.2
export AMP_SERVER_PORT=25576
export AMP_BOT_USERNAME=AMP
export AMP_OPERATOR_USERNAME=YourMinecraftName
export AMP_MODE=guided
export AMP_JAVA_PATH=/usr/lib/jvm/jdk-25/bin/java
python tools/run_local_world.py --accept-eula
```

Guided mode is the default and requires a configured model provider. Autonomous
mode accepts a high-level goal and replans between action batches. Idle mode is
only a connection smoke test and does not require a model provider:

```bash
python tools/run_local_world.py --mode idle --world PATH --accept-eula
```

## What the runner does

The runner performs the following sequence:

1. Validates that the selected source contains `level.dat`.
2. Copies it to `.tmp/local-world-<version>/world` if no server copy exists.
3. Downloads and verifies the official server JAR when absent.
4. Writes `eula=true` after explicit acceptance.
5. Updates only AMP-managed properties and preserves other server settings.
6. Adds the optional human username to `ops.json` with permission level 4.
7. Starts the server, connects AMP, and enters the selected planning mode.
8. On exit or failure, disconnects AMP and asks the server to save and stop.

## Security and data boundaries

An offline-mode server does not verify player identities. The runners bind it to
`127.0.0.1`, so only clients on the same computer can reach it. Do not change
that binding or expose the port to the internet: anyone who can reach an
offline-mode server can choose another player's username.

The server copy becomes a separate world after its first run. Changes are not
synchronized back into the original single-player save. Back up any world that
matters before moving or merging save directories manually.
