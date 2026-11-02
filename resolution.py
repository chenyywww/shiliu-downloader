"""Serializable download plans. These contain data, never executable commands or cookies."""
from datetime import datetime, timezone
from urllib.parse import urlsplit
from resolve_net import validate_url

SCHEMA = "local-video-download-plan/v1"
HEADERS = {"referer": "Referer", "origin": "Origin", "user-agent": "User-Agent"}


def clean_headers(headers):
    result = {}
    for key, value in (headers or {}).items():
        if key.lower() not in HEADERS:
            continue
        if not isinstance(value, str) or len(value) > 8000 or any(c in value for c in "\r\n\0"):
            raise ValueError("下载参数中的请求头无效。")
        result[HEADERS[key.lower()]] = value
    return result


def source(url, kind="file", role="combined", headers=None, width=None, height=None):
    return {"url": url, "kind": kind, "role": role, "headers": clean_headers(headers), "width": width, "height": height}


def plan(input_url, provider, sources=None, title=None, reason=None, strategy=None):
    sources = sources or []
    return {"schema": SCHEMA, "input_url": input_url, "provider": provider, "title": title or provider,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "ready" if sources else "local_required", "strategy": strategy or ("sources" if sources else "local"),
            "sources": sources, "reason": reason,
            "cross_network_verified": False, "expires_at": None,
            "notice": "临时链接有效期由源站决定。跨网络可用性尚未验证；失败时在本机重新解析。"}


def validate_plan(value, allow_private=False):
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        raise ValueError("不是本工具支持的解析任务文件。")
    validate_url(value.get("input_url"), allow_private)
    sources = value.get("sources")
    if not isinstance(sources, list) or len(sources) > 12:
        raise ValueError("解析结果中的资源列表无效。")
    if value.get("status") not in {"ready", "local_required"} or value.get("strategy") not in {"sources", "local"}:
        raise ValueError("解析结果中的处理方式无效。")
    if value["status"] == "ready" and not sources:
        raise ValueError("解析结果没有资源地址。")
    for item in sources:
        validate_url(item.get("url"), allow_private)
        if item.get("kind") not in {"file", "hls", "dash"} or item.get("role") not in {"video", "audio", "combined"}:
            raise ValueError("解析结果中的流类型无效。")
        headers = item.get("headers", {})
        if not isinstance(headers, dict) or set(name.lower() for name in headers) - set(HEADERS):
            raise ValueError("任务文件不能携带 Cookie、账号凭证或其他请求头。")
        item["headers"] = clean_headers(headers)
    return value
