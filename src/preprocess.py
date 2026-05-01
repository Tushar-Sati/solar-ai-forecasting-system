"""
src/preprocess.py
-----------------
Phase 2: Complete preprocessing pipeline.
Loads raw data from MySQL, cleans it, engineers features,
normalizes, creates ML sequences, and saves everything.

HOW TO RUN:
    python src/preprocess.py
"""

import os
import numpy as np
import pandas as pd
import pymysql
import joblib
from sklearn.preprocessing import MinMaxScaler
from sklearn.model_selection import train_test_split
from dotenv import load_dotenv

load_dotenv()

# ─── CONFIG ───────────────────────────────────────────────
LOOKBACK     = 24    # hours of history LSTM looks back at
TEST_SIZE    = 0.15  # 15% for final test
VAL_SIZE     = 0.15  # 15% for validation
LOCATION_ID  = 1     # matches what we inserted in Phase 1
# ──────────────────────────────────────────────────────────


# ══════════════════════════════════════════════════════════
# STEP 1 — Load from MySQL
# ══════════════════════════════════════════════════════════
def load_from_mysql():
    print("\n[1/6] Loading data from MySQL...")
    conn = pymysql.connect(
        host     = os.getenv("DB_HOST", "localhost"),
        user     = os.getenv("DB_USER", "root"),
        password = os.getenv("DB_PASSWORD", "Siyaram@#2024"),
        database = os.getenv("DB_NAME", "solar_forecast_db"),
        charset  = "utf8mb4"
    )
    query = """
        SELECT timestamp, ghi, dni, dhi,
               temperature_c, humidity_pct,
               wind_speed_ms, pressure_hpa, cloud_cover_pct
        FROM solar_readings
        WHERE location_id = %s
        ORDER BY timestamp ASC
    """
    df = pd.read_sql(query, conn, params=(LOCATION_ID,))
    conn.close()

    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)

    print(f"    Loaded      : {len(df):,} rows")
    print(f"    Columns     : {list(df.columns)}")
    print(f"    Date range  : {df['timestamp'].min()} to {df['timestamp'].max()}")
    return df


# ══════════════════════════════════════════════════════════
# STEP 2 — Clean
# ══════════════════════════════════════════════════════════
def clean(df):
    print("\n[2/6] Cleaning data...")
    original_len = len(df)

    # Remove exact duplicate timestamps
    df = df.drop_duplicates(subset=["timestamp"])

    # Interpolate small gaps (up to 3 consecutive missing hours)
    numeric_cols = df.select_dtypes(include=np.number).columns
    df[numeric_cols] = df[numeric_cols].interpolate(method="linear", limit=3)

    # Drop any remaining nulls
    df = df.dropna(subset=["ghi", "temperature_c"])

    # Remove physical impossibilities
    df = df[df["ghi"].between(0, 1400)]          # Max solar constant ~1361 W/m2
    df = df[df["temperature_c"].between(-20, 60)]
    df = df[df["humidity_pct"].between(0, 100)]
    df = df[df["wind_speed_ms"] >= 0]

    # Keep only daytime rows where sun is actually shining
    # Night rows (GHI=0) add noise and are not useful for forecasting
    df_day = df[df["ghi"] > 0].copy()

    print(f"    Original rows   : {original_len:,}")
    print(f"    After cleaning  : {len(df):,}")
    print(f"    Daytime rows    : {len(df_day):,}")
    print(f"    Rows removed    : {original_len - len(df):,}")
    print(f"    GHI range       : {df['ghi'].min():.1f} to {df['ghi'].max():.1f} W/m2")
    print(f"    Temp range      : {df['temperature_c'].min():.1f} to {df['temperature_c'].max():.1f} C")

    # We keep ALL rows (day + night) for sequence integrity
    # but flag daytime for analysis
    df["is_daytime"] = df["ghi"] > 0
    return df


# ══════════════════════════════════════════════════════════
# STEP 3 — Feature engineering
# ══════════════════════════════════════════════════════════
def engineer_features(df):
    print("\n[3/6] Engineering features...")
    df = df.copy()

    # --- Time features ---
    df["hour"]        = df["timestamp"].dt.hour
    df["month"]       = df["timestamp"].dt.month
    df["day_of_year"] = df["timestamp"].dt.dayofyear
    df["weekday"]     = df["timestamp"].dt.weekday

    # Cyclical encoding — prevents model seeing hour 23 and hour 0 as far apart
    # sin/cos transforms wrap the values into a circle
    df["hour_sin"]    = np.sin(2 * np.pi * df["hour"]  / 24)
    df["hour_cos"]    = np.cos(2 * np.pi * df["hour"]  / 24)
    df["month_sin"]   = np.sin(2 * np.pi * df["month"] / 12)
    df["month_cos"]   = np.cos(2 * np.pi * df["month"] / 12)
    df["doy_sin"]     = np.sin(2 * np.pi * df["day_of_year"] / 365)
    df["doy_cos"]     = np.cos(2 * np.pi * df["day_of_year"] / 365)

    # --- Lag features — what GHI was in the past ---
    df["ghi_lag_1h"]   = df["ghi"].shift(1)
    df["ghi_lag_3h"]   = df["ghi"].shift(3)
    df["ghi_lag_24h"]  = df["ghi"].shift(24)
    df["ghi_lag_48h"]  = df["ghi"].shift(48)

    # --- Rolling averages — smoothed recent history ---
    df["ghi_roll_3h"]  = df["ghi"].rolling(window=3,  min_periods=1).mean()
    df["ghi_roll_6h"]  = df["ghi"].rolling(window=6,  min_periods=1).mean()
    df["ghi_roll_24h"] = df["ghi"].rolling(window=24, min_periods=1).mean()

    # --- Temperature difference (rate of change) ---
    df["temp_diff_1h"] = df["temperature_c"].diff(1)

    # --- Solar efficiency proxy ---
    # Higher temp = lower panel efficiency. This captures that relationship.
    df["temp_efficiency"] = 1 - 0.004 * (df["temperature_c"] - 25).clip(lower=0)

    # Drop rows with NaN created by lag/diff (first 48 rows)
    df = df.dropna()
    df = df.reset_index(drop=True)

    new_features = [
        "hour_sin","hour_cos","month_sin","month_cos","doy_sin","doy_cos",
        "ghi_lag_1h","ghi_lag_3h","ghi_lag_24h","ghi_lag_48h",
        "ghi_roll_3h","ghi_roll_6h","ghi_roll_24h",
        "temp_diff_1h","temp_efficiency"
    ]
    print(f"    Features added  : {len(new_features)}")
    print(f"    Total columns   : {len(df.columns)}")
    print(f"    Rows after lag  : {len(df):,}")
    return df


# ══════════════════════════════════════════════════════════
# STEP 4 — Normalize
# ══════════════════════════════════════════════════════════
def normalize(df):
    print("\n[4/6] Normalizing features...")

    # These are all the features we'll give to the ML models
    feature_cols = [
        "ghi", "dni", "dhi",
        "temperature_c", "humidity_pct", "wind_speed_ms",
        "pressure_hpa", "cloud_cover_pct",
        "hour_sin", "hour_cos", "month_sin", "month_cos",
        "doy_sin", "doy_cos",
        "ghi_lag_1h", "ghi_lag_3h", "ghi_lag_24h", "ghi_lag_48h",
        "ghi_roll_3h", "ghi_roll_6h", "ghi_roll_24h",
        "temp_diff_1h", "temp_efficiency"
    ]

    # Only use columns that exist
    feature_cols = [c for c in feature_cols if c in df.columns]

    X = df[feature_cols].values   # features matrix
    y = df["ghi"].values          # target: GHI we want to predict

    # Fit scaler ONLY on training portion to prevent data leakage
    # Data leakage = accidentally letting future data influence training
    split_idx = int(len(X) * (1 - TEST_SIZE - VAL_SIZE))
    scaler = MinMaxScaler(feature_range=(0, 1))
    scaler.fit(X[:split_idx])     # fit on train only
    X_scaled = scaler.transform(X)  # apply to all

    # Save scaler — needed later to inverse-transform predictions
    os.makedirs("data/models", exist_ok=True)
    joblib.dump(scaler, "data/models/scaler.pkl")
    joblib.dump(feature_cols, "data/models/feature_cols.pkl")

    print(f"    Feature columns : {len(feature_cols)}")
    print(f"    X shape         : {X_scaled.shape}")
    print(f"    y shape         : {y.shape}")
    print(f"    X min/max       : {X_scaled.min():.3f} / {X_scaled.max():.3f}")
    print(f"    Scaler saved    : data/models/scaler.pkl")
    return X_scaled, y, feature_cols, df


# ══════════════════════════════════════════════════════════
# STEP 5 — Create sequences for LSTM
# ══════════════════════════════════════════════════════════
def create_sequences(X, y, lookback=24):
    print(f"\n[5/6] Creating LSTM sequences (lookback={lookback}h)...")
    Xs, ys = [], []
    for i in range(lookback, len(X)):
        Xs.append(X[i - lookback:i])  # 24 hours of features
        ys.append(y[i])               # the next hour's GHI
    Xs = np.array(Xs)
    ys = np.array(ys)
    print(f"    Sequence shape  : {Xs.shape}  (samples, timesteps, features)")
    print(f"    Target shape    : {ys.shape}")
    return Xs, ys


def split_data(X_seq, y_seq, X_flat, y_flat):
    print("\n    Splitting into train / val / test (chronological)...")
    n = len(X_seq)
    train_end = int(n * (1 - TEST_SIZE - VAL_SIZE))
    val_end   = int(n * (1 - TEST_SIZE))

    # LSTM sequences
    X_train = X_seq[:train_end]
    X_val   = X_seq[train_end:val_end]
    X_test  = X_seq[val_end:]
    y_train = y_seq[:train_end]
    y_val   = y_seq[train_end:val_end]
    y_test  = y_seq[val_end:]

    # XGBoost flat arrays (same chronological split)
    Xf_train = X_flat[:train_end]
    Xf_val   = X_flat[train_end:val_end]
    Xf_test  = X_flat[val_end:]
    yf_train = y_flat[:train_end]
    yf_val   = y_flat[train_end:val_end]
    yf_test  = y_flat[val_end:]

    print(f"    Train size      : {len(X_train):,} samples")
    print(f"    Val size        : {len(X_val):,} samples")
    print(f"    Test size       : {len(X_test):,} samples")

    return (X_train, X_val, X_test, y_train, y_val, y_test,
            Xf_train, Xf_val, Xf_test, yf_train, yf_val, yf_test)


# ══════════════════════════════════════════════════════════
# STEP 6 — Save everything
# ══════════════════════════════════════════════════════════
def save_all(df, splits):
    print("\n[6/6] Saving processed data...")
    os.makedirs("data/processed", exist_ok=True)

    (X_train, X_val, X_test, y_train, y_val, y_test,
     Xf_train, Xf_val, Xf_test, yf_train, yf_val, yf_test) = splits

    # Save processed DataFrame
    df.to_parquet("data/processed/solar_processed.parquet", index=False)

    # Save numpy arrays for ML training
    np.save("data/processed/X_train.npy",  X_train)
    np.save("data/processed/X_val.npy",    X_val)
    np.save("data/processed/X_test.npy",   X_test)
    np.save("data/processed/y_train.npy",  y_train)
    np.save("data/processed/y_val.npy",    y_val)
    np.save("data/processed/y_test.npy",   y_test)
    np.save("data/processed/Xf_train.npy", Xf_train)
    np.save("data/processed/Xf_val.npy",   Xf_val)
    np.save("data/processed/Xf_test.npy",  Xf_test)
    np.save("data/processed/yf_train.npy", yf_train)
    np.save("data/processed/yf_val.npy",   yf_val)
    np.save("data/processed/yf_test.npy",  yf_test)

    print(f"    Processed DF    : data/processed/solar_processed.parquet")
    print(f"    LSTM arrays     : X_train {X_train.shape}, X_val {X_val.shape}, X_test {X_test.shape}")
    print(f"    XGBoost arrays  : Xf_train {Xf_train.shape}")
    print(f"    All files saved to data/processed/")


# ══════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 55)
    print("  Solar AI - Phase 2: Preprocessing Pipeline")
    print("=" * 55)

    df                          = load_from_mysql()
    df                          = clean(df)
    df                          = engineer_features(df)
    X_scaled, y, feat_cols, df  = normalize(df)
    X_seq, y_seq                = create_sequences(X_scaled, y, LOOKBACK)
    splits                      = split_data(X_seq, y_seq, X_scaled, y)
    save_all(df, splits)

    print("\n" + "=" * 55)
    print("  Phase 2 COMPLETE!")
    print("=" * 55)
    print("  Files created:")
    print("    data/processed/solar_processed.parquet")
    print("    data/processed/X_train.npy  (LSTM training data)")
    print("    data/processed/X_test.npy   (LSTM test data)")
    print("    data/models/scaler.pkl       (normalization scaler)")
    print("    data/models/feature_cols.pkl (feature names)")
    print("\n  Next step: python src/train_xgboost.py")