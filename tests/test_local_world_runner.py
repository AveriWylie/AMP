import json
import os
import sys
import zipfile
from datetime import datetime
from types import SimpleNamespace

import pytest

from amp import local_world as run_local_world
from amp.local_world import (
    add_operator,
    ask_yes_no,
    copy_world_back,
    has_operators,
    offline_player_uuid,
    parse_args,
    prepare_server,
    resolve_startup,
    update_properties,
    validate_server_java,
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


def test_world_profile_folds_case_on_macos(tmp_path, monkeypatch):
    world = make_world(tmp_path / "My World")
    monkeypatch.setattr(run_local_world.sys, "platform", "darwin")

    def identity(path):
        return world_profile(path, "26.2", tmp_path).name.split("-")[-2]

    assert identity(world) == identity(tmp_path / "my world")


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="os.path.normcase folds case on Windows regardless of the patch",
)
def test_world_profile_keeps_case_on_case_sensitive_platforms(tmp_path, monkeypatch):
    world = make_world(tmp_path / "My World")
    monkeypatch.setattr(run_local_world.sys, "platform", "linux")

    def identity(path):
        return world_profile(path, "26.2", tmp_path).name.split("-")[-2]

    assert identity(world) != identity(tmp_path / "my world")


def test_validate_model_configuration_reads_env_from_working_directory(
    tmp_path, monkeypatch
):
    (tmp_path / ".env").write_text(
        "AMP_MODEL_PROVIDER=openai-compatible\n"
        "OPENAI_BASE_URL=https://example.invalid/v1\n"
        "OPENAI_MODEL=test-model\n",
        encoding="utf-8",
    )
    provider_names = {
        "AMP_MODEL_PROVIDER", "OPENAI_BASE_URL", "OPENAI_MODEL",
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    }
    # Replace the mapping outright so load_dotenv cannot leak into later tests.
    monkeypatch.setattr(os, "environ", {
        name: value for name, value in os.environ.items()
        if name not in provider_names
    })
    monkeypatch.chdir(tmp_path)

    run_local_world.validate_model_configuration("guided")

    assert os.environ["AMP_MODEL_PROVIDER"] == "openai-compatible"


def test_update_properties_preserves_unmanaged_settings(tmp_path):
    path = tmp_path / "server.properties"
    path.write_text("difficulty=hard\nserver-port=25565\n", encoding="utf-8")

    update_properties(path, {"server-port": 25576, "online-mode": "false"})

    assert path.read_text(encoding="utf-8") == (
        "difficulty=hard\nserver-port=25576\nonline-mode=false\n"
    )


def test_local_server_allows_amp_packet_movement(tmp_path, monkeypatch):
    source = make_world(tmp_path / "source")
    run_root = tmp_path / "run"
    server_jar = tmp_path / "server-jars" / "26.2" / "server.jar"
    server_jar.parent.mkdir(parents=True)
    server_jar.write_bytes(b"server")
    monkeypatch.setattr(run_local_world, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(run_local_world, "validate_server_java", lambda *args: None)
    args = SimpleNamespace(
        refresh_world_copy=False,
        version="26.2",
        java="java",
        port=25576,
        operator=None,
    )

    prepare_server(args, source, run_root)

    properties = (run_root / "server.properties").read_text(encoding="utf-8")
    assert "allow-flight=true\n" in properties


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
    monkeypatch.setattr(run_local_world, "DATA_ROOT", tmp_path)
    args = parse_args([
        "--world", str(source), "--java", sys.executable, "--accept-eula",
    ])
    answers = iter(["n", "", ""])

    resolve_startup(args, lambda _: next(answers))

    assert args.operator is None
    assert args.mode == "guided"
    assert args.amp_game_mode == "survival"


def test_mode_prompts_show_and_accept_shortcuts(tmp_path, monkeypatch):
    source = make_world(tmp_path / "world")
    monkeypatch.setattr(run_local_world, "DATA_ROOT", tmp_path)
    args = parse_args([
        "--world", str(source), "--java", sys.executable, "--accept-eula",
    ])
    prompts = []
    answers = iter(["n", "a", "c"])

    def answer(prompt):
        prompts.append(prompt)
        return next(answers)

    resolve_startup(args, answer)

    assert args.mode == "autonomous"
    assert args.amp_game_mode == "creative"
    assert "AMP mode ([G]uided/[a]utonomous/[i]dle): " in prompts
    assert "AMP gameplay mode ([S]urvival/[c]reative): " in prompts


def test_run_amp_enters_guided_loop_before_disconnecting(monkeypatch):
    events = []

    class Bot:
        def __init__(self, config):
            events.append(("init", config["host"], config["port"]))

        def start(self):
            events.append("start")

        @staticmethod
        def is_connected():
            return True

        def set_mode(self, mode):
            events.append(("mode", mode))

        def disconnect(self):
            events.append("disconnect")

    monkeypatch.setattr("amp.bot.Bot", Bot)
    monkeypatch.setattr("amp.cli.guided_loop", lambda bot: events.append("guided"))
    monkeypatch.setattr(
        "amp.cli.autonomous_loop",
        lambda bot: events.append("autonomous"),
    )
    args = SimpleNamespace(
        port=25576,
        username="AMP",
        version="26.2",
        amp_game_mode="survival",
        mode="guided",
    )

    run_local_world.run_amp(args)

    assert events == [
        ("init", "127.0.0.1", 25576),
        "start",
        ("mode", "guided"),
        "guided",
        "disconnect",
    ]


def test_unsupported_version_is_rejected_before_profile_creation(
    tmp_path,
    monkeypatch,
):
    source = make_world(tmp_path / "world")
    monkeypatch.setattr(run_local_world, "DATA_ROOT", tmp_path)
    args = parse_args([
        "--world", str(source), "--java", sys.executable,
        "--version", "../../outside", "--accept-eula",
        "--mode", "idle", "--amp-game-mode", "survival",
    ])

    with pytest.raises(ValueError, match="Unsupported Minecraft version"):
        resolve_startup(args, lambda _: "n")


def test_existing_eula_is_not_requested_again(tmp_path, monkeypatch):
    source = make_world(tmp_path / "world")
    monkeypatch.setattr(run_local_world, "DATA_ROOT", tmp_path)
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
    monkeypatch.setattr("dotenv.load_dotenv", lambda *_, **__: None)
    monkeypatch.setattr("amp.model_clients.build_model_client", lambda _: None)

    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        validate_model_configuration("guided")


def test_server_rejects_java_older_than_jar_requires(tmp_path, monkeypatch):
    server_jar = tmp_path / "server.jar"
    class_file = b"\xca\xfe\xba\xbe\x00\x00\x00\x45"
    with zipfile.ZipFile(server_jar, "w") as archive:
        archive.writestr("net/minecraft/bundler/Main.class", class_file)
    completed = run_local_world.subprocess.CompletedProcess(
        ["java", "-version"], 0, stdout='java version "22.0.2"'
    )
    monkeypatch.setattr(run_local_world.subprocess, "run", lambda *_, **__: completed)

    with pytest.raises(ValueError, match="requires Java 25.*found Java 22"):
        validate_server_java("java", server_jar, "26.2")


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
