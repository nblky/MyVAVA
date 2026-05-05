export function createLiveWallModule({
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
}) {
  function renderWallCard(camera) {
    const activeClip = clipForCamera(camera);
    const manualClipRequested = !!appState.selectedClipByCamera[camera.cameraSn];
    const liveRequested = !!appState.liveRequestedByCamera[camera.cameraSn];
    const liveFresh = stableLiveFresh(camera);
    const liveLabel = effectiveLiveLabel(camera);
    const keepAlive = cameraKeepAlive(camera);
    const showLivePlayer = liveRequested && liveFresh;
    const waitingForLive = liveRequested && !liveFresh;
    const isRecording = !!appState.recordings[camera.cameraSn];
    const isSelected = appState.selectedDeviceSn === camera.cameraSn;
    const rateText = appState.liveRates[camera.cameraSn] || camera.liveRateText;
    const startButtonText = waitingForLive ? "启动中" : (showLivePlayer ? "重连" : "开始");
    const startButtonDisabled = waitingForLive ? "disabled" : "";
    const selectedQuality = selectedQualityValue(camera);
    const qualityOptions = (camera.qualityOptions || []).map((option) => `
      <option value="${esc(option.value)}" ${option.value === selectedQuality ? "selected" : ""}>${esc(option.label)}</option>
    `).join("");

    let playerMarkup = `
      <div class="player-idle">
        <div class="player-idle-box">
          <strong>${liveRequested ? "正在等待 Live 就绪..." : "待命中"}</strong>
          <span>${liveRequested ? "底层 P2P 已发起，正在等待新的 HLS 分片。" : (appState.livePrewarmEnabled ? "默认不自动播放。当前预热保活已开启，点开始后会在后台保留链路。" : "默认不自动播放。点击开始后才会拉起底层 P2P live；录像统一到回放页查看。")}
          </span>
          <div class="player-actions">
            ${liveRequested ? `<button type="button" class="mini-btn stop-live-btn" data-camera-sn="${esc(camera.cameraSn)}">取消等待</button>` : `<button type="button" class="mini-btn start-live-btn" data-camera-sn="${esc(camera.cameraSn)}" data-station-sn="${esc(camera.stationSn)}">开始播放</button>`}
          </div>
        </div>
      </div>
    `;

    if (showLivePlayer) {
      playerMarkup = `
        <div class="player-overlay-top">
          <div class="player-chip">True Live</div>
        </div>
        <video
          autoplay
          muted
          playsinline
          webkit-playsinline="true"
          preload="metadata"
          crossorigin="anonymous"
          disablePictureInPicture
          disableRemotePlayback
          data-live-player="1"
          data-camera-sn="${esc(camera.cameraSn)}"
          data-camera-name="${esc(camera.cameraName)}"
          data-live-transport="${esc(camera.liveTransport || "hls")}"
          data-live-src="${esc(camera.liveUrl)}"
        ></video>
      `;
    } else if (manualClipRequested && activeClip) {
      playerMarkup = `
        <div class="player-overlay-top">
          <div class="player-chip">Clip Playback</div>
          <div class="player-chip">${esc(activeClip.startTime)}</div>
        </div>
        <video
          controls
          playsinline
          preload="metadata"
          crossorigin="anonymous"
          data-camera-sn="${esc(camera.cameraSn)}"
          data-camera-name="${esc(camera.cameraName)}"
          src="${esc(activeClip.playUrl)}"
        ></video>
      `;
    }

    return `
      <article class="wall-card ${isSelected ? "is-selected" : ""}" id="camera-card-${esc(camera.cameraSn)}" data-camera-card="${esc(camera.cameraSn)}">
        <div class="wall-card-head">
          <div class="wall-card-title">
            <div class="wall-card-title-main">
              <span class="channel-badge">CH${esc(camera.channel)}</span>
              <span>${esc(camera.cameraName)}</span>
              <span class="dot ${camera.online ? "" : "offline"}" data-online-dot="${esc(camera.cameraSn)}"></span>
            </div>
            <div class="wall-card-sub" data-stream-meta="${esc(camera.cameraSn)}">${esc(streamMetaText(camera, rateText))}</div>
          </div>
          <div class="wall-card-group">
            <span class="wall-head-badge quality" data-quality-badge="${esc(camera.cameraSn)}">${esc(selectedQualityLabel(camera))}</span>
            <span class="wall-head-badge ${esc(liveBadgeClass(liveLabel))}" data-live-badge="${esc(camera.cameraSn)}">${esc(liveLabel)}</span>
            <span class="wall-head-badge keepalive" data-keepalive-badge="${esc(camera.cameraSn)}" ${keepAlive ? "" : "hidden"}>保活</span>
          </div>
        </div>

        <div class="wall-player">${playerMarkup}</div>

        <div class="wall-card-foot">
          <div class="foot-meta">
            <span><strong>在线:</strong> <span data-online-text="${esc(camera.cameraSn)}">${esc(camera.online ? "online" : "offline")}</span></span>
            <span><strong>电量:</strong> <span data-battery-text="${esc(camera.cameraSn)}">${esc(camera.batteryText)}</span></span>
            <span><strong>信号:</strong> <span data-signal-text="${esc(camera.cameraSn)}">${esc(camera.signalText)}</span></span>
          </div>

          <div class="foot-controls">
            <div class="control-select-wrap">
              <select data-quality-select="${esc(camera.cameraSn)}" data-station-sn="${esc(camera.stationSn)}">${qualityOptions}</select>
            </div>
            <div class="control-actions">
              <button type="button" class="mini-btn start-live-btn" ${startButtonDisabled} data-camera-sn="${esc(camera.cameraSn)}" data-station-sn="${esc(camera.stationSn)}">${esc(startButtonText)}</button>
              <button type="button" class="mini-btn stop-live-btn" data-camera-sn="${esc(camera.cameraSn)}">停止</button>
              <button type="button" class="mini-btn secondary fullscreen-btn" data-camera-sn="${esc(camera.cameraSn)}">全屏</button>
              <button type="button" class="mini-btn snapshot-btn" data-camera-sn="${esc(camera.cameraSn)}">截图</button>
              <button type="button" class="mini-btn ${isRecording ? "danger" : "secondary"} record-btn" data-camera-sn="${esc(camera.cameraSn)}">${isRecording ? "停止录像" : "浏览器录像"}</button>
            </div>
          </div>
        </div>
      </article>
    `;
  }

  function renderWall(data) {
    const holder = document.getElementById("wall-grid");
    const split = currentWallSplit(data);
    const cameras = reorderForSelection(data.cameras, data);
    holder.className = `wall-grid split-${split}`;
    const cells = [];
    for (let index = 0; index < split; index += 1) {
      const camera = cameras[index];
      if (camera) {
        cells.push(renderWallCard(camera));
      } else {
        cells.push('<div class="placeholder-card">空画面</div>');
      }
    }
    holder.innerHTML = cells.join("");
  }

  function syncWallCardModes(data, options = {}) {
    const visible = visibleWallCameras(data);
    const visibleSet = new Set(visible.map((camera) => camera.cameraSn));
    const forceAttach = options.forceAttach === true;
    const changedCameras = [];

    Object.keys(appState.wallModeSignatures || {}).forEach((cameraSn) => {
      if (!visibleSet.has(cameraSn)) {
        delete appState.wallModeSignatures[cameraSn];
      }
    });
    Object.keys(appState.livePlayers || {}).forEach((cameraSn) => {
      if (!visibleSet.has(cameraSn)) {
        destroyLivePlayer(cameraSn);
      }
    });

    visible.forEach((camera) => {
      const cameraSn = camera.cameraSn;
      const nextMode = wallModeSignature(camera);
      const prevMode = appState.wallModeSignatures[cameraSn] || "";

      if (forceAttach) {
        appState.wallModeSignatures[cameraSn] = nextMode;
        if (String(nextMode).startsWith("live:")) {
          changedCameras.push(cameraSn);
        } else {
          destroyLivePlayer(cameraSn);
        }
        return;
      }

      if (prevMode === nextMode) {
        if (String(nextMode).startsWith("live:") && !appState.livePlayers[cameraSn]) {
          changedCameras.push(cameraSn);
        }
        return;
      }

      appState.wallModeSignatures[cameraSn] = nextMode;
      destroyLivePlayer(cameraSn);
      const currentCard = document.getElementById(`camera-card-${cameraSn}`);
      if (currentCard) {
        currentCard.outerHTML = renderWallCard(camera);
        changedCameras.push(cameraSn);
      }
    });

    changedCameras.forEach((cameraSn) => {
      attachPlayerForCamera(cameraSn);
    });
  }

  function syncExistingWallMeta(data) {
    const cameraMap = new Map((data.cameras || []).map((camera) => [camera.cameraSn, camera]));
    document.querySelectorAll("[data-camera-card]").forEach((card) => {
      const cameraSn = card.dataset.cameraCard || "";
      const camera = cameraMap.get(cameraSn);
      if (!camera) return;

      card.classList.toggle("is-selected", appState.selectedDeviceSn === cameraSn);
      document.querySelectorAll(`[data-online-dot="${cameraSn}"]`).forEach((node) => {
        node.classList.toggle("offline", !camera.online);
      });
      document.querySelectorAll(`[data-online-text="${cameraSn}"]`).forEach((node) => {
        node.textContent = camera.online ? "online" : "offline";
      });
      document.querySelectorAll(`[data-battery-text="${cameraSn}"]`).forEach((node) => {
        node.textContent = String(camera.batteryText || "-");
      });
      document.querySelectorAll(`[data-signal-text="${cameraSn}"]`).forEach((node) => {
        node.textContent = String(camera.signalText || "-");
      });
      const rateText = appState.liveRates[cameraSn] || camera.liveRateText || "-";
      const liveLabel = effectiveLiveLabel(camera);
      updateLiveRate(cameraSn, rateText);
      document.querySelectorAll(`[data-stream-meta="${cameraSn}"]`).forEach((node) => {
        node.textContent = streamMetaText(camera, rateText);
      });
      document.querySelectorAll(`[data-quality-badge="${cameraSn}"]`).forEach((node) => {
        node.textContent = selectedQualityLabel(camera);
      });
      document.querySelectorAll(`[data-live-badge="${cameraSn}"]`).forEach((node) => {
        node.textContent = liveLabel;
        node.classList.remove("state-ready", "state-starting", "state-idle");
        node.classList.add(liveBadgeClass(liveLabel));
      });
      document.querySelectorAll(`[data-keepalive-badge="${cameraSn}"]`).forEach((node) => {
        node.hidden = !cameraKeepAlive(camera);
      });
      document.querySelectorAll(`select[data-quality-select="${cameraSn}"]`).forEach((node) => {
        const nextValue = selectedQualityValue(camera);
        if (node.value !== nextValue) {
          node.value = nextValue;
        }
      });
    });
  }

  return {
    renderWall,
    renderWallCard,
    syncExistingWallMeta,
    syncWallCardModes,
  };
}
