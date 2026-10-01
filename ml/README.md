# ML Pipeline

This directory contains the machine-learning pipeline for AI-IDS, split into separate responsibilities:

- `data_processing/` — cleaning, transformation, and feature preparation
- `training/` — model training (produces saved model artifacts)
- `inference/` — prediction using saved model artifacts (used by the backend at request time)
- `evaluation/` — model evaluation and metrics

Not yet implemented — see [ARCHITECTURE.md](../ARCHITECTURE.md) for the full ML lifecycle and the training/inference separation.
