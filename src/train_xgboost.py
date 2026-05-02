"""
src/train_xgboost.py
--------------------
Phase 3: Train XGBoost model for solar GHI prediction.

HOW TO RUN:
    python src/train_xgboost.py
"""

import os
import numpy as np
import joblib
import json
from datetime import date
import sqlite3
from pathlib import Path
from dotenv import load_dotenv

import xgboost as xgb
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

load_dotenv()


# ══════════════════════════════════════════════════════════
# STEP 1 — Load preprocessed arrays
# ══════════════════════════════════════════════════════════
def load_data():
    print("\n[1/5] Loading preprocessed data...")

    # XGBoost uses flat 2D arrays (not sequences)
    # Shape: (samples, features) — no time dimension needed
    Xf_train = np.load("data/processed/Xf_train.npy")
    Xf_val   = np.load("data/processed/Xf_val.npy")
    Xf_test  = np.load("data/processed/Xf_test.npy")
    yf_train = np.load("data/processed/yf_train.npy")
    yf_val   = np.load("data/processed/yf_val.npy")
    yf_test  = np.load("data/processed/yf_test.npy")

    feat_cols = joblib.load("data/models/feature_cols.pkl")

    print(f"    Train : {Xf_train.shape}  →  {yf_train.shape}")
    print(f"    Val   : {Xf_val.shape}    →  {yf_val.shape}")
    print(f"    Test  : {Xf_test.shape}   →  {yf_test.shape}")
    print(f"    Features : {feat_cols}")

    return Xf_train, Xf_val, Xf_test, yf_train, yf_val, yf_test, feat_cols


# ══════════════════════════════════════════════════════════
# STEP 2 — Build and train model
# ══════════════════════════════════════════════════════════
def train_model(Xf_train, yf_train, Xf_val, yf_val):
    print("\n[2/5] Training XGBoost model...")
    print("    This takes about 1-2 minutes...")

    model = xgb.XGBRegressor(
        n_estimators      = 500,    # number of trees
        max_depth         = 6,      # depth of each tree
        learning_rate     = 0.05,   # step size — smaller = more careful
        subsample         = 0.8,    # use 80% of rows per tree (prevents overfitting)
        colsample_bytree  = 0.8,    # use 80% of features per tree
        min_child_weight  = 3,      # minimum samples in a leaf
        gamma             = 0.1,    # minimum loss reduction to split
        reg_alpha         = 0.1,    # L1 regularization
        reg_lambda        = 1.0,    # L2 regularization
        random_state      = 42,
        n_jobs            = -1,     # use all CPU cores
        early_stopping_rounds = 30  # stop if no improvement for 30 rounds
    )

    model.fit(
        Xf_train, yf_train,
        eval_set        = [(Xf_val, yf_val)],
        verbose         = 50   # print progress every 50 trees
    )

    best_round = model.best_iteration
    print(f"\n    Best iteration  : {best_round}")
    return model


# ══════════════════════════════════════════════════════════
# STEP 3 — Evaluate
# ══════════════════════════════════════════════════════════
def evaluate(model, Xf_test, yf_test, label="Test"):
    preds = model.predict(Xf_test)

    mae  = mean_absolute_error(yf_test, preds)
    rmse = np.sqrt(mean_squared_error(yf_test, preds))
    r2   = r2_score(yf_test, preds)

    # MAPE — only on rows where actual > 0 to avoid division by zero
    mask = yf_test > 10
    mape = np.mean(np.abs((yf_test[mask] - preds[mask]) / yf_test[mask])) * 100

    print(f"\n[3/5] Evaluation results ({label} set):")
    print(f"    MAE   : {mae:.2f}  W/m2  (avg absolute error)")
    print(f"    RMSE  : {rmse:.2f} W/m2  (penalises large errors more)")
    print(f"    MAPE  : {mape:.2f} %     (percentage error)")
    print(f"    R2    : {r2:.4f}         (1.0 = perfect, >0.90 = good)")

    # Performance rating
    if r2 > 0.92:
        rating = "Excellent - publication quality"
    elif r2 > 0.85:
        rating = "Good - strong result"
    elif r2 > 0.75:
        rating = "Acceptable - room to improve"
    else:
        rating = "Needs improvement"
    print(f"    Rating: {rating}")

    return {"mae": mae, "rmse": rmse, "mape": mape, "r2": r2}


# ══════════════════════════════════════════════════════════
# STEP 4 — Feature importance (great for research paper)
# ══════════════════════════════════════════════════════════
def show_feature_importance(model, feat_cols):
    print("\n[4/5] Feature importance (top 10):")
    importances = model.feature_importances_
    pairs = sorted(zip(feat_cols, importances), key=lambda x: x[1], reverse=True)
    for i, (feat, score) in enumerate(pairs[:10]):
        bar = "█" * int(score * 200)
        print(f"    {i+1:2d}. {feat:<22} {score:.4f}  {bar}")
    return pairs


# ══════════════════════════════════════════════════════════
# STEP 5 — Save model + register in MySQL
# ══════════════════════════════════════════════════════════
def save_and_register(model, metrics, feat_cols):
    print("\n[5/5] Saving model...")
    os.makedirs("data/models", exist_ok=True)
    model_path = "data/models/xgboost_solar.pkl"
    joblib.dump(model, model_path)
    print(f"    Model saved : {model_path}")

    # Register in MySQL model_registry table
    try:
        conn = sqlite3.connect(str(Path(__file__).resolve().parents[1] / "solar_forecast_db.sqlite3")),
            user     = os.getenv("DB_USER", "root"),
            password = os.getenv("DB_PASSWORD", "Siyaram@#2024"),
            database = os.getenv("DB_NAME", "solar_forecast_db"),
            charset  = "utf8mb4"
        )
        with conn.cursor() as cur:
            hyperparams = json.dumps({
                "n_estimators": 500, "max_depth": 6,
                "learning_rate": 0.05, "subsample": 0.8
            })
            cur.execute("""
                INSERT INTO model_registry
                    (model_name, version, training_date, rmse, mae, r2_score, hyperparams, file_path, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                "XGBoost", "1.0", date.today(),
                round(metrics["rmse"], 4),
                round(metrics["mae"],  4),
                round(metrics["r2"],   4),
                hyperparams, model_path, True
            ))
        conn.commit()
        conn.close()
        print("    Registered  : model_registry table in MySQL")
    except Exception as e:
        print(f"    MySQL register skipped: {e}")


# ══════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 55)
    print("  Solar AI - Phase 3: XGBoost Training")
    print("=" * 55)

    Xf_train, Xf_val, Xf_test, yf_train, yf_val, yf_test, feat_cols = load_data()
    model   = train_model(Xf_train, yf_train, Xf_val, yf_val)
    metrics = evaluate(model, Xf_test, yf_test)
    show_feature_importance(model, feat_cols)
    save_and_register(model, metrics, feat_cols)

    print("\n" + "=" * 55)
    print("  Phase 3 COMPLETE!")
    print("=" * 55)
    print(f"  XGBoost R2 Score : {metrics['r2']:.4f}")
    print(f"  Model saved at   : data/models/xgboost_solar.pkl")
    print("\n  Next step: python src/train_lstm.py")