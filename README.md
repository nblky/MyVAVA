# VAVA Server

VAVA Cam pro 云服务本地开发项目，提供基站/摄像头的设备注册、认证、消息推送、P2P 打洞、RTMP 直播流转发等 IoT 云服务能力。
项目已开始用的gpt分析的，后面用deepseek v4重新梳理了下。
## 目录结构

- `app/` — FastAPI 服务主程序，包含 API 路由、存储层、云平台业务逻辑
- `app/api/` — REST API 路由（认证、设备、消息、P2P、运行时共享）
- `app/store_domains/` — 存储域服务（按业务域拆分）
- `app/cloud_platform/` — 云平台核心：直播、媒体、消息推送
- `fake_cloud_app/` — 给手机 App / 基站 / 摄像头使用的假云控独立项目入口
- `legacy/` — 旧的兼容 HTTPS mock、CONNECT 代理、证书、基站辅助脚本
- `data/` — 运行时状态文件 (`sunvalley_state.json`、`sunvalley_state.sqlite3`)
- `scripts/` — 启动/停止/工具脚本
- `deploy/` — 部署模板（nginx 前门、systemd 服务、证书配置模板）
- `docs/` — 接口矩阵和功能梳理文档
- `native/` — C++ PPCS 协议桥接源码
- `bin/` — 编译好的原生二进制（PPCS bridge）

## 快速开始

### 首次启动

首次 clone 后必须先生成 TLS 证书，再创建虚拟环境：

```bash
# 1. 生成本地 TLS 证书（必须，首次必执行）
bash scripts/regenerate_local_cert.sh

# 2. 创建虚拟环境
bash scripts/setup_venv.sh

# 3. 启动完整服务栈（FastAPI + legacy HTTPS + CONNECT 代理 + nginx 前门）
bash scripts/start_project.sh full

# 或只启动 FastAPI
bash scripts/start_project.sh fastapi
```

启动后访问：

- API 服务：`http://127.0.0.1:18080`
- API 文档：`http://127.0.0.1:18080/docs`
- nginx HTTPS 前门：`https://127.0.0.1` (端口 443)
- legacy HTTPS fallback：`https://127.0.0.1:18443`

### 停止服务

```bash
bash scripts/stop_project.sh
```

### macOS 本地前门

- nginx 配置：`deploy/nginx/macos/vava-front.conf`
- 本地多域名证书配置：`deploy/certs/vava_local_multi_san.cnf`
- 本地根 CA 配置：`deploy/certs/vava_local_root_ca.cnf`
- 重签本地证书：`bash scripts/regenerate_local_cert.sh`
- 启动 nginx 前门：`bash scripts/macos/run_nginx_front.sh`
- 重载 nginx 前门：`bash scripts/macos/reload_nginx_front.sh`
- 停止 nginx 前门：`bash scripts/macos/stop_nginx_front.sh`

## TLS 证书

本地 TLS 采用"根 CA + 服务器叶子证书"链路。

- 根 CA：`legacy/sunvalley_local_root_ca.crt`
- 服务器证书：`legacy/sunvalley_multi_san.crt` / `legacy/sunvalley_multi_san_fullchain.crt`

SAN 覆盖域名：`*.sunvalleycloud.com`、`sunvalleycloud.com`、`*.vava.com`、`vava.com`、`mi-api-pro.sunvalleycloud.com`、`iot-api.sunvalleycloud.com`、`h5.sunvalleycloud.com`、`localhost`、`127.0.0.1`

克隆后不包含证书文件。首次启动前运行 `bash scripts/regenerate_local_cert.sh` 即可生成完整证书链。证书过期或换域名后重新执行即可。

## Linux 部署

- 部署指南：`deploy/LINUX_DEPLOY.md`
- 打包说明：`deploy/RELEASE_PACKAGING.md`
- nginx 前门：`deploy/nginx/vava-front.conf`
- systemd 服务：`deploy/systemd/`

## API 覆盖

完整 Android 接口覆盖表见 `docs/ANDROID_API_MATRIX.md`，当前 FastAPI 已覆盖 Android 反编译 Retrofit 声明的 71 个 POST 接口。

## 项目文档

- 假云控说明：`fake_cloud_app/README.md`
- 架构：`fake_cloud_app/docs/ARCHITECTURE.md`
- 流程：`fake_cloud_app/docs/FLOWS.md`
- 重构计划：`fake_cloud_app/docs/REFACTOR_PLAN.md`
- 代码地图：`docs/CLOUD_PLATFORM_CODEMAP.md`
