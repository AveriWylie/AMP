
# Imports
import argparse
import sys
from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
from amp.bot import Bot


QUIT_COMMANDS = {"q", "quit"}
_prompt_session = None


def _read_input(label):
    global _prompt_session

    # Piped input and test capture are not editable terminal buffers, so ordinary input is the
    # correct behavior there. Interactive terminals need prompt_toolkit to redraw the current
    # line when a connection or worker thread prints in the background.
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return input(label)

    if _prompt_session is None:
        _prompt_session = PromptSession()

    with patch_stdout():
        return _prompt_session.prompt(label)

"""
--------------------------------------------------------------------------------------------
File Header - Interactive CLI
--------------------------------------------------------------------------------------------
No class needed here. A CLI is procedural by nature, it's just a sequence of steps: collect 
input, start the bot, loop. Wrapping it in a class would add structure without adding 
anything useful. Just plain module with functions and a if __name__ == "__main__" entry 
point at the bottom.

CLI calls bot.prompt(user_prompt) in guided mode and bot.run(goal) then bot.inject(prompt) 
in autonomous mode. Bot is the single interface, CLI never touches planner, pathfinder, 
or executor directly.

run is called once with the initial goal to start the autonomous loop on its thread.run is 
called once with the initial goal to start the autonomous loop on its thread. Every 
subsequent input the user types while that loop is running goes through inject. The user 
never calls either directly, the CLI handles it: 

goal = input("Goal: ")   # first input -> bot.run(goal)
...
user_input = input("> ") # all subsequent inputs -> bot.inject(user_input)
So from the user's perspective they just type. run vs inject is an implementation detail 
the CLI abstracts away.
--------------------------------------------------------------------------------------------
"""
def collect_config():
    print("=== Minecraft Bot Setup ===")
    print("Press enter to accept defaults\n")
    print("AMP 1.0 connects to direct offline-mode servers.")
    print("Microsoft-authenticated servers and Realms require external client-ID approval.\n")
    host = input("Server host (default: localhost): ").strip() or "localhost"

    while True:
        port = input("Port (default: 25565): ").strip() or "25565"

        if port.isdigit() and 1024 <= int(port) <= 65535:
            port = int(port)
            break

        print("Port must be a number between 1024 and 65535")

    username = input("Offline username (default: Guest): ").strip() or "Guest"
    print(f"\nRunnable versions: {', '.join(sorted(Bot.allowed_values['version']))}")

    version = (input(f"Version (default: {Bot.default_values['version']}): ").strip()
               or Bot.default_values["version"])

    print("\nGame modes: survival, creative")

    while True:
        game_mode = (input("Game mode (default: survival): ").strip().lower()
                     or Bot.default_values["game_mode"])

        if game_mode in Bot.allowed_values["game_mode"]:
            break

        print("Game mode must be survival or creative")

    return {
        "host": host,
        "port": port,
        "username": username,
        "version": version,
        "game_mode": game_mode,
        "auth_session": None,
    }


def select_mode():
    print("\n=== Select Mode ===")
    print("1. Guided    - you prompt the bot")
    print("2. Autonomous - bot reasons on its own")

    while True:
        choice = _read_input("Mode (1/2): ").strip()

        if choice in ("1", "2"):
            return "guided" if choice == "1" else "autonomous"

        print("Enter 1 or 2")


def guided_loop(bot):
    print("\n=== Guided Mode ===")
    print("Type your instructions. 'q' or 'quit' to exit.\n")

    while True:
        user_prompt = _read_input("> ").strip()

        if not user_prompt:
            continue

        if user_prompt.lower() in QUIT_COMMANDS:
            break

        bot.prompt(user_prompt)


def autonomous_loop(bot):
    print("\n=== Autonomous Mode ===")
    print("Enter a high level goal. The bot will reason and act until complete.")
    print("While running: type new instructions to inject mid-task, 'stop' to end task, 'q' or 'quit' to disconnect.\n")
    goal = _read_input("Goal: ").strip()

    if not goal:
        print("No goal entered.")
        return

    bot.run(goal)

    try:
        while True:
            user_input = _read_input("> ").strip()

            if not user_input:
                continue

            if user_input.lower() in QUIT_COMMANDS:
                break

            if user_input.lower() == "stop":
                bot.stop_run()
                print("Stop signal sent.")
                break

            if bot.is_running():
                bot.inject(user_input)
                print(f"Injected: '{user_input}'")
            else:
                bot.run(user_input)
                print(f"Started new goal: '{user_input}'")

    except KeyboardInterrupt:
        return


def main(argv=None):
    parser = argparse.ArgumentParser(description="Connect AMP to a supported direct Minecraft server.")
    parser.parse_args(argv)
    config = collect_config()
    bot = Bot(config)
    bot.start()
    mode = select_mode()
    bot.set_mode(mode)

    try:
        if mode == "guided":
            guided_loop(bot)
        else:
            autonomous_loop(bot)
    finally:
        bot.disconnect()



if __name__ == "__main__":
    main()
