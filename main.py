# main.py
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel
from datetime import datetime
from typing import Optional
import os

from db import init_db, get_conn

# ----------------------------
# App setup
# ----------------------------
app = FastAPI(title="Training Attendance API (SQLite)")

# CORS: safe for MVP. Later you can lock allow_origins to your real domain(s).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------
# Static file hosting (IMPORTANT FOR RENDER)
# ----------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

# Mount static at /static so it NEVER steals /scan, /docs, etc.
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def home():
    """Serve the mobile check-in page."""
    path = os.path.join(STATIC_DIR, "checkin.html")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="checkin.html not found in ./static")
    return FileResponse(path)


@app.get("/checkin.html")
def checkin_page():
    """Serve the same check-in page at /checkin.html (easy to remember)."""
    path = os.path.join(STATIC_DIR, "checkin.html")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="checkin.html not found in ./static")
    return FileResponse(path)


# ----------------------------
# Startup: init DB
# ----------------------------
@app.on_event("startup")
def startup():
    init_db()


# ----------------------------
# Constants for scan rules
# ----------------------------
IGNORE_MINUTES = 15
FACILITY_MINUTES = 30


# ----------------------------
# Models
# ----------------------------
class ScanRequest(BaseModel):
    qr_value: str
    first_name: str
    last_name: str
    phone: Optional[str] = None
    timestamp: Optional[datetime] = None


# ----------------------------
# Helpers
# ----------------------------
def find_member(conn, first_name: str, last_name: str, phone: Optional[str]):
    fn = (first_name or "").strip()
    ln = (last_name or "").strip()
    ph = (phone or "").strip() if phone else None

    if not fn or not ln:
        return None

    row = conn.execute(
        """
        SELECT id, first_name, last_name, phone, belt_rank, promotion_start_date, student_type, active
        FROM members
        WHERE first_name = ? AND last_name = ?
          AND (? IS NULL OR phone = ?)
        LIMIT 1
        """,
        (fn, ln, ph, ph),
    ).fetchone()
    return row


def facility_name(conn, facility_id: str) -> Optional[str]:
    r = conn.execute(
        "SELECT name FROM facilities WHERE id = ? LIMIT 1",
        (facility_id,),
    ).fetchone()
    return r["name"] if r else None


# ----------------------------
# API endpoints
# ----------------------------
@app.get("/health")
def health():
    return {"ok": True, "time": datetime.utcnow().isoformat()}


@app.get("/facilities")
def list_facilities():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, address, active FROM facilities WHERE active = 1 ORDER BY name"
        ).fetchall()
        return [dict(r) for r in rows]


@app.get("/locations")
def list_locations(facility_id: Optional[str] = None):
    with get_conn() as conn:
        if facility_id:
            rows = conn.execute(
                """
                SELECT id, facility_id, name, description, qr_value
                FROM locations
                WHERE facility_id = ?
                ORDER BY name
                """,
                (facility_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, facility_id, name, description, qr_value
                FROM locations
                ORDER BY facility_id, name
                """
            ).fetchall()

        return [dict(r) for r in rows]


@app.get("/members/lookup")
def member_lookup(first_name: str, last_name: str, phone: Optional[str] = None):
    with get_conn() as conn:
        row = find_member(conn, first_name, last_name, phone)
        if not row:
            return {"valid": False, "reason": "Not found"}
        if row["active"] != 1:
            return {"valid": False, "reason": "Inactive"}
        return {"valid": True, "member": dict(row)}


@app.post("/scan")
def scan_qr(payload: ScanRequest):
    ts = payload.timestamp or datetime.utcnow()

    with get_conn() as conn:
        # 1) Validate member (must exist + active)
        member = find_member(conn, payload.first_name, payload.last_name, payload.phone)
        if not member:
            raise HTTPException(
                status_code=400,
                detail="Member not found. Name (and phone if used) must match membership database.",
            )
        if member["active"] != 1:
            raise HTTPException(status_code=400, detail="Member is inactive.")

        member_id = member["id"]

        # 2) Resolve QR to location/facility
        loc = conn.execute(
            "SELECT id, facility_id FROM locations WHERE qr_value = ? LIMIT 1",
            (payload.qr_value,),
        ).fetchone()

        if not loc:
            raise HTTPException(status_code=400, detail="QR code not recognized")

        facility_id = loc["facility_id"]
        location_id = loc["id"]

        # 3) Apply facility timing rules based on last check-in at same facility
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
                    "user_id": member_id,
                    "facility_id": facility_id,
                    "facility_name": facility_name(conn, facility_id),
                    "minutes_since_last": round(minutes, 2),
                    "timestamp": ts.isoformat(),
                }

            if minutes < FACILITY_MINUTES:
                return {
                    "status": "too_soon",
                    "message": f"Scan blocked (must wait {FACILITY_MINUTES} minutes between check-ins at a facility).",
                    "user_id": member_id,
                    "facility_id": facility_id,
                    "facility_name": facility_name(conn, facility_id),
                    "minutes_since_last": round(minutes, 2),
                    "timestamp": ts.isoformat(),
                }

        # 4) Insert attendance record
        attendance_id = f"ATT_{int(ts.timestamp())}_{member_id}"

        conn.execute(
            """
            INSERT INTO attendance (id, user_id, facility_id, location_id, check_in_time, check_out_time)
            VALUES (?, ?, ?, ?, ?, NULL)
            """,
            (attendance_id, member_id, facility_id, location_id, ts.isoformat()),
        )
        conn.commit()

        return {
            "status": "ok",
            "message": "Attendance recorded",
            "attendance_id": attendance_id,
            "user_id": member_id,
            "member_name": f"{member['first_name']} {member['last_name']}",
            "facility_id": facility_id,
            "facility_name": facility_name(conn, facility_id),
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

