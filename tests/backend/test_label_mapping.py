"""Tests for app.services.dataset_adapters.label_mapping: the one place
dataset-specific label text is turned into a binary training target.
"""

from __future__ import annotations

from app.services.dataset_adapters.label_mapping import BinaryLabel, to_binary_label


class TestNslKddConvention:
    def test_normal_is_benign(self):
        assert to_binary_label("nsl-kdd-style", "normal") == BinaryLabel.BENIGN

    def test_case_insensitive_normal_is_benign(self):
        assert to_binary_label("nsl-kdd-style", "Normal") == BinaryLabel.BENIGN

    def test_any_other_text_is_attack(self):
        assert to_binary_label("nsl-kdd-style", "neptune") == BinaryLabel.ATTACK
        assert to_binary_label("nsl-kdd-style", "smurf") == BinaryLabel.ATTACK


class TestUnswConvention:
    def test_zero_is_benign(self):
        assert to_binary_label("unsw-nb15-style", "0") == BinaryLabel.BENIGN

    def test_one_is_attack(self):
        assert to_binary_label("unsw-nb15-style", "1") == BinaryLabel.ATTACK

    def test_unrecognized_text_is_unmappable(self):
        assert to_binary_label("unsw-nb15-style", "maybe") is None


class TestCicidsConvention:
    def test_benign_literal_is_benign(self):
        assert to_binary_label("cicids-style", "BENIGN") == BinaryLabel.BENIGN

    def test_case_insensitive_benign_is_benign(self):
        assert to_binary_label("cicids-style", "benign") == BinaryLabel.BENIGN

    def test_attack_name_is_attack(self):
        assert to_binary_label("cicids-style", "DDoS") == BinaryLabel.ATTACK
        assert to_binary_label("cicids-style", "PortScan") == BinaryLabel.ATTACK


class TestMissingAndUnsupportedLabels:
    def test_none_label_is_unmappable(self):
        assert to_binary_label("nsl-kdd-style", None) is None

    def test_empty_string_label_is_unmappable(self):
        assert to_binary_label("nsl-kdd-style", "") is None

    def test_whitespace_only_label_is_unmappable(self):
        assert to_binary_label("nsl-kdd-style", "   ") is None

    def test_unrecognized_dataset_schema_is_unmappable(self):
        assert to_binary_label("some-future-dataset-style", "normal") is None

    def test_never_guesses_for_an_unknown_schema_even_with_obvious_text(self):
        assert to_binary_label("unknown", "BENIGN") is None


class TestBinaryLabelValues:
    def test_benign_is_zero_and_attack_is_one(self):
        assert int(BinaryLabel.BENIGN) == 0
        assert int(BinaryLabel.ATTACK) == 1
