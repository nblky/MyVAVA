export function createMonitorApi({ appState }) {
  function authHeaders(extra = {}) {
    const headers = { ...extra };
    if (appState.auth && appState.auth.token) {
      headers.Authorization = `Bearer ${appState.auth.token}`;
    }
    return headers;
  }

  async function postJson(url, body, includeAuth = false) {
    const response = await fetch(url, {
      method: "POST",
      cache: "no-store",
      headers: includeAuth
        ? authHeaders({ "Content-Type": "application/json" })
        : { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    if (payload && typeof payload === "object" && "stateCode" in payload) {
      if (Number(payload.stateCode || 0) !== 200) {
        throw new Error(String(payload.stateMsg || "request failed"));
      }
      return payload.data || {};
    }
    return payload;
  }

  async function fetchMonitorData() {
    const response = await fetch("/monitor/data", {
      cache: "no-store",
      headers: authHeaders(),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  return {
    authHeaders,
    postJson,
    fetchMonitorData,
  };
}
