# Changelog

## 1.0.0 - Unreleased

- Raised the supported Python floor to 3.10 and pytest to 9.0.3+ to avoid
  CVE-2025-71176 in pytest's Unix temporary-directory handling.
- Bounded inbound protocol frames and decompression to reject malicious
  server payloads before they can exhaust client memory.
- Removed the plaintext `api_key.txt` credential fallback; API credentials now
  come only from the environment or an ignored `.env` file.
- Established Minecraft Java Edition 1.20.2 as the primary supported version.
- Added raw protocol connection and configuration-state handling, live world and inventory tracking, pathfinding, movement, mining, block placement, and nearby entity combat.
- Added guided and autonomous Claude planning with execution outcomes fed back into the next planning step.
- Generated protocol, block, item, and entity data from a pinned MIT-licensed PrismarineJS/minecraft-data revision.
- Added pytest coverage and vanilla-server checks for connection, movement, inventory, creative and survival mining, placement, and combat.
- Fixed decoding and retention of packed chunk heightmaps used for spatial planning.
- Added validation for model-produced commands and explicit rejection of unsupported actions.
- Prevented duplicate execution workers during reconnects.
- Derived supported versions from generated protocol data and made imports independent of the working directory.
- Split connection transport, packet-driven world state, gameplay coordination, and lifecycle management out of the Bot façade.
