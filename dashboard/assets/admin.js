const adminState = {
  refreshTimer: null,
};

document.addEventListener("DOMContentLoaded", () => {
  Solar.initTheme();
  Solar.iconRefresh();
  bindAdminEvents();
  checkSetup();
  resumeSession();
});

function bindAdminEvents() {
  document.getElementById("login-form").addEventListener("submit", login);
  document.getElementById("bootstrap-form").addEventListener("submit", bootstrapAdmin);
  document.getElementById("logout-btn").addEventListener("click", logout);
  document.getElementById("admin-refresh").addEventListener("click", loadAdminData);
  document.getElementById("user-form").addEventListener("submit", createUser);
  document.getElementById("upload-form").addEventListener("submit", uploadDataset);
  document.getElementById("retrain-btn").addEventListener("click", triggerRetraining);
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.addEventListener("click", () => showView(button.dataset.view));
  });
}

async function checkSetup() {
  try {
    const data = await Solar.request("/auth/setup-status", { auth: false });
    document.getElementById("bootstrap-form").classList.toggle("hidden", !data.setup_required);
  } catch (error) {
    Solar.showToast(error.message, "error");
  }
}

async function resumeSession() {
  if (!Solar.token()) return showLogin();
  try {
    const data = await Solar.request("/auth/me");
    showAdmin(data.user);
  } catch {
    Solar.setToken(null);
    showLogin();
  }
}

async function login(event) {
  event.preventDefault();
  try {
    const data = await Solar.request("/auth/login", {
      method: "POST",
      auth: false,
      body: {
        email: document.getElementById("login-email").value.trim(),
        password: document.getElementById("login-password").value,
      },
    });
    Solar.setToken(data.token);
    showAdmin(data.user);
  } catch (error) {
    Solar.showToast(error.message, "error");
  }
}

async function bootstrapAdmin(event) {
  event.preventDefault();
  try {
    await Solar.request("/auth/bootstrap", {
      method: "POST",
      auth: false,
      body: {
        full_name: document.getElementById("bootstrap-name").value.trim(),
        email: document.getElementById("bootstrap-email").value.trim(),
        password: document.getElementById("bootstrap-password").value,
      },
    });
    Solar.showToast("First admin created. Login with that account.");
    document.getElementById("bootstrap-form").classList.add("hidden");
  } catch (error) {
    Solar.showToast(error.message, "error");
  }
}

async function logout() {
  try {
    await Solar.request("/auth/logout", { method: "POST" });
  } catch {
    // The local token is cleared either way.
  }
  Solar.setToken(null);
  if (adminState.refreshTimer) clearInterval(adminState.refreshTimer);
  showLogin();
}

function showLogin() {
  document.getElementById("login-shell").classList.remove("hidden");
  document.getElementById("admin-shell").classList.add("hidden");
}

function showAdmin(user) {
  document.getElementById("login-shell").classList.add("hidden");
  document.getElementById("admin-shell").classList.remove("hidden");
  Solar.setText("admin-user", `${user.full_name} | ${user.role}`);
  loadAdminData();
  if (adminState.refreshTimer) clearInterval(adminState.refreshTimer);
  adminState.refreshTimer = setInterval(loadAdminData, 60000);
}

function showView(view) {
  document.querySelectorAll("[data-view]").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
  document.querySelectorAll(".admin-view").forEach((section) => section.classList.add("hidden"));
  document.getElementById(`view-${view}`).classList.remove("hidden");
}

async function loadAdminData() {
  await Promise.allSettled([
    loadAnalytics(),
    loadUsers(),
    loadModels(),
    loadPredictions(),
    loadUploads(),
    loadJobs(),
    loadLogs(),
    loadPowerBIViews(),
  ]);
  Solar.iconRefresh();
}

async function loadAnalytics() {
  const data = await Solar.request("/admin/analytics");
  Solar.setText("admin-locations", Solar.fmt(data.database.locations?.count, 0));
  Solar.setText("admin-readings", Solar.fmt(data.database.readings?.count, 0));
  Solar.setText("admin-predictions", Solar.fmt(data.database.predictions?.count, 0));
  Solar.setText("admin-api-logs", Solar.fmt(data.database.api_logs?.count, 0));
  Solar.table(
    "api-health-table",
    [
      { label: "Provider", key: "provider" },
      { label: "Total", render: (row) => Solar.fmt(row.total, 0) },
      { label: "OK", render: (row) => Solar.fmt(row.ok, 0) },
      { label: "Failed", render: (row) => Solar.fmt(row.failed, 0) },
      { label: "Avg Latency", render: (row) => `${Solar.fmt(row.avg_latency_ms, 0)} ms` },
    ],
    data.api_health || []
  );
}

async function loadUsers() {
  const data = await Solar.request("/admin/users");
  Solar.table(
    "users-table",
    [
      { label: "Name", key: "full_name" },
      { label: "Email", key: "email" },
      { label: "Role", key: "role" },
      { label: "Active", render: (row) => (row.is_active ? "Yes" : "No") },
      { label: "Last Login", render: (row) => Solar.dateTime(row.last_login_at) },
      { label: "Created", render: (row) => Solar.dateTime(row.created_at) },
    ],
    data.data || []
  );
}

async function createUser(event) {
  event.preventDefault();
  try {
    await Solar.request("/admin/users", {
      method: "POST",
      body: {
        full_name: document.getElementById("user-name").value.trim(),
        email: document.getElementById("user-email").value.trim(),
        password: document.getElementById("user-password").value,
        role: document.getElementById("user-role").value,
      },
    });
    event.target.reset();
    Solar.showToast("User created.");
    loadUsers();
  } catch (error) {
    Solar.showToast(error.message, "error");
  }
}

async function loadModels() {
  const data = await Solar.request("/admin/models");
  const statusWrap = document.getElementById("model-status");
  statusWrap.innerHTML = "";
  Object.entries(data.loaded || {}).forEach(([key, value]) => {
    const badge = document.createElement("span");
    badge.className = `badge ${value ? "ok" : "error"}`;
    badge.textContent = `${key}: ${value}`;
    statusWrap.appendChild(badge);
  });
  Solar.table(
    "models-table",
    [
      { label: "Model", key: "model_name" },
      { label: "Version", key: "version" },
      { label: "Training Date", render: (row) => row.training_date || "--" },
      { label: "MAE", render: (row) => Solar.fmt(row.mae, 4) },
      { label: "RMSE", render: (row) => Solar.fmt(row.rmse, 4) },
      { label: "R2", render: (row) => Solar.fmt(row.r2_score, 4) },
      { label: "Active", render: (row) => (row.is_active ? "Yes" : "No") },
    ],
    data.data || []
  );
}

async function loadPredictions() {
  const data = await Solar.request("/predictions");
  Solar.table(
    "predictions-table",
    [
      { label: "Time", render: (row) => Solar.dateTime(row.prediction_time) },
      { label: "Location", key: "site_name" },
      { label: "XGBoost", render: (row) => `${Solar.fmt(row.xgboost_ghi, 1)} W/m2` },
      { label: "LSTM", render: (row) => `${Solar.fmt(row.lstm_ghi, 1)} W/m2` },
      { label: "Ensemble", render: (row) => `${Solar.fmt(row.ensemble_ghi || row.predicted_ghi, 1)} W/m2` },
      { label: "Power", render: (row) => `${Solar.fmt(row.predicted_power, 3)} kW` },
      { label: "Confidence", render: (row) => (row.confidence_score == null ? "--" : Solar.pct(row.confidence_score * 100, 1)) },
    ],
    data.data || []
  );
}

async function uploadDataset(event) {
  event.preventDefault();
  const file = document.getElementById("dataset-file").files[0];
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  try {
    await Solar.request("/admin/uploads", { method: "POST", body: form });
    event.target.reset();
    Solar.showToast("Dataset uploaded.");
    loadUploads();
  } catch (error) {
    Solar.showToast(error.message, "error");
  }
}

async function loadUploads() {
  const data = await Solar.request("/admin/uploads");
  Solar.table(
    "uploads-table",
    [
      { label: "File", key: "original_filename" },
      { label: "Rows", render: (row) => Solar.fmt(row.row_count, 0) },
      { label: "Status", key: "status" },
      { label: "Error", key: "error_message" },
      { label: "Created", render: (row) => Solar.dateTime(row.created_at) },
    ],
    data.data || []
  );
}

async function triggerRetraining() {
  const confirmed = confirm("Start model retraining in the background?");
  if (!confirmed) return;
  try {
    const data = await Solar.request("/admin/retrain", { method: "POST", body: {} });
    Solar.showToast(`Retraining job ${data.job_id} queued.`);
    loadJobs();
  } catch (error) {
    Solar.showToast(error.message, "error");
  }
}

async function loadJobs() {
  const data = await Solar.request("/admin/jobs");
  Solar.table(
    "jobs-table",
    [
      { label: "Job", key: "job_id" },
      { label: "Status", key: "status" },
      { label: "Started", render: (row) => Solar.dateTime(row.started_at) },
      { label: "Finished", render: (row) => Solar.dateTime(row.finished_at) },
      { label: "Log Path", key: "log_path" },
    ],
    data.data || []
  );
}

async function loadLogs() {
  const data = await Solar.request("/admin/logs");
  Solar.table(
    "api-logs-table",
    [
      { label: "Provider", key: "provider" },
      { label: "Status", key: "status_code" },
      { label: "Latency", render: (row) => `${Solar.fmt(row.latency_ms, 0)} ms` },
      { label: "OK", render: (row) => (row.success ? "Yes" : "No") },
      { label: "Error", key: "error_message" },
      { label: "Time", render: (row) => Solar.dateTime(row.created_at) },
    ],
    data.api || []
  );
  Solar.table(
    "system-logs-table",
    [
      { label: "Level", key: "level" },
      { label: "Source", key: "source" },
      { label: "Message", key: "message" },
      { label: "Time", render: (row) => Solar.dateTime(row.created_at) },
    ],
    data.system || []
  );
}

async function loadPowerBIViews() {
  const data = await Solar.request("/powerbi/views", { auth: false });
  Solar.table(
    "powerbi-table",
    [
      { label: "View", key: "view_name" },
      { label: "Usage", render: (row) => powerbiUsage(row.view_name) },
    ],
    data.views || []
  );
}

function powerbiUsage(viewName) {
  const usage = {
    vw_powerbi_forecast_analytics: "Forecast analytics and prediction accuracy reports",
    vw_powerbi_location_analytics: "Location performance and solar potential comparisons",
    vw_powerbi_efficiency_reports: "PV output and efficiency dashboards",
    vw_powerbi_historical_reports: "Historical weather, irradiance, and trend reports",
    vw_powerbi_api_health: "API reliability and latency monitoring",
    vw_powerbi_model_performance: "Model metrics, versions, and active registry status",
  };
  return usage[viewName] || "Power BI reporting dataset";
}

