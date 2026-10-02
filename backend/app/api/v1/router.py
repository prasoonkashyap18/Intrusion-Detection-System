"""Aggregates all v1 API routers.

Health and detection upload exist so far — future routers (analytics, model
metadata, etc.) will be added here as they are implemented.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import detection, health

api_router = APIRouter()
api_router.include_router(health.router, tags=["Health"])
api_router.include_router(detection.router, tags=["Detection"])
