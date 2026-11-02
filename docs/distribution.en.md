# Distribution

[简体中文](../发布说明.md) · [Home](../README.en.md)

## Release types

The first public version, v0.1.0, is a **source release**. It requires Python 3.13 (64-bit), FFmpeg/FFprobe, Google Chrome, Node.js and installation of Python dependencies with `setup_vendor.cmd`. It is not a self-contained portable EXE.

GitHub's source ZIP and the source ZIP attached to the release contain source, documentation, tests and license notices, without complete installed dependencies or system programs.

`build_release.cmd` creates a different, local runtime ZIP: it includes installed `vendor` dependencies. That ZIP still needs Python and the external programs. Do not describe it as an install-free executable.

## Whose account is used?

Recipients explicitly sign in or import their own sessions on their own computers. No publisher account or cookies are built into the source.

Sessions in `.auth` are encrypted for the current Windows user. The same Windows account and installation share saved sessions. YouTube's local saved session can be cleared before changing accounts. This is not a multi-user server account system.

## Packaging

Run `build_release.cmd` to package the allowed source, UI assets, bilingual documentation, license notices and installed dependencies. Output goes into `releases`. The builder checks the ZIP and excludes `.auth`, `.web`, Git data, logs, Python caches and downloaded output.

Do not zip an entire live directory. Encryption is not a reason to distribute saved sessions. `.gitignore` does not untrack data already committed; inspect the contents and history before publishing.

Third-party programs and dependencies keep their own licenses. Review redistribution obligations for any runtime bundle; see [third-party notices](../THIRD_PARTY_NOTICES.md).

## YouTube sessions

Use Settings to open YouTube in the default browser. Manually sign in, complete the site's required checks and confirm the work plays. Export only your own YouTube cookies in Netscape format and import the file locally. Only the `youtube.com` session is retained; the original exported file remains your responsibility.

Enable the YouTube login fallback before retrying. It uses the saved session only after anonymous extraction fails, and does not replace access eligibility, age confirmation or membership. Cookies can expire or rotate. See [yt-dlp's official guide](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies).

## Server deployment

Local saved sessions must not become one shared identity for all visitors. User identity, isolated session storage, access controls and costs require separate design before a public service is deployed. The resolve-only path also remains experimental.
