import os

from authentication import MicrosoftAuthenticator
from bot import Bot
from dotenv import load_dotenv
from realms import RealmResolver
from version_support import load_support_manifest

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

    realm_mode = input("Connect to a Realm? (y/N): ").strip().lower() == "y"
    host, port = "localhost", 25565
    if not realm_mode:
        host = input("Server host (default: localhost): ").strip() or "localhost"
        while True:
            port = input("Port (default: 25565): ").strip() or "25565"
            if port.isdigit() and 1024 <= int(port) <= 65535:
                port = int(port)
                break
            print("Port must be a number between 1024 and 65535")

    session = None
    authenticate = realm_mode or input(
        "Authenticate a Microsoft account for online mode? (y/N): "
    ).strip().lower() == "y"
    if authenticate:
        load_dotenv()
        authenticator = MicrosoftAuthenticator(os.getenv("AMP_MICROSOFT_CLIENT_ID"))
        session = authenticator.authorize()
        username = session.profile_name
    else:
        username = input("Offline username (default: Guest): ").strip() or "Guest"

    if realm_mode:
        manifest = load_support_manifest()
        version = manifest["primary"]
        if version is None:
            raise RuntimeError("No Realm-verified primary Minecraft version is configured")
        realms = RealmResolver().list(session)
        for realm in realms:
            print(f"{realm.id}: {realm.name} ({realm.state})")
        endpoint = RealmResolver().resolve(session, input("Realm name or ID: ").strip())
        host, port = endpoint.host, endpoint.port
    else:
        print(f"\nRunnable versions: {', '.join(sorted(Bot.allowed_values['version']))}")
        version = input(
            f"Version (default: {Bot.default_values['version']}): "
        ).strip() or Bot.default_values["version"]

    print("\nGame modes: survival, creative, superflat, adventure, spectator")
    game_mode = input("Game mode (default: survival): ").strip() or "survival"

    print("\nBehavior modes: passive, aggressive, neutral")
    behavior_mode = input("Behavior mode (default: passive): ").strip() or "passive"

    return {
        "host": host,
        "port": port,
        "username": username,
        "version": version,
        "game_mode": game_mode,
        "behavior_mode": behavior_mode,
        "auth_session": session,
    }

def select_mode():
    print("\n=== Select Mode ===")
    print("1. Guided    — you prompt the bot")
    print("2. Autonomous — bot reasons on its own")

    while True:
        choice = input("Mode (1/2): ").strip()
        if choice in ("1", "2"):
            return "guided" if choice == "1" else "autonomous"
        print("Enter 1 or 2")

def guided_loop(bot):
    print("\n=== Guided Mode ===")
    print("Type your instructions. 'quit' to exit.\n")

    while True:
        user_prompt = input("> ").strip()

        if not user_prompt:
            continue

        if user_prompt.lower() == "quit":
            bot.disconnect()
            print("Disconnected.")
            break

        bot.prompt(user_prompt)

def autonomous_loop(bot):
    print("\n=== Autonomous Mode ===")
    print("Enter a high level goal. The bot will reason and act until complete.")
    print("While running: type new instructions to inject mid-task, 'stop' to end task, 'quit' to disconnect.\n")

    goal = input("Goal: ").strip()
    if not goal:
        print("No goal entered.")
        return

    bot.run(goal)

    try:
        while True:
            user_input = input("> ").strip()

            if not user_input:
                continue

            if user_input.lower() == "quit":
                bot.disconnect()
                print("Disconnected.")
                break

            if user_input.lower() == "stop":
                bot.stop_run()
                print("Stop signal sent.")
                break

            bot.inject(user_input)
            print(f"Injected: '{user_input}'")

    except KeyboardInterrupt:
        bot.disconnect()
        print("\nDisconnected.")

def main():
    config = collect_config()
    bot = Bot(config)
    bot.start()

    mode = select_mode()
    bot.set_mode(mode)

    if mode == "guided":
        guided_loop(bot)
    else:
        autonomous_loop(bot)

if __name__ == "__main__":
    main()
