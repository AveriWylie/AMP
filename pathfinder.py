"""
--------------------------------------------------------------------------------------------
Pathfinder Module
--------------------------------------------------------------------------------------------
A* pathfinder over real chunk data. Single public interface is find_path(start, goal)
which returns an ordered list of (x, y, z) tuples the bot should walk through.

A few things to note. The heuristic is Manhattan distance, fast to compute and admissible
for grid movement. Neighbors are the 4 cardinal directions plus vertical steps up and down
by 1, covering walking, stepping up one block, and dropping down. Diagonal movement is
excluded, Minecraft movement is axis-aligned per tick.

The pathfinder queries world state directly via the get_block helper, which resolves chunk
coordinates to the right Chunk object and calls get_block on it. If a chunk isn't loaded
yet the block defaults to air, which is treated as passable, conservative but safe.

A node is passable if its block is air or a non-solid, and the block above it is also air
so the bot's 2-block tall hitbox fits. The block below must be solid so the bot has
something to stand on.
--------------------------------------------------------------------------------------------
"""
# imports
import heapq

# non-solid blocks the bot can move through or stand in
PASSABLE = {
    "air", "cave_air", "void_air", "water", "lava",
    "tall_grass", "grass", "fern", "dead_bush",
    "dandelion", "poppy", "blue_orchid", "allium",
    "azure_bluet", "red_tulip", "orange_tulip",
    "white_tulip", "pink_tulip", "oxeye_daisy",
    "cornflower", "lily_of_the_valley", "wheat",
    "sugar_cane", "vine", "snow"
}

"""
--------------------------------------------------------------------------------------------
Class Header - Pathfinder
--------------------------------------------------------------------------------------------
Takes world_state dict directly so it can query the live map as chunks arrive. The map
dict is keyed by (cx, cz) chunk coordinates and values are Chunk objects with get_block.
--------------------------------------------------------------------------------------------
"""
class Pathfinder:

    def __init__(self, world_state):
        self._world_state = world_state

    """
    --------------------------------------------------------------------------------------------
    Function Header - Block getter
    --------------------------------------------------------------------------------------------
    Resolves absolute world coordinates to a block name by finding the right chunk first.
    Chunk coordinates are absolute x and z each right-shifted by 4 (divided by 16).
    Returns air if the chunk isn't loaded so unknown terrain is treated as passable.
    --------------------------------------------------------------------------------------------
    """
    def _get_block(self, x, y, z):
        cx = x >> 4
        cz = z >> 4
        chunk = self._world_state["map"].get((cx, cz))
        if chunk is None:
            return "air"
        return chunk.get_block(x, y, z)

    """
    --------------------------------------------------------------------------------------------
    Function Header - Passability check
    --------------------------------------------------------------------------------------------
    A position is walkable if:
    - the block at (x, y, z) is passable (bot's feet)
    - the block at (x, y+1, z) is passable (bot's head)
    - the block at (x, y-1, z) is solid (something to stand on)

    The solid check is just the inverse of passable, if it's not in the passable set,
    it's solid enough to stand on.
    --------------------------------------------------------------------------------------------
    """
    def _is_walkable(self, x, y, z):
        feet = self._get_block(x, y, z)
        head = self._get_block(x, y + 1, z)
        floor = self._get_block(x, y - 1, z)
        return feet in PASSABLE and head in PASSABLE and floor not in PASSABLE

    """
    --------------------------------------------------------------------------------------------
    Function Header - Neighbor generator
    --------------------------------------------------------------------------------------------
    Yields walkable neighbors from a given position. The 4 cardinal directions are checked
    at the same Y first (flat walking). Then one block up (stepping onto a raised block) and
    one block down (dropping off an edge) are checked for each direction.

    Cost is 1 for all moves, uniform cost since diagonal movement is excluded and all
    steps are one block.
    --------------------------------------------------------------------------------------------
    """
    def _neighbors(self, x, y, z):
        for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, nz = x + dx, z + dz

            # flat walk
            if self._is_walkable(nx, y, nz):
                yield nx, y, nz, 1

            # step up one block
            elif self._is_walkable(nx, y + 1, nz):
                yield nx, y + 1, nz, 1

            # drop down one block
            elif self._is_walkable(nx, y - 1, nz):
                yield nx, y - 1, nz, 1

    """
    --------------------------------------------------------------------------------------------
    Function Header - Heuristic
    --------------------------------------------------------------------------------------------
    Manhattan distance in 3D. Admissible for grid movement with unit costs so A* is
    guaranteed to find the shortest path.
    --------------------------------------------------------------------------------------------
    """
    @staticmethod
    def _heuristic(x, y, z, gx, gy, gz):
        return abs(x - gx) + abs(y - gy) + abs(z - gz)

    """
    --------------------------------------------------------------------------------------------
    Function Header - find_path
    --------------------------------------------------------------------------------------------
    Public interface. Takes start and goal as (x, y, z) tuples and returns an ordered list
    of (x, y, z) tuples from start to goal inclusive, or an empty list if no path exists.

    A* open set is a min-heap keyed by f = g + h. Each entry is (f, g, x, y, z).
    came_from maps each visited node to its predecessor for path reconstruction.
    g_score maps each visited node to its cheapest known cost from start.

    The search is capped at max_nodes to prevent runaway searches in open terrain, 
    if the goal is unreachable or too far the function returns empty rather than hanging.
    
    see thinking.txt for weighted heuristic design implementation that this code uses
    --------------------------------------------------------------------------------------------
    """
    def find_path(self, start, goal, weight=1.0, max_nodes=10000):
        sx, sy, sz = start
        gx, gy, gz = goal

        # snap start and goal to integer coordinates
        sx, sy, sz = int(sx), int(sy), int(sz)
        gx, gy, gz = int(gx), int(gy), int(gz)

        if (sx, sy, sz) == (gx, gy, gz):
            return [(sx, sy, sz)]

        h = self._heuristic(sx, sy, sz, gx, gy, gz)
        # heap entry: (f, g, x, y, z)
        open_heap = [(weight * h, 0, sx, sy, sz)]
        came_from = {}
        g_score = {(sx, sy, sz): 0}
        visited = 0

        while open_heap:
            if visited >= max_nodes:
                return []

            f, g, x, y, z = heapq.heappop(open_heap)
            visited += 1

            if (x, y, z) == (gx, gy, gz):
                return self._reconstruct(came_from, x, y, z)

            # skip stale heap entries
            if g > g_score.get((x, y, z), float("inf")):
                continue

            for nx, ny, nz, cost in self._neighbors(x, y, z):
                ng = g + cost
                if ng < g_score.get((nx, ny, nz), float("inf")):
                    g_score[(nx, ny, nz)] = ng
                    came_from[(nx, ny, nz)] = (x, y, z)
                    nh = self._heuristic(nx, ny, nz, gx, gy, gz)
                    heapq.heappush(open_heap, (ng + weight * nh, ng, nx, ny, nz))

        return []

    """
    --------------------------------------------------------------------------------------------
    Function Header - Path reconstruction
    --------------------------------------------------------------------------------------------
    Walks came_from backwards from goal to start, then reverses to get start-to-goal order.
    --------------------------------------------------------------------------------------------
    """
    @staticmethod
    def _reconstruct(came_from, x, y, z):
        path = [(x, y, z)]
        while (x, y, z) in came_from:
            x, y, z = came_from[(x, y, z)]
            path.append((x, y, z))
        path.reverse()
        return path