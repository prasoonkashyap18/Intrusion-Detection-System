"""Application errors that become clean, structured API responses.

Responses always use the `{"error": <code>, "message": <text>}` shape of
`app.schemas.common.ErrorResponse`. Messages are written for end users:
never put exception text, filesystem paths or SQL in them.
"""

from __future__ import annotations


class ApiException(Exception):
    def __init__(self, status_code: int, error: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error = error
        self.message = message
