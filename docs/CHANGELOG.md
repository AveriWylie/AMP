# Changelog

## 1.0.0 - Unreleased

- Requires Python 3.10 or later. Development tests require pytest 9.0.3 or
  later.
- Ships as the MIT-licensed `amp-mc` Python package with `amp` and
  `amp-world` console commands and an exact release-environment lock.
- Supports Minecraft Java Edition 26.1, 26.1.1, 26.1.2, and 26.2 on direct
  offline-mode servers; all four releases passed the offline and live gameplay
  gates.
- Keeps Microsoft authentication, encrypted online-mode login, and Realm
  resolution implemented but disabled because AMP's client ID is not approved by
  Minecraft Services.
- Removes the unreleased pre-26 protocol baseline; Git history is its only
  archive.
- Implements raw protocol transport, Configuration and Play states, bounded
  packet decompression, keepalive handling, and reconnection.
- Tracks chunks, heightmaps, block updates, inventory, health, and nearby
  entities as live world state.
- Supports pathfinding, movement, creative and survival mining, block placement,
  and attacks against nearby tracked entities.
- Provides guided and autonomous model planning with validated structured
  commands and server-confirmed mining and placement feedback.
- Supports Anthropic and OpenAI-compatible providers behind a provider-neutral
  completion interface.
- Reads provider configuration from the environment or an ignored `.env` file.
- Provides one cross-platform Python workflow for copied single-player worlds,
  isolated profiles, operator access, interactive AMP control, backup-first
  copy-back, and graceful server shutdown.
- Separates transport, world state, gameplay, lifecycle, execution, model
  providers, and planning behind the `Bot` facade.
- Includes offline pytest coverage and vanilla-server checks for connection and
  gameplay behavior.
- Generates protocol, block, item, and entity data from a pinned MIT-licensed
  PrismarineJS/minecraft-data revision.
- Hardens protocol frame limits, decompression limits, credential handling, CI
  action pinning, and remote model transport.
