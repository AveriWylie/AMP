AMP — Agentic Minecraft Player
AMP is a Python-based Minecraft bot with both guided and fully autonomous execution modes. It connects directly to a Minecraft server over TCP, decodes the binary protocol from scratch, builds a live world state, and uses Claude as an AI planning layer to translate natural language into in-game actions.
How it works
AMP operates across three layers:
Connection and protocol — A raw TCP connection handles the Minecraft handshake and login sequence. Incoming packets are decoded using a custom VarInt reader and framing layer. Chunk data is parsed from the binary wire format including full NBT tree processing, palette-based block state resolution across 24 vertical sections, and heightmap extraction. Block updates patch the live world state incrementally so the map stays accurate without full re-parses.
Pathfinding — An A* pathfinder runs over the live parsed chunk data. It resolves block types from loaded chunks, checks walkability against a 2-block hitbox, and expands neighbors across flat walks, step-ups, and drops. The heuristic weight is tunable based on input mode, trading path optimality for search speed.
AI planning — The Claude API translates natural language prompts into structured command sequences. In guided mode each prompt produces one plan. In autonomous mode the planner runs in a closed loop, feeding the result of each executed step back as context for the next decision, until the goal is complete or a step limit is reached.
A queue-driven execution pipeline drains commands at 20 ticks per second on a dedicated thread, synchronized with Minecraft's expected packet rate.
Setup
Requirements

Python 3.9+
An Anthropic API key

Install dependencies
bashpip install requests
blocks.json
Download the blocks.json for your target Minecraft version from PrismarineJS/minecraft-data at data/pc/<version>/blocks.json. Place it in a blocks/ folder in the project root named blocks_<version>.json, for example blocks_1.20.1.json.
API key
Create a file named api_key.txt in the project root containing your Anthropic API key.
Server
A Minecraft server running in offline mode at the version you specify. The bot must be able to connect over TCP.
Usage
bashpython cli.py
The CLI will prompt for server host, port, username, Minecraft version, game mode, and behavior mode. After connecting, select guided or autonomous mode.
Guided mode — type natural language instructions one at a time. The planner resolves each prompt into actions and executes them.
Autonomous mode — enter a high level goal. The bot reasons and acts step by step until the goal is complete.
Architecture
cli.py          — interactive setup and mode selection
bot.py          — connection, world state, packet handling, execution thread
chunk.py        — binary chunk parser, NBT, palette resolution, block queries
pathfinder.py   — A* over live world data
execution.py    — command queue, packet serialization
planner.py      — Claude API integration, guided and autonomous planning
Project status
AMP is under active development. Block interaction (mining and placing) is not yet implemented. The autonomous planning loop is functional but untested end to end against a live server.
