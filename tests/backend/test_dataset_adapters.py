"""Tests for the Step 18 dataset-adapter layer (app.services.dataset_adapters).

No test here trains a model, classifies traffic, or asserts a prediction —
this layer only translates a dataset's own column names into the project's
canonical vocabulary, preserving (never fabricating) labels and raw values.
"""

from __future__ import annotations

from pathlib import Path

from app.services.dataset_adapters import ADAPTERS, CanonicalDatasetRecord, select_adapter
from app.services.dataset_adapters import registry as registry_module
from app.services.dataset_adapters._common import header_matches
from app.services.dataset_adapters.cicids import CicidsStyleAdapter
from app.services.dataset_adapters.nsl_kdd import NslKddStyleAdapter
from app.services.dataset_adapters.unsw_nb15 import UnswNb15StyleAdapter
from app.services.ingestion import NetworkFlowRecord

NSL_KDD_HEADER = [
    "duration",
    "protocol_type",
    "service",
    "flag",
    "src_bytes",
    "dst_bytes",
    "count",
    "srv_count",
    "class",
]

UNSW_NB15_HEADER = [
    "srcip",
    "sport",
    "dstip",
    "dsport",
    "proto",
    "state",
    "dur",
    "sbytes",
    "dbytes",
    "spkts",
    "dpkts",
    "attack_cat",
    "label",
]

CICIDS_HEADER = [
    "Flow Duration",
    "Total Fwd Packets",
    "Total Backward Packets",
    "Total Length of Fwd Packets",
    "Total Length of Bwd Packets",
    "Destination Port",
    "Label",
]

UNKNOWN_HEADER = ["timestamp", "weird_column_a", "weird_column_b", "notes"]


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


class TestAdapterInterface:
    def test_every_registered_adapter_exposes_the_contract(self):
        for adapter in ADAPTERS:
            assert isinstance(adapter.schema_id, str) and adapter.schema_id
            assert isinstance(adapter.description, str) and adapter.description
            assert callable(adapter.can_handle)
            assert callable(adapter.adapt)

    def test_schema_ids_are_unique(self):
        ids = [adapter.schema_id for adapter in ADAPTERS]
        assert len(ids) == len(set(ids))

    def test_can_handle_accepts_a_plain_list_of_strings(self):
        # The interface operates on header text, not on a file or a
        # DetectionBatch — proving it needs nothing else to decide.
        assert NslKddStyleAdapter().can_handle(NSL_KDD_HEADER) is True


class TestKnownSchemaDetection:
    def test_nsl_kdd_style_header_is_recognized(self):
        selection = select_adapter(NSL_KDD_HEADER)

        assert selection.adapter is not None
        assert selection.adapter.schema_id == "nsl-kdd-style"
        assert selection.candidates == ("nsl-kdd-style",)

    def test_unsw_nb15_style_header_is_recognized(self):
        selection = select_adapter(UNSW_NB15_HEADER)

        assert selection.adapter is not None
        assert selection.adapter.schema_id == "unsw-nb15-style"

    def test_cicids_style_header_is_recognized(self):
        selection = select_adapter(CICIDS_HEADER)

        assert selection.adapter is not None
        assert selection.adapter.schema_id == "cicids-style"

    def test_detection_is_case_and_whitespace_insensitive(self):
        header = [name.upper().replace("_", " ") for name in NSL_KDD_HEADER]

        assert NslKddStyleAdapter().can_handle(header) is True

    def test_detection_ignores_column_order(self):
        reordered = list(reversed(NSL_KDD_HEADER))

        assert NslKddStyleAdapter().can_handle(reordered) is True

    def test_extra_unrelated_columns_do_not_prevent_detection(self):
        header = [*NSL_KDD_HEADER, "some_extra_column", "another_one"]

        assert NslKddStyleAdapter().can_handle(header) is True


class TestUnknownSchemaDetection:
    def test_an_unrecognized_header_matches_no_adapter(self):
        selection = select_adapter(UNKNOWN_HEADER)

        assert selection.adapter is None
        assert selection.candidates == ()
        assert selection.is_unsupported is True
        assert selection.is_ambiguous is False

    def test_an_empty_header_matches_no_adapter(self):
        selection = select_adapter([])

        assert selection.adapter is None
        assert selection.is_unsupported is True

    def test_detection_never_raises_for_an_unrecognized_header(self):
        # "Fails safely": an explicit unsupported result, not an exception.
        selection = select_adapter(["???", "", "   ", "a,b;c"])

        assert selection.adapter is None


class TestMissingRequiredColumns:
    def test_nsl_kdd_detection_requires_every_signature_column(self):
        incomplete = [c for c in NSL_KDD_HEADER if c != "dst_bytes"]

        assert NslKddStyleAdapter().can_handle(incomplete) is False

    def test_unsw_detection_requires_every_signature_column(self):
        incomplete = [c for c in UNSW_NB15_HEADER if c != "dsport"]

        assert UnswNb15StyleAdapter().can_handle(incomplete) is False

    def test_cicids_detection_requires_every_signature_column(self):
        incomplete = [c for c in CICIDS_HEADER if c != "Label"]

        assert CicidsStyleAdapter().can_handle(incomplete) is False

    def test_a_single_generic_column_name_is_not_enough_on_its_own(self):
        # "protocol"/"label" alone appear in many schemas and must prove
        # nothing about which dataset this is.
        assert NslKddStyleAdapter().can_handle(["protocol_type"]) is False
        assert UnswNb15StyleAdapter().can_handle(["proto"]) is False
        assert CicidsStyleAdapter().can_handle(["Label"]) is False


class TestNoAccidentalMisclassification:
    def test_nsl_kdd_header_is_rejected_by_the_other_adapters(self):
        assert UnswNb15StyleAdapter().can_handle(NSL_KDD_HEADER) is False
        assert CicidsStyleAdapter().can_handle(NSL_KDD_HEADER) is False

    def test_unsw_header_is_rejected_by_the_other_adapters(self):
        assert NslKddStyleAdapter().can_handle(UNSW_NB15_HEADER) is False
        assert CicidsStyleAdapter().can_handle(UNSW_NB15_HEADER) is False

    def test_cicids_header_is_rejected_by_the_other_adapters(self):
        assert NslKddStyleAdapter().can_handle(CICIDS_HEADER) is False
        assert UnswNb15StyleAdapter().can_handle(CICIDS_HEADER) is False

    def test_each_registered_header_selects_exactly_one_adapter(self):
        for header, expected in (
            (NSL_KDD_HEADER, "nsl-kdd-style"),
            (UNSW_NB15_HEADER, "unsw-nb15-style"),
            (CICIDS_HEADER, "cicids-style"),
        ):
            selection = select_adapter(header)
            assert selection.candidates == (expected,)


class TestAmbiguousSchemas:
    def test_a_header_two_adapters_both_claim_resolves_to_no_match(self, monkeypatch):
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

        monkeypatch.setattr(registry_module, "ADAPTERS", (AlwaysMatches(), AlsoAlwaysMatches()))

        selection = registry_module.select_adapter(["anything"])

        assert selection.adapter is None
        assert selection.is_ambiguous is True
        assert set(selection.candidates) == {"fake-a", "fake-b"}

    def test_ambiguity_never_raises_it_resolves_to_an_explicit_result(self, monkeypatch):
        class AlwaysMatches:
            schema_id = "fake-a"
            description = "x"

            def can_handle(self, header):
                return True

            def adapt(self, record):
                raise NotImplementedError

        monkeypatch.setattr(registry_module, "ADAPTERS", (AlwaysMatches(), AlwaysMatches()))

        selection = registry_module.select_adapter(["anything"])

        assert selection.adapter is None  # safe default, not a crash


class TestCanonicalFieldMapping:
    def test_nsl_kdd_maps_its_confident_fields(self):
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

        result = NslKddStyleAdapter().adapt(record)

        assert result.canonical_fields == {
            "flow_duration": "5",
            "protocol": "tcp",
            "forward_byte_count": "100",
            "backward_byte_count": "200",
        }

    def test_unsw_maps_its_confident_fields(self):
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
                "state": "FIN",
                "label": "0",
                "attack_cat": "",
            }
        )

        result = UnswNb15StyleAdapter().adapt(record)

        assert result.canonical_fields == {
            "source_ip": "10.0.0.1",
            "destination_ip": "10.0.0.2",
            "source_port": "80",
            "destination_port": "443",
            "protocol": "tcp",
            "flow_duration": "1.5",
            "forward_byte_count": "500",
            "backward_byte_count": "300",
            "forward_packet_count": "4",
            "backward_packet_count": "3",
        }

    def test_cicids_maps_its_confident_fields(self):
        record = make_record(
            {
                "Flow Duration": "1000",
                "Total Fwd Packets": "5",
                "Total Backward Packets": "3",
                "Total Length of Fwd Packets": "600",
                "Total Length of Bwd Packets": "400",
                "Destination Port": "443",
                "Label": "BENIGN",
            }
        )

        result = CicidsStyleAdapter().adapt(record)

        assert result.canonical_fields == {
            "flow_duration": "1000",
            "forward_packet_count": "5",
            "backward_packet_count": "3",
            "forward_byte_count": "600",
            "backward_byte_count": "400",
            "destination_port": "443",
        }

    def test_mapped_values_are_copied_verbatim_not_parsed(self):
        # Intentionally malformed/unusual text: the adapter layer must not
        # validate or convert it (that is feature_extraction's job).
        record = make_record({"srcip": "not-an-ip", "sport": "not-a-port", "proto": "tcp"})

        result = UnswNb15StyleAdapter().adapt(record)

        assert result.canonical_fields["source_ip"] == "not-an-ip"
        assert result.canonical_fields["source_port"] == "not-a-port"

    def test_canonical_field_names_match_feature_extractions_vocabulary(self):
        from app.services.feature_extraction import CANONICAL_COLUMN_NAMES

        record = make_record({"srcip": "10.0.0.1", "proto": "tcp", "dur": "1"})
        result = UnswNb15StyleAdapter().adapt(record)

        assert set(result.canonical_fields) <= CANONICAL_COLUMN_NAMES


class TestLabelPreservation:
    def test_nsl_kdd_label_text_is_preserved_verbatim(self):
        record = make_record({"protocol_type": "tcp", "class": "neptune"})

        assert NslKddStyleAdapter().adapt(record).label == "neptune"

    def test_unsw_label_text_is_preserved_verbatim(self):
        record = make_record({"srcip": "1.1.1.1", "label": "1"})

        assert UnswNb15StyleAdapter().adapt(record).label == "1"

    def test_cicids_label_text_is_preserved_verbatim(self):
        record = make_record({"Label": "DDoS"})

        assert CicidsStyleAdapter().adapt(record).label == "DDoS"

    def test_missing_label_column_leaves_label_none(self):
        record = make_record({"protocol_type": "tcp", "service": "http"})

        assert NslKddStyleAdapter().adapt(record).label is None

    def test_an_empty_label_cell_is_none_not_an_empty_string(self):
        record = make_record({"Label": "   "})

        assert CicidsStyleAdapter().adapt(record).label is None

    def test_label_is_never_normalized_to_benign_or_attack(self):
        # The adapter layer preserves text; it does not interpret it.
        record = make_record({"Label": "Web Attack - Brute Force"})

        assert CicidsStyleAdapter().adapt(record).label == "Web Attack - Brute Force"


class TestAttackCategoryPreservation:
    def test_unsw_attack_category_is_preserved_when_present(self):
        record = make_record({"srcip": "1.1.1.1", "attack_cat": "Exploits"})

        assert UnswNb15StyleAdapter().adapt(record).attack_category == "Exploits"

    def test_unsw_attack_category_is_none_when_the_cell_is_empty(self):
        record = make_record({"srcip": "1.1.1.1", "attack_cat": ""})

        assert UnswNb15StyleAdapter().adapt(record).attack_category is None

    def test_nsl_kdd_never_produces_an_attack_category_no_such_column_exists(self):
        record = make_record({"protocol_type": "tcp", "class": "smurf"})

        assert NslKddStyleAdapter().adapt(record).attack_category is None

    def test_cicids_never_produces_an_attack_category_no_such_column_exists(self):
        record = make_record({"Label": "PortScan"})

        assert CicidsStyleAdapter().adapt(record).attack_category is None

    def test_attack_category_is_never_derived_from_label(self):
        # Even when the label alone looks like it implies a category, the
        # adapter must not synthesize attack_category from it.
        record = make_record({"protocol_type": "tcp", "class": "neptune"})

        result = NslKddStyleAdapter().adapt(record)
        assert result.label == "neptune"
        assert result.attack_category is None


class TestMissingColumns:
    def test_a_record_missing_an_optional_mapped_column_leaves_it_out_of_canonical_fields(self):
        record = make_record({"protocol_type": "tcp"})  # no src_bytes/dst_bytes/duration

        result = NslKddStyleAdapter().adapt(record)

        assert result.canonical_fields == {"protocol": "tcp"}
        assert "flow_duration" not in result.canonical_fields
        assert "forward_byte_count" not in result.canonical_fields

    def test_no_fabricated_defaults_for_a_missing_field(self):
        record = make_record({"protocol_type": "tcp"})

        result = NslKddStyleAdapter().adapt(record)

        assert "flow_duration" not in result.canonical_fields
        # Never silently "0" or "" standing in for "not provided".
        assert result.canonical_fields.get("flow_duration") != "0"


class TestUnknownColumns:
    def test_unrecognized_columns_are_preserved_verbatim(self):
        record = make_record(
            {
                "protocol_type": "tcp",
                "serror_rate": "0.5",
                "dst_host_count": "12",
                "num_failed_logins": "0",
            }
        )

        result = NslKddStyleAdapter().adapt(record)

        assert result.unknown_fields == {
            "serror_rate": "0.5",
            "dst_host_count": "12",
            "num_failed_logins": "0",
        }

    def test_unknown_columns_are_not_forced_into_the_15_feature_schema(self):
        record = make_record({"protocol_type": "tcp", "totally_dataset_specific_stat": "42"})

        result = NslKddStyleAdapter().adapt(record)

        assert "totally_dataset_specific_stat" in result.unknown_fields
        assert "totally_dataset_specific_stat" not in result.canonical_fields


class TestOriginalValuePreservation:
    def test_column_mapping_traces_a_canonical_field_back_to_its_source_column(self):
        record = make_record({"srcip": "10.0.0.1", "proto": "tcp"})

        result = UnswNb15StyleAdapter().adapt(record)

        assert result.column_mapping["source_ip"] == "srcip"
        assert result.column_mapping["protocol"] == "proto"

    def test_the_full_original_record_remains_reachable(self):
        record = make_record({"srcip": "10.0.0.1", "extra": "value"})

        result = UnswNb15StyleAdapter().adapt(record)

        assert result.source_record is record
        assert result.source_record.raw_features["srcip"] == "10.0.0.1"
        assert result.source_record.raw_features["extra"] == "value"

    def test_dataset_schema_identifies_which_adapter_produced_the_record(self):
        record = make_record({"protocol_type": "tcp"})

        result = NslKddStyleAdapter().adapt(record)

        assert result.dataset_schema == "nsl-kdd-style"

    def test_row_number_is_carried_through_from_ingestion(self):
        record = make_record({"protocol_type": "tcp"}, row_number=42)

        assert NslKddStyleAdapter().adapt(record).row_number == 42


class TestHeaderMatchesHelper:
    def test_requires_every_name_not_just_any(self):
        assert header_matches(["a", "b", "c"], {"a", "b"}) is True
        assert header_matches(["a"], {"a", "b"}) is False

    def test_empty_requirement_set_always_matches(self):
        assert header_matches(["anything"], set()) is True

    def test_duplicate_header_columns_do_not_break_matching(self):
        assert header_matches(["a", "a", "b"], {"a", "b"}) is True


class TestNoCoreLogicCoupling:
    """Step 18's main acceptance criterion: adding/using a dataset adapter
    must not require ingestion.py, feature_extraction.py or
    batch_processor.py to know about dataset_adapters."""

    def test_ingestion_does_not_import_dataset_adapters(self):
        import app.services.ingestion as module

        assert "import dataset_adapters" not in Path(module.__file__).read_text(encoding="utf-8")

    def test_feature_extraction_does_not_import_dataset_adapters(self):
        import app.services.feature_extraction as module

        assert "import dataset_adapters" not in Path(module.__file__).read_text(encoding="utf-8")

    def test_batch_processor_does_not_import_dataset_adapters(self):
        import app.services.batch_processor as module

        assert "import dataset_adapters" not in Path(module.__file__).read_text(encoding="utf-8")

    def test_dataset_adapters_depends_on_ingestion_and_feature_extraction_not_the_reverse(self):
        # The dependency direction the architecture requires: adapters may
        # import the core layers' public types; the core layers must not
        # import adapters.
        import app.services.dataset_adapters.base as base_module

        source = Path(base_module.__file__).read_text(encoding="utf-8")
        assert "from app.services.ingestion import" in source
