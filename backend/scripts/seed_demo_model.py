"""Bootstraps a demo-ready AI-IDS environment after a fresh or reset
development database: trains and registers one baseline supervised model
and one anomaly detector through the exact same ingestion -> dataset-
adaptation -> feature-persistence -> training pipeline a real CSV upload
already goes through (see `app.services.batch_processor`, `app.services.
training_data`, `app.services.model_training`, `app.services.
anomaly_detection_training`) — nothing here is a shortcut, a different
code path, or a fabricated result.

Run from the backend/ directory with its virtual environment active:

    .venv/Scripts/python.exe scripts/seed_demo_model.py   (Windows)
    .venv/bin/python scripts/seed_demo_model.py            (macOS/Linux)

Why this script exists: there is no training API endpoint (deliberately —
see backend/README.md's "Model Inference in Batch Processing" and "Model
Training Foundation" sections) and no dataset is bundled with the repo
(see data/README.md), so after `backend/data/ai_ids.db` is deleted/
recreated or `models/*.joblib` is removed, nothing in the running
application can produce a prediction until a compatible `READY` model
exists again. Before this script existed, recreating one required writing
a one-off script by hand each time. This script makes that reproducible
and documented instead.

The CSV this generates is SYNTHETIC — clearly separable value ranges
standing in for benign and attack traffic, not a real-world intrusion
dataset — used only to give the pipeline something to ingest, adapt, map,
persist, and train on. It is written through `claim_for_processing` +
the same `_run_feature_extraction` step `app.services.batch_processor.
start_processing` itself calls (imported directly, not duplicated),
deliberately *not* through the full `start_processing` entry point: that
would also attempt model inference immediately afterward, which cannot
succeed yet — this is the one bootstrap moment where persisted features
must exist before any model does. Every later upload (including the
seed CSV's own batch, if reprocessed) goes through the real, complete
`start_processing` path exactly as before.

The resulting model's `validation_metrics` are real numbers computed from
this run's own held-out predictions on this synthetic dataset — never a
production-accuracy claim, and this script does not print them as one.
"""

from __future__ import annotations

import random
import sys
import uuid
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import settings  # noqa: E402
from app.db.database import SessionLocal, init_db  # noqa: E402
from app.models.detection_batch import DetectionBatch  # noqa: E402
from app.models.enums import ModelStatus, ProcessingStatus  # noqa: E402
from app.models.model_metadata import ModelMetadata  # noqa: E402
from app.services.anomaly_detection_training import MODEL_TYPE as ANOMALY_MODEL_TYPE  # noqa: E402
from app.services.anomaly_detection_training import train_anomaly_detector  # noqa: E402
from app.services.batch_processor import _run_feature_extraction, claim_for_processing  # noqa: E402
from app.services.feature_extraction import FEATURE_SCHEMA_VERSION  # noqa: E402
from app.services.model_training import MODEL_TYPE as SUPERVISED_MODEL_TYPE  # noqa: E402
from app.services.model_training import train_baseline_model  # noqa: E402
from app.services.training_data import load_training_data  # noqa: E402

DATASET_SCHEMA = "nsl-kdd-style"
SAMPLES_PER_CLASS = 60
RANDOM_SEED = 42


def _generate_demo_csv() -> bytes:
    """A small, synthetic NSL-KDD-style CSV: clearly separable benign vs.
    attack value ranges, with enough rows per class for the existing
    stratified train/validation split to succeed (see model_training's
    MIN_SAMPLES_PER_CLASS). Deterministic given RANDOM_SEED, so re-running
    this script produces the same seed data every time."""
    rng = random.Random(RANDOM_SEED)
    lines = ["duration,protocol_type,service,flag,src_bytes,dst_bytes,class"]
    for _ in range(SAMPLES_PER_CLASS):
        duration = rng.randint(0, 10)
        src_bytes = rng.randint(50, 500)
        dst_bytes = rng.randint(50, 500)
        lines.append(f"{duration},tcp,http,SF,{src_bytes},{dst_bytes},normal")
    for _ in range(SAMPLES_PER_CLASS):
        duration = rng.randint(100, 1000)
        src_bytes = rng.randint(5000, 50000)
        dst_bytes = rng.randint(5000, 50000)
        lines.append(f"{duration},tcp,private,S0,{src_bytes},{dst_bytes},neptune")
    return "\n".join(lines).encode()


def _find_existing_ready_model(db, model_type: str) -> ModelMetadata | None:
    return (
        db.query(ModelMetadata)
        .filter(
            ModelMetadata.status == ModelStatus.READY,
            ModelMetadata.model_type == model_type,
            ModelMetadata.dataset_name == DATASET_SCHEMA,
            ModelMetadata.feature_set_version == FEATURE_SCHEMA_VERSION,
        )
        .order_by(ModelMetadata.created_at.desc())
        .first()
    )


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        existing_supervised = _find_existing_ready_model(db, SUPERVISED_MODEL_TYPE)
        existing_anomaly = _find_existing_ready_model(db, ANOMALY_MODEL_TYPE)
        if existing_supervised is not None:
            print(
                f"A compatible READY supervised model already exists: {existing_supervised.model_name} "
                f"v{existing_supervised.model_version} ({existing_supervised.id})."
            )
        if existing_anomaly is not None:
            print(
                f"A compatible READY anomaly detector already exists: {existing_anomaly.model_name} "
                f"v{existing_anomaly.model_version} ({existing_anomaly.id})."
            )
        if existing_supervised is not None and existing_anomaly is not None:
            print("Nothing to do.")
            return

        upload_dir = Path(settings.upload_dir)
        upload_dir.mkdir(parents=True, exist_ok=True)

        batch = DetectionBatch(
            id=uuid.uuid4(),
            filename="demo-training-seed.csv",
            status=ProcessingStatus.PENDING,
            total_records=SAMPLES_PER_CLASS * 2,
        )
        db.add(batch)
        db.commit()

        csv_path = upload_dir / f"{batch.id}.csv"
        csv_path.write_bytes(_generate_demo_csv())

        if not claim_for_processing(db, batch.id):
            raise RuntimeError("Could not claim the seed batch for processing (unexpected concurrent state).")

        summary = _run_feature_extraction(db, batch.id, csv_path)
        db.commit()
        print(
            f"Seed batch processed: {summary.records_persisted} row(s) persisted "
            f"(schema={summary.dataset_schema}, feature_schema_version={summary.feature_schema_version})."
        )

        dataset = load_training_data(db, [batch.id])
        print(
            f"Loaded {len(dataset.feature_matrix)} labeled row(s) for training "
            f"({dataset.excluded_unmappable_label_count} excluded as unmappable)."
        )

        artifact_dir = Path(settings.model_artifact_dir)

        if existing_supervised is None:
            result = train_baseline_model(
                db,
                dataset,
                model_name="ai-ids-baseline",
                artifact_dir=artifact_dir,
                test_fraction=0.0,
                group_by_batch=False,
            )
            print()
            print("Trained and registered a new READY supervised model:")
            print(f"  model_id:      {result.model_id}")
            print(f"  name/version:  {result.model_name} v{result.model_version}")
            print(f"  dataset:       {result.dataset_schema} (feature schema v{result.feature_schema_version})")
            print(f"  train/val:     {result.training_sample_count} / {result.validation_sample_count} samples")
            print(f"  class counts:  {result.class_distribution}")
            print(f"  validation:    {result.validation_metrics}  (this run's own held-out split, synthetic data)")
            print(f"  artifact:      {result.artifact_path}")

        if existing_anomaly is None:
            anomaly_result = train_anomaly_detector(
                db,
                dataset,
                model_name="ai-ids-anomaly-detector",
                artifact_dir=artifact_dir,
            )
            print()
            print("Trained and registered a new READY anomaly detector:")
            print(f"  model_id:      {anomaly_result.model_id}")
            print(f"  name/version:  {anomaly_result.model_name} v{anomaly_result.model_version}")
            print(f"  dataset:       {anomaly_result.dataset_schema} (feature schema v{anomaly_result.feature_schema_version})")
            print(f"  train/val:     {anomaly_result.training_sample_count} / {anomaly_result.validation_sample_count} samples (BENIGN only)")
            print(
                f"  validation:    normal_rate={anomaly_result.validation_normal_rate:.2f}  "
                "(this run's own held-out split, synthetic data — not a production false-positive-rate claim)"
            )
            print(f"  artifact:      {anomaly_result.artifact_path}")

        print()
        print(
            "Upload a compatible NSL-KDD-style CSV (columns: duration, protocol_type, "
            "service, flag, src_bytes, dst_bytes, class) through the API or frontend and "
            "process it to see real predictions from these models."
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
