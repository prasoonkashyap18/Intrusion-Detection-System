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

    # PCAP ingestion (Step 41) — the idle-timeout that determines when a
    # packet-derived flow is considered finished (see app.services.
    # flow_construction's module docstring for the full policy).
    pcap_flow_idle_timeout_seconds: float = float(os.getenv("PCAP_FLOW_IDLE_TIMEOUT_SECONDS", "120"))

    # Threat intelligence (Step 40) — disabled by default, and disabled
    # whenever a required value is missing, so ordinary detection/alert
    # generation is completely unaffected unless this is explicitly
    # configured. See backend/README.md's "Threat Intelligence" section.
    threat_intel_enabled: bool = os.getenv("THREAT_INTEL_ENABLED", "false").lower() == "true"
    threat_intel_provider: str = os.getenv("THREAT_INTEL_PROVIDER", "abuseipdb")
    # Never hard-coded, never committed: read from the environment only.
    threat_intel_api_key: str | None = os.getenv("THREAT_INTEL_API_KEY") or None
    threat_intel_timeout_seconds: float = float(os.getenv("THREAT_INTEL_TIMEOUT_SECONDS", "5"))
    threat_intel_max_retries: int = int(os.getenv("THREAT_INTEL_MAX_RETRIES", "1"))
    threat_intel_cache_ttl_seconds: float = float(os.getenv("THREAT_INTEL_CACHE_TTL_SECONDS", "3600"))


settings = Settings()
