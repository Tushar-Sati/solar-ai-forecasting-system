-- Power BI direct-query/import views.
-- Connect Power BI Desktop to MySQL and select these views for live reports.

DROP VIEW IF EXISTS vw_powerbi_forecast_analytics;
CREATE VIEW vw_powerbi_forecast_analytics AS
SELECT
  p.prediction_id,
  p.prediction_time,
  p.forecast_time,
  l.site_name,
  l.latitude,
  l.longitude,
  p.model_name,
  COALESCE(p.ensemble_ghi, p.predicted_ghi) AS forecast_ghi,
  p.xgboost_ghi,
  p.lstm_ghi,
  p.predicted_power,
  p.actual_power,
  p.confidence_score,
  p.mae,
  DATE(p.prediction_time) AS prediction_date,
  CAST(strftime('%H', p.prediction_time) AS INTEGER) AS prediction_hour
FROM predictions p
LEFT JOIN locations l ON l.location_id = p.location_id;

DROP VIEW IF EXISTS vw_powerbi_location_analytics;
CREATE VIEW vw_powerbi_location_analytics AS
SELECT
  l.location_id,
  l.site_name,
  l.latitude,
  l.longitude,
  DATE(sr.timestamp) AS reading_date,
  COUNT(*) AS reading_count,
  ROUND(AVG(sr.ghi), 2) AS avg_ghi,
  ROUND(MAX(sr.ghi), 2) AS peak_ghi,
  ROUND(AVG(sr.temperature_c), 2) AS avg_temperature_c,
  ROUND(AVG(sr.humidity_pct), 2) AS avg_humidity_pct,
  ROUND(AVG(sr.cloud_cover_pct), 2) AS avg_cloud_cover_pct,
  ROUND(AVG(sr.wind_speed_ms), 2) AS avg_wind_speed_ms
FROM solar_readings sr
JOIN locations l ON l.location_id = sr.location_id
GROUP BY l.location_id, l.site_name, l.latitude, l.longitude, DATE(sr.timestamp);

DROP VIEW IF EXISTS vw_powerbi_efficiency_reports;
CREATE VIEW vw_powerbi_efficiency_reports AS
SELECT
  pv.output_id,
  pv.timestamp,
  l.site_name,
  pv.ac_power_kw,
  pv.dc_power_kw,
  pv.energy_kwh,
  pv.efficiency_pct,
  DATE(pv.timestamp) AS output_date,
  CAST(strftime('%H', pv.timestamp) AS INTEGER) AS output_hour
FROM pv_output pv
LEFT JOIN locations l ON l.location_id = pv.location_id;

DROP VIEW IF EXISTS vw_powerbi_historical_reports;
CREATE VIEW vw_powerbi_historical_reports AS
SELECT
  sr.reading_id,
  sr.timestamp,
  l.site_name,
  l.latitude,
  l.longitude,
  sr.ghi,
  sr.dni,
  sr.dhi,
  sr.temperature_c,
  sr.humidity_pct,
  sr.wind_speed_ms,
  sr.pressure_hpa,
  sr.cloud_cover_pct,
  sr.dew_point_c,
  sr.uv_index,
  DATE(sr.timestamp) AS reading_date,
  CAST(strftime('%H', sr.timestamp) AS INTEGER) AS reading_hour
FROM solar_readings sr
LEFT JOIN locations l ON l.location_id = sr.location_id;

DROP VIEW IF EXISTS vw_powerbi_api_health;
CREATE VIEW vw_powerbi_api_health AS
SELECT
  provider,
  DATE(created_at) AS log_date,
  COUNT(*) AS total_requests,
  SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS successful_requests,
  SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS failed_requests,
  ROUND(AVG(latency_ms), 0) AS avg_latency_ms,
  ROUND(MAX(latency_ms), 0) AS max_latency_ms
FROM api_request_logs
GROUP BY provider, DATE(created_at);

DROP VIEW IF EXISTS vw_powerbi_model_performance;
CREATE VIEW vw_powerbi_model_performance AS
SELECT
  model_id,
  model_name,
  version,
  training_date,
  rmse,
  mae,
  r2_score,
  is_active,
  file_path
FROM model_registry;

