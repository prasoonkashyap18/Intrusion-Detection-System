"""Importing this package registers all ORM models on the shared Base's
metadata, so Base.metadata.create_all() (see app/db/database.py) can
discover and create their tables.
"""

from app.models.detection_batch import DetectionBatch
from app.models.model_metadata import ModelMetadata
from app.models.detection_result import DetectionResult

__all__ = ["DetectionBatch", "ModelMetadata", "DetectionResult"]
