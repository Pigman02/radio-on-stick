"""Optional private provider. Implementation is not tracked in git."""
from __future__ import annotations

try:
    from .private_impl.bilibili import (
        BilibiliError,
        BilibiliSource,
        is_bilibili_url,
        normalize_bilibili_url,
    )
except ImportError:
    class BilibiliError(RuntimeError):
        pass

    class BilibiliSource:
        name = "bilibili"
        display_name = "哔哩哔哩"

        def __init__(self, *args, **kwargs):
            pass

        def search(self, query, limit=5):
            return []

        def resolve(self, candidate):
            raise BilibiliError("private provider unavailable")

    def is_bilibili_url(value):
        return False

    def normalize_bilibili_url(value):
        return ""
