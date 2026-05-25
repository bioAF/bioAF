from fastapi import Request


def public_base_url(request: Request) -> str:
    """Best-effort public origin (scheme://host) for links embedded in emails.

    Honors the proxy headers nginx sets in front of the app (X-Forwarded-Proto and
    Host), falling back to the request's own scheme/host when called without a proxy.
    """
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"
