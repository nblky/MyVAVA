# Android API Matrix

更新时间: 2026-04-09

这份文档只盯 Android App 对假云的接口需求，用来做两件事:

- 排查 Android 页面功能时，快速定位页面会打哪些接口
- 区分哪些接口已经有真实业务数据，哪些只是兼容占位

## 当前结论

- FastAPI 现在已经覆盖 Android 反编译 Retrofit 声明的 `71` 个 `POST` 接口。
- 这次新补了 `25` 个之前缺失的兼容接口，主要集中在:
  - feedback / image upload
  - share your device
  - cloud storage purchase / renew / payment
  - storage bind / unbind
  - station SN existence check
- 调试日志范围已扩大到 Android 相关前缀:
  - `/oauth`
  - `/users`
  - `/ipc`
  - `/app`
  - `/feedback`
  - `/file`
  - `/mi`
  - `/logs/item`

## 已验证的 Android 主链路

这些能力在当前测试线程里已经跑过，后续优先保证不回退:

- 登录
- 注册
- 基站在线状态恢复
- Base Station Settings:
  - 修改 base station name
  - 修改 time zone
  - 查看 device info
  - Micro SD 信息读取和 format
- 摄像头重新 sync 后恢复 live view
- Notifications 列表展示
- 摄像头回放入口展示
- Settings -> Messages 展示恢复正常

## 实测高频接口

这是之前按 Android 实际流量整理出来的高频请求，优先级最高:

| Path | Hits |
| --- | ---: |
| `/ipc/device/camera/list-sn-status` | 426 |
| `/ipc/device/camera/list-for-index` | 120 |
| `/ipc/msg/push/report-token` | 40 |
| `/ipc/device/upgrade/check-version` | 37 |
| `/ipc/msg/notice/v4/page` | 36 |
| `/users/collectAppVersion` | 15 |
| `/oauth/login` | 10 |
| `/logs/item` | 10 |
| `/app/ota/upgrade/task/latest/rule` | 9 |
| `/ipc/msg/notice/v3/count` | 9 |
| `/ipc/p2p/get-session-key` | 8 |
| `/users/detail` | 7 |
| `/ipc/msg/notice/v4/condition` | 5 |
| `/ipc/storage/camera/management/info` | 4 |
| `/oauth/logout` | 1 |

## 页面到接口映射

### 登录 / 注册 / 账户

- `POST /oauth/login`
- `POST /oauth/refresh-token`
- `POST /oauth/logout`
- `POST /users/detail`
- `POST /users/send-register-email-verify-code`
- `POST /users/send-register-sms-verify-code`
- `POST /users/email-verify`
- `POST /users/mobile-verify`
- `POST /users/email-password-register`
- `POST /users/mobile-password-register`
- `POST /users/send-reset-password-email`
- `POST /users/forget-password-send-email-code`
- `POST /users/send-reset-password-email-verify-code`
- `POST /users/send-reset-password-sms-verify-code`
- `POST /users/reset-password`
- `POST /users/update-password-by-email-code`
- `POST /users/change-password`
- `POST /users/update`
- `POST /users/collectAppVersion`
- `POST /mi/authentication/sendMailCodeForModify`

状态:

- 账户接口是实装的，数据存 SQLite。
- 测试环境当前允许“任意 6 位验证码”走注册/重置流程。

### 首页 / 设备列表 / 在线状态

- `POST /ipc/device/station/add`
- `POST /ipc/device/station/set`
- `POST /ipc/device/station/check-bind-status`
- `POST /ipc/device/station/list-bind-station`
- `POST /ipc/device/station/remove`
- `POST /ipc/device/station/is_exist_by_sn`
- `POST /ipc/device/camera/check-bind-status`
- `GET|POST /ipc/device/camera/check-bind-status-by-iot`
- `GET|POST /ipc/device/camera/add-blind`
- `POST /ipc/device/camera/set`
- `POST /ipc/device/camera/remove`
- `POST /ipc/device/camera/list-for-index`
- `POST /ipc/device/camera/list-for-my-devices`
- `POST /ipc/device/camera/list-for-mgt`
- `GET|POST /ipc/device/camera/list-for-station`
- `POST /ipc/device/camera/list-sn-status`
- `POST /ipc/device/camera/list-for-share`
- `POST /ipc/device/camera/list-speaker-volume`
- `POST /ipc/device/upgrade/check-version`
- `POST /ipc/p2p/get-session-key`
- `POST /ipc/p2p/check-session-key`
- `GET|POST /ipc/p2p/get-did`

状态:

- 基站 / 摄像头主链路是实装的。
- `list-for-share`、`list-speaker-volume` 是兼容补齐接口:
  - `list-for-share` 目前返回空列表
  - `list-speaker-volume` 返回低/中/高三档固定值

### Notifications / Messages

- `POST /ipc/msg/notice/add`
- `POST /ipc/msg/push/report-token`
- `POST /ipc/msg/notice/count`
- `POST /ipc/msg/notice/v3/count`
- `POST /ipc/msg/notice/v4/condition`
- `POST /ipc/msg/notice/v4/page`
- `POST /ipc/msg/notice/remove`

状态:

- 这组接口是实装的。
- 通知消息、缩略图字段、消息筛选已经接进当前状态层。

### Storage / 回放 / 云存储页

- `POST /ipc/storage/camera/management/info`
- `POST /ipc/storage/service/info`
- `GET|POST /ipc/connection/token/get`
- `GET|POST /connection/token/get`
- `GET|POST /token/get`
- `POST /ipc/storage/video/list`
- `POST /ipc/storage/camera/unbind/list`
- `POST /ipc/storage/video/signed/play/url`
- `POST /ipc/storage/camera/bind`
- `POST /ipc/storage/camera/unbind`
- `POST /ipc/storage/service/activate`
- `POST /ipc/storage/service/purchase`
- `POST /ipc/storage/service/purchase/list`
- `POST /ipc/storage/service/renew`
- `POST /ipc/storage/service/renew/list`
- `POST /ipc/storage/video/dailyNum`
- `POST /ipc/storage/video/delete`
- `POST /ipc/storage/video/signed/download/url`
- `POST /mi/storage/pay/checkByPayId`
- `POST /mi/storage/pay/page`
- `POST /mi/storage/pay/paypalOrder`

状态:

- 本地 TF 卡和通知链路相关的数据是实装的。
- 云存储购买/续费/支付相关接口目前是兼容占位:
  - 返回结构按 Android 模型补齐
  - 方便 App 页面不再因为 404 直接断掉
  - 还没有接入真实支付和真实云录像文件

### Share / Share Your Device

- `POST /ipc/device/share/add`
- `POST /ipc/device/share/check-receiver-mail`
- `POST /ipc/device/share/edit`
- `POST /ipc/device/share/list-invite`
- `POST /ipc/device/share/remove-device`
- `POST /ipc/device/share/remove-invite`

状态:

- 目前先做兼容占位和事件日志。
- 这组接口不会再 404，但还没有做完整的分享关系持久化。

### Feedback / Upload

- `POST /feedback/submit`
- `POST /file/upload/single`

状态:

- 已补兼容。
- `file/upload/single` 目前返回本地调试缩略图 URL，主要用于让 Android 提交流程继续走通。

## 本地扩展接口

这些不是 Android Retrofit 里声明的原始接口，但对本地调试很有用:

- `GET /`
- `GET /healthz`
- `GET /ping`
- `GET /debug/routes`
- `GET /debug/state-summary`
- `GET /debug/state`
- `GET /debug/thumbnail`
- `POST /logs/item`
- `POST /ipc/device/station/report-status`
- `POST /ipc/device/station/report-attr`

## 兼容占位接口清单

这批接口已经有路由，但当前还是“结构兼容优先”:

- `POST /feedback/submit`
- `POST /file/upload/single`
- `POST /ipc/device/camera/list-for-share`
- `POST /ipc/device/camera/list-speaker-volume`
- `POST /ipc/device/share/add`
- `POST /ipc/device/share/check-receiver-mail`
- `POST /ipc/device/share/edit`
- `POST /ipc/device/share/list-invite`
- `POST /ipc/device/share/remove-device`
- `POST /ipc/device/share/remove-invite`
- `POST /ipc/device/station/is_exist_by_sn`
- `POST /ipc/storage/camera/bind`
- `POST /ipc/storage/camera/unbind`
- `POST /ipc/storage/service/activate`
- `POST /ipc/storage/service/purchase`
- `POST /ipc/storage/service/purchase/list`
- `POST /ipc/storage/service/renew`
- `POST /ipc/storage/service/renew/list`
- `POST /ipc/storage/video/dailyNum`
- `POST /ipc/storage/video/delete`
- `POST /ipc/storage/video/signed/download/url`
- `POST /mi/authentication/sendMailCodeForModify`
- `POST /mi/storage/pay/checkByPayId`
- `POST /mi/storage/pay/page`
- `POST /mi/storage/pay/paypalOrder`

## 下一轮 Android 建议测试顺序

如果接下来继续只盯 Android，建议按这个顺序回归:

1. 登录 / 注册 / 忘记密码
2. 首页设备列表刷新
3. Base Station Settings 全项
4. Camera Settings 全项
5. Notifications / Messages / Local Playback
6. Share Your Device
7. Cloud Storage 购买页 / 订单页
8. Feedback 上传
