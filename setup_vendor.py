"""Install pinned dependencies while preserving the reviewed local Douyin backport."""
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parent
target = root / "vendor" / "yt_dlp" / "extractor" / "tiktok.py"
patch = target.read_bytes() if target.is_file() else None
try:
    subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "--target", str(root / "vendor"),
                    "-r", str(root / "requirements-vendor.txt")], check=True)
finally:
    if patch is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(patch)
print("依赖已安装，已保留项目里的抖音签名适配。")
