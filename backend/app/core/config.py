"""Centralized application configuration.

Settings are read from environment variables, with sensible defaults for
local development. No secrets are defined here — see .env.example for the
documented, non-secret configuration surface.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _parse_origins(raw: str) -> list[str]:
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "AI-IDS API")
    environment: str = os.getenv("ENVIRONMENT", "development")
    api_v1_prefix: str = os.getenv("API_V1_PREFIX", "/api/v1")
    debug: bool = os.getenv("DEBUG", "true").lower() == "true"
    cors_origins: list[str] = field(
        default_factory=lambda: _parse_origins(
            os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:5173")
        )
    )


settings = Settings()
