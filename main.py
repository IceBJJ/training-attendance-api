from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from datetime import datetime
from typing import Optional

from fastapi.staticfiles import StaticFiles


from db import init_db, get_conn

app = FastAPI(title="Training Attendance API (SQLite)")

app.mount("/", StaticFiles(directory="static", html=True), name="static")


@app.on_event("startup")
def startup():
    init_db()


# --- Request model for scanning ---
class ScanRequest(BaseModel):
    qr_value: str
    first_name: str
    last_name: str
    phone: Optional[str] = None
    event: str = "check_in"   # reserved for future: "check_out"
    timestamp: Optional[datetime] = None


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
                (facility_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, facility_id, name, description, qr_value FROM locations ORDER BY id"
            ).fetchall()

        return [dict(r) for r in rows]


IGNORE_MINUTES = 15
FACILITY_MINUTES = 30


@app.post("/scan")
def scan_qr(payload: ScanRequest):
    ts = payload.timestamp or datetime.utcnow()

    fn = payload.first_name.strip()
    ln = payload.last_name.strip()
    ph = payload.phone.strip() if payload.phone else None

    if not fn or not ln:
        raise HTTPException(status_code=400, detail="first_name and last_name are required")

    with get_conn() as conn:
        # 1) Validate member exists and is active
        member = conn.execute(
            """
            SELECT id, active
            FROM members
            WHERE first_name = ? AND last_name = ?
              AND (? IS NULL OR phone = ?)
            LIMIT 1
            """,
            (fn, ln, ph, ph)
        ).fetchone()

        if not member:
            raise HTTPException(
                status_code=400,
                detail="Member not found. Name (and phone if provided) must match membership database."
            )
        if member["active"] != 1:
            raise HTTPException(status_code=400, detail="Member is inactive.")

        member_id = member["id"]
        user_display = f"{fn} {ln}"  # keep readable user_id value too

        # 2) Find the location from the QR value
        loc = conn.execute(
            "SELECT id, facility_id FROM locations WHERE qr_value = ?",
            (payload.qr_value,)
        ).fetchone()

        if not loc:
            raise HTTPException(status_code=400, detail="QR code not recognized")

        facility_id = loc["facility_id"]
        location_id = loc["id"]

        # 3) Enforce 15/30 minute rules (per member+facility)
        last = conn.execute(
            """
            SELECT id, check_in_time
            FROM attendance
            WHERE member_id = ? AND facility_id = ?
            ORDER BY check_in_time DESC
            LIMIT 1
            """,
            (member_id, facility_id)
        ).fetchone()

        if last:
            last_time = datetime.fromisoformat(last["check_in_time"])
            minutes = (ts - last_time).total_seconds() / 60.0

            if minutes < IGNORE_MINUTES:
                return {
                    "status": "ignored",
                    "message": f"Scan ignored (within {IGNORE_MINUTES} minutes).",
                    "member_id": member_id,
                    "user_id": user_display,
                    "facility_id": facility_id,
                    "last_attendance_id": last["id"],
                    "minutes_since_last": round(minutes, 2),
                    "timestamp": ts.isoformat()
                }

            if minutes < FACILITY_MINUTES:
                return {
                    "status": "too_soon",
                    "message": f"Scan blocked (must wait {FACILITY_MINUTES} minutes between check-ins at a facility).",
                    "member_id": member_id,
                    "user_id": user_display,
                    "facility_id": facility_id,
                    "last_attendance_id": last["id"],
                    "minutes_since_last": round(minutes, 2),
                    "timestamp": ts.isoformat()
                }

        # 4) Record attendance
        attendance_id = f"ATT_{int(ts.timestamp())}_{member_id}"

        conn.execute(
            """
            INSERT INTO attendance (id, member_id, user_id, facility_id, location_id, check_in_time, check_out_time)
            VALUES (?, ?, ?, ?, ?, ?, NULL)
            """,
            (attendance_id, member_id, user_display, facility_id, location_id, ts.isoformat())
        )
        conn.commit()

    return {
        "status": "ok",
        "message": "Attendance recorded",
        "attendance_id": attendance_id,
        "member_id": member_id,
        "user_id": user_display,
        "facility_id": facility_id,
        "location_id": location_id,
        "check_in_time": ts.isoformat()
    }


@app.get("/attendance")
def list_attendance(limit: int = 100):
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, member_id, user_id, facility_id, location_id, check_in_time, check_out_time
            FROM attendance
            ORDER BY check_in_time DESC
            LIMIT ?
            """,
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


@app.get("/members/lookup")
def member_lookup(first_name: str, last_name: str, phone: Optional[str] = None):
    fn = first_name.strip()
    ln = last_name.strip()
    ph = phone.strip() if phone else None

    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT id, first_name, last_name, phone, belt_rank, promotion_start_date, student_type, active
            FROM members
            WHERE first_name = ? AND last_name = ?
              AND (? IS NULL OR phone = ?)
            LIMIT 1
            """,
            (fn, ln, ph, ph)
        ).fetchone()

        if not row:
            return {"valid": False, "reason": "Not found"}
        if row["active"] != 1:
            return {"valid": False, "reason": "Inactive"}

        return {"valid": True, "member": dict(row)}
