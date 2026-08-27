# Design

## Runtime flow

`cli.py` connects AMP to an existing direct server.
`tools/run_local_world.py` adds copied-world setup and dedicated-server
orchestration. Both create a `Bot`, start its connection and execution
lifecycle, and select an AMP input mode. `Bot` is the public facade and
composition root; callers do not coordinate transport, planning, world state,
or execution directly.

```text
user goal
  -> planner and world snapshot
  -> validated command objects
  -> gameplay resolution and pathfinding
  -> execution queue
  -> protocol packets
  -> Minecraft server
  -> clientbound updates
  -> world state and action feedback
```

## Module boundaries

- `connection.py` owns sockets, framing, compression, encryption, keepalive
  handling, and the listener thread.
- `java26_protocol.py` owns Java 26 Login and Configuration states, clientbound
  decoding, and serverbound action encoding.
- `world_state.py` applies normalized protocol events and owns mutable world,
  inventory, health, and entity state.
- `pathfinder.py` reads world state and produces walkable coordinate paths.
- `gameplay.py` turns high-level actions into paths, inventory preparation,
  orientation, and executable interactions.
- `execution.py` owns the action queue and serverbound packet serialization.
- `planner.py` builds model context, validates replies, resolves supported
  commands, and runs the autonomous loop.
- `model_clients.py` normalizes provider APIs to a plain-text completion
  contract.
- `lifecycle.py` starts, stops, and recovers the connection and execution
  workers.
- `bot.py` composes these modules and remains the public interface.
- `tools/run_local_world.py` owns isolated local-world profiles, server process
  lifecycle, operator setup, and backup-first copy-back outside the `Bot`
  runtime.

## Concurrency

AMP uses separate threads for socket listening, action execution, and autonomous
planning. The CLI remains responsive so a user can inject or stop an autonomous
goal. The execution queue and result list share a condition variable, and the
planner waits for a completed action batch before replanning.

## World representation

Chunk sections use palette-compressed block states. `chunk.py` resolves a block
coordinate through section-local packed data, the section palette, and the
generated global block registry. `world_state.py` patches block changes after
the initial chunk decode so pathfinding and action confirmation read current
state.

## Planning boundary

The model receives a concise snapshot rather than raw chunk objects. Model
replies are treated as untrusted input: they must parse as a command list and
satisfy `command_data.py` before resolution or execution. Provider adapters
return text only and normalize transport or SDK errors into `ModelClientError`.

## Supported-scope principle

Generated protocol IDs are data, not proof of complete version support. The
checked-in support manifest advertises only stable versions with complete
offline and live evidence. AMP 1.0 supports all stable Java 26 releases for
direct offline-mode servers. It has no Realm-primary version because Minecraft
Services has not approved AMP's client ID. Functionality outside the 1.0
boundary is listed in [Future work](FUTURE.md).
