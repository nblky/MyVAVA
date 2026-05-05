# 2026-04-11 Cloud RTMP 解密 / 提缩略图记忆

## 目标

- 记录当前 fake cloud 对基站 RTMP 上传样本的最小可用还原链路
- 重点是：
  - 把 cloud `.flv` 里的加密关键帧还原出来
  - 让 `ffmpeg/ffprobe` 能识别视频
  - 成功提取真实缩略图
- 本文只覆盖：
  - cloud RTMP 上传样本
  - 服务器端后处理
- 不覆盖：
  - TF 卡本地录像完整 demux
  - live / P2P 播放链

## 当前硬结论

- 已确认当前真实 cloud 上传样本是 `H.264`，不是 `H.265`
- 已确认 cloud 上传里的视频不是全坏，而是：
  - `P` 帧基本已是可读的标准 Annex-B
  - `I` 帧被厂家按固定规则加密
- 已确认用于当前样本的解密 key 不是动态协商出来的：
  - 官方 demo / push task 直接把 keystring 写死为 `vavalic2`
- 已确认当前缩略图提取的关键不在“先修好所有音频”，而在“先把加密关键帧净化出来”

## 真实样本与产物

- 关键样本：
  - `./data/cloud_media/incoming/20260411-015606_64XIJEE3QF0EAC5DE2647496E_storage-b96c_seg001.flv`
- 当前净化后中间产物：
  - `./data/cloud_media/incoming/20260411-015606_64XIJEE3QF0EAC5DE2647496E_storage-b96c_seg001.clean.flv`
- 当前成功生成的缩略图：
  - `./data/cloud_media/incoming/20260411-015606_64XIJEE3QF0EAC5DE2647496E_storage-b96c_seg001.png`
- 当前成功生成的 mp4：
  - `./data/cloud_media/incoming/20260411-015606_64XIJEE3QF0EAC5DE2647496E_storage-b96c_seg001.mp4`

## 样本层面的硬证据

### 1. 这是 H.264，不是 H.265

- 真实 `.flv` 的第一个视频 sequence header 前缀是：
  - `17 00 00 00 00 ...`
- `FLV codecID = 7`，对应 AVC，也就是 `H.264`
- 把加密关键帧解开后，前几个 NAL 是：
  - `00 00 00 01 67 ...`
  - `00 00 00 01 68 ...`
  - `00 00 00 01 65 ...`
- 这分别就是 H.264 的：
  - `SPS`
  - `PPS`
  - `IDR`

### 2. 加密关键帧的识别特征

- 当前样本里，关键帧 payload 在去掉 FLV 视频头 5 字节后，前 4 字节稳定是：
  - `27 be 9b 5b`
- 这在官方 demo 里也有对应检测：
  - `VAVA_Aes_Check_Video()` 会检查 `0x27 0xbe 0x9b 0x5b`
- 非关键帧样本则直接能看到：
  - `00 00 00 01 61 ...`

### 3. 官方代码里 keystring 是写死的

- 直接证据：
  - `../vava-test/media-push-sdk/demo/cloud_sunvalley.c`
  - `../vava-test/media-push-sdk/sdk/push_task.hpp`
- 两处都直接写：
  - `char* pkeystring = "vavalic2";`
- 并在 segment begin 时发送：
  - `E_SV_METADATA_TYPE_VIDEO_ENCRYPT`
  - `E_SV_METADATA_TYPE_AUDIO_ENCRYPT`

### 4. 官方 demo 的还原规则

- 视频：
  - 见 `../vava-test/media-push-sdk/demo/aesencrypt.c`
  - 若帧头是 `27 be 9b 5b`，则直接整块 `AES-128-ECB` 解密
- 音频：
  - 若首字节不是 `0xFF`
  - 先把首字节改成 `0xFF`
  - 再从 `buff + 1` 开始做 `AES-128-ECB` 解密

## 当前最小可用还原流程

### 视频净化规则

对 cloud `.flv` 的视频 tag：

1. 只处理 `tagType = 9` 且 `AVCPacketType = 1` 的视频包
2. 跳过 FLV 视频头前 5 字节后，得到视频 payload
3. 如果 payload 以 `27be9b5b` 开头：
   - 使用 `vavalic2` 补零到 16 字节作为 AES key
   - 走 `AES-128-ECB`
   - 只解完整的 16 字节块
   - 结尾不足 16 字节的尾巴保留原样
4. 如果 payload 已是 Annex-B：
   - 把 `00 00 00 01 / 00 00 01` 起始码切成 NAL
   - 转成 AVCC 的 `4-byte length + NAL` 形式
5. 重写回 `.flv`

### 为什么先不强依赖音频修好

- 当前缩略图提取只依赖视频
- 只做视频净化后，`ffprobe` 已能稳定识别：
  - `Video: h264 (Baseline), 1920x1080`
- `ffmpeg -frames:v 1` 也已经能产出真实 PNG
- 音频虽然仍有 AAC warning，但不阻塞“封面恢复”

## 当前实现位置

- 服务端后处理逻辑在：
  - `./scripts/rtmp_ingest_server.py`
- 当前新增的关键 helper：
  - `_openssl_bin()`
  - `_aes_128_ecb_decrypt_partial()`
  - `_find_start_code()`
  - `_annexb_to_avcc()`
  - `_sanitize_cloud_video_payload()`
  - `_sanitize_cloud_flv()`
  - `_postprocess_capture()`

## 当前实现要点

- 不引入新的 Python crypto 依赖
- 直接调用系统 `openssl`
- 原始上传文件保留不动：
  - `*.flv`
- 生成中间净化文件：
  - `*.clean.flv`
- 再由 `ffmpeg` 基于 `*.clean.flv` 生成：
  - `*.mp4`
  - `*.png`

## 关键验证结果

### `clean.flv`

- `ffprobe` 已能识别出：
  - `Video: h264 (Baseline)`
  - `1920x1080`
- 同时还能识别出 AAC 音频流，但音频会有 warning

### 缩略图

- 直接基于净化后的输入执行：
  - `ffmpeg -frames:v 1`
- 已成功生成：
  - `./data/cloud_media/incoming/20260411-015606_64XIJEE3QF0EAC5DE2647496E_storage-b96c_seg001.png`

### mp4

- 当前生成的 mp4 已可被 `ffprobe` 识别为：
  - `Stream #0:0 Video: h264`
  - `Stream #0:1 Audio: aac`
- 当前样本里视频轨已闭环

## 当前限制

- 音频虽然已经能随容器一起保留下来，但如果强行做 AAC 解码/重编码，仍会看到很多 warning
- 这说明“完整音频净化规则”还没像视频那样完全整理干净
- 但这不影响当前目标：
  - cloud 缩略图恢复
  - cloud 视频轨恢复

## 后续建议

- 如果后面优先解决“cloud 列表黑封面”：
  - 继续沿用当前视频净化 + `ffmpeg` 首帧截图路线
- 如果后面优先解决“播放时音频兼容性”：
  - 再单独补音频净化规则
  - 重点回头看：
    - `skipbytes`
    - `aencstring`
    - `akeyenctype`
    - 官方 `VAVA_Aes_Check_Audio()` 与真实样本的偏差
- 如果后面要做“更接近原厂的资产沉淀”：
  - 继续保留：
    - 原始 `.flv`
    - 中间 `.clean.flv`
    - 产出的 `.mp4`
    - 产出的 `.png`
  - 这样后面回溯最方便
