from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from datetime import datetime, timedelta
from typing import Optional, List
import re
import uuid

from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import os


from db import init_db, get_conn

@app.get("/debug/static")
def debug_static():
    return {
        "static_dir": STATIC_DIR,
        "static_dir_exists": os.path.isdir(STATIC_DIR),
        "checkin_exists": os.path.exists(os.path.join(STATIC_DIR, "checkin.html")),
        "static_files": os.listdir(STATIC_DIR) if os.path.isdir(STATIC_DIR) else [],
    }


app = FastAPI(title="Training Attendance API (SQLite)")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

# Serve static assets at /static (does NOT override API routes like /scan)
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/")
def home():
    path = os.path.join(STATIC_DIR, "checkin.html")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="checkin.html not found in ./static")
    return FileResponse(path)

@app.get("/checkin.html")
def checkin_page():
    path = os.path.join(STATIC_DIR, "checkin.html")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="checkin.html not found in ./static")
    return FileResponse(path)


# --- rules ---
IGNORE_MINUTES = 15
FACILITY_MINUTES = 30

def utcnow_iso() -> str:
    return datetime.utcnow().isoformat()

def normalize_name(s: str) -> str:
    return (s or "").strip()

def normalize_phone(s: Optional[str]) -> Optional[str]:
    if not s:
        return None
    digits = re.sub(r"\D+", "", s)
    return digits if digits else None

@app.on_event("startup")
def startup():
    init_db()

# ---------- Models ----------
class ScanRequest(BaseModel):
    qr_value: str
    first_name: str
    last_name: str
    phone: Optional[str] = None
    timestamp: Optional[datetime] = None  # optional override for testing

class MemberCreate(BaseModel):
    first_name: str
    last_name: str
    phone: Optional[str] = None
    address: Optional[str] = None
    belt_rank: Optional[str] = None
    promotion_start_date: Optional[str] = None  # keep as TEXT for now
    student_type: str  # "adult" or "youth"
    active: int = 1

# ---------- Facilities / Locations ----------
@app.get("/facilities")
def list_facilities():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, address, active FROM facilities WHERE active = 1 ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]

@app.get("/locations")
def list_locations(facility_id: Optional[str] = None):
    with get_conn() as conn:
        if facility_id:
            rows = conn.execute(
                "SELECT id, facility_id, name, description, qr_value FROM locations WHERE facility_id = ? ORDER BY id",
                (facility_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, facility_id, name, description, qr_value FROM locations ORDER BY id"
            ).fetchall()
        return [dict(r) for r in rows]

# ---------- Members ----------
@app.get("/members")
def list_members(limit: int = 200):
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, first_name, last_name, phone, address, belt_rank, promotion_start_date,
                   student_type, active, created_at
            FROM members
            ORDER BY last_name, first_name
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

@app.post("/members")
def create_member(payload: MemberCreate):
    fn = normalize_name(payload.first_name)
    ln = normalize_name(payload.last_name)
    ph = normalize_phone(payload.phone)

    if not fn or not ln:
        raise HTTPException(status_code=400, detail="first_name and last_name are required")

    # Generate a safe ID (avoid primary key collisions)
    member_id = f"MBR_{uuid.uuid4().hex[:12].upper()}"

    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO members (
              id, first_name, last_name, phone, address, belt_rank,
              promotion_start_date, student_type, active, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                member_id,
                fn,
                ln,
                ph,
                payload.address,
                payload.belt_rank,
                payload.promotion_start_date,
                payload.student_type.strip(),
                int(payload.active),
                utcnow_iso(),
            ),
        )
        conn.commit()

    return {"status": "ok", "member_id": member_id}

@app.get("/members/lookup")
def member_lookup(first_name: str, last_name: str, phone: Optional[str] = None):
    fn = normalize_name(first_name)
    ln = normalize_name(last_name)
    ph = normalize_phone(phone)

    with get_conn() as conn:
        # Case-insensitive match for names
        if ph:
            row = conn.execute(
                """
                SELECT id, first_name, last_name, phone, address, belt_rank, promotion_start_date, student_type, active, created_at
                FROM members
                WHERE LOWER(first_name) = LOWER(?) AND LOWER(last_name) = LOWER(?) AND phone = ?
                LIMIT 1
                """,
                (fn, ln, ph),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT id, first_name, last_name, phone, address, belt_rank, promotion_start_date, student_type, active, created_at
                FROM members
                WHERE LOWER(first_name) = LOWER(?) AND LOWER(last_name) = LOWER(?)
                LIMIT 1
                """,
                (fn, ln),
            ).fetchone()

        if not row:
            return {"valid": False, "reason": "Not found"}
        if row["active"] != 1:
            return {"valid": False, "reason": "Inactive"}

        return {"valid": True, "member": dict(row)}

# ---------- Scan / Attendance ----------
@app.post("/scan")
def scan_qr(payload: ScanRequest):
    ts = payload.timestamp or datetime.utcnow()

    fn = normalize_name(payload.first_name)
    ln = normalize_name(payload.last_name)
    ph = normalize_phone(payload.phone)

    # 1) Validate member exists
    with get_conn() as conn:
        if ph:
            member = conn.execute(
                """
                SELECT id, first_name, last_name, student_type, belt_rank, active
                FROM members
                WHERE LOWER(first_name) = LOWER(?) AND LOWER(last_name) = LOWER(?) AND phone = ?
                LIMIT 1
                """,
                (fn, ln, ph),
            ).fetchone()
        else:
            member = conn.execute(
                """
                SELECT id, first_name, last_name, student_type, belt_rank, active
                FROM members
                WHERE LOWER(first_name) = LOWER(?) AND LOWER(last_name) = LOWER(?)
                LIMIT 1
                """,
                (fn, ln),
            ).fetchone()

        if not member or member["active"] != 1:
            raise HTTPException(
                status_code=400,
                detail="Name (and phone if used) must match membership database"
            )

        member_id = member["id"]

        # 2) Find location from QR
        loc = conn.execute(
            "SELECT id, facility_id FROM locations WHERE qr_value = ?",
            (payload.qr_value,),
        ).fetchone()
        if not loc:
            raise HTTPException(status_code=400, detail="QR code not recognized")

        facility_id = loc["facility_id"]
        location_id = loc["id"]

        # 3) Apply ignore/block rules per facility
        last = conn.execute(
            """
            SELECT id, check_in_time
            FROM attendance
            WHERE user_id = ? AND facility_id = ?
            ORDER BY check_in_time DESC
            LIMIT 1
            """,
            (member_id, facility_id),
        ).fetchone()

        if last:
            last_time = datetime.fromisoformat(last["check_in_time"])
            minutes = (ts - last_time).total_seconds() / 60.0

            if minutes < IGNORE_MINUTES:
                return {
                    "status": "ignored",
                    "message": f"Scan ignored (within {IGNORE_MINUTES} minutes).",
                    "member_id": member_id,
                    "member_name": f"{member['first_name']} {member['last_name']}",
                    "facility_id": facility_id,
                    "minutes_since_last": round(minutes, 2),
                    "timestamp": ts.isoformat(),
                }

            if minutes < FACILITY_MINUTES:
                return {
                    "status": "too_soon",
                    "message": f"Scan blocked (must wait {FACILITY_MINUTES} minutes between check-ins at a facility).",
                    "member_id": member_id,
                    "member_name": f"{member['first_name']} {member['last_name']}",
                    "facility_id": facility_id,
                    "minutes_since_last": round(minutes, 2),
                    "timestamp": ts.isoformat(),
                }

        # 4) Insert attendance
        attendance_id = f"ATT_{int(ts.timestamp())}_{member_id}"
        conn.execute(
            """
            INSERT INTO attendance (id, user_id, facility_id, location_id, check_in_time, check_out_time)
            VALUES (?, ?, ?, ?, ?, NULL)
            """,
            (attendance_id, member_id, facility_id, location_id, ts.isoformat()),
        )
        conn.commit()

        # Optional: facility name for prettier UI
        fac = conn.execute("SELECT name FROM facilities WHERE id = ? LIMIT 1", (facility_id,)).fetchone()
        facility_name = fac["name"] if fac else facility_id

    return {
        "status": "ok",
        "message": "Attendance recorded",
        "attendance_id": attendance_id,
        "member_id": member_id,
        "member_name": f"{member['first_name']} {member['last_name']}",
        "facility_id": facility_id,
        "facility_name": facility_name,
        "location_id": location_id,
        "check_in_time": ts.isoformat(),
    }

@app.get("/attendance")
def list_attendance(limit: int = 100):
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, user_id, facility_id, location_id, check_in_time, check_out_time
            FROM attendance
            ORDER BY check_in_time DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
