from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import queue
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

try:
    import httpx as _httpx
    _HTTPX_OK = True
except Exception:  # pragma: no cover - optional dependency
    _httpx = None
    _HTTPX_OK = False

UA = "PyMCLauncher/1.0"
IS_WINDOWS = os.name == "nt"
OS_NAME = "windows" if IS_WINDOWS else ("osx" if sys.platform == "darwin" else "linux")

_machine = platform.machine().lower()
if _machine in ("amd64", "x86_64", "x64"):
    ARCH = "x86_64"
elif _machine in ("arm64", "aarch64"):
    ARCH = "arm64"
elif _machine in ("x86", "i386", "i686"):
    ARCH = "x86"
else:
    ARCH = "x86_64"

IS_64 = ARCH != "x86"


class LauncherError(Exception):
    """Friendly error that can be shown to the user."""


class Cancelled(LauncherError):
    pass


class HttpError(LauncherError):
    def __init__(self, status: int, body: bytes, url: str):
        self.status = status
        self.body = body
        self.url = url
        text = ""
        try:
            text = body.decode("utf-8", "replace")[:300]
        except Exception:
            pass
        super().__init__(f"HTTP {status}: {url}\n{text}")

    def json(self) -> dict:
        try:
            return json.loads(self.body.decode("utf-8-sig"))
        except Exception:
            return {}


def ensure_dir(path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception:
        return default


def write_json(path, data):
    path = Path(path)
    ensure_dir(path.parent)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def sha1_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def human_size(num) -> str:
    try:
        num = float(num)
    except Exception:
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024 or unit == "TB":
            return f"{num:.1f} {unit}" if unit != "B" else f"{int(num)} B"
        num /= 1024.0
    return f"{num:.1f} TB"


_HTTPX_HOSTS = {"api.modrinth.com", "mod.mcimirror.top",
                "modrinth.com", "www.modrinth.com"}


def _use_httpx(url: str) -> bool:
    try:
        return urlsplit(url).hostname in _HTTPX_HOSTS
    except Exception:
        return False


def _http_request_httpx(url: str, method: str, data: bytes | None,
                        headers: dict, timeout: float, retries: int) -> bytes:
    last_err = None
    for attempt in range(retries + 1):
        try:
            with _httpx.Client(
                http2=True,
                timeout=_httpx.Timeout(timeout, connect=min(timeout, 8.0)),
                follow_redirects=True,
            ) as client:
                resp = client.request(method, url, content=data, headers=headers)
            if resp.status_code in (408, 425, 429, 500, 502, 503, 504) and attempt < retries:
                last_err = HttpError(resp.status_code, resp.content, url)
                time.sleep(0.8 * (attempt + 1))
                continue
            if resp.status_code >= 400:
                raise HttpError(resp.status_code, resp.content, url)
            return resp.content
        except HttpError:
            raise
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(0.8 * (attempt + 1))
                continue
            raise LauncherError(f"网络请求失败：{url}\n{last_err}") from None
    raise LauncherError(f"网络请求失败：{url}\n{last_err}")


def http_request(url: str, method: str | None = None, data: bytes | None = None,
                 headers: dict | None = None, timeout: float = 30.0,
                 retries: int = 2) -> bytes:
    method = method or ("POST" if data is not None else "GET")
    hdrs = {"User-Agent": UA, "Accept-Encoding": "identity", "Accept": "*/*"}
    if headers:
        hdrs.update(headers)
    if _HTTPX_OK and _use_httpx(url):
        return _http_request_httpx(url, method, data, hdrs, timeout, retries)
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            body = b""
            try:
                body = e.read()
            except Exception:
                pass
            if e.code in (408, 425, 429, 500, 502, 503, 504) and attempt < retries:
                last_err = e
                time.sleep(0.8 * (attempt + 1))
                continue
            raise HttpError(e.code, body, url) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last_err = e
            if attempt < retries:
                time.sleep(0.8 * (attempt + 1))
                continue
            raise LauncherError(f"网络请求失败：{url}\n{last_err}") from None
    raise LauncherError(f"网络请求失败：{url}\n{last_err}")


def http_json(url: str, **kwargs) -> dict:
    raw = http_request(url, **kwargs)
    text = raw.decode("utf-8-sig")
    try:
        return json.loads(text)
    except Exception as e:
        raise LauncherError(f"返回内容无法解析：{url}\n{e}") from None


def post_form(url: str, fields: dict, timeout: float = 40.0, retries: int = 2) -> dict:
    import urllib.parse
    data = urllib.parse.urlencode(fields).encode()
    raw = http_request(url, data=data, method="POST", timeout=timeout, retries=retries,
                       headers={"Content-Type": "application/x-www-form-urlencoded"})
    return json.loads(raw.decode("utf-8-sig"))


def post_json(url: str, payload: dict, timeout: float = 40.0, retries: int = 2) -> dict:
    raw = http_request(url, data=json.dumps(payload).encode(), method="POST",
                       timeout=timeout, retries=retries,
                       headers={"Content-Type": "application/json", "Accept": "application/json"})
    return json.loads(raw.decode("utf-8-sig"))


def maven_path(name: str) -> str:
    ext = "jar"
    if "@" in name:
        name, ext = name.split("@", 1)
    parts = name.split(":")
    if len(parts) < 3:
        return name.replace(":", "/") + "." + ext
    group, artifact, version = parts[0], parts[1], parts[2]
    classifier = parts[3] if len(parts) > 3 else None
    base = f"{artifact}-{version}" + (f"-{classifier}" if classifier else "")
    return "/".join(group.split(".")) + f"/{artifact}/{version}/{base}.{ext}"


def _rule_matches(rule: dict, features: dict) -> bool:
    os_rule = rule.get("os") or {}
    if os_rule:
        name = os_rule.get("name")
        if name and name != OS_NAME:
            return False
        arch = os_rule.get("arch")
        if arch and arch != ARCH:
            return False
        version = os_rule.get("version")
        if version:
            try:
                if not re.search(version, platform.version()):
                    return False
            except re.error:
                return False
    for key, expected in (rule.get("features") or {}).items():
        if bool(features.get(key, False)) != bool(expected):
            return False
    return True


def rules_allow(rules, features: dict | None = None) -> bool:
    if not rules:
        return True
    features = features or {}
    allowed = False
    for rule in rules:
        if _rule_matches(rule, features):
            allowed = rule.get("action") == "allow"
    return allowed


@dataclass
class DownloadItem:
    url: str
    dest: Path
    sha1: str = ""
    size: int = 0
    label: str = ""
    kind: str = ""
    alternates: tuple[str, ...] = ()

    def __post_init__(self):
        self.dest = Path(self.dest)
        if isinstance(self.alternates, list):
            self.alternates = tuple(self.alternates)


def file_ok(item: DownloadItem) -> bool:
    try:
        st = item.dest.stat()
    except OSError:
        return False
    if not item.dest.is_file() or st.st_size <= 0:
        return False
    if item.size:
        return st.st_size == item.size
    if item.sha1:
        return sha1_file(item.dest) == item.sha1
    return True


class Progress:
    """Aggregates progress from several download threads."""

    def __init__(self, callback=None, label: str = ""):
        self.callback = callback
        self.label = label
        self.items_done = 0
        self.items_total = 0
        self.bytes_done = 0
        self.bytes_total = 0
        self._lock = threading.Lock()
        self._last = 0.0
        self._t0 = time.time()

    def start(self, items_total: int, bytes_total: int, label: str | None = None):
        with self._lock:
            self.items_total = items_total
            self.bytes_total = max(0, int(bytes_total))
            self.items_done = 0
            self.bytes_done = 0
            if label is not None:
                self.label = label
            self._t0 = time.time()
        self.emit(True)

    def add_bytes(self, n: int):
        if not n:
            return
        with self._lock:
            self.bytes_done = max(0, self.bytes_done + n)
            if self.bytes_done > self.bytes_total and self.bytes_total > 0:
                self.bytes_done = min(self.bytes_done, max(self.bytes_total, self.bytes_done))
        self.emit()

    def item_done(self):
        with self._lock:
            self.items_done += 1
        self.emit()

    def snapshot(self):
        with self._lock:
            return (self.items_done, self.items_total, self.bytes_done,
                    self.bytes_total, self.label, time.time() - self._t0)

    def emit(self, force: bool = False):
        if not self.callback:
            return
        now = time.time()
        if not force and now - self._last < 0.15:
            return
        self._last = now
        done, total, bdone, btotal, label, elapsed = self.snapshot()
        try:
            self.callback(done, total, bdone, btotal, label, elapsed)
        except Exception:
            pass


log = logging.getLogger(__name__)

_RANGE_MIN_SIZE = 256 << 10
_SEGMENT_SIZE = 8 << 20
_SEGMENT_MAX = 1024
_SEGMENT_RETRIES = 2
_DEFAULT_FILE_CONNECTIONS = 16
_MAX_FILE_CONNECTIONS = 16
_SMALL_FILE_CUTOFF = 8 << 20
_SMALL_FILE_CONNECTIONS = 8
_READ_BUFFER = 64 << 10
_SLOW_AFTER = 8.0
_SLOW_CHECK = 1.0
_SLOW_MIN_RATE = 128 << 10


class _RangeUnsupported(Exception):
    pass


class _SlowSegment(Exception):
    pass


class _ByteMeter:
    __slots__ = ("progress", "counted")

    def __init__(self, progress: Progress | None):
        self.progress = progress
        self.counted = 0

    def add(self, n: int):
        if n <= 0:
            return
        self.counted += n
        if self.progress:
            self.progress.add_bytes(n)

    def rollback(self):
        if self.progress and self.counted:
            self.progress.add_bytes(-self.counted)
        self.counted = 0


class _ConnectionLimiter:
    def __init__(self, limit: int):
        self._sem = threading.BoundedSemaphore(max(1, int(limit)))

    def acquire(self, cancel: threading.Event | None = None):
        while True:
            if cancel is not None and cancel.is_set():
                raise Cancelled("已取消")
            if self._sem.acquire(timeout=0.2):
                return

    def release(self):
        try:
            self._sem.release()
        except ValueError:
            pass


def _content_range_start(value: str) -> tuple[int, int, int]:
    m = re.match(r"bytes\s+(\d+)-(\d+)/(\d+|\*)", (value or "").strip(), re.I)
    if not m:
        raise _RangeUnsupported()
    total = 0 if m.group(3) == "*" else int(m.group(3))
    return int(m.group(1)), int(m.group(2)), total


class _H2Response:
    __slots__ = ("_resp", "_it", "_buf")

    def __init__(self, resp):
        self._resp = resp
        self._it = resp.iter_bytes()
        self._buf = b""

    def read(self, size: int) -> bytes:
        while len(self._buf) < size:
            try:
                chunk = next(self._it)
            except StopIteration:
                break
            if chunk:
                self._buf += chunk
        if not self._buf:
            return b""
        out = self._buf[:size]
        self._buf = self._buf[size:]
        return out

    def close(self):
        try:
            self._resp.close()
        except Exception:
            pass


@contextmanager
def _h2_range(url: str, start: int, end: int, timeout: float, cancel):
    client = _httpx.Client(
        http2=True,
        timeout=_httpx.Timeout(timeout, connect=min(timeout, 8.0)),
        follow_redirects=True,
    )
    try:
        with client.stream("GET", url, headers={
            "User-Agent": UA,
            "Accept-Encoding": "identity",
            "Range": f"bytes={start}-{end}",
        }) as resp:
            if resp.status_code != 206:
                raise _RangeUnsupported()
            real_start, _real_end, total = _content_range_start(
                resp.headers.get("content-range", ""))
            if real_start != start:
                raise _RangeUnsupported()
            yield _H2Response(resp), total
    finally:
        try:
            client.close()
        except Exception:
            pass


def _prepare_read(resp):
    if _HTTPX_OK and isinstance(resp, _H2Response):
        return resp
    try:
        sock = resp.fp.raw._sock
        sock.settimeout(1.0)
        return sock
    except Exception:
        return None


def _is_timeout(error: Exception) -> bool:
    if isinstance(error, TimeoutError) or getattr(error, "winerror", None) == 10060:
        return True
    if _HTTPX_OK and isinstance(error, _httpx.TimeoutException):
        return True
    return False


def _read_block(resp, size: int, cancel: threading.Event | None,
                idle_timeout: float, stop: threading.Event | None = None,
                sliced: bool = False) -> bytes:
    if stop is not None and stop.is_set():
        raise Cancelled("已取消")
    if cancel is not None and cancel.is_set():
        raise Cancelled("已取消")
    if not sliced:
        try:
            return resp.read(size)
        except Exception:
            if stop is not None and stop.is_set():
                raise Cancelled("已取消") from None
            raise
    started = time.monotonic()
    while True:
        if stop is not None and stop.is_set():
            raise Cancelled("已取消")
        if cancel is not None and cancel.is_set():
            raise Cancelled("已取消")
        try:
            return resp.read(size)
        except Exception as e:
            if not _is_timeout(e):
                raise
            if time.monotonic() - started >= idle_timeout:
                raise TimeoutError("下载超时") from None


@contextmanager
def _range_conn(url: str, start: int, end: int, timeout: float,
                cancel: threading.Event | None, limiter: _ConnectionLimiter):
    limiter.acquire(cancel)
    try:
        if _HTTPX_OK and url.startswith("https://"):
            with _h2_range(url, start, end, timeout, cancel) as (resp, total):
                yield resp, total
            return
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept-Encoding": "identity",
            "Range": f"bytes={start}-{end}",
        })
        try:
            resp = urllib.request.urlopen(req, timeout=min(timeout, 8.0))
        except urllib.error.HTTPError as e:
            if e.code in (416, 501):
                raise _RangeUnsupported() from None
            body = b""
            try:
                body = e.read(2048)
            except Exception:
                pass
            raise HttpError(e.code, body, url) from None
        status = getattr(resp, "status", None) or resp.getcode()
        if status != 206:
            resp.close()
            raise _RangeUnsupported()
        try:
            real_start, _real_end, total = _content_range_start(
                resp.headers.get("Content-Range", ""))
            if real_start != start:
                raise _RangeUnsupported()
            with resp:
                yield resp, total
        except Exception:
            try:
                resp.close()
            except Exception:
                pass
            raise
    finally:
        limiter.release()


def _probe_range(url: str, timeout: float, cancel: threading.Event | None,
                 limiter: _ConnectionLimiter) -> int:
    try:
        with _range_conn(url, 0, 0, timeout, cancel, limiter) as (_resp, total):
            return total
    except _RangeUnsupported:
        return 0


class _Segment:
    __slots__ = ("start", "end", "pos", "retries", "attempt_start", "attempt_pos")

    def __init__(self, start: int, end: int):
        self.start = start
        self.end = end
        self.pos = start
        self.retries = 0
        self.attempt_start = 0.0
        self.attempt_pos = start


class _RangeSession:
    def __init__(self, url: str, total: int, fd: int, segments: list[_Segment],
                 meter: _ByteMeter, progress_cancel: threading.Event | None,
                 limiter: _ConnectionLimiter, connections: int, timeout: float):
        self.url = url
        self.total = total
        self.fd = fd
        self.meter = meter
        self.cancel = progress_cancel
        self.limiter = limiter
        self.connections = connections
        self.timeout = timeout
        self.queue: queue.Queue = queue.Queue()
        self.lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.done = threading.Event()
        self.stop = threading.Event()
        self.error: Exception | None = None
        self.outstanding = len(segments)
        self.segment_count = len(segments)
        self.written = 0
        self.rates: dict[int, tuple[float, float]] = {}
        self.active: set = set()
        for seg in segments:
            self.queue.put(seg)

    def _track(self, sock):
        with self.lock:
            self.active.add(sock)

    def _untrack(self, sock):
        with self.lock:
            self.active.discard(sock)

    def _abort_active(self):
        with self.lock:
            active = list(self.active)
        for sock in active:
            try:
                sock.close()
            except Exception:
                pass

    def _fail(self, error: Exception):
        with self.lock:
            if self.error is None:
                self.error = error
        self.stop.set()
        self._abort_active()

    def run(self):
        workers = max(1, min(self.connections, self.segment_count))
        threads = [threading.Thread(target=self._worker, daemon=True) for _ in range(workers)]
        for t in threads:
            t.start()
        try:
            while not self.done.wait(0.2):
                if self.cancel is not None and self.cancel.is_set():
                    raise Cancelled("已取消")
                with self.lock:
                    error = self.error
                if error is not None:
                    raise error
                if self.stop.is_set():
                    break
            with self.lock:
                error = self.error
            if error is not None:
                raise error
            if not self.done.is_set():
                raise LauncherError("分段下载提前停止")
        finally:
            self.stop.set()
            self._abort_active()
            for _ in threads:
                self.queue.put(None)
            for t in threads:
                t.join(timeout=10)

    def _worker(self):
        while not self.stop.is_set():
            try:
                seg = self.queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if seg is None:
                return
            try:
                self._fetch(seg)
            except _SlowSegment:
                self._retry(seg, slow=True)
            except _RangeUnsupported as e:
                self._fail(e)
                return
            except Cancelled as e:
                if self.error is None and not self.stop.is_set():
                    self._fail(e)
                return
            except Exception as e:
                self._retry(seg, slow=False, error=e)
            else:
                with self.lock:
                    self.outstanding -= 1
                    if self.outstanding == 0:
                        self.done.set()

    def _retry(self, seg: _Segment, slow: bool, error: Exception | None = None):
        with self.lock:
            seg.retries += 1
            remaining = seg.end - seg.pos + 1
            can_split = (slow and seg.retries <= _SEGMENT_RETRIES
                         and remaining >= 2 << 20 and self.segment_count < 256)
            new_seg = None
            if can_split:
                mid = seg.pos + remaining // 2
                new_seg = _Segment(mid, seg.end)
                seg.end = mid - 1
                self.outstanding += 1
                self.segment_count += 1
            retries = seg.retries
        if self.stop.is_set():
            return
        if can_split:
            log.debug("splitting slow segment %s-%s at %s", seg.start, seg.end, seg.pos)
            self.queue.put(new_seg)
            self.queue.put(seg)
        elif retries <= _SEGMENT_RETRIES and remaining > 0:
            time.sleep(0.2 * retries)
            self.queue.put(seg)
        else:
            self._fail(error or LauncherError("分段下载多次失败"))

    def _fetch(self, seg: _Segment):
        seg.attempt_start = time.time()
        seg.attempt_pos = seg.pos
        last_check = seg.attempt_start
        with _range_conn(self.url, seg.pos, seg.end, self.timeout,
                         self.cancel, self.limiter) as (resp, total):
            if total and total != self.total:
                raise LauncherError(f"分段大小不一致（{total} != {self.total}）")
            sock = _prepare_read(resp)
            sliced = sock is not None and not (_HTTPX_OK and isinstance(sock, _H2Response))
            if sock is not None:
                self._track(sock)
            try:
                while seg.pos <= seg.end:
                    want = min(_READ_BUFFER, seg.end - seg.pos + 1)
                    block = _read_block(resp, want, self.cancel, self.timeout,
                                        self.stop, sliced)
                    if not block:
                        raise LauncherError("下载连接提前中断")
                    with self.write_lock:
                        os.lseek(self.fd, seg.pos, os.SEEK_SET)
                        view = memoryview(block)
                        while view:
                            n = os.write(self.fd, view)
                            if n <= 0:
                                raise OSError("写入下载文件失败")
                            view = view[n:]
                    seg.pos += len(block)
                    with self.lock:
                        self.written += len(block)
                    self.meter.add(len(block))
                    now = time.time()
                    if now - last_check >= _SLOW_CHECK:
                        elapsed = now - seg.attempt_start
                        if elapsed >= _SLOW_AFTER:
                            rate = (seg.pos - seg.attempt_pos) / elapsed
                            if self._is_slow(seg, rate, now):
                                raise _SlowSegment()
                        last_check = now
            finally:
                if sock is not None:
                    self._untrack(sock)

    def _is_slow(self, seg: _Segment, rate: float, now: float) -> bool:
        with self.lock:
            self.rates[id(seg)] = (rate, now)
            others = [r for key, (r, t) in self.rates.items()
                      if key != id(seg) and now - t < 4 and r > 0]
        if not others:
            return False
        others.sort()
        median = others[len(others) // 2]
        return ((median >= _SLOW_MIN_RATE and rate < median / 4)
                or (rate < 50 << 10 and median >= _SLOW_MIN_RATE))


def _range_download(url: str, item: DownloadItem, tmp: Path, meter: _ByteMeter,
                    cancel: threading.Event | None, timeout: float,
                    limiter: _ConnectionLimiter, connections: int) -> bool:
    total = int(item.size or 0)
    if total and total < _RANGE_MIN_SIZE:
        return False
    if not total:
        total = _probe_range(url, timeout, cancel, limiter)
        if total < _RANGE_MIN_SIZE:
            return False
    if connections < 2:
        return False
    if total < _SMALL_FILE_CUTOFF:
        connections = min(connections, _SMALL_FILE_CONNECTIONS)
    per = max(1, (total + connections - 1) // connections)
    target = min(_SEGMENT_SIZE, per)
    count = min(_SEGMENT_MAX, max(1, (total + target - 1) // target))
    if count < 2:
        return False
    seg_size = (total + count - 1) // count
    segments = []
    for i in range(count):
        start = i * seg_size
        end = min(total, start + seg_size) - 1
        if start <= end:
            segments.append(_Segment(start, end))
    if len(segments) < 2:
        return False
    flags = os.O_RDWR | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
    fd = os.open(tmp, flags, 0o666)
    try:
        os.ftruncate(fd, total)
    except Exception:
        os.close(fd)
        raise
    session = _RangeSession(url, total, fd, segments, meter, cancel, limiter,
                            connections, timeout)
    try:
        session.run()
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
    if session.written != total:
        raise LauncherError(f"分段下载不完整（{session.written} != {total}）")
    return True


def _download_stream_h2(url: str, item: DownloadItem, tmp: Path, meter: _ByteMeter,
                        cancel: threading.Event | None, timeout: float):
    client = _httpx.Client(
        http2=True,
        timeout=_httpx.Timeout(timeout, connect=min(timeout, 8.0)),
        follow_redirects=True,
    )
    done = threading.Event()
    if cancel is not None:
        def _watch():
            while not done.is_set():
                if cancel.is_set():
                    client.close()
                    break
                done.wait(0.2)
        threading.Thread(target=_watch, daemon=True).start()
    try:
        with client.stream("GET", url, headers={
            "User-Agent": UA,
            "Accept-Encoding": "identity",
        }) as resp:
            if resp.status_code != 200:
                raise HttpError(resp.status_code, b"", url)
            length = resp.headers.get("content-length")
            length = int(length) if length and length.isdigit() else -1
            if item.size and length >= 0 and length != item.size:
                raise LauncherError(f"下载大小与清单不一致（{length} != {item.size}）")
            written = 0
            with open(tmp, "wb") as f:
                for chunk in resp.iter_bytes():
                    if cancel is not None and cancel.is_set():
                        raise Cancelled("已取消")
                    f.write(chunk)
                    written += len(chunk)
                    meter.add(len(chunk))
            if length >= 0 and written != length:
                raise LauncherError(f"下载不完整（{written} != {length}）")
    finally:
        done.set()
        try:
            client.close()
        except Exception:
            pass


def _download_stream(url: str, item: DownloadItem, tmp: Path, meter: _ByteMeter,
                     cancel: threading.Event | None, timeout: float,
                     limiter: _ConnectionLimiter):
    limiter.acquire(cancel)
    try:
        if _HTTPX_OK and url.startswith("https://"):
            _download_stream_h2(url, item, tmp, meter, cancel, timeout)
            return
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept-Encoding": "identity",
        })
        try:
            resp = urllib.request.urlopen(req, timeout=min(timeout, 8.0))
        except urllib.error.HTTPError as e:
            body = b""
            try:
                body = e.read(2048)
            except Exception:
                pass
            raise HttpError(e.code, body, url) from None
        with resp:
            length = resp.headers.get("Content-Length")
            length = int(length) if length and length.isdigit() else -1
            if item.size and length >= 0 and length != item.size:
                raise LauncherError(f"下载大小与清单不一致（{length} != {item.size}）")
            sliced = _prepare_read(resp) is not None
            written = 0
            with open(tmp, "wb") as f:
                while True:
                    block = _read_block(resp, _READ_BUFFER, cancel, timeout,
                                        None, sliced)
                    if not block:
                        break
                    f.write(block)
                    written += len(block)
                    meter.add(len(block))
            if length >= 0 and written != length:
                raise LauncherError(f"下载不完整（{written} != {length}）")
    finally:
        limiter.release()


def _remove(path: Path):
    try:
        path.unlink()
    except OSError:
        pass


def download_item(item: DownloadItem, progress: Progress | None = None,
                  cancel: threading.Event | None = None, timeout: float = 30.0,
                  connections: int = 0,
                  limiter: _ConnectionLimiter | None = None):
    if file_ok(item):
        return False
    dest = item.dest
    ensure_dir(dest.parent)
    tmp = dest.with_name(dest.name + ".part")
    per_file = max(1, min(_MAX_FILE_CONNECTIONS,
                          int(connections or _DEFAULT_FILE_CONNECTIONS)))
    limiter = limiter or _ConnectionLimiter(per_file)
    urls = []
    for url in (item.url, *item.alternates):
        if url and url not in urls:
            urls.append(url)
    last_err: Exception | None = None
    for url in urls:
        range_ok = True
        for attempt in range(3):
            if cancel is not None and cancel.is_set():
                raise Cancelled("已取消")
            meter = _ByteMeter(progress)
            try:
                if range_ok:
                    try:
                        used_range = _range_download(url, item, tmp, meter, cancel,
                                                     timeout, limiter, per_file)
                    except _RangeUnsupported:
                        used_range = False
                        range_ok = False
                        meter.rollback()
                        _remove(tmp)
                    if not used_range:
                        _download_stream(url, item, tmp, meter, cancel, timeout, limiter)
                else:
                    _download_stream(url, item, tmp, meter, cancel, timeout, limiter)
                actual = tmp.stat().st_size
                if item.size and actual != item.size:
                    raise LauncherError(f"大小不匹配（{actual} != {item.size}）")
                if item.sha1 and sha1_file(tmp).lower() != item.sha1.lower():
                    raise LauncherError("文件校验失败（sha1）")
                os.replace(tmp, dest)
                if progress:
                    progress.item_done()
                log.debug("downloaded %s (%d bytes)", dest, actual)
                return True
            except Cancelled:
                meter.rollback()
                _remove(tmp)
                raise
            except Exception as e:
                last_err = e
                meter.rollback()
                _remove(tmp)
                log.debug("download attempt failed (%s): %s", url, e)
                if attempt < 2:
                    time.sleep(0.6 * (attempt + 1))
    raise LauncherError(f"下载失败：{item.label or dest.name}\n{last_err}")


def download_many(items: list[DownloadItem], workers: int = 16,
                  progress: Progress | None = None,
                  cancel: threading.Event | None = None) -> dict:
    items = list(items)
    progress = progress or Progress()
    needed = [it for it in items if not file_ok(it)]
    skipped = len(items) - len(needed)
    progress.start(len(needed), sum(max(0, int(it.size or 0)) for it in needed))
    if not needed:
        progress.emit(True)
        return {"downloaded": 0, "skipped": skipped, "bytes": 0}
    total_bytes = sum(max(0, int(it.size or 0)) for it in needed)
    conn_limit = max(1, int(workers or 16))
    active_files = max(1, min(4, len(needed)))
    per_file = max(1, min(_MAX_FILE_CONNECTIONS, conn_limit // active_files))
    workers = max(1, min(conn_limit, len(needed)))
    limiter = _ConnectionLimiter(conn_limit)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(download_item, it, progress, cancel, 30.0,
                             per_file, limiter): it for it in needed}
        for fut in as_completed(futures):
            try:
                fut.result()
            except Cancelled:
                for f in futures:
                    f.cancel()
                raise
            except Exception as e:
                for f in futures:
                    f.cancel()
                raise LauncherError(str(e)) from None
    progress.emit(True)
    return {"downloaded": len(needed), "skipped": skipped, "bytes": total_bytes}
