"""Health check endpoint.

Reports only that the API process itself is running. It does not report on
the database or ML model, since neither is implemented yet.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def get_health() -> dict[str, str]:
    return {"status": "healthy", "service": "ai-ids-api"}
