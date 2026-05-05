export function createMonitorRender({
  appState,
  esc,
  GROUP_DEVICE_LABEL,
  GROUP_DEVICE_SN,
  cameraFilterOptions,
  cameraKeepAlive,
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
}) {
  function renderLiveSplitControls(data = appState.data) {
    const holder = document.getElementById("toolbar-live-split");
    if (!holder) return;
    const config = liveSplitConfig(data);
    holder.innerHTML = `
      <span>分屏:</span>
      ${config.options.map((value) => `
        <button
          type="button"
          class="split-btn ${value === config.value ? "is-active" : ""}"
          data-split="${value}"
          ${config.locked ? "disabled" : ""}
          title="${config.locked ? `${config.label}视图固定 ${value} 分屏` : `${config.label}切到 ${value} 分屏`}"
        >${value}</button>
      `).join("")}
    `;
  }

  function syncLivePrewarmButton() {
    const button = document.getElementById("toggle-prewarm-btn");
    if (!button) return;
    const activeCount = Number((appState.data && appState.data.summary && appState.data.summary.livePrewarmCount) || 0);
    button.textContent = `预热保活: ${appState.livePrewarmEnabled ? "开" : "关"}${activeCount ? ` · 后台${activeCount}` : ""}`;
    button.classList.toggle("is-active", appState.livePrewarmEnabled);
  }

  function renderHeader(data) {
    const liveReady = data.cameras.filter((item) => effectiveLiveLabel(item) === "ready").length;
    const livePrewarm = Number((data.summary && data.summary.livePrewarmCount) || 0);
    const splitConfig = liveSplitConfig(data);
    const stationTabActive = appState.selectedTab === "station";
    const profile = appState.auth && appState.auth.profile ? appState.auth.profile : null;
    const profileLabel = profile
      ? (profile.nickname || profile.name || profile.username || profile.email || "已登录")
      : "未登录";
    document.getElementById("topbar-summary").textContent =
      `在线 ${data.cameras.filter((item) => item.online).length}/${data.cameras.length} · Live ready ${liveReady} · 云录像 ${data.summary.cloudMediaCount} · 消息 ${data.summary.messageCount} · ${data.station.sessionText || "idle"} session`;
    document.getElementById("current-user").textContent = profileLabel;
    document.getElementById("toggle-auto-btn").textContent = `自动刷新: ${appState.autoRefresh ? "开" : "关"}`;
    document.getElementById("sidebar-split-label").textContent = `${currentWallSplit(data)}分屏`;
    document.getElementById("sidebar-title").textContent = stationTabActive ? "基站列表" : "设备树";
    document.getElementById("sidebar-context-label").textContent = stationTabActive ? "当前账号基站" : "当前账号视图";
    document.getElementById("sidebar-scope-label").textContent = stationTabActive ? "本地假云 / 基站" : "本地假云 / 单基站";
    document.getElementById("device-filter").placeholder = stationTabActive ? "输入基站名称过滤" : "输入设备或点位名称过滤";
    const pageTitleMap = {
      home: "首页",
      live: "Live",
      playback: "回放",
      messages: "消息",
      station: "基站",
    };
    const liveTabActive = appState.selectedTab === "live";
    document.getElementById("page-title").textContent = pageTitleMap[appState.selectedTab] || "Live";
    document.getElementById("toolbar-live-split").hidden = !liveTabActive;
    document.getElementById("toolbar-live-actions").hidden = !liveTabActive;
    renderLiveSplitControls(data);
    syncLivePrewarmButton();
    document.querySelectorAll(".main-tab").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.tab === appState.selectedTab);
    });
    document.querySelectorAll(".page").forEach((page) => {
      page.classList.toggle("is-active", page.id === `page-${appState.selectedTab}`);
    });
    const toolbarStatus = document.getElementById("toolbar-status");
    if (toolbarStatus) {
      const chips = [
        ["基站", data.station.stationName || data.station.stationSn || "-"],
        ["当前对象", selectedObjectLabel(data)],
        ["层级", splitConfig.label],
        ["在线", `${data.cameras.filter((item) => item.online).length}/${data.cameras.length}`],
        ["Live", `${liveReady}/${data.cameras.length}`],
        ["预热", `${appState.livePrewarmEnabled ? "开" : "关"} · 后台 ${livePrewarm}`],
        ["刷新", appState.autoRefresh ? "自动" : "手动"],
      ];
      toolbarStatus.innerHTML = chips.map(([key, value]) => `
        <div class="toolbar-chip">
          <strong>${esc(key)}</strong>
          <span>${esc(value)}</span>
        </div>
      `).join("");
    }
  }

  function renderDeviceTree(data) {
    const holder = document.getElementById("device-tree");
    const filter = appState.deviceFilter.trim().toLowerCase();
    const stationOnlyMode = appState.selectedTab === "station";
    const cameras = sortCameras(data.cameras).filter((camera) => {
      if (!filter) return true;
      return `${camera.cameraName} ${camera.cameraSn}`.toLowerCase().includes(filter);
    });
    const station = data.station || {};
    const onlineCount = cameras.filter((camera) => camera.online).length;

    if (stationOnlyMode) {
      const stationText = `${station.stationName || ""} ${station.stationSn || ""}`.toLowerCase();
      const stationVisible = !filter || stationText.includes(filter);
      holder.innerHTML = stationVisible ? `
        <div class="tree-group-label">
          <span class="tree-icon">▣</span>
          <span>基站</span>
        </div>
        <div class="tree-children">
          <button type="button" class="tree-item ${appState.selectedDeviceSn === station.stationSn ? "is-active" : ""}" data-device-sn="${esc(station.stationSn || "")}" data-device-kind="station">
            <span class="tree-meta">
              <span class="tree-icon">▣</span>
              <span class="tree-copy">
                <strong>${esc(station.stationName || "Base Station")}</strong>
                <span>${esc(`${station.onlineText || "station unknown"} · ${data.cameras.length} 路监控`)}</span>
              </span>
            </span>
            <span class="dot ${station.online ? "" : "offline"}"></span>
          </button>
        </div>
      ` : '<div class="empty">没有匹配到基站。</div>';

      document.getElementById("sidebar-station-quick").innerHTML = [
        ["基站", station.onlineText || "-"],
        ["通道", `${data.cameras.length}`],
        ["TF", station.tfCardText || "-"],
        ["Session", station.sessionText || "-"],
      ].map(([key, value]) => `
        <div class="mini-box">
          <div class="k">${esc(key)}</div>
          <div class="v">${esc(value)}</div>
        </div>
      `).join("");
      return;
    }

    holder.innerHTML = `
      <div class="tree-group-label">
        <span class="tree-icon">▾</span>
        <span>分组1</span>
      </div>
      <div class="tree-children">
        <button type="button" class="tree-item ${appState.selectedDeviceSn === GROUP_DEVICE_SN ? "is-active" : ""}" data-device-sn="${GROUP_DEVICE_SN}" data-device-kind="group">
          <span class="tree-meta">
            <span class="tree-icon">▥</span>
            <span class="tree-copy">
              <strong>${esc(GROUP_DEVICE_LABEL)}</strong>
              <span>${esc(`${station.stationName || "1 个基站"} · ${onlineCount}/${cameras.length || data.cameras.length} 在线`)}</span>
            </span>
          </span>
          <span class="dot ${onlineCount > 0 ? "" : "offline"}"></span>
        </button>
        <div class="tree-children">
          <div class="tree-station-block">
            <button type="button" class="tree-item ${appState.selectedDeviceSn === station.stationSn ? "is-active" : ""}" data-device-sn="${esc(station.stationSn || "")}" data-device-kind="station">
              <span class="tree-meta">
                <span class="tree-icon">▣</span>
                <span class="tree-copy">
                  <strong>${esc(station.stationName || "Base Station")}</strong>
                  <span>${esc(station.onlineText || "station unknown")}</span>
                </span>
              </span>
              <span class="dot ${station.online ? "" : "offline"}"></span>
            </button>
            <div class="tree-children tree-camera-children">
              ${cameras.map((camera) => `
                <button type="button" class="tree-item ${appState.selectedDeviceSn === camera.cameraSn ? "is-active" : ""}" data-device-sn="${esc(camera.cameraSn)}" data-device-kind="camera">
                  <span class="tree-meta">
                    <span class="tree-icon">▢</span>
                    <span class="tree-copy">
                      <strong>${esc(camera.cameraName)}</strong>
                      <span>${esc(`Channel ${camera.channel} · ${effectiveLiveLabel(camera)}`)}</span>
                    </span>
                  </span>
                  <span class="dot ${camera.online ? "" : "offline"}"></span>
                </button>
              `).join("")}
            </div>
          </div>
        </div>
      </div>
    `;

    document.getElementById("sidebar-station-quick").innerHTML = [
      ["基站", station.onlineText || "-"],
      ["TF", station.tfCardText || "-"],
      ["NAS", station.nasText || "-"],
      ["Session", station.sessionText || "-"],
    ].map(([key, value]) => `
      <div class="mini-box">
        <div class="k">${esc(key)}</div>
        <div class="v">${esc(value)}</div>
      </div>
    `).join("");
  }

  function renderLiveDetail(data) {
    const station = data.station || {};
    const selectedKind = selectedNodeKind(data);
    const groupSelected = selectedKind === "group";
    const stationSelected = !!(station.stationSn && station.stationSn === appState.selectedDeviceSn);
    const selectedCamera = data.cameras.find((item) => item.cameraSn === appState.selectedDeviceSn) || null;
    const holder = document.getElementById("live-detail");

    if (groupSelected || stationSelected) {
      holder.innerHTML = `
        <div class="detail-head">
          <div>
            <h3>${esc(groupSelected ? GROUP_DEVICE_LABEL : (station.stationName || station.stationSn || "基站"))} Live 总览</h3>
            <div class="detail-sub">${esc(groupSelected ? "当前选中对象是分组。可在这里切 2 / 4 / 6 / 9 分屏。" : "当前选中对象是基站。基站视图固定 4 分屏，单独点某台摄像头后这里会切成该点位的 live 详情。")}</div>
          </div>
        </div>
        <div class="detail-grid">
          ${[
            ["层级", groupSelected ? "分组视图" : "基站视图"],
            ["基站在线", station.onlineText || "-"],
            ["会话", station.sessionText || "-"],
            ["TF 卡", station.tfCardText || "-"],
            ["NAS", station.nasText || "-"],
            ["P2P / Live", "网页不会常驻 P2P；点开始后才按需拉起"],
            ["当前分屏", `${currentWallSplit(data)} 分屏`],
            ["预热保活", `${appState.livePrewarmEnabled ? "开" : "关"} · 后台 ${Number((data.summary && data.summary.livePrewarmCount) || 0)}`],
            ["摄像头数量", String((data.cameras || []).length)],
            ["Live Ready", `${data.cameras.filter((item) => effectiveLiveLabel(item) === "ready").length}/${data.cameras.length}`],
            ["云录像", String(data.summary.cloudMediaCount)],
            ["消息数", String(data.summary.messageCount)],
          ].map(([key, value]) => `
            <div class="panel-kv-item">
              <div class="k">${esc(key)}</div>
              <div class="v">${esc(value)}</div>
            </div>
          `).join("")}
        </div>
      `;
      return;
    }

    if (!selectedCamera) {
      holder.innerHTML = '<div class="empty">当前没有摄像头数据。</div>';
      return;
    }

    const latestMessage = selectedCamera.latestMessage || null;
    const selectedLiveLabel = effectiveLiveLabel(selectedCamera);
    holder.innerHTML = `
      <div class="detail-head">
        <div>
          <h3>${esc(selectedCamera.cameraName)} Live 信息</h3>
          <div class="detail-sub">${esc(selectedCamera.cameraSn)} · ${esc(selectedCamera.playbackMode)} · ${esc(selectedCamera.audioProfileText)}</div>
        </div>
      </div>
      <div class="detail-grid">
        ${[
          ["Browser Live", selectedLiveLabel],
          ["Live 链路", livePipelineText(selectedCamera)],
          ["预热保活", cameraKeepAlive(selectedCamera) ? "开启" : "关闭"],
          ["当前码率", appState.liveRates[selectedCamera.cameraSn] || selectedCamera.liveRateText || "-"],
          ["视频", selectedCamera.streamProfileText],
          ["音频", selectedCamera.audioProfileText],
          ["检测模式", selectedCamera.detectionText],
          ["最后录像", selectedCamera.lastClipText],
          ["最新消息", latestMessage ? latestMessage.title : "-"],
          ["HLS 窗口", selectedCamera.liveWindowText],
        ].map(([key, value]) => `
          <div class="panel-kv-item">
            <div class="k">${esc(key)}</div>
            <div class="v">${esc(value)}</div>
          </div>
        `).join("")}
      </div>
    `;
  }

  function renderHome(data) {
    const station = data.station || {};
    const selectedKind = selectedNodeKind(data);
    const groupSelected = selectedKind === "group";
    const stationSelected = !!(station.stationSn && station.stationSn === appState.selectedDeviceSn);
    const liveReady = data.cameras.filter((item) => effectiveLiveLabel(item) === "ready").length;
    const onlineCount = data.cameras.filter((item) => item.online).length;
    const selectedCamera = data.cameras.find((item) => item.cameraSn === appState.selectedDeviceSn) || null;

    document.getElementById("home-metrics").innerHTML = [
      ["在线摄像头", `${onlineCount}/${data.cameras.length}`],
      ["Live Ready", `${liveReady}/${data.cameras.length}`],
      ["云录像", String(data.summary.cloudMediaCount)],
      ["消息数", String(data.summary.messageCount)],
    ].map(([key, value]) => `
      <article class="stat-card">
        <div class="k">${esc(key)}</div>
        <div class="v">${esc(value)}</div>
      </article>
    `).join("");

    document.getElementById("home-focus").innerHTML = groupSelected ? `
      <div class="detail-head">
        <div>
          <h3>当前关注对象</h3>
          <div class="detail-sub">${esc(GROUP_DEVICE_LABEL)} · 分组视角</div>
        </div>
      </div>
      <div class="detail-grid">
        ${[
          ["分组", GROUP_DEVICE_LABEL],
          ["基站", station.stationName || station.stationSn || "-"],
          ["在线摄像头", `${onlineCount}/${data.cameras.length}`],
          ["Live Ready", `${liveReady}/${data.cameras.length}`],
          ["当前分屏", `${currentWallSplit(data)} 分屏`],
          ["云录像", String(data.summary.cloudMediaCount)],
          ["消息数", String(data.summary.messageCount)],
          ["会话", station.sessionText || "-"],
        ].map(([key, value]) => `
          <div class="panel-kv-item">
            <div class="k">${esc(key)}</div>
            <div class="v">${esc(value)}</div>
          </div>
        `).join("")}
      </div>
    ` : stationSelected ? `
      <div class="detail-head">
        <div>
          <h3>当前关注对象</h3>
          <div class="detail-sub">${esc(station.stationName || station.stationSn || "基站")} · 基站视角</div>
        </div>
      </div>
      <div class="detail-grid">
        ${[
          ["基站在线", station.onlineText || "-"],
          ["会话", station.sessionText || "-"],
          ["当前分屏", `${currentWallSplit(data)} 分屏`],
          ["TF / NAS", `${station.tfCardText || "-"} / ${station.nasText || "-"}`],
          ["本地存储", station.storageText || "-"],
          ["剩余空间", station.storageFreeText || "-"],
          ["固件", station.firmwareText || "-"],
          ["IP", station.ipText || "-"],
          ["NTP", station.ntpText || "-"],
        ].map(([key, value]) => `
          <div class="panel-kv-item">
            <div class="k">${esc(key)}</div>
            <div class="v">${esc(value)}</div>
          </div>
        `).join("")}
      </div>
    ` : selectedCamera ? `
      <div class="detail-head">
        <div>
          <h3>当前关注点位</h3>
          <div class="detail-sub">${esc(selectedCamera.cameraName)} · ${esc(selectedCamera.cameraSn)}</div>
        </div>
      </div>
      <div class="detail-grid">
        ${[
          ["基站", station.stationName || station.stationSn || "-"],
          ["设备在线", selectedCamera.online ? "online" : "offline"],
          ["Live", effectiveLiveLabel(selectedCamera)],
          ["视频规格", selectedCamera.streamProfileText],
          ["检测模式", selectedCamera.detectionText],
          ["最后录像", selectedCamera.lastClipText],
          ["TF / NAS", `${station.tfCardText || "-"} / ${station.nasText || "-"}`],
          ["会话", station.sessionText || "-"],
        ].map(([key, value]) => `
          <div class="panel-kv-item">
            <div class="k">${esc(key)}</div>
            <div class="v">${esc(value)}</div>
          </div>
        `).join("")}
      </div>
    ` : '<div class="empty">暂无当前关注点位。</div>';

    document.getElementById("home-cameras").innerHTML = `
      <div class="detail-head">
        <div>
          <h3>点位状态总览</h3>
          <div class="detail-sub">这里只看摘要，不重复放控制按钮。</div>
        </div>
      </div>
      <div class="list-grid">
        ${sortCameras(data.cameras).map((camera) => `
          <article class="panel-card">
            <strong>${esc(camera.cameraName)}</strong>
            <p>${esc(`Channel ${camera.channel} · ${camera.online ? "online" : "offline"} · ${camera.streamProfileText}`)}</p>
            <div class="panel-kv">
              ${[
                ["Live", effectiveLiveLabel(camera)],
                ["电量", camera.batteryText],
                ["信号", camera.signalText],
                ["最后录像", camera.lastClipText],
              ].map(([key, value]) => `
                <div class="panel-kv-item">
                  <div class="k">${esc(key)}</div>
                  <div class="v">${esc(value)}</div>
                </div>
              `).join("")}
            </div>
          </article>
        `).join("")}
      </div>
    `;
  }

  function renderStation(data) {
    const station = data.station || {};
    const stationCameras = document.getElementById("station-cameras");
    if (stationCameras) {
      stationCameras.hidden = true;
      stationCameras.innerHTML = "";
    }
    document.getElementById("station-detail").innerHTML = `
      <div class="detail-head">
        <div>
          <h3>基站配置</h3>
          <div class="detail-sub">${esc(station.stationName || "")} · ${esc(station.stationSn || "")} · 这里只列基站本身，不重复展开下挂摄像头。</div>
        </div>
        <div class="toolbar-right">
          <button type="button" class="action-btn ${station.buzzerOn ? "danger" : "warn"} station-buzzer-toggle" data-station-sn="${esc(station.stationSn || "")}" data-action="${station.buzzerOn ? "off" : "on"}">${station.buzzerOn ? "关闭基站音响" : "开启基站音响"}</button>
        </div>
      </div>
      <div class="panel-kv">
        ${[
          ["在线状态", station.onlineText || "-"],
          ["会话", station.sessionText || "-"],
          ["TF 卡", station.tfCardText || "-"],
          ["本地存储", station.storageText || "-"],
          ["剩余空间", station.storageFreeText || "-"],
          ["NAS", station.nasText || "-"],
          ["固件", station.firmwareText || "-"],
          ["App Build", station.appBuildText || "-"],
          ["时区", station.timezoneText || "-"],
          ["IP", station.ipText || "-"],
          ["MAC", station.macText || "-"],
          ["NTP", station.ntpText || "-"],
        ].map(([key, value]) => `
          <div class="panel-kv-item">
            <div class="k">${esc(key)}</div>
            <div class="v">${esc(value)}</div>
          </div>
        `).join("")}
      </div>
    `;
  }

  function renderMessages(data) {
    const holder = document.getElementById("message-list");
    const cameraFilter = effectiveMessageCameraFilter();
    const typeFilter = appState.messageFilters.typeKey || "all";
    const dateFrom = appState.messageFilters.dateFrom || "";
    const dateTo = appState.messageFilters.dateTo || "";
    const typeOptions = [{ value: "all", label: "全部类型" }];
    const seenTypeKeys = new Set(["all"]);
    (data.messages || []).forEach((item) => {
      const key = messageTypeKey(item);
      if (seenTypeKeys.has(key)) return;
      seenTypeKeys.add(key);
      typeOptions.push({ value: key, label: messageTypeLabel(item) });
    });
    const messages = (data.messages || []).filter((item) => {
      if (cameraFilter !== "all" && item.deviceSn !== cameraFilter) return false;
      if (typeFilter !== "all" && messageTypeKey(item) !== typeFilter) return false;
      if (!matchesDateRange(item.deviceTime, dateFrom, dateTo)) return false;
      return true;
    });

    if (!messages.length) {
      holder.innerHTML = `
        <div class="filter-bar">
          <div class="filter-grid">
            <div class="filter-field">
              <label>设备</label>
              <select data-message-filter="cameraSn">
                ${cameraFilterOptions(data, "全部设备").map((item) => `<option value="${esc(item.value)}" ${item.value === cameraFilter ? "selected" : ""}>${esc(item.label)}</option>`).join("")}
              </select>
            </div>
            <div class="filter-field">
              <label>类型</label>
              <select data-message-filter="typeKey">
                ${typeOptions.map((item) => `<option value="${esc(item.value)}" ${item.value === typeFilter ? "selected" : ""}>${esc(item.label)}</option>`).join("")}
              </select>
            </div>
            <div class="filter-field">
              <label>开始日期</label>
              <input type="date" data-message-filter="dateFrom" value="${esc(dateFrom)}">
            </div>
            <div class="filter-field">
              <label>结束日期</label>
              <input type="date" data-message-filter="dateTo" value="${esc(dateTo)}">
            </div>
          </div>
          <div class="detail-head" style="margin-bottom:0;">
            <div class="filter-summary">当前筛选后没有消息。</div>
            <div class="toolbar-right">
              <button type="button" class="action-btn secondary" id="message-filter-reset">清空筛选</button>
            </div>
          </div>
        </div>
        <div class="empty">暂无消息。</div>
      `;
      return;
    }

    holder.innerHTML = `
      <div class="filter-bar">
        <div class="filter-grid">
          <div class="filter-field">
            <label>设备</label>
            <select data-message-filter="cameraSn">
              ${cameraFilterOptions(data, "全部设备").map((item) => `<option value="${esc(item.value)}" ${item.value === cameraFilter ? "selected" : ""}>${esc(item.label)}</option>`).join("")}
            </select>
          </div>
          <div class="filter-field">
            <label>类型</label>
            <select data-message-filter="typeKey">
              ${typeOptions.map((item) => `<option value="${esc(item.value)}" ${item.value === typeFilter ? "selected" : ""}>${esc(item.label)}</option>`).join("")}
            </select>
          </div>
          <div class="filter-field">
            <label>开始日期</label>
            <input type="date" data-message-filter="dateFrom" value="${esc(dateFrom)}">
          </div>
          <div class="filter-field">
            <label>结束日期</label>
            <input type="date" data-message-filter="dateTo" value="${esc(dateTo)}">
          </div>
        </div>
        <div class="detail-head" style="margin-bottom:0;">
          <div class="filter-summary">共 ${messages.length} 条消息。</div>
          <div class="toolbar-right">
            <button type="button" class="action-btn secondary" id="message-filter-reset">清空筛选</button>
          </div>
        </div>
      </div>
      ${messages.map((item) => {
      const body = `
        <div class="message-copy">
          <div class="message-head">
            <strong>${esc(item.title)}</strong>
            <span>${esc(item.deviceTime)}</span>
          </div>
          <span>${esc(item.deviceName)} · ${esc(item.deviceSn)}</span>
          <span>${esc(item.subtitle)}</span>
        </div>
      `;
      if (item.playUrl) {
        return `<article class="message-card"><a href="${esc(item.playUrl)}" target="_blank" rel="noreferrer">${body}</a></article>`;
      }
      return `<article class="message-card">${body}</article>`;
    }).join("")}
    `;
  }

  function renderEvents(data) {
    const holder = document.getElementById("event-list");
    if (!data.events.length) {
      holder.innerHTML = '<div class="empty">暂无调试事件。</div>';
      return;
    }
    holder.innerHTML = data.events.map((item) => `
      <article class="message-card">
        <div class="message-copy">
          <div class="message-head">
            <strong>${esc(item.message)}</strong>
            <span>${esc(item.ts || "")}</span>
          </div>
          <span class="mono">${esc(JSON.stringify(item.payload || {}))}</span>
        </div>
      </article>
    `).join("");
  }

  function renderPlayback(data) {
    const holder = document.getElementById("playback-list");
    const cameraFilter = effectivePlaybackCameraFilter(data);
    const dateFrom = appState.playbackFilters.dateFrom || "";
    const dateTo = appState.playbackFilters.dateTo || "";
    const clips = ((data.playback && Array.isArray(data.playback.recentClips)) ? data.playback.recentClips : [])
      .filter((clip) => {
        if (cameraFilter !== "all" && clip.cameraSn !== cameraFilter) return false;
        if (!matchesDateRange(clip.startTime, dateFrom, dateTo)) return false;
        return true;
      })
      .sort((left, right) => String(right.startTime || "").localeCompare(String(left.startTime || "")));
    const activeClip = selectedPlaybackClip(clips);

    if (!clips.length) {
      holder.innerHTML = `
        <div class="detail-head">
          <div>
            <h3>云录像回放</h3>
            <div class="detail-sub">按设备和日期筛选云录像，先筛选再看列表。</div>
          </div>
        </div>
        <div class="filter-bar">
          <div class="filter-grid">
            <div class="filter-field">
              <label>设备</label>
              <select data-playback-filter="cameraSn">
                ${cameraFilterOptions(data, "全部设备").map((item) => `<option value="${esc(item.value)}" ${item.value === cameraFilter ? "selected" : ""}>${esc(item.label)}</option>`).join("")}
              </select>
            </div>
            <div class="filter-field">
              <label>开始日期</label>
              <input type="date" data-playback-filter="dateFrom" value="${esc(dateFrom)}">
            </div>
            <div class="filter-field">
              <label>结束日期</label>
              <input type="date" data-playback-filter="dateTo" value="${esc(dateTo)}">
            </div>
          </div>
          <div class="detail-head" style="margin-bottom:0;">
            <div class="filter-summary">当前筛选后没有录像。</div>
            <div class="toolbar-right">
              <button type="button" class="action-btn secondary" id="playback-filter-reset">清空筛选</button>
            </div>
          </div>
        </div>
        <div class="empty">暂无可回放的云录像。</div>
      `;
      return;
    }

    holder.innerHTML = `
      <div class="playback-shell">
        <div class="detail-head">
          <div>
            <h3>云录像回放</h3>
            <div class="detail-sub">直接播放本地已经解码完成的 MP4，不走基站 P2P / Live 链路。</div>
          </div>
        </div>
        <div class="filter-bar">
          <div class="filter-grid">
            <div class="filter-field">
              <label>设备</label>
              <select data-playback-filter="cameraSn">
                ${cameraFilterOptions(data, "全部设备").map((item) => `<option value="${esc(item.value)}" ${item.value === cameraFilter ? "selected" : ""}>${esc(item.label)}</option>`).join("")}
              </select>
            </div>
            <div class="filter-field">
              <label>开始日期</label>
              <input type="date" data-playback-filter="dateFrom" value="${esc(dateFrom)}">
            </div>
            <div class="filter-field">
              <label>结束日期</label>
              <input type="date" data-playback-filter="dateTo" value="${esc(dateTo)}">
            </div>
          </div>
          <div class="detail-head" style="margin-bottom:0;">
            <div class="filter-summary">共 ${clips.length} 条录像。</div>
            <div class="toolbar-right">
              <button type="button" class="action-btn secondary" id="playback-filter-reset">清空筛选</button>
            </div>
          </div>
        </div>
        ${activeClip ? `
          <div class="playback-preview">
            <div class="playback-player">
              <video
                controls
                playsinline
                preload="metadata"
                crossorigin="anonymous"
                poster="${esc(activeClip.thumbUrl || "")}"
                src="${esc(activeClip.playUrl || "")}"
              ></video>
            </div>
            <div class="detail-panel">
              <div class="detail-head">
                <div>
                  <h3>${esc(activeClip.cameraName || "录像回放")}</h3>
                  <div class="detail-sub">${esc(activeClip.startTime || "")}</div>
                </div>
              </div>
              <div class="panel-kv">
                ${[
                  ["设备", activeClip.cameraName || "-"],
                  ["开始时间", activeClip.startTime || "-"],
                  ["时长", activeClip.durationText || "-"],
                  ["文件", activeClip.fileName || "-"],
                  ["播放方式", "本地 MP4 直读"],
                  ["来源", "decode/movies"],
                ].map(([key, value]) => `
                  <div class="panel-kv-item">
                    <div class="k">${esc(key)}</div>
                    <div class="v">${esc(value)}</div>
                  </div>
                `).join("")}
              </div>
              <p class="playback-note">当前回放不会去拉基站，也不会启动 P2P；这里只读取本地已经解码好的录像文件。</p>
              <div class="toolbar-right">
                <a class="action-btn secondary" href="${esc(activeClip.playUrl || "#")}" target="_blank" rel="noreferrer">新窗口打开</a>
              </div>
            </div>
          </div>
        ` : ""}
        <div class="list-grid">
          ${clips.slice(0, 24).map((clip) => `
            <article class="clip-card ${playbackClipKey(clip) === appState.playbackSelectedKey ? "is-active" : ""}">
              <button
                type="button"
                class="search-clip-pick"
                data-camera-sn="${esc(clip.cameraSn)}"
                data-playback-key="${esc(playbackClipKey(clip))}"
              >
                <div class="clip-row">
                  <img loading="lazy" src="${esc(clip.thumbUrl)}" alt="${esc(clip.title)}">
                  <div class="clip-copy">
                    <strong>${esc(clip.cameraName)}</strong>
                    <span>${esc(clip.startTime)}</span>
                    <span>${esc(clip.durationText)} · ${esc(clip.fileName)}</span>
                  </div>
                </div>
              </button>
            </article>
          `).join("")}
        </div>
      </div>
    `;
  }

  return {
    renderDeviceTree,
    renderEvents,
    renderHeader,
    renderHome,
    renderLiveDetail,
    renderLiveSplitControls,
    renderMessages,
    renderPlayback,
    renderStation,
    syncLivePrewarmButton,
  };
}
