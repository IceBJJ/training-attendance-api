from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel
from datetime import datetime, timedelta
from typing import Optional, List, Dict
import re
import uuid
from urllib.parse import urlparse, parse_qs

from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import os


from db import init_db, get_conn

app = FastAPI(title="Training Attendance API (SQLite)")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

# Serve static assets at /static (does NOT override API routes like /scan)
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/debug/static")
def debug_static():
    return {
        "static_dir": STATIC_DIR,
        "static_dir_exists": os.path.isdir(STATIC_DIR),
        "checkin_exists": os.path.exists(os.path.join(STATIC_DIR, "checkin.html")),
        "static_files": os.listdir(STATIC_DIR) if os.path.isdir(STATIC_DIR) else [],
    }

@app.get("/admin")
def admin_page():
    path = os.path.join(STATIC_DIR, "admin.html")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="admin.html not found in ./static")
    return FileResponse(path)

@app.get("/qr/{facility_id}")
def qr_print_page(facility_id: str):
    path = os.path.join(STATIC_DIR, "qr_print.html")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="qr_print.html not found in ./static")
    return FileResponse(path)

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
    if len(digits) > 10:
        digits = digits[-10:]
    return digits if digits else None

def normalize_qr_value(s: str) -> str:
    # Accept raw codes or URLs; extract the meaningful token for lookup.
    raw = (s or "").strip().strip('"').strip("'")
    if not raw:
        return raw
    if raw.lower().startswith(("http://", "https://")):
        parsed = urlparse(raw)
        params = parse_qs(parsed.query)
        for key in ("qr", "code", "value"):
            if key in params and params[key]:
                return params[key][0].strip()
        path = parsed.path.strip("/")
        if path:
            return path.split("/")[-1]
    return raw

def slugify(s: str) -> str:
    value = (s or "").strip().upper()
    value = re.sub(r"[^A-Z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value

def default_facility_id(name: str) -> str:
    return f"FAC_{slugify(name)}"

def default_location_id(facility_id: str) -> str:
    suffix = facility_id
    if suffix.startswith("FAC_"):
        suffix = suffix[4:]
    return f"LOC_{suffix}_CHECKIN"

def default_qr_value(facility_id: str) -> str:
    return f"QR_{facility_id}"

def parse_promotion_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    v = value.strip()
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        try:
            return datetime.strptime(v, "%Y-%m-%d")
        except ValueError:
            return None

def months_since(start: datetime, end: datetime) -> int:
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day >= start.day:
        months += 1
    return max(0, months)

def require_admin(request: Request) -> None:
    expected = os.getenv("ADMIN_PASSWORD")
    if not expected:
        raise HTTPException(status_code=500, detail="Admin password not configured")
    provided = request.headers.get("x-admin-password")
    if provided != expected:
        raise HTTPException(status_code=401, detail="Invalid admin password")

def find_member_by_name(conn, first_name: str, last_name: str, phone: Optional[str]):
    # Match names case-insensitively; if phone is provided, compare normalized digits.
    rows = conn.execute(
        """
        SELECT id, first_name, last_name, phone, address, belt_rank, promotion_start_date, student_type, active, created_at
        FROM members
        WHERE LOWER(first_name) = LOWER(?) AND LOWER(last_name) = LOWER(?)
        """,
        (first_name, last_name),
    ).fetchall()

    if not rows:
        return None

    if phone:
        for row in rows:
            if normalize_phone(row["phone"]) == phone:
                return row
        return None

    return rows[0]

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

class FacilityCreate(BaseModel):
    id: Optional[str] = None
    name: str
    address: Optional[str] = None
    active: int = 1
    create_location: bool = True
    location_id: Optional[str] = None
    location_name: Optional[str] = "Check-in"
    location_description: Optional[str] = "Facility check-in QR"
    qr_value: Optional[str] = None

class LocationCreate(BaseModel):
    id: str
    facility_id: str
    name: str
    description: Optional[str] = None
    qr_value: str

class FacilityUpdate(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None
    active: Optional[int] = None

class MemberUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    belt_rank: Optional[str] = None
    promotion_start_date: Optional[str] = None
    student_type: Optional[str] = None
    active: Optional[int] = None

# ---------- Facilities / Locations ----------
@app.get("/facilities")
def list_facilities():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, address, active FROM facilities WHERE active = 1 ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]

@app.get("/admin/facilities")
def admin_list_facilities(request: Request):
    require_admin(request)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, address, active FROM facilities ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]

@app.post("/admin/facilities")
def create_facility(payload: FacilityCreate, request: Request):
    require_admin(request)

    facility_id = payload.id or default_facility_id(payload.name)
    location_id = payload.location_id or default_location_id(facility_id)
    qr_value = payload.qr_value or default_qr_value(facility_id)

    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM facilities WHERE id = ?",
            (facility_id,),
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="Facility already exists")

        conn.execute(
            "INSERT INTO facilities (id, name, address, active) VALUES (?, ?, ?, ?)",
            (facility_id, payload.name.strip(), payload.address, int(payload.active)),
        )

        created_location = None
        if payload.create_location:
            loc_existing = conn.execute(
                "SELECT id FROM locations WHERE id = ? OR qr_value = ?",
                (location_id, qr_value),
            ).fetchone()
            if loc_existing:
                raise HTTPException(status_code=409, detail="Location or QR already exists")
            conn.execute(
                """
                INSERT INTO locations (id, facility_id, name, description, qr_value)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    location_id,
                    facility_id,
                    (payload.location_name or "Check-in").strip(),
                    payload.location_description,
                    qr_value,
                ),
            )
            created_location = {
                "id": location_id,
                "facility_id": facility_id,
                "name": (payload.location_name or "Check-in").strip(),
                "description": payload.location_description,
                "qr_value": qr_value,
            }

        conn.commit()

    return {
        "status": "ok",
        "facility": {
            "id": facility_id,
            "name": payload.name.strip(),
            "address": payload.address,
            "active": int(payload.active),
        },
        "location": created_location,
    }

@app.put("/admin/facilities/{facility_id}")
def update_facility(facility_id: str, payload: FacilityUpdate, request: Request):
    require_admin(request)

    fields = []
    params: List[object] = []
    if payload.name is not None:
        fields.append("name = ?")
        params.append(payload.name.strip())
    if payload.address is not None:
        fields.append("address = ?")
        params.append(payload.address)
    if payload.active is not None:
        fields.append("active = ?")
        params.append(int(payload.active))

    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    params.append(facility_id)

    with get_conn() as conn:
        cur = conn.execute(
            f"UPDATE facilities SET {', '.join(fields)} WHERE id = ?",
            tuple(params),
        )
        conn.commit()

    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Facility not found")

    return {"status": "ok"}

@app.post("/admin/locations")
def create_location(payload: LocationCreate, request: Request):
    require_admin(request)

    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM locations WHERE id = ? OR qr_value = ?",
            (payload.id, payload.qr_value),
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="Location or QR already exists")

        conn.execute(
            """
            INSERT INTO locations (id, facility_id, name, description, qr_value)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                payload.id,
                payload.facility_id,
                payload.name.strip(),
                payload.description,
                payload.qr_value.strip(),
            ),
        )
        conn.commit()

    return {"status": "ok", "location_id": payload.id}

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

def create_member_record(payload: MemberCreate) -> Dict[str, str]:
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

@app.post("/members")
def create_member(payload: MemberCreate):
    return create_member_record(payload)

@app.get("/admin/members")
def admin_list_members(request: Request, limit: int = 500):
    require_admin(request)
    return list_members(limit=limit)

@app.post("/admin/members")
def admin_create_member(payload: MemberCreate, request: Request):
    require_admin(request)
    return create_member_record(payload)

@app.put("/admin/members/{member_id}")
def admin_update_member(member_id: str, payload: MemberUpdate, request: Request):
    require_admin(request)

    fields = []
    params: List[object] = []
    if payload.first_name is not None:
        fields.append("first_name = ?")
        params.append(normalize_name(payload.first_name))
    if payload.last_name is not None:
        fields.append("last_name = ?")
        params.append(normalize_name(payload.last_name))
    if payload.phone is not None:
        fields.append("phone = ?")
        params.append(normalize_phone(payload.phone))
    if payload.address is not None:
        fields.append("address = ?")
        params.append(payload.address)
    if payload.belt_rank is not None:
        fields.append("belt_rank = ?")
        params.append(payload.belt_rank)
    if payload.promotion_start_date is not None:
        fields.append("promotion_start_date = ?")
        params.append(payload.promotion_start_date)
    if payload.student_type is not None:
        fields.append("student_type = ?")
        params.append(payload.student_type.strip())
    if payload.active is not None:
        fields.append("active = ?")
        params.append(int(payload.active))

    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    params.append(member_id)

    with get_conn() as conn:
        cur = conn.execute(
            f"UPDATE members SET {', '.join(fields)} WHERE id = ?",
            tuple(params),
        )
        conn.commit()

    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Member not found")

    return {"status": "ok"}

@app.get("/admin/ping")
def admin_ping(request: Request):
    require_admin(request)
    return {"status": "ok"}

@app.get("/members/lookup")
def member_lookup(first_name: str, last_name: str, phone: Optional[str] = None):
    fn = normalize_name(first_name)
    ln = normalize_name(last_name)
    ph = normalize_phone(phone)

    with get_conn() as conn:
        row = find_member_by_name(conn, fn, ln, ph)

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
        member = find_member_by_name(conn, fn, ln, ph)

        if not member or member["active"] != 1:
            raise HTTPException(
                status_code=400,
                detail="Name (and phone if used) must match membership database"
            )

        member_id = member["id"]

        # 2) Find location from QR
        qr_raw = payload.qr_value or ""
        qr_norm = normalize_qr_value(qr_raw)
        loc = conn.execute(
            "SELECT id, facility_id FROM locations WHERE qr_value = ?",
            (qr_raw,),
        ).fetchone()
        if not loc and qr_norm and qr_norm != qr_raw:
            loc = conn.execute(
                "SELECT id, facility_id FROM locations WHERE qr_value = ?",
                (qr_norm,),
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

# ---------- Reports (Admin) ----------
@app.get("/admin/reports/members-summary")
def report_members_summary(request: Request):
    require_admin(request)
    now = datetime.utcnow()

    with get_conn() as conn:
        members = conn.execute(
            """
            SELECT id, first_name, last_name, belt_rank, promotion_start_date, student_type, active
            FROM members
            ORDER BY last_name, first_name
            """
        ).fetchall()

        results = []
        for m in members:
            start = parse_promotion_date(m["promotion_start_date"])
            if start:
                count_row = conn.execute(
                    "SELECT COUNT(*) AS c FROM attendance WHERE user_id = ? AND check_in_time >= ?",
                    (m["id"], start.isoformat()),
                ).fetchone()
                months_elapsed = months_since(start, now)
            else:
                count_row = conn.execute(
                    "SELECT COUNT(*) AS c FROM attendance WHERE user_id = ?",
                    (m["id"],),
                ).fetchone()
                months_elapsed = None

            total_sessions = int(count_row["c"]) if count_row else 0

            results.append(
                {
                    "id": m["id"],
                    "first_name": m["first_name"],
                    "last_name": m["last_name"],
                    "belt_rank": m["belt_rank"],
                    "student_type": m["student_type"],
                    "promotion_start_date": m["promotion_start_date"],
                    "months_since_promotion": months_elapsed,
                    "sessions_since_promotion": total_sessions,
                    "active": m["active"],
                }
            )

    return results

@app.get("/admin/reports/members-summary.csv")
def report_members_summary_csv(request: Request):
    require_admin(request)
    rows = report_members_summary(request)

    header = [
        "id",
        "first_name",
        "last_name",
        "belt_rank",
        "student_type",
        "promotion_start_date",
        "months_since_promotion",
        "sessions_since_promotion",
        "active",
    ]
    lines = [",".join(header)]
    for r in rows:
        line = [
            str(r.get("id") or ""),
            str(r.get("first_name") or ""),
            str(r.get("last_name") or ""),
            str(r.get("belt_rank") or ""),
            str(r.get("student_type") or ""),
            str(r.get("promotion_start_date") or ""),
            str(r.get("months_since_promotion") or ""),
            str(r.get("sessions_since_promotion") or ""),
            str(r.get("active") or ""),
        ]
        lines.append(",".join(line))

    return Response("\n".join(lines), media_type="text/csv")

@app.get("/admin/reports/member/{member_id}")
def report_member_detail(member_id: str, request: Request):
    require_admin(request)
    now = datetime.utcnow()

    with get_conn() as conn:
        member = conn.execute(
            """
            SELECT id, first_name, last_name, belt_rank, promotion_start_date, student_type, active
            FROM members
            WHERE id = ?
            LIMIT 1
            """,
            (member_id,),
        ).fetchone()
        if not member:
            raise HTTPException(status_code=404, detail="Member not found")

        start = parse_promotion_date(member["promotion_start_date"])
        if start:
            rows = conn.execute(
                """
                SELECT check_in_time
                FROM attendance
                WHERE user_id = ? AND check_in_time >= ?
                ORDER BY check_in_time
                """,
                (member_id, start.isoformat()),
            ).fetchall()
            months_elapsed = months_since(start, now)
        else:
            rows = conn.execute(
                """
                SELECT check_in_time
                FROM attendance
                WHERE user_id = ?
                ORDER BY check_in_time
                """,
                (member_id,),
            ).fetchall()
            months_elapsed = None

        bucket: Dict[str, int] = {}
        for row in rows:
            ts = datetime.fromisoformat(row["check_in_time"])
            key = f"{ts.year:04d}-{ts.month:02d}"
            bucket[key] = bucket.get(key, 0) + 1

        monthly = [{"month": k, "sessions": bucket[k]} for k in sorted(bucket.keys())]

    return {
        "member": dict(member),
        "sessions_since_promotion": sum(bucket.values()),
        "months_since_promotion": months_elapsed,
        "sessions_by_month": monthly,
    }

@app.get("/admin/reports/member/{member_id}.csv")
def report_member_detail_csv(member_id: str, request: Request):
    require_admin(request)
    data = report_member_detail(member_id, request)
    lines = ["month,sessions"]
    for row in data["sessions_by_month"]:
        lines.append(f"{row['month']},{row['sessions']}")
    return Response("\n".join(lines), media_type="text/csv")
