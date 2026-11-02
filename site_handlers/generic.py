"""Fallback strategies are used only for domains without a dedicated route."""


def run(url, options):
    from . import browser, hls, yt_engine, web_session
    strategies = (
        ("网页配置 / HLS 解析", lambda: hls.run_webpage(url, options)),
        ("yt-dlp 通用解析", lambda: yt_engine.run(url, options)),
        ("浏览器加载与网络监听", lambda: browser.run(url, options)),
    )
    if web_session.ClearanceStore().load(url):
        print("正在复用这个网站的验证状态，直接读取网页配置。", flush=True)
    errors = []
    for index, (name, operation) in enumerate(strategies, start=1):
        print(f"通用流程 {index}/{len(strategies)}：{name}", flush=True)
        try:
            return operation()
        except browser.BrowserInteractionCancelled:
            raise
        except Exception as error:
            errors.append(f"{name}：{error}")
            print(f"该步骤未成功：{str(error)[-500:]}", flush=True)
    raise RuntimeError("通用流程均未成功：\n" + "\n\n".join(errors)[-6000:])
