# Local web workspace

[简体中文](../本地网页说明.md) · [Home](../README.en.md)

`start_web.cmd` runs download mode at `http://127.0.0.1:8765`. `start_resolver.cmd` runs experimental resolve-only mode at `http://127.0.0.1:8766`.

## Download mode

Submit an individual video URL or share text. Tasks run sequentially; the page shows progress, errors, history and retry controls. Files default to the parent of the project folder. “Open file” and “Open folder” operate on the computer running the service.

The interface is currently Chinese. `开始下载` means Start download, `详情` means Details, `重试` means Retry, and `登录与辅助` contains login settings.

## Login

Settings provide personal Douyin, X and YouTube sessions. Users complete authentication manually. For YouTube, signing in with the usual browser and importing an exported Netscape cookie file is available; Google may reject independent automation browsers.

## Local data

`.auth` stores encrypted sessions. `.web` stores task records, logs and screenshots. Command-line errors use `download-error.log`. These files are excluded from public source and packages. Do not share an archive of a live installation directory.

## Separate copies and ports

Keep test copies in separate directories and use an unoccupied port:

```powershell
python -X utf8 web_app.py --launch --port 8770
python -X utf8 web_app.py --launch --resolve-only --port 8771
```

The page has an exit-service control. The server binds only to `127.0.0.1` and uses origin checks and a request token. It is a single-machine tool. Public deployment requires separately designed user isolation, quotas, network egress restrictions and cost controls; changing the listening address alone is insufficient.
