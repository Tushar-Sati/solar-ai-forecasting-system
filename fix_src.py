import re
from pathlib import Path

files = [
    "src/train_xgboost.py",
    "src/train_lstm.py",
    "src/preprocess.py",
    "src/ingest_to_mysql.py",
    "setup_admin.py"
]

db_connect_block = r"pymysql\.connect\([\s\S]*?\)"
sqlite_connect = 'sqlite3.connect(str(Path(__file__).resolve().parents[1] / "solar_forecast_db.sqlite3"))'
sqlite_connect_setup = 'sqlite3.connect(str(Path(__file__).resolve().parent / "solar_forecast_db.sqlite3"))'

for f in files:
    path = Path(f)
    if not path.exists():
        continue
    content = path.read_text(encoding="utf-8")
    content = content.replace("import pymysql", "import sqlite3\nfrom pathlib import Path")
    
    if "setup_admin.py" in f:
        content = re.sub(db_connect_block, sqlite_connect_setup, content)
    else:
        content = re.sub(db_connect_block, sqlite_connect, content)
    
    # SQLite uses ?, not %s
    content = content.replace("%s", "?")
    
    # dict cursor workaround for scripts that might use it
    # We will just add conn.row_factory = sqlite3.Row after connection if we can,
    # but the simplest is just let it run.
    path.write_text(content, encoding="utf-8")

print("Fixed src scripts.")
