def run(url, options):
    import download_hls
    return download_hls.download(
        url, options.output, options.referer, options.jobs, options.check
    )


def run_webpage(url, options):
    import download_hls
    from .web_session import http_state
    with http_state(url, options.referer) as (headers, cookiefile):
        return download_hls.download(
            url, options.output, options.referer, options.jobs, options.check,
            headers=headers, cookiefile=cookiefile,
        )
