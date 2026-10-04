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

Claims a pending batch and runs the full data-preparation pipeline — CSV ingestion followed by feature extraction — inside the processing boundary a later step's ML inference will also run in (`app/services/batch_processor.py`):

```
Upload -> ingestion -> NetworkFlowRecord -> feature extraction -> normalization -> waiting for detection
```

**This step performs no analysis.** It reads and validates the CSV's rows (see [CSV Ingestion](#csv-ingestion)) and turns each one into a normalized, model-ready feature representation (see [Feature Extraction and Normalization](#feature-extraction-and-normalization)) — but runs no model, makes no prediction, and creates no `DetectionResult` rows. **Feature extraction is not detection**: it describes what a row's data looks like, not what it means.

**Lifecycle** (deliberately conservative — no other transitions exist):

```
pending -> processing -> completed   (completing is a future step's job)
pending -> processing -> failed      (processing could not even start, or data preparation failed structurally)
```

A batch reaches `failed` here if its upload file is missing from disk, its CSV fails to ingest (unreadable, no header, or a malformed/inconsistent row — see below), or an unexpected error occurs during ingestion or feature extraction — never because "analysis" failed, since none runs. Critically, a batch does **not** fail merely because one row is missing an optional value, has a value feature extraction could not parse (`FeatureStatus.MALFORMED`), or uses an encoding this layer doesn't resolve yet (`FeatureStatus.UNSUPPORTED`): those are everyday outcomes in heterogeneous network-flow data, not errors, and a `MALFORMED`/`UNSUPPORTED` field is never treated as "an attack" or "a failed detection" — see `FeatureStatus` below. A batch that ingests and feature-extracts cleanly is left `processing`, not `completed`: marking it `completed` would claim IDS analysis happened when only data preparation did, and the current status model has no state between the two. `processed_records` and `failed_records` stay `0` throughout; they mean *records detected*, not *rows ingested or feature-extracted*, and nothing increments them until a real detection pipeline exists to earn that truthfully.

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

**Streaming end to end.** `_run_feature_extraction()` iterates `ingest_batch()` directly and calls `extract_features()` on each record as it is produced — it never calls `list(ingest_batch(...))` or otherwise collects every row first. At most one `NetworkFlowRecord` and one `NormalizedFlowFeatures` are held at a time, for the same reason ingestion itself streams (see [CSV Ingestion](#csv-ingestion)): a large CSV's memory use stays bounded by one row, not by the file's row count.

**Processing result.** `_run_feature_extraction()` returns an internal `ProcessingSummary` (`records_ingested`, `records_feature_extracted`, `feature_schema_version`) that is only ever logged server-side — never persisted, never returned through the API. Under this step's design the two counts are always equal on success: `extract_features()` never fails for an individual record's missing/malformed/unsupported field values (see below), so only a structural ingestion failure or a genuinely unexpected error can stop the loop early, and either of those fails the whole batch rather than leaving a partial count. There is accordingly no separate "records that could not be represented" counter — under the current design that number is always either 0 (success) or the batch is `failed` instead.

**Why normalized features are not persisted.** `NormalizedFlowFeatures` stays an in-memory value, discarded once `_run_feature_extraction()` returns. No new database table was added for it: the existing architecture does not yet need one, since nothing downstream reads features back after processing — there is no detection step yet to consume them from storage, and no API response exposes them. Persisting the model input contract makes sense once a step exists that genuinely benefits from them outliving one request (replaying a batch through a newer model without re-reading its CSV, for example); introducing that table now would be speculative. Revisit this when that step is designed.

**Where a later step plugs in:** `_run_feature_extraction()` in `batch_processor.py` currently discards every `NormalizedFlowFeatures` it produces — nothing is written anywhere. A later step replaces this function's body to run model inference against each `NormalizedFlowFeatures`, write `DetectionResult` rows, and update `processed_records`/`failed_records` as real rows are analyzed, and is responsible for the `processing -> completed` transition this step intentionally does not make.

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
- **No ML inference.** This module does not predict, classify, score, or create `DetectionResult` rows. Every record this module yields is now fed into feature extraction by `batch_processor.py`'s `_run_feature_extraction()` (see [Batch Processing Lifecycle](#batch-processing-lifecycle) and [Feature Extraction and Normalization](#feature-extraction-and-normalization)) — ingestion's own job stops at producing a valid `NetworkFlowRecord`.

## Feature Extraction and Normalization

```
NetworkFlowRecord -> extract_raw_features() -> RawFeatureSet -> normalize_features() -> NormalizedFlowFeatures
```

`app/services/feature_extraction.py` turns a `NetworkFlowRecord` into `NormalizedFlowFeatures`: the input contract a future ML model step will consume. **As of this step it is wired into the processing boundary**: `batch_processor.py`'s `_run_feature_extraction()` calls `extract_features()` (`extract_raw_features()` + `normalize_features()` composed) for every record ingestion yields. It performs no machine learning: no prediction, classification, scoring, risk assessment, or `DetectionResult` row is produced here, and it has no knowledge of any particular ML algorithm. The dependency direction is one-way and layered — `api/v1/detection.py` → `batch_processor.py` → `ingestion.py` → `feature_extraction.py` — so the API route and ingestion stay unaware of feature extraction's internals, and feature-extraction logic exists in exactly one place.

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
- **Dataset-independent.** No `if dataset == "..."` branch exists anywhere in this module. `_COLUMN_ALIASES` is a small, explicitly generic seed (duration, packet/byte counts, forward/backward stats, TCP flags, ...), matched case- and whitespace-insensitively; every unrecognized column survives, untouched, in `unknown_features`. A specific public dataset's own column names are translated to this vocabulary by the dataset-adapter layer below, not by this module.
- **Normalization is separated from extraction on purpose, and does nothing by default.** `extract_raw_features()` only type-parses; `normalize_features()` is the hook a future *fitted* scaler plugs into via the `FeatureScaler` protocol (`(feature_name, value) -> value`). This module does not compute or store any mean/std/min/max itself — doing so would require a representative dataset this layer does not have access to, and scaling parameters are a model-training concern, not a request-time one. Called with no scaler (today's only caller), `normalize_features()` is the identity transform. A scaler is applied only to `PRESENT` features; `MISSING`/`MALFORMED`/`UNSUPPORTED` features stay `None` regardless of any scaler, so normalization can never turn "we don't have this value" into a number.

## Dataset Adapter Layer

```
NetworkFlowRecord -> DatasetAdapter.adapt() -> CanonicalDatasetRecord
```

`app/services/dataset_adapters/` translates a dataset's own CSV column names into this project's shared vocabulary, so `ingestion.py` and `feature_extraction.py` never need to know any individual public IDS dataset's column names. **It performs no ML training or inference, and no model/training/inference code of any kind exists in this layer** — it only renames columns and, when confidently recognized, reads out a label and attack category.

**Why this exists.** Without it, supporting another dataset would mean adding `if dataset == "..."` branches somewhere in the core pipeline — exactly what Steps 15–17 were careful to avoid. The adapter layer isolates that dataset-specific knowledge into small, independent files instead.

```python
@dataclass(frozen=True)
class CanonicalDatasetRecord:
    row_number: int
    dataset_schema: str                 # which adapter matched, e.g. "nsl-kdd-style"
    source_record: NetworkFlowRecord    # the full original row, for traceability
    canonical_fields: dict[str, str]    # renamed, still-raw text — e.g. {"source_ip": "10.0.0.1"}
    column_mapping: dict[str, str]      # canonical name -> original source column name
    unknown_fields: dict[str, str]      # every column the adapter didn't recognize, verbatim
    label: str | None = None            # the dataset's own label text, when present
    attack_category: str | None = None  # a separate category column's text, when present
```

- **Adapter interface** (`base.py`, `DatasetAdapter`): `schema_id` and `description` identify the adapter; `can_handle(header) -> bool` decides, from column names alone, whether this adapter recognizes a schema; `adapt(record) -> CanonicalDatasetRecord` performs the translation. Implementations are small, stateless, and independent of each other.
- **Detection is schema-based, never identity-based.** `can_handle` looks only at header column names — never at a user-supplied "dataset" string, a filename, or file content — so a request cannot simply claim to be a dataset it isn't. Each adapter requires several of its most distinctive column names together (e.g. NSL-KDD: `protocol_type`+`service`+`flag`+`src_bytes`+`dst_bytes`); a single generic name like "protocol" is never sufficient on its own.
- **Selection never guesses** (`registry.select_adapter`). It tries every registered adapter's `can_handle` and returns the unique match. If zero adapters recognize a header, or more than one does (an ambiguous overlap), the result's `adapter` is `None` — `is_unsupported`/`is_ambiguous` distinguish the two cases for logging, but neither falls back to picking an adapter anyway.
- **Supported schemas today:** `nsl-kdd-style`, `unsw-nb15-style`, `cicids-style` (see each adapter's own module docstring for its exact detection signature and which columns it maps). Mapping confidence varies deliberately by dataset: NSL-KDD and UNSW-NB15 have long-stable, well-documented column layouts and are mapped fairly completely for the columns this project has a canonical equivalent for; CICIDS varies across releases, so its adapter maps only the small subset of columns consistently present everywhere and leaves the rest — the majority of its much larger column set — as `unknown_fields` rather than guessed at.
- **No destructive feature selection.** A dataset's columns with no canonical equivalent (NSL-KDD's `serror_rate`, `dst_host_count`, and the many other derived-rate/host-count columns; most of CICIDS' 70+ statistical features) are preserved verbatim in `unknown_fields`, not discarded. Adapters are a translation layer, not a filter.
- **Labels are preserved, never interpreted.** `label`/`attack_category` hold the dataset's own text exactly as written (`"neptune"`, `"0"`, `"BENIGN"`, `"Exploits"`, ...). Nothing in this layer normalizes a label to "benign"/"attack", infers one from an unrelated column, or defaults a missing one to anything — a missing label column simply leaves `label` as `None`. This is translation, not classification.
- **Raw-value traceability.** `canonical_fields` holds copied, unparsed text (no type conversion — that stays `feature_extraction.py`'s job, so this layer never duplicates it); `column_mapping` records which original column each canonical field came from; `source_record` keeps the complete original row. A canonical field can always be traced back to exactly which CSV column produced it.
- **Performance.** `can_handle` takes only a header (a list of column names) — selecting an adapter never requires reading a CSV's data rows, duplicating the ingestion parse, or loading a file into memory.
- **Not wired into batch processing.** `batch_processor.py` does not call this layer. There is currently no mechanism for a batch to declare or select a dataset (no API field, no stored attribute on `DetectionBatch`), and auto-detecting a schema for every batch during normal processing would mean silently applying a translation the caller never asked for — exactly the kind of guessing this step's detection rules are designed to avoid. Wiring this in is left to a later step that deliberately designs how a batch's dataset is chosen; see `__init__.py`'s and `registry.py`'s docstrings.
- **Adding a new dataset adapter** requires: writing a class implementing `DatasetAdapter` (a new file, following the existing three as examples) and appending an instance to `ADAPTERS` in `registry.py`. It requires no change to `ingestion.py`, `feature_extraction.py`, or `batch_processor.py` — verified by a dedicated test that checks none of those three files mention this package.

## Dataset Feature Mapping

```
CanonicalDatasetRecord -> map_canonical_record() -> MappedFeatureSet
```

`app/services/feature_mapping.py` turns a `CanonicalDatasetRecord` (Step 18) into `MappedFeatureSet`: a deterministic, provenance-carrying, algorithm-agnostic ML input representation. **It does not train a model, generate a prediction, calculate an IDS metric, or fit a dataset-wide normalization parameter** — it only maps already-translated values into the canonical feature schema, the same way `feature_extraction.py` already does for ingestion's output.

```python
@dataclass(frozen=True)
class MappedFeature:
    canonical_name: str
    value: float | int | None       # None whenever status is not PRESENT — never fabricated
    status: FeatureStatus           # reused from feature_extraction: PRESENT/MISSING/MALFORMED/UNSUPPORTED
    source_column: str | None       # the dataset's own column name this came from
    raw_value: str | None           # the original, unparsed text

@dataclass(frozen=True)
class MappedFeatureSet:
    row_number: int
    dataset_schema: str                      # metadata only — never a feature
    features: dict[str, MappedFeature]        # keyed by FEATURE_SCHEMA name
    unknown_fields: dict[str, str]

    def feature_vector(self) -> list[float | int | None]:
        ...  # built from FEATURE_SCHEMA's order, always
```

- **Single source of truth, reused directly.** This module defines no feature schema of its own and performs no independent type parsing. Each canonical field's raw text is parsed via `feature_extraction.parse_canonical_field(canonical_name, text)` — a small, new, *additive* public function that dispatches by canonical name directly (bypassing `extract_raw_features()`'s raw-dataset-column alias matching, which does not apply to already-canonical input and would silently misclassify several canonical names — like `backward_byte_count` — as unrecognized, since they are not themselves listed as one of their own input aliases). The numeric encodings, `FEATURE_SCHEMA` order, and `FeatureStatus` vocabulary are exactly Steps 16/17's, not reinvented.
- **No dataset-specific branching.** This module has no knowledge of NSL-KDD, UNSW-NB15, CICIDS, or any other dataset family — verified by a dedicated test that scans its functions' source for dataset-name literals and `if dataset ==`-style branches. All dataset-specific knowledge stays one layer upstream, in `app/services/dataset_adapters/`.
- **Missing, malformed and unsupported stay distinguishable.** A canonical feature with no source column at all is `MISSING` (`value=None`, `source_column=None`); a column that exists but fails to parse (invalid IP, invalid port, non-numeric or negative count, ...) is `MALFORMED` (`value=None`, but `source_column`/`raw_value` are still populated, so the bad input stays traceable); a value this project has no deterministic encoding for yet (e.g. an unrecognized protocol name) is `UNSUPPORTED`. None of the three is ever coerced into `0` or any other plausible-looking value.
- **Unknown dataset columns are preserved, not discarded.** Every column the adapter layer did not recognize at all reaches `MappedFeatureSet.unknown_fields`, verbatim — passed straight through from `CanonicalDatasetRecord.unknown_fields`. A dataset's extra columns never corrupt the canonical feature set.
- **Labels never become features.** `label`/`attack_category` are not present on `MappedFeatureSet` at all (there is no such attribute), because `CanonicalDatasetRecord.canonical_fields` never contains them in the first place — the adapter layer routes them separately before this module ever sees a row. Neither can appear in `features`, `feature_vector()`, or as any feature's `source_column`/`raw_value` — verified directly by tests using real label/category text.
- **No other form of data leakage.** `dataset_schema` and `row_number` are kept as metadata fields on `MappedFeatureSet`, outside `features` and `feature_vector()` — the same pattern `NormalizedFlowFeatures` already uses for `row_number`. There is no batch ID or filename anywhere in this pipeline (`NetworkFlowRecord`/`CanonicalDatasetRecord` never carry either), so neither can leak into a feature vector by construction.
- **Deterministic ordering, independent of source-column order.** `feature_vector()` is always built by walking `FEATURE_SCHEMA`, never CSV column order, dict insertion order, or an adapter's internal mapping order — verified with two records whose source columns are given in opposite orders, producing identical output.
- **No dataset-wide fitting.** `map_canonical_record()` takes a single `CanonicalDatasetRecord` and nothing else — no scaler, no fitted parameters, no access to any other record. It cannot compute a mean, standard deviation, or dataset-wide min/max even in principle, because it never sees more than one row.
- **Not persisted, not wired into batch processing.** `MappedFeatureSet` is an in-memory value only — no new database table exists for it, and `batch_processor.py` does not call this module (it is not called from anywhere in the live request path yet, for the same reason `dataset_adapters` and `dataset_profiling` are not: there is still no mechanism for a batch to declare or select a dataset). A later step that does is responsible for wiring ingestion → adapter selection → this module → model inference together.

## Dataset Feature Persistence

```
CanonicalDatasetRecord -> map_canonical_record() -> MappedFeatureSet -> persist_mapped_features() -> MappedFeatureRecord (DB)
```

Step 21 adds the first persistent storage for mapped features: `app/models/mapped_feature_record.py` (`MappedFeatureRecord`) and `app/services/feature_persistence.py`. **This is storage only — it does not train a model, run inference, or generate a prediction, confidence, severity or risk score.**

- **Wired into processing.** `batch_processor._run_feature_extraction` now tries `dataset_adapters.select_adapter()` on the batch's CSV header once. For a **recognized** schema, every row is adapted, mapped, and persisted as a `MappedFeatureRecord`. For an **unrecognized** schema, nothing is persisted — rows still go through the plain `feature_extraction.extract_features` path (proving they parse cleanly) without guessing a dataset identity nobody confirmed. Either way the batch stays `processing`, never `completed` — persisting features is not detection.
- **Representation.** One row per CSV data row: `batch_id`, `row_number`, `feature_schema_version`, `dataset_schema`, a `features` JSON object (`{canonical_name: {"value", "status", "source_column", "raw_value"}}` for every `FEATURE_SCHEMA` name), `unknown_fields` JSON (unmapped columns only — not the full original row, which stays reachable via ingestion if ever needed), and `created_at`. A missing/malformed/unsupported feature's `"value"` is always `null`, never `0`; `"status"` (reusing `FeatureStatus`) says which. `app.services.feature_persistence.load_feature_vector()` rebuilds the ordered vector by walking `FEATURE_SCHEMA` explicitly — never JSON/dict key order.
- **Transactional.** `persist_mapped_features` only `db.add()`s; `batch_processor` commits once after a whole batch's rows are staged, and rolls back before marking a batch `failed` — so a failed batch never leaves a partially persisted feature set. Verified by a test that fails mapping partway through a batch and confirms zero rows remain.
- **Batch isolation & duplicate protection.** Every row carries its `batch_id`; a `UniqueConstraint("batch_id", "row_number")` is a database-level backstop (not a second concurrency mechanism) against ever creating two rows for the same batch/row pair — the existing atomic `claim_for_processing` already prevents a batch from being processed twice.
- **No migration framework exists** in this repository (no Alembic). `MappedFeatureRecord` is a brand-new table, so `Base.metadata.create_all()` (already run at startup) creates it additively without touching existing tables — the smallest change consistent with the repository's current schema-management approach. Altering this table's shape later will need a real migration tool; that is out of scope here.
- **Not an API-facing feature yet.** No new endpoint was added — persistence happens only as a side effect of the existing `POST /process` endpoint; `MappedFeatureRecord` rows are not returned by any route.

## Dataset Profiling

```
Stored CSV -> ingestion.read_header() / ingest_batch() -> dataset_adapters.select_adapter() -> DatasetProfiler -> DatasetProfile
```

`app/services/dataset_profiling/` (`profile_dataset(csv_path) -> DatasetProfile`) answers "what does this dataset actually look like?" — row/column counts, per-column missingness and inferred type, which schema (if any) was recognized, which label/attack-category values occur and how often, and which of this project's canonical features the dataset provides. **Step 19 profiles a dataset. It does not train a model, classify traffic, infer an attack, normalize a label's meaning, calculate risk, or generate a prediction** — those remain later roadmap steps' work; a `DatasetProfile` is purely descriptive.

- **Nothing is fabricated.** Every number is either exact (row/column counts, missing counts and percentages, type inference, min/max) or explicitly marked as a bounded lower bound (`ColumnProfile.unique_count_is_exact`, `LabelSummary.counts_truncated`) — never silently presented as complete. Schema identity, label availability and canonical-feature availability are reported as `unsupported`/`ambiguous`/`unavailable`/`missing` rather than guessed when they cannot be determined safely.
- **Reuses Steps 15 and 18 as-is.** CSV reading goes through `ingestion.read_header()` (new: a cheap, header-only read sharing `ingest_batch`'s own encoding detection and header-validation code — not a second parser) and `ingestion.ingest_batch()`. Schema detection and column-name translation go through `dataset_adapters.select_adapter()` and one `adapter.adapt()` call. This module never inspects a dataset-specific column name itself, and the dependency stays one-way: `dataset_profiling` imports `ingestion`/`dataset_adapters`/`feature_extraction`; none of those import it.
- **Missing-value semantics match the rest of the project.** Only an empty or whitespace-only cell counts as missing — never a literal `"0"`, `"N/A"`, `"None"` or `"unknown"` — the same convention `ingestion.py` and `feature_extraction.py` already use. `missing_percentage` is computed from actual counts and is `None` (not a fabricated `0%`) for a zero-row dataset, avoiding division by zero.
- **Type inference is conservative and advisory-only** (`type_inference.py`): `empty` / `boolean` (`"true"`/`"false"` only — a bare `0`/`1` is `integer`, never guessed as boolean) / `integer` / `float` (`"inf"`/`"nan"` text excluded) / `ip_address` (via `ipaddress.ip_address`) / `timestamp` (via `datetime.fromisoformat`, same conservative rule as ingestion's own timestamp parsing) / `text` / `mixed` (more than one category observed). Classification never alters, parses-and-replaces, or discards a value anywhere else in the pipeline — `"00123"` is classified `integer` while its text stays exactly `"00123"` everywhere it is actually stored.
- **Bounded-memory strategy.** The CSV is read once via `ingest_batch`'s existing generator; each row updates per-column counters and is discarded — no list of rows or of any column's values is ever held in memory. Two statistics are deliberately size-bounded rather than exact, via `BoundedValueCounter`: a column's distinct-value count (`MAX_TRACKED_UNIQUE_VALUES = 200`) and label/attack-category value counts (`MAX_TRACKED_LABEL_VALUES = 500`). Counts for values already being tracked stay exact even after the cap is hit; only "have we seen every distinct value" becomes unknown, surfaced via `unique_count_is_exact` / `counts_truncated` rather than silently presented as complete. Every other statistic is exact and O(1) per column, cheap enough to need no approximation.
- **Label/attack-category profiling** reads real per-row text from whichever column the adapter identified as the label/category — determined once, from header information alone (an all-empty-valued probe record, since an adapter's column mapping depends only on column names, never on values — see `_structural_mapping`), so availability is identical whether the dataset has 0 rows or a million. Observed values are reported exactly as written; nothing is normalized to "benign"/"attack", and a schema with no category column (e.g. NSL-KDD) always reports `attack_category_summary.available = False`, never a guess derived from the label.
- **Canonical feature availability** checks, for each of `feature_extraction.CANONICAL_COLUMN_NAMES`, only whether some column was mapped to it — `AVAILABLE` or `MISSING`, from header/mapping information alone. There is deliberately no third "unusable" state at this layer: that per-*value* distinction is `feature_extraction.FeatureStatus`'s job (e.g. a `protocol` column present but a particular row's text not resolving to a known IANA number), a different question this column-level availability check does not attempt to answer.
- **Duplicate columns.** A header with the same column name twice is detected and reported (`DatasetProfile.duplicate_column_names`, a `duplicate_columns` warning) rather than silently resolved — though the resolution itself (keeping only the last value per name in `NetworkFlowRecord.raw_features`) is pre-existing `ingestion.py` behavior this step does not change; the warning exists so that data loss is at least visible.
- **Warnings** (`ProfileWarning`: a `code` and a factual `message`) are produced only for meaningful conditions: `empty_dataset`, `duplicate_columns`, `high_missingness` (≥50% missing), `mixed_type_column`, `unsupported_schema`, `ambiguous_schema`, `no_recognizable_labels`. Each states an observable fact, not a judgment, and routine/harmless conditions are not flagged.
- **Malformed CSVs fail, they are not partially profiled.** A structurally invalid file (missing, unreadable, malformed syntax, wrong row width, no header) raises the same `ApiException` `ingestion.py` already raises — `profile_dataset` does not catch and re-wrap it into a differently-shaped "failed" profile. Messages are user-safe; filesystem paths and exception details are logged server-side only.
- **No API endpoint.** This step adds only the backend service; there is no new route. Exposing profiling over the API (with response size limits, pagination for large `value_counts`, etc.) is left for whenever an actual caller needs it, rather than speculatively designed now.
- **Why profiles are not persisted.** A `DatasetProfile` is computed fresh from the stored CSV each time `profile_dataset` is called and is never written to the database. No new table was added: nothing downstream currently reads a profile back later, there is no "profile history" feature yet, and persisting one now would be exactly the kind of speculative table this step's instructions call out to avoid. Revisit if a future step genuinely needs profiles to outlive one call (e.g. comparing a batch's profile across reprocessing runs).
- **Limitations.** `minimum`/`maximum` are computed across all of a column's numeric-looking values regardless of `inferred_type` (so a `mixed` column can still report a sensible min/max over its numeric subset) but are not computed for non-numeric columns. Mean/standard-deviation/distribution analysis is deliberately out of scope: Step 19 is a focused structural/statistical profile, not a general analysis engine.

## Model Training Foundation

```
Database (MappedFeatureRecord rows) -> load_training_data() -> TrainingDataset
    -> train_baseline_model(): split -> impute -> RandomForestClassifier.fit() -> evaluate
    -> artifact (models/<model_id>.joblib) -> ModelMetadata row (registry)
```

Step 22 adds the first model-*training* code to this project: `app/services/training_data.py` and `app/services/model_training.py`. **This is a training foundation, not the runtime detection engine — it does not add a prediction/inference API, does not create `DetectionResult` rows, and does not compute or display any attack confidence, severity, risk, or threat score. No existing endpoint or UI starts showing ML results because of this step.**

- **Training data source.** `load_training_data(db, batch_ids)` reads already-persisted `MappedFeatureRecord` rows (Step 21) through `feature_persistence.load_feature_vector()` — it never re-reads a batch's original CSV and never re-runs ingestion, dataset adaptation, feature extraction, or feature mapping. Rows are read in deterministic `(batch_id, row_number)` order.
- **Label handling is isolated from training code.** A row's raw `label` text (now stored on `MappedFeatureRecord` — see below) is turned into a binary target only by `app.services.dataset_adapters.label_mapping.to_binary_label(dataset_schema, label_text)`, the **one place** in the codebase allowed to branch on a dataset's schema to decide what counts as benign. `training_data.py` and `model_training.py` call it uniformly and contain no `if dataset_schema == ...` branches themselves. Documented conventions: NSL-KDD-style (`"normal"` -> benign, anything else -> attack), UNSW-NB15-style (`"0"`/`"1"`), CICIDS-style (`"BENIGN"` -> benign, anything else -> attack). A missing label, or text not matching its dataset's documented convention, maps to `None` and the row is **excluded**, never defaulted to benign — `TrainingDataset.excluded_unmappable_label_count` reports how many.
- **Labels are stored as row metadata, never as a feature.** `MappedFeatureRecord` gained two new nullable columns, `label` and `attack_category`, populated from the already-computed `CanonicalDatasetRecord` at persistence time (`batch_processor.py`). They are never merged into the `features` JSON blob `load_feature_vector()` reads — the feature vector a model trains on is exactly the same one Step 21 already defined, with zero risk of a label leaking in as a feature.
- **Feature ordering and schema compatibility.** `TrainingDataset.feature_matrix` rows follow `FEATURE_SCHEMA` order (via the existing `load_feature_vector()`, never dict/JSON key order) and never include labels, batch/row/provenance metadata, or `attack_category`. Every loaded row's `feature_schema_version` must match the code's current `FEATURE_SCHEMA_VERSION`; a single mismatched row raises `ModelTrainingError` for the whole load rather than silently mixing schema versions in one training run.
- **Missing values are never silently zeroed.** A missing/malformed/unsupported feature stays `None` through `TrainingDataset.feature_matrix`. `model_training.py` converts `None` -> `np.nan` and fits an `sklearn.impute.SimpleImputer(strategy="median", keep_empty_features=True)` **only on the training split** (`fit_transform(X_train)`), then applies it unchanged to the validation split (`transform(X_val)`) — validation data never influences the fit. `keep_empty_features=True` is required: without it, a feature that is entirely missing across the whole training split is silently *dropped* by sklearn, shrinking the feature matrix below `len(FEATURE_SCHEMA)` and misaligning every later column with the artifact's declared schema; with it, such a column is kept and filled with `0.0` (sklearn's own documented fallback for "nothing to compute a median from" — not a general "replace missing with zero" policy).
- **Train/validation split.** Delegated to `app.services.dataset_split.prepare_training_data` (Step 23 — see "Training-Data Preparation" below) with `test_fraction=0.0` and `group_by_batch=False` by default, which reproduces this step's original behavior exactly: a single stratified split *before* any preprocessing is fitted, with a fixed, documented seed. A `ModelTrainingError` is raised (wrapping `DatasetSplitError`) if there are too few samples in a class to stratify; `MIN_SAMPLES_PER_CLASS = 5` is checked up front so this is normally caught with a clear message before the split layer is even asked to split. `train_baseline_model` accepts optional `test_fraction`/`group_by_batch` keyword arguments to request a genuine held-out test partition and/or batch-isolated splitting, but never reads the resulting `test` partition itself — see the next section.
- **Baseline model.** `sklearn.ensemble.RandomForestClassifier` with a fixed, explicit, documented configuration (`n_estimators=100`, `max_depth=None`, `random_state=42`, `class_weight=None`) — no deep learning, no hyperparameter search. One model is trained per training run; `_validate_single_dataset_schema` rejects a run whose rows span more than one `dataset_schema` (train one model per dataset family — a deliberate MVP simplification, not something generic split/train code decides on its own).
- **Truthful training result.** `TrainingResult` reports real counts and configuration only: model/feature-schema identity, train/validation sample counts, feature count, class distribution (post-exclusion, train+validation combined), excluded-label count, training/preprocessing config, random seed, and `validation_metrics` (accuracy/precision/recall/F1, `zero_division=0`) computed from the model's **actual predictions on its own held-out validation split**. These are validation metrics from this one run, not a production/runtime IDS performance claim, and are not exposed anywhere outside this internal result object in this step.
- **Model artifact.** Saved via `joblib.dump` to the existing, already-git-ignored `models/` directory at the repository root (`model_artifact_dir` setting, default `../models` relative to the backend working directory — see `models/README.md`, which anticipated this exact need). The artifact is a plain dict: the fitted model, the fitted imputer, `feature_schema`, `feature_schema_version`, `dataset_schema`, `model_type`, and both config dicts — enough to verify compatibility before ever reusing it, with no dynamic class loading required to reload it.
- **Model registry reuses the existing, previously-unused `ModelMetadata` table** rather than adding a new one — extended with `status` (new `ModelStatus` enum: `ready`/`failed`; only `ready` is ever persisted by this step, since a row is only created after a successful write), `training_config` (JSON), and `training_batch_ids` (JSON list of batch UUIDs, for traceability back to the training data without duplicating it). As with every other table in this project, there is no migration framework (no Alembic); these are additive columns created by the existing `Base.metadata.create_all()` call at startup.
- **Transaction safety.** The artifact file is written **before** any database write. If `joblib.dump` fails (`OSError`), `ModelTrainingError` is raised and **no `ModelMetadata` row is ever created** — the registry never claims a model exists that has no file backing it. If registering the already-written artifact fails (`SQLAlchemyError`), the session is rolled back and the now-orphaned artifact file is deleted (`_remove_orphaned_artifact`) before re-raising — a database failure never leaves an unregistered artifact file masquerading as a successful run. Training is never reported as successful if either step failed.
- **Security.** Artifacts are produced and read only by this project's own pipeline — no artifact path is ever accepted from API input, no dynamic model-class import, no `eval`/`exec`, no shell commands. `ModelTrainingError` messages are written to be safe to surface to a caller: no filesystem paths, no stack traces, no SQL (exceptions are logged server-side via `logging.exception`, never included in the raised message).
- **No new API endpoint.** Training is invoked as an internal service (`train_baseline_model(db, dataset, artifact_dir=...)`), fully testable without the HTTP layer. The architecture does not yet require an endpoint — there is no frontend training trigger or training-status page in this step.
- **Explicit non-goals (deferred to later roadmap steps).** No runtime inference API, no `DetectionResult` rows produced from a trained model, no attack-classification UI, no risk/confidence/severity scoring surfaced anywhere, no alerting or incident management, no threat intelligence, no live packet capture or real-time/streaming detection, no automated retraining or scheduling, no hyperparameter optimization, no deep learning.

## Training-Data Preparation (Train/Validation/Test Split)

```
TrainingDataset (app.services.training_data) -> prepare_training_data() -> PreparedDataset
    .train / .validation / .test, each a DatasetPartition with its own
    feature matrix, labels, and truthful class-distribution metadata
```

Step 23 adds `app/services/dataset_split.py` — a dedicated layer that turns a `TrainingDataset` into three explicit, disjoint, deterministic partitions, and `app.services.model_training` (Step 22) is refactored to consume it instead of performing its own split. **This step is about reliable data separation only — it adds no inference, no evaluation of the `test` partition, no new model, and no detection/risk/confidence/severity concept.**

- **Why a three-way split exists.** A model fit on `train` and tuned/selected using `validation` still needs a partition that influenced *no* decision made before it, or every number reported later is optimistic by construction. `prepare_training_data()` prepares `test` alongside `train`/`validation` specifically so a later evaluation step has genuinely held-out data — this step does not evaluate anything itself; it only guarantees `test` stays untouched by every choice this step makes.
- **Deterministic splitting.** Both splitting stages (first `test_fraction` is carved out, then `validation_fraction` of the *original* total is carved from what remains) use a fixed, documented `random_seed` (default `42`), happen strictly *before* any learned preprocessing is fitted, and are stratified (class proportions preserved) whenever there are enough samples of each class. The same `TrainingDataset` + configuration + seed always produces the same three partitions — verified by dedicated determinism tests, including in group mode.
- **Batch/group leakage prevention.** Multiple `MappedFeatureRecord` rows can come from the same uploaded batch and are not independent of each other the way i.i.d. splitting assumes. With `group_by_batch=True` (the default for `prepare_training_data()` itself), whole batches — never individual rows from the same batch — are assigned to a single partition, via `sklearn.model_selection.StratifiedGroupKFold`, which also tries to keep class proportions balanced across the group-level assignment. This needs at least two distinct batches to isolate any partition from another; `DatasetSplitError` is raised otherwise rather than silently falling back to row-level mixing. An internal `_assert_group_isolated` check re-verifies no batch ever appears in more than one partition before returning — tested directly, including via a mutation that bypassed grouping and was caught by this check and by dedicated isolation tests.
- **Why preprocessing is fitted only on training data.** `prepare_training_data()` itself returns row indices and partitions only — it never computes or returns any fitted preprocessing object, and never computes a dataset-wide statistic (e.g. a global mean) before splitting, so there is nothing here a caller could misuse to leak test/validation information into a fit. `app.services.model_training` still does the actual fitting (its `SimpleImputer`), and still fits it only on `prepared.train` — unchanged from Step 22, now sourced from this layer's output instead of a duplicated split call.
- **Why the test set is kept untouched.** `train_baseline_model` never reads `prepared.test` — it is returned in `TrainingResult.test_sample_count` purely for transparency (a real count, not a metric) and is otherwise ignored. No model-fitting, no preprocessing-fitting, no hyperparameter or threshold decision in this codebase touches it. Evaluating against it is left to a later step that does not exist yet.
- **Missing-value semantics are unchanged.** A `DatasetPartition.feature_matrix` is a direct slice of the input `TrainingDataset.feature_matrix` — missing/malformed/unsupported values are still `None`, genuine zeros are still `0.0`; nothing in this layer fabricates or discards a value.
- **Truthful partition metadata.** Each `DatasetPartition` reports its own real `sample_count`, `class_distribution` (`{"benign": N, "attack": M}`), and `class_proportions` — all computed directly from that partition's own rows. No model-performance metric of any kind is computed or exposed by this module.
- **Error handling.** A dedicated `DatasetSplitError` (not `ModelTrainingError`) is raised for every precondition this layer cannot safely meet: invalid split-fraction configuration, no labeled data, fewer than two classes, too few samples in a class, too few distinct batches for group-aware isolation, or (defensively) an internal overlap/isolation check failing. Messages are written to be safe to surface: no filesystem paths, no stack traces, no SQL. `app.services.model_training` catches `DatasetSplitError` and re-raises it as `ModelTrainingError`, so callers of the existing training service see one consistent exception type.
- **Compatibility with Step 22.** `train_baseline_model`'s new `test_fraction`/`group_by_batch` keyword arguments default to `0.0`/`False`, which reproduces Step 22's original train/validation-only, non-grouped behavior exactly — all of Step 22's existing tests pass unchanged. No training logic is duplicated: the only code that decides which rows go where now lives in `dataset_split.py`.
- **No new API endpoint.** Still an internal service (`prepare_training_data(dataset, ...)`), fully testable without the HTTP layer, same as Step 22.
- **Limitations.** Group-aware splitting approximates the requested fractions (via `StratifiedGroupKFold`'s fold-count math) rather than hitting them exactly, and exact stratification is not guaranteed when grouping is enabled — isolating whole batches necessarily trades off against perfectly balancing every partition's class proportions; `StratifiedGroupKFold` makes a best effort. A dataset with only one batch cannot use group-aware isolation at all (`group_by_batch=False` must be chosen explicitly for it, accepting row-level mixing). This step does not evaluate a model, does not compute any accuracy/precision/recall/F1 for the `test` partition, and does not claim the resulting split makes any model "production-ready."

## Model Evaluation (Test-Set Validation)

```
ModelMetadata (registry row) -> joblib.load(artifact_path) -> artifact payload
                                                                    |
DatasetPartition (.test, from app.services.dataset_split)  ->  evaluate_model()
                                                                    |
                                                            EvaluationResult
```

Step 24 adds `app/services/model_evaluation.py` — a dedicated service that evaluates an already-trained model against the untouched `test` partition Step 23 prepares, and reports real metrics computed from real predictions. **This step is evaluation only: it does not retrain anything, does not add an inference/prediction API, does not create `DetectionResult` rows, and does not compute or display any attack confidence, severity, risk, or threat score.**

- **Why a separate evaluation layer exists.** Training (Step 22) and splitting (Step 23) both carefully avoid letting the `test` partition influence any decision. A trustworthy final assessment needs its own boundary too: `evaluate_model()` only ever calls `.transform()` on the training-fitted preprocessing object and `.predict()` on the trained model — it never calls `.fit()` or `.fit_transform()` anywhere in the module, so there is structurally nothing here that could leak test data back into a fit.
- **Why the test set must remain untouched.** `evaluate_model()` reads `test_partition` and the loaded artifact; it never writes to either, never adds rows to any table, and never changes `ModelMetadata` (status, `training_config`, or anything else). Verified by dedicated tests that snapshot the test partition's feature matrix/labels and the registry row before and after evaluation and assert nothing changed, plus tests that spy on `SimpleImputer.fit`/`fit_transform` and `RandomForestClassifier.fit` to prove neither is ever called during evaluation.
- **Metrics calculated.** `accuracy`, `precision`, `recall`, `f1` (all `zero_division=0`, ATTACK as the positive class), a full confusion matrix (`tp`/`tn`/`fp`/`fn`, via `sklearn.metrics.confusion_matrix(..., labels=[0, 1])`), and per-class precision/recall/f1/support for BENIGN and ATTACK separately (`precision_recall_fscore_support`) — so performance on each class can be read directly rather than inferred from the combined metrics or the class distribution alone. Every number comes from comparing the model's actual predictions against the test partition's actual labels; nothing is hardcoded, and nothing is computed when there isn't enough valid test data (see error handling below).
- **How model/schema compatibility is checked.** Before any prediction is made, `_validate_compatibility()` verifies: the artifact's recorded `feature_schema` matches the code's current `FEATURE_SCHEMA` exactly, the artifact's `feature_schema_version` matches the test data's own version, the artifact's `dataset_schema` matches both the test data's dataset schema and the registry row's own `dataset_name`, and every test row's feature count matches the artifact's expected count. Any mismatch raises `EvaluationError` before touching the model — never silently reordering, truncating, or padding a feature vector to make an incompatible artifact "fit."
- **Why evaluation does not retrain the model.** The artifact (model + fitted imputer) is loaded read-only via `joblib.load` and used exactly as training left it. Determinism follows directly from this: the same model artifact, the same test partition, and the same (deterministic) preprocessing always produce the same predictions and therefore the same metrics — verified by a test that evaluates the same model/test-partition pair twice and asserts identical results.
- **Model artifact safety.** Uses the existing registry (`ModelMetadata`) and artifact convention unchanged from Step 22 — no filesystem path is ever accepted as a parameter to `evaluate_model`; the only path used is the one already recorded on the registry row. No `eval`/`exec`, no dynamic class import. A missing, unreadable, or structurally incomplete artifact (missing required payload keys) raises `EvaluationError` rather than propagating a raw exception.
- **Error handling.** A dedicated `EvaluationError` (distinct from `ModelTrainingError`/`DatasetSplitError`) covers: no registered model found, a model with no usable artifact, a corrupt/unreadable artifact, an incompatible feature schema or version, an empty test set, a test set with fewer than two classes present, and an internal inconsistency in the model's output (wrong prediction count, prediction values outside `{0, 1}`). Messages never include a filesystem path, stack trace, SQL, or raw exception text; the original exception is logged server-side via `logger.exception`/`logger.error` only.
- **Evaluation result representation.** `EvaluationResult` carries model identity/version/type, feature schema version, dataset schema, real test sample/class counts, the metrics and confusion matrix above, per-class metrics, a small non-sensitive `evaluation_config` (positive-class convention, zero-division policy — never a filesystem path or training hyperparameters), and a timestamp. It deliberately has no `artifact_path`, no raw feature vectors, and no raw predictions array.
- **No new API endpoint.** Still an internal service (`evaluate_model(db, model_id, test_partition, ...)`), fully testable without the HTTP layer, same as Steps 22–23. No dashboard or frontend surface exists for evaluation results in this step.
- **What this step does NOT implement yet.** No inference/prediction API, no live prediction, no `DetectionResult` generation, no risk/confidence/severity scoring, no alerting, no automated retraining, no hyperparameter optimization. The metrics this module reports are evaluation results for one specific held-out test dataset from one specific training run — not a claim about production IDS performance, and not evidence the model is production-ready.

## Model Inference (Reusable Prediction Service)

```
ModelMetadata (registry row) -> joblib.load(artifact_path) -> artifact payload
                                                                    |
feature_matrix (FEATURE_SCHEMA-ordered)              ->  predict_with_model()
                                                                    |
                                                            InferenceResult
```

Step 25 adds `app/services/model_inference.py` — a reusable, standalone prediction primitive that takes an already-trained model artifact and an already-prepared feature matrix and returns validated, deterministic predictions. **This step is only the inference service: it is not wired into `batch_processor.py`, does not create `DetectionResult` rows, does not expose an HTTP endpoint, and does not compute or display any risk, severity, anomaly, or confidence score.** Integrating inference into the processing pipeline is explicitly deferred to a later step.

- **What the service does.** `predict_with_model(db, model_id, feature_matrix, feature_schema_version=..., dataset_schema=...)` loads the registered model's artifact, validates it is compatible with the supplied data, transforms the feature matrix with the artifact's own fitted preprocessing, calls the trained model's `.predict()` (and `.predict_proba()` when genuinely supported), and returns a typed `InferenceResult` containing one `Prediction` per input row.
- **Artifact compatibility validation.** Before any prediction: the artifact's recorded `feature_schema` must equal the code's current `FEATURE_SCHEMA` exactly, its `feature_schema_version` must match the supplied data's version, its `dataset_schema` must match both the supplied value and the registry row's own `dataset_name`, the artifact must contain a usable preprocessing object and a model exposing `.predict()`, and every input row's length must match the artifact's expected feature count. Any mismatch raises `InferenceError` before the model is ever called — never a silent reorder, truncation, or padding.
- **Preprocessing behavior.** Only the exact fitted `imputer` stored in the artifact at training time is ever used, and only via `.transform()` — the module contains no call to `.fit()` or `.fit_transform()` anywhere, so there is nothing here that could refit preprocessing on inference input. Verified by a monkeypatch spy test asserting zero fit calls during inference.
- **Feature ordering.** The caller is responsible for supplying a `FEATURE_SCHEMA`-ordered feature matrix (the same discipline `app.services.training_data`/`app.services.feature_persistence` already establish) — this module never reorders, invents, or imputes a value itself. A missing value stays `None` (never a feature column dropped or shuffled) until the stored imputer handles it. Verified by a test using a position-reporting stub model proving columns reach the model in the order supplied, unchanged.
- **Prediction semantics.** `Prediction.predicted_label` is `0` (BENIGN) or `1` (ATTACK) — validated against the only two legitimate values before being reported; `prediction_name` is the canonical `"benign"`/`"attack"` string. `attack_probability` (the probability of the ATTACK class, in `[0, 1]`) is only ever populated when the loaded model genuinely exposes `predict_proba` and its output is internally consistent (right shape, class `1` present, values in range) — `None` otherwise, and never fabricated as a fallback. It is deliberately never called a "confidence score": nothing about a `RandomForestClassifier`'s vote fractions makes that the correct name for a concept a later risk-scoring step might introduce.
- **Why inference does not train.** The artifact (model + fitted imputer) is loaded read-only and used exactly as training left it; determinism follows directly — the same artifact and the same feature matrix always produce the same predictions, verified by a test running inference twice and asserting identical results.
- **Batch support.** `feature_matrix` is a sequence of rows; the artifact is loaded once per call and the model predicts over the whole matrix in one `.predict()` call — no per-row reloading or retraining.
- **No dataset-specific branching.** The module never branches on a dataset name (verified by a test scanning its own source for `"nsl-kdd"`/`"unsw"`/`"cicids"`); `dataset_schema` is used only as an artifact-compatibility *comparison*, never a decision.
- **Model artifact safety.** Uses the existing registry/artifact mechanism unchanged — no filesystem path is ever accepted as a parameter; the only path used is the one already recorded on the registry row. No `eval`/`exec`, no dynamic class import, no subprocess/shell execution.
- **Error handling.** A dedicated `InferenceError` (distinct from `ModelTrainingError`/`DatasetSplitError`/`EvaluationError`) covers: no registered model found, a model with no usable artifact, a corrupt/unreadable/malformed artifact, an incompatible feature schema/version/dataset-schema, a feature-count mismatch, a missing preprocessing object, an empty input, and an internal inconsistency in the model's output (wrong prediction count, prediction values outside `{0, 1}`, malformed probability output). Messages never include a filesystem path, stack trace, SQL, or raw exception text; details are logged server-side via `logger.exception`/`logger.error` only.
- **Why pipeline integration is deferred.** Wiring this service into `batch_processor.py`, persisting `DetectionResult` rows, and exposing predictions over an API or the frontend are all Step 26+ concerns. Keeping inference as an independently-tested, standalone function now means that integration work can be reviewed on its own, without re-verifying the prediction logic itself.

## Model Inference in Batch Processing

```
claim batch -> ingest CSV -> persist mapped features (recognized schema only)
    -> load THIS batch's persisted feature vectors + row_numbers
    -> resolve a READY, compatible model from the registry
    -> predict_with_model() (app.services.model_inference, unchanged)
    -> persist_detection_results() (app.services.detection_result_persistence)
    -> DetectionResult rows, linked to DetectionBatch and ModelMetadata
```

Step 26 wires the existing `app.services.model_inference.predict_with_model` into `app.services.batch_processor`'s processing boundary; Step 27 (below, "DetectionResult Persistence") adds the step after it that actually writes the resulting predictions to the database. **Together these steps only connect inference to processing and persist its output — they do not add an API or frontend surface for results, and do not compute or display any risk, severity, anomaly, or confidence score.**

- **Where inference occurs in the processing flow.** `start_processing` calls `_run_feature_extraction` (unchanged: ingest, adapt, map, persist) and then, in the same `try` block (so the existing error handling covers it unchanged), calls the new `_run_model_inference`. Inference is attempted only when the batch's CSV header was recognized by a dataset adapter *and* at least one `MappedFeatureRecord` row was actually persisted for it — the same condition that already distinguished "a dataset was recognized" from "it wasn't" before this step.
- **How persisted features reach inference.** `_load_feature_matrix_for_batch` queries `MappedFeatureRecord` filtered to `batch_id` (the entire batch-isolation guarantee — another batch's rows can never match), ordered by `row_number`, and builds each row via the existing `app.services.feature_persistence.load_feature_vector` — the same `FEATURE_SCHEMA`-ordered mechanism `app.services.training_data` already uses, returning the matching `row_number`s alongside so Step 27's persistence can trace each prediction back to its original row. The original CSV is never re-read for inference. This deliberately does **not** go through `training_data.load_training_data`: that loader excludes any row whose label can't be mapped to BENIGN/ATTACK, which is correct for assembling a training set but wrong for inference — real inference input is typically unlabeled entirely, and excluding unlabeled rows would make inference silently run on nothing.
- **How the READY model is selected.** `_select_ready_model` queries the existing `ModelMetadata` registry for `status = READY` rows whose `dataset_name`/`feature_set_version` match the batch's recognized dataset schema and the code's current feature schema version, ordered by `created_at` descending (ties broken by `id` descending) — a deterministic "most recently registered compatible model" rule, not a new model-management system. No model is fabricated, auto-trained, or silently substituted when incompatible; `None` means nothing suitable exists.
- **How compatibility is enforced.** Entirely by reusing `predict_with_model`'s own validation (feature schema, feature schema version, dataset schema, feature count, preprocessing-object presence) — `batch_processor` does not duplicate any compatibility rule. It only decides *which* model to hand to that validation; the validation itself is unchanged Step 25 code.
- **How failures are handled.** No compatible `READY` model, an unusable artifact, or a failure persisting the resulting `DetectionResult` rows all raise `ApiException(500, "inference_unavailable" | "detection_result_persistence_unavailable", ...)` with a generic, safe message — caught by `start_processing`'s existing `except ApiException`/`except Exception` handling (no new lifecycle branch), which marks the batch `failed` exactly as any other processing failure already does. `InferenceError`/`DetectionResultPersistenceError` are caught and re-raised the same way. No filesystem path, stack trace, SQL, or raw exception text ever reaches the batch's `error_message`; full detail is logged server-side via `logger.exception`/`logger.error`.
- **Why `DetectionResult` persistence was deferred, then added.** Step 26 deliberately produced an `InferenceResult` and only logged it, so inference integration could be reviewed independently of persistence. Step 27 is that persistence step, reading the exact same `InferenceResult` Step 26 already produces — no inference or compatibility logic needed to change.
- **Why risk/severity/anomaly/explanation/alert functionality is not part of this step.** None of those concepts exist anywhere in this integration; `InferenceResult`/`DetectionResult` carry only what Steps 25/27 define (predicted label, prediction name, attack probability when genuinely available, model/batch/row provenance). Nothing here computes or implies a judgment about risk.
- **Lifecycle truthfulness.** The batch still never reaches `completed` — only `processing` (inference ran and its results were persisted, or inference was correctly skipped) or `failed` (inference or its persistence was expected but could not happen, or an earlier step failed). `processed_records`/`failed_records` are untouched by this step; they remain detection-outcome counters this pipeline does not yet populate, and nothing here fabricates values for them.
- **Metrics/claims.** Nothing in this integration claims production IDS performance — it only orchestrates an existing, already-evaluated model's predictions into the processing path, for one batch at a time.

## DetectionResult Persistence

```
InferenceResult (app.services.model_inference)
    -> persist_detection_results() (app.services.detection_result_persistence)
    -> DetectionResult rows  <- row_number, predicted_label, prediction_name,
                                 attack_probability, batch_id, model_id
```

Step 27 gives the predictions Step 26 produces a persistent home. `app/models/detection_result.py` is **redesigned** from an earlier, never-populated MVP placeholder (`predicted_class`/`confidence`/`severity`/flow-context columns that nothing in the real pipeline ever wrote) to the fields the actual training/inference architecture produces. **This step is persistence only — it does not add an API endpoint, frontend functionality, risk/severity/anomaly/confidence scoring beyond what Step 25 already defined, or a new `completed` lifecycle state.**

- **Relationship to `DetectionBatch` and `InferenceResult`.** Each `DetectionResult` row has a required `batch_id` (FK to `detection_batches.id`) and `model_id` (FK to `model_metadata.id`), reusing the existing registry/batch identifiers rather than duplicating model or batch metadata onto this table. `app.services.detection_result_persistence.persist_detection_results(db, batch_id, inference_result, row_numbers)` builds one row per `inference_result.predictions[i]`, tagged with `row_numbers[i]` — the same `MappedFeatureRecord.row_number` that row came from — so every prediction traces back to the batch, the original CSV row, and the model used, without duplicating that row's feature vector (it already exists on `MappedFeatureRecord`, reachable by `batch_id` + `row_number`).
- **Fields stored.** `row_number` (int, the original row), `predicted_label` (`0`/`1`, `BinaryLabel`'s own convention), `prediction_name` (`"benign"`/`"attack"`), `attack_probability` (nullable float). **The NULL/real distinction is deliberate and enforced**: a model that didn't genuinely support `predict_proba` leaves this column `NULL`, never a fabricated placeholder like `0.0` — `0.0` is reserved for an actual reported probability of zero, which is a different, real fact. A dedicated test (`test_a_null_probability_is_never_coerced_to_zero`) proves the two are never conflated.
- **Transaction guarantees.** `persist_detection_results` stages every row for a call with `db.add_all()` and commits exactly once — proven by a test spying on `Session.commit` and asserting it is called only once regardless of how many predictions are persisted. A failure (a database error, or a uniqueness violation) rolls back before raising `DetectionResultPersistenceError`, so a failed attempt never leaves a partial set of rows behind — not even the ones that would have inserted cleanly.
- **Uniqueness guarantees.** `UniqueConstraint("batch_id", "row_number")` makes a second insert attempt for an already-persisted `(batch, row)` pair fail at the database level (an `IntegrityError`, caught and re-raised as `DetectionResultPersistenceError`) rather than silently creating a duplicate or silently succeeding. `CheckConstraint`s additionally enforce `predicted_label IN (0, 1)`, `prediction_name IN ('benign', 'attack')`, and `attack_probability` either `NULL` or in `[0.0, 1.0]` — defense in depth beyond what the application layer already validates.
- **Does not re-read the CSV, rerun feature extraction, or rerun inference.** This service's only inputs are an already-produced `InferenceResult` and a list of `row_numbers` a caller already determined (see `app.services.batch_processor._run_model_inference`); it never queries `MappedFeatureRecord`, never calls `predict_with_model`, and never touches the original upload file.
- **Batch isolation.** Every row's `batch_id` comes from the single `batch_id` parameter the whole call was made with — there is no code path by which one call can write rows for more than one batch, and a dedicated test persists two batches' results independently and confirms neither's rows appear under the other's `batch_id`.
- **Kept in a dedicated service, not inside `model_inference.py`.** `app.services.model_inference` has no database dependency and stays that way; persistence is entirely `app.services.detection_result_persistence`'s responsibility, called from `app.services.batch_processor` alongside (not inside) the call to `predict_with_model`.
- **Limitations.** No frontend reads these rows yet — only the read-only API below (Step 28) does. No batch reaches `completed` because of this persistence — that still requires a later step to define. Repeated processing of the same batch is prevented upstream by the existing atomic claim mechanism, not by this service; this service's own duplicate protection is a database-level backstop for whatever calls it, not a substitute for that claim.

## DetectionResult API

```
GET /api/v1/detection/batches/{batch_id}/results   (paginated, one batch's own rows)
GET /api/v1/detection/results/{result_id}          (one row by id)
```

Step 28 exposes the `DetectionResult` rows Step 27 persists through two read-only endpoints in the existing `app/api/v1/detection.py` router, backed by a new `app.services.detection_result_service` query layer. **This step is read access only — it does not create, update, or delete any `DetectionResult` row, does not re-run inference, feature extraction, or CSV ingestion, and does not add risk/severity/anomaly/confidence scoring beyond what Steps 25/27 already defined.**

- **Endpoints.** `GET /batches/{batch_id}/results` returns one page of the given batch's own results, 404 if the batch itself does not exist (validated via the existing `batch_service.get_batch` before querying results — the same check `GET /batches/{batch_id}` already uses). `GET /results/{result_id}` returns one result by id, 404 if it does not exist. Both are plain reads: `app.services.detection_result_service.list_detection_results_for_batch`/`get_detection_result` only ever `SELECT`, never `INSERT`/`UPDATE`/`DELETE`.
- **Pagination.** Follows the exact convention `GET /batches` already established: 1-based `page` (default `1`), `page_size` (default `20`, max `100`), both validated by FastAPI's `Query(..., ge=..., le=...)` before the handler runs (invalid values are a `422`, never a silent clamp). The response carries `{items, batch_id, page, page_size, total_items, total_pages}` — the same shape `DetectionBatchListResponse` uses, with `batch_id` added since this list is always scoped to one batch. Two queries total per request (a `COUNT` and the page itself, `ORDER BY ... LIMIT/OFFSET` in SQL) plus one `selectinload` query for the related model's name/version — never one query per row, and never the whole table loaded into memory to paginate in Python.
- **Deterministic ordering.** `ORDER BY row_number ASC, id ASC` — the same row always sorts the same way, and page boundaries never shift between requests for the same data (verified by a test that repeats the same request and compares exact ordering).
- **Response contents.** `id`, `batch_id`, `model_id`, `model_name`, `model_version` (read through the existing `DetectionResult.model` relationship via two new read-only properties on the ORM model — never a duplicated column), `row_number`, `predicted_label`, `prediction_name`, `attack_probability` (nullable — preserved as JSON `null`, never coerced to `0`), `created_at`. Deliberately excludes anything not already defined: no raw feature vector, no CSV content, no filesystem/artifact path, no risk/severity/confidence field.
- **Batch isolation.** `list_detection_results_for_batch` filters strictly on `batch_id` — there is no code path by which one request can return another batch's rows, verified by a dedicated test that persists results for two batches and confirms neither list contains the other's `batch_id`. An arbitrary (but valid-shaped) UUID that doesn't match any batch correctly 404s rather than returning anything.
- **Safe errors.** A database failure on either endpoint returns a generic `{"error": ..., "message": ...}` body with no SQL, filesystem path, or raw exception text — verified by tests that inject a failure with a deliberately sensitive message and confirm it never reaches the response. A malformed UUID in either path parameter is a `422` from FastAPI's own path-type validation, before any query runs — the same behavior the existing `/batches/{batch_id}` endpoint already has, which also means SQL-injection-shaped text in a UUID path segment is rejected as an invalid UUID, never reaching the database layer.
- **Limitations.** No filtering or search beyond pagination (e.g. by `predicted_label` or `prediction_name`) — deliberately out of scope for this step. No frontend consumes these endpoints yet. No batch lifecycle change: a batch still never reaches `completed`, and these endpoints do not imply one did.

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

- A model-training foundation exists (`app/services/training_data.py`, `app/services/model_training.py`) that can train, evaluate, persist, and register one baseline `RandomForestClassifier` per dataset family from already-persisted features — but no inference/prediction API, no detection results, and no dashboard logic exists yet. A batch's CSV is read, validated, and feature-extracted into `NormalizedFlowFeatures` during processing, but no model runs against a batch as part of processing — nothing is actually analyzed and no batch reaches `completed` yet.
- Training is invoked only as an internal service call, not from any endpoint or UI; there is no mechanism yet to trigger, schedule, or monitor a training run from outside a direct Python call.
- Model inference now runs as part of batch processing (`batch_processor._run_model_inference`, Step 26) for a batch whose dataset schema was recognized and whose features were persisted, provided a compatible `READY` model exists, and its predictions are now persisted as `DetectionResult` rows (Step 27) — but there is still no API or frontend exposure for those rows, and no batch reaches `completed` yet: persisting a prediction is not the same as recording a finished detection (no risk/severity/review workflow exists). A batch with a recognized schema but no compatible trained model, or whose detection-result persistence fails, now fails processing (a deliberate Step 26/27 behavior, not a bug) rather than silently skipping inference or its persistence.
- A dataset-adapter layer exists (`app/services/dataset_adapters/`) that can translate NSL-KDD/UNSW-NB15/CICIDS-style CSV schemas into a canonical representation, but it is not called during batch processing — there is no mechanism yet for a batch to declare or select a dataset.
- A dataset-profiling service exists (`app/services/dataset_profiling/`) that can produce a structural/statistical profile of a stored batch's CSV, but it is not called during batch processing or exposed over the API yet — it is a standalone, independently-tested service.
- A dataset feature-mapping layer exists (`app/services/feature_mapping.py`) that can turn a `CanonicalDatasetRecord` into a deterministic, provenance-carrying `MappedFeatureSet`, but it is not called during batch processing yet — it is a standalone, independently-tested service, same as the adapter and profiling layers above.
- Only batches created by real uploads exist; nothing inserts sample or fake data.
- No authentication/authorization exists yet.
- CORS is configured for local development origins only; it has not been reviewed or hardened for production use.
- Five public endpoints exist: `GET /api/v1/health`, `POST /api/v1/detection/upload`, `GET /api/v1/detection/batches`, `GET /api/v1/detection/batches/{batch_id}` and `POST /api/v1/detection/batches/{batch_id}/process`.
