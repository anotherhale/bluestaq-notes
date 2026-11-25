"""Context assembly: a digest must not become a declassification.

The defect these tests exist to prevent is subtle and common: "we only send a
hash, so nothing sensitive leaves". That is false. A SHA-256 of a short, guessable
value (an email address, a note title) is a value an attacker can confirm by
hashing candidates, so sending the digest is sending a fingerprint of the secret.

The load-bearing test is therefore
:func:`test_confidential_value_cannot_influence_the_digest`: two envelopes that
differ *only* in a confidential field must hash identically. If that ever fails,
a confidential value is influencing an outbound digest and the module's whole
premise is broken.
"""

from __future__ import annotations

import pytest

from notes_service.domain.agent.context import (
    DEFAULT_CEILING,
    MAX_FIELD_VALUE_LENGTH,
    MAX_FIELDS,
    ContextEnvelope,
    ContextField,
    DataClassification,
)
from notes_service.domain.agent.decisioning import DecisionKind, DecisionRequest
from notes_service.domain.errors import ValidationError


def _field(name: str, value: str, classification: DataClassification) -> ContextField:
    return ContextField(name=name, classification=classification, value=value)


class TestClassificationLadder:
    def test_rank_orders_public_below_restricted(self) -> None:
        assert DataClassification.PUBLIC.rank < DataClassification.INTERNAL.rank
        assert DataClassification.INTERNAL.rank < DataClassification.CONFIDENTIAL.rank
        assert DataClassification.CONFIDENTIAL.rank < DataClassification.RESTRICTED.rank

    def test_default_ceiling_excludes_the_subject_confidential_material(self) -> None:
        assert DEFAULT_CEILING is DataClassification.INTERNAL
        assert DataClassification.CONFIDENTIAL.rank > DEFAULT_CEILING.rank


class TestContextField:
    @pytest.mark.parametrize("name", ["Note", "note id", "", "1note", "n" * 65, "note-id"])
    def test_field_name_must_be_a_lowercase_identifier(self, name: str) -> None:
        with pytest.raises(ValidationError):
            _field(name, "value", DataClassification.PUBLIC)

    def test_field_name_accepts_snake_case(self) -> None:
        assert _field("note_kind", "x", DataClassification.PUBLIC).name == "note_kind"

    def test_value_over_the_cap_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            _field("note_kind", "x" * (MAX_FIELD_VALUE_LENGTH + 1), DataClassification.PUBLIC)

    def test_value_at_the_cap_is_allowed(self) -> None:
        assert len(
            _field("note_kind", "x" * MAX_FIELD_VALUE_LENGTH, DataClassification.PUBLIC).value
        )


class TestContextEnvelopeConstruction:
    def test_duplicate_field_names_are_refused(self) -> None:
        with pytest.raises(ValidationError, match="duplicate context field name"):
            ContextEnvelope.from_fields(
                [
                    _field("note_kind", "a", DataClassification.PUBLIC),
                    _field("note_kind", "b", DataClassification.PUBLIC),
                ]
            )

    def test_restricted_ceiling_is_a_construction_error(self) -> None:
        """The hardest rule: no configuration may send restricted content out."""
        with pytest.raises(ValidationError, match="restricted context may never leave"):
            ContextEnvelope.from_fields(
                [_field("note_kind", "a", DataClassification.PUBLIC)],
                ceiling=DataClassification.RESTRICTED,
            )

    def test_too_many_fields_are_refused(self) -> None:
        fields = [
            _field(f"field_{i}", "v", DataClassification.PUBLIC) for i in range(MAX_FIELDS + 1)
        ]
        with pytest.raises(ValidationError, match="at most"):
            ContextEnvelope.from_fields(fields)

    def test_non_field_member_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            ContextEnvelope.from_fields(["not-a-field"])  # type: ignore[list-item]


class TestHashIsMinimalAndHonest:
    def test_confidential_value_cannot_influence_the_digest(self) -> None:
        """Two envelopes differing only in a confidential field hash the same.

        This is the property the module exists for. If the confidential value
        contributed to the hash at all, these two would differ and the test names
        the leak.
        """
        shared = _field("note_kind", "memo", DataClassification.PUBLIC)
        withheld_a = _field("owner_email", "alice@example.com", DataClassification.CONFIDENTIAL)
        withheld_b = _field("owner_email", "bob@example.com", DataClassification.CONFIDENTIAL)

        envelope_a = ContextEnvelope.from_fields([shared, withheld_a])
        envelope_b = ContextEnvelope.from_fields([shared, withheld_b])

        assert envelope_a.context_hash == envelope_b.context_hash

    def test_confidential_value_is_absent_from_the_canonical_input(self) -> None:
        envelope = ContextEnvelope.from_fields(
            [
                _field("note_kind", "memo", DataClassification.PUBLIC),
                _field("owner_email", "alice@example.com", DataClassification.CONFIDENTIAL),
            ]
        )
        assert [f.name for f in envelope.included_fields] == ["note_kind"]
        assert envelope.excluded_field_names == ("owner_email",)

    def test_included_value_changes_the_digest(self) -> None:
        a = ContextEnvelope.from_fields([_field("note_kind", "memo", DataClassification.PUBLIC)])
        b = ContextEnvelope.from_fields([_field("note_kind", "letter", DataClassification.PUBLIC)])
        assert a.context_hash != b.context_hash

    def test_hash_is_independent_of_field_order(self) -> None:
        first = _field("alpha", "1", DataClassification.PUBLIC)
        second = _field("beta", "2", DataClassification.PUBLIC)
        assert (
            ContextEnvelope.from_fields([first, second]).context_hash
            == ContextEnvelope.from_fields([second, first]).context_hash
        )

    def test_hash_is_a_lowercase_sha256_hex_digest(self) -> None:
        digest = ContextEnvelope.from_fields(
            [_field("note_kind", "memo", DataClassification.PUBLIC)]
        ).context_hash
        assert len(digest) == 64
        assert digest == digest.lower()
        int(digest, 16)

    def test_classification_itself_is_not_part_of_the_content_identity(self) -> None:
        """Labelling a value more strictly must not change the digest of the value."""
        as_public = ContextEnvelope.from_fields(
            [_field("note_kind", "memo", DataClassification.PUBLIC)]
        )
        as_internal = ContextEnvelope.from_fields(
            [_field("note_kind", "memo", DataClassification.INTERNAL)]
        )
        assert as_public.context_hash == as_internal.context_hash

    def test_widening_the_ceiling_admits_more_fields(self) -> None:
        fields = [
            _field("note_kind", "memo", DataClassification.PUBLIC),
            _field("owner_email", "a@example.com", DataClassification.CONFIDENTIAL),
        ]
        narrow = ContextEnvelope.from_fields(fields)
        wide = ContextEnvelope.from_fields(fields, ceiling=DataClassification.CONFIDENTIAL)
        assert narrow.context_hash != wide.context_hash
        assert narrow.excluded_field_names == ("owner_email",)
        assert wide.excluded_field_names == ()

    def test_exclusion_record_names_the_field_without_its_value(self) -> None:
        envelope = ContextEnvelope.from_fields(
            [_field("owner_email", "alice@example.com", DataClassification.CONFIDENTIAL)]
        )
        assert envelope.excluded_field_names == ("owner_email",)
        assert "alice@example.com" not in repr(envelope.excluded_field_names)


class TestDecisionRequestFromContext:
    def test_request_takes_the_envelope_hash(self) -> None:
        envelope = ContextEnvelope.from_fields(
            [_field("note_kind", "memo", DataClassification.PUBLIC)]
        )
        request = DecisionRequest.from_context(DecisionKind.RISK_SCORE, envelope)
        assert request.context_hash == envelope.context_hash

    def test_request_records_excluded_field_names(self) -> None:
        envelope = ContextEnvelope.from_fields(
            [
                _field("note_kind", "memo", DataClassification.PUBLIC),
                _field("owner_email", "a@example.com", DataClassification.CONFIDENTIAL),
            ]
        )
        request = DecisionRequest.from_context(DecisionKind.RISK_SCORE, envelope)
        assert request.excluded_context_fields == ("owner_email",)

    def test_choice_request_still_requires_candidates(self) -> None:
        envelope = ContextEnvelope.from_fields(
            [_field("note_kind", "memo", DataClassification.PUBLIC)]
        )
        with pytest.raises(ValidationError):
            DecisionRequest.from_context(DecisionKind.INTENT_ROUTING, envelope)
        ok = DecisionRequest.from_context(
            DecisionKind.INTENT_ROUTING, envelope, candidates=("create", "search")
        )
        assert ok.candidates == ("create", "search")

    def test_from_context_rejects_a_non_envelope(self) -> None:
        with pytest.raises(ValidationError, match="requires a ContextEnvelope"):
            DecisionRequest.from_context(DecisionKind.RISK_SCORE, object())  # type: ignore[arg-type]


class TestEvaluationCarriesTheExclusionRecord:
    def test_evaluate_decision_surfaces_excluded_fields(self) -> None:
        from notes_service.application.use_cases.decisioning import EvaluateDecision
        from notes_service.infrastructure.decisioning.stub import StubDecisionProvider

        envelope = ContextEnvelope.from_fields(
            [
                _field("note_kind", "memo", DataClassification.PUBLIC),
                _field("owner_email", "a@example.com", DataClassification.CONFIDENTIAL),
            ]
        )
        request = DecisionRequest.from_context(DecisionKind.RISK_SCORE, envelope)
        evaluation = EvaluateDecision(
            provider=StubDecisionProvider(), enabled=True, min_confidence=0.05
        ).run(request)

        assert evaluation.excluded_context_fields == ("owner_email",)
        assert evaluation.context_hash == envelope.context_hash
