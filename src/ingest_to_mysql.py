"""
src/ingest_to_mysql.py
----------------------
Loads the downloaded Parquet file into MySQL.

HOW TO RUN (after download_data.py):
    python src/ingest_to_mysql.py

WHAT IT DOES:
    1. Reads data/raw/solar_weather_raw.parquet
    2. Connects to your MySQL database
    3. Inserts the location into the locations table
    4. Bulk-inserts all rows into solar_readings
    5. Prints a summary of what was inserted
"""

import os
import pandas as pd
import sqlite3
from pathlib import Path
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()


# ─── DATABASE CONNECTION ──────────────────────────────────────────────────────
def get_connection():
    """Returns a live MySQL connection using .env credentials."""
    return sqlite3.connect(str(Path(__file__).resolve().parents[1] / "solar_forecast_db.sqlite3")),
        user     = os.getenv("DB_USER",     "root"),
        password = os.getenv("DB_PASSWORD", "Siyaram@#2024"),
        database = os.getenv("DB_NAME",     "solar_forecast_db"),
        charset  = "utf8mb4",
        cursorclass = pymysql.cursors.DictCursor
    )


def insert_location(conn, site_name, lat, lon):
    """
    Inserts a site into the locations table.
    Returns the location_id for use in solar_readings FK.
    Skips insert if the site already exists (safe to re-run).
    """
    with conn.cursor() as cur:
        # Check if already exists
        cur.execute(
            "SELECT location_id FROM locations WHERE site_name = ?",
            (site_name,)
        )
        row = cur.fetchone()
        if row:
            print(f"      Location '{site_name}' already exists — ID {row['location_id']}")
            return row["location_id"]

        cur.execute(
            """INSERT INTO locations (site_name, latitude, longitude, data_source)
               VALUES (?, ?, ?, ?)""",
            (site_name, lat, lon, "Open-Meteo")
        )
        conn.commit()
        loc_id = cur.lastrowid
        print(f"      Inserted location '{site_name}' → ID {loc_id}")
        return loc_id


def bulk_insert_readings(conn, df, location_id, batch_size=500):
    """
    Inserts solar_readings in batches.
    Batching avoids memory issues with large datasets.
    batch_size=500 means 500 rows per INSERT statement.
    """
    # Prepare rows — only include columns that map to our table
    table_cols = [
        "ghi", "dni", "dhi", "temperature_c",
        "humidity_pct", "wind_speed_ms", "pressure_hpa", "cloud_cover_pct"
    ]

    # Only use columns that exist in both df and our table
    use_cols = [c for c in table_cols if c in df.columns]

    sql = f"""
        INSERT INTO solar_readings
            (location_id, timestamp, {', '.join(use_cols)})
        VALUES
            ({location_id}, ?, {', '.join(['?'] * len(use_cols))})
    """

    rows_inserted = 0
    total = len(df)

    with conn.cursor() as cur:
        batch = []
        for _, row in df.iterrows():
            values = [str(row["timestamp"])] + [
                None if pd.isna(row[c]) else float(row[c])
                for c in use_cols
            ]
            batch.append(values)

            if len(batch) >= batch_size:
                cur.executemany(sql, batch)
                conn.commit()
                rows_inserted += len(batch)
                pct = (rows_inserted / total) * 100
                print(f"      Progress: {rows_inserted:,}/{total:,} rows ({pct:.0f}%)", end="\r")
                batch = []

        # Insert remaining rows
        if batch:
            cur.executemany(sql, batch)
            conn.commit()
            rows_inserted += len(batch)

    print(f"      Progress: {rows_inserted:,}/{total:,} rows (100%)    ")
    return rows_inserted


def verify_insertion(conn, location_id):
    """Queries the DB to confirm data was inserted correctly."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) as cnt FROM solar_readings WHERE location_id = ?",
            (location_id,)
        )
        count = cur.fetchone()["cnt"]

        cur.execute(
            """SELECT MIN(timestamp) as first_ts, MAX(timestamp) as last_ts,
                      AVG(ghi) as avg_ghi, MAX(ghi) as max_ghi
               FROM solar_readings WHERE location_id = ?""",
            (location_id,)
        )
        stats = cur.fetchone()

    print(f"\n      Rows in DB:      {count:,}")
    print(f"      Date range:      {stats['first_ts']} → {stats['last_ts']}")
    print(f"      Avg GHI:         {stats['avg_ghi']:.1f} W/m²")
    print(f"      Peak GHI:        {stats['max_ghi']:.1f} W/m²")


if __name__ == "__main__":
    print("=" * 55)
    print("  Solar AI — Phase 1: MySQL Ingestion")
    print("=" * 55)

    # ── 1. Load the downloaded data ──────────────────────────
    parquet_path = "data/raw/solar_weather_raw.parquet"
    print(f"\n[1/4] Loading {parquet_path}...")

    if not os.path.exists(parquet_path):
        print("[ERROR] File not found. Run download_data.py first!")
        exit(1)

    df = pd.read_parquet(parquet_path)
    print(f"      Loaded {len(df):,} rows, {len(df.columns)} columns")

    # ── 2. Connect to MySQL ──────────────────────────────────
    print("\n[2/4] Connecting to MySQL...")
    try:
        conn = get_connection()
        print("      Connected successfully")
    except Exception as e:
        print(f"[ERROR] MySQL connection failed: {e}")
        print("        Check your .env file — DB_PASSWORD, DB_USER, DB_NAME")
        exit(1)

    # ── 3. Insert location ───────────────────────────────────
    print("\n[3/4] Setting up location...")
    loc_id = insert_location(conn, "New Delhi, India", 28.6139, 77.2090)

    # ── 4. Bulk insert readings ──────────────────────────────
    print(f"\n[4/4] Inserting {len(df):,} rows into solar_readings...")
    start_time = datetime.now()
    count = bulk_insert_readings(conn, df, loc_id, batch_size=500)
    elapsed = (datetime.now() - start_time).seconds

    print(f"\n      Inserted {count:,} rows in {elapsed}s")

    # ── 5. Verify ────────────────────────────────────────────
    print("\n[VERIFY] Querying DB to confirm insertion...")
    verify_insertion(conn, loc_id)

    conn.close()
    print("\n[DONE] Ingestion complete! Your data is now in MySQL.")
    print("       Open MySQL and run: SELECT * FROM solar_readings LIMIT 5;")