import sys


def main():
    if len(sys.argv) < 2:
        print("Usage: python main.py <command>")
        print("Commands:")
        print("  bot       - Run the Telegram bot")
        print("  worker    - Run the Upwork monitor worker")
        print("  scheduler - Run the scheduler (pushes due queries to Redis)")
        print("  init-db   - Initialize the database schema")
        return

    cmd = sys.argv[1].lower()

    if cmd == "bot":
        import asyncio
        from app.notify.bot_uw import main as bot_main
        asyncio.run(bot_main())

    elif cmd == "worker":
        from app.workers.monitor_uw import main as worker_main
        worker_main()

    elif cmd == "scheduler":
        from app.workers.scheduler import main as scheduler_main
        scheduler_main()

    elif cmd == "init-db":
        from app.store.init_core import main as init_main
        init_main()

    else:
        print(f"Unknown command: {cmd}")


if __name__ == "__main__":
    main()
