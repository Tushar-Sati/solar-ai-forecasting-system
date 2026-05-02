"""
Production backend for the AI-Powered Solar Energy Forecasting and
Analytics Platform.

The backend intentionally uses live providers for weather/solar inputs.
No demo weather values, random charts, or manual weather-entry prediction
paths are exposed.
"""

from __future__ import annotations

import csv
import json
import os
import secrets
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

import joblib
import numpy as np
import sqlite3
import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename


BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")

app = Flask(__name__, static_folder=str(BASE_DIR / "dashboard"), static_url_path="")
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", secrets.token_hex(32))
app.config["UPLOAD_FOLDER"] = str(BASE_DIR / os.getenv("UPLOAD_FOLDER", "uploads"))
CORS(app)

MODEL_DIR = BASE_DIR / os.getenv("MODEL_DIR", "data/models")
OPENWEATHERMAP_API_KEY = os.getenv("OPENWEATHERMAP_API_KEY") or os.getenv("OWM_API_KEY")
WEATHERAPI_KEY = os.getenv("WEATHERAPI_KEY")
APP_REFRESH_SECONDS = int(os.getenv("APP_REFRESH_SECONDS", "300"))
ENSEMBLE_XGB_WEIGHT = float(os.getenv("ENSEMBLE_XGB_WEIGHT", "0.60"))
GRID_EMISSION_FACTOR = float(os.getenv("GRID_EMISSION_FACTOR_KG_PER_KWH", "0.82"))

ALLOWED_UPLOADS = {".csv", ".parquet", ".xlsx", ".xls"}
MODEL_METRICS_CACHE: dict[str, Any] | None = None


def model_path(name: str) -> Path:
    return MODEL_DIR / name


print("Loading solar forecasting models...")
try:
    import tensorflow as tf

    os.environ["TF_CPP_MIN_LOG_LEVEL"] = "2"
    tf.get_logger().setLevel("ERROR")
    lstm_model = tf.keras.models.load_model(model_path("lstm_best.keras"))
    print("  LSTM loaded")
except Exception as exc:  # pragma: no cover - startup diagnostic
    lstm_model = None
    print(f"  LSTM unavailable: {exc}")

try:
    xgb_model = joblib.load(model_path("xgboost_solar.pkl"))
    scaler = joblib.load(model_path("scaler.pkl"))
    feat_cols = joblib.load(model_path("feature_cols.pkl"))
    y_max = joblib.load(model_path("y_max.pkl"))
    print("  XGBoost, scaler, feature columns loaded")
except Exception as exc:  # pragma: no cover - startup diagnostic
    xgb_model = scaler = feat_cols = y_max = None
    print(f"  XGBoost stack unavailable: {exc}")

print("Model startup complete.\n")


class SQLiteCursorWrapper:
    def __init__(self, cursor):
        self.cursor = cursor
    def execute(self, sql, params=()):
        sql = sql.replace("AUTO_INCREMENT", "AUTOINCREMENT")
        sql = sql.replace("INT AUTOINCREMENT", "INTEGER AUTOINCREMENT")
        sql = sql.replace("BIGINT AUTOINCREMENT", "INTEGER AUTOINCREMENT")
        sql = re.sub(r"ENUM\([^)]+\)", "TEXT", sql)
        sql = re.sub(r",\s*INDEX\s+\w+\s*\([^)]+\)", "", sql)
        sql = sql.replace("%s", "?")
        if "SHOW COLUMNS FROM" in sql:
            match = re.search(r"SHOW COLUMNS FROM `?(\w+)`?", sql)
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
    return SQLiteConnWrapper(conn)


def to_jsonable(value: Any) -> Any:
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_jsonable(v) for v in value]
    return value


def json_dumps(value: Any) -> str:
    return json.dumps(to_jsonable(value), ensure_ascii=True)


def db_query_one(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()
    finally:
        conn.close()


def db_query_all(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())
    finally:
        conn.close()


def db_execute(sql: str, params: tuple[Any, ...] = ()) -> int:
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row_id = cur.lastrowid
        conn.commit()
        return row_id
    finally:
        conn.close()


def table_columns(table_name: str) -> set[str]:
    try:
        conn = get_db()
        with conn.cursor() as cur:
            cur.execute(f"PRAGMA table_info(`{table_name}`)")
            rows = cur.fetchall()
        return {row["name"] for row in rows}
    except Exception:
        return set()


def ensure_column(table_name: str, column_name: str, definition: str) -> None:
    if column_name in table_columns(table_name):
        return
    db_execute(f"ALTER TABLE `{table_name}` ADD COLUMN {definition}")


def ensure_schema() -> None:
    """Create the app-owned tables and add backward-compatible columns."""
    ddl_statements = [
        """
        CREATE TABLE IF NOT EXISTS locations (
          location_id INT AUTO_INCREMENT PRIMARY KEY,
          site_name VARCHAR(100) NOT NULL,
          latitude DECIMAL(9,6) NOT NULL,
          longitude DECIMAL(9,6) NOT NULL,
          elevation_m FLOAT NULL,
          timezone VARCHAR(50) NULL,
          data_source VARCHAR(50) NULL,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS solar_readings (
          reading_id BIGINT AUTO_INCREMENT PRIMARY KEY,
          location_id INT NOT NULL,
          timestamp DATETIME NOT NULL,
          ghi FLOAT NULL,
          dni FLOAT NULL,
          dhi FLOAT NULL,
          temperature_c FLOAT NULL,
          humidity_pct FLOAT NULL,
          wind_speed_ms FLOAT NULL,
          pressure_hpa FLOAT NULL,
          cloud_cover_pct FLOAT NULL,
          solar_zenith FLOAT NULL,
          INDEX idx_solar_location_time (location_id, timestamp)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS predictions (
          prediction_id BIGINT AUTO_INCREMENT PRIMARY KEY,
          location_id INT NOT NULL,
          model_name VARCHAR(30) NULL,
          prediction_time DATETIME NOT NULL,
          forecast_time DATETIME NOT NULL,
          predicted_ghi FLOAT NULL,
          predicted_power FLOAT NULL,
          actual_power FLOAT NULL,
          mae FLOAT NULL,
          INDEX idx_predictions_location_time (location_id, prediction_time)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS model_registry (
          model_id INT AUTO_INCREMENT PRIMARY KEY,
          model_name VARCHAR(50) NULL,
          version VARCHAR(20) NULL,
          training_date DATE NULL,
          rmse FLOAT NULL,
          mae FLOAT NULL,
          r2_score FLOAT NULL,
          hyperparams JSON NULL,
          file_path VARCHAR(255) NULL,
          is_active TINYINT(1) DEFAULT 0
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS pv_output (
          output_id BIGINT AUTO_INCREMENT PRIMARY KEY,
          location_id INT NOT NULL,
          timestamp DATETIME NOT NULL,
          ac_power_kw FLOAT NULL,
          dc_power_kw FLOAT NULL,
          energy_kwh FLOAT NULL,
          efficiency_pct FLOAT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS users (
          user_id INT AUTO_INCREMENT PRIMARY KEY,
          full_name VARCHAR(120) NOT NULL,
          email VARCHAR(190) NOT NULL UNIQUE,
          password_hash VARCHAR(255) NOT NULL,
          role ENUM('admin', 'operator', 'viewer') NOT NULL DEFAULT 'viewer',
          is_active TINYINT(1) NOT NULL DEFAULT 1,
          last_login_at DATETIME NULL,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS user_sessions (
          session_id CHAR(64) PRIMARY KEY,
          user_id INT NOT NULL,
          expires_at DATETIME NOT NULL,
          revoked_at DATETIME NULL,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS pv_system_configs (
          config_id INT AUTO_INCREMENT PRIMARY KEY,
          user_id INT NULL,
          location_id INT NULL,
          system_name VARCHAR(120) NOT NULL,
          capacity_kw DECIMAL(10,3) NOT NULL,
          panel_area_m2 DECIMAL(10,3) NOT NULL,
          panel_efficiency_pct DECIMAL(6,3) NOT NULL,
          tilt_deg DECIMAL(6,2) NULL,
          azimuth_deg DECIMAL(6,2) NULL,
          loss_pct DECIMAL(6,3) NOT NULL DEFAULT 14.000,
          inverter_efficiency_pct DECIMAL(6,3) NOT NULL DEFAULT 96.000,
          is_default TINYINT(1) NOT NULL DEFAULT 0,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS api_request_logs (
          log_id BIGINT AUTO_INCREMENT PRIMARY KEY,
          provider VARCHAR(80) NOT NULL,
          endpoint VARCHAR(255) NOT NULL,
          status_code INT NULL,
          latency_ms INT NULL,
          success TINYINT(1) NOT NULL DEFAULT 0,
          request_params JSON NULL,
          response_summary JSON NULL,
          error_message TEXT NULL,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          INDEX idx_api_logs_provider_time (provider, created_at),
          INDEX idx_api_logs_success_time (success, created_at)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS system_logs (
          log_id BIGINT AUTO_INCREMENT PRIMARY KEY,
          level ENUM('info', 'warning', 'error') NOT NULL DEFAULT 'info',
          source VARCHAR(80) NOT NULL,
          message VARCHAR(500) NOT NULL,
          context JSON NULL,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          INDEX idx_system_logs_level_time (level, created_at)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS dataset_uploads (
          upload_id BIGINT AUTO_INCREMENT PRIMARY KEY,
          user_id INT NULL,
          original_filename VARCHAR(255) NOT NULL,
          stored_path VARCHAR(500) NOT NULL,
          row_count INT NULL,
          status ENUM('uploaded', 'validated', 'failed') NOT NULL DEFAULT 'uploaded',
          error_message TEXT NULL,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS retraining_jobs (
          job_id BIGINT AUTO_INCREMENT PRIMARY KEY,
          requested_by INT NULL,
          upload_id BIGINT NULL,
          status ENUM('queued', 'running', 'completed', 'failed') NOT NULL DEFAULT 'queued',
          command TEXT NULL,
          log_path VARCHAR(500) NULL,
          started_at DATETIME NULL,
          finished_at DATETIME NULL,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
    ]

    conn = get_db()
    try:
        with conn.cursor() as cur:
            for statement in ddl_statements:
                cur.execute(statement)
        conn.commit()
    finally:
        conn.close()

    for column, definition in {
        "xgboost_ghi": "xgboost_ghi FLOAT NULL",
        "lstm_ghi": "lstm_ghi FLOAT NULL",
        "ensemble_ghi": "ensemble_ghi FLOAT NULL",
        "confidence_score": "confidence_score FLOAT NULL",
        "input_snapshot": "input_snapshot JSON NULL",
        "api_source": "api_source VARCHAR(80) NULL",
        "pv_config_id": "pv_config_id INT NULL",
    }.items():
        try:
            ensure_column("predictions", column, definition)
        except Exception as exc:
            print(f"Schema warning for predictions.{column}: {exc}")

    for column, definition in {
        "dew_point_c": "dew_point_c FLOAT NULL",
        "uv_index": "uv_index FLOAT NULL",
        "visibility_km": "visibility_km FLOAT NULL",
        "data_source": "data_source VARCHAR(80) NULL",
    }.items():
        try:
            ensure_column("solar_readings", column, definition)
        except Exception as exc:
            print(f"Schema warning for solar_readings.{column}: {exc}")

    try:
        seed_admin_user()
        install_powerbi_views()
    except Exception as exc:
        print(f"Startup setup warning: {exc}")


def seed_admin_user() -> None:
    email = os.getenv("ADMIN_EMAIL")
    password = os.getenv("ADMIN_PASSWORD")
    name = os.getenv("ADMIN_NAME", "Solar Administrator")
    if not email or not password:
        return
    existing = db_query_one("SELECT user_id FROM users WHERE email=%s", (email,))
    if existing:
        return
    db_execute(
        """
        INSERT INTO users (full_name, email, password_hash, role, is_active)
        VALUES (%s, %s, %s, 'admin', 1)
        """,
        (name, email, generate_password_hash(password)),
    )


def install_powerbi_views() -> None:
    views_path = BASE_DIR / "sql" / "002_powerbi_views.sql"
    if not views_path.exists():
        return
    statements = [s.strip() for s in views_path.read_text(encoding="utf-8").split(";")]
    conn = get_db()
    try:
        with conn.cursor() as cur:
            for statement in statements:
                if statement:
                    cur.execute(statement)
        conn.commit()
    finally:
        conn.close()


def log_api_request(
    provider: str,
    endpoint: str,
    params: dict[str, Any],
    status_code: int | None,
    latency_ms: int | None,
    success: bool,
    response_summary: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> None:
    try:
        db_execute(
            """
            INSERT INTO api_request_logs
              (provider, endpoint, status_code, latency_ms, success,
               request_params, response_summary, error_message)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                provider,
                endpoint,
                status_code,
                latency_ms,
                1 if success else 0,
                json_dumps(params),
                json_dumps(response_summary or {}),
                error_message,
            ),
        )
    except Exception:
        pass


def log_system(
    level: str,
    source: str,
    message: str,
    context: dict[str, Any] | None = None,
) -> None:
    try:
        db_execute(
            """
            INSERT INTO system_logs (level, source, message, context)
            VALUES (%s,%s,%s,%s)
            """,
            (level, source, message, json_dumps(context or {})),
        )
    except Exception:
        pass


def fetch_json(provider: str, url: str, params: dict[str, Any], timeout: int = 18) -> dict[str, Any]:
    started = time.perf_counter()
    status_code = None
    try:
        response = requests.get(
            url,
            params=params,
            timeout=timeout,
            headers={"User-Agent": "SolarAIPlatform/1.0"},
        )
        status_code = response.status_code
        response.raise_for_status()
        payload = response.json()
        latency_ms = int((time.perf_counter() - started) * 1000)
        log_api_request(
            provider,
            url,
            params,
            status_code,
            latency_ms,
            True,
            {"top_level_keys": list(payload.keys())[:12]},
        )
        return payload
    except Exception as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        log_api_request(provider, url, params, status_code, latency_ms, False, error_message=str(exc))
        raise


def bad_request(message: str, status: int = 400):
    return jsonify({"status": "error", "error": message}), status


def auth_token() -> str | None:
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header.split(" ", 1)[1].strip()
    return request.headers.get("X-Session-Token") or request.cookies.get("solar_session")


def current_user() -> dict[str, Any] | None:
    token = auth_token()
    if not token:
        return None
    row = db_query_one(
        """
        SELECT u.user_id, u.full_name, u.email, u.role, u.is_active
        FROM user_sessions s
        JOIN users u ON u.user_id = s.user_id
        WHERE s.session_id=%s
          AND s.revoked_at IS NULL
          AND s.expires_at > NOW()
          AND u.is_active=1
        """,
        (token,),
    )
    return row


def require_admin(fn: Callable):
    def wrapper(*args, **kwargs):
        user = current_user()
        if not user or user.get("role") != "admin":
            return bad_request("Admin authentication required.", 401)
        return fn(user, *args, **kwargs)

    wrapper.__name__ = fn.__name__
    return wrapper


def open_meteo_geocode(query: str) -> dict[str, Any]:
    data = fetch_json(
        "Open-Meteo Geocoding",
        "https://geocoding-api.open-meteo.com/v1/search",
        {"name": query, "count": 10, "language": "en", "format": "json"},
    )
    results = data.get("results") or []
    if not results:
        raise ValueError("No matching location found.")
    item = results[0]
    return {
        "name": item.get("name"),
        "state": item.get("admin1") or "",
        "country": item.get("country") or "",
        "country_code": item.get("country_code") or "",
        "lat": float(item["latitude"]),
        "lon": float(item["longitude"]),
        "timezone": item.get("timezone"),
        "elevation_m": item.get("elevation"),
    }


def reverse_geocode(lat: float, lon: float) -> dict[str, Any]:
    data = fetch_json(
        "OpenStreetMap Nominatim",
        "https://nominatim.openstreetmap.org/reverse",
        {"format": "jsonv2", "lat": lat, "lon": lon, "zoom": 10, "addressdetails": 1},
    )
    address = data.get("address") or {}
    city = address.get("city") or address.get("town") or address.get("village") or address.get("county")
    return {
        "name": city or data.get("name") or "Selected location",
        "state": address.get("state") or "",
        "country": address.get("country") or "",
        "country_code": (address.get("country_code") or "").upper(),
        "lat": float(lat),
        "lon": float(lon),
        "timezone": None,
        "elevation_m": None,
    }


def resolve_location(args: dict[str, Any]) -> dict[str, Any]:
    lat = args.get("lat")
    lon = args.get("lon")
    city = args.get("city") or args.get("q")
    if lat is not None and lon is not None:
        try:
            location = reverse_geocode(float(lat), float(lon))
        except Exception:
            location = {
                "name": city or "Selected location",
                "state": "",
                "country": "",
                "country_code": "",
                "lat": float(lat),
                "lon": float(lon),
                "timezone": None,
                "elevation_m": None,
            }
        return location
    if city:
        return open_meteo_geocode(str(city))
    raise ValueError("Provide lat/lon or city.")


def site_name(location: dict[str, Any]) -> str:
    parts = [location.get("name"), location.get("state"), location.get("country")]
    return ", ".join([str(p) for p in parts if p])


def upsert_location(location: dict[str, Any], data_source: str = "Open-Meteo") -> int:
    lat = round(float(location["lat"]), 6)
    lon = round(float(location["lon"]), 6)
    row = db_query_one(
        """
        SELECT location_id FROM locations
        WHERE ABS(latitude - %s) < 0.0005 AND ABS(longitude - %s) < 0.0005
        ORDER BY location_id DESC LIMIT 1
        """,
        (lat, lon),
    )
    if row:
        return int(row["location_id"])
    return db_execute(
        """
        INSERT INTO locations
          (site_name, latitude, longitude, elevation_m, timezone, data_source)
        VALUES (%s,%s,%s,%s,%s,%s)
        """,
        (
            site_name(location),
            lat,
            lon,
            location.get("elevation_m"),
            location.get("timezone"),
            data_source,
        ),
    )


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def closest_hour_index(times: list[str], current_time: str | None) -> int:
    if current_time and current_time in times:
        return times.index(current_time)
    target = parse_dt(current_time) or datetime.now().replace(minute=0, second=0, microsecond=0)
    parsed = [parse_dt(t) for t in times]
    distances = [abs((p - target).total_seconds()) if p else float("inf") for p in parsed]
    return int(np.argmin(distances))


def series_value(hourly: dict[str, list[Any]], key: str, idx: int) -> float | None:
    values = hourly.get(key) or []
    if idx < 0 or idx >= len(values):
        return None
    value = values[idx]
    if value is None:
        return None
    return float(value)


def required_series_value(hourly: dict[str, list[Any]], key: str, idx: int) -> float:
    value = series_value(hourly, key, idx)
    if value is None:
        raise ValueError(f"Live provider did not return {key} for the required forecast hour.")
    return value


def wmo_description(code: int | None) -> str:
    mapping = {
        0: "Clear sky",
        1: "Mainly clear",
        2: "Partly cloudy",
        3: "Overcast",
        45: "Fog",
        48: "Depositing rime fog",
        51: "Light drizzle",
        53: "Moderate drizzle",
        55: "Dense drizzle",
        61: "Slight rain",
        63: "Moderate rain",
        65: "Heavy rain",
        80: "Rain showers",
        95: "Thunderstorm",
    }
    return mapping.get(code, "Weather data available")


def fetch_nasa_power(lat: float, lon: float) -> dict[str, Any] | None:
    # NASA POWER near-real-time availability can lag. Use a recent completed day
    # and treat it as enrichment, never as the primary live provider.
    day = (datetime.utcnow() - timedelta(days=3)).strftime("%Y%m%d")
    try:
        payload = fetch_json(
            "NASA POWER",
            "https://power.larc.nasa.gov/api/temporal/hourly/point",
            {
                "parameters": "ALLSKY_SFC_SW_DWN,CLRSKY_SFC_SW_DWN,T2M,RH2M,WS10M,PS",
                "community": "RE",
                "longitude": lon,
                "latitude": lat,
                "start": day,
                "end": day,
                "format": "JSON",
            },
            timeout=25,
        )
        params = (payload.get("properties") or {}).get("parameter") or {}
        latest: dict[str, Any] = {}
        for key, values in params.items():
            if isinstance(values, dict) and values:
                latest_key = sorted(values.keys())[-1]
                latest[key] = values.get(latest_key)
        return {"date": day, "latest": latest, "source": "NASA POWER"}
    except Exception as exc:
        log_system("warning", "weather", "NASA POWER enrichment unavailable", {"error": str(exc)})
        return None


def fetch_weather_bundle(location: dict[str, Any]) -> dict[str, Any]:
    lat = float(location["lat"])
    lon = float(location["lon"])
    hourly_vars = [
        "temperature_2m",
        "relative_humidity_2m",
        "dew_point_2m",
        "apparent_temperature",
        "pressure_msl",
        "surface_pressure",
        "cloud_cover",
        "wind_speed_10m",
        "wind_direction_10m",
        "uv_index",
        "shortwave_radiation",
        "direct_normal_irradiance",
        "diffuse_radiation",
        "global_tilted_irradiance",
    ]
    current_vars = [
        "temperature_2m",
        "relative_humidity_2m",
        "apparent_temperature",
        "is_day",
        "precipitation",
        "weather_code",
        "cloud_cover",
        "pressure_msl",
        "surface_pressure",
        "wind_speed_10m",
        "wind_direction_10m",
        "wind_gusts_10m",
    ]
    daily_vars = [
        "sunrise",
        "sunset",
        "uv_index_max",
        "shortwave_radiation_sum",
        "temperature_2m_max",
        "temperature_2m_min",
        "precipitation_sum",
        "wind_speed_10m_max",
    ]
    payload = fetch_json(
        "Open-Meteo Forecast",
        "https://api.open-meteo.com/v1/forecast",
        {
            "latitude": lat,
            "longitude": lon,
            "current": ",".join(current_vars),
            "hourly": ",".join(hourly_vars),
            "daily": ",".join(daily_vars),
            "timezone": "auto",
            "past_days": 4,
            "forecast_days": 7,
        },
    )
    hourly = payload.get("hourly") or {}
    daily = payload.get("daily") or {}
    current = payload.get("current") or {}
    times = hourly.get("time") or []
    if not times:
        raise ValueError("Open-Meteo returned no hourly weather data.")
    idx = closest_hour_index(times, current.get("time"))

    current_data = {
        "time": times[idx],
        "temperature_c": current.get("temperature_2m", series_value(hourly, "temperature_2m", idx)),
        "feels_like_c": current.get("apparent_temperature", series_value(hourly, "apparent_temperature", idx)),
        "humidity_pct": current.get("relative_humidity_2m", series_value(hourly, "relative_humidity_2m", idx)),
        "dew_point_c": series_value(hourly, "dew_point_2m", idx),
        "pressure_hpa": current.get("surface_pressure") or current.get("pressure_msl") or series_value(hourly, "surface_pressure", idx),
        "wind_speed_ms": current.get("wind_speed_10m", series_value(hourly, "wind_speed_10m", idx)),
        "wind_direction_deg": current.get("wind_direction_10m", series_value(hourly, "wind_direction_10m", idx)),
        "cloud_cover_pct": current.get("cloud_cover", series_value(hourly, "cloud_cover", idx)),
        "uv_index": series_value(hourly, "uv_index", idx),
        "ghi": series_value(hourly, "shortwave_radiation", idx),
        "dni": series_value(hourly, "direct_normal_irradiance", idx),
        "dhi": series_value(hourly, "diffuse_radiation", idx),
        "global_tilted_irradiance": series_value(hourly, "global_tilted_irradiance", idx),
        "precipitation_mm": current.get("precipitation"),
        "is_day": current.get("is_day"),
        "weather_code": current.get("weather_code"),
        "description": wmo_description(current.get("weather_code")),
    }

    hourly_rows = []
    for i, t in enumerate(times):
        hourly_rows.append(
            {
                "time": t,
                "temperature_c": series_value(hourly, "temperature_2m", i),
                "humidity_pct": series_value(hourly, "relative_humidity_2m", i),
                "dew_point_c": series_value(hourly, "dew_point_2m", i),
                "pressure_hpa": series_value(hourly, "surface_pressure", i),
                "cloud_cover_pct": series_value(hourly, "cloud_cover", i),
                "wind_speed_ms": series_value(hourly, "wind_speed_10m", i),
                "uv_index": series_value(hourly, "uv_index", i),
                "ghi": series_value(hourly, "shortwave_radiation", i),
                "dni": series_value(hourly, "direct_normal_irradiance", i),
                "dhi": series_value(hourly, "diffuse_radiation", i),
            }
        )

    daily_rows = []
    for i, date_value in enumerate(daily.get("time") or []):
        daily_rows.append(
            {
                "date": date_value,
                "sunrise": (daily.get("sunrise") or [None] * 7)[i],
                "sunset": (daily.get("sunset") or [None] * 7)[i],
                "uv_index_max": (daily.get("uv_index_max") or [None] * 7)[i],
                "ghi_sum": (daily.get("shortwave_radiation_sum") or [None] * 7)[i],
                "temp_max_c": (daily.get("temperature_2m_max") or [None] * 7)[i],
                "temp_min_c": (daily.get("temperature_2m_min") or [None] * 7)[i],
                "precipitation_mm": (daily.get("precipitation_sum") or [None] * 7)[i],
                "wind_max_ms": (daily.get("wind_speed_10m_max") or [None] * 7)[i],
            }
        )

    location_id = upsert_location(
        {
            **location,
            "timezone": payload.get("timezone") or location.get("timezone"),
            "elevation_m": payload.get("elevation") or location.get("elevation_m"),
        }
    )
    save_solar_reading(location_id, current_data)

    return {
        "provider": "Open-Meteo",
        "refresh_seconds": APP_REFRESH_SECONDS,
        "location_id": location_id,
        "location": {
            **location,
            "site_name": site_name(location),
            "timezone": payload.get("timezone") or location.get("timezone"),
            "elevation_m": payload.get("elevation") or location.get("elevation_m"),
        },
        "current": current_data,
        "hourly": hourly_rows,
        "daily": daily_rows,
        "nasa_power": fetch_nasa_power(lat, lon),
        "raw_hourly": hourly,
        "current_hour_index": idx,
        "fetched_at": datetime.now().isoformat(),
    }


def save_solar_reading(location_id: int, current_data: dict[str, Any]) -> None:
    try:
        ts = parse_dt(current_data.get("time")) or datetime.now()
        db_execute(
            """
            INSERT INTO solar_readings
              (location_id, timestamp, ghi, dni, dhi, temperature_c,
               humidity_pct, wind_speed_ms, pressure_hpa, cloud_cover_pct,
               dew_point_c, uv_index, data_source)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                location_id,
                ts,
                current_data.get("ghi"),
                current_data.get("dni"),
                current_data.get("dhi"),
                current_data.get("temperature_c"),
                current_data.get("humidity_pct"),
                current_data.get("wind_speed_ms"),
                current_data.get("pressure_hpa"),
                current_data.get("cloud_cover_pct"),
                current_data.get("dew_point_c"),
                current_data.get("uv_index"),
                "Open-Meteo",
            ),
        )
    except Exception as exc:
        log_system("warning", "weather", "Could not store solar reading", {"error": str(exc)})


def rolling_average(values: list[float], start: int, end: int) -> float:
    window = values[start:end]
    clean = [v for v in window if v is not None]
    if not clean:
        raise ValueError("Insufficient live data for rolling feature calculation.")
    return float(np.mean(clean))


def build_feature_row(hourly: dict[str, list[Any]], idx: int) -> list[float]:
    times = hourly.get("time") or []
    if idx < 48 or idx >= len(times):
        raise ValueError("Insufficient live hourly history to build model lag features.")
    current_time = parse_dt(times[idx])
    if not current_time:
        raise ValueError("Invalid hourly timestamp returned by live provider.")

    ghi_series = [series_value(hourly, "shortwave_radiation", i) for i in range(len(times))]
    temp_now = required_series_value(hourly, "temperature_2m", idx)
    temp_prev = required_series_value(hourly, "temperature_2m", idx - 1)

    feature_map = {
        "ghi": required_series_value(hourly, "shortwave_radiation", idx),
        "dni": required_series_value(hourly, "direct_normal_irradiance", idx),
        "dhi": required_series_value(hourly, "diffuse_radiation", idx),
        "temperature_c": temp_now,
        "humidity_pct": required_series_value(hourly, "relative_humidity_2m", idx),
        "wind_speed_ms": required_series_value(hourly, "wind_speed_10m", idx),
        "pressure_hpa": required_series_value(hourly, "surface_pressure", idx),
        "cloud_cover_pct": required_series_value(hourly, "cloud_cover", idx),
        "hour_sin": np.sin(2 * np.pi * current_time.hour / 24),
        "hour_cos": np.cos(2 * np.pi * current_time.hour / 24),
        "month_sin": np.sin(2 * np.pi * current_time.month / 12),
        "month_cos": np.cos(2 * np.pi * current_time.month / 12),
        "doy_sin": np.sin(2 * np.pi * current_time.timetuple().tm_yday / 365),
        "doy_cos": np.cos(2 * np.pi * current_time.timetuple().tm_yday / 365),
        "ghi_lag_1h": required_series_value(hourly, "shortwave_radiation", idx - 1),
        "ghi_lag_3h": required_series_value(hourly, "shortwave_radiation", idx - 3),
        "ghi_lag_24h": required_series_value(hourly, "shortwave_radiation", idx - 24),
        "ghi_lag_48h": required_series_value(hourly, "shortwave_radiation", idx - 48),
        "ghi_roll_3h": rolling_average(ghi_series, idx - 2, idx + 1),
        "ghi_roll_6h": rolling_average(ghi_series, idx - 5, idx + 1),
        "ghi_roll_24h": rolling_average(ghi_series, idx - 23, idx + 1),
        "temp_diff_1h": temp_now - temp_prev,
        "temp_efficiency": 1 - 0.004 * max(0, temp_now - 25),
    }
    return [float(feature_map[col]) for col in feat_cols]


def get_default_pv_config(location_id: int | None = None) -> dict[str, Any] | None:
    params: tuple[Any, ...]
    if location_id:
        row = db_query_one(
            """
            SELECT * FROM pv_system_configs
            WHERE location_id=%s OR is_default=1
            ORDER BY location_id=%s DESC, is_default DESC, updated_at DESC
            LIMIT 1
            """,
            (location_id, location_id),
        )
    else:
        row = db_query_one(
            """
            SELECT * FROM pv_system_configs
            WHERE is_default=1
            ORDER BY updated_at DESC LIMIT 1
            """
        )
    return to_jsonable(row) if row else None


def calculate_power(ghi: float, pv_config: dict[str, Any] | None) -> dict[str, Any]:
    if not pv_config:
        return {
            "configured": False,
            "estimated_power_kw": None,
            "co2_offset_kg": None,
            "efficiency_pct": None,
            "message": "Configure PV system settings to estimate power output.",
        }
    panel_area = float(pv_config["panel_area_m2"])
    panel_efficiency = float(pv_config["panel_efficiency_pct"]) / 100
    loss = float(pv_config["loss_pct"]) / 100
    inverter_eff = float(pv_config["inverter_efficiency_pct"]) / 100
    capacity_kw = float(pv_config["capacity_kw"])
    dc_kw = max(0.0, (ghi / 1000.0) * panel_area * panel_efficiency)
    ac_kw = min(capacity_kw, dc_kw * (1 - loss) * inverter_eff)
    efficiency_pct = (ac_kw / capacity_kw) * 100 if capacity_kw else None
    return {
        "configured": True,
        "pv_config_id": pv_config.get("config_id"),
        "dc_power_kw": round(dc_kw, 4),
        "estimated_power_kw": round(ac_kw, 4),
        "co2_offset_kg": round(ac_kw * GRID_EMISSION_FACTOR, 4),
        "efficiency_pct": round(efficiency_pct, 2) if efficiency_pct is not None else None,
    }


def get_model_metrics() -> dict[str, Any]:
    global MODEL_METRICS_CACHE
    if MODEL_METRICS_CACHE:
        return MODEL_METRICS_CACHE
    rows = db_query_all(
        """
        SELECT model_name, version, training_date, rmse, mae, r2_score, is_active, file_path
        FROM model_registry
        ORDER BY is_active DESC, training_date DESC, model_id DESC
        """
    )
    metrics = {}
    for row in rows:
        name = (row.get("model_name") or "").lower()
        if name and name not in metrics:
            metrics[name] = to_jsonable(row)
    MODEL_METRICS_CACHE = metrics
    return metrics


def confidence_from_metrics(predictions: dict[str, float], metrics: dict[str, Any]) -> float | None:
    ensemble = predictions.get("ensemble")
    if ensemble is None:
        return None
    available_rmse = [
        float(m["rmse"])
        for m in metrics.values()
        if isinstance(m, dict) and m.get("rmse") is not None
    ]
    base = 0.85
    if available_rmse:
        base = max(0.0, 1 - (float(np.mean(available_rmse)) / 1000.0))
    spread_penalty = 0.0
    if predictions.get("xgboost") is not None and predictions.get("lstm") is not None:
        spread_penalty = min(0.25, abs(predictions["xgboost"] - predictions["lstm"]) / 1000.0)
    return round(max(0.0, min(1.0, base - spread_penalty)), 4)


def run_live_prediction(weather: dict[str, Any]) -> dict[str, Any]:
    if xgb_model is None or scaler is None or feat_cols is None:
        raise RuntimeError("XGBoost model stack is not loaded.")
    hourly = weather["raw_hourly"]
    idx = int(weather["current_hour_index"])
    current_features = np.array([build_feature_row(hourly, idx)])
    scaled_current = scaler.transform(current_features)
    predictions: dict[str, float] = {}

    xgb_pred = float(xgb_model.predict(scaled_current)[0])
    predictions["xgboost"] = round(max(0.0, xgb_pred), 3)

    if lstm_model is not None and y_max is not None:
        sequence_rows = [build_feature_row(hourly, i) for i in range(idx - 23, idx + 1)]
        sequence_scaled = scaler.transform(np.array(sequence_rows)).reshape(1, 24, len(feat_cols))
        lstm_norm = float(lstm_model.predict(sequence_scaled, verbose=0)[0][0])
        predictions["lstm"] = round(max(0.0, lstm_norm * float(y_max)), 3)

    if "lstm" in predictions:
        xgb_weight = max(0.0, min(1.0, ENSEMBLE_XGB_WEIGHT))
        predictions["ensemble"] = round(
            xgb_weight * predictions["xgboost"] + (1 - xgb_weight) * predictions["lstm"],
            3,
        )
    else:
        predictions["ensemble"] = predictions["xgboost"]

    metrics = get_model_metrics()
    predictions["confidence_score"] = confidence_from_metrics(predictions, metrics)
    return {"predictions": predictions, "metrics": metrics}


def store_prediction(
    weather: dict[str, Any],
    prediction_result: dict[str, Any],
    power_result: dict[str, Any],
    pv_config: dict[str, Any] | None,
) -> int:
    predictions = prediction_result["predictions"]
    forecast_time = (parse_dt(weather["current"]["time"]) or datetime.now()) + timedelta(hours=1)
    return db_execute(
        """
        INSERT INTO predictions
          (location_id, model_name, prediction_time, forecast_time,
           predicted_ghi, predicted_power, mae, xgboost_ghi, lstm_ghi,
           ensemble_ghi, confidence_score, input_snapshot, api_source, pv_config_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            weather["location_id"],
            "Ensemble",
            datetime.now(),
            forecast_time,
            predictions.get("ensemble"),
            power_result.get("estimated_power_kw"),
            (prediction_result.get("metrics", {}).get("ensemble") or {}).get("mae"),
            predictions.get("xgboost"),
            predictions.get("lstm"),
            predictions.get("ensemble"),
            predictions.get("confidence_score"),
            json_dumps({"location": weather["location"], "current": weather["current"]}),
            weather["provider"],
            pv_config.get("config_id") if pv_config else None,
        ),
    )


def create_retraining_thread(job_id: int, upload_id: int | None = None) -> None:
    def runner():
        log_path = BASE_DIR / "uploads" / f"retraining_job_{job_id}.log"
        commands = [
            [sys.executable, str(BASE_DIR / "src" / "preprocess.py")],
            [sys.executable, str(BASE_DIR / "src" / "train_xgboost.py")],
            [sys.executable, str(BASE_DIR / "src" / "train_lstm.py")],
        ]
        try:
            db_execute(
                "UPDATE retraining_jobs SET status='running', started_at=%s, log_path=%s WHERE job_id=%s",
                (datetime.now(), str(log_path), job_id),
            )
            with log_path.open("a", encoding="utf-8") as log_file:
                for command in commands:
                    log_file.write(f"\n$ {' '.join(command)}\n")
                    log_file.flush()
                    proc = subprocess.run(
                        command,
                        cwd=str(BASE_DIR),
                        stdout=log_file,
                        stderr=subprocess.STDOUT,
                        text=True,
                        check=False,
                    )
                    if proc.returncode != 0:
                        raise RuntimeError(f"Command failed with exit code {proc.returncode}: {command}")
            db_execute(
                "UPDATE retraining_jobs SET status='completed', finished_at=%s WHERE job_id=%s",
                (datetime.now(), job_id),
            )
            log_system("info", "retraining", "Retraining job completed", {"job_id": job_id})
        except Exception as exc:
            db_execute(
                "UPDATE retraining_jobs SET status='failed', finished_at=%s WHERE job_id=%s",
                (datetime.now(), job_id),
            )
            log_system("error", "retraining", "Retraining job failed", {"job_id": job_id, "error": str(exc)})

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()


@app.route("/")
def index():
    return app.send_static_file("index.html")


@app.route("/admin")
def admin_page():
    return app.send_static_file("admin.html")


@app.route("/api/health")
def health():
    db_ok = False
    try:
        conn = get_db()
        conn.close()
        db_ok = True
    except Exception:
        db_ok = False
    return jsonify(
        {
            "status": "running",
            "timestamp": datetime.now().isoformat(),
            "refresh_seconds": APP_REFRESH_SECONDS,
            "models": {
                "xgboost": xgb_model is not None,
                "lstm": lstm_model is not None,
                "scaler": scaler is not None,
                "features": len(feat_cols or []),
            },
            "database": db_ok,
            "providers": {
                "primary": ["Open-Meteo"],
                "enrichment": ["NASA POWER"],
                "optional_keys": {
                    "openweathermap": bool(OPENWEATHERMAP_API_KEY),
                    "weatherapi": bool(WEATHERAPI_KEY),
                },
            },
        }
    )


@app.route("/api/location/search")
def location_search():
    query = request.args.get("q", "").strip()
    if len(query) < 2:
        return jsonify({"status": "success", "results": []})
    try:
        data = fetch_json(
            "Open-Meteo Geocoding",
            "https://geocoding-api.open-meteo.com/v1/search",
            {"name": query, "count": 8, "language": "en", "format": "json"},
        )
        results = []
        for item in data.get("results") or []:
            results.append(
                {
                    "name": item.get("name"),
                    "state": item.get("admin1") or "",
                    "country": item.get("country") or "",
                    "country_code": item.get("country_code") or "",
                    "lat": item.get("latitude"),
                    "lon": item.get("longitude"),
                    "timezone": item.get("timezone"),
                    "elevation_m": item.get("elevation"),
                }
            )
        return jsonify({"status": "success", "results": results})
    except Exception as exc:
        return bad_request(str(exc), 502)


@app.route("/api/location/reverse")
def location_reverse():
    try:
        lat = float(request.args.get("lat"))
        lon = float(request.args.get("lon"))
        return jsonify({"status": "success", "location": reverse_geocode(lat, lon)})
    except Exception as exc:
        return bad_request(str(exc), 502)


@app.route("/api/weather")
def weather():
    try:
        location = resolve_location(request.args)
        bundle = fetch_weather_bundle(location)
        public_bundle = {k: v for k, v in bundle.items() if k not in {"raw_hourly", "current_hour_index"}}
        return jsonify({"status": "success", "data": to_jsonable(public_bundle)})
    except Exception as exc:
        log_system("error", "weather", "Weather request failed", {"error": str(exc)})
        return bad_request(str(exc), 502)


@app.route("/api/predict", methods=["POST"])
def predict():
    try:
        body = request.get_json(silent=True) or {}
        location = resolve_location(body)
        weather_bundle = fetch_weather_bundle(location)
        prediction_result = run_live_prediction(weather_bundle)
        pv_config = get_default_pv_config(weather_bundle["location_id"])
        ghi_for_power = prediction_result["predictions"]["ensemble"]
        power_result = calculate_power(ghi_for_power, pv_config)
        prediction_id = store_prediction(weather_bundle, prediction_result, power_result, pv_config)
        if power_result.get("configured"):
            try:
                db_execute(
                    """
                    INSERT INTO pv_output
                      (location_id, timestamp, ac_power_kw, dc_power_kw, energy_kwh, efficiency_pct)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        weather_bundle["location_id"],
                        datetime.now(),
                        power_result.get("estimated_power_kw"),
                        power_result.get("dc_power_kw"),
                        power_result.get("estimated_power_kw"),
                        power_result.get("efficiency_pct"),
                    ),
                )
            except Exception:
                pass
        return jsonify(
            {
                "status": "success",
                "prediction_id": prediction_id,
                "location": to_jsonable(weather_bundle["location"]),
                "current": to_jsonable(weather_bundle["current"]),
                "predictions": to_jsonable(prediction_result["predictions"]),
                "power": to_jsonable(power_result),
                "metrics": to_jsonable(prediction_result["metrics"]),
                "timestamp": datetime.now().isoformat(),
            }
        )
    except Exception as exc:
        log_system("error", "prediction", "Prediction failed", {"error": str(exc)})
        return bad_request(str(exc), 500)


@app.route("/api/pv-config", methods=["GET", "POST"])
def pv_config():
    if request.method == "GET":
        location_id = request.args.get("location_id", type=int)
        config = get_default_pv_config(location_id)
        return jsonify({"status": "success", "configured": bool(config), "config": to_jsonable(config)})

    body = request.get_json(silent=True) or {}
    required = ["system_name", "capacity_kw", "panel_area_m2", "panel_efficiency_pct", "loss_pct", "inverter_efficiency_pct"]
    missing = [field for field in required if body.get(field) in (None, "")]
    if missing:
        return bad_request(f"Missing PV settings: {', '.join(missing)}")
    try:
        values = {
            "system_name": str(body["system_name"]).strip(),
            "capacity_kw": float(body["capacity_kw"]),
            "panel_area_m2": float(body["panel_area_m2"]),
            "panel_efficiency_pct": float(body["panel_efficiency_pct"]),
            "tilt_deg": float(body["tilt_deg"]) if body.get("tilt_deg") not in (None, "") else None,
            "azimuth_deg": float(body["azimuth_deg"]) if body.get("azimuth_deg") not in (None, "") else None,
            "loss_pct": float(body["loss_pct"]),
            "inverter_efficiency_pct": float(body["inverter_efficiency_pct"]),
            "location_id": body.get("location_id"),
        }
        if values["capacity_kw"] <= 0 or values["panel_area_m2"] <= 0 or values["panel_efficiency_pct"] <= 0:
            return bad_request("PV capacity, panel area, and efficiency must be positive.")
        db_execute("UPDATE pv_system_configs SET is_default=0 WHERE is_default=1")
        config_id = db_execute(
            """
            INSERT INTO pv_system_configs
              (system_name, capacity_kw, panel_area_m2, panel_efficiency_pct,
               tilt_deg, azimuth_deg, loss_pct, inverter_efficiency_pct,
               location_id, is_default)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,1)
            """,
            (
                values["system_name"],
                values["capacity_kw"],
                values["panel_area_m2"],
                values["panel_efficiency_pct"],
                values["tilt_deg"],
                values["azimuth_deg"],
                values["loss_pct"],
                values["inverter_efficiency_pct"],
                values["location_id"],
            ),
        )
        log_system("info", "pv-config", "PV system configuration saved", {"config_id": config_id})
        return jsonify({"status": "success", "config_id": config_id})
    except Exception as exc:
        return bad_request(str(exc), 400)


@app.route("/api/history")
def history():
    days = request.args.get("days", default=7, type=int)
    location_id = request.args.get("location_id", type=int)
    try:
        if not location_id:
            row = db_query_one("SELECT location_id FROM locations ORDER BY location_id DESC LIMIT 1")
            location_id = row["location_id"] if row else None
        if not location_id:
            return jsonify({"status": "success", "count": 0, "data": []})
        rows = db_query_all(
            """
            SELECT
              strftime('%Y-%m-%d %H:00:00', timestamp) AS ts,
              ROUND(AVG(ghi), 2) AS ghi,
              ROUND(AVG(dni), 2) AS dni,
              ROUND(AVG(dhi), 2) AS dhi,
              ROUND(AVG(temperature_c), 2) AS temperature_c,
              ROUND(AVG(humidity_pct), 2) AS humidity_pct,
              ROUND(AVG(wind_speed_ms), 2) AS wind_speed_ms,
              ROUND(AVG(cloud_cover_pct), 2) AS cloud_cover_pct
            FROM solar_readings
            WHERE location_id=%s
              AND timestamp >= NOW() - INTERVAL %s DAY
            GROUP BY strftime('%Y-%m-%d %H:00:00', timestamp)
            ORDER BY ts ASC
            """,
            (location_id, days),
        )
        return jsonify({"status": "success", "count": len(rows), "data": to_jsonable(rows)})
    except Exception as exc:
        return bad_request(str(exc), 500)


@app.route("/api/predictions")
def predictions_log():
    rows = db_query_all(
        """
        SELECT
          p.prediction_id,
          p.model_name,
          p.prediction_time,
          p.forecast_time,
          p.predicted_ghi,
          p.predicted_power,
          p.xgboost_ghi,
          p.lstm_ghi,
          p.ensemble_ghi,
          p.confidence_score,
          p.actual_power,
          l.site_name
        FROM predictions p
        LEFT JOIN locations l ON l.location_id = p.location_id
        ORDER BY p.prediction_time DESC
        LIMIT 100
        """
    )
    return jsonify({"status": "success", "count": len(rows), "data": to_jsonable(rows)})


@app.route("/api/stats")
def stats():
    location_id = request.args.get("location_id", type=int)
    params: tuple[Any, ...] = ()
    where = ""
    if location_id:
        where = "WHERE location_id=%s"
        params = (location_id,)
    overall = db_query_one(
        f"""
        SELECT
          COUNT(*) AS total_readings,
          ROUND(AVG(ghi), 2) AS avg_ghi,
          ROUND(MAX(ghi), 2) AS peak_ghi,
          ROUND(AVG(temperature_c), 2) AS avg_temperature,
          ROUND(AVG(humidity_pct), 2) AS avg_humidity,
          MIN(timestamp) AS data_from,
          MAX(timestamp) AS data_to
        FROM solar_readings {where}
        """,
        params,
    )
    pred_stats = db_query_one("SELECT COUNT(*) AS total_predictions FROM predictions")
    return jsonify({"status": "success", "overall": to_jsonable(overall), "predictions": pred_stats})


@app.route("/api/auth/login", methods=["POST"])
def login():
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    user = db_query_one("SELECT * FROM users WHERE email=%s AND is_active=1", (email,))
    if not user or not check_password_hash(user["password_hash"], password):
        log_system("warning", "auth", "Failed admin login", {"email": email})
        return bad_request("Invalid email or password.", 401)
    token = secrets.token_hex(32)
    expires_at = datetime.now() + timedelta(hours=12)
    db_execute(
        "INSERT INTO user_sessions (session_id, user_id, expires_at) VALUES (%s,%s,%s)",
        (token, user["user_id"], expires_at),
    )
    db_execute("UPDATE users SET last_login_at=%s WHERE user_id=%s", (datetime.now(), user["user_id"]))
    safe_user = {k: v for k, v in user.items() if k != "password_hash"}
    return jsonify({"status": "success", "token": token, "expires_at": expires_at.isoformat(), "user": to_jsonable(safe_user)})


@app.route("/api/auth/setup-status")
def setup_status():
    row = db_query_one("SELECT COUNT(*) AS count FROM users")
    return jsonify({"status": "success", "setup_required": int(row["count"] or 0) == 0})


@app.route("/api/auth/bootstrap", methods=["POST"])
def bootstrap_admin():
    row = db_query_one("SELECT COUNT(*) AS count FROM users")
    if int(row["count"] or 0) > 0:
        return bad_request("Bootstrap is disabled after the first user is created.", 403)
    body = request.get_json(silent=True) or {}
    full_name = (body.get("full_name") or "").strip()
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    if not full_name or not email or len(password) < 8:
        return bad_request("Name, email, and an 8+ character password are required.")
    user_id = db_execute(
        """
        INSERT INTO users (full_name, email, password_hash, role, is_active)
        VALUES (%s,%s,%s,'admin',1)
        """,
        (full_name, email, generate_password_hash(password)),
    )
    log_system("info", "auth", "Bootstrap admin user created", {"user_id": user_id, "email": email})
    return jsonify({"status": "success", "user_id": user_id})


@app.route("/api/auth/logout", methods=["POST"])
def logout():
    token = auth_token()
    if token:
        db_execute("UPDATE user_sessions SET revoked_at=%s WHERE session_id=%s", (datetime.now(), token))
    return jsonify({"status": "success"})


@app.route("/api/auth/me")
def me():
    user = current_user()
    if not user:
        return bad_request("Not authenticated.", 401)
    return jsonify({"status": "success", "user": to_jsonable(user)})


@app.route("/api/admin/users", methods=["GET", "POST"])
@require_admin
def admin_users(user):
    if request.method == "GET":
        rows = db_query_all(
            """
            SELECT user_id, full_name, email, role, is_active, last_login_at, created_at
            FROM users ORDER BY created_at DESC
            """
        )
        return jsonify({"status": "success", "data": to_jsonable(rows)})
    body = request.get_json(silent=True) or {}
    required = ["full_name", "email", "password", "role"]
    missing = [field for field in required if not body.get(field)]
    if missing:
        return bad_request(f"Missing fields: {', '.join(missing)}")
    role = body["role"]
    if role not in {"admin", "operator", "viewer"}:
        return bad_request("Invalid user role.")
    user_id = db_execute(
        """
        INSERT INTO users (full_name, email, password_hash, role, is_active)
        VALUES (%s,%s,%s,%s,1)
        """,
        (
            body["full_name"],
            body["email"].strip().lower(),
            generate_password_hash(body["password"]),
            role,
        ),
    )
    return jsonify({"status": "success", "user_id": user_id})


@app.route("/api/admin/models")
@require_admin
def admin_models(user):
    rows = db_query_all(
        """
        SELECT model_id, model_name, version, training_date, rmse, mae,
               r2_score, hyperparams, file_path, is_active
        FROM model_registry
        ORDER BY is_active DESC, training_date DESC, model_id DESC
        """
    )
    return jsonify(
        {
            "status": "success",
            "loaded": {
                "xgboost": xgb_model is not None,
                "lstm": lstm_model is not None,
                "scaler": scaler is not None,
                "feature_count": len(feat_cols or []),
            },
            "data": to_jsonable(rows),
        }
    )


@app.route("/api/admin/logs")
@require_admin
def admin_logs(user):
    api_logs = db_query_all(
        """
        SELECT log_id, provider, endpoint, status_code, latency_ms,
               success, error_message, created_at
        FROM api_request_logs
        ORDER BY created_at DESC LIMIT 100
        """
    )
    system_logs = db_query_all(
        """
        SELECT log_id, level, source, message, context, created_at
        FROM system_logs
        ORDER BY created_at DESC LIMIT 100
        """
    )
    return jsonify({"status": "success", "api": to_jsonable(api_logs), "system": to_jsonable(system_logs)})


@app.route("/api/admin/uploads", methods=["GET", "POST"])
@require_admin
def admin_uploads(user):
    if request.method == "GET":
        rows = db_query_all(
            """
            SELECT upload_id, original_filename, stored_path, row_count,
                   status, error_message, created_at
            FROM dataset_uploads ORDER BY created_at DESC LIMIT 50
            """
        )
        return jsonify({"status": "success", "data": to_jsonable(rows)})
    if "file" not in request.files:
        return bad_request("Upload a dataset file.")
    file = request.files["file"]
    original = file.filename or ""
    ext = Path(original).suffix.lower()
    if ext not in ALLOWED_UPLOADS:
        return bad_request("Allowed uploads: CSV, Parquet, XLSX, XLS.")
    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)
    stored_name = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{secure_filename(original)}"
    stored_path = Path(app.config["UPLOAD_FOLDER"]) / stored_name
    file.save(stored_path)
    row_count = None
    status = "uploaded"
    error = None
    try:
        if ext == ".csv":
            with stored_path.open("r", encoding="utf-8", errors="ignore") as handle:
                row_count = max(0, sum(1 for _ in csv.reader(handle)) - 1)
        elif ext == ".parquet":
            import pandas as pd

            row_count = len(pd.read_parquet(stored_path))
        elif ext in {".xlsx", ".xls"}:
            import pandas as pd

            row_count = len(pd.read_excel(stored_path))
        status = "validated"
    except Exception as exc:
        status = "failed"
        error = str(exc)
    upload_id = db_execute(
        """
        INSERT INTO dataset_uploads
          (user_id, original_filename, stored_path, row_count, status, error_message)
        VALUES (%s,%s,%s,%s,%s,%s)
        """,
        (user["user_id"], original, str(stored_path), row_count, status, error),
    )
    return jsonify({"status": "success", "upload_id": upload_id, "validation_status": status, "row_count": row_count, "error": error})


@app.route("/api/admin/retrain", methods=["POST"])
@require_admin
def admin_retrain(user):
    body = request.get_json(silent=True) or {}
    upload_id = body.get("upload_id")
    command_text = "python src/preprocess.py && python src/train_xgboost.py && python src/train_lstm.py"
    job_id = db_execute(
        """
        INSERT INTO retraining_jobs (requested_by, upload_id, status, command)
        VALUES (%s,%s,'queued',%s)
        """,
        (user["user_id"], upload_id, command_text),
    )
    create_retraining_thread(job_id, upload_id)
    return jsonify({"status": "success", "job_id": job_id})


@app.route("/api/admin/jobs")
@require_admin
def admin_jobs(user):
    rows = db_query_all(
        """
        SELECT job_id, requested_by, upload_id, status, command, log_path,
               started_at, finished_at, created_at
        FROM retraining_jobs ORDER BY created_at DESC LIMIT 50
        """
    )
    return jsonify({"status": "success", "data": to_jsonable(rows)})


@app.route("/api/admin/analytics")
@require_admin
def admin_analytics(user):
    database = {
        "locations": db_query_one("SELECT COUNT(*) AS count FROM locations"),
        "readings": db_query_one("SELECT COUNT(*) AS count FROM solar_readings"),
        "predictions": db_query_one("SELECT COUNT(*) AS count FROM predictions"),
        "users": db_query_one("SELECT COUNT(*) AS count FROM users"),
        "api_logs": db_query_one("SELECT COUNT(*) AS count FROM api_request_logs"),
    }
    api_health = db_query_all(
        """
        SELECT provider,
               COUNT(*) AS total,
               SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) AS ok,
               SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) AS failed,
               ROUND(AVG(latency_ms), 0) AS avg_latency_ms
        FROM api_request_logs
        WHERE created_at >= NOW() - INTERVAL 7 DAY
        GROUP BY provider
        ORDER BY total DESC
        """
    )
    return jsonify({"status": "success", "database": to_jsonable(database), "api_health": to_jsonable(api_health)})


@app.route("/api/admin/stats")
@require_admin
def admin_stats(user):
    return admin_analytics()


@app.route("/api/powerbi/views")
def powerbi_views():
    rows = db_query_all(
        """
        SELECT TABLE_NAME AS view_name
        FROM information_schema.VIEWS
        WHERE TABLE_SCHEMA=%s AND TABLE_NAME LIKE 'vw_powerbi_%%'
        ORDER BY TABLE_NAME
        """,
        (os.getenv("DB_NAME", "solar_forecast_db"),),
    )
    return jsonify({"status": "success", "views": to_jsonable(rows)})


try:
    ensure_schema()
except Exception as exc:  # pragma: no cover - startup diagnostic
    print(f"Schema setup skipped: {exc}")


if __name__ == "__main__":
    print("=" * 60)
    print("  Solar AI Forecasting Platform Backend")
    print("=" * 60)
    app.run(debug=True, host="0.0.0.0", port=5000)
