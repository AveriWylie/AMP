import json
import sys
from datetime import datetime

import pytest

from tools import run_local_world
from tools.run_local_world import (
    add_operator,
    ask_yes_no,
    copy_world_back,
    has_operators,
    offline_player_uuid,
    parse_args,
    resolve_startup,
    update_properties,
    validate_model_configuration,
    world_profile,
)


def make_world(path, marker="source"):
    path.mkdir()
    (path / "level.dat").write_text(marker, encoding="utf-8")
    return path


def test_world_profile_is_stable_and_isolates_same_version_worlds(tmp_path):
    first = make_world(tmp_path / "First World")
    second = make_world(tmp_path / "Second World")

    assert world_profile(first, "26.2", tmp_path) == world_profile(
        first, "26.2", tmp_path
    )
    assert world_profile(first, "26.2", tmp_path) != world_profile(
        second, "26.2", tmp_path
    )


def test_update_properties_preserves_unmanaged_settings(tmp_path):
    path = tmp_path / "server.properties"
    path.write_text("difficulty=hard\nserver-port=25565\n", encoding="utf-8")

    update_properties(path, {"server-port": 25576, "online-mode": "false"})

    assert path.read_text(encoding="utf-8") == (
        "difficulty=hard\nserver-port=25576\nonline-mode=false\n"
    )


def test_add_operator_uses_offline_uuid_and_is_idempotent(tmp_path):
    path = tmp_path / "ops.json"

    add_operator(path, "Notch")
    add_operator(path, "Notch")

    assert offline_player_uuid("Notch") == "b50ad385-829d-3141-a216-7e7d7539ba7f"
    assert json.loads(path.read_text(encoding="utf-8")) == [{
        "uuid": "b50ad385-829d-3141-a216-7e7d7539ba7f",
        "name": "Notch",
        "level": 4,
        "bypassesPlayerLimit": False,
    }]
    assert has_operators(path)


def test_empty_operator_list_still_prompts(tmp_path):
    path = tmp_path / "ops.json"
    path.write_text("[]\n", encoding="utf-8")

    assert not has_operators(path)


def test_yes_no_prompt_uses_requested_default():
    assert ask_yes_no("Copy back?", True, lambda _: "")
    assert not ask_yes_no("Allow commands?", False, lambda _: "")


def test_two_blank_mode_answers_start_with_defaults(tmp_path, monkeypatch):
    source = make_world(tmp_path / "world")
    monkeypatch.setattr(run_local_world, "REPO_ROOT", tmp_path)
    args = parse_args([
        "--world", str(source), "--java", sys.executable, "--accept-eula",
    ])
    answers = iter(["n", "", ""])

    resolve_startup(args, lambda _: next(answers))

    assert args.operator is None
    assert args.mode == "guided"
    assert args.amp_game_mode == "survival"


def test_unsupported_version_is_rejected_before_profile_creation(
    tmp_path,
    monkeypatch,
):
    source = make_world(tmp_path / "world")
    monkeypatch.setattr(run_local_world, "REPO_ROOT", tmp_path)
    args = parse_args([
        "--world", str(source), "--java", sys.executable,
        "--version", "../../outside", "--accept-eula",
        "--mode", "idle", "--amp-game-mode", "survival",
    ])

    with pytest.raises(ValueError, match="Unsupported Minecraft version"):
        resolve_startup(args, lambda _: "n")


def test_existing_eula_is_not_requested_again(tmp_path, monkeypatch):
    source = make_world(tmp_path / "world")
    monkeypatch.setattr(run_local_world, "REPO_ROOT", tmp_path)
    run_root = world_profile(source, "26.2")
    run_root.mkdir(parents=True)
    (run_root / "eula.txt").write_text("eula=true\n", encoding="ascii")
    args = parse_args([
        "--world", str(source), "--java", sys.executable,
        "--mode", "idle", "--amp-game-mode", "survival",
    ])
    prompts = []

    resolve_startup(args, lambda prompt: prompts.append(prompt) or "n")

    assert all("EULA" not in prompt for prompt in prompts)


def test_missing_model_configuration_fails_actionably(monkeypatch):
    monkeypatch.setattr("dotenv.load_dotenv", lambda: None)
    monkeypatch.setattr("model_clients.build_model_client", lambda _: None)

    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        validate_model_configuration("guided")


def test_copy_back_preserves_original_as_timestamped_backup(tmp_path):
    source = make_world(tmp_path / "world", "original")
    server = make_world(tmp_path / "server", "played")

    backup = copy_world_back(
        server,
        source,
        now=datetime(2026, 8, 27, 12, 0, 0),
    )

    assert (source / "level.dat").read_text(encoding="utf-8") == "played"
    assert (backup / "level.dat").read_text(encoding="utf-8") == "original"
    assert backup.name == "world.amp-backup-20260827-120000"
