"""Create a local distribution from an explicit source allowlist, without user data."""
from datetime import datetime
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parent
ROOT_FILES = {
    ".gitignore", "download.cmd", "start_web.cmd", "login_x.cmd", "login_douyin.cmd",
    "login_youtube.cmd", "download_router.py", "download_hls.py", "download_events.py",
    "progress_display.py", "url_input.py", "web_app.py", "web_jobs.py", "site_routes.json",
    "build_release.py", "build_release.cmd", "使用说明.md", "本地网页说明.md", "发布说明.md",
    "resolve_net.py", "resolution.py", "resolve_router.py", "resolve_browser.py", "download_plan.py",
    "download_plan.cmd", "start_resolver.cmd", "仅解析说明.md", "版本与分支.md", "requirements-vendor.txt",
    "setup_vendor.py", "setup_vendor.cmd", "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md",
}
SOURCE_DIRS = {"site_handlers", "web_ui", "vendor", "third_party"}
PRIVATE_PARTS = {".auth", ".web", ".git", "__pycache__", ".pytest_cache", "releases"}


def public_files(root):
    root = Path(root).resolve()
    candidates = [root / name for name in sorted(ROOT_FILES)]
    for name in sorted(SOURCE_DIRS):
        directory = root / name
        if directory.is_dir():
            candidates.extend(directory.rglob("*"))
    for path in candidates:
        relative = path.relative_to(root)
        if set(relative.parts) & PRIVATE_PARTS or not path.is_file() or path.is_symlink():
            continue
        if not path.resolve().is_relative_to(root):
            continue
        if path.suffix.lower() in {".pyc", ".pyo", ".log", ".sqlite3"} or path.name == "direct_url.json":
            continue
        if relative.parts[0] == "site_handlers" and path.suffix != ".py":
            continue
        if relative.parts[:2] in {("vendor", "bin"), ("vendor", "include"), ("vendor", "share")}:
            continue
        yield path, relative.as_posix()


def verify_release(path):
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            parts = Path(name).parts
            if set(parts) & PRIVATE_PARTS or name.endswith((".log", ".sqlite3", ".pyc", ".pyo")):
                raise RuntimeError("发布包包含本机运行数据，检查失败。")
        if archive.testzip() is not None:
            raise RuntimeError("发布包完整性检查失败。")


def create_release(root, destination):
    files = list(public_files(root))
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "x", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path, relative in files:
            archive.write(path, "local-video-downloader/" + relative)
        manifest = {"created": datetime.now().isoformat(), "files": [relative for _, relative in files],
                    "excluded": [".auth", ".web", "logs", "downloaded media"],
                    "account": "Each recipient signs into their own account locally. No saved session is bundled."}
        archive.writestr("local-video-downloader/release-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    verify_release(destination)
    return destination


if __name__ == "__main__":
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = create_release(ROOT, ROOT / "releases" / f"local-video-downloader_{stamp}.zip")
    print("发布包已生成并检查：" + str(path))
    print("不包含 .auth、.web、错误日志或下载视频。接收者使用自己的账号登录。")
