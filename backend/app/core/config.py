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
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./data/ai_ids.db")
    # Uploaded CSVs are stored on the local filesystem (relative to the working
    # directory, like DATABASE_URL). Contents never go into SQLite.
    upload_dir: str = os.getenv("UPLOAD_DIR", "./data/uploads")
    max_upload_mb: int = int(os.getenv("MAX_UPLOAD_MB", "50"))
    # Trained model artifacts (joblib files), relative to the working
    # directory like the paths above. Resolves to the repo-root `models/`
    # directory the project already git-ignores *.joblib under (see
    # models/README.md) — not a new, separate location.
    model_artifact_dir: str = os.getenv("MODEL_ARTIFACT_DIR", "../models")


settings = Settings()
