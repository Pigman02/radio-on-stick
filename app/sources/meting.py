"""Optional private provider. Implementation is not tracked in git."""
from __future__ import annotations

try:
    from .private_impl.meting import MetingSource
except ImportError:
    class MetingSource:
        name = "meting"
        display_name = "Meting"

        def __init__(self, *args, **kwargs):
            pass

        def search(self, query, limit=5):
            return []

        def resolve(self, candidate):
            raise RuntimeError("private provider unavailable")
