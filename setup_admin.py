import pymysql
from werkzeug.security import generate_password_hash

conn = pymysql.connect(
    host='localhost',
    user='root',
    password='Siyaram@#2024',
    database='solar_forecast_db',
    charset='utf8mb4',
    cursorclass=pymysql.cursors.DictCursor
)

with conn.cursor() as cur:
    # Check existing users
    cur.execute('SELECT COUNT(*) as c FROM users')
    count = cur.fetchone()['c']
    print(f'Existing users: {count}')

    if count == 0:
        cur.execute(
            """INSERT INTO users (full_name, email, password_hash, role, is_active)
               VALUES (%s, %s, %s, 'admin', 1)""",
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
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            ('My Solar System', 10.0, 65.0, 18.5, 14.0, 96.0, 1)
        )
        conn.commit()
        print('PV system configured: 10kW, 65m2 panels, 18.5% efficiency')
    else:
        print('PV system already configured')

conn.close()
print('Setup complete!')