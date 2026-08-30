"""Execution completion, verification, timeout, and planner feedback tests."""

import threading

from amp.connection import Connection
from amp.bot import Bot
from amp.execution import Execute
from amp.java26_protocol import Java26ProtocolAdapter
from amp.planner import Planner


def _executor(world_state=None, game_mode="survival"):
    connection = Connection("localhost", 25565, "26.1.2", "Feedback", None, 775)
    connection._send = lambda packet: None
    adapter = Java26ProtocolAdapter("java-26.1", "26.1.2", connection)
    return Execute(connection, game_mode, adapter, world_state=world_state)


def test_wait_until_idle_returns_completed_command_result():
    executor = _executor({
        "position": {"x": 0.0, "y": 64.0, "z": 0.0},
        "inventory": {"slots": {}, "selected_hotbar_slot": 0, "state_id": 0},
        "map": {},
    })
    start = executor.result_count()
    executor.enque_command({"action": "move", "x": 1.0, "y": 64.0, "z": 0.0})
    worker = threading.Thread(target=executor.execute_queue)
    worker.start()

    results = executor.wait_until_idle(start, timeout=1)
    worker.join()

    assert results == [{"action": "move", "success": True, "message": "move packet sent"}]


def test_server_movement_correction_cancels_stale_path(monkeypatch, capsys):
    world = {
        "position": {"x": 0.0, "y": 64.0, "z": 0.0},
        "position_revision": 0,
        "inventory": {"slots": {}, "selected_hotbar_slot": 0, "state_id": 0},
        "map": {},
    }
    executor = _executor(world)
    monkeypatch.setattr("amp.execution.time.sleep", lambda seconds: None)

    def correct_position(packet):
        world["position"].update({"x": 0.25, "y": 64.0, "z": 0.0})
        world["position_revision"] += 1

    executor._connection._send = correct_position
    executor.enque_command({"action": "move", "x": 1, "y": 64, "z": 0})
    executor.enque_command({"action": "move", "x": 2, "y": 64, "z": 0})

    result = executor.execute_queue()

    assert result["success"] is False
    assert "corrected" in result["message"]
    assert world["position"] == {"x": 0.25, "y": 64.0, "z": 0.0}
    assert not executor._command_queue
    output = capsys.readouterr().out
    assert "Failed {'action': 'move'" in output
    assert "cancelled stale actions" in output


def test_movement_is_paced_after_sending(monkeypatch):
    world = {
        "position": {"x": 0.0, "y": 64.0, "z": 0.0},
        "position_revision": 0,
        "inventory": {"slots": {}, "selected_hotbar_slot": 0, "state_id": 0},
        "map": {},
    }
    executor = _executor(world)
    events = []
    executor._connection._send = lambda packet: events.append("send")
    monkeypatch.setattr(
        "amp.execution.time.sleep",
        lambda seconds: events.append(("sleep", seconds)),
    )

    result = executor._execute({"action": "move", "x": 1, "y": 64, "z": 0})

    assert result["success"] is True
    assert events == ["send", ("sleep", 0.25)]


def test_successful_internal_movement_tick_is_silent(monkeypatch, capsys):
    world = {
        "position": {"x": 0.0, "y": 64.0, "z": 0.0},
        "position_revision": 0,
        "inventory": {"slots": {}, "selected_hotbar_slot": 0, "state_id": 0},
        "map": {},
    }
    executor = _executor(world)
    monkeypatch.setattr("amp.execution.time.sleep", lambda seconds: None)

    result = executor._execute({
        "action": "move", "x": 0.2, "y": 64, "z": 0,
        "delay": 0.05, "report": False,
    })

    assert result["success"] is True
    assert result["internal"] is True
    assert capsys.readouterr().out == ""


def test_wait_until_idle_reports_timeout_when_queue_is_not_drained():
    executor = _executor()
    executor.enque_command({"action": "chat", "message": "queued"})
    results = executor.wait_until_idle(timeout=0.01)
    assert results[-1]["success"] is False
    assert "Timed out" in results[-1]["message"]


def test_guided_prompt_waits_for_queued_actions():
    bot = Bot({"version": "26.1.2", "model_optional": True})
    bot._planner.plan = lambda prompt: [{"action": "chat", "message": "done"}]
    events = []
    bot._executor.result_count = lambda: events.append("count") or 0
    bot._executor.enque_command = lambda command: events.append("enqueue")
    bot._executor.wait_until_idle = (
        lambda result_start: events.append("wait") or [{
            "action": "chat",
            "success": True,
            "message": "chat packet sent",
        }]
    )

    result = bot.prompt("say done")

    assert events == ["count", "enqueue", "wait"]
    assert result == "Succeeded: chat packet sent"


def test_high_level_actions_execute_before_the_next_action_is_planned():
    bot = Bot({"version": "26.1.2", "model_optional": True})
    events = []
    bot.move_to = lambda goal: events.append(("plan_move", goal)) or True
    bot.place_block = lambda target, block: (
        events.append(("plan_place", target, block)) or True
    )
    bot._executor.result_count = lambda: len(events)
    bot._executor.wait_until_idle = (
        lambda result_start: events.append(("wait", result_start)) or []
    )

    bot._on_step([
        {"action": "go_to", "x": 4, "y": 64, "z": 5},
        {
            "action": "place", "x": 4, "y": 64, "z": 5,
            "block": "dirt",
        },
    ])

    assert events == [
        ("plan_move", (4, 64, 5)),
        ("wait", 0),
        ("plan_place", (4, 64, 5), "dirt"),
        ("wait", 2),
    ]


def test_internal_movement_ticks_do_not_fill_planner_feedback():
    bot = Bot({"version": "26.1.2", "model_optional": True})
    bot.move_to = lambda goal: True
    bot._executor.result_count = lambda: 0
    bot._executor.wait_until_idle = lambda result_start: [
        {
            "action": "move", "success": True,
            "message": "move packet sent", "internal": True,
        },
        {
            "action": "move", "success": True,
            "message": "move packet sent",
        },
    ]

    result = bot._on_step([
        {"action": "go_to", "x": 4, "y": 64, "z": 5},
    ])

    assert result == "Succeeded: move packet sent"


def test_disconnected_send_is_reported_as_a_failed_result():
    connection = Connection("localhost", 25565, "26.1.2", "Feedback", None, 775)
    adapter = Java26ProtocolAdapter("java-26.1", "26.1.2", connection)
    executor = Execute(connection, "survival", adapter)
    executor.enque_command({"action": "chat", "message": "not sent"})

    try:
        executor.execute_queue()
    except ConnectionError:
        pass

    assert executor.wait_until_idle() == [{
        "action": "chat", "success": False,
        "message": "ConnectionError: Cannot send packet while disconnected",
    }]


def test_mining_result_uses_world_block_confirmation():
    class Chunk:
        block = "stone"

        def get_block(self, x, y, z):
            return self.block

    chunk = Chunk()
    world = {"map": {(0, 0): chunk}}
    executor = _executor(world)
    sends = []

    def send(packet):
        sends.append(packet)
        if len(sends) == 2:
            chunk.block = "air"

    executor._connection._send = send
    result = executor._execute({
        "action": "mine", "x": 1, "y": 64, "z": 1, "face": 1, "duration": 0
    })
    assert result["success"] is True
    assert "Mined block" in result["message"]


def test_creative_mining_predicts_air_before_waiting_for_server_update(monkeypatch):
    class Chunk:
        block = "stone"

        def get_block(self, x, y, z):
            return self.block

        def patch_block(self, x, y, z, state_id):
            if state_id == 0:
                self.block = "air"

    chunk = Chunk()
    executor = _executor({"map": {(0, 0): chunk}}, game_mode="creative")
    monkeypatch.setattr(
        executor,
        "_wait_for_block",
        lambda position, expected, timeout=2: expected(chunk.get_block(*position)),
    )

    result = executor._execute({
        "action": "mine", "x": 1, "y": 64, "z": 1,
        "face": 1, "duration": 0,
    })

    assert result["success"] is True
    assert chunk.block == "air"


def test_placement_result_reports_missing_server_update(monkeypatch):
    executor = _executor({"map": {}})
    monkeypatch.setattr(executor, "_wait_for_block", lambda *args, **kwargs: False)
    result = executor._execute({
        "action": "place", "x": 1, "y": 63, "z": 1, "face": 1,
        "target": (1, 64, 1), "block": "oak_planks",
    })
    assert result["success"] is False
    assert "did not appear" in result["message"]


def test_autonomous_loop_passes_real_step_result_to_next_turn():
    planner = Planner({
        "position": {"x": 0, "y": 64, "z": 0}, "health": 20, "food": 20,
        "map": {}, "entities": {}, "inventory": {"slots": {}, "selected_hotbar_slot": 0},
    })
    messages = []
    replies = iter(('[{"action":"chat","message":"hi"}]', "[]"))

    def call_api(message):
        messages.append(message)
        return next(replies)

    planner._call_api = call_api
    planner.plan_loop("say hi", on_step=lambda commands: "Succeeded: chat reached server")

    assert len(messages) == 2
    assert "Last step result: Succeeded: chat reached server" in messages[1]


def test_autonomous_loop_stops_before_reporting_or_planning_another_step(capsys):
    planner = Planner({
        "position": {"x": 0, "y": 64, "z": 0}, "health": 20, "food": 20,
        "map": {}, "entities": {}, "inventory": {"slots": {}, "selected_hotbar_slot": 0},
    })
    calls = []

    def call_api(message):
        calls.append(message)
        return '[{"action":"chat","message":"working"}]'

    def stop_after_step(commands):
        planner.stop()
        return "Succeeded: chat reached server"

    planner._call_api = call_api
    planner.plan_loop("keep working", on_step=stop_after_step)

    assert len(calls) == 1
    assert "Step 1:" not in capsys.readouterr().out


def test_counted_mining_goal_stops_after_requested_successes():
    planner = Planner({
        "position": {"x": 0, "y": 64, "z": 0}, "health": 20, "food": 20,
        "map": {}, "entities": {}, "inventory": {"slots": {}, "selected_hotbar_slot": 0},
    })
    batches = iter((1, 4, 1))
    api_calls = []
    executed = []

    def call_api(message):
        api_calls.append(message)
        count = next(batches)
        return "[" + ",".join(
            f'{{"action":"mine","x":{x},"y":64,"z":0}}'
            for x in range(count)
        ) + "]"

    def execute(commands):
        executed.extend(commands)
        return "; ".join(
            f"Succeeded: Mined block at ({command['x']}, 64, 0)"
            for command in commands
        )

    planner._call_api = call_api
    planner.plan_loop("break 5 grass blocks", on_step=execute)

    assert len(api_calls) == 2
    assert len(executed) == 5


def test_counted_mining_goal_caps_a_model_batch_to_the_remaining_count():
    planner = Planner({
        "position": {"x": 0, "y": 64, "z": 0}, "health": 20, "food": 20,
        "map": {}, "entities": {}, "inventory": {"slots": {}, "selected_hotbar_slot": 0},
    })
    executed = []
    planner._call_api = lambda message: "[" + ",".join(
        f'{{"action":"mine","x":{x},"y":64,"z":0}}'
        for x in range(7)
    ) + "]"

    def execute(commands):
        executed.extend(commands)
        return "; ".join(
            f"Succeeded: Mined block at ({command['x']}, 64, 0)"
            for command in commands
        )

    planner.plan_loop("mine exactly 5 dirt blocks", on_step=execute)

    assert len(executed) == 5
