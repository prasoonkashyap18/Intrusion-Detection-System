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

## Batch Processing Lifecycle

```
POST /api/v1/detection/batches/{batch_id}/process
```

Claims a pending batch and runs CSV ingestion — reading and validating the batch's CSV into the generic representation `app/services/ingestion.py` defines (see [CSV Ingestion](#csv-ingestion) below) — inside the boundary a later step's feature extraction and ML inference will also run (`app/services/batch_processor.py`). **This step performs no analysis**: it reads and validates the CSV's rows, but runs no model and creates no `DetectionResult` rows.

**Lifecycle** (deliberately conservative — no other transitions exist):

```
pending -> processing -> completed   (completing is a future step's job)
pending -> processing -> failed      (processing could not even start, or its CSV failed to ingest)
```

A batch reaches `failed` here if its upload file is missing from disk, its CSV fails to ingest (unreadable, no header, or a malformed/inconsistent row — see below), or an unexpected error occurs — never because "analysis" failed, since none runs. A batch whose CSV ingests cleanly is left `processing`, not `completed`: marking it `completed` would claim IDS analysis happened when only ingestion did, and the current status model has no state between the two. `processed_records` and `failed_records` stay `0` throughout; they mean *records detected*, not *rows ingested*, and nothing increments them until a real detection pipeline exists to earn that truthfully.

| Status | Meaning |
|---|---|
| `200` | Request handled; body's `status` is the batch's true resulting state (`processing` or `failed`) |
| `404 batch_not_found` | No batch exists with that id |
| `409 invalid_batch_state` | The batch is not `pending` (already processing, completed, or failed) |
| `422 invalid_request` | `batch_id` is not a valid UUID |
| `500 processing_unavailable` | The claim could not be written to the database |

```json
{
  "batch_id": "3f2b8c1e-6a4d-4e0b-9d7a-2c5f1a8b9e30",
  "status": "processing",
  "message": "Batch processing started."
}
```

**Concurrency.** The `pending -> processing` transition is one atomic `UPDATE detection_batches SET status = 'processing' WHERE id = :id AND status = 'pending'` (`claim_for_processing`), committed immediately, with the result read from `rowcount` rather than a separate SELECT beforehand. Two requests that both observe `pending` cannot both win: only one UPDATE's `WHERE` clause can still match once the other has committed, so the loser's statement affects zero rows and the service raises `409` instead of double-claiming the batch.

**Where a later step plugs in:** `_ingest_batch_csv()` in `batch_processor.py` currently does nothing with the `NetworkFlowRecord`s it reads beyond proving they all parse. `app/services/feature_extraction.py` (see [Feature Extraction and Normalization](#feature-extraction-and-normalization) below) already exists to turn each record into model-ready features, but is not yet wired into this boundary — a later step replaces this body to call it, run model inference, write `DetectionResult` rows and update `processed_records`/`failed_records` as real rows are analyzed, and is responsible for the `processing -> completed` transition this step intentionally does not make.

## CSV Ingestion

```
Stored CSV -> safe CSV reader -> CSV rows -> validated row structure -> NetworkFlowRecord
```

`app/services/ingestion.py` reads a batch's stored CSV (`<upload_dir>/<batch_id>.csv`) and turns each data row into a `NetworkFlowRecord`: a plain dataclass, independent of the SQLAlchemy ORM, so a later step can consume it without a database session.

```python
@dataclass(frozen=True)
class NetworkFlowRecord:
    row_number: int
    source_ip: str | None
    destination_ip: str | None
    source_port: int | None
    destination_port: int | None
    protocol: str | None
    flow_timestamp: datetime | None
    raw_features: dict[str, str]
```

- **Dataset-independent.** Only a small, generic set of networking column names is recognized (`source_ip`/`src_ip`, `destination_ip`/`dst_ip`/`dest_ip`, `source_port`/`src_port`, `destination_port`/`dst_port`/`dest_port`, `protocol`/`proto`, `timestamp`/`flow_timestamp`; matched case- and whitespace-insensitively). There is no `if dataset == "NSL-KDD"` branch and no hardcoded column list for any specific public IDS dataset. Every other column — `duration`, `service`, `flag`, `label`, or anything else a dataset uses — survives ingestion untouched in `raw_features`, keyed by its original header text. Expanding this mapping for a specific dataset is left to a future dataset-adapter mechanism; this layer only provides the header-to-field mechanism for one to build on. The feature-extraction layer below recognizes a separate, larger set of generic aliases of its own, read directly from `raw_features` — see [Feature Extraction and Normalization](#feature-extraction-and-normalization).
- **Never fabricates a value.** A recognized column whose text cannot be safely interpreted (a non-numeric port, an invalid IP) leaves that field `None` rather than guessing or defaulting to `0`/`""`/`false` — the original text is still available in `raw_features`, so nothing is lost. Timestamp parsing is conservative: only an unambiguous ISO 8601 string is accepted.
- **Structural errors fail the whole operation, not just one row.** A header with too few columns, no header at all, or a data row whose width does not match the header raises `ApiException` (`ingestion_invalid_csv`) rather than silently skipping the bad row — the same standard `upload_service.py`'s own validation already applies at upload time, so a file that reaches ingestion is expected to already satisfy it; this is defense in depth for whenever ingestion runs independently of that path. A missing or unreadable file raises `ingestion_file_missing` / `ingestion_unreadable`. Every error follows the project's existing `ApiException` pattern; messages are user-safe, and filesystem paths and stack traces are logged server-side only, never returned.
- **Streaming.** `ingest_batch()` is a generator: it yields one `NetworkFlowRecord` per data row as the caller consumes it, so a large CSV's rows are never collected into a list in this module. Confirming the file's text encoding (reusing the same `utf-8-sig` → `latin-1` fallback as `csv_validation.py`) does read the file's bytes once upfront — encoding can only be confirmed by seeing the whole file decode, and deciding that mid-stream would mean silently re-yielding earlier rows a second time after restarting with a different encoding. That one bounded read (bounded by the same upload size limit already enforced at upload time) is what buys that correctness; the per-row parse after it is what stays lazy.
- **No ML inference.** This module does not predict, classify, score, or create `DetectionResult` rows. `batch_processor.py`'s `_ingest_batch_csv()` currently exhausts the iterator and discards every record, which exists only to prove every row reads and validates — see "Where a later step plugs in" above for what replaces that body.

## Feature Extraction and Normalization

```
NetworkFlowRecord -> extract_raw_features() -> RawFeatureSet -> normalize_features() -> NormalizedFlowFeatures
```

`app/services/feature_extraction.py` turns a `NetworkFlowRecord` into `NormalizedFlowFeatures`: the input contract a future ML model step will consume. **It is not wired into the processing lifecycle yet** — nothing currently calls it from `batch_processor.py`; it exists as a standalone, independently-tested layer for that later step to call. It performs no machine learning: no prediction, classification, scoring, risk assessment, or `DetectionResult` row is produced here, and it has no knowledge of any particular ML algorithm.

```python
FEATURE_SCHEMA: tuple[str, ...] = (
    "source_port", "destination_port", "source_ip_numeric", "destination_ip_numeric",
    "protocol_number", "flow_duration", "packet_count", "byte_count", "packet_rate",
    "byte_rate", "forward_packet_count", "forward_byte_count", "backward_packet_count",
    "backward_byte_count", "tcp_flags",
)

@dataclass(frozen=True)
class NormalizedFlowFeatures:
    row_number: int
    source_ip: str | None
    destination_ip: str | None
    protocol_name: str | None
    features: dict[str, float | int | None]   # exactly FEATURE_SCHEMA's keys
    feature_status: dict[str, FeatureStatus]   # why each feature does/doesn't have a value
    unknown_features: dict[str, str]           # every unrecognized raw_features column, verbatim
```

- **Why it reads `raw_features`, not `NetworkFlowRecord`'s typed fields.** Ingestion already types 6 fields (`source_ip`, `source_port`, ...), but folds "column missing" and "column present but unparseable" into the same `None` — a deliberate ingestion-layer simplification (see its own docstring). This layer needs that distinction, so it re-parses directly from `raw_features`, which keeps every column's original text regardless of whether ingestion recognized it. This also means adding a new generic feature here never requires changing `ingestion.py`.
- **Four explicit outcomes per feature** (`FeatureStatus`): `PRESENT` (parsed successfully), `MISSING` (no matching column, or an empty value — most datasets don't provide every concept), `MALFORMED` (a value was present but did not parse as the expected type, or violated an inherent constraint like a negative byte count or an out-of-range port — **never** silently turned into `0`/`""`/a guess), and `UNSUPPORTED` (syntactically valid data this module has no deterministic encoding for yet, e.g. a protocol name outside its small known table, or textual TCP flags like `"SF"` instead of a numeric bitmask — the original text is not discarded, it stays reachable on the source `NetworkFlowRecord`).
- **Deterministic feature ordering.** `FEATURE_SCHEMA` is a fixed tuple, not a dict's iteration order. `NormalizedFlowFeatures.feature_vector()` builds the model input list from that tuple explicitly; extracting the same record twice always yields identical output (covered by a repeated-extraction test).
- **IP and protocol representation.** A syntactically valid IPv4/IPv6 address is encoded as its standard integer form (`int(ipaddress.ip_address(...))`) — a deterministic, reversible encoding, not a threat or reputation judgment; this module makes no external network calls and assigns no risk to any address. A protocol is recognized by name or IANA number for a small set of common protocols (`icmp`/`tcp`/`udp`/`icmpv6`) via a fixed, never-invented lookup table; anything else is `UNSUPPORTED`, with its original text preserved as `protocol_name`.
- **Dataset-independent.** No `if dataset == "..."` branch exists anywhere in this module. `_COLUMN_ALIASES` is a small, explicitly generic seed (duration, packet/byte counts, forward/backward stats, TCP flags, ...), matched case- and whitespace-insensitively; every unrecognized column survives, untouched, in `unknown_features`. A specific public dataset's own column names are left to a future dataset-adapter mechanism, not implemented here.
- **Normalization is separated from extraction on purpose, and does nothing by default.** `extract_raw_features()` only type-parses; `normalize_features()` is the hook a future *fitted* scaler plugs into via the `FeatureScaler` protocol (`(feature_name, value) -> value`). This module does not compute or store any mean/std/min/max itself — doing so would require a representative dataset this layer does not have access to, and scaling parameters are a model-training concern, not a request-time one. Called with no scaler (today's only caller), `normalize_features()` is the identity transform. A scaler is applied only to `PRESENT` features; `MISSING`/`MALFORMED`/`UNSUPPORTED` features stay `None` regardless of any scaler, so normalization can never turn "we don't have this value" into a number.

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

- No ML training/inference, detection results, or dashboard logic exists yet. A batch's CSV is read and structurally validated during processing, and a standalone feature-extraction layer (`app/services/feature_extraction.py`) can turn a `NetworkFlowRecord` into model-ready features, but that layer is not yet called during processing and no model runs — nothing is actually analyzed and no batch reaches `completed` yet.
- Only batches created by real uploads exist; nothing inserts sample or fake data.
- No authentication/authorization exists yet.
- CORS is configured for local development origins only; it has not been reviewed or hardened for production use.
- Five public endpoints exist: `GET /api/v1/health`, `POST /api/v1/detection/upload`, `GET /api/v1/detection/batches`, `GET /api/v1/detection/batches/{batch_id}` and `POST /api/v1/detection/batches/{batch_id}/process`.
