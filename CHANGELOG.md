# Changelog

## 1.0.0 - Unreleased

- Established Minecraft Java Edition 1.20.2 as the primary supported version.
- Added raw protocol connection and configuration-state handling, live world and inventory tracking, pathfinding, movement, mining, block placement, and nearby entity combat.
- Added guided and autonomous Claude planning with execution outcomes fed back into the next planning step.
- Generated protocol, block, item, and entity data from a pinned MIT-licensed PrismarineJS/minecraft-data revision.
- Added pytest coverage and vanilla-server checks for connection, movement, inventory, creative and survival mining, placement, and combat.
