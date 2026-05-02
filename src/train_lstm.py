"""
src/train_lstm.py
-----------------
Phase 4: Train LSTM model for solar GHI time-series forecasting.

HOW TO RUN:
    python src/train_lstm.py

NOTE: This takes 5-15 minutes depending on your CPU/GPU.
      You will see progress after each epoch.
"""

import os
from contextlib import closing
import numpy as np
import joblib
import json
from datetime import date
import sqlite3
from pathlib import Path
from dotenv import load_dotenv

import tensorflow as tf
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import LSTM, Dense, Dropout, BatchNormalization
from tensorflow.keras.callbacks import EarlyStopping, ReduceLROnPlateau, ModelCheckpoint
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

load_dotenv()
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def db_path() -> Path:
    path = Path(os.getenv("SQLITE_DB_PATH", "solar_forecast_db.sqlite3"))
    return path if path.is_absolute() else PROJECT_ROOT / path

# Suppress TensorFlow info messages — only show warnings/errors
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "2"


# ══════════════════════════════════════════════════════════
# STEP 1 — Load data
# ══════════════════════════════════════════════════════════
def load_data():
    print("\n[1/5] Loading LSTM sequence data...")

    X_train = np.load("data/processed/X_train.npy")
    X_val   = np.load("data/processed/X_val.npy")
    X_test  = np.load("data/processed/X_test.npy")
    y_train = np.load("data/processed/y_train.npy")
    y_val   = np.load("data/processed/y_val.npy")
    y_test  = np.load("data/processed/y_test.npy")

    print(f"    X_train : {X_train.shape}  (samples, timesteps, features)")
    print(f"    X_val   : {X_val.shape}")
    print(f"    X_test  : {X_test.shape}")
    print(f"    y_train : {y_train.shape}")

    # Normalize targets to 0-1 range for stable LSTM training
    # We save the max so we can inverse-transform predictions later
    y_max = y_train.max()
    y_train_n = y_train / y_max
    y_val_n   = y_val   / y_max
    y_test_n  = y_test  / y_max

    joblib.dump(y_max, "data/models/y_max.pkl")
    print(f"    y_max saved : {y_max:.2f} W/m2 (used to rescale predictions)")

    return X_train, X_val, X_test, y_train, y_val, y_test, y_train_n, y_val_n, y_test_n, y_max


# ══════════════════════════════════════════════════════════
# STEP 2 — Build LSTM architecture
# ══════════════════════════════════════════════════════════
def build_model(timesteps, n_features):
    print("\n[2/5] Building LSTM model...")

    model = Sequential([

        # Layer 1 — first LSTM layer
        # return_sequences=True passes the full sequence to next LSTM layer
        LSTM(128, return_sequences=True,
             input_shape=(timesteps, n_features),
             kernel_regularizer=tf.keras.regularizers.l2(0.001)),
        BatchNormalization(),   # stabilises training
        Dropout(0.2),           # randomly drops 20% of neurons — prevents overfitting

        # Layer 2 — second LSTM layer
        LSTM(64, return_sequences=True,
             kernel_regularizer=tf.keras.regularizers.l2(0.001)),
        BatchNormalization(),
        Dropout(0.2),

        # Layer 3 — third LSTM layer (compresses to single vector)
        LSTM(32, return_sequences=False),
        Dropout(0.1),

        # Dense layers — final prediction head
        Dense(32, activation="relu"),
        Dense(16, activation="relu"),
        Dense(1)   # single output = predicted GHI
    ])

    model.compile(
        optimizer = tf.keras.optimizers.Adam(learning_rate=0.001),
        loss      = "huber",   # Huber loss is robust to outliers (better than MSE)
        metrics   = ["mae"]
    )

    model.summary()
    total_params = model.count_params()
    print(f"\n    Total parameters : {total_params:,}")
    return model


# ══════════════════════════════════════════════════════════
# STEP 3 — Train
# ══════════════════════════════════════════════════════════
def train_model(model, X_train, y_train_n, X_val, y_val_n):
    print("\n[3/5] Training LSTM...")
    print("    Epochs will print below. Training stops early if no improvement.")
    print("    Expected time: 5-15 minutes\n")

    os.makedirs("data/models", exist_ok=True)

    callbacks = [
        # Stop training if val_loss doesn't improve for 10 epochs
        EarlyStopping(
            monitor="val_loss",
            patience=10,
            restore_best_weights=True,
            verbose=1
        ),
        # Halve learning rate if stuck for 5 epochs
        ReduceLROnPlateau(
            monitor="val_loss",
            factor=0.5,
            patience=5,
            min_lr=1e-6,
            verbose=1
        ),
        # Save the best model automatically during training
        ModelCheckpoint(
            filepath="data/models/lstm_best.keras",
            monitor="val_loss",
            save_best_only=True,
            verbose=0
        )
    ]

    history = model.fit(
        X_train, y_train_n,
        validation_data = (X_val, y_val_n),
        epochs          = 100,     # max epochs — early stopping will trigger before this
        batch_size      = 32,
        callbacks       = callbacks,
        verbose         = 1
    )

    print(f"\n    Training complete!")
    print(f"    Best epoch      : {np.argmin(history.history['val_loss']) + 1}")
    print(f"    Best val_loss   : {min(history.history['val_loss']):.4f}")
    return model, history


# ══════════════════════════════════════════════════════════
# STEP 4 — Evaluate
# ══════════════════════════════════════════════════════════
def evaluate(model, X_test, y_test, y_test_n, y_max):
    print("\n[4/5] Evaluating on test set...")

    # Predict (output is normalized 0-1)
    preds_n = model.predict(X_test, verbose=0).flatten()

    # Rescale back to W/m2
    preds   = preds_n * y_max
    actuals = y_test

    mae  = mean_absolute_error(actuals, preds)
    rmse = np.sqrt(mean_squared_error(actuals, preds))
    r2   = r2_score(actuals, preds)

    mask = actuals > 10
    mape = np.mean(np.abs((actuals[mask] - preds[mask]) / actuals[mask])) * 100

    print(f"    MAE   : {mae:.2f}  W/m2")
    print(f"    RMSE  : {rmse:.2f} W/m2")
    print(f"    MAPE  : {mape:.2f} %")
    print(f"    R2    : {r2:.4f}")

    if r2 > 0.92:
        print(f"    Rating: Excellent - publication quality")
    elif r2 > 0.85:
        print(f"    Rating: Good")
    else:
        print(f"    Rating: Acceptable")

    # Show sample predictions vs actuals
    print(f"\n    Sample predictions vs actuals (first 8 test rows):")
    print(f"    {'Actual':>10}  {'Predicted':>10}  {'Error':>10}")
    for a, p in zip(actuals[:8], preds[:8]):
        print(f"    {a:>10.1f}  {p:>10.1f}  {abs(a-p):>10.1f}")

    return {"mae": mae, "rmse": rmse, "mape": mape, "r2": r2}, preds


# ══════════════════════════════════════════════════════════
# STEP 5 — Save model + register
# ══════════════════════════════════════════════════════════
def save_and_register(model, metrics):
    print("\n[5/5] Saving final model...")

    model_path = "data/models/lstm_solar.keras"
    model.save(model_path)
    print(f"    Model saved : {model_path}")

    # Register in MySQL
    try:
        conn = sqlite3.connect(str(db_path()))
        with closing(conn.cursor()) as cur:
            hyperparams = json.dumps({
                "layers": [128, 64, 32],
                "dropout": 0.2,
                "lookback": 24,
                "batch_size": 32,
                "optimizer": "Adam",
                "loss": "huber"
            })
            cur.execute("""
                INSERT INTO model_registry
                    (model_name, version, training_date, rmse, mae,
                     r2_score, hyperparams, file_path, is_active)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (
                "LSTM", "1.0", date.today(),
                round(metrics["rmse"], 4),
                round(metrics["mae"],  4),
                round(metrics["r2"],   4),
                hyperparams, model_path, True
            ))
        conn.commit()
        conn.close()
        print("    Registered in SQLite model_registry")
    except Exception as e:
        print(f"    SQLite register skipped: {e}")


# ══════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 55)
    print("  Solar AI - Phase 4: LSTM Training")
    print("=" * 55)

    (X_train, X_val, X_test,
     y_train, y_val, y_test,
     y_train_n, y_val_n, y_test_n,
     y_max) = load_data()

    timesteps  = X_train.shape[1]   # 24
    n_features = X_train.shape[2]   # 23

    model           = build_model(timesteps, n_features)
    model, history  = train_model(model, X_train, y_train_n, X_val, y_val_n)
    metrics, preds  = evaluate(model, X_test, y_test, y_test_n, y_max)
    save_and_register(model, metrics)

    print("\n" + "=" * 55)
    print("  Phase 4 COMPLETE!")
    print("=" * 55)
    print(f"  LSTM R2 Score  : {metrics['r2']:.4f}")
    print(f"  XGBoost R2     : 0.9999  (from Phase 3)")
    print(f"  Model saved    : data/models/lstm_solar.keras")
    print("\n  Both models trained! Next: python api/app.py")
