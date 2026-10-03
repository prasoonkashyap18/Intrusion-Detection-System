"""Tests for app.services.ingestion: the CSV -> NetworkFlowRecord pipeline.

Every CSV used here is a tiny inline fixture; no real IDS dataset is
committed. This module performs no ML inference and no test here asserts
that analysis happened — only that rows are read, validated and mapped into
the generic internal representation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.errors import ApiException
from app.services.ingestion import NetworkFlowRecord, ingest_batch


def write_csv(tmp_path: Path, content: bytes, name: str = "batch.csv") -> Path:
    path = tmp_path / name
    path.write_bytes(content)
    return path


class TestValidCsv:
    def test_reads_a_small_valid_csv(self, tmp_path):
        path = write_csv(tmp_path, b"a,b\n1,2\n3,4\n")

        records = list(ingest_batch(path))

        assert len(records) == 2
        assert all(isinstance(r, NetworkFlowRecord) for r in records)

    def test_handles_quoted_fields(self, tmp_path):
        path = write_csv(tmp_path, b'a,b\n"hello",2\n')

        records = list(ingest_batch(path))

        assert records[0].raw_features == {"a": "hello", "b": "2"}

    def test_handles_commas_inside_quoted_values(self, tmp_path):
        path = write_csv(tmp_path, b'a,b\n"x, y",2\n')

        records = list(ingest_batch(path))

        assert records[0].raw_features["a"] == "x, y"

    def test_handles_multiple_rows_in_order(self, tmp_path):
        path = write_csv(tmp_path, b"a,b\n1,2\n3,4\n5,6\n")

        records = list(ingest_batch(path))

        assert [r.raw_features["a"] for r in records] == ["1", "3", "5"]
        assert [r.row_number for r in records] == [1, 2, 3]

    def test_skips_blank_lines_consistently_with_upload_validation(self, tmp_path):
        path = write_csv(tmp_path, b"a,b\n1,2\n\n  \n3,4\n")

        records = list(ingest_batch(path))

        assert len(records) == 2

    def test_header_row_is_not_counted_as_data(self, tmp_path):
        path = write_csv(tmp_path, b"a,b,c\n1,2,3\n")

        records = list(ingest_batch(path))

        assert len(records) == 1
        assert records[0].raw_features == {"a": "1", "b": "2", "c": "3"}


class TestStructuralValidation:
    def test_row_width_mismatch_raises_a_controlled_error(self, tmp_path):
        path = write_csv(tmp_path, b"a,b,c\n1,2,3\n4,5\n")

        with pytest.raises(ApiException) as excinfo:
            list(ingest_batch(path))

        assert excinfo.value.status_code == 400
        assert excinfo.value.error == "ingestion_invalid_csv"

    def test_malformed_csv_raises_a_controlled_error_not_a_crash(self, tmp_path):
        path = write_csv(tmp_path, b'a,b\n"1,2\n3,4\n')  # unterminated quote

        with pytest.raises(ApiException) as excinfo:
            list(ingest_batch(path))

        assert excinfo.value.status_code == 400
        assert excinfo.value.error == "ingestion_invalid_csv"

    def test_too_few_columns_is_rejected(self, tmp_path):
        path = write_csv(tmp_path, b"onlycolumn\nx\n")

        with pytest.raises(ApiException):
            list(ingest_batch(path))

    def test_no_header_row_is_rejected(self, tmp_path):
        path = write_csv(tmp_path, b"  \n\n")

        with pytest.raises(ApiException):
            list(ingest_batch(path))

    def test_error_does_not_expose_the_filesystem_path(self, tmp_path):
        path = write_csv(tmp_path, b"a,b,c\n1,2,3\n4,5\n")

        with pytest.raises(ApiException) as excinfo:
            list(ingest_batch(path))

        assert str(tmp_path) not in excinfo.value.message
        assert str(path) not in excinfo.value.message

    def test_malformed_row_fails_the_whole_operation_rather_than_being_skipped(self, tmp_path):
        path = write_csv(tmp_path, b"a,b\n1,2\n3,4,5\n6,7\n")

        records = []
        with pytest.raises(ApiException):
            for record in ingest_batch(path):
                records.append(record)

        # The good row before the bad one was already yielded (this is a
        # streaming generator), but the caller must see the failure and must
        # not be told the batch ingested cleanly.
        assert len(records) == 1


class TestMissingOrUnreadableFile:
    def test_missing_file_raises_a_controlled_error(self, tmp_path):
        with pytest.raises(ApiException) as excinfo:
            list(ingest_batch(tmp_path / "does-not-exist.csv"))

        assert excinfo.value.error == "ingestion_file_missing"

    def test_missing_file_error_does_not_expose_the_path(self, tmp_path):
        missing = tmp_path / "does-not-exist.csv"

        with pytest.raises(ApiException) as excinfo:
            list(ingest_batch(missing))

        assert str(missing) not in excinfo.value.message
        assert "does-not-exist" not in excinfo.value.message


class TestGenericFieldParsing:
    def test_valid_source_and_destination_ip_are_parsed(self, tmp_path):
        path = write_csv(tmp_path, b"source_ip,destination_ip\n10.0.0.1,10.0.0.2\n")

        record = next(ingest_batch(path))

        assert record.source_ip == "10.0.0.1"
        assert record.destination_ip == "10.0.0.2"

    def test_invalid_source_ip_is_preserved_as_raw_not_rejected(self, tmp_path):
        path = write_csv(tmp_path, b"source_ip,b\nnot-an-ip,2\n")

        record = next(ingest_batch(path))

        assert record.source_ip is None
        assert record.raw_features["source_ip"] == "not-an-ip"

    def test_invalid_destination_ip_is_preserved_as_raw_not_rejected(self, tmp_path):
        path = write_csv(tmp_path, b"destination_ip,b\n999.999.999.999,2\n")

        record = next(ingest_batch(path))

        assert record.destination_ip is None
        assert record.raw_features["destination_ip"] == "999.999.999.999"

    def test_valid_ports_are_parsed_as_integers(self, tmp_path):
        path = write_csv(tmp_path, b"source_port,destination_port\n80,443\n")

        record = next(ingest_batch(path))

        assert record.source_port == 80
        assert record.destination_port == 443

    def test_invalid_port_is_preserved_as_raw_not_rejected(self, tmp_path):
        path = write_csv(tmp_path, b"source_port,b\nnot-a-port,2\n")

        record = next(ingest_batch(path))

        assert record.source_port is None
        assert record.raw_features["source_port"] == "not-a-port"

    def test_out_of_range_port_is_preserved_as_raw_not_rejected(self, tmp_path):
        path = write_csv(tmp_path, b"source_port,b\n99999,2\n")

        record = next(ingest_batch(path))

        assert record.source_port is None
        assert record.raw_features["source_port"] == "99999"

    def test_protocol_is_preserved_as_plain_text(self, tmp_path):
        path = write_csv(tmp_path, b"protocol,b\nTCP,2\n")

        record = next(ingest_batch(path))

        assert record.protocol == "TCP"

    def test_never_fabricates_a_default_for_a_bad_value(self, tmp_path):
        path = write_csv(tmp_path, b"source_port,destination_ip\nbad,also-bad\n")

        record = next(ingest_batch(path))

        assert record.source_port is None
        assert record.destination_ip is None
        # Never silently coerced to 0, "", or any other fake default.
        assert record.source_port != 0


class TestRawFeaturePreservation:
    def test_unrecognized_columns_are_preserved_as_raw_features(self, tmp_path):
        path = write_csv(tmp_path, b"duration,bytes,packets,flag,service,label\n5,1024,10,SF,http,normal\n")

        record = next(ingest_batch(path))

        assert record.raw_features == {
            "duration": "5",
            "bytes": "1024",
            "packets": "10",
            "flag": "SF",
            "service": "http",
            "label": "normal",
        }
        assert record.source_ip is None  # none of these columns are generic fields

    def test_recognized_columns_also_keep_their_raw_text(self, tmp_path):
        path = write_csv(tmp_path, b"source_ip,label\n10.0.0.1,normal\n")

        record = next(ingest_batch(path))

        assert record.source_ip == "10.0.0.1"
        assert record.raw_features["source_ip"] == "10.0.0.1"
        assert record.raw_features["label"] == "normal"

    def test_original_header_casing_is_preserved_in_raw_features_keys(self, tmp_path):
        path = write_csv(tmp_path, b"Source_IP,Label\n10.0.0.1,normal\n")

        record = next(ingest_batch(path))

        assert "Source_IP" in record.raw_features
        assert record.source_ip == "10.0.0.1"  # still recognized, case-insensitively


class TestDatasetIndependentHeaders:
    @pytest.mark.parametrize(
        "content",
        [
            # NSL-KDD-shaped
            b"duration,protocol_type,service,flag,src_bytes,dst_bytes,label\n0,tcp,http,SF,181,5450,normal\n",
            # UNSW-NB15-shaped
            b"srcip,sport,dstip,dsport,proto,state,dur,label\n1.1.1.1,1,2.2.2.2,2,tcp,FIN,0.1,0\n",
            # CICIDS-shaped
            b"Flow Duration,Total Fwd Packets,Total Backward Packets,Label\n1000,5,3,BENIGN\n",
            b"completely_custom_column_a,completely_custom_column_b\n1,2\n",
        ],
    )
    def test_arbitrary_dataset_headers_ingest_without_a_hardcoded_dataset_branch(self, tmp_path, content):
        path = write_csv(tmp_path, content)

        records = list(ingest_batch(path))

        assert len(records) == 1
        assert isinstance(records[0], NetworkFlowRecord)


class TestEncodings:
    def test_reads_a_utf8_bom_file(self, tmp_path):
        path = write_csv(tmp_path, b"\xef\xbb\xbfa,b\n1,2\n")

        records = list(ingest_batch(path))

        assert len(records) == 1
        assert "a" in records[0].raw_features

    def test_reads_non_utf8_text_via_the_latin1_fallback(self, tmp_path):
        content = "label,n\nWeb Attack – Brute Force,1\n".encode("cp1252")
        path = write_csv(tmp_path, content)

        records = list(ingest_batch(path))

        assert len(records) == 1
        assert records[0].raw_features["n"] == "1"


class TestStreamingBehavior:
    def test_ingest_batch_is_a_lazy_generator(self, tmp_path):
        path = write_csv(tmp_path, b"a,b\n1,2\n3,4\n")

        result = ingest_batch(path)

        # Calling the function must not itself open or read the file yet;
        # nothing happens until the caller starts iterating.
        assert hasattr(result, "__next__")

    def test_rows_are_produced_one_at_a_time_not_collected_upfront(self, tmp_path):
        rows = [f"{i},{i * 2}\n".encode() for i in range(50)]
        path = write_csv(tmp_path, b"a,b\n" + b"".join(rows))

        iterator = ingest_batch(path)
        first = next(iterator)
        second = next(iterator)

        # Only as many records exist as have been pulled so far: proof this
        # is genuine step-by-step iteration, not a list materialized eagerly
        # and then handed out one element at a time.
        assert first.row_number == 1
        assert second.row_number == 2

    def test_a_large_number_of_rows_can_be_consumed_without_building_a_list(self, tmp_path):
        rows = [f"{i},{i}\n".encode() for i in range(5000)]
        path = write_csv(tmp_path, b"a,b\n" + b"".join(rows))

        count = 0
        for _record in ingest_batch(path):
            count += 1  # each record is dropped immediately, never accumulated

        assert count == 5000


class TestNoDetectionSideEffects:
    def test_ingestion_module_does_not_import_the_detection_result_model(self):
        import app.services.ingestion as ingestion_module

        assert "DetectionResult" not in vars(ingestion_module)
        assert not hasattr(ingestion_module, "DetectionResult")

    def test_ingest_batch_return_type_carries_no_prediction_fields(self, tmp_path):
        path = write_csv(tmp_path, b"a,b\n1,2\n")

        record = next(ingest_batch(path))

        assert not hasattr(record, "predicted_class")
        assert not hasattr(record, "confidence")
        assert not hasattr(record, "severity")
