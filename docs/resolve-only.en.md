# Resolve-only API + local downloading (experimental)

[简体中文](../仅解析说明.md) · [Home](../README.en.md)

The same source includes local-download and metadata-resolution entry points. Resolve-only is experimental and does not promise anonymous cloud extraction for every site.

## Use it

1. Run `start_resolver.cmd` and open `http://127.0.0.1:8766`.
2. Submit an article, player, individual video or share link.
3. Copy the returned resource URL or export the JSON download plan.
4. Run `download_plan.cmd` on the computer that will save the video and select the JSON file. That computer downloads from the source and uses its own FFmpeg when merging is needed.

Normal local downloading remains available through `start_web.cmd` on port 8765 and `download.cmd`.

## Two stages

The resolver reads pages, embedded player configuration, API JSON and playlists. The local client downloads files or segments, handles disguised prefixes and merges tracks.

The resolver does not invoke the downloader or FFmpeg, probe media segments, or use saved personal X, Douyin or YouTube sessions. Browser discovery blocks recognized media/image requests and limits response-body reads. Network handshakes and small prefetch overhead can still occur; metadata-only does not mean zero traffic.

Resolve-only rejects download jobs, account imports and opening server files. Jobs have a 75-second limit. Individual metadata responses are limited to 2 MB, and HTTP/browser stages have total limits. The existing public X service uses its own fixed page/JSON limits. Failure never automatically enables a cloud video relay.

## Results

`ready` means a resource was found. `local_required` means the original page needs to be resolved on the user's machine. Both results can be exported.

Plans include the original page, provider, resource type, track role, declared dimensions and needed User-Agent/Referer/Origin headers. Cookie, Authorization, executable commands, scripts and output paths are not accepted in plans.

Resource metadata is kept for 30 minutes before periodic cleanup clears it. Exported JSON remains under the user's control. Source signatures may expire sooner; opaque signatures are not assigned guessed expiration times.

## Limits

- m3u8 is a playlist, not a finished MP4. The client tries the TS downloader, with local yt-dlp/FFmpeg fallback for other formats.
- Separate video/audio tracks are downloaded locally and merged into MKV. Multiple complete videos are saved separately.
- IP, cookie and header binding can prevent use from another network. Results carry `cross_network_verified: false`.
- `local_required` first uses the existing local workflow. Ordinary page/player failures can open a local independent browser for manual verification. YouTube, Douyin and X retain their local account choices.
- The service binds to loopback and has not been prepared as a public multi-user AWS service.
- HTTP requests validate public addresses, redirects and connected IPs. Public deployment still needs network-level egress isolation, including protection against browser DNS rebinding. The private-network test flag cannot be enabled by web users.

## CLI and API

```powershell
python -X utf8 resolve_router.py "PAGE_URL" > download-plan.json
python -X utf8 download_plan.py download-plan.json
python -X utf8 download_plan.py download-plan.json --use-youtube-login
```

`POST /api/resolve` submits `{ "url": "..." }`. `GET /api/tasks/{id}` reads status; `GET /api/tasks/{id}/plan` exports JSON. State changes require the local page's token and origin checks.

Automated local tests cover static/dynamic pages, controlled cross-origin players, HLS, separate tracks and web-to-client handoff. They verify the resolver does not fetch media segments. A page loading successfully does not prove all embedded players can be resolved.
