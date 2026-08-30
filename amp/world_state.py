
# imports
import os

from amp.protocol_types import (
    BlockChanged,
    ChunkLoaded,
    EntitiesRemoved,
    EntityMoved,
    EntitySpawned,
    EntityTeleported,
    HealthChanged,
    HotbarSelected,
    InventoryReplaced,
    PositionChanged,
    SelfEntityIdentified,
    SlotChanged,
    WorldReset,
)


"""
--------------------------------------------------------------------------------------------
Class Header - World state tracker
--------------------------------------------------------------------------------------------
Holds everything AMP believes about the world right now, and is the only place that mutates
it. Packets come in, the adapter turns them into version-neutral events, and this applies
them. Nothing above this layer knows a packet ID, and nothing below it knows what a bot wants,
which is what lets a new Minecraft version change its wire format without touching any of the
state, pathfinding or planning code.

It takes a protocol_adapter and a connection rather than owning either. The adapter is how
bytes become events, the connection is only held so respawn can be sent, which is the one
place this has to talk back rather than just listen.

state is a plain nested dict on purpose. It gets snapshotted and handed to the planner as
model context, so keeping it as ordinary data means no serialization step between here and
there.
--------------------------------------------------------------------------------------------
"""
class WorldStateTracker:

    def __init__(self, protocol_adapter, connection):
        self.protocol_adapter = protocol_adapter
        self.connection = connection
        # opt-in, off by default. Entity tracing is noisy enough to drown everything else, so
        # it is an environment switch rather than a flag anyone has to remember to turn back off
        self._trace_entities = os.environ.get("AMP_TRACE_ENTITIES") == "1"
        self._player_loaded_pending = False
        # death recovery is a reconnect, and these two track where in it we are
        self._reconnect_after_respawn_pending = False
        self._respawn_requested = False
        # set by Bot, so this can signal death without importing anything that owns the socket
        self.on_respawn = None
        self.on_respawn_complete = None
        # position_revision counts corrections so movement code can notice the server moved us
        # and abandon a path that was planned from a position we are no longer standing at
        self.state = {
            "position": {"x": 0.0, "y": 0.0, "z": 0.0, "yaw": 0.0, "pitch": 0.0},
            "position_revision": 0,
            "health": 20.0,
            "food": 20,
            "entities": {},
            "self_entity_id": None,
            # compared on respawn, a different dimension means the whole map is stale
            "dimension_id": None,
            "map": {},
            "blocks": {},
            "inventory": {"slots": {}, "selected_hotbar_slot": 0, "carried": None, "state_id": 0},
        }


    # One packet can decode to several events, a chunk packet carries block data and entities
    # together, so this drains whatever the adapter produced rather than expecting one event.
    def _on_packet(self, packet_id, payload):
        for event in self.protocol_adapter.decode_play(packet_id, payload):
            self.apply(event)


    """
    --------------------------------------------------------------------------------------------
    Function Header - Apply
    --------------------------------------------------------------------------------------------
    The single mutation point. Every event type lands in exactly one branch, and the final else
    raises rather than ignoring the event, because a silently dropped event means state quietly
    drifts out of sync with the server and you debug it much later as "the bot walked into a
    wall". Failing loudly at the moment an unhandled type appears is far cheaper.

    A few branches are doing more than they look.

    Position is relative or absolute per axis, not per packet. The server sets one bit per
    field, so yaw can be a delta while x is absolute in the same message, which is why each
    line tests its own flag rather than the packet having a single mode.

    Entity movement is applied only to entities already known. A delta for an entity that was
    never spawned has no base to apply to, so it is dropped rather than inventing a position at
    the origin.

    Inventory has a state_id which is the server's sequence number for the window. Anything
    older than what is already held is stale and ignored, which is what stops a slow reply from
    overwriting a newer inventory and making AMP hold an item it no longer has.
    --------------------------------------------------------------------------------------------
    """
    def apply(self, event):

        if isinstance(event, PositionChanged):
            previous = self.state["position"]

            # each bit marks its own field as relative, so one packet can mix both kinds
            self.state["position"] = {
                "x": previous["x"] + event.x if event.relative_flags & 1 else event.x,
                "y": previous["y"] + event.y if event.relative_flags & 2 else event.y,
                "z": previous["z"] + event.z if event.relative_flags & 4 else event.z,
                "yaw": previous["yaw"] + event.yaw if event.relative_flags & 8 else event.yaw,
                "pitch": previous["pitch"] + event.pitch if event.relative_flags & 16 else event.pitch,
            }

            self.state["position_revision"] += 1
            # every teleport has to be echoed back or the server keeps resending it
            self.protocol_adapter.acknowledge_position(self.state["position"])
            self._trace(f"position acknowledged pos={self.state['position']} load_pending={self._player_loaded_pending}")

        elif isinstance(event, HealthChanged):
            self.state["health"] = event.health
            self.state["food"] = event.food
            self._trace(f"health={event.health} food={event.food} load_pending={self._player_loaded_pending}")

            # death is a state the server will not move us out of until we ask
            if event.health <= 0:
                self._respawn()

            # health above zero after a death is the respawn actually completing, which is the
            # only reliable signal that the new session is live enough to reconnect on
            else:
                self._respawn_requested = False

                if self._reconnect_after_respawn_pending:
                    self._reconnect_after_respawn_pending = False

                    if self.on_respawn_complete is not None:
                        self.on_respawn_complete()
                        return

                self._send_player_loaded_if_ready()

        # arrives on first join and again on every respawn, so it also starts the load handshake
        elif isinstance(event, SelfEntityIdentified):
            self.state["self_entity_id"] = event.entity_id
            self.state["dimension_id"] = event.dimension_id
            self._begin_player_load()

        # the world we were tracking is gone, discard it rather than trying to reconcile
        elif isinstance(event, WorldReset):
            self._trace(f"respawn dimension={event.dimension_id} position={self.state['position']}")
            self._reset_world_state(event.dimension_id)

        elif isinstance(event, EntitySpawned):

            self.state["entities"][event.entity_id] = {
                "uuid": event.uuid, "type": event.type_id, "name": event.name,
                "x": event.x, "y": event.y, "z": event.z,
            }

            if event.name == "player":
                self._trace(f"spawn id={event.entity_id} type=player uuid={event.uuid} pos=({event.x}, {event.y}, {event.z})")

        elif isinstance(event, EntityMoved):
            entity = self.state["entities"].get(event.entity_id)

            # a delta needs something to apply to, an unknown entity has no base position
            if entity is not None:
                entity["x"] += event.dx
                entity["y"] += event.dy
                entity["z"] += event.dz

                if entity.get("name") == "player":
                    self._trace(f"move player={event.entity_id} pos=({entity['x']}, {entity['y']}, {entity['z']})")

            # traced rather than ignored silently, an unknown ID here usually means a spawn was
            # missed, which is exactly what respawn bugs look like
            else:
                self._trace(f"move unknown={event.entity_id} delta=({event.dx}, {event.dy}, {event.dz})")

        elif isinstance(event, EntityTeleported):
            entity = self.state["entities"].get(event.entity_id)

            if entity is not None:
                entity.update({"x": event.x, "y": event.y, "z": event.z})

                if entity.get("name") == "player":
                    self._trace(f"teleport player={event.entity_id} pos=({event.x}, {event.y}, {event.z})")

            else:
                self._trace(f"teleport unknown={event.entity_id} pos=({event.x}, {event.y}, {event.z})")

        elif isinstance(event, EntitiesRemoved):
            # captured before the removal loop, afterwards there is nothing left to name
            removed_players = {entity_id: self.state["entities"].get(entity_id) for entity_id in event.entity_ids
                               if self.state["entities"].get(entity_id, {}).get("name") == "player"}

            if removed_players:
                self._trace(f"remove players={removed_players}")

            for entity_id in event.entity_ids:
                self.state["entities"].pop(entity_id, None)

        elif isinstance(event, ChunkLoaded):
            self.state["map"][(event.chunk_x, event.chunk_z)] = event.chunk

        elif isinstance(event, BlockChanged):
            self._apply_block_change(event)

        elif isinstance(event, InventoryReplaced):
            # window 0 is the player's own inventory, other windows are chests and the like
            if event.window_id == 0:
                inventory = self.state["inventory"]

                # older than what we hold means a stale reply arrived late, so ignore it
                if event.state_id >= inventory["state_id"]:
                    inventory.update({
                        "slots": dict(event.slots), "carried": event.carried,
                        "state_id": event.state_id,
                    })

        elif isinstance(event, SlotChanged):
            self._apply_slot_change(event)

        elif isinstance(event, HotbarSelected):
            self.state["inventory"]["selected_hotbar_slot"] = event.slot

        # unhandled events are a bug, not a no-op, silently dropping one desynchronises state
        else:
            raise TypeError(f"Unsupported world event: {type(event).__name__}")


    # Opt-in through AMP_TRACE_ENTITIES=1. flush=True because this is used to follow a live
    # session alongside server output, and buffered lines would arrive out of order with it.
    def _trace(self, message):

        if self._trace_entities:
            print(f"Entity trace: {message}", flush=True)


    """
    --------------------------------------------------------------------------------------------
    Function Field Header - Player load handshake
    --------------------------------------------------------------------------------------------
    Java 26 expects the client to say when it has finished loading, and the server holds the
    player in place until it hears it.

    It used to wait on a position and terrain arriving before sending. That is gone, because
    death recovery now reconnects rather than reconciling in place, so by the time this fires
    the session is either fresh from login or fresh from a respawn, and in both cases the
    server has already sent what it wanted us to have.

    _player_loaded_pending is armed on join and on respawn and cleared on send, so this fires
    once per load rather than every time something arrives afterwards.
    --------------------------------------------------------------------------------------------
    """
    def _begin_player_load(self):
        self._player_loaded_pending = True


    def _send_player_loaded_if_ready(self):

        if not self._player_loaded_pending:
            return

        self.connection._send_protocol_packet(self.connection.play_ids["player_loaded"])
        # cleared so this fires once per load, not on everything that follows
        self._player_loaded_pending = False
        self._trace("player_loaded sent")


    """
    --------------------------------------------------------------------------------------------
    Function Header - Respawn request
    --------------------------------------------------------------------------------------------
    Asks the server to put us back in the world, and starts the recovery that follows.

    Guarded by _respawn_requested because health can be reported as zero several times before
    the respawn takes, and asking again each time would queue duplicate requests.

    on_respawn fires immediately, before the request goes out, so queued actions planned for
    the world we just died in are cancelled rather than replayed into the new one.

    _reconnect_after_respawn_pending is set here and consumed by the health branch above, which
    is what makes health rising above zero the trigger for reconnecting. That is deliberate,
    the respawn packet is fire and forget and nothing acknowledges it, so the first sign it
    worked is health coming back.

    Health is reset locally as well because of that same silence, state would otherwise read 0
    until the server answers and anything checking "am I alive" would keep thinking we are dead.
    --------------------------------------------------------------------------------------------
    """
    def _respawn(self):
        # zero health can be reported more than once before the respawn lands
        if self._respawn_requested:
            return

        # cancels work planned for the world we just died in, before asking for a new one
        if self.on_respawn is not None:
            self.on_respawn()

        self._reconnect_after_respawn_pending = True
        self._respawn_requested = True
        packet_id = self.connection._encode_varint(self.connection.play_ids["client_command"])
        data = self.connection._encode_varint(0)
        self.connection._send(self.connection._encode_varint(len(packet_id + data)) + packet_id + data)
        self.state["health"] = 20.0
        self.state["food"] = 20


    """
    --------------------------------------------------------------------------------------------
    Function Header - Reset for reconnect
    --------------------------------------------------------------------------------------------
    Discards everything the previous network session owned, called between the old connection
    closing and the new one opening.

    Unlike _reset_world_state below, this keeps nothing at all. That one is a respawn inside a
    live session, where terrain may still be valid. This is a new socket, so every entity ID,
    chunk and inventory sequence number belongs to a session that no longer exists, and the
    server is about to send all of it again.

    selected_hotbar_slot is cleared here where the respawn path preserves it, because a
    reconnect starts a fresh login and the server states the selected slot itself.
    --------------------------------------------------------------------------------------------
    """
    def reset_for_reconnect(self):
        self._player_loaded_pending = False
        self._reconnect_after_respawn_pending = False
        self._respawn_requested = False

        self.state.update({
            "position": {"x": 0.0, "y": 0.0, "z": 0.0, "yaw": 0.0, "pitch": 0.0},
            "health": 20.0,
            "food": 20,
            "self_entity_id": None,
            "dimension_id": None,
        })

        self.state["position_revision"] += 1
        self.state["entities"].clear()
        self.state["map"].clear()
        self.state["blocks"].clear()
        self.state["inventory"].update({"slots": {}, "selected_hotbar_slot": 0,"carried": None, "state_id": 0})


    """
    --------------------------------------------------------------------------------------------
    Function Header - Reset world state
    --------------------------------------------------------------------------------------------
    Discards what is no longer true after a respawn, and keeps what still is. What survives
    depends entirely on whether the dimension changed.

    Entities always go. Every entity ID is reassigned on respawn, so keeping them would leave
    the bot attacking IDs that now belong to something else, or nothing.

    Chunks only go on a dimension change. Dying in place puts you back in the same world, so
    the terrain already parsed is still correct and throwing it away would mean waiting for it
    all to arrive again before the bot could path anywhere. Change dimension and none of it
    applies, so map and blocks are cleared.

    dimension_changed needs both values present. A None on either side means we cannot tell,
    and the safe reading there is that the world is the same, since wrongly clearing costs a
    reload while wrongly keeping costs correctness.

    Inventory is cleared but selected_hotbar_slot is not, because the server resends the
    contents after respawn while the selected slot persists across death.

    position_revision is bumped so any path planned before the death is recognised as stale,
    the same mechanism a server correction uses.
    --------------------------------------------------------------------------------------------
    """
    def _reset_world_state(self, dimension_id):
        # both sides must be known, an unknown dimension is treated as unchanged
        dimension_changed = (self.state["dimension_id"] is not None and dimension_id is not None
                             and dimension_id != self.state["dimension_id"])

        self.state["dimension_id"] = dimension_id
        # invalidates any path planned before the death, same as a server correction would
        self.state["position_revision"] += 1
        self._begin_player_load()

        # same dimension keeps its terrain and its entities, only a dimension change makes
        # them meaningless, since dying in place leaves the world and its mobs where they were
        if dimension_changed:
            self.state["entities"].clear()
            self.state["map"].clear()
            self.state["blocks"].clear()

        # selected_hotbar_slot deliberately survives, it persists across death server-side
        self.state["inventory"].update({"slots": {}, "carried": None, "state_id": 0})


    """
    --------------------------------------------------------------------------------------------
    Function Header - Block change patching
    --------------------------------------------------------------------------------------------
    Writes a single block update over an already-parsed chunk. It patches rather than reparsing
    because the chunk's packed long array is read-only as far as AMP is concerned, unpacking,
    editing one entry and repacking for every block break would be far more work than keeping a
    small dict of overrides that get_block consults first.

    Both misses are ignored deliberately. A change in a chunk that was never loaded, or in a
    section that was never sent, is a change to something AMP cannot see anyway, so there is
    nothing to correct and nothing to report.
    --------------------------------------------------------------------------------------------
    """
    def _apply_block_change(self, event):
        chunk = self.state["map"].get((event.x >> 4, event.z >> 4))

        # change in an unloaded chunk, nothing here can see it so nothing needs updating
        if chunk is None:
            return

        section_y = (event.y + 64) >> 4

        if section_y in chunk._sections:
            section = chunk._sections[section_y]
            section.setdefault("patched", {})[(event.x & 0xF, event.y & 0xF, event.z & 0xF)] = event.state_id


    """
    --------------------------------------------------------------------------------------------
    Function Header - Slot change
    --------------------------------------------------------------------------------------------
    Applies a single inventory slot update. Window 0 is the player inventory and -2 is the
    server writing directly into it, both of which are ours to track. -1 with slot -1 is the
    cursor, the item being dragged, which lives beside the slots rather than in them.

    The stale check only applies to window 0, since that is the window carrying a sequence
    number worth comparing.

    A None item means the slot emptied, so the key is removed rather than stored as None. That
    keeps "slot missing" as the single way to say empty, instead of callers having to handle
    both a missing key and a present None.
    --------------------------------------------------------------------------------------------
    """
    def _apply_slot_change(self, event):
        inventory = self.state["inventory"]

        if event.window_id in (0, -2) and event.slot >= 0:
            # a lower state_id than we hold is an out-of-order reply, so drop it
            if (event.window_id == 0 and event.state_id is not None
                    and event.state_id < inventory["state_id"]):
                return

            # empty means remove the key, so absence is the only representation of empty
            if event.item is None:
                inventory["slots"].pop(event.slot, None)
            else:
                inventory["slots"][event.slot] = event.item

            if event.state_id is not None:
                inventory["state_id"] = event.state_id

        # the cursor, whatever is being dragged, tracked apart from the slots themselves
        elif event.window_id == -1 and event.slot == -1:
            inventory["carried"] = event.item
