"""Aggregates all v1 API routers.

Only the health router exists at this step — future routers (detection,
analytics, model metadata, etc.) will be added here as they are implemented.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import health

api_router = APIRouter()
api_router.include_router(health.router, tags=["Health"])
