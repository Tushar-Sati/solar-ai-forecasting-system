"""
src/download_data.py
--------------------
Downloads solar + weather data from Open-Meteo (no API key needed)
and optionally from NSRDB (requires NREL API key).

HOW TO RUN:
    cd solar_ai_project
    venv\Scripts\activate          (Windows)
    source venv/bin/activate       (Mac/Linux)
    python src/download_data.py
"""

import os
import requests
import pandas as pd
from dotenv import load_dotenv

load_dotenv()   # reads your .env file


# ─── CONFIG ──────────────────────────────────────────────────────────────────
# Change these coordinates to your preferred location
LATITUDE  = 28.6139   # New Delhi — change to your city
LONGITUDE = 77.2090
START_DATE = "2020-01-01"
END_DATE   = "2022-12-31"
SITE_NAME  = "New Delhi, India"
# ─────────────────────────────────────────────────────────────────────────────


def download_open_meteo(lat, lon, start, end):
    """
    Downloads historical solar + weather data from Open-Meteo.
    FREE — no API key required. Works immediately.
    Returns a cleaned DataFrame.
    """
    print(f"\n[1/3] Downloading weather data from Open-Meteo...")
    print(f"      Location: ({lat}, {lon})  |  {start} → {end}")

    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude":   lat,
        "longitude":  lon,
        "start_date": start,
        "end_date":   end,
        "hourly": ",".join([
            "shortwave_radiation",       # GHI equivalent
            "direct_normal_irradiance",  # DNI
            "diffuse_radiation",         # DHI
            "temperature_2m",
            "relativehumidity_2m",
            "windspeed_10m",
            "surface_pressure",
            "cloudcover",
            "precipitation"
        ]),
        "timezone": "auto"
    }

    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as e:
        print(f"[ERROR] Could not reach Open-Meteo: {e}")
        return None

    # Build DataFrame from hourly data
    df = pd.DataFrame(data["hourly"])

    # Parse timestamp
    df["timestamp"] = pd.to_datetime(df["time"])
    df = df.drop(columns=["time"])

    # Rename to our schema column names
    df = df.rename(columns={
        "shortwave_radiation":       "ghi",
        "direct_normal_irradiance":  "dni",
        "diffuse_radiation":         "dhi",
        "temperature_2m":            "temperature_c",
        "relativehumidity_2m":       "humidity_pct",
        "windspeed_10m":             "wind_speed_ms",
        "surface_pressure":          "pressure_hpa",
        "cloudcover":                "cloud_cover_pct",
        "precipitation":             "precipitation_mm"
    })

    # Reorder columns cleanly
    cols = [
        "timestamp", "ghi", "dni", "dhi",
        "temperature_c", "humidity_pct", "wind_speed_ms",
        "pressure_hpa", "cloud_cover_pct", "precipitation_mm"
    ]
    df = df[cols]

    print(f"      Downloaded {len(df):,} rows  |  Columns: {list(df.columns)}")
    print(f"      Date range: {df['timestamp'].min()} → {df['timestamp'].max()}")
    return df


def validate_data(df):
    """
    Runs basic quality checks and prints a report.
    Call this after downloading to spot problems early.
    """
    print(f"\n[2/3] Validating data quality...")

    report = {
        "Total rows":        len(df),
        "Null values":       df.isnull().sum().sum(),
        "Duplicate rows":    df.duplicated(subset=["timestamp"]).sum(),
        "GHI min":           df["ghi"].min(),
        "GHI max":           df["ghi"].max(),
        "Temp min (°C)":     df["temperature_c"].min(),
        "Temp max (°C)":     df["temperature_c"].max(),
        "Date start":        str(df["timestamp"].min()),
        "Date end":          str(df["timestamp"].max()),
        "Daytime rows (GHI>0)": (df["ghi"] > 0).sum(),
    }

    for key, val in report.items():
        status = ""
        if key == "Null values"     and val  > 0:  status = "  ← check this"
        if key == "Duplicate rows"  and val  > 0:  status = "  ← check this"
        if key == "GHI max"         and val  > 1400: status = "  ← outlier!"
        if key == "Temp max (°C)"   and val  > 60:   status = "  ← check this"
        print(f"      {key:<28} {val}{status}")

    return report


def save_data(df, filename):
    """
    Saves the DataFrame to two formats:
      - Parquet: compressed, fast to reload for ML
      - CSV:     human-readable backup
    """
    print(f"\n[3/3] Saving data...")

    os.makedirs("data/raw", exist_ok=True)

    parquet_path = f"data/raw/{filename}.parquet"
    csv_path     = f"data/raw/{filename}.csv"

    df.to_parquet(parquet_path, index=False)
    df.to_csv(csv_path, index=False)

    parquet_size = os.path.getsize(parquet_path) / 1024
    csv_size     = os.path.getsize(csv_path)     / 1024

    print(f"      Parquet saved: {parquet_path}  ({parquet_size:.1f} KB)")
    print(f"      CSV saved:     {csv_path}       ({csv_size:.1f} KB)")
    print(f"      Parquet is {csv_size/parquet_size:.1f}x smaller than CSV — use it for ML")


if __name__ == "__main__":
    print("=" * 55)
    print("  Solar AI — Phase 1: Data Download")
    print("=" * 55)

    # Download
    df = download_open_meteo(LATITUDE, LONGITUDE, START_DATE, END_DATE)

    if df is not None:
        # Validate
        validate_data(df)

        # Save
        save_data(df, "solar_weather_raw")

        print("\n[DONE] Phase 1 complete! Check data/raw/ for your files.")
        print("       Next step: python src/ingest_to_mysql.py")
    else:
        print("\n[FAILED] Could not download data. Check your internet connection.")