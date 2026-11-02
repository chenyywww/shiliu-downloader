from dataclasses import dataclass
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT.parent
VENDOR = ROOT / "vendor"


@dataclass
class Options:
    output: str | None = None
    referer: str | None = None
    jobs: int = 8
    check: bool = False
    show_browser: bool = False
    browser_wait: int = 25
    use_x_login: bool = False
    manual_browser: bool = False
    expected_video_id: str | None = None
    passive_browser: bool = False
    browser_state: dict | None = None
    browser_user_agent: str | None = None
    use_youtube_login: bool = False


def child_env():
    env = os.environ.copy()
    env["PYTHONPATH"] = str(VENDOR)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env
