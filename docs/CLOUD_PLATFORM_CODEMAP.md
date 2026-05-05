# Cloud Platform Code Map

This document separates the current browser-based local cloud platform from the
older mobile-app compatibility stack, so they do not get mixed together during
future changes.

## 1. What "cloud platform" means in this repo

For this project, "cloud platform" has two different meanings:

1. Browser cloud platform
   - The web UI you open in a browser on the Mac mini.
   - Uses local decoded media files and on-demand local P2P live pipelines.
   - Main entry: `/monitor`

2. Mobile-app compatibility cloud
   - The local replacement API that the original Android/iOS apps talk to.
   - Mimics original cloud routes such as `/oauth/*`, `/ipc/*`, `/users/*`.
   - Shares the same FastAPI process and same state store.

These two surfaces share data, but they are not the same code path.

## 2. Browser cloud platform: current core files

### Main web UI

- `app/cloud_platform/web_router.py`
  - Owns the browser routes:
    - `GET /monitor`
    - `GET /monitor/data`
    - `GET /monitor/assets/*`
  - Thin browser router only.

- `app/cloud_platform/monitor_bundle.py`
  - Splits the browser UI into:
    - shell html
    - extracted css asset
    - extracted js asset
  - Keeps bundle parsing/cache logic in one place.

- `app/cloud_platform/payloads.py`
  - Orchestrates browser-facing view models from shared store data.

- `app/cloud_platform/message_payloads.py`
  - Message list / clip card payload shaping.

- `app/cloud_platform/camera_payloads.py`
  - Camera card payload shaping, recent clip stitching, live state enrichment.

- `app/cloud_platform/station_payloads.py`
  - Station payload shaping and runtime summary counters.

- `app/cloud_platform/live_state.py`
  - Browser-facing live state helpers:
    - control.json reading
    - HLS manifest parsing
    - live status shaping

- `app/cloud_platform/media_urls.py`
  - Shared URL builders for:
    - local thumbnails
    - local clip mp4 playback
    - live HLS playback

- `app/api/web.py`
  - Now only a compatibility wrapper that re-exports the extracted browser router.

- `app/cloud_platform/monitor_page.html`
  - Lightweight shell page for the browser cloud platform.
  - Loads the extracted css/js assets.

- `app/cloud_platform/monitor_body.html`
  - Standalone page body template used by the browser cloud platform shell.

- `app/cloud_platform/assets/`
  - Browser static assets and front-end functional modules.
  - Current split includes:
    - `monitor.css`
    - `monitor_app.js`
    - `monitor_auth.js`
    - `monitor_controller.js`
    - `monitor_handlers.js`
    - `monitor_live_control.js`
    - `monitor_constants.js`
    - `monitor_state.js`
    - `monitor_utils.js`
    - `monitor_api.js`
    - `monitor_domain.js`
    - `monitor_render.js`
    - `monitor_live_wall.js`
    - `monitor_live_players.js`

- `app/cloud_platform/assets/monitor_app.js`
  - Browser platform composition root only.
  - Wires together:
    - app state
    - API client
    - domain helpers
    - render module
    - live wall module
    - controller module
    - handlers module

- `app/cloud_platform/assets/monitor_controller.js`
  - Owns browser page orchestration:
    - refresh loop
    - tab switching
    - page rendering dispatch
    - monitor data refresh

- `app/cloud_platform/assets/monitor_auth.js`
  - Owns browser auth/session flow:
    - local auth restore
    - login / logout form handling
    - login screen/app shell switching
    - auth storage persistence

- `app/cloud_platform/assets/monitor_handlers.js`
  - Owns browser UI event binding for:
  - Uses one-time delegated listeners instead of rebinding after each render.
    - live controls
    - playback/message filters
    - fullscreen / snapshot / browser recording
    - station buzzer toggle

- `app/cloud_platform/assets/monitor_live_control.js`
  - Owns browser live control orchestration:
    - start / stop single live
    - release background live sessions
    - visible live batch start / stop
    - optimistic live-only UI refresh before full data polling returns

- `app/cloud_platform/assets/monitor_render.js`
  - Owns HTML rendering for:
    - header
    - device tree
    - home / live detail / playback / messages / station

- `app/cloud_platform/monitor_source.html`
  - Legacy full browser bundle source kept as fallback/archive source during the refactor.

### Browser runtime media/control endpoints

- `app/api/system.py`
  - Browser-only `/debug/*` routes:
    - `/debug/thumbnail(.png)`
    - `/debug/cloud-play.mp4`
    - `/debug/live/{camera}/{asset}`
    - `/debug/live/control/start`
    - `/debug/live/control/stop`
    - `/debug/live/control/buzzer`
  - These are part of the browser cloud platform runtime.

### Standalone browser cloud platform app

- `app/cloud_platform/app.py`
  - Separate FastAPI entry for the browser cloud platform.
  - Reuses:
    - `auth_router`
    - `system_router`
  - Mounts browser web routes from:
    - `app/cloud_platform/web_router.py`

- `scripts/run_cloud_platform.sh`
  - Starts the standalone browser cloud platform on a dedicated port.

### Browser live pipeline scripts

- `scripts/run_live_hls.sh`
  - Builds live path:
    - base station P2P
    - local bridge
    - ffmpeg
    - HLS files

- `scripts/stop_live_hls.sh`
  - Stops per-camera live pipeline.

- `scripts/run_ppcs_bridge.sh`
  - Launches PPCS bridge binary.

- `scripts/prewarm_live.sh`
  - Manual helper for warm live pipelines.

### Browser/local media data

- `data/cloud_media/decode/movies`
  - Local decoded mp4 files for playback page.

- `data/cloud_media/decode/imgs`
  - Local generated thumbnails for playback page.

- `data/live_hls`
  - Browser live HLS output and per-camera control state.

## 3. Mobile-app compatibility cloud: current core files

### App entry process

- `app/main.py`
  - Starts one FastAPI app and mounts both browser and mobile-app routers.
  - Adds request logging middleware for app-compatible routes.

### App-compatible API routers

- `app/api/auth.py`
  - `/oauth/*`
  - `/users/*`

- `app/api/device.py`
  - station/camera inventory and status routes
  - OTA/version check style routes

- `app/api/message.py`
  - notice/message list, count, delete, push token reporting

- `app/api/p2p.py`
  - session key and DID style compatibility routes

- `app/api/android_compat.py`
  - extra Android-facing compatibility routes not covered elsewhere

- `app/api/system.py`
  - app-compatible storage/token routes:
    - `/ipc/storage/*`
    - `/ipc/connection/token/get`
    - `/connection/token/get`
    - `/token/get`

## 4. Shared state layer used by both sides

- `app/store.py`
  - Central shared state and persistence layer.
  - Used by:
    - browser cloud platform
    - mobile-app compatibility cloud
  - Owns:
    - auth/user records
    - station/camera state
    - message records
    - cloud media records
    - request logs

This is the main shared dependency between the browser UI and the app-compatible
cloud.

## 5. Older legacy stack: not the browser cloud platform

- `legacy/mock_sunvalley_https.py`
  - Older HTTPS mock service / fallback stack.

- `legacy/mock_sunvalley_connect_proxy.py`
  - Older connect proxy for app traffic.

- `legacy/*.crt`, `legacy/*.key`
  - TLS material for the app-facing interception/mocking side.

These are not the browser cloud platform UI.

## 6. Important mixed points that still exist today

### Mixed point A: `app/api/system.py`

This file contains both:

- browser runtime `/debug/*`
- app-compatible `/ipc/storage/*` and token routes

So this module is a runtime/media bridge, not a pure "web module".

### Mixed point B: `app/store.py` imports a helper from `legacy/`

- `app/store.py` dynamically loads:
  - `legacy/vava_station_ctl.py`

This does not mean the browser UI is using the old HTTPS mock server, but it
does mean the shared state layer still reuses code from the `legacy` folder.

### Mixed point C: one FastAPI process serves both surfaces

The current app process exposes:

- browser cloud platform routes
- app-compatible routes

This is operationally convenient, but conceptually easy to confuse.

## 7. Clean mental model to use from now on

If the task is about browser cloud platform behavior, start from:

1. `app/cloud_platform/monitor_page.html`
2. `app/cloud_platform/monitor_bundle.py`
3. `app/cloud_platform/web_router.py`
4. browser-facing `/debug/*` routes in `app/api/system.py`
5. live scripts under `scripts/`

If the task is about Android/iOS original app behavior, start from:

1. `app/api/auth.py`
2. `app/api/device.py`
3. `app/api/message.py`
4. `app/api/p2p.py`
5. app-compatible parts of `app/api/system.py`
6. `app/store.py`

If the task is about the older certificate/proxy mock stack, start from:

1. `legacy/mock_sunvalley_https.py`
2. `legacy/mock_sunvalley_connect_proxy.py`
3. nginx/cert deployment files

## 8. Recommended refactor order

To reduce confusion without breaking behavior, split in this order:

1. Move browser `/debug/*` routes out of `app/api/system.py`
   - target file: `app/api/web_runtime.py`

2. Keep app-compatible storage/token routes in a separate router
   - target file: `app/api/storage_compat.py`

3. Move `legacy/vava_station_ctl.py` helper code that is still actively reused
   into a neutral location such as:
   - `app/native/station_helper.py`
   or
   - `app/runtime/station_helper.py`

4. Leave old HTTPS/proxy mock code under `legacy/` only for app interception
   and certificate-based compatibility.

After that split, "browser cloud platform" and "fake cloud compatibility" will
still share `store.py`, but the route and runtime ownership will be much easier
to reason about.
