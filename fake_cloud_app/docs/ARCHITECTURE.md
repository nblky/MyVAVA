# 假云控架构说明

## 1. 项目边界

本项目只负责“官方 App 侧假云”。

明确不混入下面这些内容：

- 浏览器云平台页面
- 浏览器直播播放器体验
- Web 端导航、分屏、实时预热 UI
- ZLMediaKit Web 适配层

这些内容应该继续放在浏览器云平台项目里。

## 2. 当前运行结构

当前实际链路可以理解成：

1. App / 基站 / 摄像头 发请求
2. 本地 TLS 前门或直连 `18080`
3. `fake_cloud_app` 负责应用装配
4. 路由复用 `app/api/*`
5. 状态持久化进入 `app/store.py`
6. SQLite 与 `kv_state` 提供运行态和缓存态

## 3. 当前模块分工

### 入口层

- `fake_cloud_app/app.py`
- `fake_cloud_app/factory.py`
- `fake_cloud_app/http.py`

职责：

- 装配 FastAPI
- 注册中间件
- 注册异常输出
- 汇总路由

### 业务接口层

- `app/api/auth.py`
- `app/api/device.py`
- `app/api/message.py`
- `app/api/p2p.py`
- `app/api/runtime_shared.py`
- `app/api/android_compat.py`

职责：

- 接收 App / 基站请求
- 解析 payload
- 调用 store 层更新状态
- 输出兼容官方风格的响应

### 状态与持久化层

- `app/store.py`
- `app/config.py`

职责：

- 用户、token、设备、绑定关系
- 基站状态与摄像头状态
- pairing session / pairing slot
- request log
- kv_state 缓存镜像

## 4. 当前数据库主表

- `users`
  - 账户信息
- `auth_tokens`
  - access token / refresh token
- `stations`
  - 基站主档
- `cameras`
  - 摄像头主档
- `user_station_bindings`
  - 账户与基站归属关系
- `pairing_sessions`
  - 配对会话轨迹
- `pairing_slots`
  - 基站槽位占用状态
- `request_logs`
  - 请求探针日志
- `kv_state`
  - App 读取友好的运行态缓存

## 5. 已经确认的重要规则

### 删除保护

摄像头删除后不能被基站后续 `report-status` / `report-attr` 自动复活。

当前已通过 `cameraTombstones` 实现保护：

- 删除摄像头时写入 tombstone
- 基站状态上报命中 tombstone 时，不再自动恢复设备
- 只有显式重新配对时，才清除 tombstone 并重新入库

### 槽位绑定

基站的摄像头槽位由 `pairing_slots` 管理：

- `slot_index` 表示通道
- `active_flag` 表示已激活
- `pending_pairlist_flag` 表示正在配对中的临时态

### 改名链路

- 摄像头改名通过 `/ipc/device/camera/set`
- 基站改名通过 `/ipc/device/station/set`

## 6. 建议的下一步模块拆分

建议按下面顺序拆：

1. `domain/auth`
   - 登录、token、用户资料
2. `domain/station`
   - 基站注册、上线、属性同步
3. `domain/camera`
   - 摄像头档案、改名、删除、状态同步
4. `domain/pairing`
   - 配对会话、槽位分配、删除保护
5. `domain/message`
   - 告警、消息分页、删除
6. `domain/playback`
   - 本地录像、云录像索引、按日统计
7. `infra/sqlite`
   - 统一 DAO / 仓储层

## 7. 为什么先拆入口层

因为当前假云已经在用，先直接大拆业务层风险太高。

先把入口层独立出来，有几个好处：

- 不影响现有 `18080` 服务
- 能先把“假云控”和“浏览器云平台”概念彻底分开
- 后面拆业务层时，不会继续把两条线搅在一起
