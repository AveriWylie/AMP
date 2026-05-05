"""
--------------------------------------------------------------------------------------------
Bot Module
--------------------------------------------------------------------------------------------
TCP connection, varint encoding/decoding, handshake/login, a keepalive loop, validation
layers for bot configuration, and a singular entry point to start all these processes
(bot.start()). With this architecture the only external interface / process of an external
interface is the code blow:

bot = Bot(config)
bot.start()
--------------------------------------------------------------------------------------------
"""
# imports
import socket
import threading
import struct
import time
from collections import deque
from execution import Execute
from pathfinder import Pathfinder
from planner import Planner
from chunk import Chunk

"""
--------------------------------------------------------------------------------------------
Class Header - Bot initialization
--------------------------------------------------------------------------------------------
"""
class Bot:
    # above all constants, an initialization of version array to define a constant with it.
    arr = []
    with open("TEXT/mc_versions.txt", "r+", encoding="utf-8", errors="ignore") as f:
        for line in f:
            arr.append(line.strip())

    """
    --------------------------------------------------------------------------------------------
    Function Header - Constants field
    --------------------------------------------------------------------------------------------
    Within Bot to avoid duplication of constants for each Bot object. Explicitely we are saying 
    username/host has no restricted range of allowed possibilities (same as saying "username": 
    None ... etc.).
    --------------------------------------------------------------------------------------------
    """
    allowed_values = {"game_mode": {"survival", "creative", "superflat", "adventure", "spectator"},
                      "behavior_mode": {"passive", "aggressive", "neutral"}, "port": range(1024, 65536),
                      "version": arr}

    default_values = {"host": "localhost", "port": 25565, "username": "Guest", "version": "1.21.4",
        "game_mode": "survival", "behavior_mode": "passive"}

    # ------------------------------------------------------------------------------------------

    """
    --------------------------------------------------------------------------------------------
    Function Header - Validation Layer
    --------------------------------------------------------------------------------------------
    For configuration input which is retrived interactively, (meaning that it is 
    quite simple and need not be more complicated then this) the range of possible 
    inputs is small and easily describable through sets, and existence notation which 
    translates to logic directly, and is thus, easily codable / understandable.
    --------------------------------------------------------------------------------------------
    """
    def _validate_input(self):
        self._valid_flags = {}

        for key in self.default_values:
            # key is a string as we retrieve it by iterating over default values an f-string
            # which lets you embed a variable inside a string. So f"_{key}" produces "_host"
            # when key is "host", which matches the actual attribute name self._host. To
            # validate we only use string data types.
            value = getattr(self, f"_{key}")
            allowed = self.allowed_values.get(key)

            # we handle param keys that have no restricted range as mentioned above
            # if theres no restricted range then is_valid is true for any none empty input
            if allowed is None:
                is_valid = value is not None
            else:
                is_valid = value in allowed

            self._valid_flags[key] = is_valid

            if not is_valid:
                setattr(self, f"_{key}", self.default_values[key])

    def __init__(self, config):
        # config.get("..") = config[""] in esence and efficiency however .get
        # checks self._host = config.get("host", None) implicitely which will be
        # needed in the validation layer we can now handle error input later on
        # self._ means outside of this class you cannot internally reference the object
        # to have complete encapsulation we provide implmentation for any needed change to
        # internal data, seperating the implmentation from interface
        self._host = config.get("host")
        self._port = config.get("port")
        self._version = config.get("version")
        self._username = config.get("username")
        self._game_mode = config.get("game_mode")
        self._behavior_mode = config.get("behavior_mode")
        # empty tracker of world state, context bot needs collected by other
        # areas of the project
        self._world_state = {
            "position": {"x": 0.0, "y": 0.0, "z": 0.0, "yaw": 0.0, "pitch": 0.0},
            "health": 20.0,
            "food": 20,
            "entities": {},
            "map": {},
            "blocks": {}
        }
        # implemented with a deqeue or for efficient popping
        self._command_queue = deque()
        self._valid_flags = {}
        # guarantee the object is always in a valid state immediately after
        # creation with config get
        self._validate_input()
        # keyword args must come after all positional arguments in python
        self._connection = Connection(self._host, self._port, self._version, self._username,
                                      on_failure=self._handle_failure, protocol_version=762, packet_handler = self._on_packet)
        self._input_mode = None
        self._pathfinder = Pathfinder(self._world_state)
        self._executor = Execute(self._connection, game_mode=config.get("game_mode","survival"),
                                 behavior_mode=config.get("behavior_mode", "neutral"))
        self._execution_started = False
        # api key loaded from file so it is never hardcoded
        try:
            with open("api_key.txt", "r") as f:
                api_key = f.read().strip()
        except FileNotFoundError:
            api_key = None
            print("Warning: api_key.txt not found, planner will not function")

        self._planner = Planner(self._world_state, self._pathfinder, api_key)

    # entrance for cli
    def start(self):
        if not all(self._valid_flags.values()):
            invalid = [k for k, v in self._valid_flags.items() if not v]
            print(f"Warning: fields fell back to defaults: {invalid}")

        try:
            # rather then call bot.start as this is post validation -> less overhead
            self._connection.connect()
            self._start_execution()
            print(f"Bot '{self._username}' started on {self._host}:{self._port}")

        except ConnectionError as e:
            print(f"Failed to start: {e}")
            self._handle_failure(e)

        except Exception as e:
            print(f"Unexpected error during start: {e}")
            self._connection.disconnect()

    """
    --------------------------------------------------------------------------------------------
    Function Field Header - packet handler
    --------------------------------------------------------------------------------------------
    handles non-keepalive response packets for world state an other data to flow to the bot. 
    Conventional to minecraft:
    
    A few things to be aware of: the packet IDs 0x26, 0x40, 0x1D are for protocol 762 
    (1.20.1) — double check against wiki.vg if you're targeting a different version.
    
    tldr ...
    gets all world state data as packets are handled, sends any packets necessary for connection.
    
    Chunk and block update handlers:
    
    Chunk X and Z come as two big-endian signed ints at the start of the payload, the rest is
    the chunk data blob that Chunk parses. Stored in world_state["map"] keyed by (cx, cz) so
    the pathfinder can retrieve the right chunk for any absolute coordinate.

    Block update from path finder patches a single block into the already-stored chunk so world 
    state stays accurate without a full re-parse. Block position is packed into a single long as
    x<<38 | z<<12 | y (wiki.vg protocol 762).
    --------------------------------------------------------------------------------------------
    """
    def _on_packet(self, packet_id, payload):
        if packet_id == 0x38:
            self._handle_position(payload)
        elif packet_id == 0x1D:
            self._handle_health(payload)
        elif packet_id == 0x01:
            self._handle_entity(payload)
        elif packet_id == 0x26:
            self._handle_chunk(payload)
        elif packet_id == 0x40:
            self._handle_block_update(payload)

    # x, y, z are 8-byte doubles, yaw and pitch are 4-byte floats
    # all big-endian
    def _handle_position(self, payload):
        x, y, z = struct.unpack_from(">ddd", payload, 0)
        yaw, pitch = struct.unpack_from(">ff", payload, 24)
        self._world_state["position"] = {
            "x": x, "y": y, "z": z,
            "yaw": yaw, "pitch": pitch
        }
        # must confirm position back to server or it will kick you
        self._confirm_position(payload)

    # server sends a teleport id as a varint at byte 32
    # we must echo it back with packet 0x00 (confirm teleport)
    def _confirm_position(self, payload):
        teleport_id = payload[32]
        packet_id = self._connection._encode_varint(0x00)
        data = self._connection._encode_varint(teleport_id)
        length = self._connection._encode_varint(len(packet_id + data))
        self._connection._send(length + packet_id + data)

    # respawn handling goes here later
    def _handle_health(self, payload):
        health = struct.unpack_from(">f", payload, 0)[0]
        food = struct.unpack_from(">i", payload, 4)[0]
        self._world_state["health"] = health
        self._world_state["food"] = food

        if health <= 0:
            print("Bot has died")

    # entity id is a varint — for simplicity read first byte
    # full varint parsing needed for large entity counts
    def _handle_entity(self, payload):
        entity_id = payload[0]
        entity_type = payload[1]
        x, y, z = struct.unpack_from(">ddd", payload, 2)
        self._world_state["entities"][entity_id] = {
            "type": entity_type,
            "x": x, "y": y, "z": z
        }

    def _handle_chunk(self, payload):
        cx = struct.unpack_from(">i", payload, 0)[0]
        cz = struct.unpack_from(">i", payload, 4)[0]
        # chunk data blob starts at byte 8, rest of payload is heightmap NBT + sections
        chunk_data = payload[8:]
        self._world_state["map"][(cx, cz)] = Chunk(chunk_data, self._version)

    def _handle_block_update(self, payload):
        # position packed as a single big-endian long: x<<38 | z<<12 | y
        packed = struct.unpack_from(">q", payload, 0)[0]
        x = packed >> 38
        z = (packed >> 12) & 0x3FFFFFF
        y = packed & 0xFFF
        # sign-extend x and z from 26-bit signed
        if x >= (1 << 25): x -= (1 << 26)
        if z >= (1 << 25): z -= (1 << 26)

        cx = x >> 4
        cz = z >> 4
        chunk = self._world_state["map"].get((cx, cz))

        if chunk is None:
            return
        # new state id follows the position long as a varint
        state_id = payload[8] & 0x7F
        # patch the block into the chunk's section directly
        section_y = (y + 64) >> 4
        if section_y in chunk._sections:
            chunk._sections[section_y]["patched"] = chunk._sections[section_y].get("patched", {})
            chunk._sections[section_y]["patched"][(x & 0xF, y & 0xF, z & 0xF)] = state_id

        # --------------------------------------------------------------------------------------

    """
    --------------------------------------------------------------------------------------------
    Function Header - set_mode
    --------------------------------------------------------------------------------------------
    Called by CLI after mode selection. Guided mode uses optimal pathfinding (w=1.0) since
    the user is directing precisely. Autonomous mode uses weighted A* (w=1.5) — the AI planner
    needs speed over optimality when reasoning over longer horizons.

    see thinking.txt for weighted heuristic design implementation that this code uses
    --------------------------------------------------------------------------------------------
    """
    def set_mode(self, mode):
        self._input_mode = mode

    """
    --------------------------------------------------------------------------------------------
    Function Header - move_to
    --------------------------------------------------------------------------------------------
    Public interface for movement. Takes a goal (x, y, z) tuple, finds a path from the bot's
    current position using A* weighted by behavior mode, and enqueues each step as a move
    command on the executor.

    Returns True if a path was found and enqueued, False if no path exists.

    The executor then sends each move as a movement packet when execute_queue is called by the 
    execution loop.

    Derives weight inline from self._input_mode as the cli asks for the mode, the user picks 
    guided or autonomous in select_mode(), then bot.set_mode(mode) sets _input_mode, and 
    move_to derives weight from that inline. The user never sees or touches the weight directly.
    --------------------------------------------------------------------------------------------
    """
    def move_to(self, goal):
        pos = self._world_state["position"]
        start = (pos["x"], pos["y"], pos["z"])
        if not self._input_mode is None:
            weight = 1.5 if self._input_mode == "autonomous" else 1.0
        else:
            print("Executed pathfinding without an explicit weight for "
                  "the manhattan distance heuristic")
            weight = 1.0
        path = self._pathfinder.find_path(start, goal, weight=weight)

        if not path:
            print(f"No path found to {goal}")
            return False

        for x, y, z in path:
            self._executor.enque_command({"action": "move", "x": x, "y": y, "z": z})

        return True

    """
    --------------------------------------------------------------------------------------------
    Function Field Header - Execution loop
    --------------------------------------------------------------------------------------------
    Runs on its own daemon thread, draining the command queue at 20 ticks per second to match
    Minecraft's expected packet rate. Started after connection is established so packets are
    never sent before the server is ready. Mirrors the listen thread pattern I created above
    exactly, best architectrue to achieve this, so it is safe to call on reconnect without 
    double starting.
    --------------------------------------------------------------------------------------------
    """

    def _start_execution(self):
        """
        if not self._execution_started:
        """
        self._execution_thread = threading.Thread(target=self._execution_loop, daemon=True)
        self._execution_thread.start()
        self._execution_started = True

        """
        else:
            print("Execution already started")

        ...

        _start_execution is only called in two places
        start() on initial connection, and _handle_failure 
        on reconnect where _execution_started is explicitly 
        reset to False first. So by the time _start_execution is 
        called it's always False. The else print is dead code

        -> now this else msg isnt printed for every 
        connection error
        """

    def _execution_loop(self):
        while True:
            try:
                self._executor.execute_queue()
                time.sleep(0.05)

            except Exception as e:
                self._execution_started = False
                print(f"Execution error: {e}")
                break

        # ------------------------------------------------------------------------------------------

    """
    --------------------------------------------------------------------------------------------
    Function Header - prompt
    --------------------------------------------------------------------------------------------
    Public interface for guided mode. Takes a natural language prompt, passes it to the
    planner for a single shot API call, and enqueues the returned commands onto the executor.
    The execution loop picks them up automatically.
    --------------------------------------------------------------------------------------------
    """
    def prompt(self, user_prompt):
        commands = self._planner.plan(user_prompt)
        for cmd in commands:
            self._executor.enque_command(cmd)

    """
    --------------------------------------------------------------------------------------------
    Function Header - run
    --------------------------------------------------------------------------------------------
    Public interface for autonomous mode. Takes a high level goal string and passes it to
    the planner agentic loop which reasons step by step until the goal is complete or
    max_steps is reached. Commands are enqueued directly by the planner loop.
    --------------------------------------------------------------------------------------------
    """
    def run(self, goal, max_steps=20):
        self._planner.plan_loop(goal, self._executor, max_steps=max_steps)

    """
    --------------------------------------------------------------------------------------------
    Function Header - Update field with a validation layer built into it
    --------------------------------------------------------------------------------------------
    Generic updater for any configurable field with built in validation layer. Validates the 
    value and falls back to default if invalid. Designed seperately from the initialization 
    validation layer as a design choice.
    --------------------------------------------------------------------------------------------
    """
    def set(self, key, value):
        while key not in self.default_values:
            key = input(f"'{key}' is not a valid field. Enter a valid key: ")

        if value is None:
            value = input(f"Enter new value for '{key}': ")

        allowed = self.allowed_values.get(key)

        if allowed is None:
            is_valid = value is not None
        else:
            is_valid = value in allowed

        self._valid_flags[key] = is_valid

        if not is_valid:
            print(f"Invalid key: '{key}', using default: {self.default_values[key]}")
            value = self.default_values[key]
            is_valid = True
            self._valid_flags[key] = is_valid

        setattr(self, f"_{key}", value)

    """
    --------------------------------------------------------------------------------------------
    Function Header - Failure Handling
    --------------------------------------------------------------------------------------------
    define a general handler in Bot that takes the exception and decides what to do based on 
    it's type. Everytime there is a connection error that propagates to this function, we pass 
    e and try to connect again, with a loop of connection attempts (3 iterations). Same for
    execution thread however:
    
     he break exits the while True loop which returns from _execution_loop, ending the thread 
     naturally. The thread function returning is what terminates the thread in Python, there's 
     no explicit thread stop needed. In connection we do it explicitely because _listen was 
     written with an explicit boolean flag b before you had the execution loop as a reference. 
     The b = False pattern is slightly more verbose but functionally identical to break.
    --------------------------------------------------------------------------------------------
    """
    def _handle_failure(self, e):
        if isinstance(e, ConnectionError):
            print(f"Connection failure: {e}, attempting reconnect (3 attempts before "
                  f"system shutdown")

            i = 1
            while i <= 3:
                try:
                    self._execution_started = False
                    self._connection.connect()
                    self._start_execution()
                    break

                except Exception as e:

                    if isinstance(e, ConnectionError):
                        print(f"protocol error: {e}.\nDISCONNECTING.")
                        self._connection.disconnect()

                    # bad data from server, maybe log and disconnect cleanly
                    elif isinstance(e, ValueError):
                        print(f"Protocol error: {e}.\nDISCONNECTING.")
                        self._connection.disconnect()

                    else:
                        print(f"Unexpected error: {e}, shutting down")
                        self._connection.disconnect()

                    if (i == 3):
                        break

                i += 1


"""
--------------------------------------------------------------------------------------------
Class Header - Connection layer
--------------------------------------------------------------------------------------------
Bot will have access because classes in the same file share the same module scope. As long 
as the Connection class is defined in that file, Bot can reference it directly.
--------------------------------------------------------------------------------------------
"""


class Connection:

    def __init__(self, host, port, version, username, on_failure, protocol_version, packet_handler=None):
        self._host = host
        self._port = port
        self._version = version
        self._socket = None
        self._protocol_version = protocol_version
        self._connected = False
        self._username = username
        self._on_failure = on_failure
        self._thread_a = None
        self._started = False
        self._packet_handler = packet_handler

    """
    --------------------------------------------------------------------------------------------
    Function Header - Encode varint algorithm
    --------------------------------------------------------------------------------------------
    VarInt encodes an integer into a variable number of bytes, using only 7 bits (temp = value 
    & 0b01111111 of each byte for data and reserving the 8th (highest) bit as a "more bytes 
    coming" signal (value != 0: temp |= 0b10000000 If there's anything left after the shift, you 
    OR the high bit to 1. This is the signal to the receiver that another byte is coming).

    So the algorithm has two jobs per iteration — pack 7 bits of data, and signal whether the 
    reader should keep reading (ie if the number can be repped in 7). The Minecraft protocol 
    receiver on the other end is reading one byte at a time and needs to know when to stop. 
    The convention chosen is:high bit = 1 → keep reading, high bit = 0 → this is the last byte 
    --------------------------------------------------------------------------------------------
    """

    @staticmethod
    def _encode_varint(value: int) -> bytes:
        if value < 0:
            raise ValueError("VarInt cannot be negative")

        result = bytearray()
        while True:
            # we make high bit zero for temp not value
            temp = value & 0b01111111
            value >>= 7

            if value != 0:
                temp |= 0b10000000

            result.append(temp)

            if value == 0:
                break

        return bytes(result)

    """
    --------------------------------------------------------------------------------------------
    Function Field Header - Handshake (Minecraft Conventional Binary)
    --------------------------------------------------------------------------------------------
    Two more serialization helpers that convert Python types into raw bytes the way Minecraft 
    expects them, one uses VarInt for integers (algorithm above), and length-prefixed UTF-8 for 
    strings, the other is returns big-endian 2 bytes for the port.

    Those helpers are then used to build two packets. Each packet follows the same envelope — 
    length, then packet_id, then data fields in the order Minecraft specifies. The length is 
    computed last because it needs to measure the finished packet_id + data bytes before it can 
    be encoded. The two send functions just call their serialize counterpart and hand the result 
    to sendall.

    The handshake packet tells the server your protocol version, where you're connecting to, 
    and that you intend to log in. The login start packet tells it your username. Together they 
    complete the opening exchange — after these two packets the server has everything it needs 
    to either accept or reject the connection, which is why connect() immediately reads a 
    packet after sending them.
    --------------------------------------------------------------------------------------------
    """

    def _encode_string(self, s: str) -> bytes:
        encoded = s.encode("utf-8")
        return self._encode_varint(len(encoded)) + encoded

    @staticmethod
    def _encode_unsigned_short(port: int) -> bytes:
        return port.to_bytes(2, byteorder="big")  # big endian

    def _serialize_handshake(self) -> bytes:
        packet_id = self._encode_varint(0x00)
        data = (self._encode_varint(self._protocol_version) + self._encode_string(self._host) +
                self._encode_unsigned_short(self._port) + self._encode_varint(2))
        length = self._encode_varint(len(packet_id + data))
        return length + packet_id + data

    def _send_handshake(self):
        packet = self._serialize_handshake()
        self._socket.sendall(packet)

    def _serialize_login_start(self, username: str) -> bytes:
        packet_id = self._encode_varint(0x00)  # Login Start packet ID
        data = self._encode_string(username)
        length = self._encode_varint(len(packet_id + data))
        return length + packet_id + data

    def _send_login_start(self):
        packet = self._serialize_login_start(self._username)
        self._socket.sendall(packet)

    # ------------------------------------------------------------------------------------------

    """
    --------------------------------------------------------------------------------------------
    Function Header - Length setter
    --------------------------------------------------------------------------------------------
    encode_varint takes a number already in memory and converts it into bytes in the way 
    minecraft needs to connect and login, it has everything it needs upfront and just loops over 
    the integer until it's fully encoded. _read_varint_ from_socket can't do that because it 
    doesn't know the number yet, the number is still arriving over the network one byte at a 
    time at runtime which is what were meant to calulate.

    So instead of looping over an integer it loops over the packets ϵ socket, pulling one byte
    per iteration and checking the high bit to know when to stop. so for each socket in read var 
    int we check high bit and shift it if bytes high is 0 then break other wise read moire
    --------------------------------------------------------------------------------------------
    """

    def _read_varint_from_socket(self) -> int:
        result = 0
        shift = 0
        while True:
            # we never advance in index as the socket connection automatically advances.When
            # you call _read_exact(1) it asks for exactly 1 byte from the os buffer,
            # returning it as a single byte chunk. You then index with [0] to get the
            # integer value of that byte, which is what you actually check the high bit on
            byte = self._socket.recv(1)[0]
            # so for each socket in read_var_int we check high bit and shift it if bytes
            # high is 0 then break otherwise read more
            result |= (byte & 0b01111111) << shift

            if not (byte & 0b10000000):
                break

            shift += 7

            if shift >= 32:
                raise ValueError("VarInt too large")

        return result

    """
    --------------------------------------------------------------------------------------------
    Function Field Header - Recieve packets
    --------------------------------------------------------------------------------------------
    Recv(4096) doesn't guarantee you get a full packet. It returns however many 
    bytes the OS has ready. This could be half a packet, could be two packets concatenated, etc. 
    Minecraft's protocol requires you read the length first, then read exactly that many bytes, 
    so you need _read_exact before anything else can work reliably. We use this (and read varint 
    to get length) to read the full packet everytimee. Concept is called framing -> wrap all 
    packets in a length prefix to accurately implement the reciever.
    --------------------------------------------------------------------------------------------
    """

    def _read_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            # Why n - len(buf)? n = total number of bytes you want to read. but = bytes
            # you've already received so far. len(but) = how many bytes you already have.
            # n - len(buf) = how many bytes are still needed.
            chunk = self._socket.recv(n - len(buf))

            # this propagates back up to the exception function on the call stack (i.e. to
            # read_packet, then to listen
            if not chunk:
                raise ConnectionError("Socket closed while reading")

            buf += chunk

        return buf

    def _read_packet(self) -> tuple[int, bytes]:
        if self._connected:
            length = self._read_varint_from_socket()
            payload = self._read_exact(length)
            packet_id = payload[0]
            return packet_id, payload[1:]

        else:
            raise ConnectionError("Cannot read: not connected")

    # ------------------------------------------------------------------------------------------

    """
    --------------------------------------------------------------------------------------------
    Function Field Header - Send and packet sent builder auxiliary functions
    --------------------------------------------------------------------------------------------
    Builds the message as convened by minecraft so to remain connected. Also a aux to send this
    message, we only check for connection here before sendall, as previously we used this socket
    function to connect.
    --------------------------------------------------------------------------------------------
    """

    def _send(self, data: bytes):
        if self._connected:
            return self._socket.sendall(data)

        # ""b is base None return case for bytes
        return b""

    # Same envelope as the handshake packets, length, packet_id, data.
    def _keepalive_response_aux(self, payload: bytes) -> bytes:
        packet_id = b"\x21"
        length = self._encode_varint(len(packet_id + payload))
        return length + packet_id + payload

    # ------------------------------------------------------------------------------------------

    """
    --------------------------------------------------------------------------------------------
    Function Header - Keepalive loop.
    --------------------------------------------------------------------------------------------
    Uses response builder aux to send the needed data to minecraft before 30 seconds is up, and 
    recieved packet data using packet read. Errors are propagated from all above code to this
    function and that feedback is accessed by bot to handle the errors generically.
    --------------------------------------------------------------------------------------------
    """

    def _listen(self):
        b = True
        while b:
            try:
                # this is the id minecraft must recieve to then send back the next one
                # when the 20 seconds is up, helper builds and sends data (in read_p)
                packet_id, payload = self._read_packet()

                if packet_id == 0x21:
                    self._send(self._keepalive_response_aux(payload))

                else:
                    # functions are truthy objects, in bot we initialize this attribute
                    # to a function within bot that will handle world state / data other
                    # than keep alive
                    if self._packet_handler:
                        self._packet_handler(packet_id, payload)

            except Exception as e:
                self._started = False
                b = False

                if self._on_failure:
                    self._on_failure(e)

                # if we do not pass an error function -> gen case error handling
                else:
                    print(f"Error: {e}")
                    self._started = False
                    self.disconnect()
                    b = False

    """
    --------------------------------------------------------------------------------------------
    Function Header - Thread starter
    --------------------------------------------------------------------------------------------
    Your main program runs on one thread, it executes line by line, so if it's waiting for a
    packet it can't do anything else. A thread is a separate line of execution that runs
    concurrently alongside your main code. We need this to be constantly running, thus, we
    need this seperate execution line. Target is the function thread that will run, and daemon
    is a flag that marks it as a background thread if true, i.e. so that it exists as a
    seperate main program, and one that lives as long as our main program.
    --------------------------------------------------------------------------------------------
    """

    def _start_func(self):
        if not self._started:
            # breaks when target throws an exception
            self._thread_a = threading.Thread(target=self._listen, daemon=True)
            self._thread_a.start()
            self._started = True

        else:
            print("Already started")

    """
    --------------------------------------------------------------------------------------------
    Function Field Header - Connect and Disconnect
    --------------------------------------------------------------------------------------------
    In Python, the socket module provides the basic TCP/UDP network interface that you can use 
    to connect to Minecraft as minecraft uses TCP packets. Opens a TCP socket, 
    socket.AF_INET → IPv4, socket.SOCK_STREAM → TCP.
    --------------------------------------------------------------------------------------------
    """

    def connect(self):
        if not self._connected:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._socket.connect((self._host, self._port))
            self._send_handshake()
            self._send_login_start()
            # but then connected is true seemingly for a period while connection failed
            # based of packet id7:02 PMThat's a valid concern but it's acceptable in
            # practice the window is a single blocking call to _read_packet(), which
            # returns almost instantly. Nothing else can observe _connected = True during
            # that window because the keepalive thread hasn't started yet and no other
            # code is running concurrently at that point.
            packet_id, payload = self._read_packet()

            if packet_id == 0x02:
                # because we (As per design choice) have keepalive handled within connection
                # we start it when someone connects
                self._connected = True
                self._start_func()
                print(f"Connected to {self._host}:{self._port}")

            # connect is called on Connection directly by whatever sets up the bot, so that
            # ConnectionError propagates up to that caller, not to _listen. They're the same
            # exception type but raised in completely separate contexts which determines the
            # propogation. (i.e. it will propogate to bot.start() which initiates connection,
            # etc.)
            elif packet_id == 0x00:
                # note that a consequence of the information above is that there is no
                # gen case for this raised exception
                raise ConnectionError("Login failed: server rejected connection")

        else:
            print("Already connected")

    def disconnect(self):
        if self._connected:
            self._socket.close()
            self._socket = None
            self._connected = False
            print(f"Disconnected from {self._host}:{self._port}")

        else:
            print("Not connected to begin with")

    # ------------------------------------------------------------------------------------------