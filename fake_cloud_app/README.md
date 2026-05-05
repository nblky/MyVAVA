# VAVA Fake Cloud Control

这个目录是“给手机 App / 基站使用的假云控项目”入口层。

它和浏览器云平台是两条线：

- 假云控
  - 服务对象是手机 App、基站、摄像头
  - 目标是复刻官方云接口、维持设备状态、承接配对和消息链路
- 浏览器云平台
  - 服务对象是电脑浏览器
  - 目标是做你自己的 Web 控制台和直播/回放体验
  - 当前代码在 `app/cloud_platform/`

## 当前阶段

这是第一阶段抽离：

- 已把假云控的应用装配层单独放到 `fake_cloud_app/`
- 现有业务模块仍复用 `app/api/*.py`、`app/store.py`、`app/config.py`
- `app/main.py` 保留为兼容入口，避免影响现在 `18080` 的启动方式

也就是说，现在是“入口和文档先独立，业务继续复用”，这样风险最小，后面再继续拆服务层和存储层。

## 目录

- `app.py`
  - 独立项目入口
- `factory.py`
  - 假云控应用装配
- `http.py`
  - 请求日志、中间件、异常输出
- `docs/ARCHITECTURE.md`
  - 架构边界和模块拆分建议
- `docs/FLOWS.md`
  - 关键业务流程
- `docs/REFACTOR_PLAN.md`
  - 后续重构路线

## 当前复用模块

- 认证与账户：`app/api/auth.py`
- 设备与基站/摄像头：`app/api/device.py`
- 消息：`app/api/message.py`
- P2P / DID / session key：`app/api/p2p.py`
- 运行时共享接口：`app/api/runtime_shared.py`
- Android 兼容接口：`app/api/android_compat.py`
- 状态库：`app/store.py`
- 配置：`app/config.py`

## 启动

兼容当前方式：

```bash
../scripts/run_fastapi.sh
```

或者直接用新项目入口：

```bash
../scripts/run_fake_cloud_app.sh
```

## DID 绑定

假云控里现在把 DID 按“用户 + 基站”关系发放：

- 用户绑定基站时，为该 `user_id + station_sn` 生成唯一 DID
- 同一用户新增另一台基站，会再生成新的 DID
- 若基站 owner 变化，会按新的 owner 重新补 DID
- 基站自己上报的原始 DID 会单独保存在 `reportedDid`，不直接覆盖云侧绑定 DID

可用脚本：

```bash
python3 ../scripts/generate_station_did.py --backfill
python3 ../scripts/generate_station_did.py --station-sn 64XXX --user-id local-user-1
python3 ../scripts/generate_station_did.py --station-sn 64XXX --user-id local-user-1 --apply
```

## 目标

后续这里会继续往“独立项目”推进：

- 把状态持久化、认证、设备域、消息域拆成单独模块
- 把 App 假云和浏览器云平台彻底解耦
- 为后续接入 ZLMediaKit 之前，先把假云控边界固定住
