# 拾流 · Shiliu Downloader

Windows 本机视频下载器。粘贴具体作品页或分享链接，由本机解析并下载，必要时用 FFmpeg 合并。提供本地网页、命令行，以及“仅解析 → 导出任务 → 本机下载”的实验接口。

**每位使用者在自己的电脑运行、使用自己的账号、保存自己的文件。项目不提供公共视频中转服务。**

## 环境

- Windows 64 位，Python 3.13（当前验证环境）。
- FFmpeg 和 FFprobe：从 [FFmpeg 官方下载页](https://ffmpeg.org/download.html)选择适合系统的发行包，将程序加入 PATH。
- 已安装 Google Chrome，供 Playwright 使用。
- Node.js，供 yt-dlp 的部分站点解析使用。
- Python 依赖安装在项目内 `vendor`，不包含在 Git 源码快照中。

## 开始使用

1. 将仓库克隆或下载并解压到自己的目录。
2. 在项目目录运行 `setup_vendor.cmd` 安装锁定版本的 Python 依赖。
3. 双击 `start_web.cmd`，打开本机网页 `http://127.0.0.1:8765`。
4. 粘贴具体视频链接，开始下载。文件默认保存在项目目录的上一级。

也可以双击 `download.cmd`，或在项目目录运行：

```powershell
python -X utf8 download_router.py "具体视频网址"
python -X utf8 download_router.py "具体视频网址" --check
python -X utf8 download_router.py "具体视频网址" -o "我的视频.mp4"
```

源文件可能保存为 MP4 或 MKV，以处理结果为准。首页和推荐流地址不等于具体作品链接。

## 处理方式

| 输入 | 处理流程 |
| --- | --- |
| YouTube | yt-dlp 专用解析 |
| 抖音具体作品或分享链接 | 作品识别、签名适配、必要时使用本人保存的会话 |
| X / Twitter | 匿名解析、公开解析服务后备、本人会话后备 |
| 已适配文章及播放器 | 提取播放器、播放清单，处理 HLS 或分离音视频 |
| 直接 m3u8 | 下载播放清单中的分段，在本机合并 |
| 其他站点 | 网页配置 → yt-dlp → 浏览器检测 |

路由见 `site_routes.json`；实际可用性受站点改动、地区、账号权限、签名有效期等影响。支持路由不保证每个视频都可下载。项目不会解除 DRM 或授予原本没有的访问权限。

## 仅解析实验入口

双击 `start_resolver.cmd`，打开 `http://127.0.0.1:8766`。解析端返回文件地址或播放清单；导出 JSON 后，在实际保存文件的电脑运行 `download_plan.cmd`。

```text
解析端：读取网页/API/播放清单 → 返回小型 JSON
使用者电脑：从原站下载视频 → 合并 → 保存
```

解析端拒绝视频下载和账号导入，不会因解析失败自动变成视频中转。结果可能需要在本机重新解析，尤其是绑定 IP、Cookie 或请求头的资源。它是本机实验功能，尚不是可直接开放到互联网的多用户服务。详见[仅解析说明](仅解析说明.md)。

## 账号与隐私

- 源码和发布包不包含发布者账号、Cookie、任务历史、日志或视频文件。
- `.auth` 保存使用者主动导入的会话，绑定当前 Windows 用户加密；`.web` 保存本机任务和日志。它们均被 Git 忽略。
- YouTube、抖音、X 登录由使用者本人完成；程序不会自动读取个人 Chrome 资料。
- X 的公开解析后备会把帖子编号发送给第三方服务 `xunlangbot.com`。请按自己的隐私需求决定是否使用该流程。
- 不要将整个正在使用的安装目录直接上传或发送。分发前运行 `build_release.cmd`，并检查包内清单。

详见[使用说明](使用说明.md)、[本地网页说明](本地网页说明.md)、[发布说明](发布说明.md)。

## 开发与检查

安装系统程序和 Python 依赖后，在项目目录运行：

```powershell
python -X utf8 -m unittest discover -s tests -v
```

测试会生成本地音视频夹具、启动临时本机网页，并通过独立 Chrome 进行界面检查；不需要个人账号。请使用独立安装目录测试。

## 许可

项目原创代码使用 [MIT License](LICENSE)。`vendor/yt_dlp/extractor/tiktok.py` 基于 yt-dlp，保留其 Unlicense；依赖和外部程序各自遵循原许可，见[第三方说明](THIRD_PARTY_NOTICES.md)。仅下载自己拥有或有权下载的内容，并遵守来源平台规则。
