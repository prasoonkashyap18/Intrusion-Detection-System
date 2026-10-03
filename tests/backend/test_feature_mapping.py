"""Tests for the Step 20 dataset feature-mapping layer
(app.services.feature_mapping): CanonicalDatasetRecord -> MappedFeatureSet.

No test here trains a model, classifies traffic, computes risk, or fits a
dataset-wide normalization parameter — this layer only translates an
already-canonicalized row into a deterministic, provenance-carrying
numeric representation.
"""

from __future__ import annotations

import dataclasses
import inspect

from app.services.dataset_adapters.cicids import CicidsStyleAdapter
from app.services.dataset_adapters.nsl_kdd import NslKddStyleAdapter
from app.services.dataset_adapters.unsw_nb15 import UnswNb15StyleAdapter
from app.services.feature_extraction import FEATURE_SCHEMA, FeatureStatus
from app.services.feature_mapping import MappedFeatureSet, map_canonical_record
from app.services.ingestion import NetworkFlowRecord


def make_record(raw_features: dict[str, str], row_number: int = 1) -> NetworkFlowRecord:
    return NetworkFlowRecord(
        row_number=row_number,
        source_ip=None,
        destination_ip=None,
        source_port=None,
        destination_port=None,
        protocol=None,
        flow_timestamp=None,
        raw_features=raw_features,
    )


class TestNslKddMapping:
    def test_nsl_kdd_fields_map_to_the_correct_canonical_features(self):
        record = make_record(
            {
                "duration": "5",
                "protocol_type": "tcp",
                "service": "http",
                "flag": "SF",
                "src_bytes": "100",
                "dst_bytes": "200",
                "class": "normal",
            }
        )
        canonical = NslKddStyleAdapter().adapt(record)

        mapped = map_canonical_record(canonical)

        assert mapped.features["flow_duration"].value == 5.0
        assert mapped.features["protocol_number"].value == 6
        assert mapped.features["forward_byte_count"].value == 100.0
        assert mapped.features["backward_byte_count"].value == 200.0

    def test_nsl_kdd_has_no_ip_or_port_columns_so_those_features_are_missing(self):
        record = make_record({"protocol_type": "tcp", "service": "http", "class": "normal"})
        canonical = NslKddStyleAdapter().adapt(record)

        mapped = map_canonical_record(canonical)

        assert mapped.features["source_ip_numeric"].status == FeatureStatus.MISSING
        assert mapped.features["source_port"].status == FeatureStatus.MISSING


class TestUnswMapping:
    def test_unsw_fields_map_to_the_correct_canonical_features(self):
        record = make_record(
            {
                "srcip": "10.0.0.1",
                "dstip": "10.0.0.2",
                "sport": "80",
                "dsport": "443",
                "proto": "tcp",
                "dur": "1.5",
                "sbytes": "500",
                "dbytes": "300",
                "spkts": "4",
                "dpkts": "3",
                "label": "0",
                "attack_cat": "",
            }
        )
        canonical = UnswNb15StyleAdapter().adapt(record)

        mapped = map_canonical_record(canonical)

        assert mapped.features["source_port"].value == 80.0
        assert mapped.features["destination_port"].value == 443.0
        assert mapped.features["forward_byte_count"].value == 500.0
        assert mapped.features["backward_byte_count"].value == 300.0
        assert mapped.features["forward_packet_count"].value == 4.0
        assert mapped.features["backward_packet_count"].value == 3.0

    def test_unsw_ip_addresses_map_to_their_numeric_encoding(self):
        import ipaddress

        record = make_record({"srcip": "10.0.0.1", "dstip": "10.0.0.2", "proto": "tcp"})
        canonical = UnswNb15StyleAdapter().adapt(record)

        mapped = map_canonical_record(canonical)

        assert mapped.features["source_ip_numeric"].value == float(int(ipaddress.ip_address("10.0.0.1")))


class TestCicidsMapping:
    def test_cicids_fields_map_to_the_correct_canonical_features(self):
        record = make_record(
            {
                "Flow Duration": "1000",
                "Total Fwd Packets": "5",
                "Total Backward Packets": "3",
                "Total Length of Fwd Packets": "600",
                "Total Length of Bwd Packets": "400",
                "Label": "BENIGN",
            }
        )
        canonical = CicidsStyleAdapter().adapt(record)

        mapped = map_canonical_record(canonical)

        assert mapped.features["flow_duration"].value == 1000.0
        assert mapped.features["forward_packet_count"].value == 5.0
        assert mapped.features["backward_packet_count"].value == 3.0
        assert mapped.features["forward_byte_count"].value == 600.0
        assert mapped.features["backward_byte_count"].value == 400.0

    def test_cicids_has_no_total_packet_count_column_so_it_is_missing(self):
        record = make_record(
            {"Flow Duration": "1000", "Total Fwd Packets": "5", "Total Backward Packets": "3", "Label": "BENIGN"}
        )
        canonical = CicidsStyleAdapter().adapt(record)

        mapped = map_canonical_record(canonical)

        assert mapped.features["packet_count"].status == FeatureStatus.MISSING


class TestPartialCustomSchema:
    def test_an_unsupported_schema_still_produces_a_fully_missing_but_valid_feature_set(self):
        # No adapter recognizes this header, so dataset_adapters.select_adapter
        # would return no match — but map_canonical_record works directly off
        # a CanonicalDatasetRecord, which a caller only constructs when an
        # adapter *did* match. This test instead exercises a minimal,
        # partial adapter-produced record to prove the mapper tolerates an
        # almost-empty canonical_fields set.
        record = make_record({"protocol_type": "tcp"})
        canonical = NslKddStyleAdapter().adapt(record)

        mapped = map_canonical_record(canonical)

        assert mapped.features["protocol_number"].status == FeatureStatus.PRESENT
        missing = [name for name, f in mapped.features.items() if f.status == FeatureStatus.MISSING]
        assert len(missing) == len(FEATURE_SCHEMA) - 1


class TestMissingCanonicalFeature:
    def test_a_feature_with_no_source_column_is_missing_not_fabricated(self):
        record = make_record({"protocol_type": "tcp", "service": "http"})
        canonical = NslKddStyleAdapter().adapt(record)

        mapped = map_canonical_record(canonical)

        feature = mapped.features["flow_duration"]
        assert feature.status == FeatureStatus.MISSING
        assert feature.value is None
        assert feature.source_column is None
        assert feature.raw_value is None


class TestMalformedNumericValue:
    def test_non_numeric_text_in_a_numeric_field_is_malformed(self):
        record = make_record({"duration": "not-a-number", "protocol_type": "tcp"})
        canonical = NslKddStyleAdapter().adapt(record)

        mapped = map_canonical_record(canonical)

        feature = mapped.features["flow_duration"]
        assert feature.status == FeatureStatus.MALFORMED
        assert feature.value is None
        assert feature.raw_value == "not-a-number"  # original text preserved

    def test_negative_count_is_malformed(self):
        record = make_record({"src_bytes": "-5", "protocol_type": "tcp"})
        canonical = NslKddStyleAdapter().adapt(record)

        mapped = map_canonical_record(canonical)

        assert mapped.features["forward_byte_count"].status == FeatureStatus.MALFORMED


class TestMalformedIp:
    def test_invalid_ip_text_is_malformed_not_guessed_at(self):
        record = make_record({"srcip": "999.999.999.999", "proto": "tcp"})
        canonical = UnswNb15StyleAdapter().adapt(record)

        mapped = map_canonical_record(canonical)

        feature = mapped.features["source_ip_numeric"]
        assert feature.status == FeatureStatus.MALFORMED
        assert feature.value is None
        assert feature.raw_value == "999.999.999.999"


class TestUnsupportedValue:
    def test_unrecognized_protocol_text_is_unsupported_not_discarded(self):
        record = make_record({"protocol_type": "QUIC"})
        canonical = NslKddStyleAdapter().adapt(record)

        mapped = map_canonical_record(canonical)

        feature = mapped.features["protocol_number"]
        assert feature.status == FeatureStatus.UNSUPPORTED
        assert feature.value is None
        assert feature.raw_value == "QUIC"
        assert feature.source_column == "protocol_type"


class TestUnknownColumn:
    def test_columns_with_no_canonical_equivalent_are_preserved_in_unknown_fields(self):
        record = make_record({"protocol_type": "tcp", "serror_rate": "0.5", "dst_host_count": "12"})
        canonical = NslKddStyleAdapter().adapt(record)

        mapped = map_canonical_record(canonical)

        assert mapped.unknown_fields == {"serror_rate": "0.5", "dst_host_count": "12"}

    def test_unknown_columns_never_appear_in_features(self):
        record = make_record({"protocol_type": "tcp", "totally_custom_stat": "42"})
        canonical = NslKddStyleAdapter().adapt(record)

        mapped = map_canonical_record(canonical)

        assert "totally_custom_stat" not in mapped.features
        assert all(f.source_column != "totally_custom_stat" for f in mapped.features.values())


class TestLabelExclusion:
    def test_mapped_feature_set_has_no_label_attribute(self):
        record = make_record({"protocol_type": "tcp", "class": "neptune"})
        canonical = NslKddStyleAdapter().adapt(record)

        mapped = map_canonical_record(canonical)

        assert not hasattr(mapped, "label")

    def test_label_text_never_appears_as_a_feature_value(self):
        record = make_record({"protocol_type": "tcp", "class": "neptune"})
        canonical = NslKddStyleAdapter().adapt(record)

        mapped = map_canonical_record(canonical)

        assert "neptune" not in [f.raw_value for f in mapped.features.values()]
        assert "neptune" not in [f.value for f in mapped.features.values()]

    def test_the_label_column_name_never_appears_as_a_feature_source_column(self):
        record = make_record({"protocol_type": "tcp", "class": "neptune"})
        canonical = NslKddStyleAdapter().adapt(record)

        mapped = map_canonical_record(canonical)

        assert all(f.source_column != "class" for f in mapped.features.values())


class TestAttackCategoryExclusion:
    def test_mapped_feature_set_has_no_attack_category_attribute(self):
        record = make_record({"srcip": "1.1.1.1", "proto": "tcp", "attack_cat": "Exploits"})
        canonical = UnswNb15StyleAdapter().adapt(record)

        mapped = map_canonical_record(canonical)

        assert not hasattr(mapped, "attack_category")

    def test_attack_category_text_never_appears_as_a_feature_value(self):
        record = make_record({"srcip": "1.1.1.1", "proto": "tcp", "attack_cat": "Exploits"})
        canonical = UnswNb15StyleAdapter().adapt(record)

        mapped = map_canonical_record(canonical)

        assert "Exploits" not in [f.raw_value for f in mapped.features.values()]

    def test_the_category_column_name_never_appears_as_a_feature_source_column(self):
        record = make_record({"srcip": "1.1.1.1", "proto": "tcp", "attack_cat": "Exploits"})
        canonical = UnswNb15StyleAdapter().adapt(record)

        mapped = map_canonical_record(canonical)

        assert all(f.source_column != "attack_cat" for f in mapped.features.values())


class TestDataLeakageProtection:
    """Requirement #12: label, attack_category, dataset_schema, row_number,
    batch ID, filename must never enter the ML feature vector."""

    def test_feature_vector_length_matches_feature_schema_exactly(self):
        record = make_record({"protocol_type": "tcp", "class": "normal"})
        canonical = NslKddStyleAdapter().adapt(record)

        mapped = map_canonical_record(canonical)

        assert len(mapped.feature_vector()) == len(FEATURE_SCHEMA)

    def test_dataset_schema_and_row_number_are_metadata_not_features(self):
        record = make_record({"protocol_type": "tcp"}, row_number=7)
        canonical = NslKddStyleAdapter().adapt(record)

        mapped = map_canonical_record(canonical)

        assert mapped.row_number == 7
        assert mapped.dataset_schema == "nsl-kdd-style"
        assert "row_number" not in mapped.features
        assert "dataset_schema" not in mapped.features
        assert 7 not in mapped.feature_vector()
        assert "nsl-kdd-style" not in mapped.feature_vector()

    def test_there_is_no_batch_id_or_filename_field_anywhere_on_the_result(self):
        record = make_record({"protocol_type": "tcp"})
        canonical = NslKddStyleAdapter().adapt(record)

        mapped = map_canonical_record(canonical)

        field_names = {f.name for f in dataclasses.fields(mapped)}
        assert "batch_id" not in field_names
        assert "filename" not in field_names


class TestDeterministicOrdering:
    def test_feature_vector_follows_feature_schema_order_exactly(self):
        record = make_record({"protocol_type": "tcp", "src_bytes": "1", "dst_bytes": "2"})
        canonical = NslKddStyleAdapter().adapt(record)

        mapped = map_canonical_record(canonical)

        assert mapped.feature_vector() == [mapped.features[name].value for name in FEATURE_SCHEMA]
        assert list(mapped.features.keys()) == list(FEATURE_SCHEMA)

    def test_repeated_mapping_of_the_same_record_is_identical(self):
        record = make_record({"srcip": "10.0.0.1", "proto": "udp", "dur": "2"})
        canonical = UnswNb15StyleAdapter().adapt(record)

        first = map_canonical_record(canonical)
        second = map_canonical_record(canonical)

        assert first.feature_vector() == second.feature_vector()


class TestReorderedSourceColumns:
    def test_reordered_csv_columns_produce_the_same_canonical_feature_order(self):
        forward = make_record({"protocol_type": "tcp", "src_bytes": "1", "dst_bytes": "2", "duration": "5"})
        backward = make_record({"duration": "5", "dst_bytes": "2", "src_bytes": "1", "protocol_type": "tcp"})

        mapped_forward = map_canonical_record(NslKddStyleAdapter().adapt(forward))
        mapped_backward = map_canonical_record(NslKddStyleAdapter().adapt(backward))

        assert mapped_forward.feature_vector() == mapped_backward.feature_vector()
        assert list(mapped_forward.features.keys()) == list(mapped_backward.features.keys())


class TestProvenancePreservation:
    def test_every_present_feature_traces_back_to_its_source_column_and_raw_text(self):
        record = make_record({"srcip": "10.0.0.1", "proto": "tcp", "dur": "1.5"})
        canonical = UnswNb15StyleAdapter().adapt(record)

        mapped = map_canonical_record(canonical)

        ip_feature = mapped.features["source_ip_numeric"]
        assert ip_feature.source_column == "srcip"
        assert ip_feature.raw_value == "10.0.0.1"

        protocol_feature = mapped.features["protocol_number"]
        assert protocol_feature.source_column == "proto"
        assert protocol_feature.raw_value == "tcp"

    def test_provenance_is_preserved_even_when_the_value_is_malformed(self):
        record = make_record({"sport": "not-a-port", "proto": "tcp"})
        canonical = UnswNb15StyleAdapter().adapt(record)

        mapped = map_canonical_record(canonical)

        feature = mapped.features["source_port"]
        assert feature.status == FeatureStatus.MALFORMED
        assert feature.source_column == "sport"
        assert feature.raw_value == "not-a-port"


class TestNoDatasetSpecificBranchingInTheMapper:
    def test_the_mapper_function_bodies_contain_no_dataset_name_literals(self):
        # Checks actual code, not module/docstring prose (which legitimately
        # names these datasets while explaining that no such branching
        # exists) — inspect.getsource on each function pulls only its body.
        import app.services.feature_mapping as module

        for _, obj in inspect.getmembers(module, predicate=inspect.isfunction):
            if obj.__module__ != module.__name__:
                continue
            source = inspect.getsource(obj)
            for forbidden in ("nsl-kdd", "nsl_kdd", "unsw", "cicids"):
                assert forbidden not in source.lower(), (
                    f"found dataset-specific reference {forbidden!r} in {obj.__name__}()"
                )

    def test_map_canonical_record_has_no_dataset_conditional_branches(self):
        source = inspect.getsource(map_canonical_record)

        assert "if dataset" not in source
        assert "elif dataset" not in source
        assert "dataset_schema ==" not in source


class TestNoFabricatedZeroValues:
    def test_a_missing_field_is_none_never_zero(self):
        record = make_record({"protocol_type": "tcp"})
        canonical = NslKddStyleAdapter().adapt(record)

        mapped = map_canonical_record(canonical)

        feature = mapped.features["flow_duration"]
        assert feature.value is None
        assert feature.value != 0

    def test_a_malformed_field_is_none_never_zero(self):
        record = make_record({"src_bytes": "garbage", "protocol_type": "tcp"})
        canonical = NslKddStyleAdapter().adapt(record)

        mapped = map_canonical_record(canonical)

        feature = mapped.features["forward_byte_count"]
        assert feature.value is None
        assert feature.value != 0

    def test_a_genuine_zero_value_is_still_reported_as_zero(self):
        record = make_record({"src_bytes": "0", "protocol_type": "tcp"})
        canonical = NslKddStyleAdapter().adapt(record)

        mapped = map_canonical_record(canonical)

        feature = mapped.features["forward_byte_count"]
        assert feature.status == FeatureStatus.PRESENT
        assert feature.value == 0.0


class TestNoDatasetWideNormalization:
    def test_map_canonical_record_takes_no_scaler_or_statistics_parameter(self):
        signature = inspect.signature(map_canonical_record)

        assert list(signature.parameters) == ["record"]

    def test_values_are_not_scaled_relative_to_other_records(self):
        # Two records with very different magnitudes must each report
        # their own raw parsed value — nothing here could even compute a
        # relative/normalized value without seeing the whole dataset,
        # which this function never receives.
        small = make_record({"src_bytes": "1", "protocol_type": "tcp"})
        large = make_record({"src_bytes": "1000000", "protocol_type": "tcp"})

        mapped_small = map_canonical_record(NslKddStyleAdapter().adapt(small))
        mapped_large = map_canonical_record(NslKddStyleAdapter().adapt(large))

        assert mapped_small.features["forward_byte_count"].value == 1.0
        assert mapped_large.features["forward_byte_count"].value == 1_000_000.0


class TestEmptyAndPartialRecords:
    def test_a_record_with_only_a_label_column_maps_to_an_entirely_missing_feature_set(self):
        record = make_record({"protocol_type": "", "class": "normal"})
        # protocol_type present as a header but empty for this row; still
        # structurally "recognized" by the adapter.
        canonical = NslKddStyleAdapter().adapt(record)

        mapped = map_canonical_record(canonical)

        assert all(f.status == FeatureStatus.MISSING for f in mapped.features.values())
        assert mapped.feature_vector() == [None] * len(FEATURE_SCHEMA)

    def test_an_entirely_empty_raw_features_record_maps_cleanly(self):
        record = make_record({})
        canonical = NslKddStyleAdapter().adapt(record)

        mapped = map_canonical_record(canonical)

        assert isinstance(mapped, MappedFeatureSet)
        assert mapped.feature_vector() == [None] * len(FEATURE_SCHEMA)
        assert mapped.unknown_fields == {}
