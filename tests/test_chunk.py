"""Chunk payload parsing tests."""

import struct

from chunk import Chunk


def _modern_chunk_with_heightmap():
    # 1.20.2 overworld heightmaps contain 256 nine-bit values packed seven per long.
    # Stored values are the first available Y relative to the dimension minimum (-64).
    longs = [0] * 37
    longs[0] = 135 << 9  # column (1, 0): highest block is 135 - 64 - 1 = 70
    longs[1] = 201       # column (7, 0): highest block is 201 - 64 - 1 = 136
    name = b"WORLD_SURFACE"
    heightmap = (
        b"\x0a"  # unnamed root compound in network NBT
        + b"\x0c" + struct.pack(">H", len(name)) + name
        + struct.pack(">i", len(longs))
        + struct.pack(f">{len(longs)}q", *longs)
        + b"\x00"
    )
    return heightmap + b"\x00"  # zero-length section-data byte array


def test_modern_chunk_exposes_decoded_surface_height():
    chunk = Chunk(_modern_chunk_with_heightmap(), "1.20.2")

    assert chunk.get_surface_y(1, 0) == 70
    assert chunk.get_surface_y(7, 0) == 136
