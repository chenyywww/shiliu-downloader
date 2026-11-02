"""Optional machine-readable events for local frontends; terminal usage is unchanged."""
import json
import os
import sys
import threading

PREFIX = "__VD_EVENT__"
_lock = threading.Lock()


def emit(event, **values):
    if os.environ.get("VIDEO_DOWNLOADER_EVENTS") != "1":
        return
    with _lock:
        sys.stdout.write(PREFIX + json.dumps({"event": event, **values}, ensure_ascii=False, allow_nan=False) + "\n")
        sys.stdout.flush()
