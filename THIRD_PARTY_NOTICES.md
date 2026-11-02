# Third-party notices

The project's MIT license covers original project code. It does not replace the licenses of dependencies or external programs.

## yt-dlp backport

`vendor/yt_dlp/extractor/tiktok.py` is derived from [yt-dlp](https://github.com/yt-dlp/yt-dlp), with a local Douyin signature and target-identity adaptation. The upstream source is distributed under the Unlicense; its license text is retained in `third_party/yt-dlp-LICENSE`.

Dependency versions are recorded in `requirements-vendor.txt`. `setup_vendor.py` preserves this adapted source file while installing the matching dependencies. No complete third-party package or executable is checked into this source repository.

## Installed dependencies and tools

The installer retrieves Python packages from the configured package index. Their distribution metadata includes each package's own license. Playwright, Requests, cryptographic libraries, yt-dlp and its plugins retain their respective licenses.

FFmpeg/FFprobe, Google Chrome, Node.js and Python are external prerequisites, not bundled by the source repository. FFmpeg licensing varies by build; use the documentation and license of the build you install.

`build_release.py` can create a local runtime ZIP containing installed Python dependencies and their available license notices. Anyone distributing such a runtime package must review the dependencies and their redistribution obligations; the MIT license alone does not cover the whole runtime package.
