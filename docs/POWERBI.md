# Power BI Integration Plan

Power BI is the reporting layer over the app's SQLite database. It should use the reporting views maintained by the backend startup migration so reports stay consistent with the Flask dashboard.

## Data Source

1. Open Power BI Desktop.
2. Use an SQLite connector/ODBC driver, or export from the app views if your Power BI install does not include SQLite support.
3. Database file: `solar_forecast_db.sqlite3` locally, or the path configured by `SQLITE_DB_PATH`.
4. Choose Import mode for college demos.
5. Select the `vw_powerbi_*` views.

## Reporting Views

- `vw_powerbi_forecast_analytics`
  - Forecast time, location, model predictions, ensemble GHI, power output, confidence, and prediction dates.
  - Use for forecast dashboards, model comparison, and forecast volume.

- `vw_powerbi_location_analytics`
  - Daily averages and peaks per location.
  - Use for location analytics, solar potential ranking, and environmental summaries.

- `vw_powerbi_efficiency_reports`
  - PV AC/DC output, energy, and efficiency.
  - Use for PV performance and efficiency reporting.

- `vw_powerbi_historical_reports`
  - Hourly GHI, DNI, DHI, temperature, humidity, wind, cloud, dew point, and UV.
  - Use for historical trend charts and monthly analytics.

- `vw_powerbi_api_health`
  - Provider request counts, success/failure totals, and latency.
  - Use for API monitoring and system reliability reports.

- `vw_powerbi_model_performance`
  - Model registry metrics: MAE, RMSE, R2, version, active flag, file path.
  - Use for model performance monitoring and research evaluation pages.

## Suggested Power BI Pages

1. Forecast Analytics
   - Ensemble GHI by hour/day.
   - Power output by location.
   - Confidence score trend.

2. Location Analytics
   - Average GHI and peak GHI by city/site.
   - Temperature, cloud cover, humidity comparisons.
   - Map visual using latitude and longitude.

3. Efficiency Reports
   - AC power, DC power, energy, efficiency percentage.
   - Daily and monthly PV performance.

4. Historical Reports
   - Hourly irradiance and weather trends.
   - Monthly GHI averages.
   - Seasonal analysis.

5. Operations
   - API health, failed requests, latency.
   - Model registry and active model status.

## Refresh

- Import mode: refresh from the SQLite file used by the deployed app.
- For Render persistence, attach a disk and set `SQLITE_DB_PATH` so Power BI always reads the same database file.
- Keep reporting access read-only where possible.

## SQL Files

Run or review:

```sql
.read sql/002_powerbi_views.sql
```

The Flask app also installs these views at startup when the SQLite database is writable.
