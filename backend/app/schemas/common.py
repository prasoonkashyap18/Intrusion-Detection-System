"""Small, generic schemas shared across the API.

Kept deliberately minimal — no large response-wrapper framework and no
pagination logic (see PageInfo's docstring).
"""

from __future__ import annotations

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    """Structured error shape returned by API error responses."""

    error: str
    message: str


class PageInfo(BaseModel):
    """Minimal, reusable pagination metadata shape.

    Not wired into any endpoint yet — no pagination logic exists in this
    step. Defined now only so a future detection-list endpoint has an
    agreed-on shape to return rather than inventing one ad hoc.
    """

    total: int
    limit: int
    offset: int
