# Run AMP in a local Minecraft world

AMP cannot connect directly to a single-player **Open to LAN** session because
the integrated Java server requires a Microsoft-authenticated session. AMP 1.0
does not advertise that authentication path pending approval from Minecraft
Services.

The local-world runner copies a single-player save, hosts the copy with Mojang's
dedicated Java server, and connects AMP. It prints the local server address for
the human player to join manually. The `amp-world` command owns setup, server
startup, interactive planning, saving, and shutdown.

## Requirements

- Minecraft Java Edition and a local world created with a supported version
- Python 3.10 or later with AMP's dependencies installed
- A JDK compatible with the selected Minecraft server release
- Java available on `PATH`, or its path supplied with `--java`
- A configured model provider for guided or autonomous mode

Close the world in Minecraft before starting AMP.

Windows PowerShell:

```powershell
amp-world `
    --world "$env:APPDATA\.minecraft\saves\My World" `
    --version 26.2 --port 25576
```

macOS:

```bash
amp-world \
    --world "$HOME/Library/Application Support/minecraft/saves/My World" \
    --version 26.2 --port 25576
```

Linux:

```bash
amp-world \
    --world "$HOME/.minecraft/saves/My World" \
    --version 26.2 --port 25576
```

## First startup

The runner asks the following questions when their answers are not already
configured:

1. Accept the [Minecraft EULA](https://aka.ms/MinecraftEULA). This is asked only
   when creating a world profile. Later runs reuse its `eula=true` file.
2. Choose whether to allow operator commands for the human player. This is the
   dedicated-server equivalent of enabling commands or cheats in Minecraft.
3. If enabled, enter the human player's exact in-game username. Offline-mode
   identity is case-sensitive, so spelling and capitalization must match.
4. Select AMP's mode. Press Enter for the default, `guided`.
5. Select AMP's gameplay mode. Press Enter for the default, `survival`.

After the operator question, pressing Enter twice accepts the two AMP defaults
and starts the server. The runner prints both paths before it changes anything:

```text
Source world:      .../.minecraft/saves/My World
Active world copy: ~/.amp/local-world-My-World-<identity>-26.2/world
```

When AMP connects:

1. Launch the same Minecraft Java version.
2. Select **Multiplayer**, then **Direct Connection**.
3. Join `localhost:25576`.
4. Enter guided instructions in the runner terminal.
5. Enter `quit` to disconnect AMP and stop the server.

Autonomous mode asks for a high-level goal instead. Idle mode only connects AMP
for a smoke test and does not require a model provider.

## Shutdown and copy-back

AMP first disconnects and asks the Java server to save every dimension and stop.
The runner then asks:

```text
Copy the played server world back over the source world? [Y/n]:
```

Yes is the default. Before replacing the source, AMP preserves it beside the
save as a timestamped backup:

```text
My World.amp-backup-20260827-120000
```

The server copy also remains under AMP's data directory, `~/.amp` by default.
If copying or renaming fails, AMP restores the original source path instead of
leaving it missing.

## World isolation and reuse

Each source path and Minecraft version gets a distinct profile. Two 26.2 worlds
therefore cannot reuse each other's server copy. Later runs of the same source
and version reuse its active copy, operator list, EULA acceptance, and advanced
server properties.

Use `--refresh-world-copy` to discard the active copy and import the current
source again. This does not delete the source world.

## Model configuration

Guided and autonomous modes validate the model configuration before copying the
world, downloading Java server data, or starting Java. Missing configuration
produces an actionable startup error. Provider variables are documented in the
root README.

Use idle mode when only testing the Minecraft connection:

```bash
amp-world --world PATH --mode idle
```

## Environment variables

Command-line arguments override environment variables.

| Variable                | Default  | Purpose                           |
|-------------------------|----------|-----------------------------------|
| `AMP_WORLD_PATH`        | none     | Source Java save path             |
| `AMP_MC_VERSION`        | `26.2`   | Minecraft Java version            |
| `AMP_SERVER_PORT`       | `25565`  | Local server port                 |
| `AMP_BOT_USERNAME`      | `AMP`    | Bot's displayed username          |
| `AMP_BOT_GAME_MODE`     | prompted | AMP survival or creative behavior |
| `AMP_MODE`              | prompted | `guided`, `autonomous`, or `idle` |
| `AMP_OPERATOR_USERNAME` | prompted | Exact human username for commands |
| `AMP_JAVA_PATH`         | `java`   | Compatible Java executable        |
| `AMP_DATA_DIR`          | `~/.amp` | Profiles and downloaded servers   |

## Non-interactive startup

Automation can supply every startup decision explicitly:

```bash
amp-world \
    --world PATH \
    --version 26.2 \
    --port 25576 \
    --operator ExactHumanName \
    --mode guided \
    --amp-game-mode survival \
    --accept-eula \
    --copy-back \
    --non-interactive
```

`--accept-eula` is required only if that world profile has no accepted EULA.
Without `--copy-back`, a non-interactive run leaves the source untouched.
`--non-interactive` suppresses setup questions; guided and autonomous modes
still read goals and instructions from the terminal.

## Security boundary

The server binds to `127.0.0.1`, so only clients on the same computer can reach
it. Do not expose an offline-mode server to the internet: it does not verify
player identities, so a reachable client could claim another player's name.
