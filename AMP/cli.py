from bot import Bot

"""
--------------------------------------------------------------------------------------------
File Header - Interactive CLI
--------------------------------------------------------------------------------------------
No class needed here. A CLI is procedural by nature, it's just a sequence of steps: collect 
input, start the bot, loop. Wrapping it in a class would add structure without adding anything 
useful. Just plain module with functions and a if __name__ == "__main__" entry point at 
the bottom.
--------------------------------------------------------------------------------------------
"""
def collect_config():
    print("=== Minecraft Bot Setup ===")
    print("Press enter to accept defaults\n")

    host = input("Server host (default: localhost): ").strip() or "localhost"

    while True:
        port = input("Port (default: 25565): ").strip() or "25565"
        if port.isdigit() and 1024 <= int(port) <= 65535:
            port = int(port)
            break
        print("Port must be a number between 1024 and 65535")

    username = input("Username (default: Guest): ").strip() or "Guest"

    print("\nValid versions: see mc_versions.txt")
    version = input("Version (default: 1.21.4): ").strip() or "1.21.4"

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
        "behavior_mode": behavior_mode
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
            bot._connection.disconnect()
            print("Disconnected.")
            break

        bot.prompt(user_prompt)

def autonomous_loop(bot):
    print("\n=== Autonomous Mode ===")
    print("Enter a high level goal. The bot will reason and act until complete.\n")

    goal = input("Goal: ").strip()
    if not goal:
        print("No goal entered.")
        return

    try:
        bot.run(goal)
    except KeyboardInterrupt:
        bot._connection.disconnect()
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