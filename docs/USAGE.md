# Run AMP in a local Minecraft world

AMP cannot connect directly to a single-player **Open to LAN** session because the integrated Java server requires a Microsoft-authenticated session. AMP 1.0 intentionally disables that authentication path pending approval from Minecraft Services.

The local-world runner uses the practical alternative: it copies a single-player save, runs the copy in the official dedicated Java server with authentication disabled, and connects both the human player and AMP to that server. The original save is never opened or modified by the script.

## One-command setup

Requirements:

- Minecraft Java Edition and a local world created with a version supported by AMP
- Python 3.10 or later with AMP's dependencies installed
- A JDK compatible with that Minecraft server release
- PowerShell 5.1 or later on Windows, or Bash on Linux

Close the world in Minecraft before copying it. From the AMP repository, run one of these commands.

Windows PowerShell:

```powershell
.\tools\run_local_world.ps1 -WorldPath "$env:APPDATA\.minecraft\saves\My World" -MinecraftVersion 26.2 -Port 25576 -AcceptEula
```

Linux:

```bash
./tools/run_local_world.sh --world "$HOME/.minecraft/saves/My World" --version 26.2 --port 25576 --accept-eula
```

`-AcceptEula` and `--accept-eula` confirm that the operator has read and accepts the [Minecraft EULA](https://aka.ms/MinecraftEULA). The runners will not write `eula=true` without that explicit flag.

The first run downloads the matching official server JAR from Mojang, verifies its published SHA-1 digest, and copies the save. When the console says AMP is online:

1. Launch the same Minecraft Java version.
2. Select **Multiplayer**, then **Direct Connection**.
3. Join `localhost:25576`.
4. AMP appears as a player named `AMP`.
5. Press Ctrl+C in the runner's terminal to disconnect AMP and stop the dedicated server.

Later runs reuse the dedicated-server copy, including changes made while playing there. To discard that copy and recopy the original save, close Minecraft and add `-RefreshWorldCopy` on Windows or `--refresh-world-copy` on Linux. This permanently removes only the copy under AMP's ignored `.tmp` directory; it does not remove the source save.

## Environment variables

Parameters override these environment variables when both are supplied.

| Variable | Default | Purpose |
| --- | --- | --- |
| `AMP_WORLD_PATH` | none | Full path to the source Java save; required unless `-WorldPath` is given |
| `AMP_MC_VERSION` | `26.2` | Supported Minecraft Java version |
| `AMP_SERVER_PORT` | `25565` | Local dedicated-server port |
| `AMP_BOT_USERNAME` | `AMP` | Name displayed above the bot |
| `AMP_GAME_MODE` | `survival` | AMP gameplay mode: `survival` or `creative` |
| `AMP_JAVA_PATH` | `java` on `PATH` | Full path to a compatible Java executable |
| `AMP_PYTHON_PATH` | `python` on `PATH` | Python executable containing AMP's dependencies |

For example, a reusable PowerShell configuration is:

```powershell
$env:AMP_WORLD_PATH = "$env:APPDATA\.minecraft\saves\My World"
$env:AMP_MC_VERSION = "26.2"
$env:AMP_SERVER_PORT = "25576"
$env:AMP_BOT_USERNAME = "AMP"
$env:AMP_JAVA_PATH = "C:\Program Files\Java\jdk-25\bin\java.exe"
.\tools\run_local_world.ps1 -AcceptEula
```

The equivalent Bash configuration is:

```bash
export AMP_WORLD_PATH="$HOME/.minecraft/saves/My World"
export AMP_MC_VERSION=26.2
export AMP_SERVER_PORT=25576
export AMP_BOT_USERNAME=AMP
export AMP_JAVA_PATH=/usr/lib/jvm/jdk-25/bin/java
./tools/run_local_world.sh --accept-eula
```

Model-provider variables such as `ANTHROPIC_API_KEY` are not required merely to connect AMP and see it in the world. They are required only for model-driven guided or autonomous planning. See the root README for provider setup.

## What the runner does

The runner performs the following sequence:

1. Validates that the selected source contains `level.dat`.
2. Copies it to `.tmp/local-world-<version>/world` if no server copy exists.
3. Downloads the official version-specific dedicated-server JAR when absent.
4. Writes `eula=true` after the operator explicitly runs the script.
5. Configures `online-mode=false` and `enforce-secure-profile=false` for local offline login.
6. Starts the server and waits for its TCP port to open.
7. Runs `tools/hold_bot.py`, which connects AMP and keeps it online.
8. On Ctrl+C or failure, disconnects AMP and stops only the server process started by this invocation.

## Security and data boundaries

An offline-mode server does not verify player identities. The runners bind it to `127.0.0.1`, so only clients on the same computer can reach it. Do not change that binding or expose the port to the internet: anyone who can reach an offline-mode server can choose another player's username.

The server copy becomes a separate world after its first run. Changes are not synchronized back into the original single-player save. Back up any world that matters before moving or merging save directories manually.
