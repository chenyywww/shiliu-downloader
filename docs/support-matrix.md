# Site support and validation matrix

[简体中文](support-matrix.zh-CN.md) · English · [Home](../README.en.md)

**Dedicated** means `site_routes.json` selects a named handler. **Generic** means webpage configuration, yt-dlp or browser fallback. Validation covers limited samples, not permanent support for an entire site.

| Site / input | Routing and module | Evidence | Limits |
| --- | --- | --- | --- |
| Douyin / iesdouyin | Dedicated: `douyin` | Fresh public-source installation completed an anonymous 1080p short-video download with audio and full decode | Individual works only; some require the user's session |
| YouTube / youtu.be / youtube-nocookie | Dedicated: `youtube`, using yt-dlp | Successful full-download records from an existing installation; routing and session selection tested automatically | Not every content type retested online; age-restricted sample not verified successful |
| X / Twitter | Dedicated: `x` | Historical complete download through public fallback | Fallback sends the post ID to `xunlangbot.com`; not all posts guaranteed |
| sexbjcam.com | Dedicated article route: `sexbjcam` | Historical local-download success | Resolve-only can still return `local_required`; not counted as verified cloud resolution |
| playerrecord.cfd | Dedicated: `playerrecord` | Separate-track downloading, merging and checks implemented | No independent full online acceptance test of this public build in this pass |
| recordplay.biz / playrecord.biz | Assigned HLS handler: `hls` | Historical complete-download samples | Signed links, player behavior and domains can change |
| Bilibili | Generic yt-dlp, no dedicated route | User-reported success | Not independently retested online in this pass; not a dedicated adapter |
| MissAV and ordinary pages | Generic webpage / HLS / yt-dlp / browser path | Historical page-configuration and HLS full/sample downloads | Pages can require verification; not a site-wide guarantee |
| Direct m3u8 | HLS | Local tests cover segments, disguised prefixes, merging and duration | Encryption, separate audio and other formats may need local fallback |
| Other domains | Generic: `generic` | Local fixtures verify fallback behavior | No guarantee for arbitrary sites |

## Evidence categories

- **Fresh-install check**: source retrieved again from the public repository and run with newly installed Python dependencies.
- **Historical record**: a project sample succeeded previously, rather than every site being retested for the current build.
- **User report**: reported success, not equivalent to an independent maintainer acceptance test.
- **Automated test**: generated local media and synthetic work identifiers, without real user accounts.

Resolution, downloading, short-sample checks, complete decoding and cross-network delivery are separate validation scopes. Source or permission changes can invalidate earlier success. Private work-specific test links are not published.
