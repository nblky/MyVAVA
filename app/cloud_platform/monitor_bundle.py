from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


SOURCE_TEMPLATE_PATH = Path(__file__).with_name("monitor_source.html")
BODY_TEMPLATE_PATH = Path(__file__).with_name("monitor_body.html")
SHELL_TEMPLATE_PATH = Path(__file__).with_name("monitor_page.html")
ASSET_ROOT = Path(__file__).with_name("assets")
EXTERNAL_SCRIPT_URLS = ("https://cdn.jsdelivr.net/npm/hls.js@1/dist/hls.min.js",)

_cached_bundle_key: tuple[int, ...] | None = None
_cached_bundle: "MonitorBundle | None" = None


@dataclass(frozen=True)
class MonitorBundle:
    body_html: str
    external_script_urls: tuple[str, ...]
    version: str

def load_monitor_bundle() -> MonitorBundle:
    global _cached_bundle_key, _cached_bundle

    watched_paths = [SHELL_TEMPLATE_PATH]
    if BODY_TEMPLATE_PATH.is_file():
        watched_paths.append(BODY_TEMPLATE_PATH)
    elif SOURCE_TEMPLATE_PATH.is_file():
        watched_paths.append(SOURCE_TEMPLATE_PATH)
    if ASSET_ROOT.is_dir():
        watched_paths.extend(sorted(path for path in ASSET_ROOT.rglob("*") if path.is_file()))
    bundle_key = tuple(path.stat().st_mtime_ns for path in watched_paths)
    if _cached_bundle is not None and _cached_bundle_key == bundle_key:
        return _cached_bundle

    if BODY_TEMPLATE_PATH.is_file():
        body_html = BODY_TEMPLATE_PATH.read_text(encoding="utf-8").strip()
    else:
        body_html = SOURCE_TEMPLATE_PATH.read_text(encoding="utf-8")
    digest = hashlib.sha1(body_html.encode("utf-8"))
    digest.update(SHELL_TEMPLATE_PATH.read_bytes())
    if ASSET_ROOT.is_dir():
        for asset_path in sorted(path for path in ASSET_ROOT.rglob("*") if path.is_file()):
            digest.update(asset_path.relative_to(ASSET_ROOT).as_posix().encode("utf-8"))
            digest.update(asset_path.read_bytes())
    version = digest.hexdigest()[:12]
    bundle = MonitorBundle(
        body_html=body_html,
        external_script_urls=EXTERNAL_SCRIPT_URLS,
        version=version,
    )
    _cached_bundle_key = bundle_key
    _cached_bundle = bundle
    return bundle


def render_monitor_shell(bundle: MonitorBundle) -> str:
    template = SHELL_TEMPLATE_PATH.read_text(encoding="utf-8")
    external_scripts = "\n".join(
        f'  <script src="{src}"></script>'
        for src in bundle.external_script_urls
    )
    return (
        template.replace("__MONITOR_CSS_URL__", f"/monitor/assets/monitor.css?v={bundle.version}")
        .replace("__MONITOR_BODY__", bundle.body_html)
        .replace("__MONITOR_EXTERNAL_SCRIPTS__", external_scripts)
        .replace("__MONITOR_APP_URL__", f"/monitor/assets/monitor_app.js?v={bundle.version}")
    )
