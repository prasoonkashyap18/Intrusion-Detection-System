"""FastAPI application entrypoint for the AI-IDS backend MVP.

The application exposes a health endpoint and CSV upload (which registers a
pending detection batch). ML inference, detection results and dashboard
endpoints are not implemented yet — see ARCHITECTURE.md for the planned design.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.errors import ApiException
from app.core.config import settings
from app.core.logging import configure_logging
from app.db.database import check_connection, init_db

configure_logging(debug=settings.debug)
logger = logging.getLogger("ai_ids")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("%s starting up (environment=%s)", settings.app_name, settings.environment)
    init_db()
    if check_connection():
        logger.info("Database connectivity check succeeded (%s)", settings.database_url)
    else:
        logger.warning("Database connectivity check failed (%s)", settings.database_url)
    yield
    logger.info("%s shutting down", settings.app_name)


app = FastAPI(
    title="AI-Based Network Intrusion Detection & Security Operations Platform",
    description=(
        "MVP backend API for AI-IDS. Currently exposes a health check and CSV upload, "
        "which registers a pending detection batch without analyzing it. ML inference, "
        "detection results and dashboard endpoints will be added in later steps."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.api_v1_prefix)


@app.exception_handler(ApiException)
async def api_exception_handler(request: Request, exc: ApiException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"error": exc.error, "message": exc.message})


@app.exception_handler(RequestValidationError)
async def request_validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Field-level details are omitted on purpose: they can echo request content back.
    return JSONResponse(
        status_code=422,
        content={"error": "invalid_request", "message": "The request was not in the expected format."},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error while processing %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"error": "internal_server_error", "message": "An unexpected error occurred."},
    )
