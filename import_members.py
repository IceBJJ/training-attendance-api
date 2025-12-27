import csv
import re
import sys
from datetime import datetime
from uuid import uuid4

from db import init_db, get_conn


ALLOWED_STUDENT_TYPES = {"adult", "youth"}


def norm_phone(phone: str | None) -> str | None:
    """Normalize phone to digits only; return None if empty."""
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if len(digits) > 10:
        digits = digits[-10:]
    return digits if digits else None


def norm_text(s: str | None) -> str | None:
    if s is None:
        return None
    s = s.strip()
    return s if s else None


def norm_active(val: str | None) -> int:
    if val is None or val.strip() == "":
        return 1
    v = val.strip().lower()
    if v in {"1", "true", "yes", "y"}:
        return 1
    if v in {"0", "false", "no", "n"}:
        return 0
    raise ValueError(f"active must be 1/0 (or true/false). Got: {val!r}")


def norm_student_type(val: str) -> str:
    v = val.strip().lower()
    if v not in ALLOWED_STUDENT_TYPES:
        raise ValueError(f"student_type must be 'adult' or 'youth'. Got: {val!r}")
    return v


def norm_date(val: str | None) -> str | None:
    """Accept YYYY-MM-DD or ISO datetime; store as string; blank -> None."""
    if not val:
        return None
    v = val.strip()
    if not v:
        return None
    # Light validation: try parse common date formats
    try:
        if len(v) == 10:
            datetime.strptime(v, "%Y-%m-%d")
        else:
            # allow ISO datetime strings
            datetime.fromisoformat(v)
    except Exception:
        raise ValueError(f"promotion_start_date must be YYYY-MM-DD or ISO datetime. Got: {val!r}")
    return v


def import_members(csv_path: str, dry_run: bool = False) -> None:
    init_db()

    required_cols = {
        "first_name", "last_name", "phone", "address",
        "belt_rank", "promotion_start_date", "student_type", "active"
    }

    created = 0
    updated = 0
    skipped = 0
    errors = 0

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise RuntimeError("CSV has no header row.")

        missing = required_cols - set(reader.fieldnames)
        if missing:
            raise RuntimeError(f"CSV missing required columns: {sorted(missing)}")

        rows = list(reader)

    with get_conn() as conn:
        for i, row in enumerate(rows, start=2):  # start=2 because header is line 1
            try:
                first_name = norm_text(row.get("first_name"))
                last_name = norm_text(row.get("last_name"))
                phone = norm_phone(row.get("phone"))
                address = norm_text(row.get("address"))
                belt_rank = norm_text(row.get("belt_rank"))
                promotion_start_date = norm_date(row.get("promotion_start_date"))
                student_type = norm_student_type(row.get("student_type") or "")
                active = norm_active(row.get("active"))

                if not first_name or not last_name:
                    raise ValueError("first_name and last_name are required.")

                created_at = datetime.utcnow().isoformat()

                # Find existing member by unique key (first_name, last_name, phone)
                existing = conn.execute(
                    """
                    SELECT id
                    FROM members
                    WHERE first_name = ? AND last_name = ?
                      AND ( (phone IS NULL AND ? IS NULL) OR phone = ? )
                    LIMIT 1
                    """,
                    (first_name, last_name, phone, phone)
                ).fetchone()

                if existing:
                    member_id = existing["id"]
                    updated += 1

                    if not dry_run:
                        conn.execute(
                            """
                            UPDATE members
                            SET phone = ?,
                                address = ?,
                                belt_rank = ?,
                                promotion_start_date = ?,
                                student_type = ?,
                                active = ?
                            WHERE id = ?
                            """,
                            (phone, address, belt_rank, promotion_start_date, student_type, active, member_id)
                        )
                else:
                    member_id = f"MEM_{uuid4().hex[:12].upper()}"
                    created += 1

                    if not dry_run:
                        conn.execute(
                            """
                            INSERT INTO members (
                                id, first_name, last_name, phone, address,
                                belt_rank, promotion_start_date, student_type,
                                active, created_at
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                member_id, first_name, last_name, phone, address,
                                belt_rank, promotion_start_date, student_type,
                                active, created_at
                            )
                        )

            except Exception as e:
                errors += 1
                print(f"[Line {i}] ERROR: {e} | Row={row}", file=sys.stderr)

        if not dry_run:
            conn.commit()

    print("Import complete.")
    print(f"  created: {created}")
    print(f"  updated: {updated}")
    print(f"  skipped: {skipped}")
    print(f"  errors:  {errors}")
    if dry_run:
        print("  (dry run: no changes were written)")


if __name__ == "__main__":
    # Usage:
    #   python import_members.py members.csv
    #   python import_members.py members.csv --dry-run
    args = sys.argv[1:]
    if not args:
        print("Usage: python import_members.py <members.csv> [--dry-run]")
        sys.exit(1)

    path = args[0]
    dry = "--dry-run" in args[1:]
    import_members(path, dry_run=dry)
