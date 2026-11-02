"""Small terminal progress displays shared by the download engines."""

import math
import sys
import threading
import time
from download_events import emit


def byte_size(value):
    value = max(0, value or 0)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f}{unit}"
        value /= 1024


def duration(value):
    if value is None or not math.isfinite(value):
        return "--:--"
    value = max(0, int(value))
    hours, rest = divmod(value, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}" if hours else f"{minutes:02}:{seconds:02}"


class ProgressBar:
    def __init__(self, label, stream=None):
        self.label = label
        self.stream = stream or sys.stdout
        self.tty = self.stream.isatty()
        self.started = time.monotonic()
        self.last_draw = 0
        self.last_width = 0
        self.fraction = None
        self.downloaded = None
        self.total_bytes = None
        self.estimated = False
        self.detail = ""
        self.speed = None
        self.eta = None
        self.open_line = False
        self.lock = threading.Lock()

    def update(
        self, *, fraction=None, downloaded=None, detail="", speed=None, eta=None,
        total_bytes=None, estimated=False,
    ):
        with self.lock:
            self.fraction = (
                min(1, max(0, fraction)) if fraction is not None else None
            )
            self.downloaded = downloaded
            self.total_bytes = total_bytes
            self.estimated = estimated
            self.detail = detail
            self.speed = speed
            self.eta = eta

    def draw(self, force=False):
        now = time.monotonic()
        interval = 0.15 if self.tty else 1.0
        if not force and now - self.last_draw < interval:
            return
        with self.lock:
            fraction, received = self.fraction, self.downloaded
            detail, speed, eta = self.detail, self.speed, self.eta
            total_bytes, estimated = self.total_bytes, self.estimated
        elapsed = max(0.001, now - self.started)
        width = 22
        if fraction is None:
            symbol = "|/-\\"[int(elapsed * 4) % 4]
            bar, percent = symbol.center(width), " --.-%"
        else:
            filled = min(width, int(width * fraction))
            bar = "#" * filled + "-" * (width - filled)
            percent = f"{fraction * 100:5.1f}%"
        fields = [f"{self.label} [{bar}] {percent}"]
        if detail:
            fields.append(detail)
        if received is not None:
            speed = speed if speed is not None else received / elapsed
            size = byte_size(received)
            if total_bytes:
                size += ("/~" if estimated else "/") + byte_size(total_bytes)
            fields += [size, byte_size(speed) + "/s"]
        if eta is None and fraction is not None and fraction > 0:
            eta = elapsed * (1 - fraction) / fraction
        fields.append("ETA " + duration(eta))
        emit("progress", label=self.label, fraction=fraction, downloaded=received,
             total_bytes=total_bytes, estimated=estimated, detail=detail,
             speed=speed, eta=eta)
        text = "  ".join(fields)
        if self.tty:
            padding = " " * max(0, self.last_width - len(text))
            self.stream.write("\r" + text + padding)
            self.open_line = True
        else:
            self.stream.write(text + "\n")
        self.stream.flush()
        self.last_width = len(text)
        self.last_draw = now

    def finish(self, success=True):
        if success:
            with self.lock:
                self.fraction = 1.0
                self.eta = 0
        self.draw(force=True)
        if self.open_line:
            self.stream.write("\n")
            self.stream.flush()
            self.open_line = False


class SegmentProgress:
    def __init__(self, count):
        self.count = count
        self.completed = 0
        self.finished_bytes = 0
        self.active = {}
        self.lock = threading.Lock()
        self.bar = ProgressBar("下载")

    def received(self, index, current, total):
        with self.lock:
            self.active[index] = (current, total)

    def done(self, index, received):
        with self.lock:
            self.active.pop(index, None)
            self.completed += 1
            self.finished_bytes += received

    def draw(self, force=False):
        with self.lock:
            completed = self.completed
            partial = sum(
                min(1, current / total)
                for current, total in self.active.values() if total
            )
            received = self.finished_bytes + sum(
                current for current, _ in self.active.values()
            )
        self.bar.update(
            fraction=(
                1 if completed == self.count
                else min(0.999, (completed + partial) / self.count)
            ),
            downloaded=received,
            detail=f"{completed}/{self.count}段",
        )
        self.bar.draw(force=force)

    def finish(self, success=True):
        self.draw()
        self.bar.finish(success)
