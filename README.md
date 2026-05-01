# AI-Powered Solar Energy Forecasting and Analytics Platform

Production-style Flask + MySQL + vanilla JS platform for live solar/weather analytics and AI-based irradiance forecasting.

## Folder Structure

- `api/app.py` - Flask API, live weather integration, model inference, auth, admin APIs, logging, uploads, retraining jobs.
- `dashboard/` - Static user and admin dashboards served by Flask.
- `data/models/` - Existing trained LSTM/XGBoost/scaler artifacts.
- `src/` - Data download, preprocessing, and model training scripts.
- `sql/001_platform_schema.sql` - MySQL schema migration reference.
- `sql/002_powerbi_views.sql` - Power BI reporting views.
- `powerbi/` - Power BI project file location.

## Setup

1. Create a virtual environment and install dependencies:

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

2. Copy `.env.example` to `.env` and fill in MySQL/admin values:

```powershell
Copy-Item .env.example .env
```

3. Make sure MySQL database `solar_forecast_db` exists, then run the app:

```powershell
python api/app.py
```

The backend creates missing platform tables and Power BI views at startup. Open:

- User dashboard: `http://localhost:5000`
- Forecasts: `http://localhost:5000/forecast.html`
- Location map: `http://localhost:5000/location.html`
- Historical analytics: `http://localhost:5000/history.html`
- PV system: `http://localhost:5000/pv-system.html`
- Power BI: `http://localhost:5000/powerbi.html`
- Admin dashboard: `http://localhost:5000/admin`

If you open the project with VS Code Live Server on port `5500`, the frontend now auto-detects the Flask API on ports `5000`, `5001`, or `5002`. The preferred final-year demo path is still the Flask URL because it serves the API and UI from one application.

If no admin user exists, the admin page exposes a one-time bootstrap form. After the first admin is created, bootstrap is disabled.

## Live APIs

Primary providers are no-key APIs:

- Open-Meteo Forecast API for live weather, hourly forecast, GHI, DNI, DHI, UV, sunrise, sunset.
- Open-Meteo Geocoding API for location search.
- OpenStreetMap Nominatim for reverse geocoding.
- NASA POWER for solar/environmental enrichment when available.

Optional keys can be added later through `.env` for OpenWeatherMap or WeatherAPI, but the current implementation does not require them.

## AI Inference

The backend reuses existing artifacts:

- `data/models/lstm_best.keras`
- `data/models/xgboost_solar.pkl`
- `data/models/scaler.pkl`
- `data/models/feature_cols.pkl`
- `data/models/y_max.pkl`

`/api/predict` accepts a location, fetches live hourly data, builds the 23 trained features including lag/rolling features from real provider data, runs XGBoost and LSTM, then stores the ensemble prediction in MySQL.

## Power BI

Power BI should connect directly to MySQL, not static CSV exports. See [docs/POWERBI.md](docs/POWERBI.md).

Use these views from `sql/002_powerbi_views.sql`:

- `vw_powerbi_forecast_analytics`
- `vw_powerbi_location_analytics`
- `vw_powerbi_efficiency_reports`
- `vw_powerbi_historical_reports`
- `vw_powerbi_api_health`
- `vw_powerbi_model_performance`

## Deployment Notes

- Use a real `SECRET_KEY`.
- Set `ADMIN_EMAIL` and `ADMIN_PASSWORD` before first startup or create the first admin through bootstrap.
- Run Flask behind a production WSGI server for deployment.
- Configure MySQL backups and Power BI scheduled refresh credentials.
- Keep model artifacts under `data/models/` and restrict upload access to admins.
