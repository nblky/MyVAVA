# Linux Deploy Notes

## Recommended topology

- `nginx` listens on `443`
- `FastAPI` listens on `127.0.0.1:18080`
- `legacy HTTPS` compatibility service listens on `127.0.0.1:18443`
- `CONNECT proxy` listens on `0.0.0.0:8888`

This keeps the front door stable while we gradually retire the legacy backend.

## Files in this folder

- `deploy/nginx/vava-front.conf`
  - `nginx` front-door config
  - sends requests to FastAPI first
  - falls back to legacy HTTPS when FastAPI returns `404`
- `deploy/systemd/vava-fastapi.service`
- `deploy/systemd/vava-legacy-https.service`
- `deploy/systemd/vava-connect-proxy.service`

## Suggested target layout

```text
/opt/vava-server
├── .venv
├── app
├── legacy
├── data
├── scripts
└── deploy
```

## Environment file

Copy:

```bash
cp /opt/vava-server/.env.example /opt/vava-server/deploy/env/vava-server.env
```

Then edit:

- `VAVA_FASTAPI_PORT`
- `VAVA_LEGACY_PORT`
- `VAVA_PROXY_LISTEN_PORT`
- `VAVA_DEFAULT_PASSWORD`
- `VAVA_DEFAULT_VERIFY_CODE`

Important:

- `VAVA_PROXY_UPSTREAM_PORT` should stay `443` when nginx is the front door
- the CONNECT proxy should point to nginx, not directly to FastAPI

## systemd setup

Copy service files:

```bash
sudo cp /opt/vava-server/deploy/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now vava-fastapi.service
sudo systemctl enable --now vava-legacy-https.service
sudo systemctl enable --now vava-connect-proxy.service
```

Check status:

```bash
sudo systemctl status vava-fastapi.service
sudo systemctl status vava-legacy-https.service
sudo systemctl status vava-connect-proxy.service
```

## nginx setup

Copy config:

```bash
sudo cp /opt/vava-server/deploy/nginx/vava-front.conf /etc/nginx/conf.d/vava-front.conf
sudo nginx -t
sudo systemctl reload nginx
```

## Why this layout works well

- nginx handles TLS and access logs
- FastAPI becomes the main business backend
- legacy only stays around as a compatibility fallback
- the CONNECT proxy remains isolated on `8888`, which is easier than forcing it into nginx
