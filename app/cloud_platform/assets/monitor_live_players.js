export function createLivePlayerRuntime({
  appState,
  escapeAttrSelectorValue,
  pushLiveRateSample,
  setStatus,
  visibleWallCameras,
}) {
  function destroyLivePlayer(cameraSn) {
    const player = appState.livePlayers[cameraSn];
    if (!player) return;
    try {
      player.destroy();
    } catch (_) {
    }
    delete appState.livePlayers[cameraSn];
  }

  function destroyLivePlayers() {
    Object.keys(appState.livePlayers || {}).forEach((cameraSn) => {
      destroyLivePlayer(cameraSn);
    });
    appState.livePlayers = {};
  }

  function attachPlayers() {
    visibleWallCameras(appState.data).forEach((camera) => {
      if (String(appState.wallModeSignatures[camera.cameraSn] || "").startsWith("live:")) {
        attachPlayerForCamera(camera.cameraSn);
      }
    });
  }

  function attachPlayerForCamera(cameraSn) {
    const selector = `video[data-live-src][data-camera-sn="${escapeAttrSelectorValue(cameraSn)}"]`;
    const video = document.querySelector(selector);
    if (!video) {
      destroyLivePlayer(cameraSn);
      return;
    }

    const liveSrc = String(video.dataset.liveSrc || "").trim();
    const liveTransport = String(video.dataset.liveTransport || "hls").trim().toLowerCase() || "hls";
    if (!liveSrc) {
      destroyLivePlayer(cameraSn);
      return;
    }

    const existing = appState.livePlayers[cameraSn];
    if (existing && existing.video === video && existing.liveSrc === liveSrc) {
      video.play().catch(() => {});
      return;
    }
    destroyLivePlayer(cameraSn);

    if (liveTransport !== "hls") {
      setStatus(`Unsupported live transport for ${cameraSn}: ${liveTransport}`);
      return;
    }

    const canPlayNativeHls = !!video.canPlayType && video.canPlayType("application/vnd.apple.mpegurl") !== "";
    video.controls = false;
    video.defaultMuted = true;
    video.muted = true;
    video.setAttribute("playsinline", "");
    video.setAttribute("webkit-playsinline", "true");
    video.setAttribute("disablePictureInPicture", "");
    video.setAttribute("disableRemotePlayback", "");

    if (window.Hls && window.Hls.isSupported()) {
      let networkRecoveries = 0;
      let mediaRecoveries = 0;
      let resumeTimer = null;
      let pauseTimer = null;
      const hls = new window.Hls({
        enableWorker: true,
        lowLatencyMode: true,
        backBufferLength: 5,
        liveSyncDurationCount: 2,
        liveMaxLatencyDurationCount: 5,
        maxLiveSyncPlaybackRate: 1.12,
        maxBufferLength: 6,
        maxMaxBufferLength: 10,
        maxBufferHole: 0.5,
        startFragPrefetch: true,
        manifestLoadingTimeOut: 8000,
        fragLoadingTimeOut: 12000,
        highBufferWatchdogPeriod: 2,
        nudgeOffset: 0.2,
        nudgeMaxRetry: 6,
      });

      const chaseLiveEdge = (force = false) => {
        try {
          const latency = Number(hls.latency || 0);
          const syncPos = Number(hls.liveSyncPosition || 0);
          if (!Number.isFinite(syncPos) || syncPos <= 0) return;
          const current = Number(video.currentTime || 0);
          const gap = syncPos - current;
          if (force) {
            if (!Number.isFinite(current) || gap > 1.6) {
              video.currentTime = Math.max(0, syncPos - 0.9);
            }
            return;
          }
          if (latency > 4 || gap > 2.5) {
            video.currentTime = Math.max(0, syncPos - 0.8);
          }
        } catch (_) {
        }
      };

      const scheduleResume = () => {
        if (resumeTimer) clearTimeout(resumeTimer);
        resumeTimer = window.setTimeout(() => {
          try {
            hls.startLoad(-1);
          } catch (_) {
          }
          chaseLiveEdge(true);
          video.play().catch(() => {});
        }, 350);
      };

      const fullReload = () => {
        try {
          hls.stopLoad();
        } catch (_) {
        }
        try {
          hls.loadSource(liveSrc);
        } catch (_) {
        }
        scheduleResume();
      };

      const onWaiting = () => {
        chaseLiveEdge(true);
        scheduleResume();
      };
      const onStalled = () => {
        chaseLiveEdge(true);
        scheduleResume();
      };
      const onPause = () => {
        if (pauseTimer) clearTimeout(pauseTimer);
        pauseTimer = window.setTimeout(() => {
          if (video.ended) return;
          chaseLiveEdge(true);
          video.play().catch(() => {});
        }, 220);
      };
      const onPlaying = () => {
        networkRecoveries = 0;
        mediaRecoveries = 0;
        if (pauseTimer) {
          clearTimeout(pauseTimer);
          pauseTimer = null;
        }
      };

      hls.loadSource(liveSrc);
      hls.attachMedia(video);
      hls.on(window.Hls.Events.MANIFEST_PARSED, () => {
        chaseLiveEdge(true);
        video.play().catch(() => {});
      });
      hls.on(window.Hls.Events.LEVEL_UPDATED, () => chaseLiveEdge(false));
      hls.on(window.Hls.Events.FRAG_BUFFERED, () => chaseLiveEdge(false));
      hls.on(window.Hls.Events.FRAG_LOADED, (_, data) => {
        const stats = data && data.stats ? data.stats : null;
        if (!stats || !cameraSn) return;
        const totalBytes = Number(stats.total || stats.loaded || 0);
        const started = Number((stats.loading && stats.loading.start) || stats.trequest || 0);
        const ended = Number((stats.loading && stats.loading.end) || stats.tload || 0);
        if (!totalBytes || ended <= started) return;
        pushLiveRateSample(cameraSn, (totalBytes * 8) / (ended - started));
      });
      hls.on(window.Hls.Events.ERROR, (_, data) => {
        if (!data || !data.fatal) return;
        if (data.type === window.Hls.ErrorTypes.NETWORK_ERROR && networkRecoveries < 4) {
          networkRecoveries += 1;
          scheduleResume();
          return;
        }
        if (data.type === window.Hls.ErrorTypes.MEDIA_ERROR && mediaRecoveries < 2) {
          mediaRecoveries += 1;
          try {
            hls.recoverMediaError();
            return;
          } catch (_) {
          }
        }
        fullReload();
      });
      video.addEventListener("waiting", onWaiting);
      video.addEventListener("stalled", onStalled);
      video.addEventListener("pause", onPause);
      video.addEventListener("playing", onPlaying);
      appState.livePlayers[cameraSn] = {
        video,
        liveSrc,
        destroy() {
          if (resumeTimer) clearTimeout(resumeTimer);
          if (pauseTimer) clearTimeout(pauseTimer);
          video.removeEventListener("waiting", onWaiting);
          video.removeEventListener("stalled", onStalled);
          video.removeEventListener("pause", onPause);
          video.removeEventListener("playing", onPlaying);
          try {
            hls.destroy();
          } catch (_) {
          }
        },
      };
      return;
    }

    if (canPlayNativeHls) {
      let nativeResumeTimer = null;
      const chaseNativeLive = (force = false) => {
        try {
          if (!video.seekable || !video.seekable.length) return;
          const end = video.seekable.end(video.seekable.length - 1);
          const gap = end - Number(video.currentTime || 0);
          if (!Number.isFinite(end)) return;
          if (force ? gap > 1.8 : gap > 2.8) {
            video.currentTime = Math.max(0, end - 0.8);
          }
        } catch (_) {
        }
      };
      const scheduleNativeResume = (reload = false) => {
        if (nativeResumeTimer) clearTimeout(nativeResumeTimer);
        nativeResumeTimer = window.setTimeout(() => {
          try {
            if (reload) {
              video.src = liveSrc;
              video.load();
            }
          } catch (_) {
          }
          chaseNativeLive(true);
          video.play().catch(() => {});
        }, 280);
      };
      const onLoadedMetadata = () => chaseNativeLive(true);
      const onCanPlay = () => chaseNativeLive(false);
      const onPlaying = () => chaseNativeLive(false);
      const onWaiting = () => {
        chaseNativeLive(true);
        scheduleNativeResume(false);
      };
      const onStalled = () => {
        chaseNativeLive(true);
        scheduleNativeResume(true);
      };
      const onPause = () => {
        if (video.ended) return;
        scheduleNativeResume(false);
      };
      const onError = () => {
        scheduleNativeResume(true);
      };
      video.src = liveSrc;
      video.load();
      video.addEventListener("loadedmetadata", onLoadedMetadata);
      video.addEventListener("canplay", onCanPlay);
      video.addEventListener("playing", onPlaying);
      video.addEventListener("waiting", onWaiting);
      video.addEventListener("stalled", onStalled);
      video.addEventListener("pause", onPause);
      video.addEventListener("error", onError);
      video.play().catch(() => {});
      appState.livePlayers[cameraSn] = {
        video,
        liveSrc,
        destroy() {
          if (nativeResumeTimer) clearTimeout(nativeResumeTimer);
          video.removeEventListener("loadedmetadata", onLoadedMetadata);
          video.removeEventListener("canplay", onCanPlay);
          video.removeEventListener("playing", onPlaying);
          video.removeEventListener("waiting", onWaiting);
          video.removeEventListener("stalled", onStalled);
          video.removeEventListener("pause", onPause);
          video.removeEventListener("error", onError);
          try {
            video.pause();
          } catch (_) {
          }
          try {
            video.removeAttribute("src");
            video.load();
          } catch (_) {
          }
        },
      };
      return;
    }

    setStatus("This browser does not support HLS live playback.");
  }

  return {
    destroyLivePlayer,
    destroyLivePlayers,
    attachPlayers,
    attachPlayerForCamera,
  };
}
