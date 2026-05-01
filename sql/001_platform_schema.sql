-- AI-Powered Solar Energy Forecasting and Analytics Platform
-- MySQL schema migration for app auth, PV configuration, logging, uploads,
-- retraining jobs, and richer prediction analytics.

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
);

CREATE TABLE IF NOT EXISTS user_sessions (
  session_id CHAR(64) PRIMARY KEY,
  user_id INT NOT NULL,
  expires_at DATETIME NOT NULL,
  revoked_at DATETIME NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_user_sessions_user
    FOREIGN KEY (user_id) REFERENCES users(user_id)
    ON DELETE CASCADE
);

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
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_pv_config_user
    FOREIGN KEY (user_id) REFERENCES users(user_id)
    ON DELETE SET NULL,
  CONSTRAINT fk_pv_config_location
    FOREIGN KEY (location_id) REFERENCES locations(location_id)
    ON DELETE SET NULL
);

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
);

CREATE TABLE IF NOT EXISTS system_logs (
  log_id BIGINT AUTO_INCREMENT PRIMARY KEY,
  level ENUM('info', 'warning', 'error') NOT NULL DEFAULT 'info',
  source VARCHAR(80) NOT NULL,
  message VARCHAR(500) NOT NULL,
  context JSON NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_system_logs_level_time (level, created_at)
);

CREATE TABLE IF NOT EXISTS dataset_uploads (
  upload_id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NULL,
  original_filename VARCHAR(255) NOT NULL,
  stored_path VARCHAR(500) NOT NULL,
  row_count INT NULL,
  status ENUM('uploaded', 'validated', 'failed') NOT NULL DEFAULT 'uploaded',
  error_message TEXT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_dataset_upload_user
    FOREIGN KEY (user_id) REFERENCES users(user_id)
    ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS retraining_jobs (
  job_id BIGINT AUTO_INCREMENT PRIMARY KEY,
  requested_by INT NULL,
  upload_id BIGINT NULL,
  status ENUM('queued', 'running', 'completed', 'failed') NOT NULL DEFAULT 'queued',
  command TEXT NULL,
  log_path VARCHAR(500) NULL,
  started_at DATETIME NULL,
  finished_at DATETIME NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_retraining_job_user
    FOREIGN KEY (requested_by) REFERENCES users(user_id)
    ON DELETE SET NULL,
  CONSTRAINT fk_retraining_job_upload
    FOREIGN KEY (upload_id) REFERENCES dataset_uploads(upload_id)
    ON DELETE SET NULL
);

-- Run these once on existing databases if the columns are not already present.
ALTER TABLE predictions
  ADD COLUMN xgboost_ghi FLOAT NULL,
  ADD COLUMN lstm_ghi FLOAT NULL,
  ADD COLUMN ensemble_ghi FLOAT NULL,
  ADD COLUMN confidence_score FLOAT NULL,
  ADD COLUMN input_snapshot JSON NULL,
  ADD COLUMN api_source VARCHAR(80) NULL,
  ADD COLUMN pv_config_id INT NULL;

ALTER TABLE solar_readings
  ADD COLUMN dew_point_c FLOAT NULL,
  ADD COLUMN uv_index FLOAT NULL,
  ADD COLUMN visibility_km FLOAT NULL,
  ADD COLUMN data_source VARCHAR(80) NULL;

