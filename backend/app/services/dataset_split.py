"""Train/validation/test dataset preparation: a `TrainingDataset`
(app.services.training_data) -> three explicit, disjoint, deterministic
partitions, ready for `app.services.model_training` or any later
evaluation step.

    TrainingDataset (app.services.training_data)
        |
    prepare_training_data()
        |
    PreparedDataset  <- .train / .validation / .test, each a
                         DatasetPartition with its own feature matrix,
                         labels and truthful class-distribution metadata

Why a three-way split exists: a model fit on `train` and tuned/selected
using `validation` still needs a partition that influenced *no* decision
made before it is finally evaluated, or every reported number is
optimistic by construction. `test` is prepared here, alongside `train` and
`validation`, specifically so a later evaluation step can use genuinely
held-out data — this module does not evaluate anything itself (see the
module docstring's non-goals), it only guarantees `test` stays untouched
by every choice made while producing `train`/`validation`.

Splitting happens in two stages, both before any learned preprocessing is
fitted: first `test_fraction` of the data is set aside as `test` (skipped
entirely when `test_fraction` is `0.0`), then `validation_fraction` (of the
*original* total) is carved out of what remains as `validation`; whatever
is left is `train`. Both stages use a stratified split (preserving class
proportions) when there are enough samples of each class, and both use the
same `random_seed` for a given call — same inputs and configuration always
produce the same three partitions.

Batch/group isolation: multiple `MappedFeatureRecord` rows can come from
the same uploaded batch, and rows from one batch are not independent of
each other in the way i.i.d. splitting assumes — the same measurement
process, sometimes the same flows. Splitting such rows across train/test
individually risks the model (or the reported metric) benefiting from
information that leaked from one partition to another via the batch they
share. When `group_by_batch=True` (the default), this module instead
assigns whole batches to a single partition each, via
`sklearn.model_selection.StratifiedGroupKFold` — every row from a given
batch lands in exactly one of train/validation/test, never split across
them, while still trying to keep class proportions balanced across
partitions. This requires at least two distinct batches to isolate any
partition from another; `DatasetSplitError` is raised otherwise (or the
caller may pass `group_by_batch=False` to explicitly accept row-level
splitting without batch isolation, e.g. for a single-batch dataset).

Preprocessing isolation is this module's caller's responsibility (see
`app.services.model_training`): this module only produces the partitions.
It deliberately does not compute or return any dataset-wide statistic
(e.g. a global mean or median) that could later be misused to fit
preprocessing before splitting — the only thing passed between the split
decision and the partitions themselves is a list of row indices.

Missing-value semantics are untouched: a `DatasetPartition.feature_matrix`
is a direct slice of the input `TrainingDataset.feature_matrix`, so
missing/malformed/unsupported values are still `None`, genuine zeros are
still `0.0`, and nothing here ever fabricates a value.

Non-goals: this module does not train, evaluate, or select a model, does
not compute any model-performance metric, and does not expose any
attack-detection concept (confidence/severity/risk/alerts) — it produces
data partitions only.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

import numpy as np
from sklearn.model_selection import StratifiedGroupKFold, train_test_split

from app.services.dataset_adapters.label_mapping import BinaryLabel
from app.services.training_data import TrainingDataset

logger = logging.getLogger("ai_ids")

DEFAULT_TEST_FRACTION = 0.2
DEFAULT_VALIDATION_FRACTION = 0.2
DEFAULT_RANDOM_SEED = 42
"""Fixed and documented so the same inputs always produce the same three
partitions — see the module docstring."""

MIN_SAMPLES_PER_CLASS = 10
"""A stricter floor than `app.services.model_training`'s own pre-check:
this module may need to carve *two* sequential folds out of one class
(test, then validation), each needing at least one member of every class
on both sides of the cut. 10 comfortably covers the default 20%/20%
fractions; an insufficient actual count still fails safely via the
`ValueError` -> `DatasetSplitError` wrapping below regardless of this
pre-check's exact threshold."""

MIN_DISTINCT_GROUPS = 2
"""The minimum needed to isolate even one partition from another when
`group_by_batch=True`. The real requirement is usually higher (enough
groups to support the fold counts both splitting stages need) — this is
only a fast, friendly pre-check; `ValueError` from `StratifiedGroupKFold`
is still caught and wrapped for the subtler cases this doesn't catch."""


class DatasetSplitError(Exception):
    """Raised for any train/validation/test split precondition this module
    cannot safely meet — never silently worked around (e.g. by producing
    an empty partition, mixing a batch across partitions, or guessing).
    `message` is written to be safe to surface to a caller: no filesystem
    paths, no stack traces, no SQL, no raw exception text."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class DatasetPartition:
    """One of the three partitions `prepare_training_data` produces. A
    direct, order-preserving slice of the input `TrainingDataset` — no
    value is fabricated, recomputed, or reordered here."""

    feature_matrix: list[list[float | None]]
    """`FEATURE_SCHEMA`-ordered rows, inherited unchanged from the input
    `TrainingDataset`. `None` still means genuinely missing/malformed/
    unsupported; never replaced with `0` here."""

    labels: list[BinaryLabel]
    """Parallel to `feature_matrix`. Never present inside `feature_matrix`
    itself — see `app.services.training_data` for where feature/label
    separation is first established."""

    record_ids: list[uuid.UUID]
    """Parallel to `feature_matrix`: traceability only, never a feature."""

    batch_ids: list[uuid.UUID]
    """Parallel to `feature_matrix`: which batch each row came from."""

    sample_count: int
    class_distribution: dict[str, int]
    """`{"benign": N, "attack": M}` — real counts from this partition's
    own rows, nothing fabricated or estimated."""

    class_proportions: dict[str, float]
    """`class_distribution` normalized by `sample_count`; `{"benign": 0.0,
    "attack": 0.0}` for an intentionally empty partition (never a
    division by zero)."""


@dataclass(frozen=True)
class PreparedDataset:
    """The three explicit, disjoint partitions produced from one
    `TrainingDataset`, plus the configuration that produced them — enough
    to know exactly how reproducing this call would behave."""

    train: DatasetPartition
    validation: DatasetPartition
    test: DatasetPartition
    """Deliberately not evaluated against anything by this module, or by
    `app.services.model_training` unless a later step explicitly chooses
    to. Kept untouched until a genuine evaluation step exists."""

    feature_schema_version: str
    random_seed: int
    test_fraction: float
    validation_fraction: float
    group_by_batch: bool
    excluded_unmappable_label_count: int
    """Carried through unchanged from the input `TrainingDataset` — rows
    already excluded for an unmappable label never reach any partition."""


def prepare_training_data(
    dataset: TrainingDataset,
    *,
    test_fraction: float = DEFAULT_TEST_FRACTION,
    validation_fraction: float = DEFAULT_VALIDATION_FRACTION,
    random_seed: int = DEFAULT_RANDOM_SEED,
    group_by_batch: bool = True,
) -> PreparedDataset:
    """Splits `dataset` into disjoint train/validation/test partitions.

    `test_fraction=0.0` skips creating a non-empty test partition entirely
    (useful for a caller that only wants a train/validation split; `test`
    is still returned, with `sample_count == 0`, for a uniform return
    shape). Both splitting stages happen before any learned preprocessing
    is fitted — this function does not fit or return any preprocessing
    object itself; see `app.services.model_training`.

    Raises `DatasetSplitError` for: an invalid fraction configuration, no
    labeled data at all, fewer than two classes present, too few samples
    in a class to split safely, or (when `group_by_batch=True`) too few
    distinct batches to isolate the requested partitions from each other.
    Never silently produces an empty, overlapping, or batch-leaking
    partition.
    """
    _validate_split_configuration(test_fraction, validation_fraction)

    if len(dataset.labels) == 0:
        raise DatasetSplitError("No labeled data is available to split.")

    y = np.array([int(label) for label in dataset.labels], dtype=int)
    _validate_class_counts(y)

    groups = np.array([str(batch_id) for batch_id in dataset.batch_ids])
    if group_by_batch:
        distinct_groups = len(set(groups.tolist()))
        if distinct_groups < MIN_DISTINCT_GROUPS:
            raise DatasetSplitError(
                "Group-aware splitting requires at least "
                f"{MIN_DISTINCT_GROUPS} distinct batches to isolate one partition from another; "
                f"found {distinct_groups}. Provide more batches, or pass group_by_batch=False to "
                "accept row-level splitting without batch isolation."
            )

    train_idx, val_idx, test_idx = _compute_split_indices(
        y, groups, test_fraction, validation_fraction, random_seed, group_by_batch
    )

    _assert_disjoint(train_idx, val_idx, test_idx)
    if group_by_batch:
        _assert_group_isolated(groups, train_idx, val_idx, test_idx)

    return PreparedDataset(
        train=_build_partition(dataset, train_idx),
        validation=_build_partition(dataset, val_idx),
        test=_build_partition(dataset, test_idx),
        feature_schema_version=dataset.feature_schema_version,
        random_seed=random_seed,
        test_fraction=test_fraction,
        validation_fraction=validation_fraction,
        group_by_batch=group_by_batch,
        excluded_unmappable_label_count=dataset.excluded_unmappable_label_count,
    )


def _validate_split_configuration(test_fraction: float, validation_fraction: float) -> None:
    if not (0.0 <= test_fraction < 1.0):
        raise DatasetSplitError(f"test_fraction must be in [0.0, 1.0); got {test_fraction!r}.")
    if not (0.0 < validation_fraction < 1.0):
        raise DatasetSplitError(f"validation_fraction must be in (0.0, 1.0); got {validation_fraction!r}.")
    if test_fraction + validation_fraction >= 1.0:
        raise DatasetSplitError(
            f"test_fraction ({test_fraction!r}) + validation_fraction ({validation_fraction!r}) "
            "must leave a non-zero fraction of the data for training."
        )


def _validate_class_counts(y: np.ndarray) -> None:
    classes, counts = np.unique(y, return_counts=True)
    if len(classes) < 2:
        raise DatasetSplitError("Cannot stratify a split: only one class is present in the labeled data.")
    if counts.min() < MIN_SAMPLES_PER_CLASS:
        counts_by_class = {int(c): int(n) for c, n in zip(classes, counts)}
        raise DatasetSplitError(
            "Insufficient samples per class to produce a reliable train/validation/test split: need at "
            f"least {MIN_SAMPLES_PER_CLASS} samples for each class, found {counts_by_class}."
        )


def _compute_split_indices(
    y: np.ndarray,
    groups: np.ndarray,
    test_fraction: float,
    validation_fraction: float,
    random_seed: int,
    group_by_batch: bool,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    all_idx = np.arange(len(y))

    if test_fraction > 0:
        keep_idx, test_idx = _split_two_way(all_idx, y, groups, test_fraction, random_seed, group_by_batch)
    else:
        keep_idx, test_idx = all_idx, np.array([], dtype=int)

    y_keep = y[keep_idx]
    groups_keep = groups[keep_idx]
    val_fraction_of_remaining = validation_fraction / (1 - test_fraction)
    train_idx, val_idx = _split_two_way(keep_idx, y_keep, groups_keep, val_fraction_of_remaining, random_seed, group_by_batch)

    return train_idx, val_idx, test_idx


def _split_two_way(
    current_idx: np.ndarray,
    y_subset: np.ndarray,
    groups_subset: np.ndarray,
    fraction: float,
    random_seed: int,
    group_by_batch: bool,
) -> tuple[np.ndarray, np.ndarray]:
    """Splits `current_idx` into a "kept" majority and an "extracted"
    ~`fraction` minority, mapping back to original dataset positions.
    Used twice by `_compute_split_indices` — once to carve out `test`,
    once to carve `validation` out of what remains."""
    local = np.arange(len(current_idx))

    if group_by_batch:
        n_splits = _fraction_to_n_splits(fraction)
        dummy_X = np.zeros((len(local), 1))
        try:
            splitter = StratifiedGroupKFold(n_splits=n_splits, shuffle=True, random_state=random_seed)
            keep_local, extract_local = next(iter(splitter.split(dummy_X, y_subset, groups_subset)))
        except ValueError:
            logger.exception("Group-aware stratified split failed")
            raise DatasetSplitError(
                "Unable to create a group-isolated, stratified split from the available data; there may "
                "be too few distinct batches or too few samples in one class for the requested split."
            ) from None
    else:
        try:
            keep_local, extract_local = train_test_split(
                local, test_size=fraction, random_state=random_seed, stratify=y_subset
            )
        except ValueError:
            logger.exception("Stratified split failed")
            raise DatasetSplitError(
                "Unable to create a stratified split from the available data; there may be too few "
                "samples in one class for the requested split."
            ) from None

    return current_idx[keep_local], current_idx[extract_local]


def _fraction_to_n_splits(fraction: float) -> int:
    if not (0.0 < fraction < 1.0):
        raise DatasetSplitError(f"Invalid internal split fraction {fraction!r}; must be strictly between 0 and 1.")
    return max(round(1 / fraction), 2)


def _assert_disjoint(train_idx: np.ndarray, val_idx: np.ndarray, test_idx: np.ndarray) -> None:
    combined = np.concatenate([train_idx, val_idx, test_idx])
    if len(combined) != len(set(combined.tolist())):
        raise DatasetSplitError("Unable to produce non-overlapping train/validation/test partitions.")


def _assert_group_isolated(groups: np.ndarray, train_idx: np.ndarray, val_idx: np.ndarray, test_idx: np.ndarray) -> None:
    train_groups = set(groups[train_idx].tolist())
    val_groups = set(groups[val_idx].tolist())
    test_groups = set(groups[test_idx].tolist())
    if (train_groups & val_groups) or (train_groups & test_groups) or (val_groups & test_groups):
        raise DatasetSplitError("Unable to keep every batch isolated to a single train/validation/test partition.")


def _build_partition(dataset: TrainingDataset, idx: np.ndarray) -> DatasetPartition:
    feature_matrix = [dataset.feature_matrix[i] for i in idx]
    labels = [dataset.labels[i] for i in idx]
    record_ids = [dataset.record_ids[i] for i in idx]
    batch_ids = [dataset.batch_ids[i] for i in idx]

    sample_count = len(labels)
    benign_count = sum(1 for label in labels if int(label) == 0)
    attack_count = sum(1 for label in labels if int(label) == 1)
    class_distribution = {"benign": benign_count, "attack": attack_count}
    class_proportions = (
        {"benign": benign_count / sample_count, "attack": attack_count / sample_count}
        if sample_count
        else {"benign": 0.0, "attack": 0.0}
    )

    return DatasetPartition(
        feature_matrix=feature_matrix,
        labels=labels,
        record_ids=record_ids,
        batch_ids=batch_ids,
        sample_count=sample_count,
        class_distribution=class_distribution,
        class_proportions=class_proportions,
    )


__all__ = [
    "DatasetSplitError",
    "DatasetPartition",
    "PreparedDataset",
    "prepare_training_data",
    "DEFAULT_TEST_FRACTION",
    "DEFAULT_VALIDATION_FRACTION",
    "DEFAULT_RANDOM_SEED",
]
