# AMP - Agentic Minecraft Player

AMP is a Python-based Minecraft bot with guided and fully autonomous execution modes. It connects to a Minecraft server over raw TCP, decodes the binary protocol from scratch, builds a live world state from parsed chunk data, and uses Claude as an AI planning layer to translate natural language into in-game actions.

## How to build

```bash
pip install requests
```

Requires Python 3.9+.

**blocks.json** - download for your target version from [PrismarineJS/minecraft-data](https://github.com/PrismarineJS/minecraft-data) at `data/pc/<version>/blocks.json`. Place it in a `blocks/` folder in the project root named `blocks_<version>.json`, for example `blocks_1.20.1.json`.

**api_key.txt** - create this file in the project root containing your Anthropic API key.

**Server** - a Minecraft server running in offline mode at the version you specify, reachable over TCP.

## How to use

```bash
python cli.py
```

The CLI prompts for server host, port, username, Minecraft version, game mode, and behavior mode. After connecting, select guided or autonomous mode.

**Guided mode** - type natural language instructions one at a time. The planner resolves each prompt into a structured command sequence and executes it.

**Autonomous mode** - enter a high level goal. The bot reasons and acts step by step, re-evaluating world state between actions, until the goal is complete or a step limit is reached.

## Architecture

```
cli.py          - interactive setup and mode selection
bot.py          - connection, world state, packet handling, execution thread
chunk.py        - binary chunk parser, NBT, palette resolution, block queries
pathfinder.py   - A* pathfinder over live world data
execution.py    - command queue, packet serialization
planner.py      - Claude API integration, guided and autonomous planning
```

## Connection and protocol

- Raw TCP socket with VarInt encoding and length-prefixed packet framing
- Minecraft handshake and login sequence
- Keepalive loop with automatic response to prevent server kick
- Position confirmation to satisfy server teleport requirements
- Packet handlers for position, health, entities, chunk data, and block updates
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

- Claude API integration for natural language to structured command translation
- World state snapshot passed as context: position, health, food, nearby surface blocks sampled in 8-block radius, entity positions
- Guided mode: single API call per prompt, history cleared between prompts
- Autonomous mode: closed-loop agentic planning where each executed step feeds back as context for the next decision
- High level actions (go_to, find, mine, place) resolved through pathfinder before execution
- JSON parse fault tolerance strips markdown fences if model includes them
- Graceful degradation returns empty command list on parse failure

## Execution

- Queue-driven action pipeline draining at 20 ticks per second on a dedicated daemon thread
- Set Player Position packet (0x13, protocol 762) with big-endian double serialization
- Chat Message packet (0x05) with timestamp, zero salt, and empty signature for offline mode
- Execution thread mirrors listen thread pattern: error caught, flag reset, clean exit
- Thread safe to restart on reconnect without double-starting

## Project status

Block interaction (mining and placing) is stubbed. The autonomous planning loop is functional but untested end to end against a live server.Max node cap to prevent runaway searches on unreachable goals
Stale heap entry detection to avoid reprocessing

AI planning

Claude API integration for natural language to structured command translation
World state snapshot passed as context: position, health, food, nearby surface blocks sampled in 8-block radius, entity positions
Guided mode: single API call per prompt, history cleared between prompts
Autonomous mode: closed-loop agentic planning where each executed step feeds back as context for the next decision
High level actions (go_to, find, mine, place) resolved through pathfinder before execution
JSON parse fault tolerance strips markdown fences if model includes them
Graceful degradation returns empty command list on parse failure

Execution

Queue-driven action pipeline draining at 20 ticks per second on a dedicated daemon thread
Set Player Position packet (0x13, protocol 762) with big-endian double serialization
Chat Message packet (0x05) with timestamp, zero salt, and empty signature for offline mode
Execution thread mirrors listen thread pattern: error caught, flag reset, clean exit
Thread safe to restart on reconnect without double-starting

Project status
Block interaction (mining and placing) is stubbed. The autonomous planning loop is functional but untested end to end against a live server.
