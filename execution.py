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
from collections import deque
import time

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
    # Serverbound Play packet IDs for protocols 762 (1.19.4) and 763 (1.20/1.20.1).
    play_ids = {
        "chat": 0x05,
        "position": 0x14,
        "look": 0x16,
        "block_dig": 0x1D,
        "entity_action": 0x1E,
        "swing": 0x2F,
        "block_place": 0x31,
        "use_item": 0x32,
    }

    def __init__(self, connection, game_mode, behavior_mode):
        self._connection = connection
        self._command_queue = deque()
        self._game_mode = game_mode
        self._behavior_mode = behavior_mode
        self._sequence = 0

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
        self._command_queue.append(command)

    def execute_queue(self):
        while self._command_queue:
            cmd = self._command_queue.popleft()
            self._execute(cmd)

    def _execute(self, command):
        action = command.get("action")

        if action == "move":
            x, y, z = command["x"], command["y"], command["z"]
            packet = self._create_movement_packet(x, y, z)
            self._connection._send(packet)

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
            packet = self._create_entity_action_packet(0 if command.get("sneaking") else 1)
            self._connection._send(packet)

        elif action == "mine":
            x, y, z = command["x"], command["y"], command["z"]
            face = command.get("face", 1)
            start = self._create_digging_packet(0, x, y, z, face)
            finish = self._create_digging_packet(2, x, y, z, face)
            self._connection._send(start)
            self._connection._send(finish)

        elif action == "place":
            x, y, z = command["x"], command["y"], command["z"]
            face = command.get("face", 1)
            packet = self._create_place_packet(x, y, z, face)
            self._connection._send(packet)

        elif action == "use_item":
            packet = self._create_use_item_packet(command.get("hand", 0))
            self._connection._send(packet)

        print(f"Executed {command} in {self._game_mode} mode as {self._behavior_mode} bot.")

    """
    --------------------------------------------------------------------------------------------
    Function Field Header - Creating Packets Based On MC Protocol API
    --------------------------------------------------------------------------------------------
    """

    """
    --------------------------------------------------------------------------------------------
    Function Header - Movement packet serialization
    --------------------------------------------------------------------------------------------
    Set Player Position, serverbound packet 0x14 in protocol 762 (1.19.4).
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
        packet_id = self._connection._encode_varint(self.play_ids["chat"])
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
        packet_id = self._connection._encode_varint(self.play_ids["swing"])
        data = self._connection._encode_varint(hand)
        length = self._connection._encode_varint(len(packet_id + data))

        return length + packet_id + data

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

    # ------------------------------------------------------------------------------------------
