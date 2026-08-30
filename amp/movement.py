"""
--------------------------------------------------------------------------------------------
Movement Module - Tick-paced player physics
--------------------------------------------------------------------------------------------
Convert block paths into collision-safe, tick-paced player movement. The pathfinder answers
which blocks to cross, this answers how a player body actually gets across them.

The two are not the same problem. A path is a list of block coordinates, and sending one move
per block asks the server to accept a player jumping a whole block per packet, which its own
movement checks reject. Vanilla clients send a position every tick along a continuous curve,
so that is what this produces, several small moves per path edge rather than one large one.

The vertical curves below are sampled from vanilla's own physics rather than derived. Jump and
fall arcs are the result of an initial velocity with gravity and drag applied per tick, and the
server validates against those numbers, so approximating them with a straight line gets the
move rejected and the player teleported back.

Two entry points. commands_for_path expands a planned route, gravity_command runs on idle ticks
when nobody planned anything and the player simply has to fall. Both emit the same command
shape, and both mark it report=False, since physics is not something anyone asked for and
logging it would bury everything the planner actually did at 20 lines a second.
--------------------------------------------------------------------------------------------
"""

# imports
import math


"""
--------------------------------------------------------------------------------------------
Class Header - Movement controller
--------------------------------------------------------------------------------------------
Holds the small amount of state that has to survive between ticks, the current vertical
velocity and the position revision it belongs to.

The revision is what makes falling safe. If the server corrects the player's position, the
accumulated velocity describes a fall that no longer happened, so it is discarded rather than
applied on top of wherever the server put us.

GROUND_TOLERANCE is the small gap vanilla allows between the feet and the block below before
the player counts as airborne, so standing is not misread as falling from floating-point drift.

WALK_TICKS_PER_BLOCK spreads a flat block of walking across five ticks, roughly vanilla's
walking speed, which keeps the per-tick distance inside what the server accepts.
--------------------------------------------------------------------------------------------
"""
class MovementController:

    TICK_SECONDS = 0.05
    PLAYER_RADIUS = 0.3
    GROUND_TOLERANCE = 0.0625
    WALK_TICKS_PER_BLOCK = 5

    def __init__(self):
        self._vertical_velocity = 0.0
        self._position_revision = None


    """
    --------------------------------------------------------------------------------------------
    Function Header - Path expansion
    --------------------------------------------------------------------------------------------
    Expands a block path into per-tick moves, one edge at a time. Edges are taken pairwise so
    each is expanded relative to where the previous one ended, which is what keeps the whole
    route continuous rather than a series of independent hops.

    Only the final command keeps its report flag, popped so it prints. The intermediate moves
    are physics, but the last one is the step actually arriving somewhere, and it is what the
    planner sees as the result of the move it asked for. Reporting every sample instead would
    make one short walk look like forty separate actions.
    --------------------------------------------------------------------------------------------
    """
    def commands_for_path(self, path):
        commands = []

        # pairwise, so each edge starts where the last one finished
        for previous, target in zip(path, path[1:]):
            commands.extend(self._commands_for_edge(previous, target))

        # the arrival is the reportable action, everything before it is physics
        if commands:
            commands[-1].pop("report", None)

        return commands


    """
    --------------------------------------------------------------------------------------------
    Function Header - Edge expansion
    --------------------------------------------------------------------------------------------
    Turns one path edge into its per-tick samples. Three cases, matching what the pathfinder can
    produce, flat, up one, and down one.

    Flat walking is linear, five even steps, since horizontal speed is constant.

    The jump and fall tables are vanilla's actual trajectories, each entry a horizontal progress
    from 0 to 1 paired with a vertical offset. A jump rises fast, decelerates, peaks around 1.25
    blocks and settles onto the block above, which is why it overshoots the one block it gains.
    A fall moves horizontally first, leaves the ledge, then accelerates downward.

    on_ground is per sample rather than per edge, and matters as much as the position. It is
    false through the airborne part and true again on landing, and the server applies its own
    gravity when it is set, so leaving it true through a jump would have the server pull the
    player straight back down.

    Anything steeper than one block raises. The pathfinder never emits it, so reaching this
    means the path came from somewhere that does not agree with the movement model, and
    silently interpolating it would send a move the server rejects.
    --------------------------------------------------------------------------------------------
    """
    def _commands_for_edge(self, previous, target):
        px, py, pz = previous
        tx, ty, tz = target

        # flat, constant speed, so even steps are enough
        if ty == py:
            samples = tuple((step / self.WALK_TICKS_PER_BLOCK, 0.0, True) for step in range(1, self.WALK_TICKS_PER_BLOCK + 1))

        # jump, vanilla's arc, peaks above 1.25 before settling onto the block gained
        elif ty == py + 1:
            samples = (
                (0.00, 0.4200, False),
                (0.10, 0.7532, False),
                (0.30, 1.0013, False),
                (0.55, 1.1661, False),
                (0.80, 1.2492, False),
                (1.00, 1.2522, False),
                (1.00, 1.1768, False),
                (1.00, 1.0244, False),
                (1.00, 1.0000, True),
            )

        # fall, moves clear of the ledge first, then accelerates downward
        elif ty == py - 1:
            samples = (
                (0.20, 0.0000, True),
                (0.40, 0.0000, True),
                (0.60, 0.0000, True),
                (0.80, 0.0000, False),
                (1.00, -0.0784, False),
                (1.00, -0.2336, False),
                (1.00, -0.4642, False),
                (1.00, -0.7685, False),
                (1.00, -1.0000, True),
            )

        # the pathfinder never produces this, so it means the path did not come from it
        else:
            raise ValueError(f"Path edge changes elevation by more than one block: {previous} -> {target}")

        # 0.5 centres the player in the block, coordinates name a corner
        start_x, start_z = px + 0.5, pz + 0.5
        delta_x, delta_z = tx - px, tz - pz

        return [
                    {
                        "action": "move",
                        "x": start_x + delta_x * progress,
                        "y": py + y_offset,
                        "z": start_z + delta_z * progress,
                        "on_ground": on_ground,
                        "delay": self.TICK_SECONDS,
                        "report": False,

                    } for progress, y_offset, on_ground in samples
        ]


    """
    --------------------------------------------------------------------------------------------
    Function Header - Gravity
    --------------------------------------------------------------------------------------------
    One tick of falling, or None when the player is not falling. Called on idle ticks only, so
    it never competes with a planned path.

    Velocity is reset whenever the position revision changes, because a correction means the
    server moved us and the velocity we accumulated describes a fall that did not happen.

    Two cases return None with the velocity cleared. Standing on something solid is the obvious
    one. The other is standing in an unloaded chunk, where there is no terrain to test against,
    and guessing would mean falling through ground that simply has not arrived yet.

    The velocity update is vanilla's, subtract 0.08 for gravity then scale by 0.98 for drag,
    applied in that order. Matching it matters because the server runs the same numbers and
    compares.

    Landing is checked against the whole span the tick would cross rather than just the
    destination, so a fast fall cannot pass through a block between one tick and the next.
    --------------------------------------------------------------------------------------------
    """
    def gravity_command(self, world_state, pathfinder):
        position = world_state["position"]
        revision = world_state.get("position_revision", 0)

        # the server moved us, so the velocity belongs to a fall that no longer happened
        if revision != self._position_revision:
            had_previous_position = self._position_revision is not None
            self._vertical_velocity = 0.0
            self._position_revision = revision

            # Give an established session one tick to accept the corrected position before
            # deriving another move from it. Otherwise every correction immediately recreates
            # the same first gravity step the server just rejected.
            if had_previous_position:
                return None

        x, y, z = position["x"], position["y"], position["z"]
        block_x, block_z = math.floor(x), math.floor(z)

        # no terrain to test against, and guessing risks falling through ground still in transit
        if (block_x >> 4, block_z >> 4) not in world_state["map"]:
            self._vertical_velocity = 0.0
            return None

        floor_y = math.floor(y - self.GROUND_TOLERANCE)

        # standing on something solid, so there is nothing to do this tick
        if not pathfinder._is_passable(pathfinder._get_block(block_x, floor_y, block_z)):
            self._vertical_velocity = 0.0
            return None

        # vanilla's own gravity then drag, in that order, since the server runs the same numbers
        self._vertical_velocity = (self._vertical_velocity - 0.08) * 0.98
        next_y = y + self._vertical_velocity
        landing_y = self._landing_y(block_x, block_z, y, next_y, pathfinder)
        on_ground = landing_y is not None

        # landed within this tick, so stop at the surface rather than overshooting through it
        if on_ground:
            next_y = landing_y
            self._vertical_velocity = 0.0

        return {
            "action": "move",
            "x": x,
            "y": next_y,
            "z": z,
            "on_ground": on_ground,
            "delay": self.TICK_SECONDS,
            "report": False,
        }


    # Scans every block the tick would pass through, top down, rather than only testing the
    # destination. A fast fall covers more than a block per tick, so checking just the endpoint
    # would tunnel straight through a floor. Returns the surface height to stop at, or None if
    # the whole span is clear. The 0.001 nudge keeps a player standing exactly on a boundary
    # from being read as inside the block below it.
    @staticmethod
    def _landing_y(block_x, block_z, current_y, next_y, pathfinder):
        highest = math.floor(current_y - 0.001)
        lowest = math.floor(next_y - 0.001)

        for block_y in range(highest, lowest - 1, -1):
            top = block_y + 1
            block = pathfinder._get_block(block_x, block_y, block_z)

            if (next_y <= top <= current_y and not pathfinder._is_passable(block)):
                return float(top)

        return None
