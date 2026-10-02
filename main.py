import sys
from dotenv import load_dotenv


def main():
    load_dotenv()

    if len(sys.argv) < 2:
        print("Usage: python main.py <command>")
        print("Commands:")
        print("  scraper   - Scraper pipeline (python main.py scraper --help)")
        print("  init-db   - Initialize the database schema")
        print("  migrate   - Run Alembic migrations to head")
        return

    cmd = sys.argv[1].lower()

    if cmd == "scraper":
        from app.scraper.cli import main as scraper_main
        sys.exit(scraper_main(sys.argv[2:]))

    elif cmd in ("bot", "worker", "scheduler"):
        # The old bot/worker/scheduler were replaced by the scraper pipeline (see legacy/README.md).
        print(f"'{cmd}' was retired. Use: python main.py scraper {'bot' if cmd == 'bot' else 'up'}")
        sys.exit(2)

    elif cmd == "init-db":
        from app.store.init_core import main as init_main
        init_main()

    elif cmd == "migrate":
        from alembic import command
        from alembic.config import Config

        cfg = Config("alembic.ini")
        command.upgrade(cfg, "head")

    else:
        print(f"Unknown command: {cmd}")


if __name__ == "__main__":
    main()
