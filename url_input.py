"""Extract one intended URL from a URL, Markdown link, or copied share text."""
import re
from urllib.parse import parse_qs, urlsplit


def douyin_target(url):
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if host == "v.douyin.com":
        return bool(re.fullmatch(r"/[A-Za-z0-9_-]+/?", parsed.path))
    if host in ("douyin.com", "www.douyin.com", "iesdouyin.com", "www.iesdouyin.com"):
        if re.match(r"/(?:share/)?video/\d+", parsed.path):
            return True
        return any(value.isdigit() for value in parse_qs(parsed.query).get("modal_id", []))
    return False


def extract_url(text):
    text = text.strip().strip("\"'")
    if re.fullmatch(r"https?://[^\s<>]+", text) and "](http" not in text:
        return text
    text = re.sub(r"\[[^\]]*\]\((https?://[^\s)]+)\)", r" \1 ", text)
    urls = []
    for match in re.finditer(r"https?://[^\s<>\"'\[\]“”‘’]+", text):
        url = match.group().rstrip("，。；！？」』】》")
        while url.endswith(")") and url.count(")") > url.count("("):
            url = url[:-1]
        if url not in urls:
            urls.append(url)
    if len(urls) == 1:
        return urls[0]
    if len(urls) > 1:
        hosts = {(urlsplit(url).hostname or "").lower().rstrip(".") for url in urls}
        targets = [url for url in urls if douyin_target(url)]
        if len(targets) == 1 and hosts <= {
            "douyin.com", "www.douyin.com", "v.douyin.com", "iesdouyin.com", "www.iesdouyin.com"
        }:
            return targets[0]
        raise ValueError("检测到多个不同的网址，请每次只粘贴一个目标视频的分享内容。")
    raise ValueError("没有找到完整网址。可直接粘贴包含 http:// 或 https:// 链接的分享文案。")
