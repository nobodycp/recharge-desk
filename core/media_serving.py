"""Production media serving with reliable Content-Type headers.

Django's ``serve`` view relies on :mod:`mimetypes`, which omits ``.webp`` on
some Python builds. Browsers then receive ``application/octet-stream`` for
branding favicons and refuse to render them in the tab even when the file
exists and the ``<link rel="icon">`` tag is present.
"""

from __future__ import annotations

import mimetypes
from pathlib import PurePosixPath

from django.views.static import serve

# Extensions that must always map to image/* for favicon / logo delivery.
_MEDIA_TYPE_OVERRIDES = {
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".svg": "image/svg+xml",
}


def register_media_mimetypes() -> None:
    """Ensure common image extensions are known to :mod:`mimetypes`."""
    for ext, mime in _MEDIA_TYPE_OVERRIDES.items():
        mimetypes.add_type(mime, ext)


def serve_media(request, path, document_root=None, show_indexes=False):
    """Like Django ``serve``, but always sets a sensible ``Content-Type``."""
    response = serve(
        request,
        path,
        document_root=document_root,
        show_indexes=show_indexes,
    )
    if response.status_code != 200:
        return response

    ext = PurePosixPath(path).suffix.lower()
    content_type = _MEDIA_TYPE_OVERRIDES.get(ext) or mimetypes.guess_type(path)[0]
    if content_type:
        response["Content-Type"] = content_type
    return response
