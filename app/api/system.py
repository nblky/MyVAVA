from __future__ import annotations

"""Compatibility wrapper that aggregates shared runtime and browser live routers."""

from fastapi import APIRouter

from ..cloud_platform.live_runtime import router as cloud_live_router
from .runtime_shared import router as runtime_shared_router


router = APIRouter(tags=["system"])
router.include_router(runtime_shared_router)
router.include_router(cloud_live_router)


__all__ = ["router"]
