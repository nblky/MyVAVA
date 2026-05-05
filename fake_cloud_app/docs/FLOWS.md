# 假云控关键流程

下面只梳理“手机 App / 基站 / 摄像头”这条链。

## 1. 登录与鉴权

### App 登录

1. App 调 `/oauth/login`
2. 服务端生成或复用 `access_token` / `refresh_token`
3. token 写入 `auth_tokens`
4. 用户资料写入或读取 `users`
5. App 后续带 token 调设备与消息接口

### 基站登录

1. 基站使用 `sn_password` 模式调 `/oauth/login`
2. 请求里通常会带：
   - `auth_type = sn_password`
   - `sn`
   - `client_id`
   - `client_secret`
3. 服务端返回 token
4. 基站用这个 token 上报状态与属性

## 2. 基站上线与状态同步

### 基站状态

1. 基站周期性调 `/ipc/device/station/report-status`
2. 服务端更新：
   - `stations.status_json`
   - `kv_state.stations`
3. 这里能看到：
   - `session`
   - 存储状态
   - 空间占用
   - camera status list

### 基站属性

1. 基站调 `/ipc/device/station/report-attr`
2. 服务端更新：
   - `stations.attr_json`
   - 摄像头属性 `cameras.attr_json`
3. 常见字段：
   - 固件版本
   - did/init 信息
   - 码率 / 分辨率
   - arming / pir / speaker 等配置

## 3. 摄像头配对

### 正常配对链

1. 基站发 `/ipc/device/camera/check-bind-status-by-iot`
2. 服务端为该摄像头预留 `pairing_slots`
3. 服务端写入 `pairing_sessions`
4. App 或设备继续发 `/ipc/device/camera/set`
5. 服务端把摄像头正式写入 `cameras`
6. 配对成功后：
   - `active_flag = 1`
   - `pending_pairlist_flag = 0`

### 关键字段

- `cameraSn` / `deviceSn`
- `channel` / `slotIndex`
- `stationSn`
- `cameraName`

### 已修正问题

`/ipc/device/camera/check-bind-status-by-iot` 现在同时兼容：

- `cameraSn`
- `deviceSn`
- `sn`

避免因为字段名差异把槽位配错。

## 4. 摄像头删除

1. App 调 `/ipc/device/camera/remove`
2. 服务端删除 `cameras` 表对应记录
3. 同时写入 `cameraTombstones`
4. 后续如果基站还在上报旧摄像头状态：
   - 不允许自动重建
   - 避免“删了又冒出来”

### 重新配对

只有显式重新配对成功时，才会清掉对应 tombstone。

## 5. 改名流程

### 摄像头改名

接口：

- `POST /ipc/device/camera/set`

典型请求体：

```json
{
  "cameraName": "大门口",
  "cameraSn": "64XIJEE3QC5956B7B53253EB6"
}
```

落点：

- `cameras.camera_name`
- `kv_state.cameras[sn].cameraName`

### 基站改名

接口：

- `POST /ipc/device/station/set`

典型请求体：

```json
{
  "stationName": "VAVA摄像头基站",
  "stationSn": "64XI7DE3Q2115F3BBF02F9A80"
}
```

落点：

- `stations.station_name`
- `stations.device_name`
- `kv_state.stations[sn].stationName`

## 6. 设备列表与首页

App 会高频调用：

- `/ipc/device/camera/list-for-index`
- `/ipc/device/camera/list-for-station`
- `/ipc/device/camera/list-sn-status`

这些接口本质上是在读：

- `stations`
- `cameras`
- `kv_state`

它们本身通常不修改状态，但会直接暴露“库里当前认定的设备信息”。

## 7. P2P 与 live 前置链路

当前假云里，App 侧 live 相关前置主要依赖：

- `/ipc/p2p/get-session-key`
- `/ipc/p2p/check-session-key`
- `/ipc/connection/token/get`

这里的职责不是直接出画面，而是：

1. 让 App 获取会话密钥
2. 让基站/摄像头链路被唤醒
3. 让后续 live 或回放请求能继续走下去

当前设备状态里常见几个观察点：

- `online`
- `wakeup`
- `video`

含义可以粗略理解成：

- `online = 1`
  - 设备在线
- `wakeup = 1`
  - 设备已被唤醒
- `video = 1`
  - 当前视频链路在跑

## 7.5 DID 发放

假云控里现在把 DID 当成“用户和基站之间的云侧绑定凭据”处理：

1. 用户创建后，绑定基站
2. 服务端按 `user_id + station_sn` 生成 DID
3. 生成前会检查当前库里已有 DID，避免冲突
4. 结果写入：
   - `kv_state.stations[sn].did`
   - `kv_state.stations[sn].didUserId`
   - `stations.did_json`
5. 如果基站 owner 变了，会重新给新的 owner 补 DID

补充说明：

- 基站设备自己上报的 DID / init 会落到 `reportedDid`
- App 取 DID 默认走云侧发放的 `did`

## 8. 消息与回放

### 消息

消息相关接口主要在：

- `/ipc/msg/notice/count`
- `/ipc/msg/notice/v4/page`
- `/ipc/msg/notice/remove`

数据主要落在：

- `kv_state.messages`
- `kv_state.messageCounter`

### 回放

回放当前主要分两类：

- App 看本地存储 / 本地解码产物
- App 看“云存储”样式的兼容输出

相关接口包括：

- `/ipc/storage/video/dailyNum`
- `/ipc/storage/video/list`
- `/ipc/storage/video/delete`
- `/ipc/storage/video/signed/download/url`

## 9. 当前最重要的分界线

假云控职责到这里为止：

- 账户与设备关系
- 基站与摄像头绑定
- 改名、删除、消息、回放索引
- P2P / live 的前置握手与状态承接

浏览器云平台不要再直接塞回这层里。
