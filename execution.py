"""
--------------------------------------------------------------------------------------------
Execution Module
--------------------------------------------------------------------------------------------
Translates structured commands from the command queue into raw Minecraft protocol packets
and sends them over the connection. Single entry point is execute_queue() which drains
the queue and dispatches each command.
--------------------------------------------------------------------------------------------
"""
# imports
import struct
from collections import deque
import time

"""
--------------------------------------------------------------------------------------------
Class Header - Execution layer


execution_loop runs on its own thread calling execute_queue every 0.05s, execute_queue 
drains the command queue and sends packets via _connection._send. Nothing needs rewiring, 
the thread drives execution automatically once _start_execution is called in start().
--------------------------------------------------------------------------------------------
"""
class Execute:

    def __init__(self, connection, game_mode, behavior_mode):
        self._connection = connection
        self._command_queue = deque()
        self._game_mode = game_mode
        self._behavior_mode = behavior_mode

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

        print(f"Executed {command} in {self._game_mode} mode as {self._behavior_mode} bot.")

    """
    --------------------------------------------------------------------------------------------
    Function Header - Movement packet serialization
    --------------------------------------------------------------------------------------------
    Set Player Position, serverbound packet 0x13 in protocol 762 (1.20.1).
    Fields: x (double), y (double), z (double), on_ground (bool).
    All big-endian. Wrapped in the standard length + packet_id envelope.

    on_ground is True for all pathfinder steps since the pathfinder only generates
    positions where the block below is solid.
    --------------------------------------------------------------------------------------------
    """
    def _create_movement_packet(self, x, y, z, on_ground=True):
        packet_id = self._connection._encode_varint(0x13)
        data = struct.pack(">ddd", x, y, z) + (b"\x01" if on_ground else b"\x00")
        length = self._connection._encode_varint(len(packet_id + data))
        return length + packet_id + data

    """
    --------------------------------------------------------------------------------------------
    Function Header - Chat packet serialization
    --------------------------------------------------------------------------------------------
    Chat Message, serverbound packet 0x05 in protocol 762 (1.20.1).
    Fields: message (string, max 256 chars), timestamp (long), salt (long),
    signature (optional bytes), message count (varint), acknowledged (bit set).

    For an offline mode server the signature can be empty. Timestamp and salt are
    required by the server to validate the message even in offline mode, so we send
    the current system time in milliseconds and a zero salt.
    --------------------------------------------------------------------------------------------
    """
    def _create_chat_packet(self, message):
        packet_id = self._connection._encode_varint(0x05)
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

    # ------------------------------------------------------------------------------------------