from cli import collect_config


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
