from db import get_conn, init_db

FACILITIES = [
    ("FAC_FINDLAY_BJJ", "Findlay BJJ", None, 1),
    ("FAC_MONROE_BJJ", "Monroe BJJ", None, 1),
    ("FAC_SECOND_NATURE_BJJ", "Second Nature BJJ", None, 1),
    ("FAC_US_BJJ", "U.S. BJJ", None, 1),
    ("FAC_SALINE_BJJ", "Saline BJJ", None, 1),
    ("FAC_FOSTORIA_BJJ", "Fostoria BJJ", None, 1),
]

# One QR per facility (facility-level check-in)
LOCATIONS = [
    ("LOC_FINDLAY_CHECKIN", "FAC_FINDLAY_BJJ", "Check-in", "Facility check-in QR", "QR_FAC_FINDLAY_BJJ"),
    ("LOC_MONROE_CHECKIN", "FAC_MONROE_BJJ", "Check-in", "Facility check-in QR", "QR_FAC_MONROE_BJJ"),
    ("LOC_SECOND_NATURE_CHECKIN", "FAC_SECOND_NATURE_BJJ", "Check-in", "Facility check-in QR", "QR_FAC_SECOND_NATURE_BJJ"),
    ("LOC_US_CHECKIN", "FAC_US_BJJ", "Check-in", "Facility check-in QR", "QR_FAC_US_BJJ"),
    ("LOC_SALINE_CHECKIN", "FAC_SALINE_BJJ", "Check-in", "Facility check-in QR", "QR_FAC_SALINE_BJJ"),
    ("LOC_FOSTORIA_CHECKIN", "FAC_FOSTORIA_BJJ", "Check-in", "Facility check-in QR", "QR_FAC_FOSTORIA_BJJ"),
]

def seed():
    init_db()
    with get_conn() as conn:
        if conn.dialect == "postgres":
            fac_sql = "INSERT INTO facilities (id, name, address, active) VALUES (?, ?, ?, ?) ON CONFLICT (id) DO NOTHING"
            loc_sql = """
                INSERT INTO locations
                (id, facility_id, name, description, qr_value)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (id) DO NOTHING
            """
        else:
            fac_sql = "INSERT OR IGNORE INTO facilities (id, name, address, active) VALUES (?, ?, ?, ?)"
            loc_sql = """
                INSERT OR IGNORE INTO locations
                (id, facility_id, name, description, qr_value)
                VALUES (?, ?, ?, ?, ?)
            """

        for f in FACILITIES:
            conn.execute(fac_sql, f)
        for l in LOCATIONS:
            conn.execute(loc_sql, l)
        conn.commit()

    print("Seed complete.")

if __name__ == "__main__":
    seed()
