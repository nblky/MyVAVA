export function createMonitorDomain({
  appState,
  GROUP_DEVICE_LABEL,
  GROUP_DEVICE_SN,
  LIVE_STICKY_MS,
}) {
  function selectedNodeKind(data = appState.data) {
    if (appState.selectedDeviceSn === GROUP_DEVICE_SN) {
      return "group";
    }
    const stationSn = data && data.station && data.station.stationSn ? data.station.stationSn : "";
    if (stationSn && appState.selectedDeviceSn === stationSn) {
      return "station";
    }
    if (data && Array.isArray(data.cameras) && data.cameras.some((item) => item.cameraSn === appState.selectedDeviceSn)) {
      return "camera";
    }
    return stationSn ? "station" : "group";
  }

  function normalizedGroupWallSplit() {
    const value = Number(appState.selectedWallSplit || 4);
    return [2, 4, 6, 9].includes(value) ? value : 4;
  }

  function currentWallSplit(data = appState.data) {
    const kind = selectedNodeKind(data);
    if (kind === "camera") return 1;
    if (kind === "station") return 4;
    return normalizedGroupWallSplit();
  }

  function liveSplitConfig(data = appState.data) {
    const kind = selectedNodeKind(data);
    if (kind === "camera") {
      return { kind, locked: true, options: [1], value: 1, label: "监控" };
    }
    if (kind === "station") {
      return { kind, locked: true, options: [4], value: 4, label: "基站" };
    }
    const value = normalizedGroupWallSplit();
    return { kind, locked: false, options: [2, 4, 6, 9], value, label: GROUP_DEVICE_LABEL };
  }

  function selectedObjectLabel(data = appState.data) {
    const kind = selectedNodeKind(data);
    if (kind === "group") {
      return GROUP_DEVICE_LABEL;
    }
    if (kind === "station") {
      return data && data.station ? (data.station.stationName || data.station.stationSn || "基站") : "基站";
    }
    const selectedCamera = data && Array.isArray(data.cameras)
      ? data.cameras.find((item) => item.cameraSn === appState.selectedDeviceSn)
      : null;
    return selectedCamera ? (selectedCamera.cameraName || selectedCamera.cameraSn) : "未选中";
  }

  function ensureSelectedDevice(data = appState.data) {
    if (!data) return;
    const stationSn = data.station && data.station.stationSn ? data.station.stationSn : "";
    if (appState.selectedDeviceSn === GROUP_DEVICE_SN) {
      return;
    }
    if (stationSn && appState.selectedDeviceSn === stationSn) {
      return;
    }
    const cameraExists = (data.cameras || []).some((item) => item.cameraSn === appState.selectedDeviceSn);
    if (cameraExists) return;
    appState.selectedDeviceSn = stationSn || data.cameras[0]?.cameraSn || GROUP_DEVICE_SN;
  }

  function sortCameras(cameras) {
    return [...(cameras || [])].sort((left, right) => {
      const channelDelta = Number(left.channel || 0) - Number(right.channel || 0);
      if (channelDelta !== 0) return channelDelta;
      return String(left.cameraName || "").localeCompare(String(right.cameraName || ""));
    });
  }

  function clipForCamera(camera) {
    const manual = appState.selectedClipByCamera[camera.cameraSn];
    const active =
      camera.recentClips.find((item) => item.streamCode === manual) ||
      camera.latestClip ||
      camera.recentClips[0] ||
      null;
    if (!manual && active) {
      appState.autoSelectedByCamera[camera.cameraSn] = active.streamCode;
    }
    return active;
  }

  function rememberLiveSticky(cameraSn, holdMs = LIVE_STICKY_MS) {
    if (!cameraSn) return;
    appState.liveStickyUntilByCamera[cameraSn] = Date.now() + holdMs;
  }

  function clearLiveSticky(cameraSn) {
    if (!cameraSn) return;
    delete appState.liveStickyUntilByCamera[cameraSn];
  }

  function stableLiveFresh(camera) {
    const cameraSn = camera && camera.cameraSn ? camera.cameraSn : "";
    if (!cameraSn) return false;
    if (camera.liveFresh) {
      rememberLiveSticky(cameraSn);
      return true;
    }
    if (!appState.liveRequestedByCamera[cameraSn]) {
      clearLiveSticky(cameraSn);
      return false;
    }
    return Number(appState.liveStickyUntilByCamera[cameraSn] || 0) > Date.now();
  }

  function effectiveLiveLabel(camera) {
    if (stableLiveFresh(camera)) {
      return "ready";
    }
    if (appState.liveRequestedByCamera[camera.cameraSn]) {
      return "starting";
    }
    return String(camera.liveLabel || "idle");
  }

  function wallModeSignature(camera) {
    const activeClip = clipForCamera(camera);
    const manualClipRequested = !!appState.selectedClipByCamera[camera.cameraSn];
    const liveRequested = !!appState.liveRequestedByCamera[camera.cameraSn];
    const liveFresh = stableLiveFresh(camera);
    if (liveRequested && !liveFresh) {
      return "waiting";
    }
    if (liveRequested && liveFresh) {
      return `live:${camera.liveUrl || ""}`;
    }
    if (manualClipRequested && activeClip) {
      return `clip:${activeClip.streamCode || activeClip.playUrl || ""}`;
    }
    return "idle";
  }

  function buildWallSignature(data) {
    const split = currentWallSplit(data);
    const visible = reorderForSelection(data.cameras, data).slice(0, split);
    return JSON.stringify({
      split,
      visible: visible.map((camera) => camera.cameraSn),
    });
  }

  function selectedQualityValue(camera) {
    return appState.qualityDraftByCamera[camera.cameraSn] || camera.currentQuality || "auto";
  }

  function selectedQualityLabel(camera) {
    const value = selectedQualityValue(camera);
    const matched = (camera.qualityOptions || []).find((item) => item.value === value);
    return matched ? matched.label : (camera.currentQualityLabel || "Auto");
  }

  function cameraKeepAlive(camera) {
    const liveState = camera && typeof camera.liveState === "object" ? camera.liveState : {};
    return !!liveState.keepAlive;
  }

  function liveBadgeClass(liveLabel) {
    const value = String(liveLabel || "").trim().toLowerCase();
    if (value === "ready") return "state-ready";
    if (value === "starting") return "state-starting";
    return "state-idle";
  }

  function cameraBySn(cameraSn) {
    return (appState.data && appState.data.cameras || []).find((item) => item.cameraSn === cameraSn) || null;
  }

  function streamMetaText(camera, rateTextOverride = "") {
    const parts = [
      camera.cameraSn || "",
      camera.streamProfileText || "",
      String(rateTextOverride || appState.liveRates[camera.cameraSn] || camera.liveRateText || "").trim(),
    ].filter(Boolean);
    return parts.join(" · ");
  }

  function livePipelineText(camera) {
    const liveState = camera && typeof camera.liveState === "object" ? camera.liveState : {};
    if (liveState.ready || effectiveLiveLabel(camera) === "ready") {
      return cameraKeepAlive(camera)
        ? "基站 P2P -> 本机 PPCS bridge -> ffmpeg HLS -> 浏览器 (预热保活)"
        : "基站 P2P -> 本机 PPCS bridge -> ffmpeg HLS -> 浏览器";
    }
    if (appState.liveRequestedByCamera[camera.cameraSn] || liveState.active) {
      return cameraKeepAlive(camera)
        ? "正在建立 基站 P2P -> 本机 bridge -> HLS (保活模式)"
        : "正在建立 基站 P2P -> 本机 bridge -> HLS";
    }
    return "未建立 P2P；点击开始后才按需拉起";
  }

  function updateLiveRate(cameraSn, label) {
    const text = String(label || "").trim();
    if (!text) return;
    appState.liveRates[cameraSn] = text;
    document.querySelectorAll(`[data-live-rate="${cameraSn}"]`).forEach((node) => {
      node.textContent = text;
    });
    const camera = cameraBySn(cameraSn);
    if (camera) {
      const meta = streamMetaText(camera, text);
      document.querySelectorAll(`[data-stream-meta="${cameraSn}"]`).forEach((node) => {
        node.textContent = meta;
      });
    }
  }

  function pushLiveRateSample(cameraSn, kbps) {
    if (!Number.isFinite(kbps) || kbps <= 0) return;
    const samples = appState.liveRateSamples[cameraSn] || [];
    samples.push(kbps);
    while (samples.length > 4) samples.shift();
    appState.liveRateSamples[cameraSn] = samples;
    const average = samples.reduce((sum, item) => sum + item, 0) / samples.length;
    if (average >= 1000) {
      updateLiveRate(cameraSn, `${(average / 1000).toFixed(2)} Mbps`);
      return;
    }
    updateLiveRate(cameraSn, `${average.toFixed(0)} kbps`);
  }

  function reorderForSelection(cameras, data = appState.data) {
    const items = sortCameras(cameras);
    if (selectedNodeKind(data) !== "camera") {
      return items;
    }
    const selectedIndex = items.findIndex((item) => item.cameraSn === appState.selectedDeviceSn);
    if (selectedIndex <= 0) return items;
    const selected = items[selectedIndex];
    items.splice(selectedIndex, 1);
    items.unshift(selected);
    return items;
  }

  function visibleWallCameras(data) {
    return reorderForSelection(data.cameras, data).slice(0, currentWallSplit(data)).filter(Boolean);
  }

  function liveMonitorNeeded(data = appState.data) {
    if (Object.values(appState.liveRequestedByCamera || {}).some(Boolean)) {
      return true;
    }
    if (!data || !Array.isArray(data.cameras)) {
      return false;
    }
    return data.cameras.some((camera) => !!camera.liveFresh);
  }

  function activeLiveCameras(data = appState.data, options = {}) {
    if (!data || !Array.isArray(data.cameras)) {
      return [];
    }
    const includeKeepAlive = options.includeKeepAlive === true;
    const onlyKeepAlive = options.onlyKeepAlive === true;
    return data.cameras.filter((camera) => {
      const liveState = camera && typeof camera.liveState === "object" ? camera.liveState : {};
      const active = !!appState.liveRequestedByCamera[camera.cameraSn] || !!liveState.active || effectiveLiveLabel(camera) === "ready";
      if (!active) return false;
      const keepAlive = cameraKeepAlive(camera);
      if (onlyKeepAlive) return keepAlive;
      if (!includeKeepAlive && keepAlive) return false;
      return true;
    });
  }

  function desiredRefreshIntervalMs(data = appState.data) {
    if (!appState.auth) return 0;
    if (Object.keys(appState.recordings || {}).length > 0) {
      return 0;
    }
    const liveNeeded = liveMonitorNeeded(data);
    if (appState.selectedTab === "live") {
      if (liveNeeded) {
        return 3000;
      }
      return appState.autoRefresh ? 10000 : 0;
    }
    if (!appState.autoRefresh) {
      return 0;
    }
    if (liveNeeded) {
      return 5000;
    }
    return 10000;
  }

  function cameraFilterOptions(data, allLabel = "全部设备") {
    const options = [{ value: "all", label: allLabel }];
    (sortCameras(data.cameras) || []).forEach((camera) => {
      options.push({
        value: camera.cameraSn,
        label: `${camera.cameraName} · CH${camera.channel}`,
      });
    });
    return options;
  }

  function effectivePlaybackCameraFilter() {
    const value = String(appState.playbackFilters.cameraSn || "").trim();
    return value || "all";
  }

  function playbackClipKey(clip) {
    if (!clip || typeof clip !== "object") return "";
    return [
      String(clip.cameraSn || "").trim(),
      String(clip.streamCode || "").trim(),
      String(clip.fileName || "").trim(),
      String(clip.startTime || "").trim(),
    ].join("::");
  }

  function selectedPlaybackClip(clips) {
    const items = Array.isArray(clips) ? clips : [];
    if (!items.length) {
      appState.playbackSelectedKey = "";
      return null;
    }
    const selected = items.find((clip) => playbackClipKey(clip) === appState.playbackSelectedKey) || items[0];
    appState.playbackSelectedKey = playbackClipKey(selected);
    return selected;
  }

  function effectiveMessageCameraFilter() {
    return appState.messageFilters.cameraSn || "all";
  }

  function messageTypeKey(item) {
    const typeValue = Number((item && item.visibleNoticeType) || 0);
    if (typeValue > 0) {
      return `type:${typeValue}`;
    }
    const title = String((item && item.title) || "").trim();
    return title ? `title:${title}` : "type:unknown";
  }

  function messageTypeLabel(item) {
    const title = String((item && item.title) || "Unknown").trim() || "Unknown";
    const typeValue = Number((item && item.visibleNoticeType) || 0);
    return typeValue > 0 ? `${title} · Type ${typeValue}` : title;
  }

  return {
    activeLiveCameras,
    buildWallSignature,
    cameraBySn,
    cameraFilterOptions,
    cameraKeepAlive,
    clearLiveSticky,
    clipForCamera,
    currentWallSplit,
    desiredRefreshIntervalMs,
    ensureSelectedDevice,
    effectiveLiveLabel,
    effectiveMessageCameraFilter,
    effectivePlaybackCameraFilter,
    liveBadgeClass,
    liveMonitorNeeded,
    livePipelineText,
    liveSplitConfig,
    matchesSelection: selectedNodeKind,
    messageTypeKey,
    messageTypeLabel,
    normalizedGroupWallSplit,
    playbackClipKey,
    pushLiveRateSample,
    rememberLiveSticky,
    reorderForSelection,
    selectedNodeKind,
    selectedObjectLabel,
    selectedPlaybackClip,
    selectedQualityLabel,
    selectedQualityValue,
    sortCameras,
    stableLiveFresh,
    streamMetaText,
    updateLiveRate,
    visibleWallCameras,
    wallModeSignature,
  };
}
