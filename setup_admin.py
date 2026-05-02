import os
import sqlite3
from contextlib import closing
from pathlib import Path
from werkzeug.security import generate_password_hash

PROJECT_ROOT = Path(__file__).resolve().parent
db_path = Path(os.getenv("SQLITE_DB_PATH", "solar_forecast_db.sqlite3"))
if not db_path.is_absolute():
    db_path = PROJECT_ROOT / db_path

conn = sqlite3.connect(str(db_path))
conn.row_factory = sqlite3.Row

with closing(conn.cursor()) as cur:
    cur.execute(
        """CREATE TABLE IF NOT EXISTS users (
           user_id INTEGER PRIMARY KEY AUTOINCREMENT,
           full_name TEXT NOT NULL,
           email TEXT NOT NULL UNIQUE,
           password_hash TEXT NOT NULL,
           role TEXT NOT NULL DEFAULT 'viewer',
           is_active INTEGER NOT NULL DEFAULT 1,
           last_login_at TEXT NULL,
           created_at TEXT DEFAULT CURRENT_TIMESTAMP,
           updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    cur.execute(
        """CREATE TABLE IF NOT EXISTS pv_system_configs (
           config_id INTEGER PRIMARY KEY AUTOINCREMENT,
           user_id INTEGER NULL,
           location_id INTEGER NULL,
           system_name TEXT NOT NULL,
           capacity_kw REAL NOT NULL,
           panel_area_m2 REAL NOT NULL,
           panel_efficiency_pct REAL NOT NULL,
           tilt_deg REAL NULL,
           azimuth_deg REAL NULL,
           loss_pct REAL NOT NULL DEFAULT 14.0,
           inverter_efficiency_pct REAL NOT NULL DEFAULT 96.0,
           is_default INTEGER NOT NULL DEFAULT 0,
           created_at TEXT DEFAULT CURRENT_TIMESTAMP,
           updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )"""
    )

    # Check existing users
    cur.execute('SELECT COUNT(*) as c FROM users')
    count = cur.fetchone()['c']
    print(f'Existing users: {count}')

    if count == 0:
        cur.execute(
            """INSERT INTO users (full_name, email, password_hash, role, is_active)
               VALUES (?, ?, ?, 'admin', 1)""",
            ('Solar Admin', 'admin@solar.ai', generate_password_hash('admin123'))
        )
        conn.commit()
        print('Admin created successfully!')
        print('Email    : admin@solar.ai')
        print('Password : admin123')
    else:
        print('Admin already exists — login with your existing credentials')

    # Add PV system config
    cur.execute('SELECT COUNT(*) as c FROM pv_system_configs')
    pv_count = cur.fetchone()['c']

    if pv_count == 0:
        cur.execute(
            """INSERT INTO pv_system_configs
               (system_name, capacity_kw, panel_area_m2, panel_efficiency_pct,
                loss_pct, inverter_efficiency_pct, is_default)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ('My Solar System', 10.0, 65.0, 18.5, 14.0, 96.0, 1)
        )
        conn.commit()
        print('PV system configured: 10kW, 65m2 panels, 18.5% efficiency')
    else:
        print('PV system already configured')

conn.close()
print('Setup complete!')
