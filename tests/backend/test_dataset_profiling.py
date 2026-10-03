"""Tests for the Step 19 dataset-profiling layer (app.services.dataset_profiling).

No test here trains a model, classifies traffic, computes risk, or
normalizes a label's meaning — this layer only counts and classifies what
is actually present in a CSV.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.errors import ApiException
from app.services.dataset_profiling import profile_dataset
from app.services.dataset_profiling.bounded_counter import BoundedValueCounter
from app.services.dataset_profiling.profile import FeatureAvailability, InferredType, SchemaDetectionStatus
from app.services.dataset_profiling.profiler import MAX_TRACKED_LABEL_VALUES, MAX_TRACKED_UNIQUE_VALUES
from app.services.dataset_profiling.type_inference import ColumnTypeTracker, classify_value
from app.services.feature_extraction import CANONICAL_COLUMN_NAMES

NSL_KDD_CSV = (
    b"duration,protocol_type,service,flag,src_bytes,dst_bytes,class\n"
    b"5,tcp,http,SF,100,200,normal\n"
    b"3,udp,dns,SF,50,150,neptune\n"
    b"0,tcp,http,SF,0,0,normal\n"
)


def write_csv(tmp_path: Path, content: bytes, name: str = "batch.csv") -> Path:
    path = tmp_path / name
    path.write_bytes(content)
    return path


def column(profile, name: str):
    for col in profile.columns:
        if col.name == name:
            return col
    raise AssertionError(f"no column named {name!r} in profile")


class TestEmptyDataset:
    def test_header_only_csv_profiles_as_zero_rows(self, tmp_path):
        path = write_csv(tmp_path, b"a,b,c\n")

        profile = profile_dataset(path)

        assert profile.rows.total_rows == 0
        assert profile.rows.is_exact is True

    def test_empty_dataset_produces_an_explicit_warning(self, tmp_path):
        path = write_csv(tmp_path, b"a,b,c\n")

        profile = profile_dataset(path)

        assert any(w.code == "empty_dataset" for w in profile.warnings)

    def test_empty_dataset_columns_are_all_empty_type_with_zero_counts(self, tmp_path):
        path = write_csv(tmp_path, b"a,b\n")

        profile = profile_dataset(path)

        for col in profile.columns:
            assert col.inferred_type == InferredType.EMPTY
            assert col.non_null_count == 0
            assert col.missing_count == 0


class TestSingleRowDataset:
    def test_a_single_row_profiles_correctly(self, tmp_path):
        path = write_csv(tmp_path, b"a,b\n1,2\n")

        profile = profile_dataset(path)

        assert profile.rows.total_rows == 1
        assert column(profile, "a").non_null_count == 1
        assert column(profile, "a").missing_count == 0


class TestMultipleRowDataset:
    def test_multiple_rows_are_all_counted(self, tmp_path):
        path = write_csv(tmp_path, NSL_KDD_CSV)

        profile = profile_dataset(path)

        assert profile.rows.total_rows == 3
        assert profile.rows.rows_inspected == 3


class TestColumnCounting:
    def test_total_columns_matches_the_header(self, tmp_path):
        path = write_csv(tmp_path, NSL_KDD_CSV)

        profile = profile_dataset(path)

        assert profile.total_columns == 7
        assert len(profile.columns) == 7

    def test_recognized_and_unknown_counts_are_consistent(self, tmp_path):
        path = write_csv(tmp_path, NSL_KDD_CSV)

        profile = profile_dataset(path)

        recognized = sum(1 for c in profile.columns if c.is_recognized)
        label_or_category = sum(1 for c in profile.columns if c.is_label or c.is_attack_category)
        unknown = sum(1 for c in profile.columns if not c.is_recognized and not c.is_label and not c.is_attack_category)

        assert profile.recognized_column_count == recognized
        assert profile.unknown_column_count == unknown
        assert recognized + label_or_category + unknown == profile.total_columns


class TestExactRowCounting:
    def test_row_count_is_always_reported_as_exact(self, tmp_path):
        path = write_csv(tmp_path, NSL_KDD_CSV)

        profile = profile_dataset(path)

        assert profile.rows.is_exact is True
        assert profile.rows.total_rows == profile.rows.rows_inspected


class TestMissingValues:
    def test_empty_cell_counts_as_missing(self, tmp_path):
        path = write_csv(tmp_path, b"a,b\n1,\n2,x\n")

        profile = profile_dataset(path)

        b_col = column(profile, "b")
        assert b_col.missing_count == 1
        assert b_col.non_null_count == 1

    def test_whitespace_only_cell_counts_as_missing(self, tmp_path):
        path = write_csv(tmp_path, b'a,b\n1,"   "\n')

        profile = profile_dataset(path)

        assert column(profile, "b").missing_count == 1

    def test_literal_text_values_are_not_treated_as_missing(self, tmp_path):
        # Project-wide convention: only empty/whitespace is "missing" —
        # never a literal string like "0", "N/A", "None", "unknown".
        path = write_csv(tmp_path, b"a,b\n0,1\nN/A,1\nNone,1\nunknown,1\n")

        profile = profile_dataset(path)

        col = column(profile, "a")
        assert col.missing_count == 0
        assert col.non_null_count == 4


class TestMissingPercentages:
    def test_missing_percentage_is_computed_from_actual_counts(self, tmp_path):
        path = write_csv(tmp_path, b"a,b\n1,1\n,1\n,1\n,1\n")  # 1 present, 3 missing

        profile = profile_dataset(path)

        col = column(profile, "a")
        assert col.missing_count == 3
        assert col.non_null_count == 1
        assert col.missing_percentage == pytest.approx(75.0)

    def test_zero_missing_is_zero_percent(self, tmp_path):
        path = write_csv(tmp_path, b"a,b\n1,1\n2,1\n")

        profile = profile_dataset(path)

        assert column(profile, "a").missing_percentage == 0.0


class TestZeroRowDivisionSafety:
    def test_missing_percentage_is_none_not_a_crash_for_zero_rows(self, tmp_path):
        path = write_csv(tmp_path, b"a,b\n")

        profile = profile_dataset(path)

        for col in profile.columns:
            assert col.missing_percentage is None


class TestTypeInferenceIntegers:
    def test_plain_integers_are_classified_integer(self, tmp_path):
        path = write_csv(tmp_path, b"a,b\n1,1\n2,1\n3,1\n")

        assert column(profile_dataset(path), "a").inferred_type == InferredType.INTEGER

    def test_leading_zeros_are_still_classified_integer_without_altering_text(self, tmp_path):
        assert classify_value("00123") == InferredType.INTEGER

    def test_negative_integers_are_classified_integer(self, tmp_path):
        assert classify_value("-42") == InferredType.INTEGER


class TestTypeInferenceFloats:
    def test_decimal_values_are_classified_float(self):
        assert classify_value("3.14") == InferredType.FLOAT

    def test_scientific_notation_is_classified_float(self):
        assert classify_value("1e10") == InferredType.FLOAT

    def test_inf_and_nan_text_are_not_classified_float(self):
        assert classify_value("inf") != InferredType.FLOAT
        assert classify_value("nan") != InferredType.FLOAT
        assert classify_value("-infinity") != InferredType.FLOAT


class TestTypeInferenceText:
    def test_ordinary_words_are_classified_text(self):
        assert classify_value("http") == InferredType.TEXT

    def test_mixed_alphanumeric_is_classified_text(self):
        assert classify_value("abc123") == InferredType.TEXT


class TestTypeInferenceIp:
    def test_ipv4_is_classified_ip_address(self):
        assert classify_value("192.168.1.10") == InferredType.IP_ADDRESS

    def test_ipv6_is_classified_ip_address(self):
        assert classify_value("::1") == InferredType.IP_ADDRESS

    def test_a_bare_integer_is_not_misclassified_as_an_ip(self):
        # ipaddress.ip_address() requires dotted/colon notation; a bare
        # number is never a valid textual IP form.
        assert classify_value("192") == InferredType.INTEGER

    def test_ip_text_is_not_destroyed_only_classified(self, tmp_path):
        path = write_csv(tmp_path, b"ip,b\n192.168.1.10,1\n")

        profile = profile_dataset(path)

        assert profile.label_summary.available is False  # sanity: no label here
        assert column(profile, "ip").inferred_type == InferredType.IP_ADDRESS


class TestTypeInferenceTimestamp:
    def test_iso_datetime_is_classified_timestamp(self):
        assert classify_value("2026-10-03T14:45:00") == InferredType.TIMESTAMP

    def test_iso_date_only_is_classified_timestamp(self):
        assert classify_value("2026-10-03") == InferredType.TIMESTAMP

    def test_non_iso_date_text_is_not_classified_timestamp(self):
        assert classify_value("10/03/2026") != InferredType.TIMESTAMP


class TestMixedTypeDetection:
    def test_a_column_with_both_numbers_and_text_is_mixed(self, tmp_path):
        path = write_csv(tmp_path, b"a,b\n1,1\ntext,1\n")

        profile = profile_dataset(path)

        assert column(profile, "a").inferred_type == InferredType.MIXED

    def test_mixed_type_produces_a_warning(self, tmp_path):
        path = write_csv(tmp_path, b"a,b\n1,1\ntext,1\n")

        profile = profile_dataset(path)

        assert any(w.code == "mixed_type_column" for w in profile.warnings)

    def test_a_single_consistent_type_is_not_flagged_mixed(self, tmp_path):
        path = write_csv(tmp_path, b"a,b\n1,1\n2,1\n3,1\n")

        profile = profile_dataset(path)

        assert column(profile, "a").inferred_type != InferredType.MIXED
        assert not any(w.code == "mixed_type_column" for w in profile.warnings)

    def test_tracker_reports_empty_with_no_observations(self):
        tracker = ColumnTypeTracker()
        assert tracker.inferred_type == InferredType.EMPTY

    def test_tracker_ignores_empty_values_when_determining_type(self):
        tracker = ColumnTypeTracker()
        tracker.observe("")
        tracker.observe("   ")
        tracker.observe("1")
        assert tracker.inferred_type == InferredType.INTEGER


class TestUniqueValueCounting:
    def test_unique_count_is_exact_for_a_small_column(self, tmp_path):
        path = write_csv(tmp_path, b"a,b\n1,1\n1,1\n2,1\n3,1\n3,1\n3,1\n")

        col = column(profile_dataset(path), "a")

        assert col.unique_count == 3
        assert col.unique_count_is_exact is True

    def test_bounded_counter_stops_tracking_new_keys_past_its_limit(self):
        counter = BoundedValueCounter(limit=3)
        for value in ["a", "b", "c", "d", "e"]:
            counter.add(value)

        assert counter.truncated is True
        assert counter.distinct_count == 3

    def test_bounded_counter_keeps_exact_counts_for_already_tracked_keys(self):
        counter = BoundedValueCounter(limit=2)
        counter.add("x")
        counter.add("x")
        counter.add("y")
        counter.add("z")  # triggers truncation
        counter.add("x")  # x was already tracked; still counts

        assert counter.counts["x"] == 3
        assert counter.truncated is True

    def test_a_column_exceeding_the_tracked_limit_is_marked_inexact(self, tmp_path):
        rows = "\n".join(f"{i},1" for i in range(MAX_TRACKED_UNIQUE_VALUES + 50))
        path = write_csv(tmp_path, f"a,b\n{rows}\n".encode())

        col = column(profile_dataset(path), "a")

        assert col.unique_count_is_exact is False
        assert col.unique_count == MAX_TRACKED_UNIQUE_VALUES

    def test_bounded_counter_rejects_a_non_positive_limit(self):
        with pytest.raises(ValueError):
            BoundedValueCounter(limit=0)


class TestLabelDetection:
    def test_recognized_schema_with_a_label_column_reports_it_available(self, tmp_path):
        path = write_csv(tmp_path, NSL_KDD_CSV)

        profile = profile_dataset(path)

        assert profile.label_summary.available is True
        assert profile.label_summary.column_name == "class"

    def test_no_label_column_reports_unavailable_not_invented(self, tmp_path):
        path = write_csv(tmp_path, b"foo,bar\n1,2\n")  # unsupported schema

        profile = profile_dataset(path)

        assert profile.label_summary.available is False
        assert profile.label_summary.column_name is None
        assert profile.label_summary.value_counts == {}

    def test_label_column_is_flagged_on_its_own_column_profile(self, tmp_path):
        path = write_csv(tmp_path, NSL_KDD_CSV)

        profile = profile_dataset(path)

        assert column(profile, "class").is_label is True
        assert column(profile, "duration").is_label is False


class TestLabelValueCounts:
    def test_actual_label_values_and_counts_are_reported(self, tmp_path):
        path = write_csv(tmp_path, NSL_KDD_CSV)

        profile = profile_dataset(path)

        assert profile.label_summary.value_counts == {"normal": 2, "neptune": 1}

    def test_label_text_is_preserved_verbatim_not_normalized(self, tmp_path):
        path = write_csv(
            tmp_path,
            b"Flow Duration,Total Fwd Packets,Total Backward Packets,Label\n"
            b"100,1,1,BENIGN\n100,1,1,Web Attack - Brute Force\n",
        )

        profile = profile_dataset(path)

        assert "BENIGN" in profile.label_summary.value_counts
        assert "Web Attack - Brute Force" in profile.label_summary.value_counts
        # Never collapsed to "Benign"/"Attack".
        assert "Benign" not in profile.label_summary.value_counts
        assert "Attack" not in profile.label_summary.value_counts

    def test_label_counts_past_the_tracking_limit_are_marked_truncated(self, tmp_path):
        rows = "\n".join(f"{i},tcp,http,SF,1,1,label{i}" for i in range(MAX_TRACKED_LABEL_VALUES + 10))
        path = write_csv(tmp_path, f"duration,protocol_type,service,flag,src_bytes,dst_bytes,class\n{rows}\n".encode())

        profile = profile_dataset(path)

        assert profile.label_summary.counts_truncated is True
        assert profile.label_summary.distinct_count == MAX_TRACKED_LABEL_VALUES


class TestAttackCategoryDetection:
    def test_unsw_style_schema_reports_attack_category_available(self, tmp_path):
        path = write_csv(
            tmp_path,
            b"srcip,sport,dstip,dsport,proto,label,attack_cat\n"
            b"1.1.1.1,1,2.2.2.2,2,tcp,1,Exploits\n",
        )

        profile = profile_dataset(path)

        assert profile.attack_category_summary.available is True
        assert profile.attack_category_summary.column_name == "attack_cat"

    def test_schema_without_a_category_column_reports_unavailable(self, tmp_path):
        path = write_csv(tmp_path, NSL_KDD_CSV)  # NSL-KDD has no attack_cat equivalent

        profile = profile_dataset(path)

        assert profile.attack_category_summary.available is False
        assert profile.attack_category_summary.value_counts == {}

    def test_attack_category_is_never_inferred_from_the_label(self, tmp_path):
        # NSL-KDD's label ("neptune") strongly implies a DoS category, but
        # nothing in this layer may synthesize attack_category from it.
        path = write_csv(tmp_path, NSL_KDD_CSV)

        profile = profile_dataset(path)

        assert profile.attack_category_summary.available is False


class TestAttackCategoryValueCounts:
    def test_actual_attack_category_values_are_reported(self, tmp_path):
        path = write_csv(
            tmp_path,
            b"srcip,sport,dstip,dsport,proto,label,attack_cat\n"
            b"1.1.1.1,1,2.2.2.2,2,tcp,1,Exploits\n"
            b"1.1.1.1,1,2.2.2.2,2,tcp,1,Exploits\n"
            b"1.1.1.1,1,2.2.2.2,2,tcp,0,\n",  # benign row: empty category
        )

        profile = profile_dataset(path)

        assert profile.attack_category_summary.value_counts == {"Exploits": 2}


class TestUnknownColumns:
    def test_columns_with_no_canonical_equivalent_are_marked_unrecognized(self, tmp_path):
        path = write_csv(tmp_path, NSL_KDD_CSV)

        profile = profile_dataset(path)

        assert column(profile, "service").is_recognized is False
        assert column(profile, "flag").is_recognized is False

    def test_unknown_columns_still_get_a_full_profile_not_dropped(self, tmp_path):
        path = write_csv(tmp_path, NSL_KDD_CSV)

        profile = profile_dataset(path)

        service_col = column(profile, "service")
        assert service_col.non_null_count == 3
        assert service_col.canonical_name is None

    def test_an_entirely_unsupported_schema_leaves_every_column_unrecognized(self, tmp_path):
        path = write_csv(tmp_path, b"weird_a,weird_b\n1,2\n")

        profile = profile_dataset(path)

        assert all(not c.is_recognized for c in profile.columns)
        assert profile.recognized_column_count == 0


class TestCanonicalFeatureAvailability:
    def test_every_canonical_feature_name_is_represented(self, tmp_path):
        path = write_csv(tmp_path, NSL_KDD_CSV)

        profile = profile_dataset(path)

        assert set(profile.canonical_feature_availability) == CANONICAL_COLUMN_NAMES

    def test_mapped_features_are_available(self, tmp_path):
        path = write_csv(tmp_path, NSL_KDD_CSV)

        profile = profile_dataset(path)

        assert profile.canonical_feature_availability["flow_duration"] == FeatureAvailability.AVAILABLE
        assert profile.canonical_feature_availability["protocol"] == FeatureAvailability.AVAILABLE
        assert profile.canonical_feature_availability["forward_byte_count"] == FeatureAvailability.AVAILABLE

    def test_unmapped_features_are_missing_not_assumed_available(self, tmp_path):
        path = write_csv(tmp_path, NSL_KDD_CSV)  # no IP/port columns at all

        profile = profile_dataset(path)

        assert profile.canonical_feature_availability["source_ip"] == FeatureAvailability.MISSING
        assert profile.canonical_feature_availability["source_port"] == FeatureAvailability.MISSING

    def test_an_unsupported_schema_reports_every_feature_missing(self, tmp_path):
        path = write_csv(tmp_path, b"weird_a,weird_b\n1,2\n")

        profile = profile_dataset(path)

        assert all(v == FeatureAvailability.MISSING for v in profile.canonical_feature_availability.values())

    def test_availability_does_not_depend_on_having_any_data_rows(self, tmp_path):
        path = write_csv(
            tmp_path, b"duration,protocol_type,service,flag,src_bytes,dst_bytes,class\n"
        )  # header only

        profile = profile_dataset(path)

        assert profile.canonical_feature_availability["flow_duration"] == FeatureAvailability.AVAILABLE


class TestUnsupportedSchema:
    def test_unrecognized_header_reports_unsupported_status(self, tmp_path):
        path = write_csv(tmp_path, b"foo,bar,baz\n1,2,3\n")

        profile = profile_dataset(path)

        assert profile.identity.status == SchemaDetectionStatus.UNSUPPORTED
        assert profile.identity.schema_id is None
        assert profile.identity.candidate_schema_ids == ()

    def test_unsupported_schema_produces_a_warning(self, tmp_path):
        path = write_csv(tmp_path, b"foo,bar,baz\n1,2,3\n")

        profile = profile_dataset(path)

        assert any(w.code == "unsupported_schema" for w in profile.warnings)


class TestAmbiguousSchema:
    def test_an_ambiguous_header_reports_ambiguous_status(self, tmp_path, monkeypatch):
        import app.services.dataset_profiling.profiler as profiler_module

        class AlwaysMatches:
            schema_id = "fake-a"
            description = "test double"

            def can_handle(self, header):
                return True

            def adapt(self, record):
                raise NotImplementedError

        class AlsoAlwaysMatches:
            schema_id = "fake-b"
            description = "test double"

            def can_handle(self, header):
                return True

            def adapt(self, record):
                raise NotImplementedError

        from app.services.dataset_adapters.registry import AdapterSelection

        def fake_select_adapter(header):
            return AdapterSelection(adapter=None, candidates=("fake-a", "fake-b"))

        monkeypatch.setattr(profiler_module, "select_adapter", fake_select_adapter)
        path = write_csv(tmp_path, b"foo,bar\n1,2\n")

        profile = profile_dataset(path)

        assert profile.identity.status == SchemaDetectionStatus.AMBIGUOUS
        assert set(profile.identity.candidate_schema_ids) == {"fake-a", "fake-b"}
        assert any(w.code == "ambiguous_schema" for w in profile.warnings)


class TestDuplicateColumns:
    def test_duplicate_header_names_are_detected(self, tmp_path):
        path = write_csv(tmp_path, b"a,b,a\n1,2,3\n")

        profile = profile_dataset(path)

        assert profile.duplicate_column_names == ("a",)

    def test_duplicate_columns_produce_a_warning(self, tmp_path):
        path = write_csv(tmp_path, b"a,b,a\n1,2,3\n")

        profile = profile_dataset(path)

        assert any(w.code == "duplicate_columns" for w in profile.warnings)

    def test_total_columns_counts_the_duplicate_but_profile_lists_it_once(self, tmp_path):
        path = write_csv(tmp_path, b"a,b,a\n1,2,3\n")

        profile = profile_dataset(path)

        assert profile.total_columns == 3  # header had 3 entries
        assert len(profile.columns) == 2  # "a" collapses to one entry

    def test_no_duplicates_reports_an_empty_tuple(self, tmp_path):
        path = write_csv(tmp_path, b"a,b,c\n1,2,3\n")

        profile = profile_dataset(path)

        assert profile.duplicate_column_names == ()
        assert not any(w.code == "duplicate_columns" for w in profile.warnings)


class TestMalformedCsv:
    def test_unterminated_quote_raises_a_controlled_error(self, tmp_path):
        path = write_csv(tmp_path, b'a,b\n"1,2\n3,4\n')

        with pytest.raises(ApiException) as excinfo:
            profile_dataset(path)

        assert excinfo.value.status_code == 400

    def test_missing_file_raises_a_controlled_error(self, tmp_path):
        with pytest.raises(ApiException) as excinfo:
            profile_dataset(tmp_path / "does-not-exist.csv")

        assert excinfo.value.error == "ingestion_file_missing"

    def test_no_header_row_raises_a_controlled_error(self, tmp_path):
        path = write_csv(tmp_path, b"  \n\n")

        with pytest.raises(ApiException):
            profile_dataset(path)

    def test_malformed_csv_error_does_not_leak_the_filesystem_path(self, tmp_path):
        path = write_csv(tmp_path, b'a,b\n"1,2\n3,4\n')

        with pytest.raises(ApiException) as excinfo:
            profile_dataset(path)

        assert str(tmp_path) not in excinfo.value.message


class TestWrongRowWidth:
    def test_row_width_mismatch_raises_a_controlled_error(self, tmp_path):
        path = write_csv(tmp_path, b"a,b,c\n1,2,3\n4,5\n")

        with pytest.raises(ApiException) as excinfo:
            profile_dataset(path)

        assert excinfo.value.error == "ingestion_invalid_csv"


class TestNoFabricatedLabels:
    def test_no_label_column_never_invents_a_label(self, tmp_path):
        path = write_csv(tmp_path, b"x,y\n1,2\n")

        profile = profile_dataset(path)

        assert profile.label_summary.available is False
        assert profile.label_summary.value_counts == {}
        assert profile.label_summary.distinct_count == 0


class TestNoFabricatedDatasetIdentity:
    def test_unrecognized_header_never_claims_a_schema_id(self, tmp_path):
        path = write_csv(tmp_path, b"totally,custom,columns\n1,2,3\n")

        profile = profile_dataset(path)

        assert profile.identity.schema_id is None
        assert profile.identity.schema_description is None


class TestNoFabricatedFeatureAvailability:
    def test_a_feature_with_no_mapped_column_is_never_reported_available(self, tmp_path):
        path = write_csv(tmp_path, b"totally,custom,columns\n1,2,3\n")

        profile = profile_dataset(path)

        assert all(v == FeatureAvailability.MISSING for v in profile.canonical_feature_availability.values())


class TestBoundedMemoryBehavior:
    def test_a_large_number_of_rows_profiles_without_materializing_them(self, tmp_path):
        rows = "\n".join(f"{i},tcp,http,SF,{i},{i},normal" for i in range(5000))
        path = write_csv(tmp_path, f"duration,protocol_type,service,flag,src_bytes,dst_bytes,class\n{rows}\n".encode())

        profile = profile_dataset(path)

        assert profile.rows.total_rows == 5000
        # Uniqueness tracking for a high-cardinality numeric column is
        # bounded, not an unbounded in-memory set.
        assert column(profile, "duration").unique_count <= MAX_TRACKED_UNIQUE_VALUES
        assert column(profile, "duration").unique_count_is_exact is False


class TestAdaptersRemainFunctional:
    """Cross-check: profiling must not have broken Step 18's own adapters."""

    def test_nsl_kdd_adapter_still_recognizes_its_schema(self, tmp_path):
        path = write_csv(tmp_path, NSL_KDD_CSV)

        profile = profile_dataset(path)

        assert profile.identity.schema_id == "nsl-kdd-style"

    def test_unsw_adapter_still_recognizes_its_schema(self, tmp_path):
        path = write_csv(
            tmp_path, b"srcip,sport,dstip,dsport,proto,label,attack_cat\n1.1.1.1,1,2.2.2.2,2,tcp,0,\n"
        )

        profile = profile_dataset(path)

        assert profile.identity.schema_id == "unsw-nb15-style"

    def test_cicids_adapter_still_recognizes_its_schema(self, tmp_path):
        path = write_csv(
            tmp_path,
            b"Flow Duration,Total Fwd Packets,Total Backward Packets,Label\n1000,5,3,BENIGN\n",
        )

        profile = profile_dataset(path)

        assert profile.identity.schema_id == "cicids-style"
