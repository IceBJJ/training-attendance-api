import os
import sqlite3

import psycopg2

from db import DB_PATH


TABLES = [
    ("facilities", ["id", "name", "address", "active"], ["name", "address", "active"]),
    ("locations", ["id", "facility_id", "name", "description", "qr_value"], ["facility_id", "name", "description", "qr_value"]),
    ("members", [
        "id", "first_name", "last_name", "phone", "address",
        "belt_rank", "promotion_start_date", "student_type",
        "active", "created_at"
    ], [
        "first_name", "last_name", "phone", "address",
        "belt_rank", "promotion_start_date", "student_type",
        "active", "created_at"
    ]),
    ("attendance", [
        "id", "user_id", "facility_id", "location_id",
        "check_in_time", "check_out_time", "member_id"
    ], []),
]


def fetch_sqlite_rows(conn, table: str, columns: list[str]) -> list[tuple]:
    col_sql = ", ".join(columns)
    cur = conn.execute(f"SELECT {col_sql} FROM {table}")
    return cur.fetchall()


def upsert_postgres(conn, table: str, columns: list[str], update_cols: list[str], rows: list[tuple]) -> int:
    if not rows:
        return 0
    col_sql = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    if update_cols:
        update_sql = ", ".join([f"{col} = EXCLUDED.{col}" for col in update_cols])
        sql = f"""
            INSERT INTO {table} ({col_sql})
            VALUES ({placeholders})
            ON CONFLICT (id) DO UPDATE SET {update_sql}
        """
    else:
        sql = f"""
            INSERT INTO {table} ({col_sql})
            VALUES ({placeholders})
            ON CONFLICT (id) DO NOTHING
        """
    cur = conn.cursor()
    for row in rows:
        cur.execute(sql, row)
    return len(rows)


def load_neon_member_map(conn) -> dict[tuple, str]:
    cur = conn.cursor()
    cur.execute("SELECT id, first_name, last_name, phone FROM members")
    mapping = {}
    for row in cur.fetchall():
        key = (row[1], row[2], row[3])
        mapping[key] = row[0]
    return mapping


def load_local_member_map(conn) -> dict[str, tuple]:
    cur = conn.execute("SELECT id, first_name, last_name, phone FROM members")
    mapping = {}
    for row in cur.fetchall():
        mapping[row[0]] = (row[1], row[2], row[3])
    return mapping


def main() -> None:
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise SystemExit("DATABASE_URL is required (use your Neon connection string).")

    sqlite_conn = sqlite3.connect(DB_PATH)
    pg_conn = psycopg2.connect(db_url)

    try:
        neon_member_by_key = load_neon_member_map(pg_conn)
        local_member_by_id = load_local_member_map(sqlite_conn)
        counts = {}
        for table, columns, update_cols in TABLES:
            rows = fetch_sqlite_rows(sqlite_conn, table, columns)

            if table == "members":
                remapped = []
                for row in rows:
                    row = list(row)
                    key = (row[1], row[2], row[3])
                    existing_id = neon_member_by_key.get(key)
                    if existing_id:
                        row[0] = existing_id
                    remapped.append(tuple(row))
                rows = remapped

            if table == "attendance":
                remapped = []
                for row in rows:
                    row = list(row)
                    local_key = local_member_by_id.get(row[1])
                    if local_key:
                        mapped_id = neon_member_by_key.get(local_key)
                        if mapped_id:
                            row[1] = mapped_id
                    remapped.append(tuple(row))
                rows = remapped

            counts[table] = upsert_postgres(pg_conn, table, columns, update_cols, rows)
        pg_conn.commit()
    finally:
        sqlite_conn.close()
        pg_conn.close()

    print("Sync to Neon complete.")
    for table, _, _ in TABLES:
        print(f"  {table}: {counts[table]}")
    print(f"SQLite DB: {DB_PATH}")


if __name__ == "__main__":
    main()
