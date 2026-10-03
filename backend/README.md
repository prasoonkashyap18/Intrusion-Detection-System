# Backend

The FastAPI backend for AI-IDS. It currently exposes a health check, a CSV upload endpoint that registers a pending detection batch, and a paginated batch listing. ML inference, detection results, and dashboard endpoints are not implemented yet; see [ARCHITECTURE.md](../ARCHITECTURE.md) for the planned design.

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

Configuration is read from environment variables (see [.env.example](../.env.example) at the repository root for the documented, non-secret options: `APP_NAME`, `ENVIRONMENT`, `API_V1_PREFIX`, `DEBUG`, `CORS_ORIGINS`, `DATABASE_URL`, `UPLOAD_DIR`, `MAX_UPLOAD_MB`). The backend runs with sensible local-development defaults even if no environment variables are set. Never commit a real `.env` file.

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

## CSV Upload

```
POST /api/v1/detection/upload        (multipart/form-data, field: file)
```

Registers an uploaded network-flow CSV as a **pending** detection batch. **The traffic is not analyzed**: no model runs, no `DetectionResult` rows are created, and the batch keeps `processed_records = 0` and `failed_records = 0` until a later processing step exists.

**Response (`201`)**
```json
{
  "batch_id": "3f2b8c1e-6a4d-4e0b-9d7a-2c5f1a8b9e30",
  "filename": "traffic.csv",
  "status": "pending",
  "total_records": 1234,
  "processed_records": 0,
  "failed_records": 0,
  "created_at": "2026-10-03T08:15:30Z"
}
```

**Validation** (the frontend repeats the cheap checks for fast feedback, but only these count):

| Rule | Failure |
|---|---|
| A `file` field with a filename is present | `400 missing_file` / `missing_filename` |
| Extension is `.csv` (case-insensitive) | `415 unsupported_media_type` |
| Content type, if sent, is not clearly something else (CSV, `text/plain`, `application/vnd.ms-excel` and `application/octet-stream` are accepted) | `415 unsupported_media_type` |
| File is not larger than the limit — **50 MB** by default, set with `MAX_UPLOAD_MB` | `413 file_too_large` |
| File is not empty | `400 empty_file` |
| Valid comma-delimited text: a header with at least 2 columns, no NUL bytes, well-formed quoting, and every row has the header's column count (blank lines are ignored) | `400 invalid_csv` |
| At least one data row | `400 no_data_rows` |
| Registering fails (storage or database) | `500 upload_failed` |

Errors always use `{"error": "<code>", "message": "<text>"}`, with messages safe to show users (no paths, SQL or stack traces). Any other malformed request returns `422 invalid_request`.

No dataset-specific columns are required: "valid" means structurally valid. Dataset-specific feature validation belongs to the later data-processing step. UTF-8 (with or without a BOM) is read directly; other encodings fall back to Latin-1 because some public IDS datasets are not UTF-8. Rows are counted with Python's standard `csv` module (no pandas).

**Storage and batch lifecycle**

- The file is stored at `backend/data/uploads/<batch_id>.csv` (`UPLOAD_DIR`). The name is generated server-side from the batch's own UUID, so the client's filename never touches the filesystem and a later step can find a batch's file from its ID alone. Uploaded files are untrusted user data and are git-ignored.
- The database stores only metadata (`DetectionBatch`): the sanitized display filename (directories and unsafe characters removed), `status = pending`, the data-row count, zeroed processed/failed counts, and the UTC creation time. The CSV is never stored in SQLite.
- Registration is all-or-nothing: the upload is streamed to a `.part` file, validated, atomically renamed, and only then committed. If any step fails, the partial file is removed and no batch row is created, so a success response always means a stored file *and* a committed batch.
- Uploading the same file twice creates two batches. No hashing or deduplication is done.

**Limitations**
- The size limit is enforced while the file is copied to disk, after the framework has received the request body. There is no streaming rejection of oversized bodies.
- The upload is a synchronous request; very large files hold a worker thread while they are copied and scanned.
- No authentication, rate limiting or virus scanning.

## Listing Batches

```
GET /api/v1/detection/batches?page=1&page_size=20
```

Returns persisted batches from SQLite, **newest first** (`ORDER BY created_at DESC, id DESC`, so ties never overlap across pages). Paging is done in SQL with `LIMIT/OFFSET`, plus one `COUNT` query: two queries per request, no relationships loaded.

| Parameter | Default | Limits |
|---|---|---|
| `page` | `1` | 1-based, `1`–`1,000,000` |
| `page_size` | `20` | `1`–`100` |

Out-of-range or non-numeric values return `422 invalid_request`. A page beyond the last returns `200` with empty `items` and the real totals.

```json
{
  "items": [
    {
      "batch_id": "3f2b8c1e-6a4d-4e0b-9d7a-2c5f1a8b9e30",
      "filename": "traffic.csv",
      "status": "pending",
      "total_records": 1234,
      "processed_records": 0,
      "failed_records": 0,
      "created_at": "2026-10-03T08:15:30Z",
      "completed_at": null
    }
  ],
  "page": 1,
  "page_size": 20,
  "total_items": 42,
  "total_pages": 3
}
```

- An empty database is a normal `200` with `items: []` and `total_pages: 0` — never `404` — so clients can tell "no batches" from "could not reach the API".
- Statuses are reported exactly as stored. `pending` means registered and waiting for processing; nothing in this endpoint changes a batch's status.
- The response never includes stored file paths or a batch's `error_message`. A database failure returns `500 batches_unavailable` with a generic message (details are logged only).
- The generic `PageInfo` schema was not used: it describes offset/limit, while this endpoint reports page-number metadata.

## Retrieving a Single Batch

```
GET /api/v1/detection/batches/{batch_id}
```

Returns one persisted batch by id, exactly as stored — the same shape as an item in the list endpoint above (`DetectionBatchSummary`).

| Status | Meaning |
|---|---|
| `200` | The batch, as stored |
| `404 batch_not_found` | No batch exists with that id |
| `422 invalid_request` | `batch_id` is not a valid UUID |
| `500 batch_unavailable` | The database could not be read; details are logged only |

- `batch_id` is typed as a UUID path parameter, so FastAPI rejects a malformed id with `422` before any database lookup runs.
- The response never includes stored file paths or a batch's `error_message`.

## API Schemas

Pydantic schemas (`backend/app/schemas/`) define the API's data contracts — what requests/responses look like at the HTTP boundary — and are kept **independent of the SQLAlchemy ORM models** (`backend/app/models/`). This separation means the API contract and the database schema can evolve independently.

- `DetectionResultBase` / `DetectionResultResponse`, `DetectionBatchSummary`, `ModelMetadataResponse` — response schemas for the three entities above.
- Input validation happens at this API boundary: `confidence` is validated to `0.0–1.0` by the schema itself (not just the database constraint), and `severity`/`status` only accept the same controlled values as the database (`low`/`medium`/`high`/`critical` and `pending`/`processing`/`completed`/`failed`, respectively).
- Response schemas use Pydantic v2's `from_attributes=True` so they can be built directly from ORM objects without manual field-by-field conversion.
- `ModelMetadataResponse` intentionally **omits `artifact_path`** — the internal filesystem/storage path for a saved model artifact is not exposed over the public API (see the docstring in `app/schemas/model.py` for the full rationale).
- No API endpoints exist yet — these schemas are not wired into any route in this step.

## Running Tests

From the repository root, with the backend virtual environment activated:

```bash
python -m pytest tests/backend -v
```

Database, upload and batch-listing tests use an isolated in-memory SQLite database and a temporary upload directory; they never touch the real development database or `backend/data/uploads/`.

## Current Limitations

- No ML training/inference, detection results, or dashboard logic exists yet. Uploaded CSVs are registered as `pending` batches and are not analyzed.
- Only batches created by real uploads exist; nothing inserts sample or fake data.
- No authentication/authorization exists yet.
- CORS is configured for local development origins only; it has not been reviewed or hardened for production use.
- Four public endpoints exist: `GET /api/v1/health`, `POST /api/v1/detection/upload`, `GET /api/v1/detection/batches` and `GET /api/v1/detection/batches/{batch_id}`.
