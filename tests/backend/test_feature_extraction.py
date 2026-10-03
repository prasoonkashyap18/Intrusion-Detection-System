"""Tests for app.services.feature_extraction: NetworkFlowRecord ->
NormalizedFlowFeatures. No ML inference, no predictions, and no
DetectionResult rows are exercised or expected anywhere here — this module
only extracts and normalizes features.
"""

from __future__ import annotations

from app.services.feature_extraction import (
    FEATURE_SCHEMA,
    FeatureStatus,
    extract_features,
    extract_raw_features,
    normalize_features,
)
from app.services.ingestion import NetworkFlowRecord


def make_record(raw_features: dict[str, str], row_number: int = 1) -> NetworkFlowRecord:
    """A NetworkFlowRecord built directly, mirroring what `ingestion.py`
    would hand this layer — the typed fields are deliberately left at their
    ingestion-layer defaults since this module reads `raw_features`, not
    those typed fields."""
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


class TestValidRecord:
    def test_a_fully_populated_record_extracts_every_feature(self):
        record = make_record(
            {
                "source_ip": "10.0.0.1",
                "destination_ip": "10.0.0.2",
                "source_port": "80",
                "destination_port": "443",
                "protocol": "tcp",
                "duration": "5.5",
                "packets": "10",
                "bytes": "1024",
                "packet_rate": "2.0",
                "byte_rate": "200.0",
                "fwd_packets": "6",
                "fwd_bytes": "600",
                "bwd_packets": "4",
                "bwd_bytes": "424",
                "tcp_flags": "24",
            }
        )

        features = extract_features(record)

        assert all(status == FeatureStatus.PRESENT for status in features.feature_status.values())
        assert features.protocol_name == "tcp"
        assert features.source_ip == "10.0.0.1"
        assert features.features["source_port"] == 80.0
        assert features.features["destination_port"] == 443.0
        assert features.features["protocol_number"] == 6
        assert features.features["flow_duration"] == 5.5
        assert features.unknown_features == {}

    def test_output_carries_no_prediction_or_scoring_fields(self):
        record = make_record({"source_ip": "10.0.0.1"})

        features = extract_features(record)

        assert not hasattr(features, "predicted_class")
        assert not hasattr(features, "confidence")
        assert not hasattr(features, "severity")
        assert not hasattr(features, "risk_score")


class TestMissingFields:
    def test_a_record_with_no_recognized_columns_marks_everything_missing(self):
        record = make_record({"label": "normal"})

        features = extract_features(record)

        assert all(status == FeatureStatus.MISSING for status in features.feature_status.values())
        assert all(value is None for value in features.features.values())

    def test_an_empty_value_is_treated_as_missing_not_malformed(self):
        record = make_record({"source_port": "", "duration": "   "})

        features = extract_features(record)

        assert features.feature_status["source_port"] == FeatureStatus.MISSING
        assert features.feature_status["flow_duration"] == FeatureStatus.MISSING

    def test_missing_never_produces_a_fake_zero(self):
        record = make_record({})

        features = extract_features(record)

        assert features.features["byte_count"] is None
        assert features.features["byte_count"] != 0


class TestNumericParsing:
    def test_valid_counts_and_rates_parse_as_floats(self):
        record = make_record({"bytes": "2048", "packet_rate": "3.75"})

        features = extract_features(record)

        assert features.features["byte_count"] == 2048.0
        assert features.features["packet_rate"] == 3.75

    def test_integer_text_parses_cleanly(self):
        record = make_record({"packets": "100"})

        assert extract_features(record).features["packet_count"] == 100.0

    def test_zero_is_a_legitimate_value_not_confused_with_missing(self):
        record = make_record({"bytes": "0"})

        features = extract_features(record)

        assert features.feature_status["byte_count"] == FeatureStatus.PRESENT
        assert features.features["byte_count"] == 0.0


class TestMalformedValues:
    def test_non_numeric_text_in_a_numeric_column_is_malformed_not_fabricated(self):
        record = make_record({"bytes": "not-a-number"})

        features = extract_features(record)

        assert features.feature_status["byte_count"] == FeatureStatus.MALFORMED
        assert features.features["byte_count"] is None

    def test_negative_count_is_malformed(self):
        record = make_record({"packets": "-5"})

        features = extract_features(record)

        assert features.feature_status["packet_count"] == FeatureStatus.MALFORMED
        assert features.features["packet_count"] is None

    def test_negative_duration_is_malformed(self):
        record = make_record({"duration": "-0.1"})

        assert extract_features(record).feature_status["flow_duration"] == FeatureStatus.MALFORMED

    def test_malformed_value_is_never_silently_coerced_to_a_plausible_number(self):
        record = make_record({"bytes": "lots"})

        features = extract_features(record)

        # Must be exactly None, not 0, not -1, not any other stand-in.
        assert features.features["byte_count"] is None


class TestPorts:
    def test_valid_ports_parse_as_floats_in_range(self):
        record = make_record({"source_port": "22", "destination_port": "8080"})

        features = extract_features(record)

        assert features.features["source_port"] == 22.0
        assert features.features["destination_port"] == 8080.0

    def test_non_numeric_port_is_malformed(self):
        record = make_record({"source_port": "abc"})

        features = extract_features(record)

        assert features.feature_status["source_port"] == FeatureStatus.MALFORMED
        assert features.features["source_port"] is None

    def test_out_of_range_port_is_malformed(self):
        record = make_record({"source_port": "70000"})

        features = extract_features(record)

        assert features.feature_status["source_port"] == FeatureStatus.MALFORMED
        assert features.features["source_port"] is None

    def test_negative_port_is_malformed(self):
        record = make_record({"destination_port": "-1"})

        assert extract_features(record).feature_status["destination_port"] == FeatureStatus.MALFORMED

    def test_boundary_ports_0_and_65535_are_valid(self):
        record = make_record({"source_port": "0", "destination_port": "65535"})

        features = extract_features(record)

        assert features.features["source_port"] == 0.0
        assert features.features["destination_port"] == 65535.0

    def test_port_65536_is_just_past_the_boundary_and_malformed(self):
        record = make_record({"source_port": "65536"})

        assert extract_features(record).feature_status["source_port"] == FeatureStatus.MALFORMED


class TestProtocolHandling:
    def test_known_protocol_name_maps_to_its_iana_number(self):
        record = make_record({"protocol": "udp"})

        features = extract_features(record)

        assert features.protocol_name == "udp"
        assert features.features["protocol_number"] == 17
        assert features.feature_status["protocol_number"] == FeatureStatus.PRESENT

    def test_protocol_matching_is_case_insensitive(self):
        record = make_record({"protocol": "TCP"})

        assert extract_features(record).features["protocol_number"] == 6

    def test_known_numeric_protocol_text_is_accepted(self):
        record = make_record({"protocol": "6"})

        features = extract_features(record)

        assert features.features["protocol_number"] == 6
        assert features.protocol_name == "tcp"

    def test_unrecognized_protocol_text_is_unsupported_not_discarded(self):
        record = make_record({"protocol": "QUIC"})

        features = extract_features(record)

        assert features.feature_status["protocol_number"] == FeatureStatus.UNSUPPORTED
        assert features.features["protocol_number"] is None
        assert features.protocol_name == "quic"  # text preserved, just not numbered

    def test_missing_protocol_is_missing_not_unsupported(self):
        record = make_record({"label": "x"})

        features = extract_features(record)

        assert features.feature_status["protocol_number"] == FeatureStatus.MISSING
        assert features.protocol_name is None


class TestIpRepresentation:
    def test_valid_ipv4_gets_a_deterministic_numeric_encoding(self):
        record = make_record({"source_ip": "192.168.1.1"})

        features = extract_features(record)

        assert features.source_ip == "192.168.1.1"
        assert features.features["source_ip_numeric"] == float(int(__import__("ipaddress").ip_address("192.168.1.1")))

    def test_the_numeric_encoding_is_deterministic_across_calls(self):
        record = make_record({"destination_ip": "10.0.0.5"})

        first = extract_features(record).features["destination_ip_numeric"]
        second = extract_features(record).features["destination_ip_numeric"]

        assert first == second

    def test_valid_ipv6_is_also_encoded(self):
        record = make_record({"source_ip": "::1"})

        features = extract_features(record)

        assert features.feature_status["source_ip_numeric"] == FeatureStatus.PRESENT
        assert features.features["source_ip_numeric"] == 1.0

    def test_invalid_ip_text_is_malformed_not_guessed_at(self):
        record = make_record({"source_ip": "999.999.999.999"})

        features = extract_features(record)

        assert features.feature_status["source_ip_numeric"] == FeatureStatus.MALFORMED
        assert features.features["source_ip_numeric"] is None
        assert features.source_ip is None

    def test_no_threat_assessment_fields_exist_on_the_result(self):
        record = make_record({"source_ip": "8.8.8.8"})

        features = extract_features(record)

        assert not hasattr(features, "is_malicious")
        assert not hasattr(features, "threat_level")
        assert not hasattr(features, "reputation")


class TestDeterministicOrdering:
    def test_feature_vector_follows_the_schema_order_exactly(self):
        record = make_record({"source_port": "1", "destination_port": "2"})

        features = extract_features(record)

        assert list(features.features.keys()) == list(FEATURE_SCHEMA)
        assert features.feature_vector() == [features.features[name] for name in FEATURE_SCHEMA]

    def test_feature_vector_does_not_depend_on_raw_features_insertion_order(self):
        forward = make_record({"source_port": "1", "destination_port": "2", "protocol": "tcp"})
        backward = make_record({"protocol": "tcp", "destination_port": "2", "source_port": "1"})

        assert extract_features(forward).feature_vector() == extract_features(backward).feature_vector()

    def test_repeated_extraction_of_the_same_record_is_identical(self):
        record = make_record({"source_ip": "10.0.0.1", "bytes": "100", "protocol": "udp"})

        first = extract_features(record)
        second = extract_features(record)

        assert first.feature_vector() == second.feature_vector()
        assert first.feature_status == second.feature_status
        assert first.unknown_features == second.unknown_features

    def test_schema_has_no_duplicate_names(self):
        assert len(FEATURE_SCHEMA) == len(set(FEATURE_SCHEMA))

    def test_every_schema_name_has_a_status_entry_even_when_empty_record(self):
        record = make_record({})

        features = extract_features(record)

        assert set(features.feature_status.keys()) == set(FEATURE_SCHEMA)
        assert set(features.features.keys()) == set(FEATURE_SCHEMA)


class TestUnknownColumns:
    def test_unrecognized_columns_are_preserved_verbatim(self):
        record = make_record({"flag": "SF", "service": "http", "label": "normal"})

        features = extract_features(record)

        assert features.unknown_features == {"flag": "SF", "service": "http", "label": "normal"}

    def test_a_mix_of_known_and_unknown_columns_keeps_both(self):
        record = make_record({"source_port": "80", "custom_column": "xyz"})

        features = extract_features(record)

        assert features.features["source_port"] == 80.0
        assert features.unknown_features == {"custom_column": "xyz"}

    def test_header_matching_is_case_and_whitespace_insensitive(self):
        record = make_record({"Source Port": "80"})

        features = extract_features(record)

        assert features.features["source_port"] == 80.0
        assert features.unknown_features == {}


class TestTcpFlags:
    def test_numeric_flags_bitmask_is_accepted(self):
        record = make_record({"tcp_flags": "24"})

        features = extract_features(record)

        assert features.features["tcp_flags"] == 24.0
        assert features.feature_status["tcp_flags"] == FeatureStatus.PRESENT

    def test_textual_flags_are_unsupported_not_malformed_or_discarded(self):
        record = make_record({"flags": "SF"})

        features = extract_features(record)

        assert features.feature_status["tcp_flags"] == FeatureStatus.UNSUPPORTED
        assert features.features["tcp_flags"] is None
        assert "flags" not in features.unknown_features  # it was recognized, just not numeric


class TestRawExtractionNormalizationSeparation:
    def test_extract_raw_features_applies_no_scaling(self):
        record = make_record({"bytes": "100"})

        raw = extract_raw_features(record)

        assert raw.values["byte_count"] == 100.0

    def test_normalize_features_without_a_scaler_is_the_identity_transform(self):
        record = make_record({"bytes": "100", "packets": "5"})

        raw = extract_raw_features(record)
        normalized = normalize_features(raw)

        assert normalized.features["byte_count"] == raw.values["byte_count"]
        assert normalized.features["packet_count"] == raw.values["packet_count"]

    def test_a_scaler_is_applied_only_to_present_features(self):
        record = make_record({"bytes": "100"})  # packet_count stays missing
        raw = extract_raw_features(record)

        normalized = normalize_features(raw, scaler=lambda name, value: value / 10)

        assert normalized.features["byte_count"] == 10.0
        assert normalized.features["packet_count"] is None  # scaler never invented a value

    def test_no_statistical_parameters_are_computed_by_this_module(self):
        # There is no mean/std/min/max fitting anywhere in the public API:
        # normalize_features requires nothing but the raw set and an
        # optional pre-built scaler callable.
        import inspect

        from app.services import feature_extraction

        signature = inspect.signature(feature_extraction.normalize_features)
        assert list(signature.parameters) == ["raw", "scaler"]
