import {
  GROUP_DEVICE_LABEL,
  GROUP_DEVICE_SN,
  LIVE_STICKY_MS,
} from "./monitor_constants.js";
import { createAppState } from "./monitor_state.js";
import {
  esc,
  escapeAttrSelectorValue,
  matchesDateRange,
  safeFilePart,
} from "./monitor_utils.js";
import { createMonitorApi } from "./monitor_api.js";
import { createMonitorAuthRuntime } from "./monitor_auth.js";
import { createMonitorController } from "./monitor_controller.js";
import { createMonitorDomain } from "./monitor_domain.js";
import { createMonitorHandlers } from "./monitor_handlers.js";
import { createMonitorLiveControl } from "./monitor_live_control.js";
import { createLivePlayerRuntime } from "./monitor_live_players.js";
import { createMonitorRender } from "./monitor_render.js";
import { createLiveWallModule } from "./monitor_live_wall.js";

    const appState = createAppState();
    const monitorApi = createMonitorApi({ appState });
    const { postJson, fetchMonitorData } = monitorApi;
    const monitorDomain = createMonitorDomain({
      appState,
      GROUP_DEVICE_LABEL,
      GROUP_DEVICE_SN,
      LIVE_STICKY_MS,
    });
    const {
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
      livePipelineText,
      liveSplitConfig,
      messageTypeKey,
      messageTypeLabel,
      playbackClipKey,
      pushLiveRateSample,
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
    } = monitorDomain;
    let livePlayerRuntime = null;

    function ensureLivePlayerRuntime() {
      if (!livePlayerRuntime) {
        livePlayerRuntime = createLivePlayerRuntime({
          appState,
          escapeAttrSelectorValue,
          pushLiveRateSample,
          setStatus,
          visibleWallCameras,
        });
      }
      return livePlayerRuntime;
    }

    function destroyLivePlayer(cameraSn) {
      ensureLivePlayerRuntime().destroyLivePlayer(cameraSn);
    }

    function destroyLivePlayers() {
      ensureLivePlayerRuntime().destroyLivePlayers();
    }

    function attachPlayerForCamera(cameraSn) {
      ensureLivePlayerRuntime().attachPlayerForCamera(cameraSn);
    }

    const liveWallModule = createLiveWallModule({
      appState,
      esc,
      attachPlayerForCamera,
      cameraKeepAlive,
      clipForCamera,
      currentWallSplit,
      destroyLivePlayer,
      effectiveLiveLabel,
      liveBadgeClass,
      reorderForSelection,
      selectedQualityLabel,
      selectedQualityValue,
      stableLiveFresh,
      streamMetaText,
      updateLiveRate,
      visibleWallCameras,
      wallModeSignature,
    });
    const {
      renderWall,
      syncExistingWallMeta,
      syncWallCardModes,
    } = liveWallModule;
    const monitorRender = createMonitorRender({
      appState,
      esc,
      GROUP_DEVICE_LABEL,
      GROUP_DEVICE_SN,
      cameraFilterOptions,
      cameraKeepAlive,
      clipForCamera,
      currentWallSplit,
      effectiveLiveLabel,
      effectiveMessageCameraFilter,
      effectivePlaybackCameraFilter,
      livePipelineText,
      liveSplitConfig,
      matchesDateRange,
      messageTypeKey,
      messageTypeLabel,
      playbackClipKey,
      selectedNodeKind,
      selectedObjectLabel,
      selectedPlaybackClip,
      sortCameras,
    });
    const {
      renderDeviceTree: renderDeviceTreeView,
      renderEvents: renderEventsView,
      renderHeader: renderHeaderView,
      renderHome: renderHomeView,
      renderLiveDetail: renderLiveDetailView,
      renderMessages: renderMessagesView,
      renderPlayback: renderPlaybackView,
      renderStation: renderStationView,
      syncLivePrewarmButton: syncLivePrewarmButtonView,
    } = monitorRender;
    let liveControl = null;
    let handlers = null;
    const controller = createMonitorController({
      appState,
      attachHandlers: () => handlers.attachHandlers(),
      buildWallSignature,
      desiredRefreshIntervalMs,
      destroyLivePlayers,
      effectiveLiveLabel,
      ensureSelectedDevice,
      fetchMonitorData,
      releaseLiveSessions: (...args) => liveControl.releaseLiveSessions(...args),
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
    });
    const {
      refreshData,
      render,
      setTab,
      syncAutoRefresh,
      syncRefreshLoop,
    } = controller;
    function refreshOptimisticLiveUi() {
      if (!appState.data) return;
      renderHeaderView(appState.data);
      renderDeviceTreeView(appState.data);
      if (appState.selectedTab !== "live") {
        return;
      }
      syncExistingWallMeta(appState.data);
      syncWallCardModes(appState.data, { forceAttach: false });
      renderLiveDetailView(appState.data);
    }
    liveControl = createMonitorLiveControl({
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
    });
    const {
      releaseLiveSessions,
      startLive,
      startVisibleLives,
      stopLive,
      stopVisibleLives,
    } = liveControl;
    const authRuntime = createMonitorAuthRuntime({
      appState,
      destroyLivePlayers,
      postJson,
      refreshData,
      syncAutoRefresh,
      syncLivePrewarmButton: syncLivePrewarmButtonView,
    });
    handlers = createMonitorHandlers({
      appState,
      cameraBySn,
      downloadBlob,
      effectiveLiveLabel,
      postJson,
      refreshData,
      releaseLiveSessions,
      render,
      renderDeviceTree: renderDeviceTreeView,
      renderHeader: renderHeaderView,
      renderMessages: renderMessagesView,
      renderPlayback: renderPlaybackView,
      safeFilePart,
      selectedNodeKind,
      selectedQualityLabel,
      setStatus,
      setTab,
      startLive,
      startVisibleLives,
      stopLive,
      stopVisibleLives,
      syncAutoRefresh,
      syncLivePrewarmButton: syncLivePrewarmButtonView,
      syncRefreshLoop,
    });
    const { attachHandlers } = handlers;

    function setStatus(message) {
      document.getElementById("status-line").textContent = String(message || "");
    }

    function downloadBlob(blob, filename) {
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1500);
    }

    document.getElementById("refresh-btn").addEventListener("click", refreshData);
    document.getElementById("toggle-auto-btn").addEventListener("click", () => {
      appState.autoRefresh = !appState.autoRefresh;
      syncAutoRefresh();
      if (appState.data) renderHeaderView(appState.data);
    });
    authRuntime.bindAuthUi();
    authRuntime.boot();
