# AMP - Agentic Minecraft Player

AMP is a Python-based Minecraft bot with guided and fully autonomous execution modes. It connects to a Minecraft server over raw TCP, decodes the binary protocol from scratch, builds a live world state from parsed chunk data (to do this I created a chunk parser available on my page in the [mc-chunk-parser](https://github.com/AveriWylie/mc-chunk-parser) repository), and uses a configurable language model to translate natural language into in-game actions.

Minecraft Java Edition 1.20.2 is AMP's primary supported version. The generated protocol table covers 1.19.4 through 1.20.2; the complete gameplay path and live-server checks target 1.20.2.

## How to build

```bash
python -m pip install -r requirements.txt
```

Requires Python 3.10+.

## Tests

The offline test suite uses pytest discovery and does not require a Minecraft server:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

The live connection test is skipped automatically when no server is listening on
`localhost:25565`.

To verify movement against a local offline-mode 1.20.2 server, enable RCON, set its password
in `MC_RCON_PASSWORD`, and run `python -m tools.check_movement`. The check moves its test player
one block and asks the server for the authoritative position before and after.
Creative mining has a matching `python -m tools.check_mining` check; it temporarily places a
stone block beside the test player and confirms the server changes it to air.
`python -m tools.check_inventory` gives the test player a diamond pickaxe, confirms AMP decodes
the slot update, selects that hotbar slot, and verifies the server's selected-slot state.
`python -m tools.check_survival_mining` puts a pickaxe in main inventory—not the hotbar—and
verifies AMP swaps, equips, times, and completes a survival stone break.
`python -m tools.check_placement` puts oak planks in main inventory and verifies AMP swaps,
equips, selects a support face, places the requested block in survival, and reports the
server-confirmed action result.
`python -m tools.check_combat` summons a stationary cow, confirms AMP tracks its readable
type and current position, attacks it, and verifies the server-authoritative health change.

The supported block, item, entity, and protocol registries are checked into the repository.
They are regenerated together from the pinned
[PrismarineJS/minecraft-data](https://github.com/PrismarineJS/minecraft-data) revision; no
manual registry download is required for Minecraft 1.20.2.

**Model provider** - copy `.env.example` to `.env`. The default `anthropic` provider uses `ANTHROPIC_API_KEY` and accepts an optional `ANTHROPIC_MODEL`. Set `AMP_MODEL_PROVIDER=openai-compatible` to use an OpenAI-compatible `/chat/completions` endpoint, then configure `OPENAI_BASE_URL`, `OPENAI_MODEL`, and an optional `OPENAI_API_KEY`. This supports local servers such as Ollama, LM Studio, and vLLM without an Anthropic key. Remote endpoints must use HTTPS; HTTP is accepted only for loopback development servers. Environment variables supplied by the shell, CI, or deployment take precedence.

**Server** - a Minecraft server running in offline mode at the version you specify, reachable over TCP.

## Limitations

- Servers must run in offline mode; authenticated online-mode login is not implemented.
- Combat targets must already be tracked and within normal attack reach; AMP does not yet chase moving or distant entities.
- Crafting and container interactions are not implemented.

## Protocol data

AMP's supported protocol numbers and packet IDs are generated from pinned
[PrismarineJS minecraft-data](https://github.com/PrismarineJS/minecraft-data) definitions.
The compact generated table is checked in at `protocol/packet_ids.json`, so running AMP does
not require Node.js, network access, or the full upstream dataset. The matching 1.20.2 block
and item registries are also checked in for chunk parsing and readable inventory state.

To regenerate the table after intentionally updating the pinned revision in
`tools/sync_minecraft_data.py`:

```bash
python tools/sync_minecraft_data.py
python tools/sync_minecraft_data.py --check
python -m pytest
```

See `THIRD_PARTY_NOTICES.md` for attribution and licensing notes.

## How to use

```bash
python cli.py
```

The CLI prompts for server host, port, username, Minecraft version, game mode, and behavior mode. After connecting, select guided or autonomous mode.

**Guided mode** - type natural language instructions one at a time. The planner resolves each prompt into a structured command sequence and executes it.

**Autonomous mode** - enter a high level goal. The bot reasons and acts step by step, re-evaluating world state between actions, until the goal is complete or a step limit is reached. Type new instructions at any point to inject mid-task updates, or type `stop` to end the task.

## Architecture

```
cli.py          - interactive setup and mode selection
bot.py          - public façade, dependency composition, planning coordination
connection.py   - TCP transport, framing, login/configuration protocol, keepalive listener
world_state.py  - clientbound packet dispatch and live world/inventory/entity state
gameplay.py     - movement, mining, placement, and combat command coordination
lifecycle.py    - connection recovery and execution-worker lifecycle
chunk.py        - binary chunk parser, NBT, palette resolution, block queries
pathfinder.py   - A* pathfinder over live world data
execution.py    - command queue, packet serialization
planner.py      - provider-neutral guided and autonomous planning
model_clients.py - provider-neutral client contract and Anthropic/OpenAI-compatible adapters
command_data.py - shared validation contract for planner and executor actions
```

## Connection and protocol

- Raw TCP socket with VarInt encoding and length-prefixed packet framing
- Minecraft handshake and login sequence
- Minecraft 1.20.2 Configuration state, including login acknowledgement, client information,
  keepalive/ping responses, and the transition into Play
- Keepalive loop with automatic response to prevent server kick
- Position confirmation to satisfy server teleport requirements
- Packet handlers for position, health, entity spawn/movement/teleport/removal, chunk data,
  and block updates
- Inventory snapshots, individual slot updates, and selected-hotbar tracking
- Reconnection logic with up to 3 retry attempts on connection failure

## Chunk parsing

- Full NBT tree parsing to extract heightmap data from chunk payloads
- Palette-based block state resolution: indirect mode (bits 4-14), direct mode (15+ bits), and single-value sections
- Post-1.16 long array packing where entries never straddle longs
- 24 vertical sections per chunk covering y=-64 to y=320
- Biome data skipped with correct offset bookkeeping
- Block update patching so world state stays accurate without full re-parses
- Version-aware blocks.json loading with per-version cache
- `get_block(x, y, z)` and `get_surface_y(x, z)` as the public interface

## Pathfinding

- A* over live parsed chunk data
- Manhattan distance heuristic, admissible for unit-cost grid movement
- Walkability check: feet passable, head passable, floor solid (2-block hitbox)
- Neighbor expansion: flat walk, step up one block, drop one block
- Tunable heuristic weight by input mode trading optimality for search speed
- Max node cap to prevent runaway searches on unreachable goals
- Stale heap entry detection to avoid reprocessing

## AI planning

- Provider-neutral completion contract isolates planning from SDK and HTTP response formats
- Anthropic adapter uses the official [Anthropic Python SDK](https://github.com/anthropics/anthropic-sdk-python)
- OpenAI-compatible adapter uses the documented [`/chat/completions`](https://developers.openai.com/api/reference/cli/resources/chat/subresources/completions) message and response shape without another SDK dependency
- World state snapshot passed as context: position, health, food, nearby surface blocks sampled in 8-block radius, entity positions
- Guided mode: single API call per prompt, history cleared between prompts
- Autonomous mode: closed-loop agentic planning where each executed step feeds back as context for the next decision, runs on a dedicated thread so mid-task prompts can be injected without blocking
- Autonomous steps wait for queued actions and report server-confirmed block outcomes, packet-send
  failures, or timeouts before replanning
- High level actions (go_to, find, mine, place, attack) resolved against live world state
  before execution
- JSON parse fault tolerance strips markdown fences if model includes them
- Graceful degradation returns empty command list on parse failure

## Execution

- Queue-driven action pipeline sending at most one action per 20 Hz tick on a daemon thread
- Version-aware Set Player Position packets with big-endian double serialization
- Chat Message packet (0x05) with timestamp, zero salt, and empty signature for offline mode
- Execution thread mirrors listen thread pattern: error caught, flag reset, clean exit
- Thread safe to restart on reconnect without double-starting

## Project status

Creative and base survival mining are supported on Minecraft 1.20.2, including full-inventory
tool selection and hardness-based break timing. Inventory-aware block placement selects a stack,
finds a support face, approaches, and places it. Enchantments, status effects, and
underwater/airborne mining penalties remain future work. Nearby tracked entities can be
targeted by ID for attacks. The autonomous planning loop is functional, but broader live-server
testing is still required.
