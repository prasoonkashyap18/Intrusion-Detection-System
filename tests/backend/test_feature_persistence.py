"""Tests for the Step 21 feature-persistence layer:
app.models.mapped_feature_record.MappedFeatureRecord and
app.services.feature_persistence.

No test here trains a model, classifies traffic, or computes a risk/
confidence/severity score — this layer only writes and reads back the
deterministic feature representation Steps 16/18/20 already compute.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.detection_batch import DetectionBatch
from app.models.enums import ProcessingStatus
from app.models.mapped_feature_record import MappedFeatureRecord
from app.services.dataset_adapters.nsl_kdd import NslKddStyleAdapter
from app.services.dataset_adapters.unsw_nb15 import UnswNb15StyleAdapter
from app.services.feature_extraction import FEATURE_SCHEMA, FEATURE_SCHEMA_VERSION
from app.services.feature_mapping import map_canonical_record
from app.services.feature_persistence import load_feature_vector, persist_mapped_features
from app.services.ingestion import NetworkFlowRecord


def add_batch(db_session, **overrides) -> DetectionBatch:
    defaults = dict(id=uuid.uuid4(), filename="traffic.csv", status=ProcessingStatus.PENDING, total_records=1)
    batch = DetectionBatch(**{**defaults, **overrides})
    db_session.add(batch)
    db_session.commit()
    return batch


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


def mapped_for(raw_features: dict[str, str], row_number: int = 1, adapter=None):
    adapter = adapter or NslKddStyleAdapter()
    canonical = adapter.adapt(make_record(raw_features, row_number))
    return map_canonical_record(canonical)


class TestModelCreation:
    def test_a_record_can_be_created_and_round_tripped(self, db_session):
        batch = add_batch(db_session)
        mapped = mapped_for({"protocol_type": "tcp", "src_bytes": "100", "class": "normal"})

        persist_mapped_features(db_session, batch.id, mapped)
        db_session.commit()

        stored = db_session.scalars(select(MappedFeatureRecord).where(MappedFeatureRecord.batch_id == batch.id)).one()
        assert stored.id is not None
        assert stored.batch_id == batch.id
        assert stored.row_number == 1
        assert stored.created_at is not None

    def test_the_batch_relationship_is_navigable_both_ways(self, db_session):
        batch = add_batch(db_session)
        mapped = mapped_for({"protocol_type": "tcp"})
        persist_mapped_features(db_session, batch.id, mapped)
        db_session.commit()
        db_session.refresh(batch)

        assert len(batch.mapped_feature_records) == 1
        assert batch.mapped_feature_records[0].batch is batch


class TestCompleteFeatureSetPersistence:
    def test_every_feature_schema_name_is_present_in_the_stored_json(self, db_session):
        batch = add_batch(db_session)
        mapped = mapped_for({"protocol_type": "tcp", "src_bytes": "100", "dst_bytes": "200", "duration": "5"})

        persist_mapped_features(db_session, batch.id, mapped)
        db_session.commit()

        stored = db_session.scalars(select(MappedFeatureRecord)).one()
        assert set(stored.features.keys()) == set(FEATURE_SCHEMA)

    def test_present_values_round_trip_correctly(self, db_session):
        batch = add_batch(db_session)
        mapped = mapped_for({"protocol_type": "tcp", "src_bytes": "100"})

        persist_mapped_features(db_session, batch.id, mapped)
        db_session.commit()

        stored = db_session.scalars(select(MappedFeatureRecord)).one()
        assert stored.features["protocol_number"]["value"] == 6
        assert stored.features["forward_byte_count"]["value"] == 100.0


class TestGenuineZeroValues:
    def test_a_genuine_zero_is_persisted_as_zero_not_confused_with_missing(self, db_session):
        batch = add_batch(db_session)
        mapped = mapped_for({"protocol_type": "tcp", "src_bytes": "0"})

        persist_mapped_features(db_session, batch.id, mapped)
        db_session.commit()

        stored = db_session.scalars(select(MappedFeatureRecord)).one()
        feature = stored.features["forward_byte_count"]
        assert feature["status"] == "present"
        assert feature["value"] == 0.0
        assert feature["value"] is not None


class TestMissingValuesStayNull:
    def test_a_feature_with_no_source_column_is_null_not_zero(self, db_session):
        batch = add_batch(db_session)
        mapped = mapped_for({"protocol_type": "tcp"})  # no duration/src_bytes/dst_bytes

        persist_mapped_features(db_session, batch.id, mapped)
        db_session.commit()

        stored = db_session.scalars(select(MappedFeatureRecord)).one()
        feature = stored.features["flow_duration"]
        assert feature["status"] == "missing"
        assert feature["value"] is None
        assert feature["source_column"] is None
        assert feature["raw_value"] is None


class TestMalformedAndUnsupportedDistinguishable:
    def test_a_malformed_value_is_stored_with_null_value_and_malformed_status(self, db_session):
        batch = add_batch(db_session)
        mapped = mapped_for({"protocol_type": "tcp", "src_bytes": "not-a-number"})

        persist_mapped_features(db_session, batch.id, mapped)
        db_session.commit()

        stored = db_session.scalars(select(MappedFeatureRecord)).one()
        feature = stored.features["forward_byte_count"]
        assert feature["status"] == "malformed"
        assert feature["value"] is None
        assert feature["raw_value"] == "not-a-number"

    def test_an_unsupported_value_is_stored_with_null_value_and_unsupported_status(self, db_session):
        batch = add_batch(db_session)
        mapped = mapped_for({"protocol_type": "QUIC"})

        persist_mapped_features(db_session, batch.id, mapped)
        db_session.commit()

        stored = db_session.scalars(select(MappedFeatureRecord)).one()
        feature = stored.features["protocol_number"]
        assert feature["status"] == "unsupported"
        assert feature["value"] is None
        assert feature["raw_value"] == "QUIC"

    def test_malformed_and_missing_are_distinguishable_from_each_other(self, db_session):
        batch = add_batch(db_session)
        mapped = mapped_for({"protocol_type": "tcp", "src_bytes": "garbage"})  # dst_bytes entirely absent

        persist_mapped_features(db_session, batch.id, mapped)
        db_session.commit()

        stored = db_session.scalars(select(MappedFeatureRecord)).one()
        assert stored.features["forward_byte_count"]["status"] == "malformed"
        assert stored.features["backward_byte_count"]["status"] == "missing"


class TestFeatureOrdering:
    def test_load_feature_vector_follows_feature_schema_order(self, db_session):
        batch = add_batch(db_session)
        mapped = mapped_for({"protocol_type": "tcp", "src_bytes": "1", "dst_bytes": "2", "duration": "5"})

        persist_mapped_features(db_session, batch.id, mapped)
        db_session.commit()

        stored = db_session.scalars(select(MappedFeatureRecord)).one()
        assert load_feature_vector(stored) == mapped.feature_vector()

    def test_ordering_does_not_depend_on_dict_insertion_order(self, db_session):
        batch = add_batch(db_session)
        forward = mapped_for({"protocol_type": "tcp", "src_bytes": "1", "dst_bytes": "2"}, row_number=1)
        backward = mapped_for({"dst_bytes": "2", "src_bytes": "1", "protocol_type": "tcp"}, row_number=2)

        persist_mapped_features(db_session, batch.id, forward)
        persist_mapped_features(db_session, batch.id, backward)
        db_session.commit()

        stored = db_session.scalars(select(MappedFeatureRecord).order_by(MappedFeatureRecord.row_number)).all()
        assert load_feature_vector(stored[0]) == load_feature_vector(stored[1])


class TestProvenancePersistence:
    def test_source_column_and_raw_value_are_persisted_for_present_features(self, db_session):
        batch = add_batch(db_session)
        mapped = mapped_for({"srcip": "10.0.0.1", "proto": "tcp"}, adapter=UnswNb15StyleAdapter())

        persist_mapped_features(db_session, batch.id, mapped)
        db_session.commit()

        stored = db_session.scalars(select(MappedFeatureRecord)).one()
        ip_feature = stored.features["source_ip_numeric"]
        assert ip_feature["source_column"] == "srcip"
        assert ip_feature["raw_value"] == "10.0.0.1"

    def test_unknown_columns_are_persisted_verbatim(self, db_session):
        batch = add_batch(db_session)
        mapped = mapped_for({"protocol_type": "tcp", "service": "http", "flag": "SF"})

        persist_mapped_features(db_session, batch.id, mapped)
        db_session.commit()

        stored = db_session.scalars(select(MappedFeatureRecord)).one()
        assert stored.unknown_fields == {"service": "http", "flag": "SF"}

    def test_unknown_fields_is_not_the_full_original_csv_row(self, db_session):
        # The original row has 3 columns (protocol_type, src_bytes, class);
        # protocol_type is canonical (lives in `features`) and class is the
        # label (never persisted anywhere) — only the genuinely unmapped
        # column should appear in unknown_fields.
        batch = add_batch(db_session)
        mapped = mapped_for({"protocol_type": "tcp", "src_bytes": "1", "class": "normal"})

        persist_mapped_features(db_session, batch.id, mapped)
        db_session.commit()

        stored = db_session.scalars(select(MappedFeatureRecord)).one()
        assert "protocol_type" not in stored.unknown_fields
        assert "class" not in stored.unknown_fields
        assert stored.unknown_fields == {}


class TestBatchIsolation:
    def test_features_from_different_batches_are_never_mixed(self, db_session):
        batch_a = add_batch(db_session)
        batch_b = add_batch(db_session)
        mapped_a = mapped_for({"protocol_type": "tcp"}, row_number=1)
        mapped_b = mapped_for({"protocol_type": "udp"}, row_number=1)

        persist_mapped_features(db_session, batch_a.id, mapped_a)
        persist_mapped_features(db_session, batch_b.id, mapped_b)
        db_session.commit()

        rows_a = db_session.scalars(select(MappedFeatureRecord).where(MappedFeatureRecord.batch_id == batch_a.id)).all()
        rows_b = db_session.scalars(select(MappedFeatureRecord).where(MappedFeatureRecord.batch_id == batch_b.id)).all()
        assert len(rows_a) == 1 and len(rows_b) == 1
        assert rows_a[0].features["protocol_number"]["raw_value"] == "tcp"
        assert rows_b[0].features["protocol_number"]["raw_value"] == "udp"


class TestDuplicateProtection:
    def test_the_same_batch_and_row_number_cannot_be_persisted_twice(self, db_session):
        batch = add_batch(db_session)
        mapped = mapped_for({"protocol_type": "tcp"}, row_number=1)

        persist_mapped_features(db_session, batch.id, mapped)
        db_session.commit()

        persist_mapped_features(db_session, batch.id, mapped)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_different_row_numbers_in_the_same_batch_are_both_allowed(self, db_session):
        batch = add_batch(db_session)
        persist_mapped_features(db_session, batch.id, mapped_for({"protocol_type": "tcp"}, row_number=1))
        persist_mapped_features(db_session, batch.id, mapped_for({"protocol_type": "udp"}, row_number=2))
        db_session.commit()

        rows = db_session.scalars(select(MappedFeatureRecord).where(MappedFeatureRecord.batch_id == batch.id)).all()
        assert len(rows) == 2


class TestSchemaVersionPersistence:
    def test_the_current_feature_schema_version_is_stored(self, db_session):
        batch = add_batch(db_session)
        mapped = mapped_for({"protocol_type": "tcp"})

        persist_mapped_features(db_session, batch.id, mapped)
        db_session.commit()

        stored = db_session.scalars(select(MappedFeatureRecord)).one()
        assert stored.feature_schema_version == FEATURE_SCHEMA_VERSION

    def test_dataset_schema_identifies_the_adapter_that_produced_the_row(self, db_session):
        batch = add_batch(db_session)
        mapped = mapped_for({"srcip": "1.1.1.1", "proto": "tcp"}, adapter=UnswNb15StyleAdapter())

        persist_mapped_features(db_session, batch.id, mapped)
        db_session.commit()

        stored = db_session.scalars(select(MappedFeatureRecord)).one()
        assert stored.dataset_schema == "unsw-nb15-style"


class TestNoLabelLeakageIntoPersistedFeatures:
    def test_label_text_never_appears_anywhere_in_the_persisted_row(self, db_session):
        batch = add_batch(db_session)
        mapped = mapped_for({"protocol_type": "tcp", "class": "neptune"})

        persist_mapped_features(db_session, batch.id, mapped)
        db_session.commit()

        stored = db_session.scalars(select(MappedFeatureRecord)).one()
        raw_values = [f["raw_value"] for f in stored.features.values()]
        assert "neptune" not in raw_values
        assert "neptune" not in stored.unknown_fields.values()

    def test_attack_category_text_never_appears_anywhere_in_the_persisted_row(self, db_session):
        batch = add_batch(db_session)
        mapped = mapped_for(
            {"srcip": "1.1.1.1", "proto": "tcp", "attack_cat": "Exploits"}, adapter=UnswNb15StyleAdapter()
        )

        persist_mapped_features(db_session, batch.id, mapped)
        db_session.commit()

        stored = db_session.scalars(select(MappedFeatureRecord)).one()
        raw_values = [f["raw_value"] for f in stored.features.values()]
        assert "Exploits" not in raw_values
        assert "Exploits" not in stored.unknown_fields.values()
