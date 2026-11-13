"""Optional private provider. Implementation is not tracked in git."""
from __future__ import annotations

try:
    from .private_impl.qq import QQSource
except ImportError:
    class QQSource:
        name = "qq"
        display_name = "QQ音乐"

        def __init__(self, *args, **kwargs):
            pass

        def search(self, query, limit=5):
            return []

        def resolve(self, candidate):
            raise RuntimeError("private provider unavailable")
