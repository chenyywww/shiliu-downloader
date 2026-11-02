"""Bounded, anonymous metadata transport. Media bodies are never read here."""
from contextlib import contextmanager
import http.client
import http.cookiejar
import ipaddress
import socket
import ssl
from urllib.parse import urlsplit
from urllib.request import HTTPHandler, HTTPSHandler, HTTPRedirectHandler, HTTPCookieProcessor, ProxyHandler, Request, build_opener

MAX_RESPONSE = 2 * 1024 * 1024
MAX_TOTAL = 24 * 1024 * 1024
AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


def validate_url(url, allow_private=False, resolve_dns=True):
    if not isinstance(url, str) or len(url) > 16000 or any(c in url for c in "\r\n\0"):
        raise ValueError("资源网址无效。")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise ValueError("只接受不含账号密码的 HTTP/HTTPS 网址。")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not allow_private and port not in {80, 443}:
        raise ValueError("解析服务仅访问公开网站的标准端口。")
    if not allow_private:
        if parsed.hostname.lower().rstrip(".") in {"localhost", "metadata.google.internal"}:
            raise ValueError("解析服务不访问本机或内网地址。")
        try:
            addresses = [ipaddress.ip_address(parsed.hostname)]
        except ValueError:
            addresses = [ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)] if resolve_dns else []
        if any(not address.is_global for address in addresses):
            raise ValueError("解析服务不访问本机、内网或云元数据地址。")
    return url


def safe_connect(allow_private):
    def connect(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, **kwargs):
        host, port = address
        entries = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        if not allow_private and any(not ipaddress.ip_address(item[4][0]).is_global for item in entries):
            raise ValueError("连接目标不是公开网络地址。")
        error = None
        for family, kind, protocol, _, sockaddr in entries:
            sock = socket.socket(family, kind, protocol)
            try:
                if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                    sock.settimeout(timeout)
                if source_address:
                    sock.bind(source_address)
                sock.connect(sockaddr)
                return sock
            except OSError as exc:
                error = exc
                sock.close()
        raise error or OSError("无法连接目标网站。")
    return connect


def metadata_tls_context():
    """Match standard-library HTTPS negotiation, keeping certificate checks on."""
    context = ssl.create_default_context()
    context.set_alpn_protocols(["http/1.1"])
    if context.post_handshake_auth is not None:
        context.post_handshake_auth = True
    return context


def guarded_opener(allow_private=False, jar=None):
    class Redirects(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            validate_url(newurl, allow_private)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    def connection(cls, host, **kwargs):
        conn = cls(host, **kwargs)
        conn._create_connection = safe_connect(allow_private)
        return conn

    class HTTP(HTTPHandler):
        def http_open(self, req):
            return self.do_open(lambda host, **kw: connection(http.client.HTTPConnection, host, **kw), req)

    class HTTPS(HTTPSHandler):
        def https_open(self, req):
            return self.do_open(lambda host, **kw: connection(http.client.HTTPSConnection, host, **kw), req, context=metadata_tls_context())

    return build_opener(ProxyHandler({}), Redirects(), HTTPCookieProcessor(jar if jar is not None else http.cookiejar.CookieJar()), HTTP(), HTTPS())


def is_media(mime, url):
    path = urlsplit(url).path.lower()
    if "mpegurl" in mime or "dash+xml" in mime or path.endswith((".m3u8", ".mpd")):
        return False
    return mime.startswith(("video/", "audio/")) or path.endswith((".mp4", ".m4v", ".m4s", ".ts", ".aac", ".mp3", ".webm", ".mkv"))


class MediaFound(ValueError):
    def __init__(self, url):
        self.url = url
        super().__init__("已识别媒体地址，仅返回地址，不读取视频数据。")


class MetadataClient:
    def __init__(self, allow_private=False, jar=None):
        self.allow_private = allow_private
        self.opener = guarded_opener(allow_private, jar)
        self.received = 0
        self.media = []

    def open(self, url, headers=None, data=None, method=None):
        validate_url(url, self.allow_private)
        values = {"User-Agent": AGENT, "Accept-Encoding": "identity",
                  "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                  "Accept-Language": "en-US,en;q=0.9"}
        values.update(headers or {})
        values.pop("Accept-Encoding", None)
        values["Accept-Encoding"] = "identity"
        response = self.opener.open(Request(url, data=data, headers=values, method=method), timeout=12)
        mime = response.headers.get("Content-Type", "").lower()
        if is_media(mime, response.geturl()):
            self.media.append(response.geturl())
            response.close()
            raise MediaFound(self.media[-1])
        length = response.headers.get("Content-Length", "")
        if length.isdigit() and int(length) > MAX_RESPONSE:
            response.close()
            raise ValueError("元数据响应过大，已停止解析。")
        return response

    def fetch(self, url, referer=None, limit=MAX_RESPONSE, return_url=False, **kwargs):
        headers = {"Referer": referer} if referer else {}
        with self.open(url, headers) as response:
            data = response.read(min(limit, MAX_RESPONSE) + 1)
            self.received += len(data)
            if len(data) > min(limit, MAX_RESPONSE) or self.received > MAX_TOTAL:
                raise ValueError("解析的元数据量超过限制。")
            return (data, response.geturl()) if return_url else data


@contextmanager
def native_transport(ydl, allow_private=False):
    """Keep yt-dlp metadata extraction inside the same anonymous byte limits."""
    import io
    from yt_dlp.networking.common import Response
    client = MetadataClient(allow_private, ydl.cookiejar)
    original = ydl.urlopen
    def urlopen(request):
        url = request if isinstance(request, str) else request.url
        headers = dict(getattr(request, "headers", {}))
        with client.open(url, headers, getattr(request, "data", None), getattr(request, "method", None)) as response:
            data = response.read(MAX_RESPONSE + 1)
            client.received += len(data)
            if len(data) > MAX_RESPONSE or client.received > MAX_TOTAL:
                raise ValueError("解析的元数据量超过限制。")
            return Response(io.BytesIO(data), response.geturl(), dict(response.headers), response.status)
    ydl.urlopen = urlopen
    try:
        yield client
    finally:
        ydl.urlopen = original
