# Testing

## Offline suite

Install the development dependencies and run pytest from the repository root:

```bash
python -m pip install -r requirements-dev.txt
python -m pytest
```

The suite covers protocol framing and compression, login/configuration flow,
chunk decoding, generated registries, pathfinding, packet dispatch, inventory
state, mining calculations, placement planning, combat coordination, provider
adapters, planner validation, lifecycle composition, and execution feedback.
Model-provider tests use injected fakes and do not contact external APIs.

The live connection test skips when no server is listening on `localhost:25565`.

## Generated-data check

The protocol, block, item, and entity registries are generated together from the
revision pinned in `tools/sync_minecraft_data.py`.

```bash
python tools/sync_minecraft_data.py --check
```

This check fetches upstream metadata and therefore requires network access. It
does not rewrite files when the checked-in data is current.

## Live Java 26 checks

Run the live checks against a disposable server for the Java version being
verified, in offline mode. Enable RCON and provide its password through
`MC_RCON_PASSWORD`. The checks change server state and should not target a
shared world.

```bash
python -m tools.check_movement --version 26.2
python -m tools.check_mining --version 26.2
python -m tools.check_inventory --version 26.2
python -m tools.check_survival_mining --version 26.2
python -m tools.check_placement --version 26.2
python -m tools.check_combat --version 26.2
```

- `check_movement` moves the test player 1 block and compares authoritative
  positions through RCON.
- `check_mining` places stone beside the player and verifies creative mining
  changes it to air.
- `check_inventory` gives the player a diamond pickaxe, verifies slot decoding,
  selects it, and checks the selected slot.
- `check_survival_mining` puts a pickaxe in main inventory and verifies
  swapping, equipping, timing, and breaking stone.
- `check_placement` puts oak planks in main inventory and verifies swapping,
  support-face selection, placement, and the resulting block.
- `check_combat` summons a stationary cow, verifies entity tracking, attacks it,
  and checks the authoritative health change.

Each command accepts `--version`. The checked-in support manifest records the
versions for which the complete set has passed. See
[Versioning](VERSIONING.md) for the Minecraft-version promotion policy.

Authenticated-server and Realm gates are retained but disabled until Microsoft
approves AMP's client ID for Minecraft Services. See [Authentication
status](AUTHENTICATION.md).

## External model providers

The automated suite verifies adapter request and response normalization without
credentials or network calls. Real-provider behavior is a manual integration
boundary because it requires an operator-selected model, credentials or a local
server, and may incur API charges. [Releasing](RELEASING.md) defines the release
gate; [Future work](FUTURE.md) defines the broader post-1.0 provider matrix.

## Local-world workflow

`tests/test_local_world_runner.py` covers isolated world identities, preserved
server properties, offline operator UUIDs, first-run defaults, persisted EULA
acceptance, model preflight, version validation, prompt defaults, and
backup-first copy-back. End-to-end operator checks are defined in
[Releasing](RELEASING.md).
