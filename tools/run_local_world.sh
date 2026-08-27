#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
world_path="${AMP_WORLD_PATH:-}"
mc_version="${AMP_MC_VERSION:-26.2}"
server_port="${AMP_SERVER_PORT:-25565}"
bot_username="${AMP_BOT_USERNAME:-AMP}"
game_mode="${AMP_GAME_MODE:-survival}"
java_path="${AMP_JAVA_PATH:-java}"
python_path="${AMP_PYTHON_PATH:-python3}"
refresh_world=0
accept_eula=0

usage() {
    echo "Usage: $0 [--world PATH] [--version VERSION] [--port PORT] [--username NAME] [--game-mode survival|creative] --accept-eula [--refresh-world-copy]"
}

while (($#)); do
    case "$1" in
        --world) world_path="$2"; shift 2 ;;
        --version) mc_version="$2"; shift 2 ;;
        --port) server_port="$2"; shift 2 ;;
        --username) bot_username="$2"; shift 2 ;;
        --game-mode) game_mode="$2"; shift 2 ;;
        --accept-eula) accept_eula=1; shift ;;
        --refresh-world-copy) refresh_world=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

[[ -n "$world_path" ]] || { echo "Supply --world or set AMP_WORLD_PATH." >&2; exit 2; }
((accept_eula)) || { echo "Read https://aka.ms/MinecraftEULA, then rerun with --accept-eula if you agree." >&2; exit 2; }
world_path="$(cd "$world_path" && pwd)"
[[ -f "$world_path/level.dat" ]] || { echo "Not a Minecraft world: $world_path" >&2; exit 2; }
command -v "$java_path" >/dev/null 2>&1 || [[ -x "$java_path" ]] || { echo "Java not found: $java_path" >&2; exit 2; }
command -v "$python_path" >/dev/null 2>&1 || [[ -x "$python_path" ]] || { echo "Python not found: $python_path" >&2; exit 2; }
[[ "$server_port" =~ ^[0-9]+$ ]] && ((server_port >= 1024 && server_port <= 65535)) || { echo "Invalid port: $server_port" >&2; exit 2; }
[[ "$game_mode" == survival || "$game_mode" == creative ]] || { echo "Invalid game mode: $game_mode" >&2; exit 2; }

run_root="$repo_root/.tmp/local-world-$mc_version"
server_world="$run_root/world"
mkdir -p "$run_root"
if ((refresh_world)) && [[ -d "$server_world" ]]; then
    resolved_run="$(cd "$run_root" && pwd -P)"
    resolved_world="$(cd "$server_world" && pwd -P)"
    [[ "$resolved_world" == "$resolved_run"/* ]] || { echo "Refusing to remove a world outside $resolved_run" >&2; exit 2; }
    rm -rf -- "$resolved_world"
fi
if [[ ! -d "$server_world" ]]; then
    echo "Copying the world. The original save will not be modified..."
    mkdir -p "$server_world"
    cp -a "$world_path/." "$server_world/"
else
    echo "Reusing server world copy at $server_world"
fi

server_jar="$run_root/server.jar"
if [[ ! -f "$server_jar" ]]; then
    echo "Downloading and verifying the official Minecraft $mc_version server..."
    "$python_path" "$repo_root/tools/download_server.py" "$mc_version" "$server_jar"
fi

printf 'eula=true\n' >"$run_root/eula.txt"
cat >"$run_root/server.properties" <<EOF
server-port=$server_port
server-ip=127.0.0.1
level-name=world
online-mode=false
enforce-secure-profile=false
motd=AMP local world
spawn-protection=0
EOF

server_pid=""
cleanup() {
    if [[ -n "$server_pid" ]] && kill -0 "$server_pid" 2>/dev/null; then
        echo "Stopping the local Minecraft server..."
        kill "$server_pid" 2>/dev/null || true
        wait "$server_pid" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

if "$python_path" -c "import socket; s=socket.socket(); s.settimeout(.25); result=s.connect_ex(('127.0.0.1',$server_port)); s.close(); raise SystemExit(result)"; then
    echo "Port $server_port is already in use. Stop that server or select another port." >&2
    exit 1
fi
echo "Starting the local server on port $server_port..."
(
    cd "$run_root"
    exec "$java_path" -Xms1G -Xmx2G -jar server.jar nogui
) >"$run_root/server-console.log" 2>"$run_root/server-error.log" &
server_pid=$!

ready=0
for _ in {1..90}; do
    kill -0 "$server_pid" 2>/dev/null || { echo "Server exited; inspect $run_root/server-console.log" >&2; exit 1; }
    if "$python_path" -c "import socket; s=socket.socket(); s.settimeout(.25); result=s.connect_ex(('127.0.0.1',$server_port)); s.close(); raise SystemExit(result)"; then
        ready=1
        break
    fi
    sleep 2
done
((ready)) || { echo "Server did not open port $server_port within 3 minutes." >&2; exit 1; }

echo "Starting $bot_username. Join localhost:$server_port with Minecraft $mc_version."
"$python_path" "$repo_root/tools/hold_bot.py" \
    --host 127.0.0.1 --port "$server_port" --username "$bot_username" \
    --version "$mc_version" --game-mode "$game_mode"
