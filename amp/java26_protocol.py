"""
--------------------------------------------------------------------------------------------
Java 26 Protocol Module
--------------------------------------------------------------------------------------------
Login and Configuration state handling for the Java 26 protocol generation, plus every
clientbound decode and serverbound encode for Play. This is the only module in AMP that knows
a packet ID or a byte layout, everything above it works in the version-neutral types from
protocol_types.

That containment is the whole point. A new Minecraft release moves fields and renumbers
packets, and absorbing that here means world state, pathfinding, gameplay and planning never
change. Adapters are selected by protocol family through the registry, so several releases
that share a wire format share this one adapter.

Packet IDs are never hard-coded. They are looked up by protocol number from the generated
tables at construction, once per state, which is why a family change is a data change rather
than an edit here.

The connection is held for two reasons, its varint encoder and its socket. This builds the
bytes, the connection frames and sends them.
--------------------------------------------------------------------------------------------
"""

# imports
import struct
import time
import uuid
from amp.chunk import Chunk
from amp.entity_data import entity_name
from amp.inventory_data import item_name
from amp.protocol_data import packet_ids_for_protocol
from amp.protocol_types import (
    BlockChanged, ChunkLoaded, EntitiesRemoved, EntityMoved, EntitySpawned,
    EntityTeleported, HealthChanged, PositionChanged, SelfEntityIdentified,
    WorldReset,
    HotbarSelected, InventoryReplaced, SlotChanged,
    ChatAction, EncodedAction, LookAction, MoveAction, PacketStep, SneakAction,
    SwingAction,
    AttackAction, MineAction, PlaceAction, SelectHotbarAction, SwapHotbarAction,
    UseItemAction,
)


"""
--------------------------------------------------------------------------------------------
Class Header - Java 26 adapter
--------------------------------------------------------------------------------------------
Satisfies the ProtocolAdapter contract for the Java 26 family. Six ID tables are resolved up
front, clientbound and serverbound for each of login, configuration and play, because a packet
ID means nothing without knowing which state the connection is in. The same number is a
different packet in login than it is in play.

_sequence backs the block-interaction counter the server uses to acknowledge and, where
needed, roll back predicted block changes. It has to increase monotonically for the life of
the connection, which is why it lives on the adapter rather than being derived per action.
--------------------------------------------------------------------------------------------
"""
class Java26ProtocolAdapter:

    def __init__(self, family, version, connection):
        self.family = family
        self.version = version
        self.connection = connection
        protocol = connection._protocol_version
        self.login_clientbound = packet_ids_for_protocol(protocol, "clientbound", "login")
        self.login_serverbound = packet_ids_for_protocol(protocol, "serverbound", "login")
        self.configuration_clientbound = packet_ids_for_protocol(protocol, "clientbound", "configuration")
        self.configuration_serverbound = packet_ids_for_protocol(protocol, "serverbound", "configuration")
        self.play_clientbound = packet_ids_for_protocol(protocol, "clientbound")
        self.play_serverbound = packet_ids_for_protocol(protocol, "serverbound")
        self._sequence = 0


    """
    --------------------------------------------------------------------------------------------
    Function Field Header - Packet building helpers
    --------------------------------------------------------------------------------------------
    The three pieces every serverbound packet is assembled from.

    _next_sequence hands out the block-interaction counter. The server echoes it back to
    acknowledge a dig or place, so it must never repeat within a connection.

    _packed_position is Minecraft's block position encoding, one 64-bit integer holding all
    three coordinates. X and Z take 26 bits each and Y takes 12, laid out X then Z then Y,
    which is why Y sits in the low bits rather than the middle. The masks matter because the
    coordinates are signed, masking keeps the two's complement bits and drops the sign
    extension that would otherwise overwrite the neighbouring field.

    _packet wraps data with its ID and a total length prefix, the outer framing every packet
    needs. Compression and encryption happen below this, in connection.
    --------------------------------------------------------------------------------------------
    """
    def _next_sequence(self):
        sequence = self._sequence
        self._sequence += 1
        return sequence


    # one 64-bit integer, X 26 bits then Z 26 then Y 12, masks strip the sign extension
    @staticmethod
    def _packed_position(x, y, z):
        return ((x & 0x3FFFFFF) << 38) | ((z & 0x3FFFFFF) << 12) | (y & 0xFFF)


    def _packet(self, name, data):
        packet_id = self.connection._encode_varint(self.play_serverbound[name])
        return self.connection._encode_varint(len(packet_id + data)) + packet_id + data


    """
    --------------------------------------------------------------------------------------------
    Function Header - Encode action
    --------------------------------------------------------------------------------------------
    Turns one typed action into the packets that perform it. Most actions are a single packet
    and fall through to the return at the bottom, mining is the exception and returns early
    because it is genuinely two.

    Mining sends start-digging then stop-digging, with the tool's break time as the delay
    between them, which is why duration was carried on the action all the way from mining_data.
    Both are sent in creative too. Skipping the stop there looked reasonable, blocks break
    instantly, but the server wants the pair either way and one without the other leaves the
    dig open.

    Placement sends the cursor position as 0.5, 0.5, 0.5, the centre of the clicked face. The
    exact hit position only matters for blocks that orient by where you click, and centre is
    the safe default for everything else.

    Both digging and placing spend a sequence number, that is what the server acknowledges.

    The final else raises rather than ignoring, an action with no encoding is a bug in the
    caller and silently sending nothing would look like a lost packet instead.
    --------------------------------------------------------------------------------------------
    """
    def encode_action(self, action, world_state, game_mode):
        encode = self.connection._encode_varint

        if isinstance(action, MoveAction):
            # the flag byte is on_ground, and the server applies its own gravity when it is set,
            # so a jump or a fall has to clear it or the position is corrected straight back
            flags = 1 if action.on_ground else 0
            packet = self._packet("position", struct.pack(">dddB", action.x, action.y, action.z, flags))

        elif isinstance(action, LookAction):
            packet = self._packet("look", struct.pack(">ffB", action.yaw, action.pitch, 1))

        elif isinstance(action, ChatAction):
            message = action.message.encode("utf-8")
            data = (encode(len(message)) + message + struct.pack(">q", int(time.time() * 1000))
                    + struct.pack(">q", 0) + b"\x00" + encode(0) + b"\x00" * 3 + b"\x00")

            packet = self._packet("chat_message", data)

        elif isinstance(action, SwingAction):
            packet = self._packet("arm_animation", encode(action.hand))
        elif isinstance(action, SneakAction):
            packet = self._packet("player_input", b"\x20" if action.sneaking else b"\x00")
        elif isinstance(action, AttackAction):
            packet = self._packet("attack", encode(action.entity_id))

        # the one two-packet action, start digging then stop after the tool's break time
        elif isinstance(action, MineAction):

            def digging(status):
                return self._packet(
                    "block_dig",
                    encode(status) + encode(self._next_sequence())
                    + struct.pack(">Qb", self._packed_position(action.x, action.y, action.z), action.face),
                )

            steps = [PacketStep(digging(0))]
            # sent in creative too. It used to be skipped there on the reasoning that blocks
            # break instantly, but the server wants the pair regardless and without the stop
            # the dig is left open
            steps.append(PacketStep(digging(2), action.duration))
            return EncodedAction(tuple(steps))

        elif isinstance(action, PlaceAction):
            data = (encode(0) + struct.pack(">Q", self._packed_position(action.x, action.y, action.z))
                    + encode(action.face) + struct.pack(">fff??", .5, .5, .5, False, False)
                    + encode(self._next_sequence()))

            packet = self._packet("block_place", data)

        elif isinstance(action, UseItemAction):
            position = (world_state or {}).get("position", {})

            data = (encode(action.hand)
                    + encode(self._next_sequence())
                    + struct.pack(">ff", position.get("yaw", 0), position.get("pitch", 0)))

            packet = self._packet("use_item", data)

        elif isinstance(action, SelectHotbarAction):
            if action.slot not in range(9):
                raise ValueError("Hotbar slot must be between 0 and 8")

            packet = self._packet("held_item_slot", struct.pack(">h", action.slot))


        elif isinstance(action, SwapHotbarAction):
            packet = self._encode_hotbar_swap(action, world_state)

        else:
            raise TypeError(f"Unsupported Java 26 action: {type(action).__name__}")

        return EncodedAction((PacketStep(packet),))


    # The tick boundary, an empty packet whose only content is its ID. Returns raw bytes rather
    # than an EncodedAction because it is sent directly by the executor each tick rather than
    # going through the queue as an action anyone planned.
    def encode_tick_end(self):
        return self._packet("tick_end", b"")


    # Echoes a position straight back. The server resends a teleport until the client confirms
    # it at the coordinates it asked for, so this replies with the position as applied rather
    # than with wherever the bot thought it was going. The trailing 0 is on_ground, false,
    # since a teleport lands us wherever the server decided and it will correct us if wrong.
    def acknowledge_position(self, position):

        self.connection._send_protocol_packet(
            self.play_serverbound["position_look"],
            struct.pack(">dddffB",position["x"], position["y"], position["z"], position["yaw"], position["pitch"], 0)
        )


    """
    --------------------------------------------------------------------------------------------
    Function Field Header - Inventory swap encoding
    --------------------------------------------------------------------------------------------
    Moving a tool into the hotbar is the most demanding thing AMP sends. A window click is not
    "put this there", it is a claim about what both slots contain, and the server verifies it.
    Get the claim wrong and it rejects the click and resynchronises the whole window.

    So the click carries a hashed description of each slot's contents, which is what
    _hashed_slot builds. A leading 0x00 means empty, 0x01 means present and is followed by the
    item's identity.

    Modified components are refused rather than guessed. An item with custom data hashes
    differently, and sending a hash the server disagrees with loses the click and possibly the
    item, so a plain error is the safer answer.

    The two slots are described swapped, source gets the destination's contents and vice versa,
    because the packet states the result of the click rather than its input.

    state_id is the window's sequence number, sent so the server can tell this click was made
    against the inventory it last sent rather than a stale view.
    --------------------------------------------------------------------------------------------
    """
    def _hashed_slot(self, item):
        # 0x00 means empty, 0x01 means present and is followed by the item's identity
        if item is None:
            return b"\x00"

        encode = self.connection._encode_varint
        components = item.get("components", {})
        removed = item.get("removed_components", ())

        # a modified item hashes differently, and a wrong hash loses the click
        if components:
            raise ValueError("Cannot hash modified item components for inventory swap")

        return (b"\x01" + encode(item["id"]) + encode(item["count"]) + encode(0)
                + encode(len(removed))+ b"".join(encode(component) for component in removed))


    def _encode_hotbar_swap(self, action, world_state):
        # 9-35 is the main inventory, below 9 is armour and crafting, 36+ is already the hotbar
        if action.source_slot not in range(9, 36):
            raise ValueError("Source slot must be in the player main inventory (9-35)")

        if action.hotbar_slot not in range(9):
            raise ValueError("Hotbar slot must be between 0 and 8")

        inventory = world_state["inventory"]
        destination = 36 + action.hotbar_slot
        source_item = inventory["slots"].get(action.source_slot)
        destination_item = inventory["slots"].get(destination)
        encode = self.connection._encode_varint

        # each slot is described with what it will hold after the swap, not before
        data = (encode(0) + encode(inventory["state_id"])
                + struct.pack(">hb", action.source_slot, action.hotbar_slot)
                + encode(2) + encode(2)
                + struct.pack(">h", action.source_slot) + self._hashed_slot(destination_item)
                + struct.pack(">h", destination) + self._hashed_slot(source_item)
                + b"\x00")

        return self._packet("window_click", data)


    """
    --------------------------------------------------------------------------------------------
    Function Header - Decode play
    --------------------------------------------------------------------------------------------
    The clientbound half. Matches a packet ID against the play table and returns a list of
    version-neutral events, empty for anything AMP does not care about, which is most of the
    protocol. Sound effects, particles and animations all arrive here and are dropped.

    A list rather than a single event because one packet can mean several things, a chunk
    carries block data alongside everything in it.

    Position is the one decode that also sends. The server expects a teleport confirmation
    carrying the ID it just sent, and without it the server keeps resending the teleport and
    eventually disconnects, so the acknowledgement belongs with the decode rather than being
    left to a caller who would have to know the rule.

    Block positions arrive packed the same way _packed_position writes them, so unpacking is
    shift then mask, with a sign correction because the fields are signed and the shift brings
    down zero bits.

    Chained ifs with returns rather than elif, since every branch exits. The order is roughly
    by frequency, position and health arrive constantly while inventory does not.
    --------------------------------------------------------------------------------------------
    """
    def decode_play(self, packet_id, payload):
        ids = self.play_clientbound

        # this decode also sends, the server disconnects if the teleport is never confirmed
        if packet_id == ids["position"]:
            teleport_id, offset = self.connection._decode_varint_bytes(payload, 0)
            x, y, z, dx, dy, dz, yaw, pitch, flags = struct.unpack_from(">ddddddffI", payload, offset)
            self.connection._send_protocol_packet(self.play_serverbound["teleport_confirm"], self.connection._encode_varint(teleport_id))
            return [PositionChanged(x, y, z, yaw, pitch, flags)]

        if packet_id == ids["update_health"]:
            health = struct.unpack_from(">f", payload, 0)[0]
            food, consumed = self.connection._decode_varint_bytes(payload, 4)
            saturation = struct.unpack_from(">f", payload, 4 + consumed)[0]
            return [HealthChanged(health, food, saturation)]

        if packet_id == ids["block_change"]:
            packed = struct.unpack_from(">q", payload, 0)[0]
            # x is the top field so the arithmetic shift carries its sign for free
            x = packed >> 38
            z = (packed >> 12) & 0x3FFFFFF
            y = packed & 0xFFF

            # z and y were masked, so their sign bit has to be restored by hand
            if z >= 1 << 25:
                z -= 1 << 26

            if y >= 1 << 11:
                y -= 1 << 12

            state_id, _ = self.connection._decode_varint_bytes(payload, 8)
            return [BlockChanged(x, y, z, state_id)]

        # one packet, many blocks, which is what a server sends when a tree falls or an
        # explosion goes off rather than one block_change each
        if packet_id == ids["multi_block_change"]:
            return self._decode_multi_block_change(payload)

        if packet_id == ids["map_chunk"]:
            return [self._decode_chunk(payload)]

        # the join packet, and the only place the starting dimension is stated, so it has to be
        # walked past several variable-length fields to be read
        if packet_id == ids["login"]:
            entity_id = struct.unpack_from(">i", payload, 0)[0]
            offset = 5
            world_count, consumed = self.connection._decode_varint_bytes(payload, offset)
            offset += consumed

            # the list of world names, skipped, only its length matters for finding what follows
            for _ in range(world_count):
                offset = self._skip_string(payload, offset)

            # max players, view distance and simulation distance, three varints of no interest
            for _ in range(3):
                _, consumed = self.connection._decode_varint_bytes(payload, offset)
                offset += consumed

            # three booleans, one byte each
            offset += 3
            dimension_id, _ = self.connection._decode_varint_bytes(payload, offset)
            return [SelfEntityIdentified(entity_id, dimension_id)]

        # death or a dimension change, both mean the tracked world is no longer the live one
        if packet_id == ids["respawn"]:
            dimension_id, _ = self.connection._decode_varint_bytes(payload, 0)
            return [WorldReset(dimension_id)]

        if packet_id == ids["spawn_entity"]:
            entity_id, consumed = self.connection._decode_varint_bytes(payload, 0)
            entity_uuid = str(uuid.UUID(bytes=payload[consumed:consumed + 16]))
            entity_type, type_size = self.connection._decode_varint_bytes(payload, consumed + 16)
            x, y, z = struct.unpack_from(">ddd", payload, consumed + 16 + type_size)
            return [EntitySpawned(entity_id, entity_uuid, entity_type, entity_name(self.version, entity_type), x, y, z)]

        # both carry the same delta, the look variant just appends rotation AMP ignores
        if packet_id in (ids["rel_entity_move"], ids["entity_move_look"]):
            entity_id, consumed = self.connection._decode_varint_bytes(payload, 0)
            # fixed point, 4096 units per block, which is what the shorts are scaled to
            dx, dy, dz = struct.unpack_from(">hhh", payload, consumed)
            return [EntityMoved(entity_id, dx / 4096, dy / 4096, dz / 4096)]

        if packet_id in (ids["entity_teleport"], ids["sync_entity_position"]):
            entity_id, consumed = self.connection._decode_varint_bytes(payload, 0)
            x, y, z = struct.unpack_from(">ddd", payload, consumed)
            return [EntityTeleported(entity_id, x, y, z)]

        if packet_id == ids["entity_destroy"]:
            count, consumed = self.connection._decode_varint_bytes(payload, 0)
            offset = consumed
            entity_ids = []
            for _ in range(count):
                entity_id, consumed = self.connection._decode_varint_bytes(payload, offset)
                offset += consumed
                entity_ids.append(entity_id)

            return [EntitiesRemoved(tuple(entity_ids))]

        if packet_id == ids["window_items"]:
            return [self._decode_window_items(payload)]

        if packet_id == ids["set_slot"]:
            return [self._decode_set_slot(payload)]

        if packet_id == ids["set_player_inventory"]:
            slot, consumed = self.connection._decode_varint_bytes(payload, 0)
            item, _ = self._decode_slot(payload, consumed)
            # this packet numbers slots differently to every other one, so remap before it
            # reaches world state and disagrees with the window it is meant to describe
            return [SlotChanged(0, None, self._player_inventory_screen_slot(slot), item)]

        if packet_id == ids["set_cursor_item"]:
            item, _ = self._decode_slot(payload, 0)
            return [SlotChanged(-1, None, -1, item)]

        if packet_id == ids["held_item_slot"]:
            slot, consumed = self.connection._decode_varint_bytes(payload, 0)

            if consumed != len(payload) or slot not in range(9):
                raise ConnectionError("Malformed selected-hotbar packet")

            return [HotbarSelected(slot)]

        return []


    """
    --------------------------------------------------------------------------------------------
    Function Field Header - Slot remapping and multi-block updates
    --------------------------------------------------------------------------------------------
    Two decoding problems that have nothing in common except being awkward.

    _player_inventory_screen_slot exists because set_player_inventory numbers slots in the
    player's own order, hotbar first at 0-8, while every other inventory packet uses screen
    order, where the hotbar is 36-44. Left unmapped, one packet would write the hotbar into the
    main inventory's slots and world state would disagree with itself depending on which packet
    arrived last. Armour is also reversed between the two orders, hence 44 - slot, and 40 is the
    offhand, which sits at 45 on screen. An out-of-range slot raises rather than being clamped,
    since a wrong slot silently corrupts the inventory view.

    _decode_multi_block_change unpacks a whole section's worth of changes from one packet. The
    section coordinate is packed into a single 64-bit value, X and Z in 22 bits each and Y in
    20, and each record then packs a state ID with a position local to that section, four bits
    per axis, in Y Z X order in the low twelve bits.

    _signed is needed because none of those fields are byte-aligned, so struct cannot do the
    sign extension and it has to be done by hand.

    The trailing-bytes check is a real guard rather than tidiness. Getting the record layout
    wrong would still produce plausible-looking blocks, and only the leftover bytes reveal it.
    --------------------------------------------------------------------------------------------
    """
    @staticmethod
    def _player_inventory_screen_slot(slot):
        # hotbar, first in player order and last in screen order
        if slot in range(9):
            return slot + 36

        # main inventory, the one range that numbers the same in both
        if slot in range(9, 36):
            return slot

        # armour, reversed between the two orderings
        if slot in range(36, 40):
            return 44 - slot

        # offhand
        if slot == 40:
            return 45

        raise ConnectionError(f"Invalid player inventory slot {slot}")


    def _skip_string(self, payload, offset):
        length, consumed = self.connection._decode_varint_bytes(payload, offset)
        end = offset + consumed + length

        if end > len(payload):
            raise ConnectionError("Truncated Java 26 string")

        return end


    def _decode_multi_block_change(self, payload):
        # section coordinate packed into one 64-bit value, X 22 bits, Z 22, Y 20
        packed_section = struct.unpack_from(">Q", payload, 0)[0]
        section_x = self._signed(packed_section >> 42, 22)
        section_z = self._signed((packed_section >> 20) & 0x3FFFFF, 22)
        section_y = self._signed(packed_section & 0xFFFFF, 20)
        count, consumed = self.connection._decode_varint_bytes(payload, 8)
        offset = 8 + consumed
        events = []

        # each record is a state ID with a section-local position in its low twelve bits,
        # four bits per axis in Y Z X order
        for _ in range(count):
            record, consumed = self.connection._decode_varint_bytes(payload, offset)
            offset += consumed

            events.append(BlockChanged(
                (section_x << 4) | ((record >> 8) & 0xF),
                (section_y << 4) | (record & 0xF),
                (section_z << 4) | ((record >> 4) & 0xF),
                record >> 12,
            ))

        # a misread layout still yields plausible blocks, only leftover bytes give it away
        if offset != len(payload):
            raise ConnectionError("Trailing bytes in Java 26 multi-block update")

        return events


    # none of the packed fields are byte-aligned, so struct cannot sign-extend them for us
    @staticmethod
    def _signed(value, bits):
        sign = 1 << (bits - 1)
        return value - (1 << bits) if value & sign else value


    """
    --------------------------------------------------------------------------------------------
    Function Field Header - Inventory decoding
    --------------------------------------------------------------------------------------------
    Slots are variable length, so each of these returns the new offset alongside its value and
    the caller threads that through. A count of zero means the slot is empty and nothing else
    follows, which is why that case returns early rather than reading an item ID that is not
    there.

    Items carry component counts for added and removed data components, custom names,
    enchantments and so on. AMP does not interpret them, but it has to walk past them to find
    where the next slot begins, so they are counted rather than parsed.

    _decode_window_items reads a whole window, _decode_set_slot a single one. Both exist
    because the server sends both, a full resync and an incremental update.
    --------------------------------------------------------------------------------------------
    """
    def _decode_slot(self, payload, offset):
        start = offset
        count, consumed = self.connection._decode_varint_bytes(payload, offset)
        offset += consumed

        if count == 0:
            return None, offset

        item_id, consumed = self.connection._decode_varint_bytes(payload, offset)
        offset += consumed
        added, consumed = self.connection._decode_varint_bytes(payload, offset)
        offset += consumed
        removed, consumed = self.connection._decode_varint_bytes(payload, offset)
        offset += consumed
        components = {}

        for _ in range(added):
            component_type, consumed = self.connection._decode_varint_bytes(payload, offset)
            offset += consumed
            if component_type in (1, 2, 3, 12, 19, 31, 41, 43, 46, 48, 63):
                value, consumed = self.connection._decode_varint_bytes(payload, offset)
                offset += consumed
                components[component_type] = value
            elif component_type in (13, 42):
                length, consumed = self.connection._decode_varint_bytes(payload, offset)
                offset += consumed
                values = []
                for _ in range(length):
                    identifier, consumed = self.connection._decode_varint_bytes(payload, offset)
                    offset += consumed
                    level, consumed = self.connection._decode_varint_bytes(payload, offset)
                    offset += consumed
                    values.append((identifier, level))

                components[component_type] = values

            else:
                raise ConnectionError(f"Unsupported Java 26 item component {component_type}")

        removed_types = []

        for _ in range(removed):
            component_type, consumed = self.connection._decode_varint_bytes(payload, offset)
            offset += consumed
            removed_types.append(component_type)

        return {
            "id": item_id, "name": item_name(self.version, item_id), "count": count,
            "components": components, "removed_components": removed_types,
            "wire": payload[start:offset].hex()
        }, offset


    def _decode_window_items(self, payload):
        window_id, consumed = self.connection._decode_varint_bytes(payload, 0)
        state_id, state_size = self.connection._decode_varint_bytes(payload, consumed)
        offset = consumed + state_size
        count, consumed = self.connection._decode_varint_bytes(payload, offset)
        offset += consumed
        slots = []

        for slot_index in range(count):
            item, offset = self._decode_slot(payload, offset)
            if item is not None:
                slots.append((slot_index, item))

        carried, offset = self._decode_slot(payload, offset)

        if offset != len(payload):
            raise ConnectionError("Trailing bytes in Java 26 inventory packet")

        return InventoryReplaced(window_id, state_id, tuple(slots), carried)


    def _decode_set_slot(self, payload):
        window_id, consumed = self.connection._decode_varint_bytes(payload, 0)
        state_id, state_size = self.connection._decode_varint_bytes(payload, consumed)
        offset = consumed + state_size
        slot = struct.unpack_from(">h", payload, offset)[0]
        item, offset = self._decode_slot(payload, offset + 2)

        if offset != len(payload):
            raise ConnectionError("Trailing bytes in Java 26 slot packet")

        return SlotChanged(window_id, state_id, slot, item)


    """
    --------------------------------------------------------------------------------------------
    Function Header - Decode chunk
    --------------------------------------------------------------------------------------------
    Splits a chunk packet into heightmaps and block data, then hands the block data to Chunk.

    Java 26 is why heightmaps are parsed here rather than inside Chunk. They used to be an NBT
    compound at the head of the chunk blob, and are now a separate length-prefixed list ahead
    of it, identified by number instead of name. That is what the name table is for, turning
    the numeric kind back into WORLD_SURFACE and friends so Chunk sees the same keys it always
    did, and why Chunk takes heightmaps as an argument.

    Unknown kinds are kept under a TYPE_n key rather than dropped, since skipping one would
    still require reading its length to find the next, and keeping it costs nothing.

    Both length checks exist because the counts are attacker-controlled in the sense that they
    come off the wire. struct.unpack_from past the end raises something far less clear than
    these, and a truncated chunk is worth naming precisely.
    --------------------------------------------------------------------------------------------
    """
    def _decode_chunk(self, payload):
        chunk_x, chunk_z = struct.unpack_from(">ii", payload, 0)
        offset = 8
        heightmap_count, consumed = self.connection._decode_varint_bytes(payload, offset)
        offset += consumed

        # Java 26 identifies heightmaps by number, this maps them back to the old names
        heightmap_names = {
            0: "WORLD_SURFACE_WG", 1: "WORLD_SURFACE", 2: "OCEAN_FLOOR_WG",
            3: "OCEAN_FLOOR", 4: "MOTION_BLOCKING", 5: "MOTION_BLOCKING_NO_LEAVES",
        }

        heightmaps = {}

        for _ in range(heightmap_count):
            kind, consumed = self.connection._decode_varint_bytes(payload, offset)
            offset += consumed
            count, consumed = self.connection._decode_varint_bytes(payload, offset)
            offset += consumed
            end = offset + count * 8

            if end > len(payload):
                raise ConnectionError("Truncated Java 26 heightmap")

            heightmaps[heightmap_names.get(kind, f"TYPE_{kind}")] = struct.unpack_from(f">{count}q", payload, offset)
            offset = end

        length, consumed = self.connection._decode_varint_bytes(payload, offset)
        offset += consumed
        end = offset + length

        if end > len(payload):
            raise ConnectionError("Truncated Java 26 chunk data")

        return ChunkLoaded(chunk_x, chunk_z, Chunk(payload[offset:end], self.version, hm=heightmaps))


    """
    --------------------------------------------------------------------------------------------
    Function Header - Handle login
    --------------------------------------------------------------------------------------------
    Drives the Login state, one packet per call. The bool it returns is the state machine, True
    means login is finished and the connection has moved on to Play, False means stay in Login
    and keep reading.

    Unlike Play, an unrecognised packet raises rather than being ignored. Login is a short
    fixed handshake, so anything unexpected means the two sides disagree about where they are,
    and continuing from there would fail later and less clearly.

    Compression is set here and takes effect immediately, every packet after this one is framed
    differently, which is why the threshold is written straight onto the connection.

    Success sends the acknowledgement and runs Configuration inline before returning True, so
    by the time the caller sees True the connection really is ready for Play.

    Encryption is the one branch that needs an account. It parses the server ID, public key and
    verify token, checks both length-prefixed fields actually arrived in full rather than
    trusting the prefixes, then hands off to the connection to do the key exchange.
    --------------------------------------------------------------------------------------------
    """
    def handle_login(self, packet_id, payload, session=None):
        ids = self.login_clientbound

        # takes effect immediately, every following packet uses the compressed framing
        if packet_id == ids["compress"]:
            threshold, _ = self.connection._decode_varint_bytes(payload, 0)
            self.connection.enable_compression(threshold)
            return False

        if packet_id == ids["cookie_request"]:
            key, consumed = self._decode_string(payload)

            if consumed != len(payload):
                raise ConnectionError("Malformed Login cookie request")

            response = self.connection._encode_string(key) + b"\x00"
            self.connection._send_protocol_packet(self.login_serverbound["cookie_response"], response)

            return False

        # Configuration runs inline, so True means genuinely ready for Play, not merely logged in
        if packet_id == ids["success"]:
            self.connection._send_protocol_packet(self.login_serverbound["login_acknowledged"])
            self.handle_configuration()
            return True

        if packet_id == ids["disconnect"]:
            raise ConnectionError("Server disconnected during Login")

        if packet_id == ids["encryption_begin"]:
            if session is None:
                raise ConnectionError("Server requires a Microsoft-authenticated session")

            server_id, offset = self._decode_string(payload)
            key_length, consumed = self.connection._decode_varint_bytes(payload, offset)
            offset += consumed
            public_key = payload[offset:offset + key_length]
            offset += key_length
            token_length, consumed = self.connection._decode_varint_bytes(payload, offset)
            offset += consumed
            verify_token = payload[offset:offset + token_length]

            # trust the bytes that arrived, not the prefixes claiming how many there were
            if len(public_key) != key_length or len(verify_token) != token_length:
                raise ConnectionError("Malformed encryption request")

            self.connection.authenticate_server(server_id, public_key, verify_token)

            return False

        if packet_id == ids["login_plugin_request"]:
            raise ConnectionError("Unsupported Login plugin request")

        # Login is a short fixed handshake, so anything unexpected means the sides disagree
        raise ConnectionError(f"Unexpected Login packet id {packet_id:#x}")


    """
    --------------------------------------------------------------------------------------------
    Function Header - Handle configuration
    --------------------------------------------------------------------------------------------
    Runs the Configuration state to completion, and is the one place in the adapter that owns a
    read loop rather than being handed a packet. Configuration is a conversation of unknown
    length, the server sends registries, tags and packs until it decides it is done, so there is
    no fixed count to unroll and the loop only exits on finish_configuration.

    Client settings are sent first, before anything is read, since the server expects them
    before it will proceed.

    Most branches are polite refusals. Known packs is answered with an empty list and resource
    packs are answered with a status the server accepts, because AMP renders nothing and has no
    use for either, but silence would stall the handshake. Keep-alive and ping are echoed back
    unchanged, which is exactly what they ask for.

    Anything unrecognised is skipped rather than raising, unlike Login. Configuration carries
    large amounts of registry data AMP has no interest in, and refusing to continue over an
    unfamiliar registry packet would break the connection for no reason.
    --------------------------------------------------------------------------------------------
    """
    def handle_configuration(self):
        # sent before reading anything, the server waits for these before continuing
        self._send_settings()

        # unknown length, the server decides when it is done, so this can only exit on finish
        while True:
            packet_id, payload = self.connection._read_packet()
            ids = self.configuration_clientbound

            if packet_id == ids["finish_configuration"]:
                self.connection._send_protocol_packet(self.configuration_serverbound["finish_configuration"])
                return

            if packet_id == ids["keep_alive"]:
                self.connection._send_protocol_packet(self.configuration_serverbound["keep_alive"], payload)

            elif packet_id == ids["ping"]:
                self.connection._send_protocol_packet(self.configuration_serverbound["pong"], payload)

            elif packet_id == ids["cookie_request"]:
                key, _ = self._decode_string(payload)
                response = self.connection._encode_string(key) + b"\x00"
                self.connection._send_protocol_packet(self.configuration_serverbound["cookie_response"], response)

            # empty list, AMP renders nothing, but silence would stall the handshake
            elif packet_id == ids["select_known_packs"]:
                self.connection._send_protocol_packet(self.configuration_serverbound["select_known_packs"], b"\x00")

            elif packet_id == ids["add_resource_pack"]:
                pack_id = payload[:16]

                if len(pack_id) != 16:
                    raise ConnectionError("Malformed resource-pack request")

                self.connection._send_protocol_packet(
                    self.configuration_serverbound["resource_pack_receive"],
                    pack_id + self.connection._encode_varint(1),
                )

            elif packet_id == ids["code_of_conduct"]:
                self.connection._send_protocol_packet(self.configuration_serverbound["accept_code_of_conduct"])
            elif packet_id == ids["disconnect"]:
                raise ConnectionError("Server disconnected during Configuration")
            elif packet_id == ids["transfer"]:
                raise ConnectionError("Server transfer requested during Configuration")

    """
    --------------------------------------------------------------------------------------------
    Function Field Header - Configuration helpers
    --------------------------------------------------------------------------------------------
    Client settings are what a real client would send from its options screen, locale, view
    distance, chat mode, skin parts and main hand. AMP does not render, so most of it is
    whatever keeps the server happy, but view distance is real, 10 chunks is what decides how
    much world arrives to path through.

    The literal bytes are packed flags rather than magic, 0x7f is every skin part enabled and
    the trailing values are chat and filtering preferences.

    _decode_string is the length-prefixed string every state uses, and it checks the prefix
    against what actually arrived rather than trusting it, so a truncated packet is named here
    instead of producing a short string that fails somewhere further along.
    --------------------------------------------------------------------------------------------
    """
    def _send_settings(self):
        payload = (
            self.connection._encode_string("en_us")
            # view distance in chunks, the one setting here that genuinely matters to AMP
            + struct.pack(">b", 10)
            + self.connection._encode_varint(0)
            + b"\x01\x7f"
            + self.connection._encode_varint(1)
            + b"\x00\x01"
            + self.connection._encode_varint(0)
        )

        self.connection._send_protocol_packet( self.configuration_serverbound["settings"], payload)


    def _decode_string(self, payload):
        length, consumed = self.connection._decode_varint_bytes(payload, 0)
        end = consumed + length
        if end > len(payload):
            raise ConnectionError("Truncated protocol string")

        return payload[consumed:end].decode("utf-8"), end
