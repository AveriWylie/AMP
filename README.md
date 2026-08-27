# AMP - Agentic Minecraft Player

AMP is a Python Minecraft bot with guided and autonomous planning modes. It
connects directly to a Minecraft Java Edition server, implements the network
protocol over raw TCP, builds live world state from chunk and entity packets,
and converts natural-language goals into validated gameplay actions.

AMP 1.0 supports Minecraft Java Edition 26.1, 26.1.1, 26.1.2, and 26.2 on direct
servers running in offline mode. Each advertised version has generated protocol
data, fixture coverage, and live gameplay verification. Historical protocol
implementations are available only through Git history.

| Java version | Protocol family | Offline suite | Live gameplay |
|--------------|-----------------|---------------|---------------|
| 26.1         | Java 26.1       | Verified      | Verified      |
| 26.1.1       | Java 26.1       | Verified      | Verified      |
| 26.1.2       | Java 26.1       | Verified      | Verified      |
| 26.2         | Java 26.2       | Verified      | Verified      |

## Install

AMP requires Python 3.10 or later.

```bash
python -m pip install amp-mc
```

This installs the `amp` and `amp-world` commands. Contributors can use
`python -m pip install -e ".[dev]"` for an editable development install. The
exact environment used to validate the release is recorded in
`requirements-lock.txt`.

Copy `.env.example` to `.env` and configure a model provider. Shell, CI, and
deployment environment variables take precedence over `.env`.

Anthropic is the default:

```env
AMP_MODEL_PROVIDER=anthropic
ANTHROPIC_API_KEY=your-api-key
# ANTHROPIC_MODEL=claude-opus-4-6
```

An OpenAI-compatible `/chat/completions` endpoint can be used instead:

```env
AMP_MODEL_PROVIDER=openai-compatible
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_MODEL=your-model-name
# OPENAI_API_KEY=optional-for-local-servers
```

This path supports local servers such as Ollama, LM Studio, and vLLM without an
API key. Remote endpoints must use HTTPS; HTTP is accepted only for loopback
development servers.

## Run

Start AMP with:

```bash
amp
```

To run AMP in a copy of an existing single-player world without Microsoft
authentication, use the documented [local-world workflow](docs/USAGE.md). One
Python command prepares and starts the server, grants optional operator access,
connects AMP in the selected planning mode, and coordinates shutdown. The human
player joins the printed local server address manually.

The CLI collects the server host, port, username, Minecraft version, and
gameplay mode before connecting. It defaults to the latest supported release,
Java 26.2.

- Guided mode accepts 1 instruction at a time, plans it, and waits for the next
  instruction.
- Autonomous mode accepts a high-level goal and replans after each action batch
  for up to 20 steps. New instructions can be injected while it runs.

Survival and creative are the supported gameplay modes.

## Implemented gameplay

- A* pathfinding over loaded chunk data, including flat movement, 1-block steps,
  and 1-block drops
- Position, health, food, chunks, block updates, player inventory, selected
  hotbar slot, and nearby entity tracking
- Creative and survival mining with inventory tool selection and hardness-based
  timing
- Inventory-aware block placement with support-face selection
- Attacks against tracked entities already within reach
- Guided and autonomous model planning with validated command objects
- Server-confirmed mining and placement results returned to the autonomous loop
- Connection recovery, keepalive handling, bounded packet frames, and bounded
  decompression

## Current limits

- Microsoft-authenticated online-mode servers and Java Realms are disabled in
  AMP 1.0. The implementation is retained, but Minecraft Services rejects newly
  registered client IDs unless Microsoft approves them. See [Authentication
  status](docs/AUTHENTICATION.md).
- World decoding and navigation target the standard Java 26 Overworld height
  range.
- Crafting, container interaction, and general inventory management are not
  implemented.
- Blueprint import, material planning, and multi-block construction are not
  implemented.
- Movement uses discrete 1-block position steps. AMP does not sprint, swim,
  climb, jump gaps, open doors, bridge gaps, or continuously replan around
  moving obstacles.
- Combat does not pursue moving or distant targets.
- Mining timing does not account for enchantments, status effects, or underwater
  and airborne penalties.
- Real-provider behavior remains a manual pre-release gate; the offline suite
  uses injected provider fakes.

The complete post-1.0 roadmap is maintained in [Future work](docs/FUTURE.md).

## Architecture

```text
amp/cli.py             interactive setup and mode selection
amp/bot.py             public facade and dependency composition
amp/connection.py      TCP framing, compression, encryption, and keepalive
amp/java26_protocol.py Java 26 login, decoding, and action encoding
amp/world_state.py     live world, inventory, and entity state
amp/gameplay.py        movement, mining, placement, and combat coordination
amp/lifecycle.py       connection recovery and worker lifecycle
amp/chunk.py           chunk, NBT, palette, and block-state decoding
amp/pathfinder.py      A* pathfinding over loaded world data
amp/execution.py       action queue and packet serialization
amp/planner.py         provider-neutral guided and autonomous planning
amp/model_clients.py   model-client contract and provider adapters
amp/command_data.py    planner and executor action validation
```

The planner receives a bounded world snapshot and returns JSON command objects.
`amp/command_data.py` validates model output. `amp/planner.py` resolves `find`
to a `go_to` command, while `amp/bot.py` sends gameplay actions to
`amp/gameplay.py`. `amp/execution.py` serializes the resulting actions and
waits for observable world-state changes where confirmation is available.

Provider-specific API shapes stay in `amp/model_clients.py`. The Anthropic
adapter uses the official [Anthropic Python
SDK](https://github.com/anthropics/anthropic-sdk-python). The OpenAI-compatible
adapter follows the OpenAI-compatible Chat Completions message and response
shape using Python's standard library.

## Tests

Install the development dependencies and run the offline suite:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

The live connection test skips automatically when no server is listening on
`localhost:25565`. Dedicated Java 26 checks cover movement, creative mining,
survival mining, inventory handling, placement, and combat against a local
offline-mode server with RCON enabled. The complete matrix has been run for
every version listed above.

See [Testing](docs/TESTING.md) for the commands and test boundaries.

## Generated Minecraft data

AMP checks in compact protocol, block, item, and entity registries generated
from a pinned
[PrismarineJS/minecraft-data](https://github.com/PrismarineJS/minecraft-data)
revision. Running AMP does not require Node.js, network access, or the upstream
dataset.

Regenerate and verify the data with:

```bash
python tools/sync_minecraft_data.py
python tools/sync_minecraft_data.py --check
python -m pytest
```

See [Third-party notices](docs/THIRD_PARTY_NOTICES.md) for attribution and
licensing details.

AMP itself is available under the [MIT License](LICENSE).

## Project documents

- [Changelog](docs/CHANGELOG.md)
- [Local-world usage](docs/USAGE.md)
- [Future work](docs/FUTURE.md)
- [Design](docs/DESIGN.md)
- [Testing](docs/TESTING.md)
- [Versioning](docs/VERSIONING.md)
- [Release process](docs/RELEASING.md)
- [Authentication status](docs/AUTHENTICATION.md)
- [Project philosophy](docs/PHILOSOPHY.md)
- [Third-party notices](docs/THIRD_PARTY_NOTICES.md)
