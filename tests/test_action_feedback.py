"""Execution completion, verification, timeout, and planner feedback tests."""

import threading

from bot import Connection
from execution import Execute
from planner import Planner


def _executor(world_state=None):
    connection = Connection("localhost", 25565, "1.20.2", "Feedback", None, 764)
    connection._send = lambda packet: None
    return Execute(connection, "survival", "passive", world_state=world_state)


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


def test_wait_until_idle_reports_timeout_when_queue_is_not_drained():
    executor = _executor()
    executor.enque_command({"action": "chat", "message": "queued"})
    results = executor.wait_until_idle(timeout=0.01)
    assert results[-1]["success"] is False
    assert "Timed out" in results[-1]["message"]


def test_disconnected_send_is_reported_as_a_failed_result():
    connection = Connection("localhost", 25565, "1.20.2", "Feedback", None, 764)
    executor = Execute(connection, "survival", "passive")
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
