# Installation and usage

[简体中文](../使用说明.md) · [Home](../README.en.md)

## Install the source release

Use 64-bit Windows with Python 3.13, Google Chrome, Node.js, FFmpeg and FFprobe. Python, Node.js, FFmpeg and FFprobe must be available from a command prompt. For example:

```powershell
python --version
node --version
ffmpeg -version
ffprobe -version
```

Extract the source ZIP into a separate folder, then run `setup_vendor.cmd`. Python dependencies install into the project's `vendor` folder. The installer preserves the project's adapted Douyin extractor. This release does not bundle the Python runtime or external programs and is not a portable EXE.

## Use the workspace

Run `start_web.cmd` and open `http://127.0.0.1:8765`. Paste a specific video URL or share text. The queue shows progress, merging, errors and retries. Finished files can be opened from the page and are saved to the parent of the project folder by default.

The backend downloads on the computer running the program. “Open file” opens a file on that computer; it does not transfer a server file to a remote browser. The interface is currently in Chinese.

## Command line

Run `download.cmd`, or use these commands from the project folder:

```powershell
python -X utf8 download_router.py "VIDEO_URL"
python -X utf8 download_router.py "VIDEO_URL" --check
python -X utf8 download_router.py "VIDEO_URL" -o "my-video.mp4"
python -X utf8 download_router.py "VIDEO_URL" --show-browser
python -X utf8 download_router.py "VIDEO_URL" --manual-browser
python -X utf8 download_router.py "X_POST_URL" --use-x-login
python -X utf8 download_router.py "YOUTUBE_VIDEO_URL" --use-youtube-login
python -X utf8 download_router.py "M3U8_URL" --referer "PLAYER_PAGE_URL"
```

`--check` is handler-dependent and may fetch short media samples. It is not the metadata-only API. Use the [resolve-only experiment](resolve-only.en.md) when only metadata is wanted. Output may be MP4 or MKV, depending on the selected media.

## Link types

- YouTube: individual watch URLs, short links, shorts, live and embed URLs. Channel and batch-playlist downloading are outside this entry point.
- Douyin: a specific video page, short share link or share text containing one work link. Recommendation homepages and `blob:` URLs are not download URLs.
- X: individual posts. Multiple videos in one post are saved separately.
- Article pages: adapted players or generic page configuration, yt-dlp and browser discovery.
- m3u8: a playlist, not a finished MP4. The local downloader fetches and merges the segments.

See the [support matrix](support-matrix.md); supported routes do not guarantee every work.

## Login and verification

Public content is attempted anonymously first. If needed, manually sign in or import your own session using Settings. Sessions in `.auth` are encrypted for the current Windows user. The same Windows user and installation share saved sessions.

Douyin can use a session explicitly saved by the user after anonymous failure. X and YouTube require their respective login-fallback checkbox or command-line flag. Google may reject an automated independent browser. In that case, manually sign in with your usual browser, follow the [yt-dlp cookie export guide](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies), and import a Netscape-format Cookie TXT through the local page.

Users must satisfy the site's account, age, membership and verification requirements themselves. Never share cookies or include them in a public issue. The X public fallback contacts a third-party service with the post ID; see [privacy information](../README.en.md#accounts-and-privacy).

## Troubleshooting

1. Confirm the same work plays in your browser and use its individual link.
2. Read the task details. Complete any required site verification manually in the independent window.
3. Signed links can expire or be network-bound. Submit the original page again.
4. Resolution depends on source formats. Missing segments or mismatched duration can cause an error.
5. Remove signed URLs, cookies, account identifiers, local paths and private content details before posting logs.

Routing is defined in `site_routes.json`; handlers are in `site_handlers`. Sites change, and an existing adapter is not proof that all works are available.
