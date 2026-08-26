"""Minecraft protocol transport and connection lifecycle."""

import hashlib
import socket
import struct
import threading
import uuid
import zlib

from protocol_data import packet_ids_for_protocol


class Connection:
    MAX_PACKET_SIZE = 2_097_151

    """
    --------------------------------------------------------------------------------------------
    Function Header - Generated Play-state packet IDs
    --------------------------------------------------------------------------------------------
    Version-specific IDs are loaded by numeric protocol from protocol/packet_ids.json, which is
    generated from the pinned minecraft-data definitions. The connection retains both directions
    because keepalive is received clientbound and echoed serverbound.
    --------------------------------------------------------------------------------------------
    """
    def __init__(self, host, port, version, username, on_failure, protocol_version, packet_handler=None):
        self._host = host
        self._port = port
        self._version = version
        self._socket = None
        self._protocol_version = protocol_version
        self.play_ids = packet_ids_for_protocol(protocol_version, "serverbound")
        self.clientbound_ids = packet_ids_for_protocol(protocol_version, "clientbound")
        self._modern_configuration = protocol_version >= 764
        if self._modern_configuration:
            self.login_ids = packet_ids_for_protocol(
                protocol_version, "serverbound", state="login"
            )
            self.configuration_ids = packet_ids_for_protocol(
                protocol_version, "serverbound", state="configuration"
            )
            self.configuration_clientbound_ids = packet_ids_for_protocol(
                protocol_version, "clientbound", state="configuration"
            )
        self._connected = False
        self._username = username
        self._on_failure = on_failure
        self._thread_a = None
        self._started = False
        self._packet_handler = packet_handler
        # None until the server sends Set Compression during login. Once set to a threshold,
        # every read and every send uses the compressed frame envelope (see _read_packet/_send).
        self._compression_threshold = None

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
        if self._modern_configuration:
            # Java's UUID.nameUUIDFromBytes("OfflinePlayer:<name>") algorithm. Modern
            # Login Start requires these 16 raw bytes even when the server is offline-mode.
            digest = hashlib.md5(
                f"OfflinePlayer:{username}".encode("utf-8"), usedforsecurity=False
            ).digest()
            data += uuid.UUID(bytes=digest, version=3).bytes
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
            raw = self._socket.recv(1)
            if not raw:
                raise ConnectionError("Socket closed while reading VarInt")
            byte = raw[0]
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
    Function Header - Decode varint from an in-memory buffer
    --------------------------------------------------------------------------------------------
    The socket version above reads one byte at a time off the wire. This one reads a varint
    that is already sitting in a bytes buffer at a known offset, returning both the value and
    how many bytes it consumed. Needed to read the Data Length inside a compressed frame and
    the threshold inside Set Compression, where the bytes are already in hand.
    --------------------------------------------------------------------------------------------
    """

    @staticmethod
    def _decode_varint_bytes(buf: bytes, offset: int) -> tuple[int, int]:
        if offset < 0 or offset >= len(buf):
            raise ValueError("VarInt offset outside buffer")

        result = 0
        shift = 0
        consumed = 0
        while True:
            if offset + consumed >= len(buf):
                raise ValueError("Truncated VarInt")

            byte = buf[offset + consumed]
            if consumed == 4 and byte & 0xF0:
                raise ValueError("VarInt exceeds 32 bits")
            result |= (byte & 0b01111111) << shift
            consumed += 1

            if not (byte & 0b10000000):
                break

            shift += 7

            if consumed >= 5:
                raise ValueError("VarInt too large")

        return result, consumed

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
        # No _connected guard: this must run DURING login (before _connected is True) to read
        # Set Compression / Login Success, and again in Play. The live socket is the real
        # precondition.
        length = self._read_varint_from_socket()
        if not 0 < length <= self.MAX_PACKET_SIZE:
            raise ValueError(f"Invalid packet length: {length}")
        frame = self._read_exact(length)

        # Uncompressed framing: the frame is packet_id + data directly.
        if self._compression_threshold is None:
            payload = frame

        # Compressed framing (after Set Compression): frame is Data Length (varint) + body.
        # Data Length 0 means the body was under the threshold and is raw; otherwise the body
        # is zlib-compressed and inflates to Data Length bytes.
        else:
            data_length, consumed = self._decode_varint_bytes(frame, 0)
            body = frame[consumed:]
            if data_length == 0:
                payload = body
            else:
                if not 0 < data_length <= self.MAX_PACKET_SIZE:
                    raise ValueError(
                        f"Invalid decompressed packet length: {data_length}"
                    )
                if data_length < self._compression_threshold:
                    raise ValueError("Compressed packet is below compression threshold")
                inflater = zlib.decompressobj()
                payload = inflater.decompress(body, self.MAX_PACKET_SIZE + 1)
                if (
                    len(payload) != data_length
                    or not inflater.eof
                    or inflater.unconsumed_tail
                    or inflater.unused_data
                ):
                    raise ValueError("Invalid decompressed packet size or stream")

        if not payload:
            raise ValueError("Packet payload is empty")

        packet_id = payload[0]
        return packet_id, payload[1:]

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
        if not self._connected:
            raise ConnectionError("Cannot send packet while disconnected")

        # Callers hand us the uncompressed frame (length + packet_id + data). Once the server
        # has enabled compression, every Play-state packet must be re-framed into the compressed
        # envelope before it goes out, or the server misreads it.
        if self._compression_threshold is not None:
            data = self._compress_frame(data)

        return self._socket.sendall(data)

    """
    --------------------------------------------------------------------------------------------
    Function Header - Compressed frame builder
    --------------------------------------------------------------------------------------------
    Re-frames an already-built uncompressed packet (length_varint + body) into the compressed
    envelope: Packet Length, then Data Length, then the body. Body at/above threshold is
    zlib-compressed with Data Length = uncompressed size; below threshold sent raw with Data
    Length 0. The old length prefix is stripped first since the compressed envelope recomputes
    its own outer length.
    --------------------------------------------------------------------------------------------
    """

    def _compress_frame(self, uncompressed_frame: bytes) -> bytes:
        _, consumed = self._decode_varint_bytes(uncompressed_frame, 0)
        body = uncompressed_frame[consumed:]

        if len(body) >= self._compression_threshold:
            payload = self._encode_varint(len(body)) + zlib.compress(body)
        else:
            payload = self._encode_varint(0) + body

        return self._encode_varint(len(payload)) + payload

    # Same envelope as the handshake packets, length, packet_id, data. Packet id comes from
    # play_ids so keepalive tuning stays one place.
    def _keepalive_response_aux(self, payload: bytes) -> bytes:
        packet_id = self._encode_varint(self.play_ids["keep_alive"])
        length = self._encode_varint(len(packet_id + payload))
        return length + packet_id + payload

    def _send_protocol_packet(self, packet_id: int, payload: bytes = b""):
        """Send a packet during Login, Configuration, or Play."""
        encoded_id = self._encode_varint(packet_id)
        frame = self._encode_varint(len(encoded_id + payload)) + encoded_id + payload
        if self._compression_threshold is not None:
            frame = self._compress_frame(frame)
        self._socket.sendall(frame)

    def _send_configuration_settings(self):
        payload = (
            self._encode_string("en_us")
            + struct.pack(">b", 10)
            + self._encode_varint(0)
            + b"\x01"
            + b"\x7f"
            + self._encode_varint(1)
            + b"\x00"
            + b"\x01"
        )
        self._send_protocol_packet(self.configuration_ids["settings"], payload)

    def _configuration(self):
        """Process Configuration packets until the server releases us into Play."""
        self._send_configuration_settings()
        while True:
            packet_id, payload = self._read_packet()
            ids = self.configuration_clientbound_ids
            if packet_id == ids["finish_configuration"]:
                self._send_protocol_packet(
                    self.configuration_ids["finish_configuration"]
                )
                return
            if packet_id == ids["keep_alive"]:
                self._send_protocol_packet(self.configuration_ids["keep_alive"], payload)
            elif packet_id == ids["ping"]:
                self._send_protocol_packet(self.configuration_ids["pong"], payload)
            elif packet_id == ids["resource_pack_send"]:
                # 1 = declined. AMP is headless and cannot apply client resource packs.
                self._send_protocol_packet(
                    self.configuration_ids["resource_pack_receive"],
                    self._encode_varint(1),
                )
            elif packet_id == ids["disconnect"]:
                raise ConnectionError("Server disconnected during Configuration")
            # Registry, feature, tag and custom payload packets are length-framed and
            # may be safely retained/ignored by this headless base.

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

                if packet_id == self.clientbound_ids["keep_alive"]:
                    self._send(self._keepalive_response_aux(payload))

                elif (self._modern_configuration and
                      packet_id == self.clientbound_ids["start_configuration"]):
                    self._send_protocol_packet(
                        self.play_ids["configuration_acknowledged"]
                    )
                    self._configuration()

                else:
                    # functions are truthy objects, in bot we initialize this attribute
                    # to a function within bot that will handle world state / data other
                    # than keep alive
                    if self._packet_handler:
                        self._packet_handler(packet_id, payload)

            except Exception as e:
                self._started = False
                b = False
                was_connected = self._connected
                failed_socket = self._socket
                self._connected = False
                self._socket = None
                if failed_socket is not None:
                    failed_socket.close()

                # Closing the socket during an intentional disconnect wakes recv() with an
                # error. That is normal shutdown, not a connection failure to reconnect from.
                if was_connected and self._on_failure:
                    self._on_failure(e)

                # if we do not pass an error function -> gen case error handling
                elif was_connected:
                    print(f"Error: {e}")

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
            self._login()

        else:
            print("Already connected")

    """
    --------------------------------------------------------------------------------------------
    Function Header - Login state machine
    --------------------------------------------------------------------------------------------
    After Login Start the server drives a short exchange before Play begins, and it does not
    always end on the first packet, so we loop until Login Success rather than read exactly one.
      0x03 Set Compression    -> store threshold, all frames after this are compressed
      0x02 Login Success      -> transition to Play, start keepalive/listen thread
      0x00 Disconnect         -> server rejected us
      0x01 Encryption Request -> online-mode server, unsupported by this base
    _read_packet already honors _compression_threshold, so once Set Compression sets it the
    following Login Success frame is read compressed automatically.
    --------------------------------------------------------------------------------------------
    """

    def _login(self):
        while True:
            packet_id, payload = self._read_packet()

            if packet_id == 0x03:
                threshold, _ = self._decode_varint_bytes(payload, 0)
                self._compression_threshold = threshold

            elif packet_id == 0x02:
                if self._modern_configuration:
                    self._send_protocol_packet(self.login_ids["login_acknowledged"])
                    self._configuration()
                # because we (As per design choice) have keepalive handled within connection
                # we start it when someone connects
                self._connected = True
                self._start_func()
                print(f"Connected to {self._host}:{self._port}")
                break

            # connect is called on Connection directly by whatever sets up the bot, so that
            # ConnectionError propagates up to that caller, not to _listen. They're the same
            # exception type but raised in completely separate contexts which determines the
            # propogation. (i.e. it will propogate to bot.start() which initiates connection,
            # etc.)
            elif packet_id == 0x00:
                # note that a consequence of the information above is that there is no
                # gen case for this raised exception
                raise ConnectionError("Login failed: server rejected connection")

            elif packet_id == 0x01:
                raise ConnectionError("Server is online-mode (encryption required). This "
                                      "base only supports offline-mode / LAN servers.")

            else:
                raise ConnectionError(f"Unexpected login packet id {hex(packet_id)}")

    def disconnect(self):
        if self._connected:
            socket_to_close = self._socket
            self._socket = None
            self._connected = False
            self._started = False
            socket_to_close.close()
            print(f"Disconnected from {self._host}:{self._port}")

        else:
            print("Not connected to begin with")

    # ------------------------------------------------------------------------------------------
