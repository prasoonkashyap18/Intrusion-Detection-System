# Backend

The FastAPI backend for AI-IDS. This is the MVP foundation only — it currently exposes a health check endpoint and nothing else. Detection, ML inference, database access, and dashboard endpoints are not implemented yet; see [ARCHITECTURE.md](../ARCHITECTURE.md) for the planned design.

## Technology Used

- Python 3.14
- FastAPI
- Uvicorn (ASGI development server)

## Setup

### 1. Create a virtual environment

From the `backend/` directory:

**Windows (PowerShell):**
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

**Windows (Git Bash) / macOS / Linux:**
```bash
python -m venv .venv
source .venv/bin/activate   # Git Bash on Windows: source .venv/Scripts/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

## Running the Development Server

From the `backend/` directory, with the virtual environment activated:

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

## Health Endpoint

```
GET http://127.0.0.1:8000/api/v1/health
```

Returns:
```json
{"status": "healthy", "service": "ai-ids-api"}
```

This endpoint only confirms the API process itself is running — it does not report on database or ML model status, since neither exists yet.

## API Documentation

While the server is running:
- Swagger UI: http://127.0.0.1:8000/docs
- ReDoc: http://127.0.0.1:8000/redoc

## Configuration

Configuration is read from environment variables (see [.env.example](../.env.example) at the repository root for the documented, non-secret options: `APP_NAME`, `ENVIRONMENT`, `API_V1_PREFIX`, `DEBUG`, `CORS_ORIGINS`, `DATABASE_URL`). The backend runs with sensible local-development defaults even if no environment variables are set. Never commit a real `.env` file.

## Database

- **SQLite** is used for the MVP (via SQLAlchemy), at `backend/data/ai_ids.db` by default (configurable through `DATABASE_URL`).
- The database file is created automatically on application startup — there is nothing to set up manually.
- The database file is intentionally **not committed** to the repository (see `.gitignore`); only the `backend/data/` directory itself is tracked.
- No application tables exist yet. This step only establishes the engine, session factory, and declarative base — detection-related tables (batches, results, model metadata) will be added in a later step.
- **PostgreSQL** may be considered once the project evolves beyond the MVP; it is not currently configured or supported.

## Running Tests

From the repository root, with the backend virtual environment activated:

```bash
python -m pytest tests/backend -v
```

Database tests use an isolated in-memory SQLite database and never touch the real development database file.

## Current Limitations

- No detection, ML, or dashboard logic exists yet — only API/app foundation and database infrastructure.
- No ORM models or tables exist yet.
- No authentication/authorization exists yet.
- CORS is configured for local development origins only; it has not been reviewed or hardened for production use.
- Only one public endpoint (`/api/v1/health`) exists.
