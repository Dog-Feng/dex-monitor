#!/usr/bin/env python3
"""Initialize SQLite schema."""

from app.config import load_config
from app.models.sqlite import connect, init_db


def main() -> None:
    config = load_config("config.yaml")
    db_path = config.get("sqlite", {}).get("path", "data/monitor.db")
    conn = connect(db_path)
    init_db(conn)
    print(f"Initialized {db_path}")


if __name__ == "__main__":
    main()
