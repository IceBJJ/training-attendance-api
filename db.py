import os
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).with_name("attendance.db")

class DBConn:
    def __init__(self, conn, dialect: str):
        self._conn = conn
        self.dialect = dialect

    def execute(self, query: str, params=()):
        if self.dialect == "postgres":
            query = query.replace("?", "%s")
        cur = self._conn.cursor()
        cur.execute(query, params)
        return cur

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type:
            try:
                self._conn.rollback()
            except Exception:
                pass
        self.close()

def get_conn():
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        import psycopg2
        from psycopg2.extras import RealDictCursor
        conn = psycopg2.connect(db_url, cursor_factory=RealDictCursor)
        return DBConn(conn, "postgres")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return DBConn(conn, "sqlite")

def init_db():
    with get_conn() as conn:
        # Facilities
        conn.execute("""
        CREATE TABLE IF NOT EXISTS facilities (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            address TEXT,
            active INTEGER NOT NULL DEFAULT 1
        )
        """)

        # Locations (one QR per facility for now)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS locations (
            id TEXT PRIMARY KEY,
            facility_id TEXT NOT NULL,
            name TEXT NOT NULL,
            description TEXT,
            qr_value TEXT NOT NULL UNIQUE,
            FOREIGN KEY (facility_id) REFERENCES facilities(id)
        )
        """)

        # Members (with current belt + promotion start date)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS members (
            id TEXT PRIMARY KEY,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            phone TEXT,
            address TEXT,
            belt_rank TEXT,
            promotion_start_date TEXT,
            student_type TEXT NOT NULL CHECK(student_type IN ('adult','youth')),
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            UNIQUE(first_name, last_name, phone)
        )
        """)

        # Safe migration for existing DBs (if members table existed before)
        if conn.dialect == "sqlite":
            cols = [row["name"] for row in conn.execute("PRAGMA table_info(members)").fetchall()]
            if "promotion_start_date" not in cols:
                conn.execute("ALTER TABLE members ADD COLUMN promotion_start_date TEXT")
        else:
            conn.execute("ALTER TABLE members ADD COLUMN IF NOT EXISTS promotion_start_date TEXT")

        # Attendance
        conn.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            facility_id TEXT NOT NULL,
            location_id TEXT NOT NULL,
            check_in_time TEXT NOT NULL,
            check_out_time TEXT,
            member_id TEXT,
            FOREIGN KEY (facility_id) REFERENCES facilities(id),
            FOREIGN KEY (location_id) REFERENCES locations(id)
        )
        """)

        # Safe migration: add member_id if missing
        if conn.dialect == "sqlite":
            att_cols = [row["name"] for row in conn.execute("PRAGMA table_info(attendance)").fetchall()]
            if "member_id" not in att_cols:
                conn.execute("ALTER TABLE attendance ADD COLUMN member_id TEXT")
        else:
            conn.execute("ALTER TABLE attendance ADD COLUMN IF NOT EXISTS member_id TEXT")

        conn.commit()
