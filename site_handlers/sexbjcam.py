"""The site's verified article -> player -> cleaned HLS workflow."""


def run(url, options):
    from urllib.parse import urlsplit
    import download_hls as backend
    from .hls import run as download_hls
    page, actual_url = backend.fetch(
        url, options.referer or "https://sexbjcam.com/", 2000000, return_url=True
    )
    frames = backend.embedded_players(page.decode("utf-8-sig", "replace"), actual_url)
    for frame in frames:
        host = (urlsplit(frame).hostname or "").lower()
        if host == "playerrecord.cfd" or host.endswith(".playerrecord.cfd"):
            from .playerrecord import run as download_playerrecord
            return download_playerrecord(frame, options)
    return download_hls(url, options)
