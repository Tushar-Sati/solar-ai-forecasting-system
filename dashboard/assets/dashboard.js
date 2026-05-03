const state = {
  location: null,
  weather: null,
  prediction: null,
  history: [],
  refreshTimer: null,
  refreshSeconds: 300,
  isLoading: false,
  charts: {},
  map: null,
  marker: null,
  circle: null,
};

const MIN_REFRESH_SECONDS = 300;
const MAX_REFRESH_SECONDS = 3600;

const DEFAULT_LOCATION = {
  name: "New Delhi",
  state: "Delhi",
  country: "India",
  lat: 28.6139,
  lon: 77.2090,
};

document.addEventListener("DOMContentLoaded", () => {
  Solar.initTheme();
  Solar.iconRefresh();
  bindDashboardEvents();
  bootstrapDashboard();
  window.addEventListener("resize", () => Object.values(state.charts).forEach((chart) => chart.resize()));
});

function bindDashboardEvents() {
  bindClick("location-apply", searchLocation);
  bindClick("geo-btn", useBrowserLocation);
  bindClick("refresh-btn", () => loadAll(true));
  bindSubmit("pv-form", savePVConfig);
}

function bindClick(id, handler) {
  const element = document.getElementById(id);
  if (element) element.addEventListener("click", handler);
}

function bindSubmit(id, handler) {
  const element = document.getElementById(id);
  if (element) element.addEventListener("submit", handler);
}

async function bootstrapDashboard() {
  setInitialLoadingState();
  await checkHealth();
  if (document.body.dataset.page === "powerbi") {
    await loadPowerBIViews();
    return;
  }
  await loadPVConfig();
  state.location = getSavedLocation();
  fillLocationInputs(state.location);
  await loadAll(true);
}

function getSavedLocation() {
  try {
    const saved = JSON.parse(localStorage.getItem("solar_location") || "null");
    if (saved && Number.isFinite(Number(saved.lat)) && Number.isFinite(Number(saved.lon))) {
      return normalizeLocation(saved);
    }
  } catch {
    // Ignore corrupted local storage.
  }
  return { ...DEFAULT_LOCATION };
}

function saveLocation(location) {
  state.location = normalizeLocation(location);
  localStorage.setItem("solar_location", JSON.stringify(state.location));
  fillLocationInputs(state.location);
}

function fillLocationInputs(location) {
  const country = document.getElementById("country-input");
  const stateInput = document.getElementById("state-input");
  const city = document.getElementById("city-input");
  if (country) country.value = location.country || "";
  if (stateInput) stateInput.value = location.state || "";
  if (city) city.value = location.name || "";
}

function setInitialLoadingState() {
  Solar.setText("api-status", "Connecting");
  Solar.setText("active-location", "Loading live solar forecast...");
  Solar.setText("kpi-ghi-note", "Fetching live irradiance");
  Solar.setText("kpi-power-note", "Waiting for model prediction");
  ["irradiance-chart", "temperature-chart", "daily-chart", "history-chart", "monthly-chart"].forEach((id) => {
    if (document.getElementById(id)) Solar.renderEmpty(id, "Loading live data...");
  });
  if (document.getElementById("solar-map")) Solar.renderEmpty("solar-map", "Loading map...");
}

async function checkHealth() {
  try {
    const data = await Solar.request("/health", { auth: false });
    state.refreshSeconds = normalizeRefreshSeconds(data.refresh_seconds || state.refreshSeconds);
    document.getElementById("api-dot")?.classList.add("ok");
    Solar.setText("api-status", data.models.xgboost && data.models.lstm ? "API online" : "API online, model issue");
  } catch (error) {
    document.getElementById("api-dot")?.classList.remove("ok");
    Solar.setText("api-status", "API offline");
    Solar.showToast(error.message, "error");
  }
}

function normalizeRefreshSeconds(value) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds)) return MIN_REFRESH_SECONDS;
  return Math.min(MAX_REFRESH_SECONDS, Math.max(MIN_REFRESH_SECONDS, Math.round(seconds)));
}

async function searchLocation() {
  const city = document.getElementById("city-input")?.value.trim() || "";
  const stateName = document.getElementById("state-input")?.value.trim() || "";
  const country = document.getElementById("country-input")?.value.trim() || "";
  const query = [city, stateName, country].filter(Boolean).join(", ");
  if (!query) {
    Solar.showToast("Enter a city, state, or country.", "error");
    return;
  }
  setLocationBusy(true);
  try {
    const data = await Solar.request(`/location/search?q=${encodeURIComponent(query)}`, { auth: false });
    const results = data.results || [];
    renderSearchResults(results);
    if (results.length === 1) {
      saveLocation(results[0]);
      clearSearchResults();
      await loadAll(true);
    }
  } catch (error) {
    Solar.showToast(error.message, "error");
  } finally {
    setLocationBusy(false);
  }
}

function renderSearchResults(results) {
  const wrap = document.getElementById("search-results");
  if (!wrap) return;
  wrap.innerHTML = "";
  if (!results.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "No matching live geocoding result.";
    wrap.appendChild(empty);
    return;
  }
  results.forEach((result) => {
    const button = document.createElement("button");
    button.className = "result-item";
    const title = document.createElement("strong");
    title.textContent = result.name || "Location";
    const detail = document.createElement("span");
    detail.textContent = [result.state, result.country, `${Solar.fmt(result.lat, 4)}, ${Solar.fmt(result.lon, 4)}`].filter(Boolean).join(" | ");
    button.append(title, detail);
    button.addEventListener("click", () => {
      saveLocation(result);
      clearSearchResults();
      loadAll(true);
    });
    wrap.appendChild(button);
  });
}

function clearSearchResults() {
  const wrap = document.getElementById("search-results");
  if (wrap) wrap.innerHTML = "";
}

function normalizeLocation(location) {
  return {
    name: location.name || "Selected location",
    state: location.state || "",
    country: location.country || "",
    lat: Number(location.lat),
    lon: Number(location.lon),
    timezone: location.timezone || null,
    elevation_m: location.elevation_m || null,
    site_name: location.site_name || [location.name, location.state, location.country].filter(Boolean).join(", "),
  };
}

function useBrowserLocation() {
  if (!navigator.geolocation) {
    Solar.showToast("Browser geolocation is unavailable.", "error");
    return;
  }
  setLocationBusy(true);
  navigator.geolocation.getCurrentPosition(
    async (position) => {
      const lat = position.coords.latitude;
      const lon = position.coords.longitude;
      try {
        const data = await Solar.request(`/location/reverse?lat=${lat}&lon=${lon}`, { auth: false });
        saveLocation(data.location);
      } catch {
        saveLocation({ name: "Current location", state: "", country: "", lat, lon });
      } finally {
        setLocationBusy(false);
        loadAll(true);
      }
    },
    (error) => {
      setLocationBusy(false);
      Solar.showToast(error.message, "error");
    },
    { enableHighAccuracy: true, timeout: 12000 }
  );
}

function setLocationBusy(isBusy) {
  const apply = document.getElementById("location-apply");
  const geo = document.getElementById("geo-btn");
  if (apply) apply.disabled = isBusy;
  if (geo) geo.disabled = isBusy;
}

async function loadAll(resetTimer = false) {
  if (!state.location) return;
  if (state.isLoading) return;
  state.isLoading = true;
  if (resetTimer) restartAutoRefresh();
  try {
    await checkHealth();
    await loadWeather();
    await Promise.all([loadPrediction(), loadHistory(), loadPredictionTable(), loadPowerBIViews()]);
  } finally {
    state.isLoading = false;
  }
}

function restartAutoRefresh() {
  if (state.refreshTimer) clearInterval(state.refreshTimer);
  const seconds = normalizeRefreshSeconds(state.refreshSeconds);
  state.refreshSeconds = seconds;
  state.refreshTimer = setInterval(() => loadAll(false), seconds * 1000);
  const minutes = Math.round(seconds / 60);
  Solar.setText("refresh-meta", `Auto-refresh every ${minutes} minute${minutes === 1 ? "" : "s"}`);
}

async function loadWeather() {
  const { lat, lon } = state.location;
  Solar.setText("active-location", "Loading live weather and solar data...");
  try {
    const data = await Solar.request(`/weather?lat=${lat}&lon=${lon}`, { auth: false });
    state.weather = data.data;
    saveLocation(state.weather.location);
    updateWeatherCards();
    renderWeatherCharts();
    renderDailySummary();
    renderMap();
    Solar.setText("active-location", state.weather.location.site_name || state.location.site_name);
  } catch (error) {
    Solar.showToast(error.message, "error");
    Solar.setText("active-location", "Live weather failed.");
    ["irradiance-chart", "temperature-chart", "daily-chart"].forEach((id) => {
      if (document.getElementById(id)) Solar.renderEmpty(id, "Live weather failed. Check Flask API and internet connection.");
    });
  }
}

function updateWeatherCards() {
  if (!state.weather) return;
  const current = state.weather.current;
  Solar.setText("kpi-ghi", Solar.fmt(current.ghi, 1));
  Solar.setText("kpi-temp", Solar.fmt(current.temperature_c, 1));
  Solar.setText("kpi-humidity", Solar.fmt(current.humidity_pct, 1));
  Solar.setText("kpi-wind", Solar.fmt(current.wind_speed_ms, 1));
  Solar.setText("kpi-dew", Solar.fmt(current.dew_point_c, 1));
  Solar.setText("kpi-cloud", Solar.fmt(current.cloud_cover_pct, 1));
  Solar.setText("kpi-pressure", Solar.fmt(current.pressure_hpa, 1));
  Solar.setText("kpi-uv", Solar.fmt(current.uv_index, 1));
  Solar.setText("kpi-dni", Solar.fmt(current.dni, 1));
  Solar.setText("kpi-dhi", Solar.fmt(current.dhi, 1));
  Solar.setText("kpi-weather", current.description || "--");
  Solar.setText("kpi-ghi-note", `Updated ${Solar.dateTime(state.weather.fetched_at)}`);
  const peak = todayPeak();
  Solar.setText("kpi-peak", Number.isFinite(peak) ? Solar.fmt(peak, 1) : "--");
  const sunrise = state.weather.daily?.[0]?.sunrise;
  const sunset = state.weather.daily?.[0]?.sunset;
  Solar.setText("sunrise-value", shortTime(sunrise));
  Solar.setText("sunset-value", shortTime(sunset));
}

function todayPeak() {
  const current = state.weather?.current || {};
  const day = String(current.time || "").slice(0, 10);
  const today = (state.weather?.hourly || []).filter((row) => String(row.time).startsWith(day));
  const values = today.map((row) => Number(row.ghi)).filter((value) => Number.isFinite(value));
  return values.length ? Math.max(...values) : NaN;
}

function shortTime(value) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(11, 16);
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

async function loadPrediction() {
  if (!state.location) return;
  const needsPrediction =
    document.getElementById("pred-ensemble") ||
    document.getElementById("pred-xgb") ||
    document.getElementById("kpi-power") ||
    document.getElementById("kpi-co2") ||
    document.getElementById("kpi-eff");
  if (!needsPrediction) return;
  if (!state.prediction) resetPredictionUi();
  try {
    const data = await Solar.request("/predict", {
      method: "POST",
      auth: false,
      body: { lat: state.location.lat, lon: state.location.lon },
    });
    state.prediction = data;
    const pred = data.predictions || {};
    const power = data.power || {};
    Solar.setText("pred-xgb", pred.xgboost === undefined ? "--" : `${Solar.fmt(pred.xgboost, 1)} W/m2`);
    Solar.setText("pred-lstm", pred.lstm === undefined ? "--" : `${Solar.fmt(pred.lstm, 1)} W/m2`);
    Solar.setText("pred-ensemble", pred.ensemble === undefined ? "--" : `${Solar.fmt(pred.ensemble, 1)} W/m2`);
    Solar.setText("pred-confidence", pred.confidence_score === null || pred.confidence_score === undefined ? "--" : Solar.pct(pred.confidence_score * 100, 1));
    const ensembleMetrics = data.metrics?.ensemble || data.metrics?.xgboost || data.metrics?.lstm || {};
    Solar.setText("pred-mae", ensembleMetrics.mae === undefined ? "--" : Solar.fmt(ensembleMetrics.mae, 2));
    Solar.setText("pred-rmse", ensembleMetrics.rmse === undefined ? "--" : Solar.fmt(ensembleMetrics.rmse, 2));
    Solar.setText("pred-r2", ensembleMetrics.r2_score === undefined ? "--" : Solar.fmt(ensembleMetrics.r2_score, 4));
    Solar.setText("kpi-power", power.estimated_power_kw === null || power.estimated_power_kw === undefined ? "--" : Solar.fmt(power.estimated_power_kw, 3));
    Solar.setText("kpi-co2", power.co2_offset_kg === null || power.co2_offset_kg === undefined ? "--" : Solar.fmt(power.co2_offset_kg, 3));
    Solar.setText("kpi-eff", power.efficiency_pct === null || power.efficiency_pct === undefined ? "--" : Solar.fmt(power.efficiency_pct, 1));
    Solar.setText("kpi-power-note", data.warning || (power.configured ? "Calculated from saved PV settings" : power.message || "PV settings required"));
  } catch (error) {
    Solar.showToast(error.message, "error");
    Solar.setText("kpi-power-note", "Prediction unavailable");
  }
}

function resetPredictionUi() {
  ["pred-xgb", "pred-lstm", "pred-ensemble", "pred-confidence", "pred-mae", "pred-rmse", "pred-r2"].forEach((id) => Solar.setText(id, "--"));
}

function chart(id) {
  const element = document.getElementById(id);
  if (!element || !window.echarts) return null;
  if (!state.charts[id]) {
    element.innerHTML = "";
    state.charts[id] = echarts.init(element);
  }
  return state.charts[id];
}

function axisTheme() {
  const text = getComputedStyle(document.documentElement).getPropertyValue("--muted").trim();
  const line = getComputedStyle(document.documentElement).getPropertyValue("--line").trim();
  return { text, line };
}

function renderWeatherCharts() {
  if (!state.weather) return;
  const hourly = state.weather.hourly || [];
  const theme = axisTheme();
  const irradianceChart = chart("irradiance-chart");
  const temperatureChart = chart("temperature-chart");
  if (!hourly.length) {
    ["irradiance-chart", "temperature-chart"].forEach((id) => {
      if (document.getElementById(id)) Solar.renderEmpty(id, "No live chart data returned.");
    });
    return;
  }
  const labels = hourly.map((row) => String(row.time).slice(5, 16).replace("T", " "));
  if (irradianceChart) {
    irradianceChart.setOption({
      tooltip: { trigger: "axis" },
      legend: { textStyle: { color: theme.text } },
      grid: { left: 52, right: 18, top: 42, bottom: 42 },
      xAxis: { type: "category", data: labels, axisLabel: { color: theme.text }, axisLine: { lineStyle: { color: theme.line } } },
      yAxis: { type: "value", name: "W/m2", axisLabel: { color: theme.text }, splitLine: { lineStyle: { color: theme.line } } },
      series: [
        { name: "GHI", type: "line", smooth: true, data: hourly.map((row) => row.ghi), color: "#f6b73c", showSymbol: false },
        { name: "DNI", type: "line", smooth: true, data: hourly.map((row) => row.dni), color: "#3cc7d9", showSymbol: false },
        { name: "DHI", type: "line", smooth: true, data: hourly.map((row) => row.dhi), color: "#57c785", showSymbol: false },
      ],
    });
  }
  if (temperatureChart) {
    temperatureChart.setOption({
      tooltip: { trigger: "axis" },
      legend: { textStyle: { color: theme.text } },
      grid: { left: 44, right: 42, top: 42, bottom: 42 },
      xAxis: { type: "category", data: labels, axisLabel: { color: theme.text }, axisLine: { lineStyle: { color: theme.line } } },
      yAxis: [
        { type: "value", name: "C", axisLabel: { color: theme.text }, splitLine: { lineStyle: { color: theme.line } } },
        { type: "value", name: "%", axisLabel: { color: theme.text }, splitLine: { show: false } },
      ],
      series: [
        { name: "Temperature", type: "line", smooth: true, data: hourly.map((row) => row.temperature_c), color: "#ef6461", showSymbol: false },
        { name: "Humidity", type: "line", yAxisIndex: 1, smooth: true, data: hourly.map((row) => row.humidity_pct), color: "#6a8dff", showSymbol: false },
      ],
    });
  }
  renderDailyChart();
}

function renderDailyChart() {
  const dailyChart = chart("daily-chart");
  if (!dailyChart || !state.weather) return;
  const daily = state.weather.daily || [];
  const theme = axisTheme();
  dailyChart.setOption({
    tooltip: { trigger: "axis" },
    legend: { textStyle: { color: theme.text } },
    grid: { left: 48, right: 44, top: 42, bottom: 38 },
    xAxis: { type: "category", data: daily.map((row) => row.date), axisLabel: { color: theme.text }, axisLine: { lineStyle: { color: theme.line } } },
    yAxis: [
      { type: "value", name: "MJ/m2", axisLabel: { color: theme.text }, splitLine: { lineStyle: { color: theme.line } } },
      { type: "value", name: "C", axisLabel: { color: theme.text }, splitLine: { show: false } },
    ],
    series: [
      { name: "Radiation Sum", type: "bar", data: daily.map((row) => row.ghi_sum), color: "#f6b73c" },
      { name: "Max Temp", type: "line", yAxisIndex: 1, data: daily.map((row) => row.temp_max_c), color: "#ef6461" },
      { name: "Min Temp", type: "line", yAxisIndex: 1, data: daily.map((row) => row.temp_min_c), color: "#3cc7d9" },
    ],
  });
}

function renderDailySummary() {
  const wrap = document.getElementById("daily-summary");
  if (!wrap || !state.weather) return;
  const rows = state.weather.daily || [];
  wrap.innerHTML = rows
    .slice(0, 7)
    .map(
      (day) => `
        <div class="summary-item">
          <strong>${day.date}</strong>
          <span>${Solar.fmt(day.ghi_sum, 1)} MJ/m2</span>
          <span>${Solar.fmt(day.temp_min_c, 1)}-${Solar.fmt(day.temp_max_c, 1)} C</span>
        </div>
      `
    )
    .join("");
}

async function loadHistory() {
  if (!state.weather?.location_id && !document.getElementById("history-chart") && !document.getElementById("monthly-chart")) return;
  try {
    const locationPart = state.weather?.location_id ? `location_id=${state.weather.location_id}&` : "";
    const data = await Solar.request(`/history?${locationPart}days=30`, { auth: false });
    state.history = data.data || [];
    renderHistoryCharts(state.history);
  } catch (error) {
    Solar.showToast(error.message, "error");
    if (document.getElementById("history-chart")) Solar.renderEmpty("history-chart", "Historical data unavailable.");
    if (document.getElementById("monthly-chart")) Solar.renderEmpty("monthly-chart", "Monthly data unavailable.");
  }
}

function renderHistoryCharts(rows) {
  const historyChart = chart("history-chart");
  const monthlyChart = chart("monthly-chart");
  if (!rows.length) {
    if (document.getElementById("history-chart")) Solar.renderEmpty("history-chart", "No stored readings for this location yet.");
    if (document.getElementById("monthly-chart")) Solar.renderEmpty("monthly-chart", "No monthly aggregates yet.");
    return;
  }
  const theme = axisTheme();
  if (historyChart) {
    historyChart.setOption({
      tooltip: { trigger: "axis" },
      legend: { textStyle: { color: theme.text } },
      grid: { left: 48, right: 18, top: 42, bottom: 42 },
      xAxis: { type: "category", data: rows.map((row) => row.ts), axisLabel: { color: theme.text }, axisLine: { lineStyle: { color: theme.line } } },
      yAxis: { type: "value", name: "W/m2", axisLabel: { color: theme.text }, splitLine: { lineStyle: { color: theme.line } } },
      series: [
        { name: "Stored GHI", type: "line", smooth: true, data: rows.map((row) => row.ghi), color: "#f6b73c", showSymbol: false },
        { name: "Temperature", type: "line", smooth: true, data: rows.map((row) => row.temperature_c), color: "#ef6461", showSymbol: false },
      ],
    });
  }
  if (monthlyChart) {
    const buckets = {};
    rows.forEach((row) => {
      const key = String(row.ts).slice(0, 7);
      if (!buckets[key]) buckets[key] = { ghi: [], temp: [] };
      if (row.ghi !== null) buckets[key].ghi.push(Number(row.ghi));
      if (row.temperature_c !== null) buckets[key].temp.push(Number(row.temperature_c));
    });
    const labels = Object.keys(buckets);
    monthlyChart.setOption({
      tooltip: { trigger: "axis" },
      legend: { textStyle: { color: theme.text } },
      grid: { left: 48, right: 18, top: 42, bottom: 38 },
      xAxis: { type: "category", data: labels, axisLabel: { color: theme.text }, axisLine: { lineStyle: { color: theme.line } } },
      yAxis: { type: "value", axisLabel: { color: theme.text }, splitLine: { lineStyle: { color: theme.line } } },
      series: [
        { name: "Avg GHI", type: "bar", data: labels.map((key) => average(buckets[key].ghi)), color: "#57c785" },
        { name: "Avg Temp", type: "line", data: labels.map((key) => average(buckets[key].temp)), color: "#ef6461" },
      ],
    });
  }
}

function average(values) {
  if (!values.length) return null;
  return Number((values.reduce((sum, value) => sum + value, 0) / values.length).toFixed(2));
}

function renderMap() {
  const mapElement = document.getElementById("solar-map");
  if (!mapElement || !window.L || !state.weather) return;
  const lat = state.location.lat;
  const lon = state.location.lon;
  const ghi = Number(state.weather.current.ghi || 0);
  if (!state.map) {
    mapElement.innerHTML = "";
    state.map = L.map("solar-map", { zoomControl: true }).setView([lat, lon], 10);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: "&copy; OpenStreetMap contributors",
    }).addTo(state.map);
  }
  state.map.setView([lat, lon], 10);
  if (state.marker) state.marker.remove();
  if (state.circle) state.circle.remove();
  const popup = [
    state.weather.location.site_name || state.location.name,
    `GHI: ${Solar.fmt(ghi, 1)} W/m2`,
    `Temp: ${Solar.fmt(state.weather.current.temperature_c, 1)} C`,
    `Cloud: ${Solar.fmt(state.weather.current.cloud_cover_pct, 1)}%`,
  ].join("<br>");
  state.marker = L.marker([lat, lon]).addTo(state.map).bindPopup(popup).openPopup();
  state.circle = L.circle([lat, lon], {
    radius: Math.max(1200, Math.min(18000, ghi * 18)),
    color: "#f6b73c",
    fillColor: "#f6b73c",
    fillOpacity: 0.18,
  }).addTo(state.map);
  setTimeout(() => state.map.invalidateSize(), 60);
}

async function loadPredictionTable() {
  if (!document.getElementById("predictions-table")) return;
  try {
    const data = await Solar.request("/predictions", { auth: false });
    Solar.table(
      "predictions-table",
      [
        { label: "Time", render: (row) => Solar.dateTime(row.prediction_time) },
        { label: "Location", key: "site_name" },
        { label: "XGBoost", render: (row) => `${Solar.fmt(row.xgboost_ghi, 1)} W/m2` },
        { label: "LSTM", render: (row) => `${Solar.fmt(row.lstm_ghi, 1)} W/m2` },
        { label: "Ensemble", render: (row) => `${Solar.fmt(row.ensemble_ghi || row.predicted_ghi, 1)} W/m2` },
        { label: "Power", render: (row) => `${Solar.fmt(row.predicted_power, 3)} kW` },
      ],
      data.data || []
    );
  } catch (error) {
    Solar.showToast(error.message, "error");
  }
}

async function loadPowerBIViews() {
  if (!document.getElementById("powerbi-table")) return;
  try {
    const data = await Solar.request("/powerbi/views", { auth: false });
    Solar.table(
      "powerbi-table",
      [
        { label: "MySQL View", key: "view_name" },
        { label: "Report Page", render: (row) => powerbiUsage(row.view_name) },
      ],
      data.views || []
    );
  } catch (error) {
    Solar.showToast(error.message, "error");
  }
}

function powerbiUsage(viewName) {
  const usage = {
    vw_powerbi_forecast_analytics: "Forecast analytics",
    vw_powerbi_location_analytics: "Location analytics",
    vw_powerbi_efficiency_reports: "Efficiency reports",
    vw_powerbi_historical_reports: "Historical reports",
    vw_powerbi_api_health: "API monitoring",
    vw_powerbi_model_performance: "Model performance",
  };
  return usage[viewName] || "Reporting dataset";
}

async function loadPVConfig() {
  try {
    const data = await Solar.request("/pv-config", { auth: false });
    if (!data.configured || !data.config) return;
    const config = data.config;
    setValue("pv-name", config.system_name || "");
    setValue("pv-capacity", config.capacity_kw ?? "");
    setValue("pv-area", config.panel_area_m2 ?? "");
    setValue("pv-eff", config.panel_efficiency_pct ?? "");
    setValue("pv-loss", config.loss_pct ?? "");
    setValue("pv-inverter", config.inverter_efficiency_pct ?? "");
    setValue("pv-tilt", config.tilt_deg ?? "");
    setValue("pv-azimuth", config.azimuth_deg ?? "");
  } catch (error) {
    Solar.showToast(error.message, "error");
  }
}

function setValue(id, value) {
  const element = document.getElementById(id);
  if (element) element.value = value;
}

async function savePVConfig(event) {
  event.preventDefault();
  const body = {
    system_name: value("pv-name"),
    capacity_kw: value("pv-capacity"),
    panel_area_m2: value("pv-area"),
    panel_efficiency_pct: value("pv-eff"),
    loss_pct: value("pv-loss"),
    inverter_efficiency_pct: value("pv-inverter"),
    tilt_deg: value("pv-tilt"),
    azimuth_deg: value("pv-azimuth"),
    location_id: state.weather?.location_id || null,
  };
  try {
    await Solar.request("/pv-config", { method: "POST", auth: false, body });
    Solar.showToast("PV settings saved.");
    await loadPrediction();
  } catch (error) {
    Solar.showToast(error.message, "error");
  }
}

function value(id) {
  return document.getElementById(id)?.value || "";
}
