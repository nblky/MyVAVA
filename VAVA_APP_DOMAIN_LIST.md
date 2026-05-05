# VAVA App Domain List

## 当前项目最常用域名

- `iot-api.sunvalleycloud.com`
  - 当前本地 fake cloud 主 API 链
- `mi-api-pro.sunvalleycloud.com`
  - APK 里最像正式 App API 基址
- `storage.sunvalleycloud.com`
  - cloud 视频云存储 / 上传链重点域名
- `third-api-dev.sunvalleycloud.com`
  - 真实 `libsvpush.so` 里出现的 token / 上传辅助域名
- `mqtt-server.sunvalleycloud.com`
  - 基站真实 MQTT broker 生产域名

## 使用建议

- 做手机 App 兼容时，优先看：
  - `mi-api-pro.sunvalleycloud.com`
  - `h5.sunvalleycloud.com`
- 做基站上报 / 事件 / 设备管理时，优先看：
  - `iot-api.sunvalleycloud.com`
- 做 cloud 视频上传 / token / 存储时，优先看：
  - `storage.sunvalleycloud.com`
  - `third-api-dev.sunvalleycloud.com`
  - 记忆补充：
    - app 侧的 `/ipc/storage/video/*` 和 `/ipc/storage/service/*` 属于同一类 HTTP 查询/播放面
    - 它们和 MQTT broker 通道分离，不要混成一条链
- 做 MQTT 时，优先看：
  - `mqtt-server.sunvalleycloud.com`

## Runtime-observed first-party domain

- `mi-api-pro.sunvalleycloud.com`
  - observed repeatedly in Android CONNECT logs
  - matches the production API base URL in the APK

## App code first-party production domains

- `mi-api-pro.sunvalleycloud.com`
- `h5.sunvalleycloud.com`
- `warranty.vava.com`

## Current base-station / local-cloud chain domain

- `iot-api.sunvalleycloud.com`
  - not found in the Android APK production URL table
  - used by the base station and by the existing local mock certificate / cloud chain

## Real binary / firmware domains confirmed on 2026-04-10

- `third-api-dev.sunvalleycloud.com`
  - hard evidence in real `libsvpush.so`
  - used by `/connection/token/get`
  - belongs to the RTMP upload token chain, not the main login API
- `mqtt-server-dev.sunvalleycloud.com`
  - hard evidence in real `Ppcs_vava`
- `mqtt-server-sit.sunvalleycloud.com`
  - hard evidence in real `Ppcs_vava`
- `mqtt-server-test.sunvalleycloud.com`
  - hard evidence in real `Ppcs_vava`
- `mqtt-server-demo.sunvalleycloud.com`
  - hard evidence in real `Ppcs_vava`
- `mqtt-server.sunvalleycloud.com`
  - hard evidence in real `Ppcs_vava`

## Runtime / local-override domains currently important in this project

- `iot-api.sunvalleycloud.com`
  - current local fake-cloud main chain
- `third-api-dev.sunvalleycloud.com`
  - user has already prepared local DNS hijack for token/upload testing
- `storage.sunvalleycloud.com`
  - user has already prepared local DNS hijack for cloud-storage path testing
  - keep as a project-relevant runtime domain even though it is not yet fully re-confirmed from the latest binary notes

## Current local wildcard certificate coverage

- `*.sunvalleycloud.com`
- `sunvalleycloud.com`
- `*.vava.com`
- `vava.com`
- project-important names to remember for cert/debug:
  - `mi-api-pro.sunvalleycloud.com`
  - `iot-api.sunvalleycloud.com`
  - `h5.sunvalleycloud.com`
  - `third-api-dev.sunvalleycloud.com`
  - `storage.sunvalleycloud.com`

## App code non-production domains

- `mi-api-dev.sunvalleycloud.com`
- `mi-api-test.sunvalleycloud.com`
- `mi-api-sit.sunvalleycloud.com`
- `mi-api-demo.sunvalleycloud.com`
- `mi-api-uat.sunvalleycloud.com`
- `h5-dev.sunvalleycloud.com`
- `h5-test.sunvalleycloud.com`
- `h5-sit.sunvalleycloud.com`
- `h5-demo.sunvalleycloud.com`
- `h5.uat.sunvalleycloud.com`
- `www-uat.vava.com`

## Excluded from this cleaned list

- Google / Huawei / Apple / Tencent / Xiaomi / ByteDance / Alibaba / map / push / analytics endpoints
- generic phone system traffic that appeared only because the handset was using a whole-device HTTP proxy

## MQTT 域名定位补充

- `mqtt-server-dev.sunvalleycloud.com`
- `mqtt-server-sit.sunvalleycloud.com`
- `mqtt-server-test.sunvalleycloud.com`
- `mqtt-server-demo.sunvalleycloud.com`
- `mqtt-server.sunvalleycloud.com`
  - 这 5 个更像同一类 MQTT broker 的多环境入口，不像 5 套不同业务
  - 当前基站实连的是生产域名 `mqtt-server.sunvalleycloud.com`
  - 当前静态/动态证据更支持：
    - MQTT 是基站侧 broker 通道
    - App 侧消息列表 / cloud 列表 / notice settings 主要走 HTTP API
    - Android 系统通知走 FCM，不是手机自己直连 MQTT
  - 2026-04-11 进一步实锤：
    - `Ppcs_vava` 只有 `mosquitto_subscribe`，没有 `mosquitto_publish`
    - broker 连接固定启用 TLS 1.2
    - CA 路径硬编码为：
      - `/etc_ro/gd_bundle-g2-g1.crt`
    - 线程入口：
      - `0x411740 -> 0x41c938 -> pthread_create(..., 0x445c40, NULL)`
      - rodata 标签：`init_mqttclint`
    - MQTT 与 cloud service 分开拉起：
      - 下一步 `0x411750 -> 0x41c9c0 -> pthread_create(..., 0x412698, NULL)`
      - rodata 标签：`init_cloudservice`
    - 当前 `user_data` 结构里已看到：
      - topic = `iot/get/%s/%s`
      - qos = `1`
      - message handler = `0x445b28`
    - `mosquitto_new(clientId=0x744d24, clean_session=1, userdata=sp+0xe0)`
    - `0x744d24` 已补实来自 `0x41e4a8` 的 EEPROM 读取链：
      - 前 32 字节复制到 `0x744d24`
      - 后 32 字节复制到 `0x744d5c`
    - 已静态看到的 broker/端口映射：
      - `mqtt-server.sunvalleycloud.com` -> `9903`
      - `mqtt-server-demo.sunvalleycloud.com` -> `9901`
      - `mqtt-server-test.sunvalleycloud.com` -> `9903`
      - `mqtt-server-sit.sunvalleycloud.com` -> `9902`
      - `mqtt-server-dev.sunvalleycloud.com` -> `9904`
    - Android APK `jadx_src/sources` 用精确关键字再扫：
      - `mqtt`
      - `mosquitto`
      - `iot/get`
      - `mqtt-server`
      - 均无命中，进一步支持 App 不直连 MQTT broker
    - 订阅 topic 模板硬编码为：
      - `iot/get/%s/%s`
    - 第一段与硬编码 `f_code` 同源
    - 第二段与 `clientId` 同源，强烈像基站自身身份串
    - 当前固件 `on_message` 只看到日志回调，未见 payload 直达业务解析
