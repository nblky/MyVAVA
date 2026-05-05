export function createMonitorLiveControl({
  appState,
  activeLiveCameras,
  cameraBySn,
  cameraKeepAlive,
  clearLiveSticky,
  effectiveLiveLabel,
  postJson,
  refreshOptimisticLiveUi,
  refreshData,
  selectedQualityValue,
  setStatus,
  syncRefreshLoop,
  visibleWallCameras,
}) {
  async function startLive(cameraSn, stationSn, quality, statusText, options = {}) {
    const refreshAfter = options.refreshAfter !== false;
    const optimisticRefresh = options.optimisticRefresh !== false;
    const forceRestart = options.forceRestart === true;
    const keepAlive = options.keepAlive === undefined ? appState.livePrewarmEnabled : !!options.keepAlive;
    const requestedQuality = String(quality || "auto").trim().toLowerCase() || "auto";
    const existingCamera = cameraBySn(cameraSn);
    if (
      !forceRestart &&
      existingCamera &&
      (appState.liveRequestedByCamera[cameraSn] || effectiveLiveLabel(existingCamera) === "ready") &&
      selectedQualityValue(existingCamera) === requestedQuality &&
      cameraKeepAlive(existingCamera) === keepAlive &&
      !appState.selectedClipByCamera[cameraSn]
    ) {
      syncRefreshLoop();
      setStatus(`Live already active for ${cameraSn}.`);
      return;
    }
    appState.qualityDraftByCamera[cameraSn] = String(quality || "auto").trim().toLowerCase() || "auto";
    appState.liveRequestedByCamera[cameraSn] = true;
    appState.selectedClipByCamera[cameraSn] = "";
    setStatus(statusText || `Starting live for ${cameraSn}...`);
    if (optimisticRefresh && appState.data) {
      refreshOptimisticLiveUi();
    }
    await postJson("/monitor/live/control/start", {
      cameraSn,
      stationSn,
      quality: quality || "auto",
      keepAlive,
    }, true);
    if (refreshAfter) {
      await refreshData({ silentStatus: true, statusText: "" });
    } else {
      syncRefreshLoop();
    }
    const liveReady = !!(appState.data && appState.data.cameras || []).find(
      (item) => item.cameraSn === cameraSn && effectiveLiveLabel(item) === "ready"
    );
    setStatus(
      liveReady
        ? `Live ready for ${cameraSn}${keepAlive ? " · 预热保活已开启" : ""}.`
        : `P2P live requested for ${cameraSn}${keepAlive ? " · 已进入预热保活模式" : ""}. 后台继续等待 HLS 就绪...`
    );
  }

  async function stopLive(cameraSn, statusText, options = {}) {
    const refreshAfter = options.refreshAfter !== false;
    const optimisticRefresh = options.optimisticRefresh !== false;
    appState.liveRequestedByCamera[cameraSn] = false;
    clearLiveSticky(cameraSn);
    setStatus(statusText || `Stopping live for ${cameraSn}...`);
    if (optimisticRefresh && appState.data) {
      refreshOptimisticLiveUi();
    }
    await postJson("/monitor/live/control/stop", { cameraSn }, true);
    await new Promise((resolve) => setTimeout(resolve, 800));
    if (refreshAfter) {
      await refreshData({ silentStatus: true, statusText: "" });
    } else {
      syncRefreshLoop();
    }
  }

  async function releaseLiveSessions(options = {}) {
    const includeKeepAlive = options.includeKeepAlive === true;
    const onlyKeepAlive = options.onlyKeepAlive === true;
    const cameras = activeLiveCameras(appState.data, { includeKeepAlive, onlyKeepAlive });
    if (!cameras.length) {
      syncRefreshLoop();
      return;
    }
    if (options.statusText) {
      setStatus(String(options.statusText));
    }
    for (const camera of cameras) {
      try {
        await stopLive(
          camera.cameraSn,
          `Stopping live for ${camera.cameraName || camera.cameraSn}...`,
          { refreshAfter: false }
        );
      } catch (error) {
        setStatus(`Release live failed on ${camera.cameraName || camera.cameraSn}: ${error}`);
      }
    }
    await refreshData({ silentStatus: true, statusText: "" });
    if (options.doneStatus) {
      setStatus(String(options.doneStatus));
    }
  }

  async function startVisibleLives() {
    if (!appState.data) return;
    const cameras = visibleWallCameras(appState.data);
    setStatus(`Starting ${cameras.length} visible camera(s)...`);
    const tasks = cameras.map(async (camera) => {
      if (appState.liveRequestedByCamera[camera.cameraSn] && effectiveLiveLabel(camera) === "ready") {
        return;
      }
      const select = document.querySelector(`select[data-quality-select="${camera.cameraSn}"]`);
      const quality = select ? select.value : (camera.currentQuality || "auto");
      try {
        await startLive(
          camera.cameraSn,
          camera.stationSn,
          quality,
          `Starting live for ${camera.cameraName || camera.cameraSn}...`,
          { refreshAfter: false, optimisticRefresh: false }
        );
      } catch (error) {
        appState.liveRequestedByCamera[camera.cameraSn] = false;
        setStatus(`Start visible live failed on ${camera.cameraName || camera.cameraSn}: ${error}`);
      }
    });
    refreshOptimisticLiveUi();
    await Promise.all(tasks);
    await refreshData({ silentStatus: true, statusText: "" });
  }

  async function stopVisibleLives() {
    if (!appState.data) return;
    const cameras = visibleWallCameras(appState.data);
    setStatus(`Stopping ${cameras.length} visible camera(s)...`);
    for (const camera of cameras) {
      if (!appState.liveRequestedByCamera[camera.cameraSn] && effectiveLiveLabel(camera) !== "ready") {
        continue;
      }
      try {
        await stopLive(
          camera.cameraSn,
          `Stopping live for ${camera.cameraName || camera.cameraSn}...`,
          { refreshAfter: false }
        );
      } catch (error) {
        setStatus(`Stop visible live failed on ${camera.cameraName || camera.cameraSn}: ${error}`);
      }
    }
    await refreshData({ silentStatus: true, statusText: "" });
  }

  return {
    releaseLiveSessions,
    startLive,
    startVisibleLives,
    stopLive,
    stopVisibleLives,
  };
}
