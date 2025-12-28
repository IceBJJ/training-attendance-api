import re
from datetime import datetime

from db import get_conn


def normalize_datetime_input(value):
    if value is None:
        return None
    v = str(value).strip()
    if not v:
        return None
    for fmt in ("%m/%d/%Y %H:%M", "%m/%d/%Y", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(v, fmt)
            return dt.isoformat()
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(v)
        return dt.isoformat()
    except ValueError:
        return v


def needs_fix(value):
    if not value:
        return False
    v = str(value).strip()
    return bool(re.match(r"^\d{1,2}/\d{1,2}/\d{4}(\s+\d{1,2}:\d{2})?$", v))


def main():
    updated = 0
    checked = 0
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, check_in_time, check_out_time FROM attendance"
        ).fetchall()
        for row in rows:
            checked += 1
            check_in = row["check_in_time"]
            check_out = row["check_out_time"]
            new_in = normalize_datetime_input(check_in) if needs_fix(check_in) else check_in
            new_out = normalize_datetime_input(check_out) if needs_fix(check_out) else check_out
            if new_in != check_in or new_out != check_out:
                conn.execute(
                    "UPDATE attendance SET check_in_time = ?, check_out_time = ? WHERE id = ?",
                    (new_in, new_out, row["id"]),
                )
                updated += 1
        conn.commit()

    print(f"Checked {checked} rows.")
    print(f"Updated {updated} rows.")


if __name__ == "__main__":
    main()
