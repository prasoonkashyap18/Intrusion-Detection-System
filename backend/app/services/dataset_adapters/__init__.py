"""Dataset-adapter layer: translates a dataset-specific CSV schema into the
project's canonical representation, without `ingestion.py` or
`feature_extraction.py` knowing about any individual dataset.

See `base.py` for the `DatasetAdapter` contract, `canonical.py` for
`CanonicalDatasetRecord`, and `registry.py` for how a header is matched to
an adapter (or explicitly recognized as unsupported). backend/README.md's
"Dataset Adapter Layer" section covers the architecture and how to add a
new adapter.

Not yet called from `batch_processor.py` — see `registry.py`'s and
backend/README.md's notes on why this step intentionally stops at a
standalone, independently-tested layer.
"""

from __future__ import annotations

from app.services.dataset_adapters.base import DatasetAdapter
from app.services.dataset_adapters.canonical import CanonicalDatasetRecord
from app.services.dataset_adapters.registry import ADAPTERS, AdapterSelection, select_adapter

__all__ = [
    "ADAPTERS",
    "AdapterSelection",
    "CanonicalDatasetRecord",
    "DatasetAdapter",
    "select_adapter",
]
