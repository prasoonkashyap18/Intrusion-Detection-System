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
- **PostgreSQL** may be considered once the project evolves beyond the MVP; it is not currently configured or supported.

### Entities

Three dataset-independent ORM models exist (`backend/app/models/`):

- **`DetectionBatch`** (`detection_batches`) — represents one uploaded processing job (filename, status, record counts, timestamps).
- **`DetectionResult`** (`detection_results`) — represents one individual detection within a batch (predicted class, confidence, severity, optional flow-context fields, which model produced it).
- **`ModelMetadata`** (`model_metadata`) — identifies a trained ML model used for inference (name, version, dataset reference, evaluation metrics). No records exist until an actual model is trained — metrics are never fabricated.

The exact ML feature schema (beyond the optional source/destination/port/protocol context fields already present) will be defined once a specific dataset is selected in a later step; these models intentionally do not assume any particular dataset's columns.

## Running Tests

From the repository root, with the backend virtual environment activated:

```bash
python -m pytest tests/backend -v
```

Database tests use an isolated in-memory SQLite database and never touch the real development database file.

## Current Limitations

- No detection API, CSV upload, ML training/inference, or dashboard logic exists yet — only API/app foundation and database models.
- No records exist in any table; nothing inserts sample or fake data.
- No authentication/authorization exists yet.
- CORS is configured for local development origins only; it has not been reviewed or hardened for production use.
- Only one public endpoint (`/api/v1/health`) exists.
