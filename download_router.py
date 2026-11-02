"""Choose a site's workflow before doing any network requests."""

import argparse
import importlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

from site_handlers.common import Options
from url_input import extract_url
from download_events import emit

ROOT = Path(__file__).resolve().parent


def select_route(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ("https", "http") or not parsed.hostname:
        raise ValueError("请粘贴完整的 http:// 或 https:// 视频网址。")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("请使用普通网页地址，不要在网址中填写账号密码。")
    host = parsed.hostname.lower().rstrip(".")
    routes = json.loads((ROOT / "site_routes.json").read_text(encoding="utf-8"))
    for route in routes["sites"]:
        handler = route.get("handler", "")
        if (
            not isinstance(handler, str)
            or not re.fullmatch(r"[a-z][a-z0-9_]*", handler)
            or not (ROOT / "site_handlers" / (handler + ".py")).is_file()
        ):
            raise ValueError("站点配置指定了不存在的处理模块。")
        for domain in route["domains"]:
            domain = domain.lower().rstrip(".")
            if host == domain or host.endswith("." + domain):
                return route
    if parsed.path.lower().endswith(".m3u8"):
        return {"id": "direct_hls", "name": "直接 HLS 清单", "handler": "hls"}
    return {"id": "unknown", "name": "未适配网站", "handler": "generic"}


def dispatch(url, options):
    original = url
    url = extract_url(url)
    if original.strip().strip("\"'") != url:
        print("已从分享内容中提取目标链接。", flush=True)
    route = select_route(url)
    emit("route", name=route["name"], handler=route["handler"], url=url)
    print(f"已识别：{route['name']} → {route['handler']} 模块", flush=True)
    module = importlib.import_module("site_handlers." + route["handler"])
    result = module.run(url, options)
    outputs = [str(item) for item in result] if isinstance(result, (list, tuple)) else (
        [str(result)] if result is not None else [])
    emit("completed", output=outputs[0] if outputs else None, outputs=outputs)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", nargs="?")
    parser.add_argument("-o", "--output")
    parser.add_argument("--referer")
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--show-browser", action="store_true")
    parser.add_argument("--manual-browser", action="store_true")
    parser.add_argument("--browser-wait", type=int, default=25)
    parser.add_argument("--route-only", action="store_true")
    parser.add_argument("--login-x", action="store_true")
    parser.add_argument("--login-douyin", action="store_true")
    parser.add_argument("--login-youtube", action="store_true")
    parser.add_argument("--use-youtube-login", action="store_true")
    parser.add_argument("--use-x-login", action="store_true")
    args = parser.parse_args()
    if args.login_youtube:
        from site_handlers.youtube_session import login
        emit("interaction", waiting=True, message="请在独立 YouTube 窗口登录，并点击工具页里的保存按钮。")
        login(target_url=extract_url(args.url) if args.url else None)
        return
    if args.login_douyin:
        from site_handlers.douyin_session import login
        emit("interaction", waiting=True, message="请在独立抖音窗口登录，并点击工具页里的保存按钮。")
        login(target_url=extract_url(args.url) if args.url else None)
        return
    if args.login_x:
        from site_handlers.x_session import login
        emit("interaction", waiting=True, message="请在独立 X 窗口登录并保存会话。")
        login(target_url=args.url)
        return
    options = Options(
        args.output, args.referer, args.jobs, args.check,
        args.show_browser or args.manual_browser, max(5, min(args.browser_wait, 180)),
        args.use_x_login, args.manual_browser,
        use_youtube_login=args.use_youtube_login,
    )
    if args.url:
        url = args.url.strip().strip("\"'")
        if args.route_only:
            print(json.dumps(select_route(extract_url(url)), ensure_ascii=False))
        else:
            dispatch(url, options)
        return
    print("粘贴网址后按域名选择专用模块；未适配网站进入通用流程。", flush=True)
    print("成功或失败后均可继续输入；输入空行退出。", flush=True)
    while True:
        try:
            url = input("\n请粘贴视频网址，然后按回车：\n").strip().strip("\"'")
        except EOFError:
            return
        if not url:
            return
        try:
            dispatch(url, options)
        except Exception as error:
            from download_hls import report_error
            report_error(error)
            print("窗口保持打开，可以粘贴另一个地址重试。", flush=True)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        main()
    except KeyboardInterrupt:
        emit("error", message="已取消。")
        print("\n已取消。", file=sys.stderr)
        sys.exit(130)
    except Exception as error:
        emit("error", message=str(error)[-5000:])
        from download_hls import report_error
        report_error(error)
        sys.exit(1)
