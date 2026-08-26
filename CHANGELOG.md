# Changelog

## 1.0.0 - Unreleased

- Requires Python 3.10 or later. Development tests require pytest 9.0.3 or later.
- Targets Minecraft Java Edition 1.20.2, with generated protocol tables for 1.19.4 through 1.20.2.
- Implements raw protocol transport, Configuration and Play states, bounded packet decompression, keepalive handling, and reconnection.
- Tracks chunks, heightmaps, block updates, inventory, health, and nearby entities as live world state.
- Supports pathfinding, movement, creative and survival mining, block placement, and nearby entity combat.
- Provides guided and autonomous model planning with validated structured commands and server-confirmed execution feedback.
- Supports Anthropic and OpenAI-compatible model providers behind a provider-neutral completion interface.
- Reads Anthropic credentials from the environment or an ignored `.env` file.
- Keeps transport, world state, gameplay, lifecycle, execution, model providers, and planning in separate modules behind the `Bot` façade.
- Includes offline pytest coverage and vanilla-server checks for connection and gameplay behavior.
- Generates protocol, block, item, and entity data from a pinned MIT-licensed PrismarineJS/minecraft-data revision.
