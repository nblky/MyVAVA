export function createMonitorHandlers({
  appState,
  cameraBySn,
  downloadBlob,
  effectiveLiveLabel,
  postJson,
  refreshData,
  releaseLiveSessions,
  render,
  renderDeviceTree,
  renderHeader,
  renderMessages,
  renderPlayback,
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
  syncLivePrewarmButton,
  syncRefreshLoop,
}) {
  let bound = false;

  function eventNode(event, selector) {
    if (!(event.target instanceof Element)) return null;
    return event.target.closest(selector);
  }

  function rerenderPlayback() {
    if (!appState.data) return;
    renderPlayback(appState.data);
  }

  function rerenderMessages() {
    if (!appState.data) return;
    renderMessages(appState.data);
  }

  async function handleClick(event) {
    const mainTab = eventNode(event, ".main-tab");
    if (mainTab) {
      setTab(mainTab.dataset.tab || "live");
      return;
    }

    const bulkStart = eventNode(event, ".bulk-start-btn");
    if (bulkStart) {
      if (appState.selectedTab !== "live") return;
      bulkStart.disabled = true;
      bulkStart.textContent = "全播中...";
      try {
        await startVisibleLives();
      } finally {
        bulkStart.disabled = false;
        bulkStart.textContent = "可见全播";
      }
      return;
    }

    const prewarmToggle = eventNode(event, "#toggle-prewarm-btn");
    if (prewarmToggle) {
      appState.livePrewarmEnabled = !appState.livePrewarmEnabled;
      syncLivePrewarmButton();
      if (appState.data) {
        renderHeader(appState.data);
      }
      if (appState.livePrewarmEnabled) {
        setStatus("预热保活已开启。之后点开始/可见全播会保持后台链路。");
        return;
      }
      if (appState.selectedTab !== "live") {
        await releaseLiveSessions({
          includeKeepAlive: true,
          onlyKeepAlive: true,
          statusText: "预热保活已关闭，回收后台保活链路...",
          doneStatus: "后台预热链路已回收。",
        });
        return;
      }
      setStatus("预热保活已关闭。当前点播不会继续保活，离开 Live 页后会自动回收链路。");
      return;
    }

    const bulkStop = eventNode(event, ".bulk-stop-btn");
    if (bulkStop) {
      if (appState.selectedTab !== "live") return;
      bulkStop.disabled = true;
      bulkStop.textContent = "全停中...";
      try {
        await stopVisibleLives();
      } finally {
        bulkStop.disabled = false;
        bulkStop.textContent = "可见全停";
      }
      return;
    }

    const splitButton = eventNode(event, ".split-btn");
    if (splitButton) {
      if (appState.selectedTab !== "live") return;
      if (selectedNodeKind() !== "group") return;
      const nextSplit = Number(splitButton.dataset.split || 4);
      if (![2, 4, 6, 9].includes(nextSplit)) return;
      appState.selectedWallSplit = nextSplit;
      if (appState.data) {
        render(appState.data);
      }
      return;
    }

    const treeItem = eventNode(event, ".tree-item");
    if (treeItem) {
      appState.selectedDeviceSn = treeItem.dataset.deviceSn || "";
      if (appState.data) {
        render(appState.data);
      }
      return;
    }

    const playbackReset = eventNode(event, "#playback-filter-reset");
    if (playbackReset) {
      appState.playbackFilters.cameraSn = "all";
      appState.playbackFilters.dateFrom = "";
      appState.playbackFilters.dateTo = "";
      rerenderPlayback();
      return;
    }

    const messageReset = eventNode(event, "#message-filter-reset");
    if (messageReset) {
      appState.messageFilters.cameraSn = "all";
      appState.messageFilters.typeKey = "all";
      appState.messageFilters.dateFrom = "";
      appState.messageFilters.dateTo = "";
      rerenderMessages();
      return;
    }

    const clipPick = eventNode(event, ".search-clip-pick");
    if (clipPick) {
      const cameraSn = clipPick.dataset.cameraSn || "";
      appState.playbackSelectedKey = clipPick.dataset.playbackKey || "";
      if (cameraSn) {
        appState.selectedDeviceSn = cameraSn;
      }
      if (appState.data) {
        render(appState.data);
      }
      setStatus(`本地回放已切到 ${cameraSn || "当前录像"}，直接读取已解码 MP4。`);
      return;
    }

    const startLiveButton = eventNode(event, ".start-live-btn");
    if (startLiveButton) {
      const select = document.querySelector(`select[data-quality-select="${startLiveButton.dataset.cameraSn}"]`);
      const quality = select ? select.value : "auto";
      startLiveButton.disabled = true;
      startLiveButton.textContent = "启动中...";
      try {
        await startLive(
          startLiveButton.dataset.cameraSn,
          startLiveButton.dataset.stationSn,
          quality,
          `Starting live for ${startLiveButton.dataset.cameraSn}...`
        );
      } catch (error) {
        appState.liveRequestedByCamera[startLiveButton.dataset.cameraSn] = false;
        setStatus(`Start live failed: ${error}`);
      } finally {
        startLiveButton.disabled = false;
        startLiveButton.textContent = "开始";
      }
      return;
    }

    const stopLiveButton = eventNode(event, ".stop-live-btn");
    if (stopLiveButton) {
      stopLiveButton.disabled = true;
      stopLiveButton.textContent = "停止中...";
      try {
        await stopLive(stopLiveButton.dataset.cameraSn, `Stopping live for ${stopLiveButton.dataset.cameraSn}...`);
      } catch (error) {
        setStatus(`Stop live failed: ${error}`);
      } finally {
        stopLiveButton.disabled = false;
        stopLiveButton.textContent = "停止";
      }
      return;
    }

    const fullscreenButton = eventNode(event, ".fullscreen-btn");
    if (fullscreenButton) {
      const cameraSn = fullscreenButton.dataset.cameraSn || "";
      const video = document.querySelector(`video[data-camera-sn="${cameraSn}"]`);
      const panel = video ? video.closest(".wall-player") : null;
      if (!video && !panel) {
        setStatus(`No player found for ${cameraSn}`);
        return;
      }
      try {
        if (video && typeof video.webkitEnterFullscreen === "function") {
          video.webkitEnterFullscreen();
          return;
        }
        const target = panel || video;
        if (target && typeof target.requestFullscreen === "function") {
          await target.requestFullscreen();
          return;
        }
        if (panel && typeof panel.webkitRequestFullscreen === "function") {
          panel.webkitRequestFullscreen();
          return;
        }
        setStatus("This browser does not support fullscreen here.");
      } catch (error) {
        setStatus(`Fullscreen failed: ${error}`);
      }
      return;
    }

    const buzzerToggle = eventNode(event, ".station-buzzer-toggle");
    if (buzzerToggle) {
      const action = buzzerToggle.dataset.action || "on";
      buzzerToggle.disabled = true;
      buzzerToggle.textContent = action === "on" ? "开启中..." : "关闭中...";
      try {
        await postJson("/monitor/live/control/buzzer", {
          stationSn: buzzerToggle.dataset.stationSn,
          action,
        }, true);
        await refreshData();
      } catch (error) {
        setStatus(`Station buzzer failed: ${error}`);
      } finally {
        buzzerToggle.disabled = false;
      }
      return;
    }

    const snapshotButton = eventNode(event, ".snapshot-btn");
    if (snapshotButton) {
      const cameraSn = snapshotButton.dataset.cameraSn || "";
      const video = document.querySelector(`video[data-camera-sn="${cameraSn}"]`);
      if (!video) {
        setStatus(`No video element found for ${cameraSn}`);
        return;
      }
      if (!video.videoWidth || !video.videoHeight) {
        setStatus(`Snapshot unavailable for ${cameraSn}: video not ready yet.`);
        return;
      }
      const canvas = document.createElement("canvas");
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      const ctx = canvas.getContext("2d");
      if (!ctx) {
        setStatus(`Snapshot failed for ${cameraSn}.`);
        return;
      }
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      canvas.toBlob((blob) => {
        if (!blob) {
          setStatus(`Snapshot failed for ${cameraSn}.`);
          return;
        }
        downloadBlob(blob, `${safeFilePart(video.dataset.cameraName)}-${Date.now()}.png`);
        setStatus(`Snapshot saved for ${cameraSn}.`);
      }, "image/png");
      return;
    }

    const recordButton = eventNode(event, ".record-btn");
    if (recordButton) {
      const cameraSn = recordButton.dataset.cameraSn || "";
      const video = document.querySelector(`video[data-camera-sn="${cameraSn}"]`);
      if (!video) {
        setStatus(`No video element found for ${cameraSn}`);
        return;
      }
      const currentRecording = appState.recordings[cameraSn];
      if (currentRecording) {
        currentRecording.recorder.stop();
        recordButton.textContent = "收尾中...";
        recordButton.disabled = true;
        return;
      }
      if (typeof window.MediaRecorder === "undefined") {
        setStatus("This browser does not support MediaRecorder.");
        return;
      }
      const captureStream = video.captureStream ? video.captureStream() : (video.webkitCaptureStream ? video.webkitCaptureStream() : null);
      if (!captureStream) {
        setStatus("This browser does not support captureStream on video.");
        return;
      }
      const mimeTypes = [
        "video/webm;codecs=vp9,opus",
        "video/webm;codecs=vp8,opus",
        "video/webm",
      ];
      const supportedMime = mimeTypes.find((item) => !window.MediaRecorder.isTypeSupported || window.MediaRecorder.isTypeSupported(item)) || "";
      const recorder = supportedMime ? new MediaRecorder(captureStream, { mimeType: supportedMime }) : new MediaRecorder(captureStream);
      const chunks = [];
      recorder.ondataavailable = (mediaEvent) => {
        if (mediaEvent.data && mediaEvent.data.size) {
          chunks.push(mediaEvent.data);
        }
      };
      recorder.onstop = () => {
        delete appState.recordings[cameraSn];
        const blob = new Blob(chunks, { type: supportedMime || "video/webm" });
        downloadBlob(blob, `${safeFilePart(video.dataset.cameraName)}-${Date.now()}.webm`);
        captureStream.getTracks().forEach((track) => track.stop());
        setStatus(`Browser recording saved for ${cameraSn}.`);
        if (Object.keys(appState.recordings || {}).length === 0 && appState.autoRefreshBeforeRecording !== null) {
          appState.autoRefresh = !!appState.autoRefreshBeforeRecording;
          appState.autoRefreshBeforeRecording = null;
          syncAutoRefresh();
        }
        render(appState.data);
      };
      recorder.start(1000);
      appState.recordings[cameraSn] = { recorder };
      if (appState.autoRefreshBeforeRecording === null) {
        appState.autoRefreshBeforeRecording = !!appState.autoRefresh;
      }
      if (appState.autoRefresh) {
        appState.autoRefresh = false;
        syncAutoRefresh();
      } else {
        syncRefreshLoop();
      }
      recordButton.textContent = "停止录像";
      recordButton.classList.add("danger");
      setStatus(`Browser recording started for ${cameraSn}. Auto refresh paused.`);
    }
  }

  async function handleChange(event) {
    const playbackField = eventNode(event, "select[data-playback-filter], input[data-playback-filter]");
    if (playbackField) {
      const key = playbackField.dataset.playbackFilter || "";
      if (!key) return;
      appState.playbackFilters[key] = String(playbackField.value || "");
      rerenderPlayback();
      return;
    }

    const messageField = eventNode(event, "select[data-message-filter], input[data-message-filter]");
    if (messageField) {
      const key = messageField.dataset.messageFilter || "";
      if (!key) return;
      appState.messageFilters[key] = String(messageField.value || "");
      rerenderMessages();
      return;
    }

    const qualitySelect = eventNode(event, "select[data-quality-select]");
    if (qualitySelect) {
      const cameraSn = qualitySelect.dataset.qualitySelect || "";
      const stationSn = qualitySelect.dataset.stationSn || "";
      const quality = String(qualitySelect.value || "auto").trim().toLowerCase() || "auto";
      appState.qualityDraftByCamera[cameraSn] = quality;
      const camera = cameraBySn(cameraSn);
      document.querySelectorAll(`[data-quality-badge="${cameraSn}"]`).forEach((node) => {
        node.textContent = selectedQualityLabel(
          camera || {
            cameraSn,
            currentQualityLabel: "Auto",
            qualityOptions: appState.data?.cameras?.[0]?.qualityOptions || [],
          }
        );
      });
      if (camera && (appState.liveRequestedByCamera[cameraSn] || effectiveLiveLabel(camera) === "ready")) {
        try {
          await startLive(
            cameraSn,
            stationSn || camera.stationSn || "",
            quality,
            `Applying ${quality} for ${camera.cameraName || cameraSn}...`
          );
        } catch (error) {
          setStatus(`Quality switch failed: ${error}`);
        }
        return;
      }
      setStatus(`已选择 ${quality.toUpperCase()}，点开始后按该清晰度拉流。`);
    }
  }

  function handleInput(event) {
    const filterInput = eventNode(event, "#device-filter");
    if (!filterInput) return;
    appState.deviceFilter = filterInput.value || "";
    if (appState.data) {
      renderDeviceTree(appState.data);
    }
  }

  function attachHandlers() {
    if (bound) return;
    bound = true;
    const root = document.getElementById("monitor-app") || document.body;
    root.addEventListener("click", (event) => {
      void handleClick(event);
    });
    root.addEventListener("change", (event) => {
      void handleChange(event);
    });
    root.addEventListener("input", (event) => {
      handleInput(event);
    });
  }

  return { attachHandlers };
}
