window.Solar = (() => {
  const tokenKey = "solar_admin_token";
  let apiBasePromise = null;

  function token() {
    return localStorage.getItem(tokenKey);
  }

  function setToken(value) {
    if (value) localStorage.setItem(tokenKey, value);
    else localStorage.removeItem(tokenKey);
  }

  function candidateApiBases() {
    const origin = window.location.origin;
    const stored = localStorage.getItem("solar_api_base");
    const candidates = [
      stored,
      `${origin}/api`,
      "http://127.0.0.1:5000/api",
      "http://127.0.0.1:5001/api",
      "http://127.0.0.1:5002/api",
      "http://localhost:5000/api",
      "http://localhost:5001/api",
      "http://localhost:5002/api",
    ].filter(Boolean);
    return [...new Set(candidates)];
  }

  async function resolveApiBase() {
    if (!apiBasePromise) {
      apiBasePromise = (async () => {
        const candidates = candidateApiBases();
        for (const base of candidates) {
          try {
            const response = await fetch(`${base}/health`, { cache: "no-store" });
            const data = await response.json();
            if (response.ok && data.status === "running" && data.models) {
              localStorage.setItem("solar_api_base", base);
              return base;
            }
          } catch {
            // Try the next likely Flask port.
          }
        }
        throw new Error("Flask API not reachable. Start it with: python run_server.py");
      })();
    }
    return apiBasePromise;
  }

  async function request(path, options = {}) {
    const apiBase = await resolveApiBase();
    const headers = options.headers ? { ...options.headers } : {};
    const authToken = token();
    if (authToken && options.auth !== false) headers.Authorization = `Bearer ${authToken}`;
    let body = options.body;
    if (body && !(body instanceof FormData)) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(body);
    }
    const response = await fetch(`${apiBase}${path}`, {
      method: options.method || "GET",
      headers,
      body,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.status === "error") {
      throw new Error(data.error || `Request failed: ${response.status}`);
    }
    return data;
  }

  function fmt(value, digits = 1) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
    return Number(value).toLocaleString(undefined, {
      maximumFractionDigits: digits,
      minimumFractionDigits: 0,
    });
  }

  function pct(value, digits = 1) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
    return `${fmt(value, digits)}%`;
  }

  function dateTime(value) {
    if (!value) return "--";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  }

  function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
  }

  function showToast(message, type = "info") {
    const node = document.createElement("div");
    node.className = `toast ${type}`;
    node.textContent = message;
    document.body.appendChild(node);
    setTimeout(() => node.remove(), 4200);
  }

  function setTheme(theme) {
    const isLight = theme === "light";
    document.documentElement.classList.toggle("light", isLight);
    localStorage.setItem("solar_theme", isLight ? "light" : "dark");
  }

  function initTheme() {
    setTheme(localStorage.getItem("solar_theme") || "dark");
    document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
      button.addEventListener("click", () => {
        setTheme(document.documentElement.classList.contains("light") ? "dark" : "light");
      });
    });
  }

  function iconRefresh() {
    if (window.lucide) window.lucide.createIcons();
  }

  function renderEmpty(id, message) {
    const el = document.getElementById(id);
    if (el) {
      el.innerHTML = "";
      const box = document.createElement("div");
      box.className = "empty";
      box.textContent = message;
      el.appendChild(box);
    }
  }

  function table(containerId, columns, rows) {
    const wrap = document.getElementById(containerId);
    if (!wrap) return;
    wrap.innerHTML = "";
    if (!rows || rows.length === 0) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = "No records found.";
      wrap.appendChild(empty);
      return;
    }
    const outer = document.createElement("div");
    outer.className = "table-wrap";
    const tbl = document.createElement("table");
    const thead = document.createElement("thead");
    const headRow = document.createElement("tr");
    columns.forEach((col) => {
      const th = document.createElement("th");
      th.textContent = col.label;
      headRow.appendChild(th);
    });
    thead.appendChild(headRow);
    const tbody = document.createElement("tbody");
    rows.forEach((row) => {
      const tr = document.createElement("tr");
      columns.forEach((col) => {
        const td = document.createElement("td");
        const value = typeof col.render === "function" ? col.render(row) : row[col.key];
        if (value instanceof Node) td.appendChild(value);
        else td.textContent = value ?? "--";
        tr.appendChild(td);
      });
      tbody.appendChild(tr);
    });
    tbl.append(thead, tbody);
    outer.appendChild(tbl);
    wrap.appendChild(outer);
  }

  return {
    request,
    resolveApiBase,
    fmt,
    pct,
    dateTime,
    setText,
    showToast,
    setTheme,
    initTheme,
    iconRefresh,
    renderEmpty,
    table,
    token,
    setToken,
  };
})();
