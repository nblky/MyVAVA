# Release Packaging

使用下面脚本生成可交付版本包（自动剔除运行时数据和本地私钥）：

```bash
../scripts/linux/build_release_bundle.sh
```

可选参数：

```bash
../scripts/linux/build_release_bundle.sh <version> <output_dir>
```

示例：

```bash
../scripts/linux/build_release_bundle.sh v2026.04.15 ../release
```

脚本会输出：

- `vava-server-<version>.tar.gz`
- `vava-server-<version>.tar.gz.sha256`

默认打包内容：

- `app/`
- `legacy/`
- `scripts/`
- `deploy/`
- `docs/`
- `fake_cloud_app/`
- `bin/`
- `native/`
- `.env.example`
- `README.md`
- `requirements.txt`
- `mediamtx-vava.yml`

默认排除：

- `.venv/`
- `data/*`
- `logs/*`
- `run/*`
- `legacy/cert_backups/`
- `legacy/sunvalley_local_root_ca.key`
- `legacy/*.srl`
- `__pycache__/`
- `*.pyc`

打包后目标机首次启动会自动创建空 `data/` 数据库结构。
