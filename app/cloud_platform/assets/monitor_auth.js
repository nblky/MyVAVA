import { AUTH_STORAGE_KEY } from "./monitor_constants.js";

export function createMonitorAuthRuntime({
  appState,
  destroyLivePlayers,
  postJson,
  refreshData,
  syncAutoRefresh,
  syncLivePrewarmButton,
}) {
  function setLoginStatus(message) {
    const node = document.getElementById("login-status");
    if (node) {
      node.textContent = String(message || "");
    }
  }

  function setAuthState(auth) {
    appState.auth = auth && auth.token ? auth : null;
    if (!appState.auth) {
      destroyLivePlayers();
      appState.wallSignature = "";
      appState.wallModeSignatures = {};
      appState.liveStickyUntilByCamera = {};
      appState.livePrewarmEnabled = false;
      appState.refreshBusy = false;
      appState.refreshQueued = false;
      appState.refreshPromise = null;
      appState.playbackSelectedKey = "";
      appState.messageFilters = { cameraSn: "all", typeKey: "all", dateFrom: "", dateTo: "" };
      appState.playbackFilters = { cameraSn: "all", dateFrom: "", dateTo: "" };
      if (appState.timer) {
        clearTimeout(appState.timer);
        appState.timer = null;
      }
    }
    if (appState.auth) {
      window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(appState.auth));
    } else {
      window.localStorage.removeItem(AUTH_STORAGE_KEY);
    }
    const loginScreen = document.getElementById("login-screen");
    const app = document.getElementById("monitor-app");
    if (loginScreen) loginScreen.hidden = !!appState.auth;
    if (app) app.hidden = !appState.auth;
    syncLivePrewarmButton();
  }

  function loadSavedAuth() {
    try {
      const raw = window.localStorage.getItem(AUTH_STORAGE_KEY);
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      if (!parsed || !parsed.token) return null;
      return parsed;
    } catch (_) {
      return null;
    }
  }

  async function restoreAuthState() {
    const saved = loadSavedAuth();
    if (!saved || !saved.token) {
      setAuthState(null);
      return false;
    }
    try {
      appState.auth = saved;
      const profile = await postJson("/users/detail", {}, true);
      setAuthState({ ...saved, profile });
      return true;
    } catch (_) {
      setAuthState(null);
      return false;
    }
  }

  function bindAuthUi() {
    document.getElementById("logout-btn").addEventListener("click", async () => {
      try {
        await postJson("/oauth/logout", {}, true);
      } catch (_) {
      }
      appState.data = null;
      setAuthState(null);
      setLoginStatus("已退出。使用之前测试账号重新登录即可。");
    });

    document.getElementById("login-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const submit = document.getElementById("login-submit");
      const account = String(document.getElementById("login-account").value || "").trim();
      const password = String(document.getElementById("login-password").value || "");
      if (!account || !password) {
        setLoginStatus("请输入账号和密码。");
        return;
      }
      submit.disabled = true;
      submit.textContent = "登录中...";
      setLoginStatus("正在通过本地假云账号体系登录...");
      try {
        const login = await postJson("/oauth/login", {
          username: account,
          password,
        });
        const token = String(login.access_token || "").trim();
        if (!token) throw new Error("empty access token");
        appState.auth = { token, login };
        const profile = await postJson("/users/detail", {}, true);
        setAuthState({ token, profile, login });
        syncAutoRefresh();
        await refreshData();
      } catch (error) {
        setAuthState(null);
        setLoginStatus(`登录失败: ${error}`);
      } finally {
        submit.disabled = false;
        submit.textContent = "登录";
      }
    });
  }

  async function boot() {
    syncAutoRefresh();
    const restored = await restoreAuthState();
    if (restored) {
      await refreshData();
      return;
    }
    setLoginStatus("请先登录。默认测试账号已帮你填好。");
  }

  return {
    bindAuthUi,
    boot,
  };
}
