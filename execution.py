"""
--------------------------------------------------------------------------------------------
Execution Module - Wrapper for packet sending through acces to bot
--------------------------------------------------------------------------------------------
Translates structured commands from the command queue into raw Minecraft protocol packets
and sends them over the connection. Single entry point is execute_queue() which drains
the queue and dispatches each command.

Every command enqueued is a dict with an action key and any additional fields the action
needs, for example {"action": "move", "x": x, "y": y, "z": z} or {"action": "mine",
"x": x, "y": y, "z": z, "face": 1}. _execute pulls the action key to dispatch and then
reads the remaining fields by name.

behavior_mode in Execute is the in-game behavior descriptor (passive, aggressive, neutral)
passed from the config, not input mode. It is only here to print actions of that behavior
mode, but the action is decided with the behavior in mind before the command is enqueued.
--------------------------------------------------------------------------------------------
"""
# imports
import struct
import threading
from collections import deque
import time
from protocol_data import packet_ids_for_protocol

"""
--------------------------------------------------------------------------------------------
Class Header - Execution layer
--------------------------------------------------------------------------------------------
execution_loop runs on its own thread calling execute_queue every 0.05s, execute_queue 
drains the command queue and sends packets via _connection._send. Nothing needs rewiring, 
the thread drives execution automatically once _start_execution is called in start().
--------------------------------------------------------------------------------------------
"""
class Execute:
    def __init__(self, connection, game_mode, behavior_mode, world_state=None):
        self._connection = connection
        self._command_queue = deque()
        self._game_mode = game_mode
        self._behavior_mode = behavior_mode
        self._world_state = world_state
        self.play_ids = packet_ids_for_protocol(
            connection._protocol_version, "serverbound"
        )
        self._sequence = 0
        self._condition = threading.Condition()
        self._active = False
        self._results = []

    def _next_sequence(self):
        sequence = self._sequence
        self._sequence += 1
        return sequence

    """
    --------------------------------------------------------------------------------------------
    Function Header - Bot methods field
    --------------------------------------------------------------------------------------------
    Using command queue we order the commands, using popleft to execute in FIFO order.
    
    execution queue is a key producing wrapper for the packets we send in execute
    --------------------------------------------------------------------------------------------
    """
    def enque_command(self, command):
        with self._condition:
            self._command_queue.append(command)
            self._condition.notify_all()

    def result_count(self):
        with self._condition:
            return len(self._results)

    def wait_until_idle(self, result_start=0, timeout=30):
        """Wait for queued and active work, returning results added since result_start."""
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._command_queue or self._active:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return self._results[result_start:] + [{
                        "action": "batch", "success": False,
                        "message": "Timed out waiting for queued actions",
                    }]
                self._condition.wait(remaining)
            return list(self._results[result_start:])

    def execute_queue(self):
        """Execute at most one command; the Bot loop calls this once per 50 ms tick."""
        with self._condition:
            if not self._command_queue:
                return None
            command = self._command_queue.popleft()
            self._active = True
        try:
            result = self._execute(command)
        except Exception as error:
            result = {
                "action": command.get("action"), "success": False,
                "message": f"{type(error).__name__}: {error}",
            }
            raise
        finally:
            with self._condition:
                self._results.append(result)
                self._active = False
                self._condition.notify_all()
        return result

    def _world_block(self, position):
        if self._world_state is None:
            return None
        x, y, z = position
        chunk = self._world_state["map"].get((x >> 4, z >> 4))
        return chunk.get_block(x, y, z) if chunk else None

    def _wait_for_block(self, position, expected, timeout=2):
        if self._world_state is None:
            return True
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            block = self._world_block(position)
            if expected(block):
                return True
            time.sleep(0.05)
        return False

    def _execute(self, command):
        action = command.get("action")
        success = True
        message = f"{action} packet sent"

        if action == "move":
            x, y, z = command["x"], command["y"], command["z"]
            packet = self._create_movement_packet(x, y, z)
            self._connection._send(packet)
            if self._world_state is not None:
                self._world_state["position"].update({"x": x, "y": y, "z": z})

        elif action == "chat":
            packet = self._create_chat_packet(command["message"])
            self._connection._send(packet)

        elif action == "look":
            packet = self._create_look_packet(command["yaw"], command["pitch"])
            self._connection._send(packet)

        elif action == "swing":
            packet = self._create_swing_packet(command.get("hand", 0))
            self._connection._send(packet)

        elif action == "sneak":
            entity_id = self._world_state.get("self_entity_id", 0) if self._world_state else 0
            packet = self._create_entity_action_packet(
                0 if command.get("sneaking") else 1, entity_id=entity_id or 0
            )
            self._connection._send(packet)

        elif action == "attack":
            self._connection._send(self._create_attack_packet(command["entity_id"]))
            message = f"Attack sent to entity {command['entity_id']}"

        elif action == "mine":
            x, y, z = command["x"], command["y"], command["z"]
            face = command.get("face", 1)
            start = self._create_digging_packet(0, x, y, z, face)
            self._connection._send(start)
            if self._game_mode != "creative":
                time.sleep(command.get("duration", 0))
                finish = self._create_digging_packet(2, x, y, z, face)
                self._connection._send(finish)
            success = self._wait_for_block(
                (x, y, z), lambda block: block in ("air", "cave_air", "void_air")
            )
            message = (
                f"Mined block at {(x, y, z)}" if success
                else f"Block at {(x, y, z)} did not disappear"
            )

        elif action == "place":
            x, y, z = command["x"], command["y"], command["z"]
            face = command.get("face", 1)
            packet = self._create_place_packet(x, y, z, face)
            self._connection._send(packet)
            target = tuple(command.get("target", ()))
            if len(target) == 3 and command.get("block"):
                success = self._wait_for_block(
                    target, lambda block: block == command["block"]
                )
                message = (
                    f"Placed {command['block']} at {target}" if success
                    else f"Expected {command['block']} did not appear at {target}"
                )

        elif action == "use_item":
            packet = self._create_use_item_packet(command.get("hand", 0))
            self._connection._send(packet)

        elif action == "select_hotbar":
            slot = command["slot"]
            self._connection._send(self._create_held_item_packet(slot))
            if self._world_state is not None:
                self._world_state["inventory"]["selected_hotbar_slot"] = slot

        elif action == "swap_hotbar":
            packet = self._create_hotbar_swap_packet(
                command["source_slot"], command["hotbar_slot"]
            )
            self._connection._send(packet)

        print(f"Executed {command} in {self._game_mode} mode as {self._behavior_mode} bot.")
        return {"action": action, "success": success, "message": message}

    """
    --------------------------------------------------------------------------------------------
    Function Field Header - Creating Packets Based On MC Protocol API
    --------------------------------------------------------------------------------------------
    """

    """
    --------------------------------------------------------------------------------------------
    Function Header - Movement packet serialization
    --------------------------------------------------------------------------------------------
    Set Player Position, using the generated packet ID for the selected protocol.
    Fields: x (double), y (double), z (double), on_ground (bool).
    All big-endian. Wrapped in the standard length + packet_id envelope.

    on_ground is True for all pathfinder steps since the pathfinder only generates
    positions where the block below is solid.
    --------------------------------------------------------------------------------------------
    """
    def _create_movement_packet(self, x, y, z, on_ground=True):
        packet_id = self._connection._encode_varint(self.play_ids["position"])
        data = struct.pack(">ddd", x, y, z) + (b"\x01" if on_ground else b"\x00")
        length = self._connection._encode_varint(len(packet_id + data))
        return length + packet_id + data

    """
    --------------------------------------------------------------------------------------------
    Function Header - Chat packet serialization
    --------------------------------------------------------------------------------------------
    Chat Message, serverbound packet 0x05 in protocol 762 (1.19.4).
    Fields: message (string, max 256 chars), timestamp (long), salt (long),
    signature (optional bytes), message count (varint), acknowledged (bit set).

    For an offline mode server the signature can be empty. Timestamp and salt are
    required by the server to validate the message even in offline mode, so we send
    the current system time in milliseconds and a zero salt.
    --------------------------------------------------------------------------------------------
    """
    def _create_chat_packet(self, message):
        packet_id = self._connection._encode_varint(self.play_ids["chat_message"])
        msg_bytes = message.encode("utf-8")
        msg = self._connection._encode_varint(len(msg_bytes)) + msg_bytes
        # timestamp in milliseconds as a big-endian long
        timestamp = struct.pack(">q", int(time.time() * 1000))
        # zero salt
        salt = struct.pack(">q", 0)
        # no signature, varint 0
        sig = self._connection._encode_varint(0)
        # message count varint 0, acknowledged bit set of 20 zero bits
        msg_count = self._connection._encode_varint(0)
        acknowledged = b"\x00" * 3
        data = msg + timestamp + salt + sig + msg_count + acknowledged
        length = self._connection._encode_varint(len(packet_id + data))
        return length + packet_id + data

    """
    --------------------------------------------------------------------------------------------
    Function Header - Look packet serialization
    --------------------------------------------------------------------------------------------
    Set Player Rotation, serverbound packet 0x16 in protocol 762 (1.19.4).
    Fields: yaw (float), pitch (float), on_ground (bool).
    Yaw is degrees clockwise from south (0=south, 90=west, 180=north, 270=east).
    Pitch is degrees from horizontal (-90=up, 90=down).
    --------------------------------------------------------------------------------------------
    """
    def _create_look_packet(self, yaw, pitch, on_ground=True):
        packet_id = self._connection._encode_varint(self.play_ids["look"])
        data = struct.pack(">ff", yaw, pitch) + (b"\x01" if on_ground else b"\x00")
        length = self._connection._encode_varint(len(packet_id + data))

        return length + packet_id + data

    """
    --------------------------------------------------------------------------------------------
    Function Header - Swing packet serialization
    --------------------------------------------------------------------------------------------
    Swing Arm, serverbound packet 0x2F in protocol 762 (1.19.4).
    Fields: hand (varint), 0 for main hand, 1 for off hand.
    Triggers the arm swing animation and is required before attack damage registers.
    --------------------------------------------------------------------------------------------
    """
    def _create_swing_packet(self, hand=0):
        packet_id = self._connection._encode_varint(self.play_ids["arm_animation"])
        data = self._connection._encode_varint(hand)
        length = self._connection._encode_varint(len(packet_id + data))

        return length + packet_id + data

    def _create_attack_packet(self, entity_id):
        packet_id = self._connection._encode_varint(self.play_ids["use_entity"])
        data = (
            self._connection._encode_varint(entity_id)
            + self._connection._encode_varint(1)
            + b"\x00"
        )
        return self._connection._encode_varint(len(packet_id + data)) + packet_id + data

    """
    --------------------------------------------------------------------------------------------
    Function Header - Entity action packet serialization
    --------------------------------------------------------------------------------------------
    Player Command, serverbound packet 0x1E in protocol 762 (1.19.4).
    Fields: entity_id (varint), action_id (varint), jump_boost (varint, always 0).
    Action IDs: 0 = start sneaking, 1 = stop sneaking, 3 = start sprinting, 4 = stop sprinting.
    Entity ID is the bot's own entity ID, set to 0 here as a safe default for offline servers.
    --------------------------------------------------------------------------------------------
    """
    def _create_entity_action_packet(self, action_id, entity_id=0):
        packet_id = self._connection._encode_varint(self.play_ids["entity_action"])
        data = (self._connection._encode_varint(entity_id) +
                self._connection._encode_varint(action_id) +
                self._connection._encode_varint(0))
        length = self._connection._encode_varint(len(packet_id + data))

        return length + packet_id + data

    """
    --------------------------------------------------------------------------------------------
    Function Header - Digging packet serialization
    --------------------------------------------------------------------------------------------
    Player Action, serverbound packet 0x1D in protocol 762 (1.19.4).
    Fields: status (varint), location (packed long), face (byte), sequence (varint).
    Status 0 = start digging, 1 = cancel digging, 2 = finish digging.
    Location is packed as x<<38 | z<<12 | y matching the block update format.
    Face is the block face being hit: 0=bottom, 1=top, 2=north, 3=south, 4=west, 5=east.
    Two packets are sent per mine action, status 0 to start and status 2 to finish.
    For creative mode a single status 0 is sufficient.
    --------------------------------------------------------------------------------------------
    """
    def _create_digging_packet(self, status, x, y, z, face=1):
        packet_id = self._connection._encode_varint(self.play_ids["block_dig"])
        packed = ((x & 0x3FFFFFF) << 38) | ((z & 0x3FFFFFF) << 12) | (y & 0xFFF)
        data = (self._connection._encode_varint(status) +
                struct.pack(">Q", packed) +
                struct.pack(">b", face) +
                self._connection._encode_varint(self._next_sequence()))
        length = self._connection._encode_varint(len(packet_id + data))

        return length + packet_id + data

    """
    --------------------------------------------------------------------------------------------
    Function Header - Place packet serialization
    --------------------------------------------------------------------------------------------
    Player Block Placement, serverbound packet 0x31 in protocol 762 (1.19.4).
    Fields: hand (varint), location (packed long), face (varint), cursor x/y/z (float), 
    inside_block (bool), sequence (varint).
    Cursor position is the crosshair position on the face being clicked, 0.5 0.5 0.5
    targets the center of the face which is safe for all placement contexts.
    --------------------------------------------------------------------------------------------
    """
    def _create_place_packet(self, x, y, z, face=1, hand=0):
        packet_id = self._connection._encode_varint(self.play_ids["block_place"])
        packed = ((x & 0x3FFFFFF) << 38) | ((z & 0x3FFFFFF) << 12) | (y & 0xFFF)
        data = (self._connection._encode_varint(hand) +
                struct.pack(">Q", packed) +
                self._connection._encode_varint(face) +
                struct.pack(">fff", 0.5, 0.5, 0.5) +
                b"\x00" +
                self._connection._encode_varint(self._next_sequence()))
        length = self._connection._encode_varint(len(packet_id + data))

        return length + packet_id + data

    """
    --------------------------------------------------------------------------------------------
    Function Header - Use item packet serialization
    --------------------------------------------------------------------------------------------
    Use Item, serverbound packet 0x32 in protocol 762 (1.19.4).
    Fields: hand (varint), sequence (varint). Hand 0 is main, 1 is off hand.
    Triggers item use for the currently held item, food eating, bow drawing, etc.
    --------------------------------------------------------------------------------------------
    """
    def _create_use_item_packet(self, hand=0):
        packet_id = self._connection._encode_varint(self.play_ids["use_item"])
        data = (self._connection._encode_varint(hand) +
                self._connection._encode_varint(self._next_sequence()))
        length = self._connection._encode_varint(len(packet_id + data))

        return length + packet_id + data

    def _create_held_item_packet(self, slot):
        if slot not in range(9):
            raise ValueError("Hotbar slot must be between 0 and 8")
        packet_id = self._connection._encode_varint(self.play_ids["held_item_slot"])
        data = struct.pack(">h", slot)
        length = self._connection._encode_varint(len(packet_id + data))
        return length + packet_id + data

    def _encode_slot(self, item):
        if item is None:
            return b"\x00"
        if "wire" in item:
            return bytes.fromhex(item["wire"])
        return (
            b"\x01" + self._connection._encode_varint(item["id"])
            + struct.pack(">b", item["count"]) + b"\x00"
        )

    def _create_hotbar_swap_packet(self, source_slot, hotbar_slot):
        if source_slot not in range(9, 36):
            raise ValueError("Source slot must be in the player main inventory (9-35)")
        if hotbar_slot not in range(9):
            raise ValueError("Hotbar slot must be between 0 and 8")
        inventory = self._world_state["inventory"]
        destination_slot = 36 + hotbar_slot
        source_item = inventory["slots"].get(source_slot)
        destination_item = inventory["slots"].get(destination_slot)
        packet_id = self._connection._encode_varint(self.play_ids["window_click"])
        data = (
            b"\x00" + self._connection._encode_varint(inventory["state_id"])
            + struct.pack(">h", source_slot) + struct.pack(">b", hotbar_slot)
            + self._connection._encode_varint(2) + self._connection._encode_varint(2)
            + struct.pack(">h", source_slot) + self._encode_slot(destination_item)
            + struct.pack(">h", destination_slot) + self._encode_slot(source_item)
            + b"\x00"
        )
        length = self._connection._encode_varint(len(packet_id + data))
        return length + packet_id + data

    # ------------------------------------------------------------------------------------------
