export function createMonitorController({
  appState,
  attachHandlers,
  buildWallSignature,
  desiredRefreshIntervalMs,
  destroyLivePlayers,
  effectiveLiveLabel,
  ensureSelectedDevice,
  fetchMonitorData,
  releaseLiveSessions,
  renderDeviceTreeView,
  renderEventsView,
  renderHeaderView,
  renderHomeView,
  renderLiveDetailView,
  renderMessagesView,
  renderPlaybackView,
  renderStationView,
  renderWall,
  setStatus,
  syncExistingWallMeta,
  syncWallCardModes,
}) {
  function syncRefreshLoop() {
    if (appState.timer) {
      clearTimeout(appState.timer);
      appState.timer = null;
    }
    const intervalMs = desiredRefreshIntervalMs();
    if (!intervalMs) {
      return;
    }
    appState.timer = window.setTimeout(() => {
      refreshData({ silentStatus: true, statusText: "" });
    }, intervalMs);
  }

  function renderCurrentPage(data) {
    const tab = appState.selectedTab;
    if (tab === "live") {
      const wallSignature = buildWallSignature(data);
      const wallChanged = wallSignature !== appState.wallSignature;
      if (wallChanged) {
        destroyLivePlayers();
        renderWall(data);
        appState.wallSignature = wallSignature;
        appState.wallModeSignatures = {};
      }
      syncExistingWallMeta(data);
      syncWallCardModes(data, { forceAttach: wallChanged });
      renderLiveDetailView(data);
      attachHandlers();
      return;
    }

    if (tab !== "live" && Object.keys(appState.livePlayers || {}).length) {
      destroyLivePlayers();
    }
    appState.wallSignature = "";
    appState.wallModeSignatures = {};
    if (tab === "home") {
      renderHomeView(data);
    } else if (tab === "playback") {
      renderPlaybackView(data);
    } else if (tab === "messages") {
      renderMessagesView(data);
      renderEventsView(data);
    } else if (tab === "station") {
      renderStationView(data);
    } else {
      renderHomeView(data);
    }
    attachHandlers();
  }

  function render(data, options = {}) {
    const silentStatus = !!options.silentStatus;
    appState.data = data;
    ensureSelectedDevice(data);
    renderHeaderView(data);
    renderDeviceTreeView(data);
    renderCurrentPage(data);
    syncRefreshLoop();
    if (!silentStatus) {
      setStatus(
        `Last refresh: ${data.generatedAt} · cameras ${data.cameras.length} · live ${data.cameras.filter((item) => effectiveLiveLabel(item) === "ready").length} ready · clips ${data.summary.cloudMediaCount}`
      );
    }
  }

  async function refreshData(options = {}) {
    const silentStatus = !!options.silentStatus;
    const statusText = options.statusText === undefined ? "Refreshing monitor data..." : String(options.statusText || "");
    if (!appState.auth) return;
    if (appState.refreshBusy) {
      appState.refreshQueued = true;
      return appState.refreshPromise;
    }
    appState.refreshBusy = true;
    appState.refreshPromise = (async () => {
      if (!silentStatus && statusText) {
        setStatus(statusText);
      }
      try {
        const payload = await fetchMonitorData();
        render(payload, { silentStatus });
      } catch (error) {
        setStatus(`Refresh failed: ${error}`);
      } finally {
        appState.refreshBusy = false;
        appState.refreshPromise = null;
        if (appState.refreshQueued) {
          appState.refreshQueued = false;
          refreshData({ silentStatus: true, statusText: "" });
          return;
        }
        syncRefreshLoop();
      }
    })();
    return appState.refreshPromise;
  }

  function setTab(tab) {
    const nextTab = ["home", "live", "playback", "messages", "station"].includes(tab) ? tab : "live";
    const prevTab = appState.selectedTab;
    if (nextTab === prevTab) {
      if (appState.data) render(appState.data);
      return;
    }
    if (prevTab === "live" && nextTab !== "live") {
      destroyLivePlayers();
      appState.wallSignature = "";
      appState.wallModeSignatures = {};
      if (!appState.livePrewarmEnabled) {
        void releaseLiveSessions({
          includeKeepAlive: true,
          statusText: "离开 Live 页，回收后台 Live 链路...",
          doneStatus: "后台 Live 链路已回收。",
        });
      }
    }
    if (prevTab !== "live" && nextTab === "live") {
      appState.wallSignature = "";
      appState.wallModeSignatures = {};
    }
    if (nextTab === "station" && appState.data && appState.data.station && appState.data.station.stationSn) {
      appState.selectedDeviceSn = appState.data.station.stationSn;
    }
    appState.selectedTab = nextTab;
    if (appState.data) render(appState.data);
  }

  function syncAutoRefresh() {
    const button = document.getElementById("toggle-auto-btn");
    button.textContent = `自动刷新: ${appState.autoRefresh ? "开" : "关"}`;
    syncRefreshLoop();
  }

  return {
    refreshData,
    render,
    renderCurrentPage,
    setTab,
    syncAutoRefresh,
    syncRefreshLoop,
  };
}
