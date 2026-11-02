# Changelog / 更新记录

## v0.1.0 — 2026-11-03 — Source release / 源码版

### English

- First tagged source release of the local Windows download workspace and command-line entry.
- Task queue, progress, history, cancellation and retries; dedicated site routes plus generic extraction and local FFmpeg merging.
- Experimental metadata-only plans with a separate local downloader.
- Chinese and English documentation, a support/validation matrix, workspace screenshot and generated local workflow GIF.
- Privacy-clean source history, MIT license and retained third-party notices.

Requires Python 3.13 (64-bit), Chrome, Node.js and FFmpeg/FFprobe, plus `setup_vendor.cmd`. This is not a portable EXE. The interface is currently Chinese. Public multi-user hosting and universal site support are outside this release.

Validation baseline: 59 checks passed in a separate public-source installation with newly installed Python dependencies; an anonymous 1080p short video downloaded and decoded successfully. This release's documentation, packaging and UI-copy changes receive targeted checks. Private test links and sessions are not published.

### 简体中文

- 首个带版本标签的本机 Windows 下载工作台与命令行源码版。
- 包含队列、进度、历史、取消和重试，专用路由与通用解析，以及本机 FFmpeg 合并。
- 保留实验性元数据解析任务与独立本机下载器。
- 新增中英文文档、支持与验证矩阵、工作台截图和本地生成视频的操作 GIF。
- 采用清理后的公开源码历史、MIT 许可证，并保留第三方许可说明。

需要 Python 3.13（64 位）、Chrome、Node.js、FFmpeg/FFprobe，并运行 `setup_vendor.cmd`；不是便携 EXE。界面目前为中文。本次不提供公共多用户托管，也不承诺所有网站均可用。

验证基线：公开源码独立安装及全新 Python 依赖环境下，59 项检查通过，并匿名下载、解码了一段1080p短视频。本次文档、打包和界面文案调整另做针对性检查。不公开私人测试链接或会话。
