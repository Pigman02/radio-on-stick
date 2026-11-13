"""Optional private provider. Implementation is not tracked in git."""
from __future__ import annotations

try:
    from .private_impl.netease import NeteaseSource
except ImportError:  # public checkout without the private package
    class NeteaseSource:
        name = "netease"
        display_name = "网易云音乐"

        def __init__(self, *args, **kwargs):
            pass

        def search(self, query, limit=5):
            return []

        def resolve(self, candidate):
            raise RuntimeError("private provider unavailable")

        def lyrics(self, candidate):
            return {"lines": []}
