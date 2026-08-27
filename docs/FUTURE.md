# Future work

This document records functionality intentionally outside AMP 1.0. Items are grouped by capability, not scheduled release. Inclusion here is not a compatibility promise or delivery commitment.

## Connection and protocol

- Activate authenticated online-mode login after Microsoft approves AMP's client ID for Minecraft Services. Device authorization, Minecraft account exchange, server-hash session join, encrypted transport, and Realm resolution are retained and covered by offline tests, but are not exposed as AMP 1.0 functionality. See [Authentication status](AUTHENTICATION.md).
- Run the retained authenticated dedicated-server and Realm live gates after approval, then publish the supported authenticated-server and Realm version matrix.
- Add resource-pack download, integrity validation, application, and status reporting. AMP 1.0 declines server resource packs.
- Add future stable Minecraft Java releases after their protocol family, fixtures, and live gameplay matrix pass. Generated packet IDs alone do not confer support.
- Add dimension-aware chunk bounds and navigation for the Nether, End, and custom dimensions. The 1.0 world model assumes the standard Java 26 Overworld height range.
- Handle server transfers, proxy-specific login behavior, plugin channels, and modded protocol extensions.
- Persist enough connection state to resume interrupted autonomous work after reconnecting instead of reconnecting only at the transport/lifecycle level.

## Movement and navigation

- Replace discrete position steps with physics-aware movement that models velocity, acceleration, collision, fall distance, and server corrections.
- Add sprinting, jumping across gaps, multi-block falls with safety checks, swimming, climbing ladders and vines, crawling, and elytra movement.
- Interact with doors, gates, trapdoors, buttons, pressure plates, scaffolding, and other traversable world objects.
- Navigate fluids, hazards, partial-height blocks, fences, walls, slabs, stairs, snow layers, and other collision shapes using block-state geometry rather than the current passable/solid classification.
- Replan when chunks change, a server corrects position, or entities and other dynamic obstacles block the route.
- Plan across unloaded chunks by exploring incrementally instead of treating absent world data as impassable.
- Add route policies for safety, speed, resource cost, and acceptable fall or environmental damage.
- Add vehicles and mounts, including boats, minecarts, and rideable entities.

## World state

- Track time, weather, dimension, biome, experience, game rules, difficulty, spawn point, status effects, attributes, equipment, and active item use.
- Decode entity metadata, equipment, health, effects, poses, ownership, hostility, and relationships instead of tracking only identity, type, and position.
- Track block entities such as chests, furnaces, signs, beds, and command-driven state.
- Track fluid levels, light, world border, portals, and environmental hazards.
- Add bounded eviction or persistence for old chunks so long sessions do not retain every loaded chunk indefinitely.
- Model other players and multiplayer contention explicitly, including ownership of targets and concurrent block changes.

## Inventory, crafting, and items

- Implement general container opening, slot synchronization, transaction acknowledgement, drag behavior, shift-clicking, and safe recovery after rejected clicks.
- Add crafting from the player grid and crafting tables, including recipe discovery, ingredient planning, and recursive acquisition of missing ingredients.
- Add furnace, blast furnace, smoker, brewing stand, anvil, enchanting table, grindstone, stonecutter, smithing table, loom, and villager trading workflows.
- Manage armor, offhand items, shields, tools, weapons, food, consumables, durability, stack consolidation, and inventory capacity.
- Pick up dropped items and verify acquisition from inventory updates.
- Support item NBT/components and version-specific inventory encodings beyond the subset needed by the 1.0 hotbar-swap workflow.

## Mining and placement

- Account for Efficiency, Haste, Mining Fatigue, Aqua Affinity, underwater mining, airborne mining, tool durability, and other modifiers in survival break timing.
- Select tools by the desired outcome, including Silk Touch and Fortune, instead of choosing only a safe and sufficiently fast tool.
- Verify drops and inventory changes after mining, not only that the target block became air.
- Handle blocks that require special tools, multi-block structures, support rules, fluids, falling blocks, and state-dependent break behavior.
- Place directional and stateful blocks with requested orientation, half, axis, attachment face, waterlogging, and neighbor-dependent state.
- Build multi-block structures from plans, reserve materials, recover from partial failure, and compare the completed structure with the requested design.
- Add bridging, scaffolding, excavation volumes, vein mining, and area-clearing strategies.

## Combat and survival

- Pursue moving or distant targets and replan as their positions change.
- Add target selection by entity type, hostility, distance, threat, owner, and user policy instead of requiring a tracked entity ID.
- Respect attack cooldowns, weapon reach, line of sight, knockback, shields, armor, critical hits, and version-specific combat rules.
- Confirm combat outcomes from tracked entity state instead of relying only on the attack packet or an external live-check query.
- Add ranged combat with bows, crossbows, tridents, and projectiles.
- Add defensive behavior: retreat, strafing, blocking, healing, eating, avoiding hazards, and responding to low health or hunger.
- Add death recovery, respawn goals, dropped-inventory retrieval, and safe task cancellation after death.
- Add farming, hunting, food acquisition, shelter, sleeping, and other long-running survival behaviors.

## Planning and behavior

- Give the CLI's `passive`, `neutral`, and `aggressive` behavior labels defined policies. In 1.0 they are stored and reported but do not alter decision-making.
- Validate the CLI's accepted game-mode labels. Adventure and spectator need explicit mechanics, while superflat is a world type and should not be accepted as a game mode. In 1.0 every value except creative follows the survival branch.
- Expand the planner command vocabulary beyond movement, chat, finding blocks, mining, placement, and attacks.
- Add provider capabilities so adapters can request schema-constrained JSON when a model supports it while preserving the plain-text fallback.
- Add model-specific prompt profiles and validation repair for smaller local models that do not reliably follow the JSON-only instruction.
- Add goal decomposition, durable task state, resumable plans, priorities, cancellation at action boundaries, and explicit success criteria.
- Add memory and mapping that persist across sessions without exposing credentials or mixing state between servers or users.
- Add token, request, time, and cost budgets with a global circuit breaker for unattended autonomous runs.
- Add policy controls and user approval gates for destructive or high-impact goals.
- Support additional hosted providers through the existing `ModelClient` contract.

## User and operator experience

- Add non-interactive configuration through command-line arguments and a validated configuration file.
- Replace free-form configuration strings with explicit validation and actionable startup errors.
- Add structured logs, configurable verbosity, metrics, and trace records linking a goal to model replies, resolved commands, packets, and server-confirmed results.
- Add saveable server profiles without storing plaintext credentials in the repository.
- Add an operator status view for connection state, current goal, queued actions, world position, health, inventory, and recent failures.
- Add an installable package, project metadata, a console entry point, and a locked reproducible environment.

## Release maintenance

- Add a pending-version update command that imports a newly stable Java release without advertising it as supported.
- Add protocol-family diff reporting for packet states, IDs, registries, and fixtures so maintainers can decide whether a release reuses an adapter or needs a new one.
- Automate the promotion checklist: generated-data completeness, protocol fixtures, the offline suite, and all live gameplay checks must pass before a version becomes supported.
- Keep the manifest's Realm `primary` value unset until authenticated-server and Realm gates can run with AMP's approved client ID. After approval, promote only a supported latest-stable release with a passing Realm smoke check.

## Validation still needed after 1.0

- Exercise the Anthropic adapter against the real Anthropic API and the OpenAI-compatible adapter against representative hosted and local servers.
- Run the complete live gameplay suite across supported server implementations, network conditions, and every version claimed for complete gameplay support.
- Add long-running soak tests for reconnects, chunk retention, autonomous loops, model failures, and server corrections.
- Add adversarial model-output tests for malformed JSON, oversized replies, prompt injection through world or user text, and repeated invalid plans.
- Add multiplayer tests for concurrent world changes, competing inventory interactions, and entity movement during planning and execution.

## Deferred project work

- Choose and add the project's license. [Third-party notices](THIRD_PARTY_NOTICES.md) cover upstream data but do not license AMP.
- Finalize package metadata and release artifacts.
- Define the dependency-locking and reproducible-build policy for release distributions.
