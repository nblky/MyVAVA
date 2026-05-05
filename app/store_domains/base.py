from __future__ import annotations

from typing import Any


class BaseDomainService:
    def __init__(self, store: Any):
        self.store = store
