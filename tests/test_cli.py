import pytest

from amp.cli import autonomous_loop, collect_config, guided_loop


class BotStub:
    def __init__(self):
        self.disconnections = 0
        self.prompts = []
        self.goals = []

    def disconnect(self):
        self.disconnections += 1

    def prompt(self, prompt):
        self.prompts.append(prompt)

    def run(self, goal):
        self.goals.append(goal)


def test_collect_config_exposes_only_implemented_game_modes(monkeypatch, capsys):
    answers = iter([
        "localhost",
        "25565",
        "AMP",
        "26.2",
        "spectator",
        "creative",
    ])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    config = collect_config()

    assert config == {
        "host": "localhost",
        "port": 25565,
        "username": "AMP",
        "version": "26.2",
        "game_mode": "creative",
        "auth_session": None,
    }
    output = capsys.readouterr().out
    assert "Game modes: survival, creative" in output
    assert "Game mode must be survival or creative" in output
    assert "Behavior modes" not in output


@pytest.mark.parametrize("command", ["q", "quit", "Q", "QUIT"])
def test_guided_quit_commands_return_to_connection_owner(monkeypatch, command):
    bot = BotStub()
    monkeypatch.setattr("builtins.input", lambda _prompt: command)

    guided_loop(bot)

    assert bot.disconnections == 0
    assert bot.prompts == []


def test_autonomous_q_returns_to_connection_owner(monkeypatch):
    bot = BotStub()
    replies = iter(("find diamonds", "q"))
    monkeypatch.setattr("builtins.input", lambda _prompt: next(replies))

    autonomous_loop(bot)

    assert bot.goals == ["find diamonds"]
    assert bot.disconnections == 0
