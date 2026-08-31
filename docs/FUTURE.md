# Future work

This document records functionality intentionally outside AMP 1.0. Items are
grouped by capability, not scheduled release. Inclusion here is not a
compatibility promise or delivery commitment.

## Connection and protocol

- Activate authenticated online-mode login after Microsoft approves AMP's client
  ID for Minecraft Services. Device authorization, Minecraft account exchange,
  server-hash session join, encrypted transport, and Realm resolution are
  retained and covered by offline tests, but are not exposed as AMP 1.0
  functionality. See [Authentication status](AUTHENTICATION.md).
- Run the retained authenticated dedicated-server and Realm live gates after
  approval, then publish the supported authenticated-server and Realm version
  matrix.
- Add resource-pack download, integrity validation, application, and status
  reporting. AMP 1.0 declines server resource packs.
- Add future stable Minecraft Java releases after their protocol family,
  fixtures, and live gameplay matrix pass. Generated packet IDs alone do not
  confer support.
- Add dimension-aware chunk bounds and navigation for the Nether, End, and
  custom dimensions. The 1.0 world model assumes the standard Java 26 Overworld
  height range.
- Handle server transfers, proxy-specific login behavior, plugin channels, and
  modded protocol extensions.
- Persist enough connection state to resume interrupted autonomous work after
  reconnecting instead of reconnecting only at the transport/lifecycle level.

## Movement and navigation

- Extend the current tick-paced path-edge movement and vertical gravity model
  with horizontal velocity, acceleration, full collision shapes, fall distance,
  and correction-aware replanning.
- Add sprinting, jumping across gaps, multi-block falls with safety checks,
  swimming, climbing ladders and vines, crawling, and elytra movement.
- Interact with doors, gates, trapdoors, buttons, pressure plates, scaffolding,
  and other traversable world objects.
- Navigate fluids, hazards, partial-height blocks, fences, walls, slabs, stairs,
  snow layers, and other collision shapes using block-state geometry rather than
  the current passable/solid classification.
- Replan when chunks change, a server corrects position, or entities and other
  dynamic obstacles block the route.
- Plan across unloaded chunks by exploring incrementally instead of treating
  absent world data as impassable.
- Add route policies for safety, speed, resource cost, and acceptable fall or
  environmental damage.
- Add vehicles and mounts, including boats, minecarts, and rideable entities.

## World state

- Track time, weather, dimension, biome, experience, game rules, difficulty,
  spawn point, status effects, attributes, equipment, and active item use.
- Decode entity metadata, equipment, health, effects, poses, ownership,
  hostility, and relationships instead of tracking only identity, type, and
  position.
- Track block entities such as chests, furnaces, signs, beds, and command-driven
  state.
- Track fluid levels, light, world border, portals, and environmental hazards.
- Track player and system chat, the tab list, teams, scoreboards, boss bars,
  advancements, titles, and other server-originated operator context.
- Add bounded eviction or persistence for old chunks so long sessions do not
  retain every loaded chunk indefinitely.
- Model other players and multiplayer contention explicitly, including ownership
  of targets and concurrent block changes.

## Inventory, crafting, and items

- Implement general container opening, slot synchronization, transaction
  acknowledgement, drag behavior, shift-clicking, and safe recovery after
  rejected clicks.
- Add crafting from the player grid and crafting tables, including recipe
  discovery, ingredient planning, and recursive acquisition of missing
  ingredients.
- Add furnace, blast furnace, smoker, brewing stand, anvil, enchanting table,
  grindstone, stonecutter, smithing table, loom, and villager trading workflows.
- Manage armor, offhand items, shields, tools, weapons, food, consumables,
  durability, stack consolidation, and inventory capacity.
- Pick up dropped items and verify acquisition from inventory updates.
- Support item NBT/components and version-specific inventory encodings beyond
  the subset needed by the 1.0 hotbar-swap workflow.

## Mining and placement

- Account for Efficiency, Haste, Mining Fatigue, Aqua Affinity, underwater
  mining, airborne mining, tool durability, and other modifiers in survival
  break timing.
- Select tools by the desired outcome, including Silk Touch and Fortune, instead
  of choosing only a safe and sufficiently fast tool.
- Verify drops and inventory changes after mining, not only that the target
  block became air.
- Handle blocks that require special tools, multi-block structures, support
  rules, fluids, falling blocks, and state-dependent break behavior.
- Place directional and stateful blocks with requested orientation, half, axis,
  attachment face, waterlogging, and neighbor-dependent state.
- Add bridging, scaffolding, excavation volumes, vein mining, and area-clearing
  strategies.
- Add connected-structure tasks such as felling a complete tree from the ground,
  with block discovery and replanning after every confirmed break.

## Blueprints and construction

- Define a versioned, provider-neutral blueprint model with a block palette,
  relative coordinates, origin, dimensions, metadata, and explicit air policy.
- Import common community formats such as Sponge `.schem` and Litematica
  `.litematic`, with size limits and clear errors for unsupported block states.
- Rotate, mirror, translate, crop, and preview a blueprint before changing the
  world.
- Produce a material manifest, compare it with inventory, reserve required
  items, and request acquisition or crafting of missing materials.
- Order construction around reach, support blocks, gravity, fluids, directional
  states, multi-block structures, scaffolding, and safe navigation.
- Checkpoint long builds, resume after interruption, reconcile the plan against
  current world state, and recover from partial placement or another player's
  edits.
- Verify the completed region block by block and report missing, extra, or
  incorrectly oriented blocks.
- Export a selected world region to AMP's neutral blueprint format.
- Require explicit bounds, maximum block counts, protected-block rules, and
  operator approval before large or destructive builds.

## Combat and survival

- Discover and pursue targets beyond the currently tracked, loaded, and
  pathfindable area. Repeated kill actions already replan while a tracked target
  moves.
- Add target selection by entity type, hostility, distance, threat, owner, and
  user policy instead of requiring a tracked entity ID.
- Add line-of-sight checks, knockback, shields, armor, critical hits, and
  version-specific combat rules. AMP 1.0 already enforces attack reach and waits
  for both the held weapon and the target's post-hit invulnerability window.
- Confirm single-hit damage from tracked entity health or metadata. Repeated
  kill actions currently confirm only that the server removed the target.
- Add ranged combat with bows, crossbows, tridents, and projectiles.
- Add defensive behavior: retreat, strafing, blocking, healing, eating, avoiding
  hazards, and responding to low health or hunger.
- Add explicit respawn goals and dropped-inventory retrieval. AMP 1.0 already
  sends the respawn request, rebuilds connection and world state, and cancels
  queued actions after death.
- Add farming, hunting, food acquisition, shelter, sleeping, and other
  long-running survival behaviors.

## Planning and behavior

- Add explicit behavior policies only when they affect planning and execution.
- Add adventure or spectator gameplay modes only with their required mechanics.
  World types such as superflat remain separate from bot gameplay modes.
- Expand the planner command vocabulary beyond movement, chat, nearby block
  acquisition, mining, placement, and attacks.
- Add provider capabilities so adapters can request schema-constrained JSON when
  a model supports it while preserving the plain-text fallback.
- Add model-specific prompt profiles and validation repair for smaller local
  models that do not reliably follow the JSON-only instruction.
- Add goal decomposition, durable task state, resumable plans, priorities, and
  explicit success criteria. AMP 1.0 can already stop between autonomous steps
  and cancels queued actions on death.
- Add staged long-distance navigation that replans as movement loads new chunks.
- Add memory and mapping that persist across sessions without exposing
  credentials or mixing state between servers or users.
- Add token, request, time, and cost budgets with a global circuit breaker for
  unattended autonomous runs.
- Add policy controls and user approval gates for destructive or high-impact
  goals.
- Support additional hosted providers through the existing `ModelClient`
  contract.
- Coordinate multiple AMP players without assigning the same target, inventory,
  or construction region to competing agents.

## User and operator experience

- Add selective world merge and backup-management tooling. AMP 1.0 includes a
  backup-first whole-world copy-back workflow.
- Add non-interactive arguments to the existing direct-server `amp/cli.py`
  flow. The local-world runner already supports non-interactive launch
  arguments.
- Replace the direct-server CLI's free-form configuration strings with explicit
  validation and actionable startup errors.
- Accept goals from authorized in-game chat users through a configurable command
  prefix, without treating arbitrary server chat as trusted model instructions.
- Add structured logs, configurable verbosity, metrics, and trace records
  linking a goal to model replies, resolved commands, packets, and
  server-confirmed results.
- Add saveable server profiles without storing plaintext credentials in the
  repository.
- Add an operator status view for connection state, current goal, queued
  actions, world position, health, inventory, and recent failures.

## Release maintenance

- Extend the candidate PR with generated data and protocol-family diff reports
  when upstream data is available. The current automation records official
  release metadata and the required promotion gates without guessing
  compatibility.
- Automate the promotion checks that can run offline. Protocol review and the
  complete live gameplay matrix remain mandatory maintainer gates before a
  Minecraft version becomes supported.
- Keep the manifest's Realm `primary` value unset until authenticated-server and
  Realm gates can run with AMP's approved client ID. After approval, promote
  only a supported latest-stable release with a passing Realm smoke check.

## Validation to expand after 1.0

- Expand the v1.0 real-provider smoke checks into a matrix of representative
  hosted and local Anthropic and OpenAI-compatible models.
- Run the complete live gameplay suite across supported server implementations,
  network conditions, and every version claimed for complete gameplay support.
- Add long-running soak tests for reconnects, chunk retention, autonomous loops,
  model failures, and server corrections.
- Add adversarial model-output tests for malformed JSON, oversized replies,
  prompt injection through world or user text, and repeated invalid plans.
- Add multiplayer tests for concurrent world changes, competing inventory
  interactions, and entity movement during planning and execution.
