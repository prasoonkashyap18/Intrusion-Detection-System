"""Binary training-label interpretation: a dataset's own label text ->
benign/attack, isolated here so no other module needs dataset-specific
knowledge of what a particular dataset family calls "benign".

Step 18's adapters deliberately preserve label text verbatim and never
interpret it ("that belongs to later dataset/label processing work" — see
`app.services.dataset_adapters.canonical`'s docstring). This module is that
later work: it is the ONLY place in the codebase allowed to branch on a
dataset's `schema_id` to decide what counts as benign. `app.services.
training_data` (Step 22's training-data loader) and `app.services.
model_training` call `to_binary_label()` uniformly and contain no
dataset-specific branching themselves.

Conservative by design: a `schema_id` this module does not recognize, or
label text that does not match a known convention for its dataset, maps to
`None` ("unknown/unmappable") — never guessed into BENIGN or ATTACK. A
missing label (`label_text is None`) is also `None`. Callers decide how to
handle unmappable rows (Step 22's loader excludes them and reports the
count rather than silently training on a guess).
"""

from __future__ import annotations

from enum import IntEnum


class BinaryLabel(IntEnum):
    """The training target: `0` = benign, `1` = attack. An `IntEnum` (not
    a plain int) so a caller can never pass an arbitrary integer through
    by accident without it being a deliberately named one of these two."""

    BENIGN = 0
    ATTACK = 1


def to_binary_label(dataset_schema: str, label_text: str | None) -> BinaryLabel | None:
    """Maps one row's raw label text to `BENIGN`/`ATTACK`, using the
    documented convention for `dataset_schema`. Returns `None` — never a
    guess — when the label is missing, the schema is not one of the
    conventions below, or the text does not match that schema's known
    values.
    """
    if not label_text:
        return None

    text = label_text.strip()
    if not text:
        return None

    mapper = _CONVENTIONS.get(dataset_schema)
    if mapper is None:
        return None
    return mapper(text)


def _nsl_kdd_label(text: str) -> BinaryLabel | None:
    # NSL-KDD / KDD Cup 99 convention (documented in the dataset's own
    # class column): the single literal value "normal" means benign;
    # every other value is a specific attack/intrusion type name (e.g.
    # "neptune", "smurf", "satan") and therefore an attack. There is no
    # third value in this dataset's documented label vocabulary.
    return BinaryLabel.BENIGN if text.lower() == "normal" else BinaryLabel.ATTACK


def _unsw_nb15_label(text: str) -> BinaryLabel | None:
    # UNSW-NB15's own `label` column is already binary by the dataset's
    # documentation: "0" = normal, "1" = attack. Any other text is not a
    # value this dataset's label column is documented to contain, so it is
    # reported as unmappable rather than guessed (e.g. a typo, or text
    # accidentally read from the wrong column).
    if text == "0":
        return BinaryLabel.BENIGN
    if text == "1":
        return BinaryLabel.ATTACK
    return None


def _cicids_label(text: str) -> BinaryLabel | None:
    # CICIDS/CICFlowMeter convention: the literal value "BENIGN" means
    # benign; every other value is a specific attack type name (e.g.
    # "DDoS", "PortScan", "Web Attack - Brute Force") and therefore an
    # attack — the same single-benign-value shape as NSL-KDD, under this
    # dataset family's own documented vocabulary.
    return BinaryLabel.BENIGN if text.upper() == "BENIGN" else BinaryLabel.ATTACK


_CONVENTIONS = {
    "nsl-kdd-style": _nsl_kdd_label,
    "unsw-nb15-style": _unsw_nb15_label,
    "cicids-style": _cicids_label,
}
