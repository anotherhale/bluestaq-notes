"""Tests for the first real decision provider adapter.

The adapter's job is narrow and testable: turn a bounded question into an HTTP
request, and turn *any* response - good, bad, hostile, or absent - into either a
contract-legal :class:`Decision` or an abstention. So the tests are organised by
that boundary rather than by method:

* the request that leaves (minimal, order-independent, no context);
* the answer that comes back (each malformed shape named individually);
* the construction rules (an adapter that cannot be configured safely must not
  exist at all);
* and the conformance suite from NEX-035, which is the contract every adapter
  must satisfy.

A fake transport stands in for the network. It is not a mock of the code under
test: every assertion is about real adapter behaviour against real bytes.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from notes_service.domain.agent.context import (
    ContextEnvelope,
    ContextField,
    ContextHash,
    DataClassification,
)
from notes_service.domain.agent.decisioning import (
    MAX_RATIONALE_LENGTH,
    AbstentionReason,
    DecisionKind,
    DecisionOutcome,
    DecisionRequest,
)
from notes_service.infrastructure.decisioning.http_provider import HttpDecisionProvider
from tests.agent.test_decision_provider_conformance import assert_provider_conformance

_ENDPOINT = "https://judge.example.test/v1/decide"


def _minted_hash() -> ContextHash:
    return ContextEnvelope(
        fields=(
            ContextField(
                name="note_body",
                classification=DataClassification.PUBLIC,
                value="adapter test",
            ),
        ),
        ceiling=DataClassification.INTERNAL,
    ).context_hash


CONTEXT = _minted_hash()


def _choice_request(*candidates: str) -> DecisionRequest:
    return DecisionRequest(
        kind=DecisionKind.INTENT_ROUTING,
        context_hash=CONTEXT,
        candidates=candidates or ("alpha", "beta"),
    )


def _score_request() -> DecisionRequest:
    return DecisionRequest(kind=DecisionKind.RISK_SCORE, context_hash=CONTEXT)


def _provider(handler: Any, **kwargs: Any) -> HttpDecisionProvider:
    return HttpDecisionProvider(
        name=kwargs.pop("name", "vendor"),
        endpoint=kwargs.pop("endpoint", _ENDPOINT),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        **kwargs,
    )


def _json_response(payload: Any, status: int = 200) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return handler


# --------------------------------------------------------------------------
# Construction: an unsafe adapter must not be constructible.
# --------------------------------------------------------------------------


def test_constructs_with_a_valid_name_and_endpoint() -> None:
    provider = _provider(_json_response({"confidence": 0.9, "choice": "alpha"}))
    assert provider.name == "vendor"


@pytest.mark.parametrize("bad_name", ["", "Vendor", "vendor name", "a" * 81, "vendor\nname"])
def test_rejects_a_name_that_is_not_an_identifier(bad_name: str) -> None:
    with pytest.raises(ValueError, match="invalid decision provider name"):
        _provider(_json_response({}), name=bad_name)


@pytest.mark.parametrize("bad_endpoint", ["http://judge.example.test", "ftp://x", "judge.test"])
def test_refuses_a_non_https_endpoint(bad_endpoint: str) -> None:
    with pytest.raises(ValueError, match="must use https"):
        _provider(_json_response({}), endpoint=bad_endpoint)


@pytest.mark.parametrize("bad_timeout", [0.0, -1.0, float("inf")])
def test_refuses_a_nonsensical_timeout(bad_timeout: float) -> None:
    with pytest.raises(ValueError, match="timeout_seconds must be a positive number"):
        _provider(_json_response({}), timeout_seconds=bad_timeout)


# --------------------------------------------------------------------------
# The request that leaves: minimal and order-independent.
# --------------------------------------------------------------------------


def test_a_choice_request_carries_only_kind_and_sorted_candidates() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"confidence": 0.9, "choice": "alpha"})

    _provider(handler).decide(_choice_request("beta", "alpha", "gamma"))

    assert captured["body"] == {"kind": "intent_routing", "candidates": ["alpha", "beta", "gamma"]}


def test_a_score_request_carries_no_candidates() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"confidence": 0.9, "score": 0.4})

    _provider(handler).decide(_score_request())

    assert captured["body"] == {"kind": "risk_score"}


def test_no_context_hash_or_correlation_id_leaves_the_process() -> None:
    """The wire body must not carry platform-internal identifiers."""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"confidence": 0.9, "choice": "alpha"})

    request = DecisionRequest(
        kind=DecisionKind.INTENT_ROUTING,
        context_hash=CONTEXT,
        candidates=("alpha", "beta"),
        correlation_id="internal-correlation-123",
        excluded_context_fields=("note_body",),
        over_cap_context_fields=("note_body",),
    )
    _provider(handler).decide(request)

    serialised = json.dumps(captured["body"])
    assert str(CONTEXT) not in serialised
    assert "internal-correlation-123" not in serialised
    assert "note_body" not in serialised


def test_candidate_order_does_not_change_the_request_bytes() -> None:
    bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content)
        return httpx.Response(200, json={"confidence": 0.9, "choice": "alpha"})

    provider = _provider(handler)
    provider.decide(_choice_request("alpha", "beta", "gamma"))
    provider.decide(_choice_request("gamma", "alpha", "beta"))

    assert bodies[0] == bodies[1]


def test_the_api_key_is_sent_as_a_bearer_token() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={"confidence": 0.9, "choice": "alpha"})

    _provider(handler, api_key="s3cret").decide(_choice_request())
    assert captured["authorization"] == "Bearer s3cret"


def test_no_authorization_header_without_a_key() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={"confidence": 0.9, "choice": "alpha"})

    _provider(handler).decide(_choice_request())
    assert captured["authorization"] is None


# --------------------------------------------------------------------------
# A legal answer becomes a decided Decision.
# --------------------------------------------------------------------------


def test_a_well_formed_choice_answer_is_decided() -> None:
    decision = _provider(
        _json_response({"confidence": 0.82, "choice": "beta", "rationale": "looks like beta"})
    ).decide(_choice_request("alpha", "beta"))

    assert decision.outcome is DecisionOutcome.DECIDED
    assert decision.choice == "beta"
    assert decision.confidence == pytest.approx(0.82)
    assert decision.rationale == "looks like beta"
    assert decision.score is None


def test_a_well_formed_score_answer_is_decided() -> None:
    decision = _provider(
        _json_response({"confidence": 0.7, "score": 0.33, "rationale": "some risk"})
    ).decide(_score_request())

    assert decision.outcome is DecisionOutcome.DECIDED
    assert decision.score == pytest.approx(0.33)
    assert decision.choice is None


def test_unknown_extra_keys_are_ignored() -> None:
    decision = _provider(
        _json_response(
            {"confidence": 0.9, "choice": "alpha", "vendor_trace": "abc", "grant": "admin"}
        )
    ).decide(_choice_request())

    # The payload cannot name a permission because no field reads one; the
    # decision has no way to hold it.
    assert decision.outcome is DecisionOutcome.DECIDED
    assert not hasattr(decision, "grant")


def test_a_missing_rationale_is_an_empty_string_not_a_failure() -> None:
    decision = _provider(_json_response({"confidence": 0.9, "choice": "alpha"})).decide(
        _choice_request()
    )
    assert decision.outcome is DecisionOutcome.DECIDED
    assert decision.rationale == ""


# --------------------------------------------------------------------------
# Every malformed shape is an abstention, named for its cause.
# --------------------------------------------------------------------------


def test_transport_failure_is_a_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    decision = _provider(handler).decide(_choice_request())
    assert decision.outcome is DecisionOutcome.ABSTAINED
    assert decision.reason is AbstentionReason.PROVIDER_ERROR


def test_non_200_is_a_provider_error() -> None:
    decision = _provider(_json_response({"error": "boom"}, status=500)).decide(_choice_request())
    assert decision.reason is AbstentionReason.PROVIDER_ERROR


def test_an_error_body_is_never_read_into_the_rationale() -> None:
    """A vendor's error text can echo a request or a credential; it must not leak."""
    decision = _provider(
        _json_response({"error": "invalid key sk-live-SECRET"}, status=401)
    ).decide(_choice_request())
    assert decision.rationale == ""


def test_non_json_body_is_malformed_output() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>")

    decision = _provider(handler).decide(_choice_request())
    assert decision.reason is AbstentionReason.MALFORMED_OUTPUT


def test_a_json_array_body_is_malformed_output() -> None:
    decision = _provider(_json_response([1, 2, 3])).decide(_choice_request())
    assert decision.reason is AbstentionReason.MALFORMED_OUTPUT


def test_an_oversized_body_is_malformed_output() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b'{"confidence": 0.9, "padding": "' + b"a" * 70_000 + b'"}'
        )

    decision = _provider(handler).decide(_choice_request())
    assert decision.reason is AbstentionReason.MALFORMED_OUTPUT


@pytest.mark.parametrize("confidence", [None, "0.9", True, False, -0.1, 1.1])
def test_an_unusable_confidence_is_malformed_output(confidence: Any) -> None:
    decision = _provider(_json_response({"confidence": confidence, "choice": "alpha"})).decide(
        _choice_request()
    )
    assert decision.reason is AbstentionReason.MALFORMED_OUTPUT


@pytest.mark.parametrize("confidence", ["nan", "inf"])
def test_a_non_finite_confidence_string_is_malformed_output(confidence: str) -> None:
    decision = _provider(_json_response({"confidence": confidence, "choice": "alpha"})).decide(
        _choice_request()
    )
    assert decision.reason is AbstentionReason.MALFORMED_OUTPUT


def test_a_nan_confidence_number_is_malformed_output() -> None:
    # A bare NaN literal is what a sloppy serialiser actually emits; httpx's own
    # json= helper refuses to produce it, so the bytes are written by hand.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'{"confidence": NaN, "choice": "alpha"}')

    decision = _provider(handler).decide(_choice_request())
    assert decision.reason is AbstentionReason.MALFORMED_OUTPUT


@pytest.mark.parametrize("choice", [None, "gamma", 1, ["alpha"]])
def test_a_choice_the_caller_never_offered_is_malformed_output(choice: Any) -> None:
    decision = _provider(_json_response({"confidence": 0.9, "choice": choice})).decide(
        _choice_request("alpha", "beta")
    )
    assert decision.reason is AbstentionReason.MALFORMED_OUTPUT


@pytest.mark.parametrize("score", [None, "0.5", True, -0.01, 1.01])
def test_an_unusable_score_is_malformed_output(score: Any) -> None:
    decision = _provider(_json_response({"confidence": 0.9, "score": score})).decide(
        _score_request()
    )
    assert decision.reason is AbstentionReason.MALFORMED_OUTPUT


def test_a_score_answer_never_carries_a_choice() -> None:
    """A score-shaped answer cannot smuggle a choice into a decision."""
    decision = _provider(
        _json_response({"confidence": 0.9, "score": 0.5, "choice": "alpha"})
    ).decide(_score_request())
    assert decision.outcome is DecisionOutcome.DECIDED
    assert decision.choice is None


# --------------------------------------------------------------------------
# Rationale handling: bounded, single-line, and truncation is visible.
# --------------------------------------------------------------------------


def test_an_over_long_rationale_is_truncated_to_the_contract_cap() -> None:
    decision = _provider(
        _json_response({"confidence": 0.9, "choice": "alpha", "rationale": "x" * 900})
    ).decide(_choice_request())

    assert decision.outcome is DecisionOutcome.DECIDED
    assert len(decision.rationale) <= MAX_RATIONALE_LENGTH
    assert decision.rationale.endswith("[truncated]")


def test_newlines_in_a_rationale_are_flattened() -> None:
    decision = _provider(
        _json_response(
            {"confidence": 0.9, "choice": "alpha", "rationale": "line one\nINFO forged log"}
        )
    ).decide(_choice_request())

    assert "\n" not in decision.rationale
    assert decision.rationale == "line one INFO forged log"


def test_a_non_string_rationale_is_ignored() -> None:
    decision = _provider(
        _json_response({"confidence": 0.9, "choice": "alpha", "rationale": {"nested": "obj"}})
    ).decide(_choice_request())
    assert decision.outcome is DecisionOutcome.DECIDED
    assert decision.rationale == ""


# --------------------------------------------------------------------------
# The contract from NEX-035. An adapter that cannot pass this is not an adapter.
# --------------------------------------------------------------------------


def _well_behaved_vendor(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    if "candidates" in body:
        # Deterministic and order-independent because the adapter sorted them.
        return httpx.Response(200, json={"confidence": 0.9, "choice": body["candidates"][0]})
    return httpx.Response(200, json={"confidence": 0.9, "score": 0.5})


def test_the_http_adapter_satisfies_the_provider_conformance_suite() -> None:
    assert_provider_conformance(_provider(_well_behaved_vendor))
