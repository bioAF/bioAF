"""Split a storage URI into its container and key, literally.

``urllib.parse.urlparse`` reads ``?`` as a query and ``#`` as a fragment, which object keys may
contain: ``gs://b/run/q?.txt`` came back as key ``run/q``. A storage URI has no query, fragment or
percent-encoding; everything after the first ``/`` past the container is the key.
"""

from __future__ import annotations

from app.exceptions import ValidationError


def split_storage_uri(uri: str, scheme: str, *, label: str = "storage") -> tuple[str, str]:
    """``<scheme>://<container>/<key>`` -> ``(container, key)``; leading key separators dropped."""
    prefix = f"{scheme}://"
    if not uri.startswith(prefix):
        raise ValidationError(f"Not a{'n' if label[0] in 'aeiouAEIOU' else ''} {label} URI: {uri!r}")
    container, _, key = uri[len(prefix) :].partition("/")
    return container, key.lstrip("/")
