# Power BI Integration Plan

Power BI is the reporting layer over MySQL. It should use SQL views maintained by the backend migration so reports stay live and consistent with the Flask dashboard.

## Data Source

1. Open Power BI Desktop.
2. Select `Get data` -> `MySQL database`.
3. Server: your MySQL host, usually `localhost`.
4. Database: `solar_forecast_db`.
5. Choose Import mode for college demos or DirectQuery for near-live reports.
6. Select the `vw_powerbi_*` views.

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

- Import mode: configure scheduled refresh in Power BI Service with MySQL gateway credentials.
- DirectQuery: visuals query MySQL directly, so indexes on `solar_readings.timestamp`, `predictions.prediction_time`, and log timestamps matter.
- Keep Power BI credentials read-only. Create a MySQL user with `SELECT` access to the `vw_powerbi_*` views.

## SQL Files

Run or review:

```sql
source sql/002_powerbi_views.sql;
```

The Flask app also installs these views at startup when DB credentials are valid.

