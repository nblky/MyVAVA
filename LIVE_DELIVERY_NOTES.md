# Live Delivery Notes

## Current Browser Live Chain

- Browser cloud platform live is currently:
  `PPCS/P2P -> ppcs_bridge -> ffmpeg -> HLS (index.m3u8 + seg_xxxxxx.ts) -> browser`
- HLS segments are rolling files, not permanent recordings.
- When no RAM disk is mounted, the default live root is:
  `./data/live_hls`

## RAM Disk Support

- Added configurable live root:
  `VAVA_LIVE_HLS_ROOT`
- If `VAVA_LIVE_HLS_ROOT` is unset and macOS RAM disk path exists, the system now auto-prefers:
  `/Volumes/VAVA_LIVE_RAM/live_hls`
- This path is resolved dynamically for browser live state and live asset serving.

### Mount Command

```bash
./scripts/mount_live_ramdisk.sh
```

Expected output:

```bash
mounted /Volumes/VAVA_LIVE_RAM/live_hls
export VAVA_LIVE_HLS_ROOT=/Volumes/VAVA_LIVE_RAM/live_hls
```

### Important Behavior

- New live sessions will write HLS rolling segments to RAM disk when mounted.
- Existing live sessions that started before RAM disk mount stay on their old path until stopped and started again.

## Browser Live Backend Switch

- Browser live URL/transport is now configuration-driven.
- Default values:
  - `VAVA_BROWSER_LIVE_BACKEND=builtin_hls`
  - `VAVA_BROWSER_LIVE_TRANSPORT=hls`
  - `VAVA_BROWSER_LIVE_URL_TEMPLATE=/monitor/live/{camera_sn}/index.m3u8`
- This is the compatibility seam for later ZLMediaKit migration.

## Relevant Scripts

- Browser cloud platform:
  `./scripts/run_cloud_platform.sh`
- Main local fake-cloud/runtime API:
  `./scripts/run_fastapi.sh`
- Start one browser live session:
  `./scripts/run_live_hls.sh`
- Stop one browser live session:
  `./scripts/stop_live_hls.sh`
- Mount RAM disk:
  `./scripts/mount_live_ramdisk.sh`

## ZLMediaKit Fit

### What It Is Good At

- ZLMediaKit is a strong candidate for the browser live distribution layer.
- Official docs/repo show it supports:
  RTSP / RTMP / HLS / HTTP-FLV / WebSocket-FLV / HTTP-fMP4 / WebRTC
- It also supports low-latency delivery, hooks, auth, on-demand pull, and auto-stop when nobody is watching.

### What It Is Not

- `pymkui` is only a management UI for ZLMediaKit.
- It is not a drop-in replacement for the current VAVA fake cloud or app compatibility stack.
- It does not solve VAVA app protocol emulation by itself.

## Recommended Layering

### Keep Separate

- Fake cloud / app compatibility:
  login, cloud record list, notifications, app API compatibility
- Browser cloud platform:
  local web UI for Mac/iPhone browser use
- Stream distribution backend:
  current HLS helper now, ZLMediaKit later if adopted

### Recommended Migration Order

1. Keep current browser live chain stable and move HLS rolling files to RAM disk.
2. If lower latency is still not enough, replace only the browser live distribution layer with ZLMediaKit.
3. Keep fake cloud endpoints unchanged while browser live switches to:
   `PPCS/P2P -> local repack/push -> ZLMediaKit -> HTTP-FLV/WebRTC/fMP4`
4. Evaluate WebRTC only after upstream wake/start latency is understood, because ZLMediaKit cannot remove the base station wake-up delay by itself.

## Practical Conclusion

- For immediate benefit:
  RAM disk + current HLS chain
- For next-stage browser live:
  ZLMediaKit as browser-only media server
- Not recommended:
  replacing the whole fake cloud with PyMKUI or treating ZLMediaKit as the app protocol layer
