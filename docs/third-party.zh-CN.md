# 第三方许可说明

简体中文 · [English](../THIRD_PARTY_NOTICES.md) · [首页](../README.md)

项目 MIT 许可证适用于原创代码，不替代依赖或外部程序的许可证。

## yt-dlp 适配文件

`vendor/yt_dlp/extractor/tiktok.py` 基于 [yt-dlp](https://github.com/yt-dlp/yt-dlp)，包含本地抖音签名和目标作品校验适配。上游源码采用 Unlicense，原文保留在 `third_party/yt-dlp-LICENSE`。

`requirements-vendor.txt` 记录依赖版本；`setup_vendor.py` 安装依赖时会保留适配文件。源码仓库不包含完整第三方包或系统可执行程序。

## 安装依赖与外部程序

安装器从配置的软件包索引获取 Python 包，分发元数据中保留各包的许可证。Playwright、Requests、加密库、yt-dlp 和插件等均遵循各自许可。

FFmpeg/FFprobe、Chrome、Node.js 和 Python 为外部运行条件，不随源码仓库打包。FFmpeg 的许可证取决于所选构建，请查看该构建的说明。

`build_release.py` 可生成包含已安装 Python 依赖及其现有许可文件的运行包。分发者需要自行检查这些依赖的再分发要求；不能用项目 MIT 许可证代表整个运行包的许可。
