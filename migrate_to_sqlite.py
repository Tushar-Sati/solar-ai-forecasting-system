import re
from pathlib import Path

app_path = Path("api/app.py")
content = app_path.read_text(encoding="utf-8")

# 1. Replace pymysql with sqlite3
content = content.replace("import pymysql", "import sqlite3")

# 2. Replace get_db
old_get_db = r"def get_db\(\):.*?autocommit=False,\n    \)"
new_get_db = """class SQLiteCursorWrapper:
    def __init__(self, cursor):
        self.cursor = cursor
    def execute(self, sql, params=()):
        sql = sql.replace("AUTO_INCREMENT", "AUTOINCREMENT")
        sql = sql.replace("INT AUTOINCREMENT", "INTEGER AUTOINCREMENT")
        sql = sql.replace("BIGINT AUTOINCREMENT", "INTEGER AUTOINCREMENT")
        sql = re.sub(r"ENUM\\([^)]+\\)", "TEXT", sql)
        sql = re.sub(r",\\s*INDEX\\s+\\w+\\s*\\([^)]+\\)", "", sql)
        sql = sql.replace("%s", "?")
        if "SHOW COLUMNS FROM" in sql:
            match = re.search(r"SHOW COLUMNS FROM `?(\\w+)`?", sql)
            if match:
                sql = f"PRAGMA table_info(`{match.group(1)}`)"
        self.cursor.execute(sql, params)
        self.lastrowid = self.cursor.lastrowid
    def fetchone(self):
        row = self.cursor.fetchone()
        return dict(row) if row else None
    def fetchall(self):
        return [dict(row) for row in self.cursor.fetchall()]

class SQLiteConnWrapper:
    def __init__(self, conn):
        self.conn = conn
    def cursor(self):
        return SQLiteCursorWrapper(self.conn.cursor())
    def commit(self):
        self.conn.commit()
    def close(self):
        self.conn.close()

def get_db():
    db_path = BASE_DIR / "solar_forecast_db.sqlite3"
    import sqlite3
    import re
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return SQLiteConnWrapper(conn)"""

content = re.sub(old_get_db, lambda _: new_get_db, content, flags=re.DOTALL)

# 3. Replace table_columns
old_table_columns = r"def table_columns\(table_name: str\) -> set\[str\]:.*?return set\(\)"
new_table_columns = """def table_columns(table_name: str) -> set[str]:
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(f"PRAGMA table_info(`{table_name}`)")
            rows = cur.fetchall()
        return {row["name"] for row in rows}
    except Exception:
        return set()"""

content = re.sub(old_table_columns, lambda _: new_table_columns, content, flags=re.DOTALL)

# 4. Replace MySQL specific DATE_FORMAT with SQLite strftime
content = content.replace("DATE_FORMAT(timestamp, '%%Y-%%m-%%d %%H:00:00')", "strftime('%Y-%m-%d %H:00:00', timestamp)")
content = content.replace("DATE_FORMAT(timestamp, '%Y-%m-%d %H:00:00')", "strftime('%Y-%m-%d %H:00:00', timestamp)")

app_path.write_text(content, encoding="utf-8")

# Now handle powerbi views
sql_path = Path("sql/002_powerbi_views.sql")
if sql_path.exists():
    sql_content = sql_path.read_text(encoding="utf-8")
    # Replace CREATE OR REPLACE VIEW
    sql_content = re.sub(r"CREATE OR REPLACE VIEW (\w+)", r"DROP VIEW IF EXISTS \1;\nCREATE VIEW \1", sql_content)
    # Replace HOUR(x) with strftime('%H', x)
    sql_content = re.sub(r"HOUR\(([^)]+)\)", r"CAST(strftime('%H', \1) AS INTEGER)", sql_content)
    sql_path.write_text(sql_content, encoding="utf-8")

# Update requirements.txt to remove PyMySQL (not strictly needed, but clean)
req_path = Path("requirements.txt")
if req_path.exists():
    req_content = req_path.read_text(encoding="utf-8")
    req_content = re.sub(r"PyMySQL==.*?\n", "", req_content)
    req_path.write_text(req_content, encoding="utf-8")

print("Migration to SQLite complete.")
