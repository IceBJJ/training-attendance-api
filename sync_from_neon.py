import os
import sqlite3

import psycopg2
from psycopg2.extras import RealDictCursor

from db import DB_PATH, init_db

TABLE_ORDER = ["facilities", "locations", "members", "attendance"]


def get_sqlite_columns(conn, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [r[1] for r in rows]


def fetch_postgres_rows(conn, table: str, columns: list[str]) -> list[dict]:
    col_sql = ", ".join(columns)
    cur = conn.cursor()
    cur.execute(f"SELECT {col_sql} FROM {table}")
    return cur.fetchall()


def sync_table(pg_conn, sqlite_conn, table: str) -> int:
    columns = get_sqlite_columns(sqlite_conn, table)
    rows = fetch_postgres_rows(pg_conn, table, columns)

    placeholders = ", ".join(["?"] * len(columns))
    col_sql = ", ".join(columns)

    sqlite_conn.execute(f"DELETE FROM {table}")
    if rows:
        values = [[row.get(col) for col in columns] for row in rows]
        sqlite_conn.executemany(
            f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders})",
            values,
        )
    return len(rows)


def main() -> None:
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise SystemExit("DATABASE_URL is required (use your Neon connection string).")

    # Ensure local SQLite schema exists by temporarily clearing DATABASE_URL.
    os.environ.pop("DATABASE_URL", None)
    init_db()
    os.environ["DATABASE_URL"] = db_url

    pg_conn = psycopg2.connect(db_url, cursor_factory=RealDictCursor)
    sqlite_conn = sqlite3.connect(DB_PATH)

    try:
        counts = {}
        for table in TABLE_ORDER:
            counts[table] = sync_table(pg_conn, sqlite_conn, table)
        sqlite_conn.commit()
    finally:
        sqlite_conn.close()
        pg_conn.close()

    print("Sync complete.")
    for table in TABLE_ORDER:
        print(f"  {table}: {counts[table]}")
    print(f"SQLite DB: {DB_PATH}")


if __name__ == "__main__":
    main()
